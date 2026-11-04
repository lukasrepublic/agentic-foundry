"""tests/test_operating_mode.py - gn-17: single-operator (default) vs team operating mode.

No network: every `gh` in a subprocess is a stub on PATH that appends its argv to a log, and the
capability probes run through an injected runner.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys

import pytest

from conftest import REPO_ROOT

SCRIPTS = os.path.join(REPO_ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
import foundry_github_capabilities as ghc  # noqa: E402
import foundry_operating_mode as om  # noqa: E402

CLI = os.path.join(SCRIPTS, "foundry_operating_mode.py")
HOOK = os.path.join(REPO_ROOT, "hooks", "foundry-worktree-create.sh")
USAGE_HOOK = os.path.join(REPO_ROOT, "hooks", "foundry-usage-bind.py")
SCOPE = {"owner": "example-org", "planning_repo": "planning", "project_number": 1, "operators": {}}


def _write(ws, doc):
    (ws / ".claude").mkdir(exist_ok=True)
    p = ws / ".claude" / "foundry-project.json"
    p.write_text(doc if isinstance(doc, str) else json.dumps(doc, indent=2) + "\n")
    return p


def _plugin(tmp_path, version):
    root = tmp_path / f"plugin-{version}"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "foundry", "version": version}))
    return str(root)


class Gh:
    """A runner for the capability probes. mode: ok | none | partial."""

    def __init__(self, mode="ok"):
        self.mode, self.calls = mode, []

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        R = ghc.Result
        if self.mode == "none":
            return R(rc=1, stderr="HTTP 401: bad credentials")
        if argv[:3] == ["gh", "api", "graphql"]:
            if self.mode == "partial":
                return R(rc=1, stderr="missing required scopes [read:project]")
            return R(rc=0, stdout=json.dumps({"data": {"repositoryOwner": {"projectV2": {"id": "x", "title": "q"}}}}))
        if argv[:2] == ["gh", "search"]:
            return R(rc=0, stdout="[]")
        if argv[:2] == ["gh", "api"] and any("notifications" in a for a in argv):
            return R(rc=0, stdout="HTTP/2.0 200 OK\n\n[]")
        if argv[:2] == ["gh", "api"] and "-i" in argv:
            return R(rc=0, stdout="HTTP/2.0 200 OK\nX-OAuth-Scopes: repo, read:project\n\n{}")
        return R(rc=0, stdout="{}")


@pytest.fixture
def stub_env(tmp_path):
    """PATH with a `gh` that records argv and fails; returns (env, logfile)."""
    b = tmp_path / "bin"
    b.mkdir()
    log = tmp_path / "gh.log"
    g = b / "gh"
    g.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nexit 1\n')
    g.chmod(g.stat().st_mode | stat.S_IXUSR)
    env = dict(os.environ, PATH=f"{b}:{os.environ['PATH']}")
    return env, log


def _team_ws(tmp_path, **extra):
    ws = tmp_path / "ws"
    ws.mkdir()
    _write(ws, {"schema_version": 1, "operating_mode": "team", "github_scope": SCOPE, **extra})
    return ws


# --------------------------------------------------------------------------------- resolution

def test_absent_resolves_single_operator_default(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    assert om.resolve(str(ws)).name == "single-operator"          # no file at all
    _write(ws, {"schema_version": 1})
    m = om.resolve(str(ws))
    assert (m.name, m.source, m.github_scope_present) == ("single-operator", "default", False)
    assert m.line == "single-operator (default)"


@pytest.mark.parametrize("val", ["Team", "", "solo", 1, None, ["team"], True])
def test_malformed_value_resolves_single_operator_and_names_it(tmp_path, val):
    ws = tmp_path / "ws"
    ws.mkdir()
    _write(ws, {"operating_mode": val, "github_scope": SCOPE})
    m = om.resolve(str(ws))
    assert m.name == "single-operator" and m.source == "malformed"
    assert m.line.startswith("single-operator (malformed value ") and m.reason in m.line


def test_explicit_and_team_and_team_without_scope(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _write(ws, {"operating_mode": "single-operator"})
    assert om.resolve(str(ws)).line == "single-operator (explicit)"
    _write(ws, {"operating_mode": "team", "github_scope": SCOPE})
    m = om.resolve(str(ws))
    assert (m.name, m.line, m.github_scope_present) == ("team", "team", True)
    _write(ws, {"operating_mode": "team"})
    m = om.resolve(str(ws))
    assert m.name == "single-operator" and "github_scope" in m.reason
    _write(ws, "{not json")
    assert om.resolve(str(ws)).name == "single-operator"


def test_team_with_owner_only_scope_is_single_operator_naming_the_missing_field(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _write(ws, {"operating_mode": "team", "github_scope": {"owner": "o"}})
    m = om.resolve(str(ws))
    assert m.name == "single-operator" and m.source == "team-without-scope"
    assert "planning_repo" in m.reason
    _write(ws, {"operating_mode": "team", "github_scope": {"planning_repo": "p"}})
    assert "owner" in om.resolve(str(ws)).reason


def test_resolve_uses_the_capabilities_scope_predicate(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    for scope in ({"owner": "o"}, {"owner": "o", "planning_repo": "p"}, {}, "x", {"owner": "", "planning_repo": "p"}):
        _write(ws, {"operating_mode": "team", "github_scope": scope})
        assert (om.resolve(str(ws)).name == "team") == (ghc.load_scope(str(ws))[1] is not None)


def test_require_team_text(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _write(ws, {})
    assert om.require_team(str(ws), "foundry_inbox.py scan") == (
        'foundry_inbox.py scan is a team-mode verb; this workspace is in single-operator mode (default). '
        'Enable it with: python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_operating_mode.py" enable-team')
    _write(ws, {"operating_mode": "team", "github_scope": SCOPE})
    assert om.require_team(str(ws), "x") is None


def test_schema_declares_operating_mode():
    sch = json.load(open(os.path.join(REPO_ROOT, "schema", "foundry-project.schema.json")))
    assert sch["properties"]["operating_mode"]["enum"] == ["single-operator", "team"]


# --------------------------------------------------------------------------------- status CLI

def test_status_cli_text_and_json(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _write(ws, {"schema_version": 1, "github_scope": SCOPE})
    old, new = _plugin(tmp_path, "1.18.5"), _plugin(tmp_path, "2.0.0")
    r = subprocess.run([sys.executable, CLI, "--project-dir", str(ws), "--plugin-root", old, "status"],
                       capture_output=True, text=True)
    assert r.returncode == 0
    assert "mode: single-operator" in r.stdout and "source: default" in r.stdout
    assert "github_scope present: yes" in r.stdout and "plugin version: 1.18.5" in r.stdout
    assert "not eligible" in r.stdout and "1.18.5" in r.stdout
    r = subprocess.run([sys.executable, CLI, "--project-dir", str(ws), "--plugin-root", new, "status", "--json"],
                       capture_output=True, text=True)
    doc = json.loads(r.stdout)
    assert doc["mode"] == "single-operator" and doc["enable_eligible"] is True
    assert doc["plugin_version"] == "2.0.0" and doc["github_scope_present"] is True


# --------------------------------------------------------------------------------- enable-team

def _enable(ws, plugin, runner=None, capsys=None):
    return om.main(["--project-dir", str(ws), "--plugin-root", plugin, "enable-team"], runner=runner or Gh())


def test_enable_refuses_below_2_0_and_names_the_version(tmp_path, capsys):
    ws = tmp_path / "ws"
    ws.mkdir()
    p = _write(ws, {"schema_version": 1, "github_scope": SCOPE})
    before = p.read_bytes()
    gh = Gh()
    assert _enable(ws, _plugin(tmp_path, "1.18.5"), gh) == 2
    err = capsys.readouterr().err
    assert "1.18.5" in err and "2.0.0" in err and "npx" in err
    assert p.read_bytes() == before and gh.calls == []            # no write, no probe


def test_enable_refuses_unreadable_plugin_version(tmp_path, capsys):
    ws = tmp_path / "ws"
    ws.mkdir()
    p = _write(ws, {"github_scope": SCOPE})
    before = p.read_bytes()
    assert _enable(ws, str(tmp_path / "nowhere")) == 2
    assert p.read_bytes() == before


@pytest.mark.parametrize("scope", [None, "x", {}, {"owner": "o"}, {"planning_repo": "p"},
                                   {"owner": "o", "planning_repo": "p", "project_number": "7"}])
def test_enable_refuses_absent_or_malformed_scope(tmp_path, capsys, scope):
    ws = tmp_path / "ws"
    ws.mkdir()
    doc = {"schema_version": 1}
    if scope is not None:
        doc["github_scope"] = scope
    p = _write(ws, doc)
    before = p.read_bytes()
    gh = Gh()
    assert _enable(ws, _plugin(tmp_path, "2.0.0"), gh) == 2
    assert "github_scope" in capsys.readouterr().err
    assert p.read_bytes() == before and gh.calls == []


def test_enable_refuses_when_capability_is_none(tmp_path, capsys):
    ws = tmp_path / "ws"
    ws.mkdir()
    p = _write(ws, {"github_scope": SCOPE})
    before = p.read_bytes()
    assert _enable(ws, _plugin(tmp_path, "2.0.0"), Gh("none")) == 2
    assert "capability" in capsys.readouterr().err
    assert p.read_bytes() == before


def test_enable_refuses_when_gh_is_absent(tmp_path, capsys):
    ws = tmp_path / "ws"
    ws.mkdir()
    p = _write(ws, {"github_scope": SCOPE})
    before = p.read_bytes()
    missing = lambda argv, timeout: ghc.Result(rc=127, missing=True)  # noqa: E731
    assert _enable(ws, _plugin(tmp_path, "2.0.0"), missing) == 2
    assert p.read_bytes() == before


def test_enable_partial_passes_and_lists_the_uncovered(tmp_path, capsys):
    ws = tmp_path / "ws"
    ws.mkdir()
    _write(ws, {"github_scope": SCOPE})
    assert _enable(ws, _plugin(tmp_path, "2.0.0"), Gh("partial")) == 0
    out = capsys.readouterr().out
    assert "partial" in out and "project" in out
    assert om.resolve(str(ws)).name == "team"


def test_enable_success_preserves_keys_order_and_is_idempotent(tmp_path, capsys):
    ws = tmp_path / "ws"
    ws.mkdir()
    orig = {"schema_version": 1, "zeta": {"b": [1, 2], "a": "é"}, "project": {"name": "demo"},
            "github_scope": SCOPE, "alpha": True}
    p = _write(ws, orig)
    plugin = _plugin(tmp_path, "2.0.0")
    assert _enable(ws, plugin) == 0
    assert "team mode enabled" in capsys.readouterr().out
    new = json.loads(p.read_text())
    assert list(new) == list(orig) + ["operating_mode"] and new["operating_mode"] == "team"
    assert {k: new[k] for k in orig} == orig
    assert p.read_text().endswith("\n") and p.read_text().startswith('{\n  "schema_version"')
    assert sorted(os.listdir(ws / ".claude")) == ["foundry-project.json"]     # no temp left behind
    first = p.read_bytes()
    assert _enable(ws, plugin) == 0 and p.read_bytes() == first               # idempotent
    r = subprocess.run([sys.executable, CLI, "--project-dir", str(ws), "--plugin-root", plugin, "status"],
                       capture_output=True, text=True)
    assert "mode: team" in r.stdout


def test_enable_replaces_a_malformed_value_in_place(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    p = _write(ws, {"operating_mode": "solo", "schema_version": 1, "github_scope": SCOPE})
    assert _enable(ws, _plugin(tmp_path, "2.0.0")) == 0
    assert list(json.loads(p.read_text()))[0] == "operating_mode"
    assert om.resolve(str(ws)).name == "team"


def test_default_plugin_root_is_this_repo_and_refuses_while_below_2_0(tmp_path):
    """No --plugin-root and no CLAUDE_PLUGIN_ROOT: the script's own repo is read (the tree is < 2.0)."""
    ws = tmp_path / "ws"
    ws.mkdir()
    p = _write(ws, {"schema_version": 1, "github_scope": SCOPE})
    before = p.read_bytes()
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_ROOT"}
    real = json.load(open(os.path.join(REPO_ROOT, ".claude-plugin", "plugin.json")))["version"]
    r = subprocess.run([sys.executable, CLI, "--project-dir", str(ws), "enable-team"],
                       capture_output=True, text=True, env=env)
    if om.parse_version(real) < om.MIN_TEAM_VERSION:
        assert r.returncode == 2 and real in r.stderr
        assert p.read_bytes() == before


