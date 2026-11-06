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
    ("foundry_usage.py", ["--dataset", "ds", "coverage", "--since", "2026-01-01", "--until", "2026-01-02"]),
]
# verbs that stop at their OWN post-gate validation before any gh call: the known first line they print
POST_GATE = {
    "foundry-ticket.py:claim": "claim refused: no repos{}",
    "foundry-ticket.py:lead": "no lease owned by this session",
    "foundry_usage.py:--dataset": '"agents"',
    "foundry-ticket.py:next": "no repos{} entry",
    "foundry-ticket.py:reconcile": "no repos{} entry",
    "foundry_inbox.py:pending": "no pending requests",
    "foundry_migrate.py:cutover": "REFUSED operator-not-registered",
}
IDS = [f"{s}:{a[0]}" for s, a in VERBS]


def _proj(tmp_path, manifest, raw=None):
    root = tmp_path / "ws"
    (root / ".claude").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "ds").mkdir()
    if raw is not None:
        (root / ".claude" / "foundry-project.json").write_text(raw)
    elif manifest is not None:
        (root / ".claude" / "foundry-project.json").write_text(json.dumps(manifest))
    stub = tmp_path / "bin"
    stub.mkdir()
    calls = tmp_path / "gh-calls.log"
    gh = stub / "gh"
    gh.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\nexit 1\n')
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    return root, stub, calls


def _run(root, stub, script, args):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root), PATH=f"{stub}:{os.environ['PATH']}",
               FOUNDRY_SESSION_ID="test-terminal")      # fixture: writing verbs (claim, lead renew) need a session id
    return subprocess.run([sys.executable, os.path.join(S, script), *args], capture_output=True, text=True,
                          env=env, cwd=str(root))


@pytest.mark.parametrize("script,args", VERBS, ids=IDS)
@pytest.mark.parametrize("manifest,raw", [
    (None, None), ({"schema_version": 1}, None), ({"github_scope": {"owner": "example-org"}}, None),
    ({"github_scope": "x"}, None), (None, "{not json"), (None, "[1, 2]"),
    ({"github_scope": {"owner": "a/b", "planning_repo": "planning"}}, None),
    ({"github_scope": {"owner": "--x", "planning_repo": "planning"}}, None),
    ({"github_scope": {**SCOPE, "operators": ["op_a"]}}, None),
], ids=["no-file", "no-key", "no-planning", "not-object", "invalid-json", "toplevel-list",
        "owner-slash", "owner-dashes", "operators-list"])
def test_refuses_without_scope_before_any_gh_call(tmp_path, script, args, manifest, raw):
    root, stub, calls = _proj(tmp_path, manifest, raw)
    r = _run(root, stub, script, args)
    assert r.returncode == 2, (r.stdout, r.stderr)
    lines = [ln for ln in r.stderr.splitlines() if ln.strip()]
    assert len(lines) == 1 and lines[0].startswith(TYPED + ":") and "/foundry:init" in lines[0]
    assert not calls.exists()   # zero gh invocations


@pytest.mark.parametrize("script,args", VERBS, ids=IDS)
def test_admits_with_scope(tmp_path, script, args):
    root, stub, calls = _proj(tmp_path, {"schema_version": 1, "github_scope": SCOPE})
    r = _run(root, stub, script, args)
    assert TYPED not in r.stderr and TYPED not in r.stdout, (r.stdout, r.stderr)
    # the verb got past the gate and reached its network layer (the stub gh), or produced its own output
    reached = calls.exists() and calls.read_text().strip() != ""
    marker = POST_GATE.get(script + ":" + args[0])
    assert reached or (marker and marker in r.stdout + r.stderr), (r.returncode, r.stdout, r.stderr)


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


def test_capabilities_probe_without_scope_stays_na_exit_zero(tmp_path):
    """The read-only capability probe is not a gated verb: no scope -> `n/a`, exit 0, no network."""
    root, stub, calls = _proj(tmp_path, None)
    r = _run(root, stub, "foundry_github_capabilities.py", ["--project-dir", str(root)])
    assert r.returncode == 0 and r.stdout.strip() == "n/a (no github_scope configured)"
    assert not calls.exists()


