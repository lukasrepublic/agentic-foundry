#!/usr/bin/env python3
"""foundry_floor_hooks — shared helpers for the two native `TaskCreated`/`TaskCompleted` hooks
that move front-authorization and the `done_when` evidence gate INSIDE a team session's task loop
(feat-foundry-authorization-floor-hooks, AC-FLH-1/-2/-3/-11).

PURE library: no argparse, no `main()`, no `__main__` — never in command position (the
corresponding `not_invoked` row this atom adds to `docs/permission-floor.json` /
`cli/permission-floor.json`). `hooks/foundry-task-created.py` and `hooks/foundry-task-completed.py`
are the only callers.

Real task JSON shape (inspected READ-ONLY on this machine, one team directory under
`~/.claude/tasks/<team>/<task-id>.json`, re-read 2026-09-19): every sampled file is exactly
`{"id", "subject", "description", "activeForm", "status", "blocks", "blockedBy"}` — there is NO
`createdAt` key anywhere in the corpus. A whole-corpus grep for "created"/"createdAt" turned up
only the plain-English substring "created" inside a few `description` prose fields, never a JSON
key. So AC-FLH-2's "the task's creation time read from the tasks dir" is implemented here as the
task FILE's own filesystem creation time (`os.stat(...).st_birthtime`, falling back to `st_mtime`
on a platform that does not report a birth time) rather than a JSON field — the spec names "read
from the tasks dir", not a specific field, so this is a within-spec implementation choice, not a
spec change. Separately, the observed team directory name on this machine is a full session UUID
(`~/.claude/tasks/<uuid>/`), not the truncated `session-<8>` form the lane README's PRIMARY-DOC
FACTS quote for the sibling `~/.claude/teams/` path — `default_tasks_dir` below therefore keys off
the payload's own `session_id` field verbatim, never truncated.

Reuses (never re-implements): `foundry_release.load_release` (release/atom resolution — the same
primitive `derive_closure`/the merge gate use), `foundry_authz.is_authorized` (the factory-lane
recompute-match), and `foundry_command_deck_watch.done_when_escalate_when` (the contract-or-charter
`done_when` reader the tick prompt already renders from). The charter-committed check is this
module's own code (AC-FLH-3's ONE allowed subprocess, `git -C <project> log -1 --format=%H --
<charter_ref>`) rather than a call into `foundry_release`'s private, fail-SAFE
`_charter_authorized` — this module's contract is fail-CLOSED (a git failure must raise, not
silently return False), which the private helper's fail-safe posture does not give it.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import foundry_authz as fa  # noqa: E402
import foundry_command_deck_watch as cdw  # noqa: E402
import foundry_release as fr  # noqa: E402


class FloorHookError(Exception):
    """A resolvable, nameable refusal reason — every raise site names the gap in its message.
    Caught by both hook entry points' outer `except Exception` alongside any OTHER exception
    (`foundry_release.ReleaseError`, a JSON error, a subprocess failure, …) — the hooks are
    fail-closed on any exception, not only this one (AC-FLH-1/AC-FLH-2)."""


# --------------------------------------------------------------------------------------------- #
# task_subject parsing (AC-FLH-11)
# --------------------------------------------------------------------------------------------- #

# Recognizes the SHAPE "atom:<anything>/<anything>" — split at the LAST '/' (greedy first group)
# so a release-id portion carrying an extra '/' or '..' (a path-traversal attempt) is still
# recognized as an ATTEMPTED atom reference rather than silently falling through as "any other
# subject" (AC-FLH-1's fail-closed "non-slug id" case; AC-FLH-3's "refused as non-slug"). Slug
# validation of the two captured parts is deliberately a SEPARATE step (`is_slug` / `resolve_atom`
# below) — a subject that merely LOOKS like an atom reference must be refused, never ignored.
_ATOM_SUBJECT_SHAPE_RE = re.compile(r"^atom:(.+)/(.+)$")
_SLUG_RE = re.compile(r"^[a-z0-9-]+$")


def parse_atom_subject(subject) -> "tuple[str, str] | None":
    """Exact-form slug-SHAPE parsing (AC-FLH-11). Returns `(release_id, atom_id)` — the raw
    captured strings, NOT yet slug-validated — whenever `subject` has the `atom:<X>/<Y>` SHAPE.
    Returns `None` for any subject that does not even carry that shape (no `atom:` prefix, or no
    `/` at all) — "any other subject", exiting 0 untouched (AC-FLH-1/AC-FLH-4). `None` is also
    returned for a non-string subject."""
    if not isinstance(subject, str):
        return None
    m = _ATOM_SUBJECT_SHAPE_RE.match(subject)
    if not m:
        return None
    return m.group(1), m.group(2)


def is_slug(value) -> bool:
    return isinstance(value, str) and bool(_SLUG_RE.match(value))


# --------------------------------------------------------------------------------------------- #
# atom resolution + authorization (AC-FLH-1)
# --------------------------------------------------------------------------------------------- #


def resolve_atom(release_id: str, atom_id: str, project_dir: str):
    """Resolve one atom, fail-closed. Raises `FloorHookError` for a non-slug id (before ever
    touching disk); propagates `foundry_release.ReleaseError` (an unresolvable release, a
    malformed manifest) and any other exception unchanged — the caller's `except Exception` is
    what makes ALL of these fail-closed (AC-FLH-1), not a narrowed catch here."""
    if not is_slug(release_id) or not is_slug(atom_id):
        raise FloorHookError(
            f"task_subject names a non-slug release/atom id ({release_id!r}/{atom_id!r})"
        )
    release = fr.load_release(release_id, project_dir=project_dir)
    atom = release.by_id.get(atom_id)
    if atom is None:
        raise FloorHookError(f"atom {atom_id!r} not found in release {release_id!r}")
    return atom


def _charter_committed(charter_ref: str, project_dir: str) -> bool:
    """AC-FLH-3's ONE allowed subprocess: `git -C <project> log -1 --format=%H -- <charter_ref>`.
    Fail-CLOSED (raises `FloorHookError`) on a git/subprocess failure — never fail-safe-to-False,
    unlike `foundry_release._charter_authorized`'s own fail-safe posture, which this hook's
    fail-closed contract cannot reuse as-is (see module docstring)."""
    path = os.path.join(project_dir, charter_ref)
    if not os.path.isfile(path):
        return False
    try:
        result = subprocess.run(
            ["git", "-C", project_dir, "log", "-1", "--format=%H", "--", charter_ref],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise FloorHookError(f"git log for charter_ref {charter_ref!r} failed to run: {e}") from e
    if result.returncode != 0:
        raise FloorHookError(
            f"git log for charter_ref {charter_ref!r} exited {result.returncode}: "
            f"{result.stderr.strip()}"
        )
    return bool(result.stdout.strip())


def is_atom_authorized(atom, project_dir: str) -> bool:
    """AC-FLH-1: a factory atom whose contract re-derives AUTHORIZED (`foundry_authz.is_authorized`
    — never re-implemented) or a charter atom whose charter exists and is committed
    (`_charter_committed` above, the module's one allowed subprocess). Raises `FloorHookError` for
    an atom carrying neither shape (a malformed manifest `foundry_release` itself would normally
    refuse to load — defensive only)."""
    charter_ref = getattr(atom, "charter_ref", None)
    if charter_ref:
        return _charter_committed(charter_ref, project_dir)
    spec_ref = getattr(atom, "spec_ref", None)
    contract_ref = getattr(atom, "contract_ref", None)
    if not spec_ref or not contract_ref:
        raise FloorHookError(
            f"atom {getattr(atom, 'id', '?')!r} carries neither charter_ref nor spec_ref+contract_ref"
        )
    spec_path = os.path.join(project_dir, spec_ref)
    contract_path = os.path.join(project_dir, contract_ref)
    return bool(fa.is_authorized(spec_path, contract_path))


# --------------------------------------------------------------------------------------------- #
# done_when (AC-FLH-2) — reused, never re-implemented
# --------------------------------------------------------------------------------------------- #


def declared_done_when(atom, project_dir: str) -> list:
    """The atom's declared `done_when` locator strings — contract or charter, whichever the atom
    carries — via `foundry_command_deck_watch.done_when_escalate_when` (never re-implemented
    here). `[]` when nothing is declared (the caller treats that as its own refusal, AC-FLH-2)."""
    done, _escalate = cdw.done_when_escalate_when(atom, project_dir=project_dir)
    return list(done)


# --------------------------------------------------------------------------------------------- #
# the tasks dir + the task's creation time (AC-FLH-2)
# --------------------------------------------------------------------------------------------- #


def default_tasks_dir(payload: dict) -> "str | None":
    """Default `--tasks-dir`, derived from the payload's own `session_id` (see module docstring:
    the observed team directory on this machine is a full session UUID, never `session-<8>`).
    Returns `None` when the payload carries no usable `session_id` — the caller then refuses
    (fail-closed), it never guesses a path."""
    session_id = payload.get("session_id") if isinstance(payload, dict) else None
    if isinstance(session_id, str) and session_id.strip():
        return os.path.expanduser(os.path.join("~", ".claude", "tasks", session_id.strip()))
    return None


def task_created_at(task_id, tasks_dir) -> datetime:
    """The task file's own filesystem creation time (see module docstring for why there is no
    JSON field to read instead). Raises `FloorHookError` — naming the gap — for a missing/absent
    `tasks_dir`, a non-slug-ish `task_id` (defensive: never used to escape `tasks_dir`), or a task
    id absent from it (AC-FLH-2's "task id not found in the tasks dir")."""
    if not tasks_dir:
        raise FloorHookError(
            "no tasks dir resolved (pass --tasks-dir, or ensure the payload carries a session_id)"
        )
    if not isinstance(task_id, str) or not task_id.strip() or "/" in task_id or ".." in task_id:
        raise FloorHookError(f"task_id {task_id!r} is not a usable id")
    path = os.path.join(tasks_dir, f"{task_id}.json")
    try:
        st = os.stat(path)
    except OSError as e:
        raise FloorHookError(f"task {task_id!r} not found in tasks dir {tasks_dir!r}: {e}") from e
    ts = getattr(st, "st_birthtime", None)
    if ts is None:
        ts = st.st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc)


