"""tests/test_worktree.py — converted from scripts/foundry_checks/{worktree-native-isolation,
worktree-native-passthrough, native-todo-discipline}.py.

`hooks/foundry-worktree-create.sh` already carries its own comprehensive, hermetic `--selftest`
(AC-WNP-1 native-passthrough, AC-MRDISPATCH-2/-3/-9 — the worktree spec/contract staging +
preflight machinery those two checks tested from the outside). Rather than reimplement its
crafted-stdin-envelope fixtures, this module drives the REAL hook's own selftest directly
(subprocess) and adds a direct wiring assertion for the native-Tasks discipline hook
(hooks.json SessionStart wiring + the emitted directive naming the native Tasks tools).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from conftest import REPO_ROOT


def test_worktree_create_hook_selftest():
    script = os.path.join(REPO_ROOT, "hooks", "foundry-worktree-create.sh")
    proc = subprocess.run(["bash", script, "--selftest"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "FOUNDRY-WORKTREE-CREATE-SELFTEST-GREEN" in proc.stdout
    assert "AC-WNP-1 no-manifest-native-fallback: PASS" in proc.stdout


class TestNativeTodoDiscipline:
    def test_hooks_json_wires_session_start_emitter(self):
        with open(os.path.join(REPO_ROOT, "hooks", "hooks.json"), encoding="utf-8") as f:
            data = json.load(f)
        commands = []

        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k == "command" and isinstance(v, str):
                        commands.append(v)
                    else:
                        walk(v)
            elif isinstance(o, list):
                for x in o:
                    walk(x)

        walk(data.get("hooks", {}))
        assert any("foundry-native-todo-discipline.py" in c and "--session-start" in c
                  for c in commands)

    def test_session_start_emitter_present_and_executable(self):
        script = os.path.join(REPO_ROOT, "scripts", "foundry-native-todo-discipline.py")
        assert os.path.isfile(script)
        proc = subprocess.run([sys.executable, script, "--session-start"],
                              capture_output=True, text=True)
        assert proc.returncode == 0
        for tool in ("TaskCreate", "TaskUpdate", "TaskList"):
            assert tool in proc.stdout, proc.stdout
