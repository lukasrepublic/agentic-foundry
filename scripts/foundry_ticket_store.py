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
import os
import re
import subprocess
from pathlib import Path

TICKET_BASENAME = "foundry-ticket.json"

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
    root = root or project_root()
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--git-path", TICKET_BASENAME],
                             capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    p = Path(out.stdout.strip())
    return p if p.is_absolute() else (root / p)


def command_digest(cmd: str) -> str:
    return hashlib.sha256((cmd or "").encode("utf-8")).hexdigest()
