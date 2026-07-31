#!/usr/bin/env python3
"""foundry-native-todo-discipline — the SessionStart native-Tasks discipline injection
(feat-foundry-native-todo-discipline, AC-NTD-1). REPLACES the retired Operator-Status-Protocol
`@status` block + its 538-line renderer/validator + its SessionStart injection.

It emits an `additionalContext` directive MANDATING the agent keep Claude Code's NATIVE Tasks list
current with the SAME foundry work-context taxonomy the retired status block encoded — epic
(release/feature/ad-hoc) / current atom-task / gate-or-governance state / next — kept current
across the authorize -> implement -> gate -> merge transitions. The taxonomy is preserved; only the
SURFACE changes (custom text block -> the native list the harness renders).

HARD CONSTRAINT — instruction-injection is the ONLY lever a plugin has. The native Tasks tools
(TaskCreate / TaskUpdate / TaskGet / TaskList) are AGENT-ONLY and bypass PreToolUse/PostToolUse, so
a hook CANNOT programmatically write the list. This emitter INSTRUCTS; it does NOT (and cannot)
write or render the list. No custom renderer/validator over native state is built — that is the
category error this atom removes (the harness renders the native list). Per-response freshness is
INSTRUCTED, not hard-enforced (the honest deferred bound the `discipline/` checks share).

Advisory + fail-open: the `--session-start` path always exits 0 (mirrors foundry-doctor.py
--session-start) so it never wedges a session.
"""
from __future__ import annotations

import argparse
import sys

# The native Claude Code Tasks primitive (ground-truth) + its statuses. Named verbatim in the
# directive so the AC-NTD-5 drop-in can assert (control a) the injection names the native tools.
NATIVE_TASK_TOOLS = ("TaskCreate", "TaskUpdate", "TaskList")
NATIVE_TASK_STATUSES = ("pending", "in_progress", "blocked", "completed")


def session_start_directive():
    """The native-Tasks discipline injected at SessionStart (advisory, universal). Carries the SAME
    work-context taxonomy the retired @status block encoded — epic / current atom-task /
    gate-or-governance state / next — on Claude Code's NATIVE Tasks list. The taxonomy is emitted as
    LABELED list elements (not bare prose) so the AC-NTD-5 drop-in can scan it label-scoped."""
    tools = " / ".join(NATIVE_TASK_TOOLS)
    statuses = " / ".join(NATIVE_TASK_STATUSES)
    return (
        "[foundry:native-todo-discipline] Work-context discipline is ACTIVE for this session. "
        f"Maintain Claude Code's NATIVE Tasks list (the {tools} tools; statuses {statuses}) as the "
        "single work-context surface, and keep it CURRENT across the "
        "authorize -> implement -> gate -> merge transitions. Carry the foundry work-context "
        "taxonomy as the task set:\n\n"
        "- epic: the release / feature / ad-hoc this session serves\n"
        "- atom-task: the current atom-task being worked\n"
        "- gate-state: the gate-or-governance state (authorize / implement / gate / merge)\n"
        "- next: the next step\n\n"
        f"Drive the list with {tools} as state transitions; keep exactly one task in_progress at a "
        "time and move it to completed (or blocked) as it lands. The harness renders the list "
        "NATIVELY — do NOT build or print a parallel tracker or a custom render. This is advisory "
        "discipline (instructed, not hard-enforced per response)."
    )


def main():
    ap = argparse.ArgumentParser(description="foundry-native-todo-discipline")
    ap.add_argument("--session-start", action="store_true",
                    help="emit the native-Tasks discipline directive for the SessionStart hook "
                         "(advisory, fail-open)")
    a = ap.parse_args()
    if a.session_start:
        # Advisory + fail-open: NEVER wedge a session (mirror foundry-doctor.py --session-start).
        try:
            print(session_start_directive())
        except Exception:
            pass
        sys.exit(0)
    ap.print_help()


if __name__ == "__main__":
    main()