# ---- gv-11: planning_repo / repos[] reach gh arguments, so they are validated like `owner` ----

BAD_SEGMENTS = ["--delete", "-x", ".hidden", "..", ".", "a/b", "a b", "a;b", "a$(x)", "", "a\nb"]


@pytest.mark.parametrize("bad", BAD_SEGMENTS)
def test_planning_repo_must_be_a_plain_segment(tmp_path, bad):
    root, stub, calls = _proj(tmp_path, {"github_scope": {"owner": "example-org", "planning_repo": bad}})
    r = _run(root, stub, "foundry-ticket.py", ["next"])
    assert r.returncode == 2 and r.stderr.startswith(TYPED), (bad, r.stdout, r.stderr)
    assert not calls.exists()


@pytest.mark.parametrize("repos", [
    "owner/name", ["--evil/x"], ["o/-x"], ["o/.."], ["o/.x"], ["o"], ["o/n/extra"], [7], [["o/n"]], ["o/n", "o/n m"],
], ids=lambda v: repr(v)[:30])
def test_repos_entries_must_be_owner_slash_name(tmp_path, repos):
    root, stub, calls = _proj(tmp_path, {"github_scope": {**SCOPE, "repos": repos}})
    r = _run(root, stub, "foundry-ticket.py", ["next"])
    assert r.returncode == 2 and r.stderr.startswith(TYPED), (repos, r.stdout, r.stderr)
    assert not calls.exists()


@pytest.mark.parametrize("scope", [
    {**SCOPE, "planning_repo": "my.repo_1-x"},
    {**SCOPE, "repos": ["example-org/planning", "other.org/app_1"]},
    {**SCOPE, "repos": []},
], ids=["dotted-name", "slug-list", "empty-list"])
def test_well_formed_planning_repo_and_repos_are_admitted(tmp_path, scope):
    root, stub, calls = _proj(tmp_path, {"github_scope": scope})
    r = _run(root, stub, "foundry-ticket.py", ["next"])
    assert TYPED not in r.stderr + r.stdout, (r.stdout, r.stderr)
    assert "no repos{} entry" in r.stdout + r.stderr          # got past the gate to the verb's own validation


def test_pathologically_nested_manifest_is_the_typed_refusal_not_a_traceback(tmp_path):
    """A non-OSError/ValueError failure while reading the scope (RecursionError here) is still exit 2."""
    deep = "[" * 200000 + "]" * 200000
    root, stub, calls = _proj(tmp_path, None, raw='{"github_scope": ' + deep + "}")
    r = _run(root, stub, "foundry-ticket.py", ["next"])
    assert r.returncode == 2 and r.stderr.startswith(TYPED), (r.returncode, r.stderr[-300:])
    assert "Traceback" not in r.stderr and not calls.exists()


def test_gate_converts_an_unexpected_load_scope_exception_to_exit_two(tmp_path, monkeypatch, capsys):
    sys.path.insert(0, S)
    import foundry_github_capabilities as g

    def boom(_):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(g, "load_scope", boom)
    assert g.gate(tmp_path, "verb x") == 2
    assert g.SCOPE_MISSING in capsys.readouterr().err


def test_worktree_create_hook_runs_its_inline_python_isolated():
    """`-I`: no PYTHONPATH / cwd / user-site import can shadow the stdlib in the hook's inline python."""
    text = open(os.path.join(REPO_ROOT, "hooks", "foundry-worktree-create.sh"), encoding="utf-8").read()
    uses = [ln for ln in text.splitlines()
            if "python3" in ln and not ln.lstrip().startswith("#") and ("<<" in ln or "-c " in ln or "python3 - " in ln)]
    assert uses, "expected inline python invocations"
    assert all("python3 -I " in ln for ln in uses), [ln for ln in uses if "python3 -I " not in ln]
