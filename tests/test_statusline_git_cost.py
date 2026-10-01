"""v1.18.5: the status line never walks the working tree. It runs after every event in every session;
on a workspace shared by several agent containers over a VM file share, its `git status
--untracked-files=normal` walks overlapped and took 1-5 s each (bursts to 142 s). It now runs only
cheap ref reads, with GIT_OPTIONAL_LOCKS=0 so it never takes the index lock."""
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RENDERER = ROOT / "scripts" / "foundry-statusline.sh"


def test_renderer_never_runs_git_status_and_never_takes_optional_locks(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "-C", str(ws), "init", "-q", "-b", "main"], check=True)
    (ws / "untracked.txt").write_text("x")
    log = tmp_path / "git.log"
    shim = tmp_path / "bin"
    shim.mkdir()
    real = shutil.which("git")
    (shim / "git").write_text(
        f"#!/bin/bash\necho \"$GIT_OPTIONAL_LOCKS|$*\" >> '{log}'\nexec '{real}' \"$@\"\n")
    (shim / "git").chmod(0o755)
    env = dict(os.environ, PATH=f"{shim}:{os.environ['PATH']}", HOME=str(tmp_path), CLAUDE_PROJECT_DIR=str(ws))
    payload = json.dumps({"session_id": "s", "workspace": {"current_dir": str(ws)},
                          "context_window": {"remaining_percentage": 80}})
    p = subprocess.run([shutil.which("bash"), str(RENDERER)], input=payload, text=True,
                       capture_output=True, env=env, cwd=str(ws))
    assert p.returncode == 0
    calls = log.read_text().splitlines()
    assert calls, "the renderer should read the branch"
    assert not any(" status" in c or c.split("|", 1)[1].startswith("status") or "|-C " in c and " status " in c
                   for c in calls), calls
    assert all(c.startswith("0|") for c in calls), "every git call must run with GIT_OPTIONAL_LOCKS=0"
    assert "?" not in p.stdout.split("·")[0], "no untracked count in the repo segment"
