#!/usr/bin/env python3
"""foundry-distill — the executable learn-distill consumer (feat-foundry-learn-distill-impl).

The missing CONSUMER half of the learnings loop: deterministic token-overlap clustering over the
session-learnings buffer → threshold-promoted HBK/memory/skill candidates → a byte-reproducible
DISTILL_REPORT in the untracked .foundry/learnings/ workspace artifact (NOT the governed specs/ tree).

Phase order is strict: Read → Cluster → Threshold → Write → Retention. Retention prunes buffer
date-partitions by the date encoded in the directory NAME (YYYY-MM-DD) >90 days before the run date —
NEVER by filesystem mtime (mtime is unstable across clone/checkout/CI; the NAME is the stable
capture-day — the content-not-mtime principle the corpus itself surfaced). A non-date-named directory
is ignored (never pruned). Whether a partition was read this run is irrelevant to the predicate.

Workspace-rooted: CLAUDE_PROJECT_DIR → `git --git-common-dir` parent → cwd (mirrors the producers'
_foundry_root); --root overrides for isolation/testing. Reads UNVALIDATED, producer-scrubbed records
(a malformed line is skipped, never fatal); does NOT re-scrub (the producer write-path scrub is
authoritative) and does NOT publish. Deterministic: no embeddings, fully pinned canonicalization +
ordering, so the same buffer + same --date yields a byte-identical report.

  foundry-distill.py [--root DIR] [--date YYYY-MM-DD] [--dry-run]
  foundry-distill.py --drift-json [--root DIR]   # AC-LBC-4: read-only never-admitted-drift JSON (no distill)
  foundry-distill.py --selftest          # AC-DISTILL-1/-2/-4 (isolated temp root)
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TOKEN_RE = re.compile(r"[a-z0-9]+")
MIN_TOKEN_LEN = 3
SHARE_THRESHOLD = 3       # >=3 shared normalized tokens => clustering edge
PROMOTE_THRESHOLD = 3     # >=3 records in a component => promotion candidate
RETENTION_DAYS = 90

# A fixed (not exhaustive) stopword set — determinism needs it FIXED, not complete.
STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "are", "was", "were", "but",
    "not", "you", "your", "its", "it's", "has", "have", "had", "will", "would", "can", "could",
    "should", "they", "them", "their", "than", "then", "when", "what", "which", "who", "how",
    "why", "where", "all", "any", "one", "two", "use", "used", "using", "via", "per", "out",
    "over", "only", "own", "new", "old", "now", "see", "set", "get", "got", "run", "ran",
    "must", "may", "more", "most", "some", "such", "each", "every", "also", "been", "being",
    "does", "did", "done", "doing", "because", "after", "before", "while", "about", "above",
    "below", "between", "both", "here", "there",
}

# class -> promotion target, over the REAL observed record vocabulary. `type` ∈ {process,decision,
# gotcha}; `kind` ∈ {method,finding,gotcha}. Precedence: `type` if present else `kind`. Anything
# unmapped/absent -> the explicit `unclassified` (review) default — never a silent drop.
CLASS_TARGET = {
    "gotcha": "HBK", "pattern": "HBK", "method": "HBK", "finding": "HBK",
    "decision": "memory", "process": "skill",
}
DEFAULT_TARGET = "unclassified"
# Tie-break order when a component's targets are tied on count.
TARGET_TIE_ORDER = ["HBK", "skill", "memory", "unclassified"]


def _resolve_root(root_arg=None):
    """Workspace root: --root → CLAUDE_PROJECT_DIR → `git --git-common-dir` parent → cwd."""
    if root_arg:
        return os.path.abspath(root_arg)
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return os.path.abspath(env)
    try:
        cdir = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, check=False,
        ).stdout.strip()
        if cdir:
            return os.path.abspath(os.path.dirname(os.path.abspath(cdir)))
    except Exception:
        pass
    return os.getcwd()


def _buffer_dir(root):
    return os.path.join(root, ".foundry", "session-learnings")


def _report_dir(root):
    return os.path.join(root, ".foundry", "learnings")


def scan_drift(buffer_dir):
    """Read-only COMPLEMENT of read_records (AC-LBC-4): enumerate the records the consumer would
    NEVER admit to its corpus — the silent-loss the producer-side reject (AC-LBC-2) cannot see
    (bypass writes). Buckets, together partitioning the entire complement of admissibility:
      unpartitioned    — a non-dotfile regular file outside any DATE_RE partition (the buffer root,
                         a non-date subdir, OR nested two-plus levels deep), ANY extension;
      wrong_file       — under exactly one DATE_RE partition, a non-dotfile file NOT ending .jsonl;
      unparseable_line — under a DATE_RE partition's admitted .jsonl, a non-empty line that fails
                         json.loads OR is not a dict (per-LINE; entry "<relpath>:<line-index>").
    Excludes every dotfile/dot-dir at any depth (covers .harvest-log.jsonl at the root, the
    .harvest.XXXXXX mktemp temps inside partitions, and any reject row in the reconciliation log);
    does NOT follow symlinks. Read-only + content-free: returns per-bucket COUNTS + buffer-root-
    relative PATHS only (never a record excerpt — an unscrubbed secret in a bypass record is never
    surfaced), each list lexicographically sorted, JSON-encodable. NB an admitted record that yields
    few/zero clustering tokens is READABLE, never counted here (admitted-but-unpromoted != loss)."""
    buckets = {"unpartitioned": [], "wrong_file": [], "unparseable_line": []}
    if os.path.isdir(buffer_dir):
        for dirpath, dirnames, filenames in os.walk(buffer_dir, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))  # prune dot-dirs, any depth
            rel_dir = os.path.relpath(dirpath, buffer_dir)
            parts = [] if rel_dir == "." else rel_dir.split(os.sep)
            for fn in sorted(filenames):
                if fn.startswith("."):
                    continue
                full = os.path.join(dirpath, fn)
                if os.path.islink(full) or not os.path.isfile(full):
                    continue
                relpath = fn if rel_dir == "." else os.path.join(rel_dir, fn)
                if len(parts) == 1 and DATE_RE.match(parts[0]):       # directly under one DATE_RE partition
                    if fn.endswith(".jsonl"):                         # an admitted file → line-scan
                        try:
                            with open(full, encoding="utf-8", errors="replace") as f:
                                lines = f.readlines()
                        except Exception:
                            continue                                  # unreadable file: read_records skips it too
                        for i, line in enumerate(lines):
                            s = line.strip()
                            if not s:
                                continue
                            try:
                                bad = not isinstance(json.loads(s), dict)
                            except Exception:
                                bad = True
                            if bad:
                                buckets["unparseable_line"].append("%s:%d" % (relpath, i))
                    else:
                        buckets["wrong_file"].append(relpath)
                else:                                                 # root / non-date dir / nested-deep
                    buckets["unpartitioned"].append(relpath)
    for k in buckets:
        buckets[k].sort()
    return {"counts": {k: len(v) for k, v in buckets.items()}, "paths": buckets}


def _canonical(obj):
    """The pinned canonical form of a record (drives ordering + reproducibility)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _tokens(rec):
    """Normalized token set: lower-cased [a-z0-9]+ (len>=3, non-stopword) from EVERY string-valued
    scalar field + the `tags` list. Schema-agnostic, so `area` and any future field contribute."""
    toks = set()

    def _add(s):
        for t in TOKEN_RE.findall(s.lower()):
            if len(t) >= MIN_TOKEN_LEN and t not in STOPWORDS:
                toks.add(t)

    for k, v in rec.items():
        if isinstance(v, str):
            _add(v)
        elif k == "tags" and isinstance(v, list):
            for tag in v:
                if isinstance(tag, str):
                    _add(tag)
    return toks


