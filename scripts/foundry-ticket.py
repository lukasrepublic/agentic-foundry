#!/usr/bin/env python3
"""foundry-ticket — the unit of work is a GitHub issue with a runnable "Done means".

    foundry-ticket.py start <issue|url> [--repo OWNER/NAME] [--trust-author]
    foundry-ticket.py allow <path> [<path>...]        permit writing a paper path for this ticket
    foundry-ticket.py done                            run the ticket's Done-means command
    foundry-ticket.py status                          print the active ticket
    foundry-ticket.py clear                           forget the active ticket

The active ticket lives in the repository's GIT DIR (per worktree; never checked out, never committed).
Two hooks read it: `hooks/foundry-paper-guard.py` (a write to specs/, .foundry/, docs/, status-reports/,
charters/ is refused unless the ticket allows the path) and `hooks/foundry-ticket-stop.py` (the session
may not stop while the Done-means command fails). That is the whole governance model: one ticket, one
runnable check, the platform's branch protection + CI as the gate.

Issue body conventions (both optional, both plain Markdown):

    ## Done means
    ```
    python3 -m pytest tests/ -q
    ```
    ## Paper allowed
    CHANGELOG.md, docs/how-to/x.md

Only the FENCED block under `## Done means` is ever executed, and only after `start` confirmed the
issue's author has write access to the issue's own repository. `--trust-author` overrides that for a
command the OPERATOR has read; it is not silently allowed by the plugin's permission hook, and neither
is `done` (both execute input). Only stdlib. `gh` must be authenticated for `start`.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import foundry_ticket_store as store  # noqa: E402

STOP_CAP = 3  # how many times the Stop hook may refuse before it lets the session end
TRUSTED_PERMISSIONS = {"admin", "maintain", "write"}
project_root = store.project_root


def ticket_path(root: Path | None = None) -> Path | None:
    return store.ticket_file(root)


def load(root: Path | None = None) -> dict | None:
    p = ticket_path(root)
    if not p or not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save(doc: dict, root: Path | None = None) -> Path:
    p = ticket_path(root)
    if not p:
        sys.exit("foundry-ticket: not a git repository — a ticket needs one (it lives in the git dir)")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return p


def norm_path(entry: str, root: Path | None = None) -> str:
    """Root-relative POSIX form of a paper-allowed entry: strips `./`, a trailing ` — note`,
    backticks/bullets, and resolves an absolute path under the root. Returns "" when unusable."""
    s = entry.strip().strip("-*`• ").strip()
    for sep in (" — ", " - ", " –"):
        if sep in s:
            s = s.split(sep, 1)[0].strip()
    s = s.strip("`").strip()
    if not s or s.startswith("#"):
        return ""
    p = Path(s).expanduser()
    if p.is_absolute():
        try:
            p = p.resolve().relative_to((root or project_root()))
        except Exception:
            return ""
    parts = [x for x in p.as_posix().split("/") if x not in ("", ".")]
    if ".." in parts:
        return ""
    return "/".join(parts)


_DONE_RE = re.compile(r"^##+[ \t]*done[ \t]+means\b.*?$", re.I | re.M)
_PAPER_RE = re.compile(r"^##+[ \t]*paper[ \t]+allowed\b.*?$", re.I | re.M)
_FENCE_RE = re.compile(r"^(```+|~~~+)[^\n]*\n(.*?)^\1[ \t]*$", re.S | re.M)
_HEADING_RE = re.compile(r"^##+[ \t]", re.M)
_ISSUE_URL_RE = re.compile(r"https?://[^/]+/([^/]+/[^/]+)/issues/\d+")


def parse_body(body: str) -> tuple[str, list[str]]:
    """Return (done_cmd, paper_allowed). Only a fenced block directly under `## Done means` counts
    as the command; an unfenced section yields "" (nothing is ever executed from prose)."""
    body = (body or "").replace("\r\n", "\n").replace("\r", "\n")
    done = ""
    m = _DONE_RE.search(body)
    if m:
        rest = body[m.end():]
        fm = _FENCE_RE.search(rest)
        nxt = _HEADING_RE.search(rest)
        if fm and (nxt is None or fm.start() < nxt.start()):
            done = fm.group(2).strip()
    paper: list[str] = []
    pm = _PAPER_RE.search(body)
    if pm:
        rest = body[pm.end():]
        nxt = _HEADING_RE.search(rest)
        sec = rest[: nxt.start()] if nxt else rest
        for raw in re.split(r"[,\n]", sec):
            item = norm_path(raw)
            if item and item not in paper:
                paper.append(item)
    return done, paper


def _gh_json(args: list[str]) -> dict | None:
    out = subprocess.run(["gh", *args], capture_output=True, text=True)
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except Exception:
        return None


def repo_of_issue(issue: dict, explicit: str | None) -> str | None:
    """OWNER/NAME of the issue itself (from its URL) — the permission check must run against the
    repository the issue lives in, never the local checkout (security review of #270, R5)."""
    m = _ISSUE_URL_RE.match(issue.get("url") or "")
    if m:
        return m.group(1)
    return explicit


def author_trusted(repo: str | None, login: str, gh_json=_gh_json) -> tuple[bool, str]:
    """True when `login` has write/maintain/admin on `repo` (or is the authenticated user)."""
    if not login:
        return False, "issue author unknown"
    me = gh_json(["api", "user"]) or {}
    if me.get("login") and me["login"] == login:
        return True, "issue author is the authenticated user"
    if not repo:
        return False, "the issue's repository could not be determined"
    perm = gh_json(["api", f"repos/{repo}/collaborators/{login}/permission"]) or {}
    level = perm.get("permission") or ""
    if level in TRUSTED_PERMISSIONS:
        return True, f"issue author {login} has {level} access on {repo}"
    return False, f"issue author {login} has {level or 'no'} access on {repo}"


def cmd_start(args: list[str]) -> int:
    if not args:
        sys.exit("usage: foundry-ticket.py start <issue|url> [--repo OWNER/NAME] [--trust-author]")
    number = args[0]
    repo = None
    if "--repo" in args:
        i = args.index("--repo")
        if i + 1 >= len(args):
            sys.exit("foundry-ticket: --repo needs OWNER/NAME")
        repo = args[i + 1]
    trust = "--trust-author" in args
    cmd = ["issue", "view", str(number), "--json", "number,title,body,url,author,state"]
    if repo:
        cmd += ["--repo", repo]
    issue = _gh_json(cmd)
    if issue is None:
        sys.exit("foundry-ticket: gh issue view failed (is gh authenticated, and is the issue number right?)")
    done, paper = parse_body(issue.get("body") or "")
    login = ((issue.get("author") or {}).get("login")) or ""
    issue_repo = repo_of_issue(issue, repo)
    trusted_by = "author"
    if done and not trust:
        ok, why = author_trusted(issue_repo, login)
        if not ok:
            sys.exit("foundry-ticket: the Done-means command in this issue was not adopted — " + why +
                     ". The Stop hook would run it unattended, so a command from outside the repository's "
                     "write team needs the OPERATOR to read it and start the ticket themselves. Nothing was recorded.")
    elif done and trust:
        trusted_by = "flag"
    doc = {
        "issue": issue["number"],
        "url": issue.get("url", ""),
        "repo": issue_repo or "",
        "title": issue.get("title", ""),
        "author": login,
        "state": issue.get("state", ""),
        "done": done,
        "done_sha256": store.command_digest(done),
        "trusted_by": trusted_by,
        "paper_allowed": paper,
        "status": "open",
        "stop_blocks": 0,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    p = save(doc)
    print(f"ticket #{doc['issue']} active: {doc['title']}")
    print(f"  done means : {store.redact(done) or '(none declared — add a fenced block under `## Done means`)'}")
    print(f"  paper      : {', '.join(paper) if paper else '(none — paper writes are refused)'}")
    print(f"  recorded   : {p}")
    return 0


def cmd_allow(args: list[str]) -> int:
    root = project_root()
    doc = load(root)
    if not doc:
        sys.exit("foundry-ticket: no active ticket; run `foundry-ticket.py start <issue>` first")
    if not args:
        sys.exit("usage: foundry-ticket.py allow <path> [<path>...]")
    for a in args:
        n = norm_path(a, root)
        if not n:
            sys.exit(f"foundry-ticket: {a!r} is not a path under the project root")
        if n not in doc["paper_allowed"]:
            doc["paper_allowed"].append(n)
    save(doc, root)
    print("paper allowed for #%s: %s" % (doc["issue"], ", ".join(doc["paper_allowed"])))
    return 0


def run_done(doc: dict, root: Path, timeout: int = 1800) -> tuple[int, str]:
    cmd = (doc.get("done") or "").strip()
    if not cmd:
        return 0, "no Done-means command declared"
    if store.command_digest(cmd) != doc.get("done_sha256"):
        return 125, "the recorded Done-means command does not match its digest; re-run `start`"
    proc = None
    try:
        proc = subprocess.Popen(["/bin/bash", "-c", cmd], cwd=str(root), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, start_new_session=True)
        try:
            out, _ = proc.communicate(timeout=timeout)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            out, rc = "", 124
    except OSError as e:
        return 127, str(e)
    finally:
        if proc is not None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)   # nothing the check started outlives it
            except (ProcessLookupError, PermissionError, OSError):
                pass
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
    if rc == 124:
        return 124, f"timed out after {timeout}s"
    tail = (out or "").strip().splitlines()[-20:]
    return rc, store.redact("\n".join(tail))


def cmd_done(_args: list[str]) -> int:
    root = project_root()
    doc = load(root)
    if not doc:
        sys.exit("foundry-ticket: no active ticket")
    rc, tail = run_done(doc, root)
    if rc == 0:
        doc = load(root) or doc
        doc["status"] = "done"
        doc["done_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        save(doc, root)
        print(f"ticket #{doc['issue']}: Done means PASSED")
        return 0
    print(f"ticket #{doc['issue']}: Done means FAILED (rc={rc})\n{tail}")
    return 1


def cmd_status(_args: list[str]) -> int:
    doc = load()
    if not doc:
        print("no active ticket")
        return 1
    print(json.dumps(doc, indent=2))
    return 0


def cmd_clear(_args: list[str]) -> int:
    p = ticket_path()
    if p and p.is_file():
        p.unlink()
        print("ticket cleared")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    verb, rest = argv[0], argv[1:]
    table = {"start": cmd_start, "allow": cmd_allow, "done": cmd_done,
             "status": cmd_status, "clear": cmd_clear}
    if verb not in table:
        sys.exit(f"foundry-ticket: unknown verb {verb!r}; one of {', '.join(table)}")
    return table[verb](rest)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