def test_plugin_root_precedence_flag_over_env_over_script_location(tmp_path, monkeypatch):
    flag, env = _plugin(tmp_path, "3.0.0"), _plugin(tmp_path, "2.5.0")
    real = json.load(open(os.path.join(REPO_ROOT, ".claude-plugin", "plugin.json")))["version"]
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", env)
    assert om.plugin_version(flag)[0] == "3.0.0"          # flag beats env
    assert om.plugin_version(None)[0] == "2.5.0"          # env beats the script location
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT")
    assert om.plugin_version(None)[0] == real             # fallback: this script's repo root


@pytest.mark.parametrize("ver", ["2.0.0-rc.1", "2.0.0.dev", "2.0.0+build", "2.0", "2.0.0-0", "v2.0.0x"])
def test_prerelease_or_suffixed_version_never_enables(tmp_path, capsys, ver):
    ws = tmp_path / "ws"
    ws.mkdir()
    p = _write(ws, {"github_scope": SCOPE})
    before = p.read_bytes()
    gh = Gh()
    assert _enable(ws, _plugin(tmp_path, ver), gh) == 2
    assert p.read_bytes() == before and gh.calls == []
    assert om.parse_version(ver) is None


def test_plain_and_v_prefixed_release_versions_parse():
    assert om.parse_version("2.0.0") == (2, 0, 0) and om.parse_version("v10.1.2") == (10, 1, 2)