def _dated_partitions(buffer_dir):
    """Dated partition dir NAMES (YYYY-MM-DD) only, sorted."""
    if not os.path.isdir(buffer_dir):
        return []
    return sorted(
        d for d in os.listdir(buffer_dir)
        if DATE_RE.match(d) and os.path.isdir(os.path.join(buffer_dir, d))
    )


def read_records(buffer_dir):
    """READ phase. Records from dated partitions only — `<YYYY-MM-DD>/*.jsonl` — explicitly EXCLUDING
    the reconciliation log `.harvest-log.jsonl` and any dotfile/dot-dir. Malformed lines skipped.
    Returns dicts {rec, canonical, tokens, partition, file, line} sorted by (partition, file, line)."""
    out = []
    for part in _dated_partitions(buffer_dir):
        pdir = os.path.join(buffer_dir, part)
        for fn in sorted(os.listdir(pdir)):
            if fn.startswith("."):            # dotfiles (incl. any .harvest-log.jsonl) excluded
                continue
            if not fn.endswith(".jsonl"):
                continue
            fpath = os.path.join(pdir, fn)
            if not os.path.isfile(fpath):
                continue
            try:
                with open(fpath, encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except Exception:
                continue
            for i, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue                  # malformed line skipped, non-fatal
                if not isinstance(rec, dict):
                    continue
                out.append({
                    "rec": rec, "canonical": _canonical(rec), "tokens": _tokens(rec),
                    "partition": part, "file": fn, "line": i,
                })
    out.sort(key=lambda r: (r["partition"], r["file"], r["line"]))
    return out


def cluster(records):
    """CLUSTER phase. Transitive connected components over the >=SHARE_THRESHOLD shared-token edge
    relation (union-find). Returns a list of member-index lists: members sorted by canonical form;
    components sorted by (size desc, lexicographically-least canonical member). Deterministic."""
    n = len(records)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for i in range(n):
        ti = records[i]["tokens"]
        for j in range(i + 1, n):
            if len(ti & records[j]["tokens"]) >= SHARE_THRESHOLD:
                union(i, j)

    comps = {}
    for i in range(n):
        comps.setdefault(find(i), []).append(i)
    clusters = [sorted(ms, key=lambda m: records[m]["canonical"]) for ms in comps.values()]
    clusters.sort(key=lambda ms: (-len(ms), records[ms[0]]["canonical"]))
    return clusters


def _target_for(rec):
    cls = rec.get("type") if rec.get("type") else rec.get("kind")
    return CLASS_TARGET.get(cls, DEFAULT_TARGET)


def _cluster_target(records, members):
    counts = {}
    for m in members:
        t = _target_for(records[m]["rec"])
        counts[t] = counts.get(t, 0) + 1
    # mode; ties broken by TARGET_TIE_ORDER (HBK > skill > memory > unclassified)
    return max(counts.items(), key=lambda kv: (kv[1], -TARGET_TIE_ORDER.index(kv[0])))[0]


def _title_of(rec):
    for k in ("title", "insight", "detail", "lesson", "rationale"):
        v = rec.get(k)
        if isinstance(v, str) and v.strip():
            t = " ".join(v.strip().split())
            return (t[:157] + "...") if len(t) > 160 else t
    return "(untitled)"


def render_report(records, clusters, run_date_str):
    """THRESHOLD + report rendering. Pure function of (records, clusters, run_date) → byte-stable."""
    promoted = [c for c in clusters if len(c) >= PROMOTE_THRESHOLD]
    lines = [
        f"# DISTILL_REPORT — {run_date_str}",
        "",
        f"> Auto-generated by foundry-distill (deterministic token-overlap clustering). "
        f"{len(records)} record(s) read; {len(clusters)} cluster(s); {len(promoted)} promotion "
        f"candidate(s) (components of >={PROMOTE_THRESHOLD}).",
        "",
        "## Promotion candidates",
    ]
    if not promoted:
        lines.append("(none — no cluster reached the promotion threshold)")
    for idx, members in enumerate(promoted, 1):
        target = _cluster_target(records, members)
        lines.append("")
        lines.append(f"### Candidate {idx} — target: {target} ({len(members)} records)")
        for m in members:
            lines.append(f"- {_title_of(records[m]['rec'])}")
    lines.append("")
    return "\n".join(lines) + "\n"


def retention(buffer_dir, run_date):
    """RETENTION phase (runs LAST). Prune partition dirs whose NAME parses as YYYY-MM-DD and is
    >RETENTION_DAYS before run_date. Non-date dirs ignored. Returns the pruned partition names."""
    pruned = []
    if not os.path.isdir(buffer_dir):
        return pruned
    cutoff = run_date - timedelta(days=RETENTION_DAYS)
    for d in sorted(os.listdir(buffer_dir)):
        if not DATE_RE.match(d):
            continue                         # non-date dir: never pruned
        pdir = os.path.join(buffer_dir, d)
        if not os.path.isdir(pdir):
            continue
        try:
            pdate = datetime.strptime(d, "%Y-%m-%d").date()
        except ValueError:
            continue
        if pdate < cutoff:
            shutil.rmtree(pdir, ignore_errors=True)
            pruned.append(d)
    return pruned


def distill(root_arg=None, date_str=None, dry_run=False):
    """The consumer: Read → Cluster → Threshold → Write → Retention (strict order)."""
    root = _resolve_root(root_arg)
    buffer_dir = _buffer_dir(root)
    run_date = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else date.today()
    records = read_records(buffer_dir)                          # READ
    clusters = cluster(records)                                 # CLUSTER
    report = render_report(records, clusters, run_date.isoformat())  # THRESHOLD + render
    promoted = sum(1 for c in clusters if len(c) >= PROMOTE_THRESHOLD)
    written = None
    pruned = []
    if not dry_run:
        rdir = _report_dir(root)                                # WRITE
        os.makedirs(rdir, exist_ok=True)
        written = os.path.join(rdir, f"DISTILL_REPORT-{run_date.isoformat()}.md")
        with open(written, "w", encoding="utf-8") as f:
            f.write(report)
        pruned = retention(buffer_dir, run_date)                # RETENTION (last)
    return {
        "root": root, "records": len(records), "clusters": len(clusters),
        "promoted": promoted, "report": report, "written": written, "pruned": pruned,
    }


def latest_report_date(root_arg=None):
    """The date of the newest DISTILL_REPORT-<date>.md (or None)."""
    root = _resolve_root(root_arg)
    rdir = _report_dir(root)
    if not os.path.isdir(rdir):
        return None
    dates = []
    for fn in os.listdir(rdir):
        m = re.match(r"^DISTILL_REPORT-(\d{4}-\d{2}-\d{2})\.md$", fn)
        if m:
            dates.append(m.group(1))
    return max(dates) if dates else None


def newest_record_partition(root_arg=None):
    """The newest dated partition NAME holding >=1 record (the record-date source — records have
    no timestamp), or None."""
    root = _resolve_root(root_arg)
    parts = _dated_partitions(_buffer_dir(root))
    return parts[-1] if parts else None


# --------------------------------------- selftest --------------------------------------- #
def _emit(token, ok, detail):
    print(f"  {token}: {'PASS' if ok else 'FAIL'} — {detail}")
    return ok


def _write_rec(buffer_dir, part, fname, recs):
    d = os.path.join(buffer_dir, part)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, fname), "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")


