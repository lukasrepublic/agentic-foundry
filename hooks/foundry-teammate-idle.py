#!/usr/bin/env python3
"""foundry-teammate-idle — the `TeammateIdle` hook: an observer, never a gate
(autonomy-continuation R3, `teammate-idle-continue`, AC-TIC-1, AC-TIC-2).

Why this hook is shaped the way it is. The hooks reference (re-read 2026-09-19) says
`TeammateIdle` exit two is NOT honored — an idle teammate stays idle regardless of this
hook's exit code. So this hook never blocks anything; it only OBSERVES an idle teammate's
claimed task, compares its declared `done_when` locators against the evidence record
(`.foundry/evidence/<atom-id>.json`, the same shape the shared floor-hooks helper module
already reads for the `TaskCreated`/`TaskCompleted` floor hooks), and — the one lever that
DOES exist natively — posts one message into its OWN session's inbox socket
(`$CLAUDE_CODE_MESSAGING_SOCKET`) naming what is still unmet. A message from the lead wakes
an idle in-process teammate; the hook cannot wake anyone itself. This module ALWAYS exits
zero: every failure mode (an absent socket, an unresolvable task, a malformed payload, a
nudge cap already reached, any internal error) is recorded — with a named reason — to
`.foundry/idle-nudges.jsonl` and never surfaced as a non-zero exit.

Payload fields (hooks reference, re-read 2026-09-19): `session_id, hook_event_name, cwd,
task_id, task_subject, task_description, agent_id, agent_type` plus `teammate_name` (the
deprecated `team_name` is accepted as a fallback). `task_subject`/`task_id` are usually
already the teammate's claimed task, straight from the payload — no tasks-dir lookup
needed. When the payload carries no `task_subject` at all (a leaner event shape, or a
future platform change), this hook falls back to `~/.claude/tasks/<session-dir>/*.json`
(via `foundry_floor_hooks.candidate_tasks_dirs`, never re-implemented): the real task JSON
shape inspected read-only on this machine (see that shared helper module's own docstring)
is exactly `{"id", "subject", "description", "activeForm", "status", "blocks", "blockedBy"}`
— there is NO `owner`/`assignee` field anywhere in that corpus. So the fallback is: the
SINGLE `in_progress` task whose `description` names the teammate (a case-insensitive
substring match); zero or more-than-one match is "unresolved" (recorded, no message —
AC-TIC-2), never guessed at.

WIRE-FORMAT ASSUMPTION (documented per this atom's build brief, since the exact inbox wire
format is not specified in the lane README's primary-doc facts beyond "a hook or Bash child
can post a message into its own session's inbox socket... optional auth line
`{"type":"auth","token":"<CLAUDE_CODE_MESSAGING_TOKEN>"}`"): this module writes newline-
delimited JSON — the auth line first (only when `$CLAUDE_CODE_MESSAGING_TOKEN` is set), then
one `{"type":"message","to":"lead","text":"<the IDLE-UNMET line>"}` line — over the Unix
socket named by `$CLAUDE_CODE_MESSAGING_SOCKET`. The entire transport lives in ONE function,
`post_idle_message`, so a corrected wire format is a one-function fix, not a scattered one.

Read-only against the corpus except the ONE write this hook makes: an append to
`.foundry/idle-nudges.jsonl` (never a locator execution, never any other file write, no
subprocess at all — mirrors feat-foundry-authorization-floor-hooks' security posture,
adapted for a hook that is a notifier rather than a gate). AC-RES-2: that ledger rotates at
`ROTATION_MAX_LINES` (2000) lines — the live file is renamed to `.foundry/idle-nudges.1.jsonl`
(replacing an older rotation, `os.replace`) and a fresh file starts; the nudge cap counts rows
across BOTH files, so a rotation never resets a teammate's cap. Rotation is the hook's only
OTHER write besides the append itself.

    echo '{"teammate_name": "worker-a", "task_subject": "atom:<release>/<atom>", \
"cwd": "/path"}' | foundry-teammate-idle.py
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
from datetime import datetime, timezone

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.join(os.path.dirname(_HOOKS_DIR), "scripts")

# Same charset as foundry_floor_hooks.py's own session_id safe-identifier check (review
# precedent): no `/`, no whitespace, no shell/JSON metacharacters — teammate_name is never
# used to build a path here, but it IS interpolated into the message text and the nudge
# ledger, so it is validated before either (build brief: "Validate session_id/teammate_
# name/atom as slugs before any path use").
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

NUDGE_CAP = 3
_MESSAGE_TO = "lead"  # see the WIRE-FORMAT ASSUMPTION above — a placeholder recipient tag.

# AC-RES-2: the ledger rotates at this many lines rather than growing unbounded for the lifetime
# of a long-running workspace. `_rotated_path` names the one-generation-deep backup.
ROTATION_MAX_LINES = 2000


def _import_ffh():
    """Deferred, guarded import — see `hooks/foundry-task-created.py`'s own docstring for
    why this is never a module-level import: a broken sibling import must become a
    recorded, exit-0 no-op through this hook's own wrapper, never an uncaught traceback."""
    if _SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _SCRIPTS_DIR)
    import foundry_floor_hooks as ffh
    return ffh


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_nudges_path(project_dir: str) -> str:
    return os.path.join(project_dir, ".foundry", "idle-nudges.jsonl")