def test_enable_on_a_team_workspace_is_a_noop_with_no_network(tmp_path, capsys):
    ws = _team_ws(tmp_path)
    p = ws / ".claude" / "foundry-project.json"
    before = p.read_bytes()
    gh = Gh()
    assert _enable(ws, _plugin(tmp_path, "2.0.0"), gh) == 0
    assert "already in team mode" in capsys.readouterr().out
    assert gh.calls == [] and p.read_bytes() == before


def test_enable_four_space_input_is_reserialised_with_keys_preserved(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    orig = {"schema_version": 1, "z": {"k": [1, {"a": None}]}, "github_scope": SCOPE, "a": "x"}
    p = _write(ws, json.dumps(orig, indent=4) + "\n")
    assert _enable(ws, _plugin(tmp_path, "2.0.0")) == 0
    new = json.loads(p.read_text())
    assert list(new) == list(orig) + ["operating_mode"] and {k: new[k] for k in orig} == orig
    assert p.read_text().startswith('{\n  "schema_version"')              # 2-space, not the input's 4


def test_enable_through_a_symlink_rewrites_the_target(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".claude").mkdir(parents=True)
    real = tmp_path / "shared" / "project.json"
    real.parent.mkdir()
    real.write_text(json.dumps({"schema_version": 1, "github_scope": SCOPE}))
    link = ws / ".claude" / "foundry-project.json"
    link.symlink_to(real)
    assert _enable(ws, _plugin(tmp_path, "2.0.0")) == 0
    assert link.is_symlink() and os.path.realpath(link) == str(real.resolve())
    assert json.loads(real.read_text())["operating_mode"] == "team"
    assert sorted(os.listdir(real.parent)) == ["project.json"]               # no stray temp file
    assert om.main(["--project-dir", str(ws), "disable-team"]) == 0
    assert link.is_symlink() and json.loads(real.read_text())["operating_mode"] == "single-operator"


# --------------------------------------------------------------------------------- disable-team

def test_disable_keeps_scope_and_runtime_files_then_team_verbs_refuse(tmp_path, capsys, stub_env):
    env, log = stub_env
    ws = _team_ws(tmp_path, github_extra={"keep": 1})
    (ws / ".foundry").mkdir()
    (ws / ".foundry" / "history.jsonl").write_text("x\n")
    p = ws / ".claude" / "foundry-project.json"
    assert om.main(["--project-dir", str(ws), "disable-team"]) == 0
    doc = json.loads(p.read_text())
    assert doc["operating_mode"] == "single-operator" and doc["github_scope"] == SCOPE
    assert doc["github_extra"] == {"keep": 1}
    assert (ws / ".foundry" / "history.jsonl").read_text() == "x\n"
    before = p.read_bytes()
    assert om.main(["--project-dir", str(ws), "disable-team"]) == 0 and p.read_bytes() == before
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "foundry_inbox.py"), "--project-dir", str(ws), "check"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 2 and "single-operator mode" in r.stderr
    assert not log.exists()


