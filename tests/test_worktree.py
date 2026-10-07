"""tests/test_worktree.py — converted from scripts/foundry_checks/{worktree-native-isolation,
worktree-native-passthrough}.py. (The native-todo-discipline half went with the SessionStart emitter
in v2.0.0.)

`hooks/foundry-worktree-create.sh` already carries its own comprehensive, hermetic `--selftest`
(AC-WNP-1 native-passthrough, AC-MRDISPATCH-2/-3/-9 — the worktree spec/contract staging +
preflight machinery those two checks tested from the outside). Rather than reimplement its
crafted-stdin-envelope fixtures, this module drives the REAL hook's own selftest directly
(subprocess).
"""
from __future__ import annotations

import os
import subprocess

from conftest import REPO_ROOT


def test_worktree_create_hook_selftest():
    script = os.path.join(REPO_ROOT, "hooks", "foundry-worktree-create.sh")
    proc = subprocess.run(["bash", script, "--selftest"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "FOUNDRY-WORKTREE-CREATE-SELFTEST-GREEN" in proc.stdout
    assert "AC-WNP-1 no-manifest-native-fallback: PASS" in proc.stdout