def selftest():
    tmp = tempfile.mkdtemp(prefix="distill-selftest-")
    fails = 0
    try:
        buf = _buffer_dir(tmp)
        # a cohesive cluster: 3 records sharing >=3 distinctive tokens (karpenter/drift/nodepool).
        shared = [
            {"insight": "karpenter drift replaces nodepool fleet", "type": "gotcha",
             "tags": ["karpenter", "drift", "nodepool"]},
            {"detail": "nodepool drift on karpenter triggers replacement", "kind": "finding",
             "tags": ["karpenter", "drift", "nodepool"]},
            {"insight": "pin karpenter nodepool to avoid drift replacement", "type": "decision",
             "tags": ["karpenter", "drift", "nodepool"]},
        ]
        # a loner with no shared tokens with the cluster.
        loner = [{"insight": "quarterly billing reconciliation cadence", "type": "process",
                  "tags": ["billing", "finance"]}]
        _write_rec(buf, "2026-06-17", "s1__session__aaa.jsonl", shared)
        _write_rec(buf, "2026-06-17", "s2__session__bbb.jsonl", loner)
        # a foreign-schema reconciliation log at the BUFFER ROOT — must be EXCLUDED from the read.
        with open(os.path.join(buf, ".harvest-log.jsonl"), "w", encoding="utf-8") as f:
            f.write('{"ts":"x","channel":"session","task_id":"session","records":3,"outcome":"ok"}\n')

        # ---- AC-DISTILL-1: deterministic clustering + promotion + report.
        r1 = 0
        out = distill(root_arg=tmp, date_str="2026-06-18")
        rep_path = out["written"]
        rep = open(rep_path, encoding="utf-8").read() if rep_path and os.path.isfile(rep_path) else ""
        if out["promoted"] != 1:
            r1 = 1                                    # exactly one component reached the threshold
        if "target: HBK" not in rep:
            r1 = 1                                    # mode of {gotcha,finding,decision} targets -> HBK
        if "DISTILL_REPORT-2026-06-18.md" not in (rep_path or ""):
            r1 = 1
        _emit("AC-DISTILL-1 deterministic-clustering-report", r1 == 0,
              f"records={out['records']} clusters={out['clusters']} promoted={out['promoted']}; report names HBK candidate")

        # ---- AC-DISTILL-2: phase order + retention by partition-NAME date (not mtime); non-date ignored.
        r2 = 0
        os.makedirs(os.path.join(buf, "2000-01-01"), exist_ok=True)     # >90d-by-name → prune
        _write_rec(buf, "2000-01-01", "old__session__ccc.jsonl", loner)
        os.makedirs(os.path.join(buf, "not-a-date"), exist_ok=True)     # non-date → never pruned
        distill(root_arg=tmp, date_str="2026-06-18")
        if os.path.isdir(os.path.join(buf, "2000-01-01")):
            r2 = 1                                    # stale partition must be pruned
        if not os.path.isdir(os.path.join(buf, "2026-06-17")):
            r2 = 1                                    # within-window partition preserved
        if not os.path.isdir(os.path.join(buf, "not-a-date")):
            r2 = 1                                    # non-date dir untouched
        _emit("AC-DISTILL-2 phase-order-retention-by-name-date", r2 == 0,
              "2000-01-01 pruned; 2026-06-17 preserved; non-date dir untouched")

        # ---- AC-DISTILL-4: anti-tautology oracle.
        r4 = 0
        # (i) no-shared-token set yields NO promoted cluster (not vacuously grouping everything).
        tmp2 = tempfile.mkdtemp(prefix="distill-noedge-")
        try:
            b2 = _buffer_dir(tmp2)
            _write_rec(b2, "2026-06-17", "n__session__d.jsonl", [
                {"insight": "alpha beta gamma unrelated", "type": "gotcha", "tags": ["alpha"]},
                {"insight": "delta epsilon zeta distinct", "type": "gotcha", "tags": ["delta"]},
                {"insight": "eta theta iota separate", "type": "gotcha", "tags": ["eta"]},
            ])
            if distill(root_arg=tmp2, date_str="2026-06-18")["promoted"] != 0:
                r4 = 1
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)
        # (ii) reproducibility: two runs over the same fixture + date → byte-identical report.
        a = distill(root_arg=tmp, date_str="2026-06-18", dry_run=True)["report"]
        b = distill(root_arg=tmp, date_str="2026-06-18", dry_run=True)["report"]
        if a != b:
            r4 = 1
        # (iii) harvest-log was excluded: the foreign row never inflated the record count to include it
        #       (3 shared + 1 loner = 4 records; the .harvest-log line is NOT among them).
        if distill(root_arg=tmp, date_str="2026-06-18", dry_run=True)["records"] != 4:
            r4 = 1
        # (iv) the advisory tri-state directly over THIS module's own primitives (no
        # scripts/foundry_checks/learn-distill.py drop-in probe ships — its `read_records`/
        # `latest_report_date`/`newest_record_partition`-driven advisory signal is proven here
        # and in tests/test_learnings.py directly against this module, never re-implemented in
        # a separate file). records-but-no-report → advisory None (NOT False; a dormant buffer is
        # a missed optimization, not a correctness breach).
        tmp3 = tempfile.mkdtemp(prefix="distill-none-")
        try:
            b3 = _buffer_dir(tmp3)
            _write_rec(b3, "2026-06-17", "x__session__e.jsonl", loner)
            newest_partition = newest_record_partition(tmp3)
            latest_report = latest_report_date(tmp3)
            records_no_report_is_advisory = (newest_partition is not None and latest_report is None)
            if not records_no_report_is_advisory:
                r4 = 1
        finally:
            shutil.rmtree(tmp3, ignore_errors=True)
        _emit("AC-DISTILL-4 selftest-real-oracle-anti-tautology", r4 == 0,
              "no-shared-token→0 promoted; reproducible byte-identical; harvest-log excluded (4 recs); advisory None on records-no-report")

        ok = (r1 == 0 and r2 == 0 and r4 == 0)
        fails = 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("FOUNDRY-DISTILL-SELFTEST-GREEN" if fails == 0 else "FOUNDRY-DISTILL-SELFTEST-RED")
    return 0 if fails == 0 else 1


