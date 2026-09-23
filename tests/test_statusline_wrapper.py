"""statusline-wiring (v1.17.0, AC-SLW-3/-4): the wrapper's three resolution paths and its inline
fallback over a fixture config root, and the doctor's `statusline:` advisory states.

The wrapper is exercised as the real script (bash), with HOME / CLAUDE_CONFIG_DIR / CLAUDE_PROJECT_DIR
pointed at throwaway trees; a fake renderer that echoes a sentinel proves WHICH path resolved.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WRAPPER = os.path.join(REPO, "cli", "templates", "foundry-statusline.sh")
SUB_WRAPPER = os.path.join(REPO, "cli", "templates", "foundry-subagent-statusline.sh")
DOCTOR = os.path.join(REPO, "scripts", "foundry-doctor.py")
MARKER = "feat-foundry-init-statusline-wrapper"
PAYLOAD = json.dumps({"workspace": {"current_dir": "/tmp/x"}, "context_window": {"remaining_percentage": 31}})

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="jq is required by the wrapper's payload reads")


def _fake_renderer(path, sentinel):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"#!/usr/bin/env bash\ncat >/dev/null\necho '{sentinel}'\n")


def _run(wrapper, cfg, project_dir, payload=PAYLOAD, extra_env=None):
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(cfg), CLAUDE_PROJECT_DIR=str(project_dir), HOME=str(cfg))
    env.pop("FOUNDRY_STATUSLINE_EXTRAS", None)
    if extra_env:
        env.update(extra_env)
    p = subprocess.run(["bash", wrapper], input=payload, text=True, capture_output=True, env=env, cwd=str(project_dir))
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def test_shipped_wrappers_are_byte_identical_to_the_templates():
    for tpl, shipped in ((WRAPPER, "foundry-statusline-wrapper.sh"), (SUB_WRAPPER, "foundry-subagent-statusline-wrapper.sh")):
        with open(tpl, "rb") as a, open(os.path.join(REPO, "scripts", shipped), "rb") as b:
            assert a.read() == b.read(), shipped
        with open(tpl, encoding="utf-8") as fh:
            assert MARKER in fh.read()


def test_resolution_1_installed_plugins_json_wins(tmp_path):
    cfg = tmp_path / "cfg"
    install = cfg / "plugins" / "cache" / "agentic-foundry" / "foundry" / "9.9.9"
    _fake_renderer(str(install / "scripts" / "foundry-statusline.sh"), "VIA-INSTALLED")
    other = cfg / "plugins" / "cache" / "zzz-marketplace" / "foundry" / "1.0.0"
    _fake_renderer(str(other / "scripts" / "foundry-statusline.sh"), "VIA-CACHE")
    (cfg / "plugins").mkdir(parents=True, exist_ok=True)
    (cfg / "plugins" / "installed_plugins.json").write_text(json.dumps(
        {"plugins": {"foundry@agentic-foundry": [{"installPath": str(install)}]}}), encoding="utf-8")
    assert _run(WRAPPER, cfg, tmp_path) == "VIA-INSTALLED"


def test_resolution_2_cache_newest_by_version_segment_not_by_marketplace_name(tmp_path):
    cfg = tmp_path / "cfg"
    _fake_renderer(str(cfg / "plugins" / "cache" / "aaa" / "foundry" / "1.16.1" / "scripts" / "foundry-statusline.sh"), "OLD-1.16.1")
    _fake_renderer(str(cfg / "plugins" / "cache" / "zzz" / "foundry" / "1.17.0" / "scripts" / "foundry-statusline.sh"), "NEW-1.17.0")
    _fake_renderer(str(cfg / "plugins" / "cache" / "mmm" / "foundry" / "1.9.0" / "scripts" / "foundry-statusline.sh"), "OLD-1.9.0")
    assert _run(WRAPPER, cfg, tmp_path) == "NEW-1.17.0"


def test_resolution_3_self_hosting_source_checkout(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    ws = tmp_path / "ws"
    _fake_renderer(str(ws / "agentic-foundry" / "scripts" / "foundry-statusline.sh"), "VIA-SOURCE")
    assert _run(WRAPPER, cfg, ws) == "VIA-SOURCE"


def test_inline_fallback_renders_the_bar_when_nothing_resolves(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "-C", str(ws), "init", "-q", "-b", "main"], check=True)
    payload = json.dumps({"workspace": {"current_dir": str(ws)}, "context_window": {"remaining_percentage": 31}})
    out = _run(WRAPPER, cfg, ws, payload=payload)
    # six filled blocks at 69% — the same rendering the shipped renderer produces
    assert out == "⌂ ws:main · tok ██████░░░░ 69%", out


def test_inline_fallback_without_a_percentage_still_prints_the_location(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()
    out = _run(WRAPPER, cfg, ws, payload=json.dumps({"workspace": {"current_dir": str(ws)}}))
    assert out == "⌂ ws", out


def test_subagent_wrapper_prints_nothing_without_a_renderer_and_resolves_the_same_way(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    assert _run(SUB_WRAPPER, cfg, tmp_path) == ""
    _fake_renderer(str(cfg / "plugins" / "cache" / "m" / "foundry" / "2.0.0" / "scripts" / "foundry-subagent-statusline.sh"), "SUB-VIA-CACHE")
    assert _run(SUB_WRAPPER, cfg, tmp_path) == "SUB-VIA-CACHE"


# ------------------------------------------------------------------ the doctor's advisory (AC-SLW-4)
def _doctor_line(project_dir, cfg):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project_dir), CLAUDE_CONFIG_DIR=str(cfg), HOME=str(cfg))
    p = subprocess.run([sys.executable, DOCTOR], capture_output=True, text=True, env=env, cwd=str(project_dir))
    lines = [ln for ln in p.stdout.splitlines() if "statusline:" in ln]
    assert len(lines) == 1, p.stdout
    return lines[0]


def _workspace(tmp_path, settings=None):
    ws = tmp_path / "ws"
    (ws / ".claude").mkdir(parents=True)
    (ws / ".claude" / "settings.json").write_text(json.dumps(settings if settings is not None else {}), encoding="utf-8")
    return ws


def test_doctor_names_the_first_missing_piece_in_order(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    ws = _workspace(tmp_path)
    assert "no `statusLine` key" in _doctor_line(ws, cfg)
    ws = _workspace(tmp_path / "b", {"statusLine": {"type": "command", "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/foundry-statusline.sh"}})
    assert "wrapper absent" in _doctor_line(ws, cfg)
    (ws / ".claude" / "hooks").mkdir()
    (ws / ".claude" / "hooks" / "foundry-statusline.sh").write_text("#!/usr/bin/env bash\necho mine\n", encoding="utf-8")
    assert "no framework marker" in _doctor_line(ws, cfg)
    shutil.copy(WRAPPER, ws / ".claude" / "hooks" / "foundry-statusline.sh")
    line = _doctor_line(ws, cfg)
    assert "no renderer resolvable" in line and str(cfg) in line, line
    _fake_renderer(str(cfg / "plugins" / "cache" / "agentic-foundry" / "foundry" / "1.17.0" / "scripts" / "foundry-statusline.sh"), "x")
    assert "wired (renderer 1.17.0)" in _doctor_line(ws, cfg)
