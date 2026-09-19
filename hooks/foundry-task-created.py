#!/usr/bin/env python3
"""foundry-task-created — the `TaskCreated` hook: front-authorization moves INSIDE the
task-claim loop (feat-foundry-authorization-floor-hooks, AC-FLH-1, AC-FLH-3, AC-FLH-4).

Reads the `TaskCreated` payload from stdin (`session_id, hook_event_name, cwd, task_id,
task_subject, task_description, agent_id, agent_type` per the hooks reference, re-read
2026-09-19: "Blocks task creation" on exit 2). Refuses (exit 2, one-line stderr reason +
remediation) a task whose `task_subject` names an atom (`atom:<release-id>/<atom-id>`) that is NOT
authorized — a factory atom whose contract re-derives AUTHORIZED (`foundry_authz.is_authorized`)
or a charter atom whose charter file is committed (the module's one allowed subprocess: `git -C
<project> log -1 --format=%H -- <charter_ref>`). Exits 0 untouched on any other subject (one that
does not even start with `atom:`, case-insensitive), and on a payload that carries no
`task_subject` at all. Fail-closed: an unresolvable release/atom, a subject that STARTS WITH
`atom:` but is not the exact form `atom:<slug>/<slug>` (review round 1, item 7 — uppercase, a
stray space, an embedded newline, `..`, …), or ANY internal error (any caught exception, including
an unparseable-but-atom-mentioning stdin payload — item 1) exits 2 naming it.

Read-only: never writes a file, never executes a `done_when` locator, spawns no subprocess beyond
the one named above (AC-FLH-3).

    echo '{"task_subject": "atom:<release>/<atom>", "cwd": "/path/to/project"}' | \\
        foundry-task-created.py
"""
from __future__ import annotations

import json
import os
import re
import sys

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.join(os.path.dirname(_HOOKS_DIR), "scripts")

_ATOM_MENTION_RE = re.compile(r"atom:", re.IGNORECASE)


def _import_ffh():
    """Deferred, guarded import (review round 1, item 2): a broken sibling import must become
    exit 2 through this hook's own fail-closed wrapper, never an uncaught traceback at MODULE
    load time — which the platform sees as a plain exit 1, non-blocking per the hooks reference."""
    if _SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _SCRIPTS_DIR)
    import foundry_floor_hooks as ffh
    return ffh


def run(payload: dict, *, project_dir: "str | None" = None) -> int:
    """The hook's decision, isolated from stdin/argv so tests can drive it directly (including
    injecting a crash into the loader/authz layer). Its ENTIRE body is one `try/except Exception`
    (review round 1, item 2); `main()` adds a further `except BaseException` around the call to
    this function so nothing — not even a `SystemExit`/`KeyboardInterrupt`-shaped surprise from a
    misbehaving dependency — escapes as anything other than exit 0/2. Returns the exit code."""
    task_subject_repr = None
    try:
        ffh = _import_ffh()

        task_subject = payload.get("task_subject") if isinstance(payload, dict) else None
        if not isinstance(task_subject, str):
            return 0  # AC-FLH-4: a malformed payload with no task_subject is never blocked
        task_subject_repr = task_subject

        parsed = ffh.parse_atom_subject(task_subject)  # may raise MalformedAtomSubjectError
        if parsed is None:
            return 0  # does not even start with atom: (case-insensitive) — exit 0 untouched

        release_id, atom_id = parsed
        pd = project_dir or ffh.resolve_project_dir(payload)

        atom = ffh.resolve_atom(release_id, atom_id, pd)
        authorized = ffh.is_atom_authorized(atom, pd)
    except Exception as e:  # fail-closed: ANY internal error refuses (AC-FLH-1)
        print(
            f"foundry-task-created: REFUSED task_subject={task_subject_repr!r} — {e}",
            file=sys.stderr,
        )
        return 2

    if authorized:
        return 0

    remediation = (
        "commit the charter" if getattr(atom, "charter_ref", None) else "/foundry:authorize <spec>"
    )
    print(
        f"foundry-task-created: REFUSED — atom:{release_id}/{atom_id} is not authorized "
        f"({remediation})",
        file=sys.stderr,
    )
    return 2


def _read_stdin_payload() -> "tuple[dict, int | None]":
    """Read + parse the `TaskCreated` payload from stdin (review round 1, item 1). Returns
    `(payload, None)` on success (including an empty payload for empty stdin — `run()` then sees
    no `task_subject` and exits 0). Returns `(dict, exit_code)` when the caller should stop right
    here: a JSON-parse failure exits 2 (naming the parse error) when the raw text mentions
    `atom:` (any case) — it might have BEEN an atom-shaped payload, mangled in transit — and exits
    0 (untouched, with a one-line stderr note) otherwise, since a payload that never mentioned an
    atom at all cannot be one this hook needed to block."""
    raw = sys.stdin.read()
    if not raw.strip():
        return {}, None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        if _ATOM_MENTION_RE.search(raw):
            print(
                f"foundry-task-created: REFUSED — stdin is not valid JSON and mentions 'atom:': {e}",
                file=sys.stderr,
            )
            return {}, 2
        print(
            "foundry-task-created: stdin is not valid JSON (no 'atom:' mention) — untouched",
            file=sys.stderr,
        )
        return {}, 0
    if not isinstance(payload, dict):
        payload = {}
    return payload, None


def main(argv=None) -> int:
    try:
        payload, early_exit = _read_stdin_payload()
        if early_exit is not None:
            return early_exit
        return run(payload)
    except BaseException as e:  # review round 1, item 2: exit 1 must be unreachable
        print(f"foundry-task-created: REFUSED — internal error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
