"""v1.18.3: the token bar without jq. A fresh host (Windows/WSL, a slim container) often has no jq; the
wrapper's inline fallback and the shipped renderer both read the context figure by bash pattern
matching then, so `tok` is still on the line. Run with a PATH that holds every tool the scripts use
EXCEPT jq (and bc, whose absence once printed a false `0%`)."""
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WRAPPER = ROOT / "cli" / "templates" / "foundry-statusline.sh"
RENDERER = ROOT / "scripts" / "foundry-statusline.sh"
TOOLS = ("bash", "git", "awk", "cat", "head", "tail", "basename", "dirname", "mkdir", "rm", "rmdir", "mv",
         "date", "sleep", "sort", "cut", "grep", "tr", "ls", "timeout", "env", "python3")


def _bin_without_jq(tmp_path):
    b = tmp_path / "bin"
    b.mkdir()
    for t in TOOLS:
        p = shutil.which(t)
        if p:
            (b / t).symlink_to(p)
    assert not (b / "jq").exists() and not (b / "bc").exists()
    return str(b)


def _ws(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "-C", str(ws), "init", "-q", "-b", "main"], check=True)
    return ws


def test_inline_fallback_renders_tok_without_jq(tmp_path):
    ws = _ws(tmp_path)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    env = {"PATH": _bin_without_jq(tmp_path), "HOME": str(cfg), "CLAUDE_CONFIG_DIR": str(cfg),
           "CLAUDE_PROJECT_DIR": str(ws), "XDG_CACHE_HOME": str(tmp_path / "xdg")}
    payload = json.dumps({"session_id": "s", "workspace": {"current_dir": str(ws)},
                          "context_window": {"remaining_percentage": 31}})
    p = subprocess.run([shutil.which("bash"), str(WRAPPER)], input=payload, text=True, capture_output=True,
                       env=env, cwd=str(ws))
    assert p.returncode == 0
    assert p.stdout.strip() == "⌂ ws:main · tok ██████░░░░ 69%", p.stdout


def test_renderer_renders_tok_without_jq(tmp_path):
    ws = _ws(tmp_path)
    env = {"PATH": _bin_without_jq(tmp_path), "HOME": str(tmp_path), "CLAUDE_PROJECT_DIR": str(ws)}
    payload = json.dumps({"session_id": "s", "workspace": {"current_dir": str(ws)},
                          "context_window": {"remaining_percentage": 95}})
    p = subprocess.run([shutil.which("bash"), str(RENDERER)], input=payload, text=True, capture_output=True,
                       env=env, cwd=str(ws))
    assert p.returncode == 0
    assert "tok " in p.stdout and " 6%" in p.stdout, p.stdout
