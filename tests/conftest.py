"""Shared fixtures for the pytest suite.

This suite replaces the ~30 converted `scripts/foundry_checks/*.py` drop-in doctor
selftests: each `tests/test_<module>.py` ports the REAL fixtures/behaviors those
selftests drove — importing the shipped `scripts/foundry_*.py` modules directly and
asserting on their COMPUTED output over throwaway temp fixtures — never the CLI
scaffolding (arg parsing, sentinel-token printing, doctor auto-discovery) that
existed only to make each check independently drop-in-discoverable.

Nothing here mutates the real repo tree, the real `.foundry/` state, or any live
git config. Every fixture-driving test uses `tmp_path` (pytest's per-test throwaway
directory) or an explicit `plugin_root=` / `root=` / `project_dir=` override that the
shipped modules already accept for exactly this reason.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import shutil
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")

# The shipped scripts/ modules import each other by bare module name (e.g. `import
# foundry_authz`), so scripts/ must be on sys.path exactly like the doctor's drop-in
# checks put it there. Do this once, at collection time, for every test module.
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import pytest


def load_module(relpath, modname=None):
    """Load a shipped scripts/ module by repo-relative path (mirrors the drop-in
    checks' own `_load()` helper). `relpath` is relative to the repo root, e.g.
    "scripts/foundry_id_apply.py". Registers under sys.modules so the module's own
    forward-ref annotations resolve."""
    path = os.path.join(REPO_ROOT, relpath)
    name = modname or os.path.splitext(os.path.basename(relpath))[0].replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _functional_plugin_root(base, floor_doc=None, malformed_floor=False):
    """A LIGHT (not full-repo-copy) plugin tree with everything the doctor's probes need to pass
    trivially, plus the real scripts/ tree (so foundry_authz / foundry_control_plane /
    foundry_permission_floor all import), for tests that must drive the doctor CLI end-to-end.

    RELOCATED here (from the deleted tests/test_permission_floor_check.py) by subtraction-wave
    (AC-SUB-1c, autonomy-continuation R4) so tests/test_agent_teams_enablement.py's cross-import
    keeps a stable home rather than reaching into a sibling test module."""
    base = str(base)
    os.makedirs(os.path.join(base, ".claude-plugin"), exist_ok=True)
    with open(os.path.join(base, ".claude-plugin", "plugin.json"), "w", encoding="utf-8") as f:
        json.dump({"name": "foundry", "version": "0.0.0-test"}, f)
    os.makedirs(os.path.join(base, "hooks"), exist_ok=True)
    with open(os.path.join(base, "hooks", "hooks.json"), "w", encoding="utf-8") as f:
        json.dump({}, f)
    shutil.copytree(os.path.join(REPO_ROOT, "scripts"), os.path.join(base, "scripts"))
    os.makedirs(os.path.join(base, "docs"), exist_ok=True)
    if malformed_floor:
        with open(os.path.join(base, "docs", "permission-floor.json"), "w", encoding="utf-8") as f:
            f.write("{ not valid json at all")
    elif floor_doc is not None:
        with open(os.path.join(base, "docs", "permission-floor.json"), "w", encoding="utf-8") as f:
            json.dump(floor_doc, f)
    else:
        shutil.copyfile(
            os.path.join(REPO_ROOT, "docs", "permission-floor.json"),
            os.path.join(base, "docs", "permission-floor.json"),
        )
    return base


@pytest.fixture()
def repo_root():
    return REPO_ROOT


@pytest.fixture()
def plugin_root(tmp_path, monkeypatch):
    """A throwaway CLAUDE_PLUGIN_ROOT so tests that resolve "the live shipped
    artifact" can point at a controlled copy instead of touching the real plugin
    checkout in place. Tests that want to assert over the REAL shipped tree just use
    `repo_root` instead and never call this fixture."""
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture()
def project_dir(tmp_path, monkeypatch):
    """A throwaway CLAUDE_PROJECT_DIR for tests exercising the adopter-workspace side
    (e.g. `.foundry/` state, stack-profile locks, learnings buffers)."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Never let a real operator's ambient CLAUDE_PLUGIN_ROOT/CLAUDE_PROJECT_DIR leak
    into a test that doesn't explicitly request one via the fixtures above."""
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