def _rotated_path(path: str) -> str:
    """The one-generation-deep rotation sibling: `.foundry/idle-nudges.jsonl` ->
    `.foundry/idle-nudges.1.jsonl` (AC-RES-2)."""
    base, ext = os.path.splitext(path)
    return f"{base}.1{ext}"


def _line_count(path: str) -> int:
    """Tolerant of a missing/unreadable file — a soft count, never a gate."""
    if not os.path.isfile(path):
        return 0
    try:
        with open(path, encoding="utf-8") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def _rotate_if_needed(path: str, max_lines: int = ROTATION_MAX_LINES) -> None:
    """Accepted race (PR #204 review): two idle events that both observe the live file at the cap can
    both `os.replace` it — the second rotation clobbers the first's `.1.jsonl` and a few rotated rows
    are lost. The ledger is advisory nudge bookkeeping, never a gate, and every write here is best
    effort; a lock is not worth its failure modes in a hook that must always exit 0. AC-RES-2: once `path` has reached `max_lines`, rename it to `_rotated_path(path)`
    (`os.replace` — atomic, and REPLACES an existing older rotation rather than erroring on
    one) so the next append starts a fresh file. This is the hook's only OTHER write besides
    the append itself. Best-effort like `_append_record`: any `OSError` here is swallowed,
    never surfaced (the hook's contract is ALWAYS exit 0)."""
    if _line_count(path) < max_lines:
        return
    try:
        os.replace(path, _rotated_path(path))
    except OSError:
        pass