def test_disable_with_no_manifest_is_a_noop(tmp_path):
    assert om.main(["--project-dir", str(tmp_path), "disable-team"]) == 0
    assert not (tmp_path / ".claude").exists()


def test_disable_on_unparseable_manifest_is_an_error(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    p = _write(ws, "{nope")
    assert om.main(["--project-dir", str(ws), "disable-team"]) == 1
    assert p.read_text() == "{nope"


# --------------------------------------------------------------------------------- the gate

def _run(script, args, ws, env, **kw):
    e = dict(env, CLAUDE_PROJECT_DIR=str(ws))
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, script), *args], capture_output=True,
                          text=True, env=e, cwd=str(ws), **kw)


@pytest.mark.parametrize("script,args,verb", [
    ("foundry-ticket.py", ["next"], "foundry-ticket.py next"),
    ("foundry-ticket.py", ["claim", "7"], "foundry-ticket.py claim"),
    ("foundry-ticket.py", ["reconcile"], "foundry-ticket.py reconcile"),
    ("foundry-ticket.py", ["lead", "status"], "foundry-ticket.py lead"),
    ("foundry_inbox.py", ["scan"], "foundry_inbox.py scan"),
    ("foundry_inbox.py", ["pending"], "foundry_inbox.py pending"),
    ("foundry_leader_board.py", [], "foundry_leader_board.py"),
    ("foundry_deck_events.py", ["parse", "o/r#1", "--as-of", "2026-11-01T00:00:00Z"], "foundry_deck_events.py parse"),
    ("foundry_deck_events.py", ["emit", "o/r#1", "ready"], "foundry_deck_events.py emit"),
    ("foundry_usage.py", ["--dataset", "ds", "attribute"], "foundry_usage.py attribute"),
    ("foundry_usage.py", ["--dataset", "ds", "bind", "--session", "s", "--issue", "1"], "foundry_usage.py bind"),
])
def test_team_verbs_refuse_in_single_operator_mode_before_any_network_or_write(tmp_path, stub_env, script, args, verb):
    env, log = stub_env
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    _write(ws, {"schema_version": 1, "github_scope": SCOPE})           # scope present, mode default
    snap = sorted(str(p.relative_to(ws)) for p in ws.rglob("*") if ".git" not in p.parts)
    r = _run(script, args, ws, env)
    assert r.returncode == 2, r.stderr
    assert r.stderr.strip() == om.refusal_text(verb) and r.stdout == ""
    assert not log.exists() or log.read_text() == ""                   # the stub gh was never invoked
    assert sorted(str(p.relative_to(ws)) for p in ws.rglob("*") if ".git" not in p.parts) == snap


