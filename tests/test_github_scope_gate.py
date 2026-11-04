"""tests/test_github_scope_gate.py - gv-02: one operating mode; the GitHub-backed verbs need `github_scope`.

Each GitHub-backed verb refuses with the single typed line `github-scope-missing` (exit 2) when
`.claude/foundry-project.json` lacks a well-formed `github_scope`, BEFORE any network call (a stub `gh` on PATH
records every invocation; it must stay empty), and admits (does not emit the refusal) when it is present.
The base ticket verbs stay open without a scope. No real `gh` is ever spawned.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys

import pytest

from conftest import REPO_ROOT

S = os.path.join(REPO_ROOT, "scripts")
SCOPE = {"owner": "example-org", "planning_repo": "planning"}
TYPED = "github-scope-missing"

VERBS = [
    ("foundry-ticket.py", ["next"]),
    ("foundry-ticket.py", ["claim", "7"]),
    ("foundry-ticket.py", ["reconcile"]),
    ("foundry-ticket.py", ["lead", "renew"]),
    ("foundry_inbox.py", ["scan"]),
    ("foundry_inbox.py", ["pending"]),
    ("foundry_leader_board.py", ["--as-of", "2026-10-01T00:00:00Z"]),
    ("foundry_migrate.py", ["cutover", "record", "--operator", "op_a", "--scope", "x", "--acknowledge-empty"]),
    ("foundry_deck_events.py", ["parse", "o/r#1", "--as-of", "2026-01-01T00:00:00Z"]),
    ("foundry_usage.py", ["--dataset", "/nonexistent-ds", "coverage", "--since", "2026-01-01", "--until", "2026-01-02"]),
]
IDS = [f"{s}:{a[0]}" for s, a in VERBS]


def _proj(tmp_path, manifest):
    root = tmp_path / "ws"
    (root / ".claude").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    if manifest is not None:
        (root / ".claude" / "foundry-project.json").write_text(json.dumps(manifest))
    stub = tmp_path / "bin"
    stub.mkdir()
    calls = tmp_path / "gh-calls.log"
    gh = stub / "gh"
    gh.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\nexit 1\n')
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    return root, stub, calls


def _run(root, stub, script, args):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root), PATH=f"{stub}:{os.environ['PATH']}")
    return subprocess.run([sys.executable, os.path.join(S, script), *args], capture_output=True, text=True,
                          env=env, cwd=str(root))


@pytest.mark.parametrize("script,args", VERBS, ids=IDS)
@pytest.mark.parametrize("manifest", [None, {"schema_version": 1}, {"github_scope": {"owner": "example-org"}},
                                      {"github_scope": "x"}], ids=["no-file", "no-key", "no-planning", "not-object"])
def test_refuses_without_scope_before_any_gh_call(tmp_path, script, args, manifest):
    root, stub, calls = _proj(tmp_path, manifest)
    r = _run(root, stub, script, args)
    assert r.returncode == 2, (r.stdout, r.stderr)
    lines = [ln for ln in r.stderr.splitlines() if ln.strip()]
    assert len(lines) == 1 and lines[0].startswith(TYPED + ":") and "/foundry:init" in lines[0]
    assert not calls.exists()   # zero gh invocations


@pytest.mark.parametrize("script,args", VERBS, ids=IDS)
def test_admits_with_scope(tmp_path, script, args):
    root, stub, _ = _proj(tmp_path, {"schema_version": 1, "github_scope": SCOPE})
    r = _run(root, stub, script, args)
    assert TYPED not in r.stderr and TYPED not in r.stdout, (r.stdout, r.stderr)


@pytest.mark.parametrize("verb", ["status", "clear"])
def test_base_ticket_verbs_stay_open_without_scope(tmp_path, verb):
    root, stub, calls = _proj(tmp_path, None)
    r = _run(root, stub, "foundry-ticket.py", [verb])
    assert TYPED not in r.stderr + r.stdout
    assert not calls.exists()


def test_usage_bind_hook_is_a_noop_without_scope(tmp_path):
    root, stub, _ = _proj(tmp_path, {"usage": {"dataset": str(tmp_path / "ds")}})
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    r = subprocess.run([sys.executable, os.path.join(REPO_ROOT, "hooks", "foundry-usage-bind.py")],
                       input=json.dumps({"cwd": str(root), "session_id": "s1"}), capture_output=True, text=True,
                       env=env)
    assert r.returncode == 0 and not (tmp_path / "ds").exists()


@pytest.mark.parametrize("manifest,want", [(None, "missing — run /foundry:init"), ({"github_scope": SCOPE}, "ok")])
def test_doctor_github_scope_line(tmp_path, manifest, want):
    root, stub, _ = _proj(tmp_path, manifest)
    r = _run(root, stub, "foundry-doctor.py", [])
    row = [ln for ln in r.stdout.splitlines() if "github-scope:" in ln]
    assert len(row) == 1 and row[0].split("github-scope:", 1)[1].strip().endswith(want), r.stdout
    assert "[RED]" not in row[0] and "✗" not in row[0]
