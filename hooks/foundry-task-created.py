#!/usr/bin/env python3
"""foundry-task-created — the `TaskCreated` hook: front-authorization moves INSIDE the
task-claim loop (feat-foundry-authorization-floor-hooks, AC-FLH-1, AC-FLH-3, AC-FLH-4).

Reads the `TaskCreated` payload from stdin (`session_id, hook_event_name, cwd, task_id,
task_subject, task_description, agent_id, agent_type` per the hooks reference, re-read
2026-09-19: "Blocks task creation" on exit 2). Refuses (exit 2, one-line stderr reason +
remediation) a task whose `task_subject` names an atom (`atom:<release-id>/<atom-id>`) that is NOT
authorized — a factory atom whose contract re-derives AUTHORIZED (`foundry_authz.is_authorized`)
or a charter atom whose charter file is committed (the module's one allowed subprocess: `git -C
<project> log -1 --format=%H -- <charter_ref>`). Exits 0 untouched on any other subject, and on a
payload that carries no `task_subject` at all — a non-atom task is never blocked by accident.
Fail-closed: an unresolvable release/atom, a non-slug id, or ANY internal error (any caught
exception) exits 2 naming it.

Read-only: never writes a file, never executes a `done_when` locator, spawns no subprocess beyond
the one named above (AC-FLH-3).

    echo '{"task_subject": "atom:<release>/<atom>", "cwd": "/path/to/project"}' | \\
        foundry-task-created.py
"""
from __future__ import annotations

import json
import os
import sys

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.join(os.path.dirname(_HOOKS_DIR), "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import foundry_floor_hooks as ffh  # noqa: E402


def _project_dir_from_payload(payload: dict) -> str:
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    if isinstance(cwd, str) and cwd.strip():
        return cwd
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def run(payload: dict, *, project_dir: "str | None" = None) -> int:
    """The hook's decision, isolated from stdin/argv so tests can drive it directly (including
    injecting a crash into the loader/authz layer). Returns the exit code."""
    task_subject = payload.get("task_subject") if isinstance(payload, dict) else None
    if not isinstance(task_subject, str):
        return 0  # AC-FLH-4: a malformed payload with no task_subject is never blocked

    parsed = ffh.parse_atom_subject(task_subject)
    if parsed is None:
        return 0  # any other subject exits 0 untouched (AC-FLH-1/AC-FLH-4)

    release_id, atom_id = parsed
    pd = project_dir or _project_dir_from_payload(payload)

    try:
        atom = ffh.resolve_atom(release_id, atom_id, pd)
        authorized = ffh.is_atom_authorized(atom, pd)
    except Exception as e:  # fail-closed: ANY internal error refuses (AC-FLH-1)
        print(f"foundry-task-created: REFUSED atom:{release_id}/{atom_id} — {e}", file=sys.stderr)
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


def main(argv=None) -> int:
    try:
        raw = sys.stdin.read()
    except Exception:
        raw = ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return run(payload)


if __name__ == "__main__":
    raise SystemExit(main())
