#!/usr/bin/env python3
"""foundry_floor_hooks — shared helpers for the two native `TaskCreated`/`TaskCompleted` hooks
that move front-authorization and the `done_when` evidence gate INSIDE a team session's task loop
(feat-foundry-authorization-floor-hooks, AC-FLH-1/-2/-3/-11).

PURE library: no argparse, no `main()`, no `__main__` — never in command position (the
corresponding `not_invoked` row this atom adds to `docs/permission-floor.json` /
`cli/permission-floor.json`). `hooks/foundry-task-created.py` and `hooks/foundry-task-completed.py`
are the only callers, and both import this module LAZILY (inside their own guarded call, never at
module load time) so a broken import becomes exit 2 through their own fail-closed wrapper rather
than an uncaught traceback (review round 1, item 2).

Real task JSON shape (inspected READ-ONLY on this machine, one team directory under
`~/.claude/tasks/<team>/<task-id>.json`, re-read 2026-09-19): every sampled file is exactly
`{"id", "subject", "description", "activeForm", "status", "blocks", "blockedBy"}` — there is NO
`createdAt` key anywhere in the corpus. A whole-corpus grep for "created"/"createdAt" turned up
only the plain-English substring "created" inside a few `description` prose fields, never a JSON
key. So AC-FLH-2's "the task's creation time read from the tasks dir" is implemented here as the
task FILE's own filesystem creation time rather than a JSON field — the spec names "read from the
tasks dir", not a specific field, so this is a within-spec implementation choice, not a spec
change.

RESIDUAL — platform + rewrite caveats on the task-creation-time signal (review round 1, item 5;
tracked as R4 backfill material, not fixed here):
  * `st_birthtime` (the file's true creation time) is macOS/BSD-only. On Linux `stat(2)` reports
    no birth time at all — `hasattr(st, "st_birthtime")` is `False` there — so `task_created_at`
    falls back to `st_mtime`. On Linux that means (a) the harness rewriting a task file on ANY
    status change (not only creation) resets what this function treats as "creation time", which
    could wrongly REFUSE a legitimate completion whose evidence predates a later, unrelated
    status-only rewrite, and (b) a builder can `os.utime` the file backwards to defeat the
    staleness check outright.
  * ATOMIC-REWRITE CAVEAT (both platforms): a write-tmp-then-`rename` durable-write idiom (the
    SAME one this codebase's own `foundry_release.save_release` uses) resets `st_birthtime` to the
    rename's own time, not the file's original creation moment, if the harness's own task writer
    ever uses that idiom for an in-place update. This function cannot distinguish "genuinely just
    created" from "just rewritten in place."
  Neither caveat is closed here. A durable, explicit creation-time record living IN the tasks dir
  (rather than inferred from filesystem metadata) would close both, and is R4 material.

Separately: the observed team directory name on this machine is a full session UUID
(`~/.claude/tasks/<uuid>/`), not the truncated `session-<8>` form the lane README's PRIMARY-DOC
FACTS quote for the sibling `~/.claude/teams/` path. `candidate_tasks_dirs` therefore tries BOTH
shapes, in order (review round 1, item 9) — the full `session_id` verbatim first (what this
machine's own team directory actually looks like), then the documented `session-<8>` truncated
form — rather than betting on one.

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
from datetime import datetime, timedelta, timezone

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


class MalformedAtomSubjectError(FloorHookError):
    """Raised by `parse_atom_subject` for a subject that STARTS WITH `atom:` (case-insensitive,
    after stripping leading/trailing whitespace) but is not the exact form `atom:<slug>/<slug>`
    (a lowercase `atom:` literal, both parts `[a-z0-9-]+`, nothing else — no extra whitespace, no
    embedded newline, no case variation, no extra `/`, no `..`). Review round 1, item 7: a subject
    that merely LOOKS like an atom reference must be refused (exit 2, naming it), never silently
    treated as "any other subject" (which would exit 0 untouched)."""


# --------------------------------------------------------------------------------------------- #
# task_subject parsing (AC-FLH-11; review round 1 item 7 — the ONE rule)
# --------------------------------------------------------------------------------------------- #

# Detection is DELIBERATELY lenient (case-insensitive, tolerant of surrounding whitespace) so no
# attempted atom reference is ever silently mis-filed as "any other subject, exit 0 untouched".
_ATOM_PREFIX_RE = re.compile(r"^atom:", re.IGNORECASE)
# Validation is DELIBERATELY strict against the RAW (unstripped) subject: a lowercase `atom:`
# literal, both parts `[a-z0-9-]+`, and `\Z` (not `$`, which in Python also matches just before a
# single trailing newline) so an embedded/trailing newline never slips through.
_EXACT_ATOM_SUBJECT_RE = re.compile(r"^atom:([a-z0-9-]+)/([a-z0-9-]+)\Z")
_SLUG_RE = re.compile(r"^[a-z0-9-]+$")


def parse_atom_subject(subject) -> "tuple[str, str] | None":
    """Exact-form parsing (AC-FLH-11). Returns `(release_id, atom_id)` for the exact form
    `atom:<slug>/<slug>`. Returns `None` when `subject` does not even START WITH `atom:` (case-
    insensitive, after `.strip()`) — "any other subject", exiting 0 untouched (AC-FLH-1/AC-FLH-4).
    Raises `MalformedAtomSubjectError` for everything in between — a subject that starts with
    `atom:` (any case) but is not the exact form (uppercase, a stray leading/trailing space, an
    embedded newline, extra `/`, `..`, …): refused (exit 2, naming it) rather than silently
    ignored. `None` is also returned for a non-string subject."""
    if not isinstance(subject, str):
        return None
    stripped = subject.strip()
    if not _ATOM_PREFIX_RE.match(stripped):
        return None  # does not even start with atom: (case-insensitive) — any other subject
    m = _EXACT_ATOM_SUBJECT_RE.match(subject)
    if not m:
        raise MalformedAtomSubjectError(
            f"task_subject {subject!r} starts with 'atom:' but is not the exact form "
            f"atom:<slug>/<slug>"
        )
    return m.group(1), m.group(2)


def is_slug(value) -> bool:
    return isinstance(value, str) and bool(_SLUG_RE.match(value))


# --------------------------------------------------------------------------------------------- #
# the project dir (review round 1 item 6)
# --------------------------------------------------------------------------------------------- #


def resolve_project_dir(payload) -> str:
    """The project dir a hook resolves atoms/evidence against. Payload `cwd` first — realpath'd,
    and MUST already resolve to an existing directory (review round 1 item 6: an attacker-
    controlled or simply stale `cwd` is refused rather than silently followed) — then
    `CLAUDE_PROJECT_DIR` (same validation), then `os.getcwd()` (whatever that raises — e.g. a
    deleted cwd — is left to the caller's own fail-closed `except Exception`, review item 2)."""
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    if isinstance(cwd, str) and cwd.strip():
        real = os.path.realpath(cwd)
        if not os.path.isdir(real):
            raise FloorHookError(f"payload cwd {cwd!r} does not resolve to an existing directory")
        return real
    env_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_dir:
        real = os.path.realpath(env_dir)
        if not os.path.isdir(real):
            raise FloorHookError(
                f"CLAUDE_PROJECT_DIR {env_dir!r} does not resolve to an existing directory"
            )
        return real
    return os.getcwd()


# --------------------------------------------------------------------------------------------- #
# atom resolution + authorization (AC-FLH-1)
# --------------------------------------------------------------------------------------------- #


def resolve_atom(release_id: str, atom_id: str, project_dir: str):
    """Resolve one atom, fail-closed. Raises `FloorHookError` for a non-slug id (defensive — by
    construction `parse_atom_subject` never hands this a non-slug part, but a direct caller
    might); propagates `foundry_release.ReleaseError` (an unresolvable release, a malformed
    manifest) and any other exception unchanged — the caller's `except Exception` is what makes
    ALL of these fail-closed (AC-FLH-1), not a narrowed catch here."""
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
# the tasks dir + the task's creation time (AC-FLH-2; review round 1 items 5/6/9)
# --------------------------------------------------------------------------------------------- #

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def candidate_tasks_dirs(session_id) -> list:
    """The tasks-dir shapes worth trying for a given `session_id`, in order (review round 1 item
    9): the full `session_id` verbatim (what this machine's own team directory actually looks
    like — see module docstring), then the lane README's documented `session-<8>` truncated form.
    `[]` when `session_id` is absent/blank — not itself a refusal, the caller decides what an
    empty candidate list means. Raises `FloorHookError` for a `session_id` that is present but
    unsafe: outside `[A-Za-z0-9._-]+`, or containing `..` (review round 1 item 6 — `/` is already
    excluded by the charset, which also excludes an absolute path)."""
    if session_id is None:
        return []
    if not isinstance(session_id, str) or not session_id.strip():
        return []
    sid = session_id.strip()
    if not _SESSION_ID_RE.match(sid) or ".." in sid:
        raise FloorHookError(f"session_id {session_id!r} is not a safe identifier")
    candidates = [os.path.expanduser(os.path.join("~", ".claude", "tasks", sid))]
    truncated = os.path.expanduser(os.path.join("~", ".claude", "tasks", f"session-{sid[:8]}"))
    if truncated not in candidates:
        candidates.append(truncated)
    return candidates


def resolve_task_file(task_id, *, tasks_dir=None, session_id=None) -> str:
    """Resolve `<tasks-dir>/<task_id>.json`, trying `tasks_dir` (an explicit `--tasks-dir`
    override) first, then each of `candidate_tasks_dirs(session_id)` in order — the first
    candidate whose file actually EXISTS wins (review round 1 item 9). Raises `FloorHookError`
    naming EVERY path tried when none contains the file, or when there was nothing to try at
    all."""
    if not isinstance(task_id, str) or not task_id.strip() or "/" in task_id or ".." in task_id:
        raise FloorHookError(f"task_id {task_id!r} is not a usable id")
    dirs = []
    if tasks_dir:
        dirs.append(tasks_dir)
    dirs.extend(candidate_tasks_dirs(session_id))
    if not dirs:
        raise FloorHookError(
            "no tasks dir resolved (pass --tasks-dir, or ensure the payload carries a session_id)"
        )
    tried = []
    for d in dirs:
        path = os.path.join(d, f"{task_id}.json")
        tried.append(path)
        if os.path.isfile(path):
            return path
    raise FloorHookError(f"task {task_id!r} not found in any tasks dir tried: {tried}")


def task_created_at(task_id, *, tasks_dir=None, session_id=None) -> datetime:
    """The task file's own filesystem creation time (see module docstring's RESIDUAL section for
    the platform + atomic-rewrite caveats — review round 1 item 5). Raises `FloorHookError` —
    naming the gap — via `resolve_task_file` for an unresolvable tasks dir or a task id absent
    from every candidate tried (AC-FLH-2's "task id not found in the tasks dir")."""
    path = resolve_task_file(task_id, tasks_dir=tasks_dir, session_id=session_id)
    st = os.stat(path)
    # Gate explicitly on `hasattr` (review round 1 item 5), not a `getattr(..., None) is None`
    # check — the two are equivalent in practice (no platform reports `st_birthtime == None`),
    # but `hasattr` says directly what is being tested: does THIS platform's stat_result carry a
    # birth time at all.
    ts = st.st_birthtime if hasattr(st, "st_birthtime") else st.st_mtime
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
    if doc["atom"] != atom_id:
        raise FloorHookError(f"evidence record {path} names atom {doc['atom']!r}, not {atom_id!r}")
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


def check_not_future(value: datetime, *, max_skew_seconds: int = 300, label: str = "timestamp") -> None:
    """Refuse a timestamp more than `max_skew_seconds` (default 300s) past the REAL current time
    (review round 1 item 4) — an evidence record naming a date far enough in the future to satisfy
    "newer than the task" forever (e.g. `2099-01-01`) is refused, named, rather than trusted."""
    now = datetime.now(timezone.utc)
    if value > now + timedelta(seconds=max_skew_seconds):
        raise FloorHookError(
            f"{label} {value.isoformat()} is more than {max_skew_seconds}s in the future "
            f"(now {now.isoformat()})"
        )