# --------------------------------------------------------------------------------------------- #
# the evidence record (AC-FLH-2) — read-only, written by the builder (AC-FLH-5)
# --------------------------------------------------------------------------------------------- #


def evidence_path(atom_id: str, project_dir: str) -> str:
    return os.path.join(project_dir, ".foundry", "evidence", f"{atom_id}.json")


def read_evidence_record(atom_id: str, project_dir: str) -> dict:
    """Read + structurally validate `.foundry/evidence/<atom-id>.json`
    (`{"atom", "done_when": [{"locator","status","evidence","at"}], "recorded_by", "at"}`).
    Raises `FloorHookError` naming the gap on a missing file, unparseable JSON, or any missing/
    malformed required field — this hook only READS the record; it never writes one (AC-FLH-3)."""
    path = evidence_path(atom_id, project_dir)
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as e:
        raise FloorHookError(f"no evidence record at {path}: {e}") from e
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as e:
        raise FloorHookError(f"evidence record {path} is not valid JSON: {e}") from e
    if not isinstance(doc, dict):
        raise FloorHookError(f"evidence record {path} is not a JSON object")
    for key in ("atom", "done_when", "recorded_by", "at"):
        if key not in doc:
            raise FloorHookError(f"evidence record {path} missing required field {key!r}")
    if not isinstance(doc["done_when"], list):
        raise FloorHookError(f"evidence record {path}: done_when must be a list")
    for i, row in enumerate(doc["done_when"]):
        if not isinstance(row, dict):
            raise FloorHookError(f"evidence record {path}: done_when[{i}] is not an object")
        for key in ("locator", "status", "evidence", "at"):
            if key not in row:
                raise FloorHookError(f"evidence record {path}: done_when[{i}] missing field {key!r}")
        if row["status"] not in ("met", "unmet"):
            raise FloorHookError(
                f"evidence record {path}: done_when[{i}].status {row['status']!r} not in met|unmet"
            )
    return doc


def parse_utc(value) -> datetime:
    """Parse an `"at"` timestamp (contract/evidence convention: an ISO-8601 UTC string, `Z` or
    `+00:00`). Raises `FloorHookError` naming the bad value on anything else."""
    if not isinstance(value, str) or not value.strip():
        raise FloorHookError(f"timestamp {value!r} is not a non-empty string")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as e:
        raise FloorHookError(f"timestamp {value!r} is not ISO-8601: {e}") from e
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
