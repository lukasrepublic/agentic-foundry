#!/usr/bin/env python3
"""foundry-ticket — the unit of work is a GitHub issue with a runnable "Done means".

    foundry-ticket.py start <issue> [--repo OWNER/NAME] [--trust-author]
    foundry-ticket.py allow <path> [<path>...]        permit writing a paper path for this ticket
    foundry-ticket.py done                            run the ticket's Done-means command
    foundry-ticket.py status                          print the active ticket
    foundry-ticket.py clear                           forget the active ticket

The active ticket lives at `<project>/.claude/foundry-ticket.json` (excluded via .git/info/exclude).
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

Only the FENCED block under `## Done means` is ever executed. Because the Stop hook runs that command
unattended, `start` refuses an issue whose author lacks write access to the repository unless
`--trust-author` is given. Only stdlib. `gh` must be authenticated for `start`.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

TICKET_REL = Path(".claude") / "foundry-ticket.json"
STOP_CAP = 3  # how many times the Stop hook may refuse before it lets the session end
TRUSTED_PERMISSIONS = {"admin", "maintain", "write"}


def project_root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and Path(env).is_dir():
        return Path(env).resolve()
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, check=True).stdout.strip()
        return Path(top).resolve()
    except Exception:
        return Path.cwd().resolve()


def ticket_path(root: Path | None = None) -> Path:
    return (root or project_root()) / TICKET_REL


def load(root: Path | None = None) -> dict | None:
    p = ticket_path(root)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save(doc: dict, root: Path | None = None) -> Path:
    root = root or project_root()
    p = ticket_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    _ensure_excluded(root)
    return p


def _ensure_excluded(root: Path) -> None:
    """Hide the ticket file from git via .git/info/exclude — never by editing the tracked .gitignore."""
    line = ".claude/foundry-ticket.json"
    try:
        gp = subprocess.run(["git", "-C", str(root), "rev-parse", "--git-path", "info/exclude"],
                            capture_output=True, text=True, timeout=10)
        if gp.returncode != 0:
            return
        ex = Path(gp.stdout.strip())
        if not ex.is_absolute():
            ex = root / ex
        existing = ex.read_text(encoding="utf-8") if ex.is_file() else ""
        if line not in existing.splitlines():
            ex.parent.mkdir(parents=True, exist_ok=True)
            with ex.open("a", encoding="utf-8") as fh:
                if existing and not existing.endswith("\n"):
                    fh.write("\n")
                fh.write(line + "\n")
    except Exception:
        pass


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


def author_trusted(repo: str | None, login: str, gh_json=_gh_json) -> tuple[bool, str]:
    """True when `login` has write/maintain/admin on the repo (or is the authenticated user)."""
    me = gh_json(["api", "user"]) or {}
    if me.get("login") and me["login"] == login:
        return True, "issue author is the authenticated user"
    if not repo:
        rv = gh_json(["repo", "view", "--json", "nameWithOwner"]) or {}
        repo = rv.get("nameWithOwner")
    if not repo:
        return False, "repository could not be determined"
    perm = gh_json(["api", f"repos/{repo}/collaborators/{login}/permission"]) or {}
    level = perm.get("permission") or ""
    if level in TRUSTED_PERMISSIONS:
        return True, f"issue author {login} has {level} access on {repo}"
    return False, f"issue author {login} has {level or 'no'} access on {repo}"


def cmd_start(args: list[str]) -> int:
    if not args:
        sys.exit("usage: foundry-ticket.py start <issue> [--repo OWNER/NAME] [--trust-author]")
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
    if done and not trust:
        ok, why = author_trusted(repo, login)
        if not ok:
            sys.exit("foundry-ticket: refusing to adopt a Done-means command from an untrusted issue author "
                     f"({why}). The Stop hook would run it unattended. Re-run with --trust-author if you "
                     "have read the command and accept it:\n    " + done.replace("\n", "\n    "))
    doc = {
        "issue": issue["number"],
        "url": issue.get("url", ""),
        "title": issue.get("title", ""),
        "author": login,
        "state": issue.get("state", ""),
        "done": done,
        "paper_allowed": paper,
        "status": "open",
        "stop_blocks": 0,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    p = save(doc)
    print(f"ticket #{doc['issue']} active: {doc['title']}")
    print(f"  done means : {done or '(none declared — add a fenced block under `## Done means`)'}")
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
    try:
        proc = subprocess.Popen(["/bin/bash", "-c", cmd], cwd=str(root), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, start_new_session=True)
        try:
            out, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, 9)
            except Exception:
                pass
            proc.communicate()
            return 124, f"timed out after {timeout}s"
    except OSError as e:
        return 127, str(e)
    tail = (out or "").strip().splitlines()[-20:]
    return proc.returncode, "\n".join(tail)


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
    if p.is_file():
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
