#!/usr/bin/env python3
"""foundry-ticket — the unit of work is a GitHub issue with a runnable "Done means".

    foundry-ticket.py start <issue> [--repo OWNER/NAME]   read the issue, make it the active ticket
    foundry-ticket.py allow <path> [<path>...]            permit writing a paper path for this ticket
    foundry-ticket.py done                                run the ticket's Done-means command
    foundry-ticket.py status                              print the active ticket
    foundry-ticket.py clear                               forget the active ticket

The active ticket lives at `<project>/.claude/foundry-ticket.json` (gitignored). Two hooks read it:
`hooks/foundry-paper-guard.py` (a write to specs/, .foundry/, docs/, status-reports/, charters/ is
refused unless the ticket allows the path) and `hooks/foundry-ticket-stop.py` (the session may not
stop while the Done-means command fails). That is the whole governance model: one ticket, one
runnable check, the platform's branch protection + CI as the gate.

Issue body conventions (both optional, both plain Markdown):

    ## Done means
    ```
    python3 -m pytest tests/ -q
    ```
    ## Paper allowed
    CHANGELOG.md, docs/how-to/x.md

Only stdlib. `gh` must be authenticated for `start`.
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
    p = ticket_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    _ensure_gitignored(p.parent.parent)
    return p


def _ensure_gitignored(root: Path) -> None:
    gi = root / ".gitignore"
    line = ".claude/foundry-ticket.json"
    try:
        existing = gi.read_text(encoding="utf-8") if gi.is_file() else ""
        if line not in existing.splitlines():
            with gi.open("a", encoding="utf-8") as fh:
                if existing and not existing.endswith("\n"):
                    fh.write("\n")
                fh.write(line + "\n")
    except OSError:
        pass


_DONE_RE = re.compile(r"^##+\s*done\s+means\b.*?$", re.I | re.M)
_PAPER_RE = re.compile(r"^##+\s*paper\s+allowed\b.*?$", re.I | re.M)
_FENCE_RE = re.compile(r"```[a-zA-Z0-9_-]*\n(.*?)```", re.S)


def _section_after(body: str, heading_re: re.Pattern) -> str:
    m = heading_re.search(body)
    if not m:
        return ""
    rest = body[m.end():]
    nxt = re.search(r"^##+\s", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def parse_body(body: str) -> tuple[str, list[str]]:
    """Return (done_cmd, paper_allowed) from an issue body."""
    done = ""
    sec = _section_after(body, _DONE_RE)
    if sec:
        fm = _FENCE_RE.search(sec)
        done = (fm.group(1) if fm else sec).strip()
        # a multi-line fenced block runs as one shell script; keep it verbatim
    paper: list[str] = []
    psec = _section_after(body, _PAPER_RE)
    for raw in re.split(r"[,\n]", psec):
        item = raw.strip().strip("-*`• ").strip()
        if item and not item.startswith("#"):
            paper.append(item)
    return done, paper


def _gh_issue(number: str, repo: str | None) -> dict:
    cmd = ["gh", "issue", "view", str(number), "--json", "number,title,body,url"]
    if repo:
        cmd += ["--repo", repo]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit("foundry-ticket: gh issue view failed: " + out.stderr.strip())
    return json.loads(out.stdout)


def cmd_start(args: list[str]) -> int:
    if not args:
        sys.exit("usage: foundry-ticket.py start <issue> [--repo OWNER/NAME]")
    number = args[0]
    repo = None
    if "--repo" in args:
        repo = args[args.index("--repo") + 1]
    issue = _gh_issue(number, repo)
    done, paper = parse_body(issue.get("body") or "")
    doc = {
        "issue": issue["number"],
        "url": issue.get("url", ""),
        "title": issue.get("title", ""),
        "done": done,
        "paper_allowed": paper,
        "status": "open",
        "stop_blocks": 0,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    p = save(doc)
    print(f"ticket #{doc['issue']} active: {doc['title']}")
    print(f"  done means : {done or '(none declared — add a `## Done means` block to the issue)'}")
    print(f"  paper      : {', '.join(paper) if paper else '(none — paper writes are refused)'}")
    print(f"  recorded   : {p}")
    return 0


def cmd_allow(args: list[str]) -> int:
    doc = load()
    if not doc:
        sys.exit("foundry-ticket: no active ticket; run `foundry-ticket.py start <issue>` first")
    if not args:
        sys.exit("usage: foundry-ticket.py allow <path> [<path>...]")
    for a in args:
        if a not in doc["paper_allowed"]:
            doc["paper_allowed"].append(a)
    save(doc)
    print("paper allowed for #%s: %s" % (doc["issue"], ", ".join(doc["paper_allowed"])))
    return 0


def run_done(doc: dict, root: Path, timeout: int = 1800) -> tuple[int, str]:
    cmd = doc.get("done") or ""
    if not cmd.strip():
        return 0, "no Done-means command declared"
    try:
        out = subprocess.run(["bash", "-lc", cmd], cwd=str(root), capture_output=True,
                             text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    tail = (out.stdout + out.stderr).strip().splitlines()[-20:]
    return out.returncode, "\n".join(tail)


def cmd_done(_args: list[str]) -> int:
    root = project_root()
    doc = load(root)
    if not doc:
        sys.exit("foundry-ticket: no active ticket")
    rc, tail = run_done(doc, root)
    if rc == 0:
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
