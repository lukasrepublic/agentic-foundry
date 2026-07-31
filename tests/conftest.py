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
import os
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
