#!/usr/bin/env python3
"""foundry-task-completed — the `TaskCompleted` hook: the `done_when` evidence gate moves INSIDE
the task-completion loop (feat-foundry-authorization-floor-hooks, AC-FLH-2, AC-FLH-3, AC-FLH-4).

Reads the `TaskCompleted` payload from stdin (same field shape as `TaskCreated`; hooks reference
re-read 2026-09-19: exit 2 "Blocks task completion"). Refuses (exit 2, one-line stderr reason) a
completion whose `task_subject` names an atom unless its EVIDENCE RECORD
(`.foundry/evidence/<atom-id>.json`, written by the builder — the tick prompt's / mode-autonomous's
definition-of-done step, AC-FLH-5) declares a `met` row, newer than the task's own creation time
AND not more than 300s in the future (review round 1, item 4 — a far-future stamp like `2099-01-01`
no longer satisfies "newer" forever), for every one of the atom's declared `done_when` locators
(its contract's `done_when`, or a charter's `## Done when` section). Exits 0 untouched on any
other subject (one that does not even start with `atom:`, case-insensitive), or a payload carrying
no `task_subject`. Fail-closed on: a missing record, a record older than the task (or timestamped
too far in the future), an unmet locator, a locator absent from the record, an atom with no
`done_when` declared, a task id absent from every tasks dir tried, a malformed atom-shaped subject
(item 7), or ANY internal error (any caught exception, including an unparseable-but-atom-mentioning
stdin payload — item 1).

The task's own creation time is read from the tasks dir: an explicit `--tasks-dir` first, then
`~/.claude/tasks/<session_id>/`, then `~/.claude/tasks/session-<session_id[:8]>/` — the first
candidate that actually contains `<task_id>.json` wins (review round 1, item 9). See the shared
helper module's own docstring for the platform (`st_birthtime`) and atomic-rewrite caveats on
what "creation time" means here (item 5, an R4 residual).

A duplicate locator in the evidence record's `done_when` list is LAST-ROW-WINS (review round 1,
item 8): a stale `met` row followed by a later `unmet` row for the same locator refuses; the
reverse (an early `unmet` followed by a later `met`) admits.

Never runs a `done_when` locator itself (the builder already ran it; this hook only reads the
record), never writes a file, spawns no subprocess (AC-FLH-3).

    echo '{"task_subject": "atom:<release>/<atom>", "task_id": "42", "cwd": "/path"}' | \\
        foundry-task-completed.py --tasks-dir /path/to/tasks-dir
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.join(os.path.dirname(_HOOKS_DIR), "scripts")

_ATOM_MENTION_RE = re.compile(r"atom:", re.IGNORECASE)


def _import_ffh():
    """Deferred, guarded import (review round 1, item 2) — see foundry-task-created.py's own
    docstring for why this cannot be a module-level `import`."""
    if _SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _SCRIPTS_DIR)
    import foundry_floor_hooks as ffh
    return ffh


def run(payload: dict, *, project_dir: "str | None" = None, tasks_dir: "str | None" = None) -> int:
    """The hook's decision, isolated from stdin/argv so tests can drive it directly (including
    injecting a crash into the evidence reader). Its ENTIRE body is one `try/except Exception`
    (review round 1, item 2); `main()` adds a further `except BaseException` around the call to
    this function. Returns the exit code."""
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
        session_id = payload.get("session_id") if isinstance(payload, dict) else None
        task_id = payload.get("task_id") if isinstance(payload, dict) else None

        atom = ffh.resolve_atom(release_id, atom_id, pd)

        done_when = ffh.declared_done_when(atom, pd)
        if not done_when:
            raise ffh.FloorHookError(f"atom:{release_id}/{atom_id} declares no done_when locators")

        if not isinstance(task_id, str) or not task_id.strip():
            raise ffh.FloorHookError("payload carries no usable task_id")
        created_at = ffh.task_created_at(task_id, tasks_dir=tasks_dir, session_id=session_id)

        record = ffh.read_evidence_record(atom_id, pd)
        record_at = ffh.parse_utc(record["at"])
        ffh.check_not_future(record_at, label="evidence record 'at'")
        if record_at <= created_at:
            raise ffh.FloorHookError(
                f"evidence record for atom:{release_id}/{atom_id} predates the task "
                f"(record at {record_at.isoformat()}, task created {created_at.isoformat()})"
            )

        # Last-row-wins on a duplicate locator (review round 1, item 8): iterate in file order
        # and let a LATER row for the same locator overwrite an earlier one.
        rows_by_locator = {}
        for row in record["done_when"]:
            rows_by_locator[row["locator"]] = row

        absent, unmet = [], []
        for locator in done_when:
            row = rows_by_locator.get(locator)
            if row is None:
                absent.append(locator)
                continue
            if row["status"] != "met":
                unmet.append(locator)
                continue
            row_at = ffh.parse_utc(row["at"])
            ffh.check_not_future(row_at, label=f"done_when[{locator!r}] 'at'")
            if row_at <= created_at:
                unmet.append(locator)
        if absent or unmet:
            raise ffh.FloorHookError(
                f"atom:{release_id}/{atom_id} completion refused — absent locator(s): "
                f"{absent or '(none)'}; unmet locator(s): {unmet or '(none)'}"
            )
    except Exception as e:  # fail-closed: ANY internal error refuses (AC-FLH-2)
        print(
            f"foundry-task-completed: REFUSED task_subject={task_subject_repr!r} — {e}",
            file=sys.stderr,
        )
        return 2

    return 0


def _read_stdin_payload() -> "tuple[dict, int | None]":
    """See `foundry-task-created.py::_read_stdin_payload` — identical rule (review round 1, item
    1)."""
    raw = sys.stdin.read()
    if not raw.strip():
        return {}, None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        if _ATOM_MENTION_RE.search(raw):
            print(
                f"foundry-task-completed: REFUSED — stdin is not valid JSON and mentions "
                f"'atom:': {e}",
                file=sys.stderr,
            )
            return {}, 2
        print(
            "foundry-task-completed: stdin is not valid JSON (no 'atom:' mention) — untouched",
            file=sys.stderr,
        )
        return {}, 0
    if not isinstance(payload, dict):
        payload = {}
    return payload, None


def main(argv=None) -> int:
    try:
        ap = argparse.ArgumentParser(description="TaskCompleted done_when evidence gate")
        ap.add_argument(
            "--tasks-dir", dest="tasks_dir", default=None,
            help="override the tasks dir (default: derived from the payload's session_id)",
        )
        args = ap.parse_args(argv)

        payload, early_exit = _read_stdin_payload()
        if early_exit is not None:
            return early_exit
        return run(payload, tasks_dir=args.tasks_dir)
    except BaseException as e:  # review round 1, item 2: exit 1 must be unreachable
        print(f"foundry-task-completed: REFUSED — internal error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
