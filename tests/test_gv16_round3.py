"""gv-16 round 3: (1) the CI identity rule, (2) the paper guard's import and payload-shape fail-closed paths. No real gh."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "foundry-paper-guard.py"
sys.path.insert(0, str(ROOT / "scripts"))
from test_gv16_round1 import DESIGN, _tree, by  # noqa: E402
from test_gv16_round2 import ft  # noqa: E402

CHECK = ROOT / "governance" / "kits" / "delivery" / "checks" / "governance-trace.py"


# ------------------------------------------------------------------------------------------------ 1 CI identity
def _ci_trace(tmp_path, base, author, *, ci=True, agent=None):
    """governance-trace through its CLI with a `gh` whose `api user` is a 403 (an App installation token)."""
    gb = tmp_path / "ghbin"
    gb.mkdir(exist_ok=True)
    g = gb / "gh"
    g.write_text("#!/bin/sh\necho \"$@\" >> \"$GH_LOG\"\ncase \"$*\" in\n  'api user') echo 'HTTP 403: Resource not accessible by integration' >&2; exit 1;;\n"
                 "  *) echo '{\"permission\": \"read\"}';;\nesac\n")
    g.chmod(0o755)
    pr, ticket = tmp_path / "pr.json", tmp_path / "ticket.json"
    pr.write_text(json.dumps({"number": 3, "body": "Closes #7", "files": [{"path": "hooks/x.py"}]}))
    ticket.write_text(json.dumps({"number": 7, "body": DESIGN, "labels": [], "author": {"login": author}}))
    cmd = [sys.executable, str(CHECK), "check", "--pr-json", str(pr), "--ticket-json", str(ticket), "--project-dir", str(base),
           "--base-dir", str(base), "--plugin-root", str(ROOT), "--repo", "o/r", "--json"]
    if agent:
        cmd += ["--agent-login", agent]
    env = dict(os.environ, PATH=str(gb) + os.pathsep + os.environ["PATH"], GH_LOG=str(tmp_path / "gh.log"))
    env.pop("GITHUB_ACTIONS", None)
    if ci:
        env["GITHUB_ACTIONS"] = "true"
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
    return p, json.loads(p.stdout)


def test_in_ci_a_registered_operator_passes_though_api_user_is_403(tmp_path):
    base = _tree(tmp_path / "base", ["hooks/**"], operators=("alice", "bot-actor"))
    p, doc = _ci_trace(tmp_path, base, "alice", agent="bot-actor")
    assert by(doc, "ticket-author")[0]["ok"] is True, doc
    assert p.returncode == 0, p.stdout
    assert "api user" not in (tmp_path / "gh.log").read_text() if (tmp_path / "gh.log").exists() else True    # no self lookup in CI


def test_in_ci_the_actor_authored_ticket_is_refused_even_if_registered(tmp_path):
    base = _tree(tmp_path / "base", ["hooks/**"], operators=("alice", "bot-actor"))
    p, doc = _ci_trace(tmp_path, base, "bot-actor", agent="bot-actor")
    f = by(doc, "ticket-author")[0]
    assert f["ok"] is False and "agent itself" in f["detail"] and p.returncode == 1


def test_in_ci_without_an_agent_login_the_check_refuses(tmp_path):      # gv-20: was "self branch is skipped"
    base = _tree(tmp_path / "base", ["hooks/**"], operators=("alice",))
    p, doc = _ci_trace(tmp_path, base, "alice")
    f = by(doc, "ticket-author")[0]
    assert f["ok"] is False and "identity lookup failed" in f["detail"]


def test_outside_ci_a_failed_lookup_is_still_identity_lookup_failed(tmp_path):
    base = _tree(tmp_path / "base", ["hooks/**"], operators=("alice",))
    p, doc = _ci_trace(tmp_path, base, "alice", ci=False)
    f = by(doc, "ticket-author")[0]
    assert f["ok"] is False and "identity lookup failed" in f["detail"]
    p, doc = _ci_trace(tmp_path, base, "alice", ci=False, agent="someone-else")   # an explicit login needs no lookup anywhere
    assert by(doc, "ticket-author")[0]["ok"] is True


def test_author_trusted_unit_under_github_actions(monkeypatch):
    dead = lambda a: None if a[:2] == ["api", "user"] else {"permission": "write"}      # noqa: E731
    assert ft.author_trusted("o/r", "carol", dead, operators=())[0] is False             # outside CI: fail closed
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert ft.author_trusted("o/r", "carol", dead, operators=())[0] is False             # gv-20: in CI with no login: refuse
    assert ft.author_trusted("o/r", "carol", dead, operators=(), agent_login="bot")[0] is True   # explicit login: permission route
    ok, why = ft.author_trusted("o/r", "carol", dead, operators=("carol",), agent_login="carol")
    assert ok is False and "agent itself" in why


def test_the_template_passes_the_actor():
    text = (ROOT / "governance" / "kits" / "delivery" / "checks" / "governance-trace.yml.template").read_text()
    step = text.split("- name: governance-trace\n")[1]
    assert "ACTOR: ${{ github.actor }}" in step and '--agent-login "$ACTOR"' in step


# ------------------------------------------------------------------------------------------------ 2 import + shape
def _run(stdin, cwd, hook=HOOK):
    return subprocess.run([str(hook)], input=stdin, capture_output=True, text=True, timeout=60, cwd=str(cwd),
                          env=dict(os.environ, CLAUDE_PROJECT_DIR=str(cwd)))


def test_an_import_failure_exits_2_with_a_reason_not_a_traceback(tmp_path):
    lone = tmp_path / "plugin" / "hooks"
    lone.mkdir(parents=True)
    shutil.copy(HOOK, lone / "foundry-paper-guard.py")                         # no ../scripts: the store cannot be imported
    (lone / "foundry-paper-guard.py").chmod(0o755)
    for payload in ({"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "a.py")}},
                    {"tool_name": "Bash", "tool_input": {"command": "ls"}}, {"tool_name": "Read", "tool_input": {}}):
        p = _run(json.dumps(payload), tmp_path, lone / "foundry-paper-guard.py")
        assert p.returncode == 2, (payload, p.returncode, p.stderr)
        assert "could not be imported" in p.stderr and "Traceback" not in p.stderr
        assert json.loads(p.stdout)["decision"] == "block"
    p = _run(json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "a.py")}}), tmp_path)   # control: real tree
    assert p.returncode == 0


@pytest.mark.parametrize("payload", [
    {"tool_name": "Edit", "tool_input": None}, {"tool_name": "Edit", "tool_input": []}, {"tool_name": "Write", "tool_input": "x"},
    {"tool_name": "MultiEdit", "tool_input": 3}, {"tool_name": "Edit", "tool_input": {"file_path": 7}},
    {"tool_name": "Edit", "tool_input": {"file_path": ["a.py"]}}, {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": {"a": 1}}},
    {"tool_name": "Bash", "tool_input": None}, {"tool_name": "Bash", "tool_input": "ls"}, {"tool_name": "Bash", "tool_input": {}},
    {"tool_name": "Bash", "tool_input": {"command": ["ls"]}}, {"tool_name": "Bash", "tool_input": {"command": 5}},
])
def test_a_wrongly_shaped_tool_input_is_refused_with_a_reason(tmp_path, payload):
    p = _run(json.dumps(payload), tmp_path)
    assert p.returncode == 2, (payload, p.returncode, p.stdout, p.stderr)
    assert "fail closed" in p.stderr and "Traceback" not in p.stderr


@pytest.mark.parametrize("payload", [
    {"tool_name": "Edit", "tool_input": {}}, {"tool_name": "Edit", "tool_input": {"file_path": ""}},
    {"tool_name": "Bash", "tool_input": {"command": ""}}, {"tool_name": "Bash", "tool_input": {"command": "ls"}},
    {"tool_name": "Read", "tool_input": "anything"}, {"tool_name": "Glob"},
])
def test_well_shaped_or_irrelevant_payloads_are_admitted_control(tmp_path, payload):
    assert _run(json.dumps(payload), tmp_path).returncode == 0, payload
