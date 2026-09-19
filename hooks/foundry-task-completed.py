#!/usr/bin/env python3
"""foundry-task-completed — the `TaskCompleted` hook: the `done_when` evidence gate moves INSIDE
the task-completion loop (feat-foundry-authorization-floor-hooks, AC-FLH-2, AC-FLH-3, AC-FLH-4).

Reads the `TaskCompleted` payload from stdin (same field shape as `TaskCreated`; hooks reference
re-read 2026-09-19: exit 2 "Blocks task completion"). Refuses (exit 2, one-line stderr reason) a
completion whose `task_subject` names an atom unless its EVIDENCE RECORD
(`.foundry/evidence/<atom-id>.json`, written by the builder — the tick prompt's / mode-autonomous's
definition-of-done step, AC-FLH-5) declares a `met` row, newer than the task's own creation time,
for every one of the atom's declared `done_when` locators (its contract's `done_when`, or a
charter's `## Done when` section). Exits 0 untouched on any other subject, or a payload carrying no
`task_subject`. Fail-closed on: a missing record, a record older than the task, an unmet locator, a
locator absent from the record, an atom with no `done_when` declared, a task id absent from the
tasks dir, or ANY internal error (any caught exception).

The task's own creation time is read from the tasks dir (`--tasks-dir`, default derived from the
payload's `session_id`) — see the shared helper module's own docstring for why this is the task
FILE's filesystem creation time rather than a JSON field (the real task JSON on this machine
carries no `createdAt` key).

Never runs a `done_when` locator itself (the builder already ran it; this hook only reads the
record), never writes a file, spawns no subprocess (AC-FLH-3).

    echo '{"task_subject": "atom:<release>/<atom>", "task_id": "42", "cwd": "/path"}' | \\
        foundry-task-completed.py --tasks-dir /path/to/tasks-dir
"""
from __future__ import annotations

import argparse
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


def run(payload: dict, *, project_dir: "str | None" = None, tasks_dir: "str | None" = None) -> int:
    """The hook's decision, isolated from stdin/argv so tests can drive it directly (including
    injecting a crash into the evidence reader). Returns the exit code."""
    task_subject = payload.get("task_subject") if isinstance(payload, dict) else None
    if not isinstance(task_subject, str):
        return 0  # AC-FLH-4: a malformed payload with no task_subject is never blocked

    parsed = ffh.parse_atom_subject(task_subject)
    if parsed is None:
        return 0  # any other subject exits 0 untouched

    release_id, atom_id = parsed
    pd = project_dir or _project_dir_from_payload(payload)
    td = tasks_dir or ffh.default_tasks_dir(payload)
    task_id = payload.get("task_id") if isinstance(payload, dict) else None

    try:
        atom = ffh.resolve_atom(release_id, atom_id, pd)

        done_when = ffh.declared_done_when(atom, pd)
        if not done_when:
            raise ffh.FloorHookError(f"atom:{release_id}/{atom_id} declares no done_when locators")

        if not isinstance(task_id, str) or not task_id.strip():
            raise ffh.FloorHookError("payload carries no usable task_id")
        created_at = ffh.task_created_at(task_id, td)

        record = ffh.read_evidence_record(atom_id, pd)
        record_at = ffh.parse_utc(record["at"])
        if record_at <= created_at:
            raise ffh.FloorHookError(
                f"evidence record for atom:{release_id}/{atom_id} predates the task "
                f"(record at {record_at.isoformat()}, task created {created_at.isoformat()})"
            )

        rows_by_locator = {}
        for row in record["done_when"]:
            rows_by_locator.setdefault(row["locator"], row)

        absent, unmet = [], []
        for locator in done_when:
            row = rows_by_locator.get(locator)
            if row is None:
                absent.append(locator)
                continue
            if row["status"] != "met":
                unmet.append(locator)
                continue
            if ffh.parse_utc(row["at"]) <= created_at:
                unmet.append(locator)
        if absent or unmet:
            raise ffh.FloorHookError(
                f"atom:{release_id}/{atom_id} completion refused — absent locator(s): "
                f"{absent or '(none)'}; unmet locator(s): {unmet or '(none)'}"
            )
    except Exception as e:  # fail-closed: ANY internal error refuses (AC-FLH-2)
        print(
            f"foundry-task-completed: REFUSED atom:{release_id}/{atom_id} — {e}", file=sys.stderr
        )
        return 2

    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="TaskCompleted done_when evidence gate")
    ap.add_argument("--tasks-dir", dest="tasks_dir", default=None,
                     help="override the tasks dir (default: derived from the payload's session_id)")
    args = ap.parse_args(argv)

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
    return run(payload, tasks_dir=args.tasks_dir)


if __name__ == "__main__":
    raise SystemExit(main())