def test_ticket_base_verbs_are_not_gated(tmp_path, stub_env):
    env, _ = stub_env
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    _write(ws, {"schema_version": 1})
    r = _run("foundry-ticket.py", ["status"], ws, env)
    assert "team-mode verb" not in r.stderr


def test_gated_scripts_run_past_the_gate_in_team_mode(tmp_path, stub_env):
    env, _ = stub_env
    ws = _team_ws(tmp_path)
    r = _run("foundry_inbox.py", ["pending"], ws, env)
    assert r.returncode == 0 and "team-mode verb" not in r.stderr


def test_capabilities_cli_and_advisory_are_na_in_single_operator_mode(tmp_path, stub_env):
    env, log = stub_env
    ws = tmp_path / "ws"
    ws.mkdir()
    _write(ws, {"github_scope": SCOPE})
    r = _run("foundry_github_capabilities.py", [], ws, env)
    assert r.returncode == 0 and r.stdout.strip() == "n/a (single-operator mode)"
    r = _run("foundry_github_capabilities.py", ["--json"], ws, env)
    assert json.loads(r.stdout)["coverage"] == "n/a"
    assert ghc.advisory_line(str(ws), Gh()) == "n/a (single-operator mode)"
    assert not log.exists()


# --------------------------------------------------------------------------------- doctor row

