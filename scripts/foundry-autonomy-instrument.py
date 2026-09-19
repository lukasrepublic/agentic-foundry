#!/usr/bin/env python3
"""foundry-autonomy-instrument — six report-only autonomy ratios (feat
yield-and-silent-yield-instrument). Promotes the two workspace extractors
(`docs/micro-workflows/empirical-mining/mine_failures.py`, `spec_signals.py`) into one Foundry
CLI, per their method doc `mine-autonomy-2026-09-18.md` §0.

REPORT ONLY (AC-INS-5): this CLI gates nothing. It always exits 0 regardless of the computed
ratios, writes no ledger and no `.foundry/` file unless `--out` is given.

The six ratios:
  1. silent-yield        — of the agent's real "stops" (an assistant turn with no tool call,
                            immediately followed by a genuine operator turn), the share with no
                            trailing question either. The corrected instrument from §0's blind
                            spot: `ended_with_open_question` cannot see a silent stop.
  2. directive-reply      — of those same stops, the share answered by an operator reply of
                            <=45 characters (`merge it`, `go`, `push to main`).
  3. granted-verb-denial  — of every classifier/hook denial found in a tool_result, the share
                            whose blocked verb is already present in the workspace's settings
                            allow rules (a denial the operator believes is granted).
  4. guard-false-positive — SAMPLED, never auto-classified: up to `--sample-size` (default 40)
                            of the denials above, reported with excerpts for a human to read.
  5. authorized->built    — specs/**/acceptance-contract.yaml carrying a real (non-placeholder)
                            spec_sha256, versus the spec_refs actually recorded in a
                            `.foundry/build-provenance.yaml` under `--repo`.
  6. rounds-per-shipped   — total `rounds` summed across every `.foundry/audit-ledger.jsonl` row
                            found under `--repo`, divided by the same shipped-atom count as (5).

Three extractor lies documented in the method doc, and what this instrument does instead:
  - `\\bBLOCK\\b` (case-insensitive) matches "Processing block 454393353" on a blockchain
    product. This instrument's DENY regex has NO bare-word "block" alternative — every member
    names a denial-shaped PHRASE (`blocked by ...`, `classifier block(ed)`, `hook (blocked|
    denied|refused)`, `permission denied`, ...).
  - ~33% of "questions" are rhetorical self-questions the agent answers itself and keeps going
    (a tool_use in the SAME assistant turn as the question text). This instrument only classifies
    an assistant turn as a question (or as a silent yield) when that turn carries NO tool_use —
    a turn that also calls a tool is a rhetorical aside, not a real handoff, and is never the one
    evaluated (the pending-assistant pointer keeps moving forward until a real stop).
  - the operator-turn discriminator must be the validated one. `is_operator_turn()` below is
    copied verbatim from `docs/micro-workflows/empirical-mining/mine_failures.py` (P/R 1.000,
    n=2,973, `mine-autonomy-2026-07-16.md`) — not re-derived.

usage:
  foundry-autonomy-instrument.py --since YYYY-MM-DD --projects-dir DIR [--projects-dir DIR ...]
                                  [--repo DIR] [--json] [--out PATH] [--sample-size N]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

try:
    import yaml  # pyyaml — pinned in requirements-dev.txt / requirements.txt
except ImportError:  # pragma: no cover - degrades to a hand-rolled scalar fallback below
    yaml = None

EXC = 240  # excerpt cap, matches mine_failures.py

# --------------------------------------------------------------------------------------------- #
# validated operator-turn discriminator — copied verbatim from
# docs/micro-workflows/empirical-mining/mine_failures.py (P/R 1.000; AC-INS-3)
# --------------------------------------------------------------------------------------------- #


def is_operator_turn(r):
    if r.get("type") != "user": return False
    if r.get("isSidechain"): return False
    if r.get("isMeta") or r.get("isCompactSummary"): return False
    o, ps = r.get("origin"), r.get("promptSource")
    if isinstance(o, dict): return o.get("kind") == "human"
    return o is None and ps in ("typed", "queued", "suggestion_accepted")


# --------------------------------------------------------------------------------------------- #
# question / denial regexes — the corrected instrument (§0's three lies avoided)
# --------------------------------------------------------------------------------------------- #

Q_TAIL = re.compile(r"\?\s*$")
Q_PHRASE = re.compile(
    r"\b(want me to|should i|do you want|would you like|which (one|option|do you)|"
    r"let me know|shall i|can you confirm|please confirm|your call|up to you|say the word)\b",
    re.I,
)

# Deliberately NO bare \bBLOCK\b member (the documented false-positive on "Processing block N"
# blockchain-sync output). Every alternative below names a denial-shaped phrase.
DENY = re.compile(
    r"(permission (for this action )?was denied|blocked by (the )?classifier|"
    r"auto[- ]mode classifier block(ed)?|classifier block(ed)?|denied by (the )?|"
    r"is not allowed|hook (blocked|denied|refused)|blocked by (the )?hook|"
    r"disabled for safety|requires approval|permission denied|EPERM|EACCES|"
    r"Read-only file system|refused by|not permitted)",
    re.I,
)


def ex(s):
    return re.sub(r"\s+", " ", s or "")[:EXC]


# --------------------------------------------------------------------------------------------- #
# transcript record helpers
# --------------------------------------------------------------------------------------------- #


def blocks(r):
    m = r.get("message") or {}
    c = m.get("content")
    if isinstance(c, str):
        return [{"type": "text", "text": c}]
    return c if isinstance(c, list) else []


def text_of(r):
    return " ".join(b.get("text", "") for b in blocks(r) if b.get("type") == "text").strip()


def tool_uses(r):
    return [b for b in blocks(r) if b.get("type") == "tool_use"]


def tool_results(r):
    """Yields (tool_use_id, is_error, text) for every tool_result block in a user-type record."""
    out = []
    for b in blocks(r):
        if b.get("type") != "tool_result":
            continue
        c = b.get("content")
        if isinstance(c, list):
            c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
        out.append((b.get("tool_use_id"), bool(b.get("is_error")), (c or "")[:2000]))
    return out


def record_ts(r):
    t = r.get("timestamp")
    if not t:
        return None
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def read_jsonl(path):
    recs = []
    try:
        with open(path, errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    recs.append(json.loads(line))
                except Exception:
                    pass
    except OSError:
        pass
    return recs


# --------------------------------------------------------------------------------------------- #
# per-file mining: stops (silent-yield/directive-reply input) + denials
# --------------------------------------------------------------------------------------------- #


def mine_file(path):
    """Returns {"stops": [{"silent": bool, "reply_len": int}], "denials": [{"file","text",
    "tool_name","verb"}]}. A "stop" is a real handoff: the nearest preceding assistant record
    (skipping non-human tool_result echoes) carries NO tool_use, and is immediately followed by
    a genuine operator (human) turn. An assistant turn that DOES carry a tool_use is never
    evaluated as a stop — it is a rhetorical aside or in-flight work, not a handoff (avoids the
    "self-answered question" lie)."""
    recs = read_jsonl(path)
    tool_use_by_id = {}
    pending_assistant = None
    stops = []
    denials = []
    fname = os.path.basename(path)
    for r in recs:
        if r.get("isSidechain"):
            continue
        rtype = r.get("type")
        if rtype == "assistant":
            for tu in tool_uses(r):
                tid = tu.get("id") or tu.get("tool_use_id")
                if tid:
                    tool_use_by_id[tid] = (tu.get("name", ""), tu.get("input") or {})
            pending_assistant = r
        elif rtype == "user":
            for tid, _is_err, txt in tool_results(r):
                if DENY.search(txt):
                    name, inp = tool_use_by_id.get(tid, ("", {}))
                    denials.append({
                        "file": fname,
                        "text": ex(txt),
                        "tool_name": name,
                        "verb": verb_of(name, inp) if name else None,
                    })
            if is_operator_turn(r) and pending_assistant is not None:
                has_tool = bool(tool_uses(pending_assistant))
                if not has_tool:
                    tail = text_of(pending_assistant)[-400:]
                    is_q = bool(Q_TAIL.search(tail)) or bool(Q_PHRASE.search(tail))
                    reply_len = len(text_of(r).strip())
                    stops.append({"silent": not is_q, "reply_len": reply_len})
                pending_assistant = None
    return {"stops": stops, "denials": denials}


def iter_jsonl_files(root, since_ts):
    """Walk root for *.jsonl main-session transcripts, mtime-filtered by since_ts (matches
    mine_failures.py's method). Subagent transcripts (path contains /subagents/) are skipped —
    counted, not mined, per the method doc."""
    if not os.path.isdir(root):
        return
    for dirpath, _dirnames, filenames in os.walk(root):
        if "/subagents/" in (dirpath + "/"):
            continue
        for fn in filenames:
            if not fn.endswith(".jsonl"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                if since_ts is not None and os.path.getmtime(path) < since_ts:
                    continue
            except OSError:
                continue
            yield path


# --------------------------------------------------------------------------------------------- #
# granted-verb-denial: does a blocked verb already have a settings allow rule?
# --------------------------------------------------------------------------------------------- #


def verb_of(tool_name, tool_input):
    if tool_name == "Bash":
        cmd = (tool_input or {}).get("command", "") or ""
        cmd = cmd.strip()
        return cmd.split()[0] if cmd else None
    return tool_name


_RULE_HEAD_RE = re.compile(r"^([A-Za-z0-9_]+)\((.*)\)$")


def load_allow_rules(repo_dir):
    """Union of permissions.allow from .claude/settings.json + .claude/settings.local.json under
    repo_dir. Best-effort: a missing or malformed file contributes nothing."""
    rules = []
    for relpath in (".claude/settings.json", ".claude/settings.local.json"):
        path = os.path.join(repo_dir, relpath)
        if not os.path.isfile(path):
            continue
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        allow = ((data.get("permissions") or {}).get("allow")) or []
        if isinstance(allow, list):
            rules.extend(str(a) for a in allow)
    return rules


def is_granted(tool_name, verb, allow_rules):
    if not verb:
        return False
    for rule in allow_rules:
        m = _RULE_HEAD_RE.match(rule.strip())
        if not m:
            if tool_name != "Bash" and rule.strip() == tool_name:
                return True
            continue
        head, body = m.group(1), m.group(2).strip()
        if tool_name == "Bash" and head == "Bash":
            inner_verb = body.split()[0] if body else None
            if inner_verb == verb:
                return True
        elif head == tool_name:
            return True
    return False


# --------------------------------------------------------------------------------------------- #
# authorized->built conversion + rounds-per-shipped-atom (--repo, optional)
# --------------------------------------------------------------------------------------------- #

_PLACEHOLDER_SHA_RE = re.compile(r"^0+$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")

_SKIP_DIRS = {".git", "node_modules", ".worktrees"}


def _walk_repo(repo_dir):
    for dirpath, dirnames, filenames in os.walk(repo_dir):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        yield dirpath, dirnames, filenames


def _load_yaml_scalar_fallback(path, key):
    """Hand-rolled fallback when pyyaml is unavailable: finds a top-level `key: value` line."""
    pat = re.compile(r"^" + re.escape(key) + r":\s*\"?([0-9a-fA-F]{10,64})\"?\s*$")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                m = pat.match(line.strip())
                if m:
                    return m.group(1)
    except OSError:
        pass
    return None


def scan_specs_authorized(repo_dir):
    """Count of specs/**/acceptance-contract.yaml carrying a real (non-placeholder,
    non-all-zero) spec_sha256 — the front-authorization corpus."""
    specs_dir = os.path.join(repo_dir, "specs")
    if not os.path.isdir(specs_dir):
        return 0
    count = 0
    for dirpath, _dirnames, filenames in _walk_repo(specs_dir):
        if "acceptance-contract.yaml" not in filenames:
            continue
        path = os.path.join(dirpath, "acceptance-contract.yaml")
        sha = None
        if yaml is not None:
            try:
                data = yaml.safe_load(open(path, encoding="utf-8")) or {}
                sha = data.get("spec_sha256")
            except Exception:
                sha = None
        if sha is None:
            sha = _load_yaml_scalar_fallback(path, "spec_sha256")
        if sha and _SHA_RE.match(sha) and not _PLACEHOLDER_SHA_RE.match(sha):
            count += 1
    return count


def scan_provenance_spec_refs(repo_dir):
    """Unique spec_ref values recorded across every .foundry/build-provenance.yaml under
    repo_dir — the "built" side of the authorized->built conversion."""
    spec_refs = set()
    for dirpath, _dirnames, filenames in _walk_repo(repo_dir):
        if os.path.basename(dirpath) != ".foundry" or "build-provenance.yaml" not in filenames:
            continue
        path = os.path.join(dirpath, "build-provenance.yaml")
        data = None
        if yaml is not None:
            try:
                data = yaml.safe_load(open(path, encoding="utf-8")) or {}
            except Exception:
                data = None
        if data is None:
            continue
        for a in (data.get("authorizations") or []):
            sr = (a or {}).get("spec_ref")
            if sr:
                spec_refs.add(sr)
    return spec_refs


def scan_audit_rounds(repo_dir):
    """Sum of the `rounds` field across every .foundry/audit-ledger.jsonl row under repo_dir
    (v1 legacy rows and v2 rows both carry `rounds`)."""
    total = 0
    for dirpath, _dirnames, filenames in _walk_repo(repo_dir):
        if os.path.basename(dirpath) != ".foundry" or "audit-ledger.jsonl" not in filenames:
            continue
        path = os.path.join(dirpath, "audit-ledger.jsonl")
        for row in read_jsonl(path):
            r = row.get("rounds")
            if isinstance(r, int):
                total += r
    return total


# --------------------------------------------------------------------------------------------- #
# ratio assembly
# --------------------------------------------------------------------------------------------- #


def _ratio(num, den):
    return {"numerator": num, "denominator": den, "ratio": (round(num / den, 4) if den else None)}


def build_report(since_str, projects_dirs, repo, sample_size=40):
    since_ts = None
    if since_str:
        since_ts = datetime.strptime(since_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()

    all_stops = []
    all_denials = []
    files_scanned = 0
    for pd in projects_dirs:
        for path in iter_jsonl_files(pd, since_ts):
            files_scanned += 1
            result = mine_file(path)
            all_stops.extend(result["stops"])
            all_denials.extend(result["denials"])

    silent_yield = _ratio(sum(1 for s in all_stops if s["silent"]), len(all_stops))
    directive_reply = _ratio(sum(1 for s in all_stops if s["reply_len"] <= 45), len(all_stops))

    allow_rules = load_allow_rules(repo) if repo else []
    granted_hits = [
        d for d in all_denials if d.get("verb") and is_granted(d.get("tool_name"), d.get("verb"), allow_rules)
    ]
    granted_verb_denial = _ratio(len(granted_hits), len(all_denials))

    sample = all_denials[: max(0, sample_size)]
    guard_false_positive_sample = {
        "numerator": None,
        "denominator": len(sample),
        "ratio": None,
        "denials_total": len(all_denials),
        "sample": sample,
    }

    if repo:
        built_refs = scan_provenance_spec_refs(repo)
        authorized_n = scan_specs_authorized(repo)
        rounds_total = scan_audit_rounds(repo)
    else:
        built_refs, authorized_n, rounds_total = set(), 0, 0

    authorized_to_built = _ratio(len(built_refs), authorized_n)
    rounds_per_shipped_atom = _ratio(rounds_total, len(built_refs))

    return {
        "corpus": {
            "since": since_str,
            "until": datetime.now(timezone.utc).isoformat(),
            "projects_dirs": list(projects_dirs),
            "repo": repo,
            "files_scanned": files_scanned,
            "stops_found": len(all_stops),
            "denials_found": len(all_denials),
        },
        "ratios": {
            "silent_yield": silent_yield,
            "directive_reply": directive_reply,
            "granted_verb_denial": granted_verb_denial,
            "guard_false_positive_sample": guard_false_positive_sample,
            "authorized_to_built": authorized_to_built,
            "rounds_per_shipped_atom": rounds_per_shipped_atom,
        },
    }


def render_text(report):
    lines = []
    c = report["corpus"]
    lines.append(f"corpus: since={c['since']} until={c['until']} files_scanned={c['files_scanned']}")
    lines.append(f"        projects_dirs={c['projects_dirs']} repo={c['repo']}")
    for name, r in report["ratios"].items():
        if name == "guard_false_positive_sample":
            lines.append(
                f"{name}: SAMPLED {r['denominator']} of {r['denials_total']} denials — "
                f"not auto-classified, see 'sample' in --json output"
            )
            continue
        lines.append(f"{name}: {r['ratio']} ({r['numerator']}/{r['denominator']})")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------------------------- #


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="foundry-autonomy-instrument.py",
        description=(
            "Report-only autonomy instrument: six ratios (silent-yield, directive-reply, "
            "granted-verb-denial, guard-false-positive-sample, authorized->built conversion, "
            "rounds-per-shipped-atom) over Claude Code transcripts + the authorization corpus. "
            "Gates nothing; always exits 0."
        ),
    )
    p.add_argument("--since", required=True, metavar="YYYY-MM-DD", help="corpus window start date")
    p.add_argument(
        "--projects-dir",
        action="append",
        default=[],
        dest="projects_dirs",
        metavar="DIR",
        help="directory tree of Claude Code transcripts (*.jsonl); repeatable",
    )
    p.add_argument(
        "--repo",
        default=None,
        metavar="DIR",
        help="workspace/repo root for the corpus-side ratios (authorized->built, rounds-per-shipped-atom, "
        "and the settings allow-rules used by granted-verb-denial); optional",
    )
    p.add_argument("--json", action="store_true", help="emit one JSON object instead of text lines")
    p.add_argument("--out", default=None, metavar="PATH", help="also write the JSON report to PATH")
    p.add_argument(
        "--sample-size",
        type=int,
        default=40,
        help="max denials in the guard-false-positive sample (default 40)",
    )
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    report = build_report(args.since, args.projects_dirs, args.repo, sample_size=args.sample_size)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=False))
    else:
        print(render_text(report))
    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
        except OSError as e:
            print(f"warning: could not write --out {args.out!r}: {e}", file=sys.stderr)
    return 0  # AC-INS-5: always exit 0, this instrument gates nothing


if __name__ == "__main__":
    sys.exit(main())
