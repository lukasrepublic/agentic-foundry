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


def _fake_renderer(path, sentinel, header=None):
    """A stand-in renderer that carries the shipped renderer's own header line — the wrapper
    refuses to run a resolved file without it (security review, Risk 3)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    header = header or f"# {os.path.basename(path)} — test fixture"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"#!/usr/bin/env bash\n{header}\ncat >/dev/null\necho '{sentinel}'\n")


def test_a_resolved_file_without_the_renderer_header_is_not_run(tmp_path):
    cfg = tmp_path / "cfg"
    _fake_renderer(str(cfg / "plugins" / "cache" / "m" / "foundry" / "9.0.0" / "scripts" / "foundry-statusline.sh"),
                   "PLANTED", header="# not the renderer")
    ws = tmp_path / "ws"
    ws.mkdir()
    out = _run(WRAPPER, cfg, ws, payload=json.dumps({"workspace": {"current_dir": str(ws)}}))
    assert out == "⌂ ws", out


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


# ------------------------------------------ AC-V118C-8 (audit D9 + D4) — the doctor's truth fixes
def _load_doctor():
    import importlib.util
    spec = importlib.util.spec_from_file_location("foundry_doctor_statusline_v118", DOCTOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _registry(cfg, records):
    (cfg / "plugins").mkdir(parents=True, exist_ok=True)
    (cfg / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": {"foundry@agentic-foundry": records}}), encoding="utf-8")


def test_doctor_renderer_picks_this_projects_record_not_the_first(tmp_path):
    doctor = _load_doctor()
    cfg = tmp_path / "cfg"
    ws = tmp_path / "ws"
    ws.mkdir()
    other = cfg / "plugins" / "cache" / "agentic-foundry" / "foundry" / "1.1.0"
    mine = cfg / "plugins" / "cache" / "agentic-foundry" / "foundry" / "1.2.0"
    user = cfg / "plugins" / "cache" / "agentic-foundry" / "foundry" / "1.0.0"
    for d in (other, mine, user):
        _fake_renderer(str(d / "scripts" / "foundry-statusline.sh"), "x")
    _registry(cfg, [
        {"scope": "project", "projectPath": str(tmp_path / "another-project"), "installPath": str(other)},
        {"scope": "user", "installPath": str(user)},
        {"scope": "project", "projectPath": str(ws), "installPath": str(mine)},
    ])
    assert doctor._statusline_renderer_path(str(cfg), str(ws))[1] == "1.2.0"
    # no record for this project -> the user-scope record, never another project's
    assert doctor._statusline_renderer_path(str(cfg), str(tmp_path / "third"))[1] == "1.0.0"


def test_doctor_renderer_never_takes_another_projects_record(tmp_path):
    doctor = _load_doctor()
    record = {"scope": "project", "projectPath": str(tmp_path / "another"), "installPath": "/x"}
    assert doctor._registry_record_for([record], str(tmp_path / "ws")) is None


def test_doctor_updater_remedy_is_pinned_to_the_shipped_updater_version(tmp_path):
    with open(os.path.join(REPO, "cli-update", "package.json"), encoding="utf-8") as fh:
        pinned = json.load(fh)["version"]
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    ws = _workspace(tmp_path)
    assert f"`npx update-agentic-workspace@{pinned}` (it wires it)" in _doctor_line(ws, cfg)
    ws = _workspace(tmp_path / "b", {"statusLine": {"type": "command", "command": "x"}})
    assert f"`npx update-agentic-workspace@{pinned}`" in _doctor_line(ws, cfg)
    doctor = _load_doctor()
    assert doctor._updater_cmd(str(tmp_path / "no-plugin-here")) == "npx update-agentic-workspace"