def _doctor_module():
    spec = importlib.util.spec_from_file_location("foundry_doctor_gn17", os.path.join(SCRIPTS, "foundry-doctor.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("doc,line", [
    ({}, "single-operator (default)"),
    ({"operating_mode": "single-operator"}, "single-operator (explicit)"),
    ({"operating_mode": "team", "github_scope": SCOPE}, "team"),
    ({"operating_mode": "bogus"}, 'single-operator (malformed value "bogus")'),
])
def test_doctor_operating_mode_row(tmp_path, doc, line):
    ws = tmp_path / "ws"
    ws.mkdir()
    _write(ws, doc)
    assert _doctor_module().check_operating_mode(str(ws)) == (True, line)


def test_doctor_stays_green_in_both_modes(tmp_path, stub_env):
    env, _ = stub_env
    for n, doc in enumerate(({}, {"operating_mode": "team", "github_scope": SCOPE})):
        ws = tmp_path / f"ws{n}"
        ws.mkdir()
        _write(ws, doc)
        (ws / ".claude" / "foundry-operators.json").write_text(
            json.dumps({"schema_version": 1, "operators": {"op_demo": {"name": "D"}}}))
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "foundry-doctor.py")], capture_output=True,
                           text=True, env=dict(env, CLAUDE_PROJECT_DIR=str(ws)), cwd=str(ws))
        assert r.stdout.strip().splitlines()[-1] == "DOCTOR-GREEN", r.stdout
        assert r.returncode == 0
        assert len([ln for ln in r.stdout.splitlines() if "operating-mode:" in ln]) == 1


def test_doctor_skill_lists_the_advisory_row():
    text = open(os.path.join(REPO_ROOT, "skills", "doctor", "SKILL.md")).read()
    assert "**`operating-mode`**" in text


# --------------------------------------------------------------------------------- hooks

def test_usage_bind_hook_is_a_noop_unless_team(tmp_path):
    def run(ws):
        payload = json.dumps({"session_id": "s1", "agent_id": "a1", "cwd": str(ws)})
        return subprocess.run([sys.executable, USAGE_HOOK], input=payload, capture_output=True, text=True,
                              env=dict(os.environ, CLAUDE_PROJECT_DIR=str(ws)))

    ds = tmp_path / "ds"
    for name, doc, expect in (("solo", {"usage": {"dataset": str(ds)}}, False),
                              ("team", {"operating_mode": "team", "github_scope": SCOPE,
                                        "usage": {"dataset": str(ds)}}, True)):
        ws = tmp_path / name
        ws.mkdir()
        _write(ws, doc)
        assert run(ws).returncode == 0
        assert (ds / "bindings.jsonl").exists() is expect


