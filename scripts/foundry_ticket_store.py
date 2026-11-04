"""foundry_ticket_store — where the active ticket lives, and the one redactor its outputs pass through.

Library (no argparse/__main__); imported by scripts/foundry-ticket.py, hooks/foundry-paper-guard.py and
hooks/foundry-ticket-stop.py.

The ticket is stored INSIDE THE GIT DIR (`git rev-parse --git-path foundry-ticket.json`, per worktree),
never in the working tree: a file under the work tree could be committed on a fork branch and checked
out by `gh pr checkout`, and the Stop hook would then run an attacker's command unattended (security
review of #270, B1). Nothing under `.claude/` in the work tree is ever read as a ticket. A project that
is not a git repository has no ticket.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

TICKET_BASENAME = "foundry-ticket.json"
CLAIMS_BASENAME = "foundry-claims.json"   # gn-08: the local execution record of claimed issues (a cache, never authority)
LEASE_RELPATH = Path(".foundry") / "runtime" / "lead.json"   # gn-08: the local duplicate-lead lease

_SECRET_ASSIGN = re.compile(
    r"(?i)\b([A-Za-z_][A-Za-z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|APIKEY|API_KEY|_KEY|_PAT))=(\S+)")
_BEARER = re.compile(r"(?i)\b(authorization:\s*bearer)\s+\S+")
_GH_TOKEN = re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")


def redact(s: str) -> str:
    s = _SECRET_ASSIGN.sub(r"\1=<redacted>", s or "")
    s = _BEARER.sub(r"\1 <redacted>", s)
    return _GH_TOKEN.sub("<redacted>", s)


def project_root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and Path(env).is_dir():
        return Path(env).resolve()
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, check=True, timeout=10).stdout.strip()
        return Path(top).resolve()
    except Exception:
        return Path.cwd().resolve()


def ticket_file(root: Path | None = None) -> Path | None:
    """The git-dir path of the ticket for `root` (None when root is not a git repository)."""
    return _git_path(TICKET_BASENAME, root)


def claims_file(root: Path | None = None) -> Path | None:
    """The git-dir path of the claim records for `root` (None when root is not a git repository)."""
    return _git_path(CLAIMS_BASENAME, root)


def claim_done_digest(root: Path | None, repo: str, number) -> str | None:
    """The vetted Done-means digest a `claim` recorded for repo#number in the git dir, or None when there is
    no claim record carrying one (the unclaimed path keeps its existing behaviour)."""
    p = claims_file(root)
    try:
        d = json.loads(p.read_text(encoding="utf-8")) if p and p.is_file() else {}
        rec = d.get(f"{repo}#{number}") if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None
    dd = rec.get("done_digest") if isinstance(rec, dict) else None
    return dd if isinstance(dd, str) and dd else None


def lease_file(root: Path | None = None) -> Path:
    return (root or project_root()) / LEASE_RELPATH


def _inside(root, path) -> bool:
    r, p = os.path.realpath(root), os.path.realpath(path)
    return p == r or p.startswith(r + os.sep)


def confined_makedirs(ws, d) -> None:
    """makedirs whose containment is decided on the nearest EXISTING ancestor BEFORE anything is created, and
    re-checked after (the same rule as foundry_migrate.ensure_dir, kept stdlib-only here so the always-on
    runtime writers need no YAML dependency). A symlinked directory that leaves the workspace is a refusal."""
    cur = str(d)
    while not os.path.lexists(cur):
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    if not os.path.isdir(cur) or not _inside(ws, cur):
        raise PermissionError(f"refusing to create a directory outside the workspace: {d}")
    os.makedirs(d, exist_ok=True)
    if not _inside(ws, d):
        raise PermissionError(f"refusing to use a directory outside the workspace: {d}")


def ensure_runtime_dir(ws) -> Path:
    """`<ws>/.foundry/runtime`, confined, with a `*` .gitignore so a runtime file is never committed."""
    d = Path(ws) / ".foundry" / "runtime"
    confined_makedirs(ws, d)
    gi = d / ".gitignore"
    try:
        fd = os.open(gi, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    except FileExistsError:
        return d
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("*\n")
    return d


def atomic_write_json(path: Path, doc: dict) -> None:
    """temp file in the same directory + rename, so a reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _git_path(basename: str, root: Path | None = None) -> Path | None:
    root = root or project_root()
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--git-path", basename],
                             capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    p = Path(out.stdout.strip())
    return p if p.is_absolute() else (root / p)


def command_digest(cmd: str) -> str:
    return hashlib.sha256((cmd or "").encode("utf-8")).hexdigest()
