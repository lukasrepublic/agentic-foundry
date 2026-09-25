"""hotfix-v1.17.4 (ER #236): the doctor's `retired-artifacts` advisory line and the shipped catalogue.

The sweep itself (plan / apply / rows) is unit-tested in cli/test/retired-artifacts.test.mjs and driven
end to end in cli/test/update-orchestration.test.mjs; this file pins the Python side.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DOCTOR = os.path.join(REPO, "scripts", "foundry-doctor.py")
CATALOGUE = os.path.join(REPO, "cli", "retired-artifacts.json")


def _doctor_line(project_dir):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project_dir))
    p = subprocess.run([sys.executable, DOCTOR], capture_output=True, text=True, env=env, cwd=str(project_dir))
    lines = [ln for ln in p.stdout.splitlines() if "retired-artifacts:" in ln]
    assert len(lines) == 1, p.stdout
    return lines[0]


def test_catalogue_is_well_formed_and_never_names_operator_dirs():
    with open(CATALOGUE, encoding="utf-8") as fh:
        doc = json.load(fh)
    assert doc["schema_version"] == 1 and doc["entries"]
    for e in doc["entries"]:
        assert not os.path.isabs(e["path"]) and ".." not in e["path"].split("/")
        assert e["kind"] in ("file", "dir")
        assert not e["path"].startswith((".claude/skills", ".claude/agents"))
        assert e["path"].count("*") <= 1 and ("*" not in e["path"] or "/" not in e["path"].split("*", 1)[1])


def test_doctor_names_present_leftovers_and_is_advisory_only(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".claude").mkdir(parents=True)
    (ws / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    assert "none present" in _doctor_line(ws)
    (ws / ".foundry").mkdir()
    (ws / ".foundry" / "wiring-hash.pin").write_text("x", encoding="utf-8")
    (ws / ".claude" / "hooks").mkdir()
    (ws / ".claude" / "hooks" / "zeta-exec-guard.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (ws / ".claude" / "hooks" / "aws-exec-guard.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (ws / ".claude" / "hooks" / "foundry-cloud-cli-exec-guard.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    # PR #237 security review Risk 1: a hook a settings hook command still names is not stale
    (ws / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": ".claude/hooks/aws-exec-guard.sh"}]}]}}),
        encoding="utf-8")
    line = _doctor_line(ws)
    assert "[adv ]" in line and "2 present" in line and ".foundry/wiring-hash.pin" in line
    assert "zeta-exec-guard.sh" in line and "aws-exec-guard.sh" not in line
    assert "foundry-cloud-cli-exec-guard.sh" not in line
    # AC-V118C-8 (audit D4): the remedy names the updater version this plugin was released with
    with open(os.path.join(REPO, "cli-update", "package.json"), encoding="utf-8") as fh:
        pinned = json.load(fh)["version"]
    assert f"npx update-agentic-workspace@{pinned} --cleanup" in line
    # unreadable settings -> no hook is called stale (fail-closed); the pin still is
    (ws / ".claude" / "settings.local.json").write_text("{ not json", encoding="utf-8")
    line = _doctor_line(ws)
    assert "1 present" in line and "exec-guard" not in line


def test_doctor_keeps_a_retired_path_a_workflow_still_names(tmp_path):
    """ER #241: the advisory must not suggest --cleanup for a path the workspace's CI still reads."""
    ws = tmp_path / "ws"
    (ws / ".claude").mkdir(parents=True)
    (ws / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    (ws / ".foundry").mkdir()
    (ws / ".foundry" / "hasher-ref").write_text("v1.2.3\n", encoding="utf-8")
    (ws / ".github" / "workflows").mkdir(parents=True)
    (ws / ".github" / "workflows" / "drift.yml").write_text("run: test -f .foundry/hasher-ref\n", encoding="utf-8")
    line = _doctor_line(ws)
    assert "none present" in line and "1 retired path(s) still referenced" in line
    assert "--cleanup" not in line


def test_doctor_scan_parity_nested_build_symlink_and_hook_basename(tmp_path):
    """PR #242 review: the doctor reads nested build/ dirs, refuses on a symlinked wiring file, and
    keeps a hook another hook sources by basename — the same answers as the updater."""
    ws = tmp_path / "ws"
    (ws / ".claude" / "hooks").mkdir(parents=True)
    (ws / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    (ws / ".foundry").mkdir()
    (ws / ".foundry" / "hasher-ref").write_text("x", encoding="utf-8")
    (ws / "scripts" / "build").mkdir(parents=True)
    (ws / "scripts" / "build" / "ci.sh").write_text("cat ./.foundry/hasher-ref\n", encoding="utf-8")
    (ws / ".claude" / "hooks" / "zeta-exec-guard.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (ws / ".claude" / "hooks" / "live.sh").write_text('. "$(dirname "$0")/zeta-exec-guard.sh"\n', encoding="utf-8")
    line = _doctor_line(ws)
    assert "none present" in line and "2 retired path(s) still referenced" in line
    (ws / "Makefile").symlink_to("/etc/hosts")
    line = _doctor_line(ws)
    assert "none removable (reference scan incomplete: Makefile is a symlink" in line