def _hook_ws(tmp_path, mode_doc):
    ws = tmp_path / "hws"
    (ws / ".claude").mkdir(parents=True)
    (ws / ".foundry" / "dispatch-queue").mkdir(parents=True)
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    for d in (ws, ws / "app"):
        d.mkdir(exist_ok=True)
        subprocess.run(["git", "init", "-q", str(d)], check=True)
        subprocess.run(git + ["-C", str(d), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    _write(ws, {"schema_version": 1, "repos": {"app": {"path": "app"}}, **mode_doc})
    (ws / ".claude" / "foundry-operators.json").write_text(
        json.dumps({"schema_version": 1, "operators": {"op_demo": {"name": "D"}}}))
    return ws


def _assignment():
    return {"issue_url": "https://github.com/o/r/issues/7", "issue_number": 7, "repo_key": "app",
            "operator": "op_demo", "lane": "charter",
            "acceptance_revision": {"body_sha256": "ab" * 32,
                                    "documents": [{"url": "https://github.com/o/r/blob/main/d.md",
                                                   "revision": "c0ffee"}]}}


def _hook(ws, **extra):
    m = {"target_repo": "app", "task": "t1", "agent": "app-engineer", "dispatcher_session_id": "s1"}
    m.update(extra)
    (ws / ".foundry" / "dispatch-queue" / "q.json").write_text(json.dumps(m))
    r = subprocess.run(["bash", HOOK], input="{}", capture_output=True, text=True,
                       env=dict(os.environ, CLAUDE_PROJECT_DIR=str(ws)))
    log = ws / ".foundry" / "dispatch.log"
    return r, (log.read_text() if log.exists() else "")


def test_hook_refuses_issue_lane_in_single_operator_mode(tmp_path):
    ws = _hook_ws(tmp_path, {})
    r, log = _hook(ws, assignment=_assignment())
    assert r.returncode != 0 and r.stdout == ""   # fail-closed, same family as the other gn-11 refusals
    assert "single-operator-mode" in log and "DROPPED-SINGLE-OPERATOR-MODE" in log
    assert not (ws / ".worktrees").exists()


def test_hook_issue_lane_proceeds_in_team_mode(tmp_path):
    ws = _hook_ws(tmp_path, {"operating_mode": "team", "github_scope": SCOPE})
    r, log = _hook(ws, assignment=_assignment())
    assert r.returncode == 0, r.stderr
    assert "single-operator-mode" not in log


def _frozen_contract(ws):
    import foundry_contract as fc
    (ws / "spec.md").write_text("# demo spec\n\n<!-- normative -->\ndemo body\n<!-- /normative -->\n")
    sh = fc.spec_sha256(str(ws / "spec.md"))
    proper = ("spec_ref: spec.md\nspec_sha256: %s\ntarget_repo: app\nscope:\n  allowed_paths:\n    - \"demo/**\"\n"
              "checkpoints:\n  - ac_id: \"AC-DEMO-1\"\n    surface: \"cli:demo\"\n    locator: \"true\"\n"
              "    expect: {op: \"non-empty\", baseline: \"pre-change\"}\n") % sh
    pb = proper.encode()
    trailer = ("authorized:\n  operator_id: op_test\n  authorized_at: \"2026-01-01T00:00:00Z\"\n  auth_seq: 1\n"
               "  spec_sha256: %s\n  contract_sha256: %s\n  merge_autonomy_mode: lean\n") % (
                   sh, fc.contract_sha256_bytes(pb))
    (ws / "contract.yaml").write_bytes(fc.freeze_proper_and_trailer(pb, trailer))


def _contract_run(tmp_path, name, mode_doc, with_assignment):
    ws = _hook_ws(tmp_path / name, mode_doc) if (tmp_path / name).mkdir() is None else None
    _frozen_contract(ws)
    extra = {"assignment": _assignment()} if with_assignment else {}
    r, log = _hook(ws, contract_ref="contract.yaml", **extra)
    wsp = str(ws.resolve())
    lines = [ln.split(" ", 1)[1].replace(wsp, "<ws>") for ln in log.splitlines()]
    files = sorted(str(p.relative_to(ws)) for p in (ws / ".worktrees").rglob("*") if ".git" not in p.parts) \
        if (ws / ".worktrees").exists() else []
    binding = None
    if r.returncode == 0:
        gd = subprocess.check_output(["git", "-C", r.stdout.strip(), "rev-parse", "--absolute-git-dir"], text=True).strip()
        binding = os.path.isfile(os.path.join(gd, "foundry-assignment.json"))
    return r.returncode, lines, files, binding, (r.stdout.strip() != "")


@pytest.mark.parametrize("with_assignment", [False, True])
def test_hook_contract_lane_success_identical_in_both_modes(tmp_path, with_assignment):
    solo = _contract_run(tmp_path, "solo", {}, with_assignment)
    team = _contract_run(tmp_path, "team", {"operating_mode": "team", "github_scope": SCOPE}, with_assignment)
    assert solo[0] == 0, solo                                  # a contract-lane SUCCESS, not a refusal
    assert solo == team                                        # exit, log lines, files written, binding
    assert solo[3] is with_assignment                          # contract+assignment still writes the gn-11 binding
    assert not any("single-operator-mode" in ln for ln in solo[1])


def test_hook_contract_lane_failure_identical_in_both_modes(tmp_path):
    for name, doc in (("solo", {}), ("team", {"operating_mode": "team", "github_scope": SCOPE})):
        ws = _hook_ws(tmp_path / name, doc) if (tmp_path / name).mkdir() is None else None
        (ws / "spec.md").write_text("# s\n\n<!-- normative -->\nx\n<!-- /normative -->\n")
        (ws / "contract.yaml").write_text("spec_ref: spec.md\ntarget_repo: app\n")
        r, log = _hook(ws, contract_ref="contract.yaml", assignment=_assignment())
        assert r.returncode != 0 and r.stdout == ""
        assert "PREFLIGHT-FAIL" in log and "single-operator-mode" not in log


def test_hook_logs_the_resolver_failure_cause(tmp_path):
    ws = _hook_ws(tmp_path, {})
    # break the resolver import in a COPY of the plugin tree so the real one is untouched
    m = {"target_repo": "app", "task": "t1", "agent": "app-engineer", "dispatcher_session_id": "s1",
         "assignment": _assignment()}
    (ws / ".foundry" / "dispatch-queue" / "q.json").write_text(json.dumps(m))
    # the hook inserts the real scripts dir at sys.path[0]; use a copy of the hook tree instead
    import shutil
    tree = tmp_path / "plugin"
    shutil.copytree(os.path.join(REPO_ROOT, "hooks"), tree / "hooks")
    shutil.copytree(SCRIPTS, tree / "scripts")
    (tree / "scripts" / "foundry_operating_mode.py").write_text("raise RuntimeError('boom-resolver')\n")
    r = subprocess.run(["bash", str(tree / "hooks" / "foundry-worktree-create.sh")], input="{}", capture_output=True,
                       text=True, env=dict(os.environ, CLAUDE_PROJECT_DIR=str(ws)))
    log = (ws / ".foundry" / "dispatch.log").read_text()
    assert r.returncode != 0 and "resolver failed" in log and "boom-resolver" in log


def test_hook_selftest_still_passes():
    r = subprocess.run(["bash", HOOK, "--selftest"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# --------------------------------------------------------------------------------- docs

def test_team_mode_skill_and_gating_notes():
    skill = open(os.path.join(REPO_ROOT, "skills", "team-mode", "SKILL.md")).read()
    assert skill.startswith("---\nname: team-mode\n")
    for phrase in ("team mode", "enable team mode", "single operator mode", "upgrade to team mode"):
        assert phrase in skill.split("---")[1]
    assert "/foundry:mode" in skill and "2.0" in skill
    for s in ("command-deck", "leader-board", "dispatch"):
        t = open(os.path.join(REPO_ROOT, "skills", s, "SKILL.md")).read()
        assert "**Team mode only.**" in t and "foundry_operating_mode.py" in t and "enable-team" in t
    assert "/foundry:team-mode" in open(os.path.join(REPO_ROOT, "docs", "VERBS-QUICK-REF.md")).read()
    assert "## Operating modes" in open(os.path.join(REPO_ROOT, "README.md")).read()
