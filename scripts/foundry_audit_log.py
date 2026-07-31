"""foundry_audit_log — the §22.5a fail-closed security-audit trail.

Append-only JSONL at .foundry/security-audit.jsonl with the record-BEFORE-action
discipline: flock(LOCK_EX) → append one line → flush+fsync → read-back-verify the
line landed (by a per-record nonce) → only then release the lock and let the caller
act. A failed append/verify RAISES (fail-closed) — the caller must NOT proceed with
the gated action. This is the independent, append-ordered position an agent cannot
backdate (§22.6c).

Distinct from the gitignored .claude/dispatch-log.jsonl (the dispatcher/noninteractive
audit trail): this trail is the authorization + merge-gate non-repudiation record.
"""
from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone


def audit_log_path(repo_root: str | None = None) -> str:
    root = repo_root or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return os.path.join(root, ".foundry", "security-audit.jsonl")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AuditLogError(Exception):
    pass


def append_record(record: dict, repo_root: str | None = None) -> str:
    """Append one record fail-closed. Returns the assigned record_id. Raises
    AuditLogError if the write cannot be confirmed (caller must abort the action)."""
    path = audit_log_path(repo_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    rec = dict(record)
    rec.setdefault("ts", now_iso())
    record_id = os.urandom(12).hex()
    rec["record_id"] = record_id
    line = json.dumps(rec, sort_keys=True, separators=(",", ":"))

    # Open for append; acquire exclusive lock across write + fsync + read-back.
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, (line + "\n").encode("utf-8"))
        os.fsync(fd)
        # Read-back-verify: the last non-empty line must be ours.
        with open(path, "rb") as rb:
            tail = rb.read().splitlines()
        if not tail or tail[-1].decode("utf-8") != line:
            raise AuditLogError("record-before-action read-back mismatch (write not confirmed)")
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    return record_id