def _append_record(path: str, record: dict) -> None:
    """Best-effort append of one JSON line, rotating first when the ledger has reached its cap
    (AC-RES-2). Swallows any `OSError` (an unwritable ledger directory, a full disk, …) — this
    hook's contract is ALWAYS exit 0, and a failed RECORD of a failure must never itself become
    a second, louder failure."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _rotate_if_needed(path)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass


def _count_prior_nudges(path: str, release_id: str, atom_id: str) -> int:
    """How many `idle-unmet` nudges this atom has already had, counted across BOTH the live
    ledger and its most recent rotation (`_rotated_path`, AC-RES-2 — a rotation must not reset
    the cap), tolerant of a missing file and of any malformed line in either (skip, never raise
    — this is a soft read for a cap check, not a gate)."""
    count = 0
    for p in (_rotated_path(path), path):
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if (
                        isinstance(row, dict)
                        and row.get("type") == "idle-unmet"
                        and row.get("release_id") == release_id
                        and row.get("atom_id") == atom_id
                    ):
                        count += 1
        except OSError:
            continue
    return count


def post_idle_message(text: str, *, socket_path=None, token=None, timeout: float = 2.0):
    """The one transport function (see the module docstring's WIRE-FORMAT ASSUMPTION).
    Returns `(sent: bool, reason: "str | None")` — never raises; every failure mode (no
    socket configured, connect/send failure) is reported back as `(False, reason)` rather
    than propagated, since the caller's contract is ALWAYS exit 0."""
    sock_path = socket_path if socket_path is not None else os.environ.get(
        "CLAUDE_CODE_MESSAGING_SOCKET"
    )
    if not sock_path:
        return False, "CLAUDE_CODE_MESSAGING_SOCKET is not set"
    tok = token if token is not None else os.environ.get("CLAUDE_CODE_MESSAGING_TOKEN")
    sock = None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)  # inside the try: never raises (review)
        sock.settimeout(timeout)
        sock.connect(sock_path)
        if tok:
            auth_line = json.dumps({"type": "auth", "token": tok}) + "\n"
            sock.sendall(auth_line.encode("utf-8"))
        message_line = json.dumps({"type": "message", "to": _MESSAGE_TO, "text": text}) + "\n"
        sock.sendall(message_line.encode("utf-8"))
    except OSError as e:
        return False, f"socket unwritable: {e}"
    finally:
        try:
            if sock is not None:
                sock.close()
        except OSError:
            pass
    return True, None


def _fallback_claimed_subject(ffh, teammate_name: str, session_id, project_dir: str):
    """The tasks-dir fallback when the payload carries no usable `task_subject` (module
    docstring): the SINGLE `in_progress` task in any candidate tasks dir whose `description`
    names `teammate_name` (case-insensitive substring). Returns `(subject, reason)` — exactly
    one of the two is non-`None`."""
    try:
        dirs = ffh.candidate_tasks_dirs(session_id)
    except ffh.FloorHookError as e:
        return None, f"unresolved: {e}"
    if not dirs:
        return None, "unresolved: no task_subject in payload and no session_id to fall back on"

    needle = teammate_name.lower()
    matches = set()
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(d, name), encoding="utf-8") as fh:
                    doc = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(doc, dict) or doc.get("status") != "in_progress":
                continue
            description = doc.get("description")
            if isinstance(description, str) and needle in description.lower():
                subject = doc.get("subject")
                if isinstance(subject, str) and subject.strip():
                    matches.add(subject)
    if len(matches) == 1:
        return next(iter(matches)), None
    if not matches:
        return None, "unresolved: no in_progress task names the teammate"
    return None, "unresolved: more than one in_progress task names the teammate"


def _evidence_rows(ffh, atom_id: str, project_dir: str) -> dict:
    """The evidence record, or `{"done_when": []}` when no record has been written yet at
    all — a builder mid-task with no evidence file is the ORDINARY case this hook exists to
    nudge, not a resolution failure (unlike a record that exists but fails to PARSE, which
    propagates via `ffh.read_evidence_record` and is caught by `run`'s own outer handler)."""
    path = ffh.evidence_path(atom_id, project_dir)
    if not os.path.isfile(path):
        return {"done_when": []}
    return ffh.read_evidence_record(atom_id, project_dir)


