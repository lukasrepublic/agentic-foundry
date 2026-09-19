"""tests/test_native_todo_discipline.py — coverage for both environment branches of the
SessionStart native-todo-discipline hook (charter `task-tool-instruction-fix`, AC-TTI-1/2/4).

The native Task tools (`TaskCreate`/`TaskUpdate`/`TaskList`) exist only in a session with
`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`; elsewhere the hook must not instruct the agent to
reach for a tool it does not have. Instead it names the manifest-as-queue fallback: the release
manifest (`.foundry/releases/<id>/release.yaml`) + `state.yaml` as the work-context surface.

Hermetic: the shipped script is run as a real subprocess (mirrors tests/test_hooks_guards.py's
`_run_hook` pattern) under a controlled environment, asserting on stdout only — never importing
the module in-process (the flag is read at call time via `os.environ.get`, so the subprocess
boundary is the honest seam for "the hook's environment").
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "foundry-native-todo-discipline.py"

NATIVE_TASK_TOOLS = ("TaskCreate", "TaskUpdate", "TaskList")
FALLBACK_LINE = "work context = the release manifest (`.foundry/releases/<id>/release.yaml`) + `state.yaml`"


def _run(extra_env):
    env = dict(os.environ)
    env.pop("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", None)
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--session-start"],
        capture_output=True, text=True, env=env, timeout=30,
    )


# --------------------------------------------------------------- AC-TTI-1: flag unset/not "1"
def test_flag_unset_emits_manifest_fallback_not_task_tools():
    p = _run({})
    assert p.returncode == 0
    assert FALLBACK_LINE in p.stdout
    for tool in NATIVE_TASK_TOOLS:
        assert tool not in p.stdout


def test_flag_not_one_emits_manifest_fallback():
    for bogus in ("0", "true", "TRUE", "yes", ""):
        p = _run({"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": bogus})
        assert p.returncode == 0, bogus
        assert FALLBACK_LINE in p.stdout, bogus
        for tool in NATIVE_TASK_TOOLS:
            assert tool not in p.stdout, bogus


# --------------------------------------------------------------- AC-TTI-2: flag == "1"
def test_flag_one_emits_existing_task_discipline_unchanged():
    p = _run({"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"})
    assert p.returncode == 0
    for tool in NATIVE_TASK_TOOLS:
        assert tool in p.stdout
    assert FALLBACK_LINE not in p.stdout
    assert "native-todo-discipline" in p.stdout


# --------------------------------------------------------------- AC-TTI-4 (this suite itself) +
# fail-open sanity: the hook always exits 0 regardless of branch (never wedges a session).
def test_always_exits_zero():
    assert _run({}).returncode == 0
    assert _run({"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"}).returncode == 0
