#!/usr/bin/env python3
"""foundry-paper-guard — PreToolUse (Edit|Write|MultiEdit|NotebookEdit).

Paper is written only when the ticket asks for it. A write under a PAPER path — specs/, intake/,
.foundry/, status-reports/, charters/, docs/, or an acceptance-contract.yaml / release.yaml /
state.yaml anywhere — is refused unless the active ticket (`.claude/foundry-ticket.json`, see
scripts/foundry-ticket.py) lists that path or a parent directory under `paper_allowed`.

Measured reason this exists: over 60 days on two adopter workspaces 76% of all agent file edits
went to paper and the paper:code line ratio ran 1.5:1 to 8:1. The guard makes paper deliberate,
not impossible: `foundry-ticket.py allow <path>` lifts it for one path when the operator asks.

Everything else — code, infra, tests, CLAUDE.md, config — is admitted without a ticket. Writes
outside the project root (e.g. ~/.claude memory) are admitted; so is a symlink that resolves
outside it. Malformed payloads are admitted (a visibility control, not a security floor). The
first path component is compared case-insensitively on case-insensitive filesystems (darwin).
A refusal is exit 2 with the reason on stderr (what the agent sees) and as JSON on stdout.
"""
import json
import os
import sys
from pathlib import Path

PAPER_DIRS = ("specs", "intake", ".foundry", "status-reports", "charters", "docs")
PAPER_FILES = ("acceptance-contract.yaml", "release.yaml", "state.yaml")
CASEFOLD = sys.platform == "darwin" or os.name == "nt"


def _root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and Path(env).is_dir():
        return Path(env).resolve()
    return Path.cwd().resolve()


def _key(s: str) -> str:
    return s.casefold() if CASEFOLD else s


def _is_paper(rel: Path) -> bool:
    parts = rel.parts
    if not parts:
        return False
    if _key(parts[0]) in {_key(d) for d in PAPER_DIRS}:
        return True
    return _key(rel.name) in {_key(f) for f in PAPER_FILES}


def _allowed(rel: Path, allowed: list) -> bool:
    rel_s = _key(rel.as_posix())
    for a in allowed:
        a = _key(str(a).strip().strip("/").lstrip("./"))
        if not a:
            continue
        if rel_s == a or rel_s.startswith(a + "/"):
            return True
    return False


def _refuse(reason: str) -> int:
    msg = "foundry-paper-guard: " + reason
    sys.stderr.write(msg + "\n")
    sys.stdout.write(json.dumps({"decision": "block", "reason": msg}))
    return 2


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    tool = payload.get("tool_name", "")
    if tool not in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return 0
    ti = payload.get("tool_input") or {}
    target = ti.get("file_path") or ti.get("notebook_path") or ""
    if not target:
        return 0
    root = _root()
    try:
        abs_t = Path(target).expanduser()
        if not abs_t.is_absolute():
            abs_t = root / abs_t
        # resolve the PARENT (the file may not exist yet) so symlinked dirs land where they point
        resolved = abs_t.parent.resolve() / abs_t.name
        rel = resolved.relative_to(root)
    except Exception:
        return 0  # outside the project → not our business
    if not _is_paper(rel):
        return 0

    ticket_file = root / ".claude" / "foundry-ticket.json"
    doc = None
    if ticket_file.is_file():
        try:
            doc = json.loads(ticket_file.read_text(encoding="utf-8"))
        except Exception:
            doc = None
    if doc and _allowed(rel, doc.get("paper_allowed") or []):
        return 0

    p = rel.as_posix()
    if doc:
        return _refuse("ticket #%s does not allow writing %s. If the operator asked for this document, run "
                       "`python3 \"$CLAUDE_PLUGIN_ROOT/scripts/foundry-ticket.py\" allow %s` and retry. "
                       "Otherwise write code, not paper." % (doc.get("issue"), p, p))
    return _refuse("no active ticket, and %s is a paper path (specs/, intake/, .foundry/, docs/, "
                   "status-reports/, charters/, contracts, manifests). Start the ticket you are working "
                   "(`python3 \"$CLAUDE_PLUGIN_ROOT/scripts/foundry-ticket.py\" start <issue>`) and list the "
                   "paper it needs under `## Paper allowed`; a ticket's Done means is a command, not a "
                   "document." % p)


if __name__ == "__main__":
    sys.exit(main())