def run(payload: dict, *, project_dir: "str | None" = None, nudges_path: "str | None" = None) -> int:
    """The hook's entire decision. Returns 0 ALWAYS (AC-TIC-1/-2) — there is no refusal path
    in this hook; every branch either exits quietly (a non-atom task, a fully-met atom) or
    records a reason to the nudge ledger before exiting. The one non-trivial side effect,
    posting a message, only happens on the single AC-TIC-1 happy path: resolved atom,
    unmet locator(s), nudge cap not yet reached."""
    try:
        ffh = _import_ffh()
        if not isinstance(payload, dict):
            payload = {}

        pd = project_dir or ffh.resolve_project_dir(payload)
        nudges_file = nudges_path or default_nudges_path(pd)

        teammate_name = payload.get("teammate_name")
        if not isinstance(teammate_name, str) or not teammate_name.strip():
            teammate_name = payload.get("team_name")  # deprecated fallback field
        if not isinstance(teammate_name, str) or not _SAFE_NAME_RE.match(teammate_name.strip()):
            _append_record(nudges_file, {
                "type": "idle-error", "reason": "missing or unsafe teammate_name",
                "at": _now_iso(),
            })
            return 0
        teammate_name = teammate_name.strip()

        session_id = payload.get("session_id")
        task_subject = payload.get("task_subject")

        if isinstance(task_subject, str) and task_subject.strip():
            subject, unresolved_reason = task_subject, None
        else:
            # The payload's own `task_description` names the CURRENT idle event, not
            # necessarily the claimed task's own description in the tasks dir — the fallback
            # below reads the tasks-dir JSON's `description` field directly, never this one.
            subject, unresolved_reason = _fallback_claimed_subject(
                ffh, teammate_name, session_id, pd
            )

        if subject is None:
            _append_record(nudges_file, {
                "type": "idle-unresolved", "teammate_name": teammate_name,
                "reason": unresolved_reason, "at": _now_iso(),
            })
            return 0

        parsed = ffh.parse_atom_subject(subject)
        if parsed is None:
            return 0  # not an atom-shaped task — out of this hook's sight, no record
        release_id, atom_id = parsed

        atom = ffh.resolve_atom(release_id, atom_id, pd)
        done_when = ffh.declared_done_when(atom, pd)
        if not done_when:
            _append_record(nudges_file, {
                "type": "idle-unresolved", "teammate_name": teammate_name,
                "release_id": release_id, "atom_id": atom_id,
                "reason": "atom declares no done_when locators", "at": _now_iso(),
            })
            return 0

        record = _evidence_rows(ffh, atom_id, pd)
        rows_by_locator = {}
        for row in record.get("done_when", []):
            if isinstance(row, dict) and isinstance(row.get("locator"), str):
                rows_by_locator[row["locator"]] = row  # last-row-wins, mirrors the floor hooks

        unmet = [
            locator for locator in done_when
            if rows_by_locator.get(locator, {}).get("status") != "met"
        ]
        if not unmet:
            return 0  # every locator met — no message, no record (AC-TIC-4's "met" case)

        prior = _count_prior_nudges(nudges_file, release_id, atom_id)
        if prior >= NUDGE_CAP:
            _append_record(nudges_file, {
                "type": "idle-cap-reached", "teammate_name": teammate_name,
                "release_id": release_id, "atom_id": atom_id, "unmet": unmet,
                "reason": f"nudge cap reached ({NUDGE_CAP})", "at": _now_iso(),
            })
            return 0

        text = (
            f"IDLE-UNMET {teammate_name} atom:{release_id}/{atom_id} — "
            f"unmet: {', '.join(unmet)}"
        )
        sent, send_reason = post_idle_message(text)
        _append_record(nudges_file, {
            "type": "idle-unmet", "teammate_name": teammate_name,
            "release_id": release_id, "atom_id": atom_id, "unmet": unmet,
            "message_sent": sent, "reason": None if sent else send_reason,
            "text": text, "at": _now_iso(),
        })
        return 0
    except Exception as e:  # ALWAYS exit 0 (AC-TIC-2): record what happened, never raise it.
        try:
            pd_fallback = project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
            _append_record(nudges_path or default_nudges_path(pd_fallback), {
                "type": "idle-error", "reason": str(e), "at": _now_iso(),
            })
        except Exception:
            pass
        return 0


def _read_stdin_payload() -> dict:
    """Malformed/empty stdin is never a refusal here (unlike the floor hooks' fail-closed
    stance) — this hook only ever observes, so an unparseable payload is simply "nothing to
    resolve", handled the same as any other resolution failure inside `run`."""
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def main(argv=None) -> int:
    try:
        payload = _read_stdin_payload()
        return run(payload)
    except BaseException:  # the one hook contract with no exceptions: ALWAYS exit 0.
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
