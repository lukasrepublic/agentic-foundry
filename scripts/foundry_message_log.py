#!/usr/bin/env python3
"""foundry_message_log -- the shared message log (gv-09): an append-only JSON-lines log on a protected git
branch for machines; one issue per topic for people.

WHAT IT IS. Several sessions on several machines cannot see each other's terminals. They coordinate through
one orphan branch, `foundry/messages`, that only this script writes: files `messages/YYYY-MM.jsonl`, one JSON
object per line, and a `.gitattributes` carrying `*.jsonl merge=union` so a human merge of two writers keeps
both lines. A record is a small CloudEvents-shaped envelope:

    {"v":1, "id":"<ULID>", "time":"<RFC 3339 UTC>", "from":"<session>", "to":"<id|all>",
     "kind":"<closed set, see foundry_message_kind.py>", "corr":"<joins BEFORE/AFTER/STOP>", "refs":[...], "body":"..."}

Identity is `id`; order is commit order; `time` is informational. Readers dedupe by id and skip + report
malformed lines.

VERBS
  post --to <id|all> --kind <K> [--corr <id>] [--refs R ...] --body-file <f|-> [--from <me>]
        Lint (foundry_message_kind), then build a commit with git PLUMBING on top of the fetched tip (the work
        tree and index are never touched), push it, and verify by read-back that the id is on the pushed tip.
        On a non-fast-forward rejection: refetch, re-append to the NEW tip, retry up to 5 times (2 s base,
        doubling, 50 % jitter, ~2 min total cap). Never force. Any other push failure, and exhaustion, is a hard
        failure: exit 1 with the reason on stderr.
  read --to <me> [--since <commit>] [--kind K ...] [--session S] [--no-advance]
        Single-branch fetch, then the records added in `cursor..tip` addressed to <me> or `all`, deduped. The
        cursor is the last processed commit, stored in the git dir (`git rev-parse --git-path
        foundry-message-cursor`), never the work tree. Without --since the stored cursor is used (none = the
        whole log) and is advanced to the tip afterwards.
  audit [--open-minutes N]
        Replay query. Flags a PROD-AFTER / STOP whose corr has no earlier PROD-BEFORE, a PROD-BEFORE with no
        PROD-AFTER / STOP after N minutes (N defaults to the policy's messaging.open_change_minutes, 120), and a
        PROD-* record with no corr. Exit 3 when anything is flagged.
  import <issue-url>
        One-time migration of an issue's comments into the log. Paginated; kind inferred from `TO:` / `BEFORE` /
        `AFTER` lines, else FINDING; idempotent by comment id (each record's refs carries the comment anchor).
  ruleset-template
        Prints the branch-ruleset JSON (block force-push, restrict deletion) for `foundry/messages`.

EVERY git / gh call goes through the ONE `Adapter` class, so tests inject a stub. Exit: 0 ok / 1 hard failure /
2 usage or refused input / 3 lint refusal or audit findings. Stdlib only (+ the policy loader). No hidden network
beyond the git remote and, for `import`, `gh`.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import random
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import foundry_message_kind as _kind  # noqa: E402

BRANCH = "foundry/messages"
FETCHED_REF = "refs/foundry/messages-fetched"
GITATTRIBUTES = "*.jsonl merge=union\n"
CURSOR_BASENAME = "foundry-message-cursor"
MAX_RETRIES = 5
BACKOFF_BASE_S = 2.0
BACKOFF_TOTAL_CAP_S = 120.0
IMPORT_PAGE = 100
IMPORT_ROW_CEILING = 30000
_ISSUE_URL_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/(\d+)/?(?:#.*)?$")
_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class LogError(Exception):
    """A hard failure: the message carries the reason; the CLI prints it and exits 1."""


# ---------------------------------------------------------------------------------------------
# the ONE adapter: every git and gh call in this file goes through it
# ---------------------------------------------------------------------------------------------
class Adapter:
    def git(self, args, cwd, env=None, input_text=None):
        full_env = dict(os.environ)
        full_env.update(env or {})
        full_env["GIT_TERMINAL_PROMPT"] = "0"
        return subprocess.run(["git", "-C", str(cwd), *args], input=input_text, capture_output=True,
                              text=True, env=full_env, timeout=300)

    def gh(self, args, stdin=None):
        r = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise RuntimeError(f"gh {' '.join(args[:2])} failed: {r.stderr.strip()[:300]}")
        return r.stdout

    def issue_comments_page(self, owner, repo, number, page, per_page=IMPORT_PAGE):
        out = self.gh(["api", f"repos/{owner}/{repo}/issues/{number}/comments?per_page={per_page}&page={page}"])
        data = json.loads(out)
        if not isinstance(data, list):
            raise RuntimeError("gh api returned a non-list for issue comments")
        return data


# ---------------------------------------------------------------------------------------------
# small pure helpers
# ---------------------------------------------------------------------------------------------
def _now():
    return _dt.datetime.now(_dt.timezone.utc)


def _stamp(dt):
    dt = dt.astimezone(_dt.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _parse_time(s):
    return _dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(_dt.timezone.utc)


def new_ulid(when=None):
    ms = int((when or _now()).timestamp() * 1000) & ((1 << 48) - 1)
    value = (ms << 80) | int.from_bytes(secrets.token_bytes(10), "big")
    return "".join(_B32[(value >> (5 * i)) & 31] for i in range(25, -1, -1))


def _month_path(time_str):
    return f"messages/{time_str[:7]}.jsonl"


def make_record(sender, to, kind, body, corr=None, refs=None, when=None):
    when = when or _now()
    rec = {"v": 1, "id": new_ulid(when), "time": _stamp(when), "from": sender, "to": to, "kind": kind}
    if corr:
        rec["corr"] = corr
    rec["refs"] = list(refs or [])
    rec["body"] = body
    return rec


def _line(rec):
    return json.dumps(rec, ensure_ascii=False, separators=(",", ":"), sort_keys=False)


def _project_dir(arg):
    return Path(arg or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())


def _retryable_push_failure(text):
    t = text.lower()
    if "[rejected]" in t and ("non-fast-forward" in t or "fetch first" in t):
        return True
    if "[remote rejected]" in t and ("failed to update ref" in t or "cannot lock ref" in t):
        return True
    return False


def backoff_delays(rng=random.random):
    """The 5 sleep durations: 2 s base, doubling, 50 % jitter (x0.5 .. x1.5), total capped at ~2 minutes."""
    out, total = [], 0.0
    for i in range(MAX_RETRIES):
        d = BACKOFF_BASE_S * (2 ** i) * (0.5 + rng())
        d = min(d, max(0.0, BACKOFF_TOTAL_CAP_S - total))
        out.append(d)
        total += d
    return out


# ---------------------------------------------------------------------------------------------
# git plumbing over the message branch
# ---------------------------------------------------------------------------------------------
def _git_ok(ad, repo, args, what, env=None, input_text=None):
    r = ad.git(args, repo, env=env, input_text=input_text)
    if r.returncode != 0:
        raise LogError(f"{what} failed: {(r.stderr or r.stdout).strip()[:400]}")
    return r.stdout


def fetch_tip(ad, repo, remote):
    """Single-branch fetch of the message branch into a private ref. Returns the tip sha, or None when the
    remote has no such branch yet. Any other failure is a hard error."""
    r = ad.git(["fetch", "--no-tags", "--quiet", remote, f"+refs/heads/{BRANCH}:{FETCHED_REF}"], repo)
    if r.returncode != 0:
        if "couldn't find remote ref" in (r.stderr or ""):
            ad.git(["update-ref", "-d", FETCHED_REF], repo)
            return None
        raise LogError(f"fetch of {BRANCH} from {remote!r} failed: {(r.stderr or r.stdout).strip()[:400]}")
    return _git_ok(ad, repo, ["rev-parse", FETCHED_REF], "rev-parse of the fetched tip").strip()


def _blob_text(ad, repo, tip, path):
    r = ad.git(["show", f"{tip}:{path}"], repo)
    return r.stdout if r.returncode == 0 else None


def _log_files(ad, repo, tip):
    out = _git_ok(ad, repo, ["ls-tree", "-r", "--name-only", tip, "--", "messages/"], "ls-tree")
    return sorted(p for p in out.splitlines() if p.endswith(".jsonl"))


def parse_lines(text):
    """-> (records, malformed[(reason, snippet)]). Envelope-only lint; body rules belong to the writer."""
    recs, bad = [], []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            bad.append((f"not-json: {e.msg}", raw[:120]))
            continue
        errs = _kind.lint(obj, strict_body=False)
        if errs:
            bad.append((errs[0], raw[:120]))
            continue
        recs.append(obj)
    return recs, bad


def replay(ad, repo, tip):
    """Every record in the log at `tip`, in commit/file order, deduped by id, with the malformed lines."""
    if tip is None:
        return [], []
    recs, bad, seen = [], [], set()
    for path in _log_files(ad, repo, tip):
        r, b = parse_lines(_blob_text(ad, repo, tip, path) or "")
        bad.extend(b)
        for rec in r:
            if rec["id"] not in seen:
                seen.add(rec["id"])
                recs.append(rec)
    return recs, bad


def _identity_env(ad, repo):
    env = {}
    if not ad.git(["config", "user.name"], repo).stdout.strip():
        env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = "foundry-message-log"
    if not ad.git(["config", "user.email"], repo).stdout.strip():
        env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = "message-log@invalid"
    return env


def _build_commit(ad, repo, tip, records, message):
    """Plumbing only: a private index, no work-tree or real-index writes. Returns the new commit sha."""
    by_file = {}
    for rec in records:
        by_file.setdefault(_month_path(rec["time"]), []).append(rec)
    with tempfile.TemporaryDirectory(prefix="foundry-msglog-") as td:
        env = {"GIT_INDEX_FILE": os.path.join(td, "index")}
        env.update(_identity_env(ad, repo))
        if tip:
            _git_ok(ad, repo, ["read-tree", tip], "read-tree", env=env)
        else:
            _git_ok(ad, repo, ["read-tree", "--empty"], "read-tree --empty", env=env)

        def put(path, text):
            sha = _git_ok(ad, repo, ["hash-object", "-w", "--stdin"], "hash-object", env=env, input_text=text).strip()
            _git_ok(ad, repo, ["update-index", "--add", "--cacheinfo", f"100644,{sha},{path}"], "update-index", env=env)

        attrs = _blob_text(ad, repo, tip, ".gitattributes") if tip else None
        if attrs is None or GITATTRIBUTES.strip() not in attrs.splitlines():
            put(".gitattributes", (attrs or "") + ("" if not attrs or attrs.endswith("\n") else "\n") + GITATTRIBUTES)
        for path, recs in sorted(by_file.items()):
            cur = (_blob_text(ad, repo, tip, path) if tip else None) or ""
            if cur and not cur.endswith("\n"):
                cur += "\n"
            put(path, cur + "".join(_line(r) + "\n" for r in recs))
        tree = _git_ok(ad, repo, ["write-tree"], "write-tree", env=env).strip()
        args = ["-c", "commit.gpgsign=false", "commit-tree", tree, "-m", message]
        if tip:
            args += ["-p", tip]
        return _git_ok(ad, repo, args, "commit-tree", env=env).strip()


def _tree_of(ad, repo, rev):
    return _git_ok(ad, repo, ["rev-parse", f"{rev}^{{tree}}"], "rev-parse tree").strip()


def publish(ad, repo, remote, make_records, message, sleep=time.sleep, rng=random.random):
    """Append the records `make_records(existing_records)` returns to the message branch and push, retrying a
    non-fast-forward by refetching and re-appending. Returns (records_written, tip_after). Never forces."""
    delays = backoff_delays(rng)
    last = ""
    for attempt in range(MAX_RETRIES + 1):
        tip = fetch_tip(ad, repo, remote)
        existing, _bad = replay(ad, repo, tip)
        records = make_records(existing)
        if not records:
            return [], tip
        commit = _build_commit(ad, repo, tip, records, message)
        push = ad.git(["push", "--porcelain", remote, f"{commit}:refs/heads/{BRANCH}"], repo)
        out = (push.stdout or "") + (push.stderr or "")
        if push.returncode == 0:
            _read_back(ad, repo, remote, records)
            return records, commit
        if not _retryable_push_failure(out):
            raise LogError(f"push to {remote!r} refused (not a race, so not retried): {out.strip()[:400]}")
        last = out.strip()[:200]
        if attempt < MAX_RETRIES:
            sleep(delays[attempt])
    raise LogError(f"push lost the race {MAX_RETRIES + 1} times (retries exhausted); last rejection: {last}")


def _read_back(ad, repo, remote, records):
    tip = fetch_tip(ad, repo, remote)
    present = {r["id"] for r in replay(ad, repo, tip)[0]}
    missing = [r["id"] for r in records if r["id"] not in present]
    if missing:
        raise LogError(f"read-back verification failed: id(s) {missing} not on the pushed tip {tip}")


# ---------------------------------------------------------------------------------------------
# verbs
# ---------------------------------------------------------------------------------------------
def post(ad, repo, remote, sender, to, kind, body, corr=None, refs=None, sleep=time.sleep, rng=random.random):
    rec = make_record(sender, to, kind, body, corr, refs)
    errs = _kind.lint(rec)
    if errs:
        return None, errs
    written, _ = publish(ad, repo, remote, lambda existing: [rec], f"message-log: {kind} {rec['id']}", sleep, rng)
    return rec, []


def _cursor_path(ad, repo, session):
    base = CURSOR_BASENAME + (f".{re.sub(r'[^A-Za-z0-9_.-]', '_', session)}" if session else "")
    r = ad.git(["rev-parse", "--git-path", base], repo)
    if r.returncode != 0 or not r.stdout.strip():
        raise LogError("cannot locate the git dir for the read cursor")
    p = Path(r.stdout.strip())
    return p if p.is_absolute() else Path(repo) / p


def read(ad, repo, remote, me, since=None, kinds=None, session=None, advance=True):
    tip = fetch_tip(ad, repo, remote)
    if tip is None:
        return {"tip": None, "since": since, "records": [], "malformed": []}
    cpath = _cursor_path(ad, repo, session)
    explicit = since is not None
    if since is None and cpath.is_file():
        since = cpath.read_text(encoding="utf-8").strip() or None
    if since and since != tip:
        if ad.git(["merge-base", "--is-ancestor", since, tip], repo).returncode != 0:
            raise LogError(f"cursor {since!r} is not an ancestor of the fetched tip {tip}; pass --since <commit> "
                           "from the tip's history, or delete the cursor file")
        d = _git_ok(ad, repo, ["diff", "--no-color", "-U0", "--no-prefix", since, tip, "--", "messages/"], "diff")
        added = "\n".join(ln[1:] for ln in d.splitlines() if ln.startswith("+") and not ln.startswith("+++ "))
        recs, bad = parse_lines(added)
    elif since == tip:
        recs, bad = [], []
    else:
        recs, bad = replay(ad, repo, tip)
        bad = [{"reason": a, "line": b} for a, b in bad]
    seen, out = set(), []
    for r in recs:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        if r["to"] not in (me, "all"):
            continue
        if kinds and r["kind"] not in kinds:
            continue
        out.append(r)
    bad = [b if isinstance(b, dict) else {"reason": b[0], "line": b[1]} for b in bad]
    if advance and not explicit:
        cpath.parent.mkdir(parents=True, exist_ok=True)
        tmp = cpath.with_name(cpath.name + f".{os.getpid()}.tmp")
        tmp.write_text(tip + "\n", encoding="utf-8")
        os.replace(tmp, cpath)
    return {"tip": tip, "since": since, "records": out, "malformed": bad}


def audit_records(records, open_minutes, now):
    findings, befores, closed = [], {}, set()
    for r in records:
        k, c = r["kind"], r.get("corr")
        if k not in ("PROD-BEFORE", "PROD-AFTER", "STOP"):
            continue
        if not c:
            findings.append({"rule": "missing-corr", "id": r["id"], "kind": k})
        elif k == "PROD-BEFORE":
            befores.setdefault(c, r)
        else:
            if c not in befores:
                findings.append({"rule": "after-without-before" if k == "PROD-AFTER" else "stop-without-before",
                                 "corr": c, "id": r["id"], "kind": k})
            closed.add(c)
    for c, r in befores.items():
        if c in closed:
            continue
        age = (now - _parse_time(r["time"])).total_seconds() / 60.0
        if age >= open_minutes:
            findings.append({"rule": "before-open", "corr": c, "id": r["id"], "age_minutes": round(age, 1)})
    return findings


def audit(ad, repo, remote, open_minutes=None, now=None):
    if open_minutes is None:
        import foundry_operating_model as om
        open_minutes = om.load(repo).get("messaging.open_change_minutes")
    tip = fetch_tip(ad, repo, remote)
    recs, bad = replay(ad, repo, tip)
    findings = audit_records(recs, open_minutes, now or _now())
    return {"tip": tip, "records": len(recs), "open_minutes": open_minutes, "findings": findings,
            "malformed": [{"reason": a, "line": b} for a, b in bad]}


_TO_RE = re.compile(r"^\s*TO:\s*([^\s,;]+)", re.MULTILINE)
_BEFORE_RE = re.compile(r"^\s*(?:\*\*|#+\s*)?BEFORE\b", re.MULTILINE)
_AFTER_RE = re.compile(r"^\s*(?:\*\*|#+\s*)?AFTER\b", re.MULTILINE)


def infer(body):
    m = _TO_RE.search(body)
    to = m.group(1) if m else "all"
    if _BEFORE_RE.search(body):
        return to, "PROD-BEFORE"
    if _AFTER_RE.search(body):
        return to, "PROD-AFTER"
    return to, "FINDING"


def fetch_comments(ad, owner, repo_name, number):
    rows, page = [], 1
    while True:
        batch = ad.issue_comments_page(owner, repo_name, number, page, IMPORT_PAGE)
        rows.extend(batch)
        if len(rows) >= IMPORT_ROW_CEILING and len(batch) == IMPORT_PAGE:
            raise LogError(f"issue has at least {IMPORT_ROW_CEILING} comments, the listing ceiling; nothing imported")
        if len(batch) < IMPORT_PAGE:
            return rows
        page += 1


def import_issue(ad, repo, remote, url, sleep=time.sleep, rng=random.random):
    m = _ISSUE_URL_RE.match(url)
    if not m:
        raise LogError(f"not an issue URL (https://github.com/<owner>/<repo>/issues/<n>): {url!r}")
    owner, name, number = m.group(1), m.group(2), int(m.group(3))
    base = f"https://github.com/{owner}/{name}/issues/{number}"
    try:
        comments = fetch_comments(ad, owner, name, number)
    except (RuntimeError, ValueError) as e:
        raise LogError(f"listing comments failed: {e}") from e
    comments.sort(key=lambda c: (c.get("created_at") or "", c.get("id") or 0))
    planned, open_before, skipped_empty = [], [], 0
    for c in comments:
        body = c.get("body") or ""
        if not body.strip() or not c.get("id") or not c.get("created_at"):
            skipped_empty += 1
            continue
        to, kind = infer(body)
        corr = None
        if kind == "PROD-BEFORE":
            corr = f"import-{owner}-{name}-{number}-{c['id']}"
            open_before.append(corr)
        elif kind == "PROD-AFTER":
            corr = open_before.pop() if open_before else f"import-{owner}-{name}-{number}-{c['id']}"
        when = _parse_time(c["created_at"])
        rec = make_record(f"gh:{(c.get('user') or {}).get('login') or 'unknown'}", to, kind, body, corr,
                          [f"{base}#issuecomment-{c['id']}"], when)
        planned.append(rec)
    state = {"skipped_existing": 0}

    def make(existing):
        have = {ref for r in existing for ref in r.get("refs", [])}
        fresh = [r for r in planned if r["refs"][0] not in have]
        state["skipped_existing"] = len(planned) - len(fresh)
        for r in fresh:
            e = _kind.lint(r, strict_body=False)
            if e:
                raise LogError(f"imported record failed envelope lint: {e[0]}")
        return fresh

    written, _ = publish(ad, repo, remote, make, f"message-log: import {base}", sleep, rng)
    return {"imported": len(written), "skipped_existing": state["skipped_existing"], "skipped_empty": skipped_empty}


def ruleset_template():
    return {
        "name": "foundry-message-log-protect",
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {"ref_name": {"include": [f"refs/heads/{BRANCH}"], "exclude": []}},
        "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
    }


def git_equivalent_of(template):
    """The bare-remote settings that enforce the same rule types locally (used to observe a real rejection)."""
    types = {r["type"] for r in template["rules"]}
    cfg = {}
    if "non_fast_forward" in types:
        cfg["receive.denyNonFastForwards"] = "true"
    if "deletion" in types:
        cfg["receive.denyDeletes"] = "true"
    return cfg


def observe_ruleset_enforcement(ad, workdir, remote, branch):
    """Attempt a normal push (control) then a force-push to `branch` and report what was OBSERVED:
    `enforced` only when the control is accepted AND the force-push is rejected; `unverifiable:tier-b` when the
    force-push is accepted (rules created-not-enforced); `unverifiable:control-failed` when even the normal push
    was refused (nothing can be concluded). Never reports a pass it did not observe."""
    env = _identity_env(ad, workdir)
    commit = lambda msg: _git_ok(ad, workdir, ["-c", "commit.gpgsign=false", "commit", "--allow-empty", "-q", "-m", msg], "commit", env=env)  # noqa: E731
    commit("observe: base")
    commit("observe: control")
    if ad.git(["push", remote, f"HEAD:refs/heads/{branch}"], workdir).returncode != 0:
        return "unverifiable:control-failed"
    _git_ok(ad, workdir, ["reset", "--hard", "-q", "HEAD~1"], "reset")
    commit("observe: diverging")
    forced = ad.git(["push", "--force", remote, f"HEAD:refs/heads/{branch}"], workdir)
    return "enforced" if forced.returncode != 0 else "unverifiable:tier-b"


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------
def _default_sender():
    return os.environ.get("FOUNDRY_MESSAGE_FROM") or os.environ.get("CLAUDE_SESSION_ID") or socket.gethostname()


def build_parser():
    ap = argparse.ArgumentParser(prog="foundry_message_log.py", description="the shared message log")
    ap.add_argument("--repo", help="the git repository (default: $CLAUDE_PROJECT_DIR or cwd)")
    ap.add_argument("--remote", default=os.environ.get("FOUNDRY_MESSAGE_REMOTE", "origin"))
    sub = ap.add_subparsers(dest="verb", required=True)
    p = sub.add_parser("post")
    p.add_argument("--to", required=True)
    p.add_argument("--kind", required=True)
    p.add_argument("--corr")
    p.add_argument("--refs", nargs="*", default=[])
    p.add_argument("--body-file", required=True, help="path, or '-' for stdin")
    p.add_argument("--from", dest="sender", default=None)
    r = sub.add_parser("read")
    r.add_argument("--to", required=True)
    r.add_argument("--since")
    r.add_argument("--kind", action="append", default=[])
    r.add_argument("--session")
    r.add_argument("--no-advance", action="store_true")
    a = sub.add_parser("audit")
    a.add_argument("--open-minutes", type=int)
    a.add_argument("--now", help=argparse.SUPPRESS)
    i = sub.add_parser("import")
    i.add_argument("issue_url")
    sub.add_parser("ruleset-template")
    return ap


def main(argv=None, adapter=None, sleep=time.sleep, rng=random.random):
    args = build_parser().parse_args(argv)
    ad = adapter or Adapter()
    if args.verb == "ruleset-template":
        print(json.dumps(ruleset_template(), indent=2))
        return 0
    repo = _project_dir(args.repo)
    try:
        if args.verb == "post":
            try:
                body = sys.stdin.read() if args.body_file == "-" else Path(args.body_file).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                print(f"foundry_message_log: REFUSED -- cannot read the body: {e}", file=sys.stderr)
                return 2
            refs = [x for ref in args.refs for x in ref.split(",") if x]
            rec, errs = post(ad, repo, args.remote, args.sender or _default_sender(), args.to, args.kind, body,
                             args.corr, refs, sleep, rng)
            if errs:
                print(json.dumps({"posted": False, "errors": errs}, indent=2))
                return 3
            print(json.dumps({"posted": True, "id": rec["id"], "kind": rec["kind"]}))
            return 0
        if args.verb == "read":
            print(json.dumps(read(ad, repo, args.remote, args.to, args.since, set(args.kind) or None,
                                  args.session, not args.no_advance), indent=2))
            return 0
        if args.verb == "audit":
            now = _parse_time(args.now) if args.now else None
            res = audit(ad, repo, args.remote, args.open_minutes, now)
            print(json.dumps(res, indent=2))
            return 3 if res["findings"] else 0
        if args.verb == "import":
            print(json.dumps(import_issue(ad, repo, args.remote, args.issue_url, sleep, rng)))
            return 0
    except LogError as e:
        print(f"foundry_message_log: FAILED -- {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 -- surfaced, never swallowed (a policy error is a named refusal)
        name = type(e).__name__
        print(f"foundry_message_log: {'REFUSED' if name == 'PolicyError' else 'FAILED'} -- {name}: {e}", file=sys.stderr)
        return 2 if name == "PolicyError" else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