def main():
    ap = argparse.ArgumentParser(description="foundry-distill — executable learn-distill consumer")
    ap.add_argument("--root")
    ap.add_argument("--date")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--drift-json", action="store_true",
                    help="AC-LBC-4: print scan_drift JSON for the buffer (read-only; SKIPS the "
                         "Read→Cluster→Write→Retention pipeline — no prune, no report). Honors --root.")
    args = ap.parse_args()
    if args.selftest:                                              # mutually exclusive; short-circuits first
        return selftest()
    if args.drift_json:                                           # read-only probe — never runs distill
        buffer_dir = _buffer_dir(_resolve_root(args.root))
        print(json.dumps(scan_drift(buffer_dir), sort_keys=True))
        return 0
    out = distill(root_arg=args.root, date_str=args.date, dry_run=args.dry_run)
    if args.dry_run:
        print(f"[dry-run] {out['records']} record(s), {out['clusters']} cluster(s), "
              f"{out['promoted']} promotion candidate(s); no write.")
    else:
        print(f"distilled {out['records']} record(s) → {out['promoted']} candidate(s); "
              f"wrote {out['written']}; pruned {len(out['pruned'])} partition(s).")
    # AC-LBC-4(a): drift readout on the CLI RUN-OUTPUT stream ONLY — never inside render_report /
    # the persisted DISTILL_REPORT (whose byte-stability is a pinned invariant).
    _dc = scan_drift(_buffer_dir(out["root"]))["counts"]
    _dt = _dc["unpartitioned"] + _dc["wrong_file"] + _dc["unparseable_line"]
    if _dt:
        print(f"drift: {_dt} never-admitted records/lines (unpartitioned={_dc['unpartitioned']} "
              f"wrong_file={_dc['wrong_file']} unparseable_line={_dc['unparseable_line']}) — not consumed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
