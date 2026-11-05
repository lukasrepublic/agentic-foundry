"""gv-20 hardening: one section per charter item, each a failing test plus a control. No real gh, no network."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
from test_gv16_round1 import _tree, by  # noqa: E402
from test_gv16_round2 import ft  # noqa: E402
from test_gv16_round3 import _ci_trace  # noqa: E402


# ------------------------------------------------------------------------------------------ 1 trust carve-out
def test_item1_ci_without_login_refuses_for_every_caller(monkeypatch):
    live = lambda a: {"permission": "write"}      # noqa: E731
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    ok, why = ft.author_trusted("o/r", "carol", live, operators=("carol",))
    assert ok is False and "identity lookup failed" in why
    ok, why = ft.done_trust("o/r", "carol", None, live)
    assert ok is False and "identity lookup failed" in why


def test_item1_explicit_login_registered_operator_passes_actor_refused(monkeypatch):
    live = lambda a: {"permission": "read"}      # noqa: E731
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert ft.author_trusted("o/r", "alice", live, operators=("alice",), agent_login="bot")[0] is True
    ok, why = ft.author_trusted("o/r", "bot", live, operators=("bot",), agent_login="bot")
    assert ok is False and "agent itself" in why


@pytest.mark.parametrize("bad", ["-bad", "a b", "x;y", "", "a/b", "ünï"])
def test_item1_agent_login_pattern_validated(bad, monkeypatch):
    live = lambda a: {"permission": "write"}      # noqa: E731
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    if bad == "":
        pytest.skip("empty is 'no login' and refuses under CI (covered above)")
    ok, why = ft.author_trusted("o/r", "carol", live, operators=("carol",), agent_login=bad)
    assert ok is False and "not a valid GitHub login" in why


def test_item1_trace_compares_declared_agent_login_from_base_with_actor_as_addition(tmp_path):
    base = _tree(tmp_path / "base", ["hooks/**"], operators=("alice", "declared-bot", "actor-bot"))
    gs = json.loads((base / ".claude" / "foundry-project.json").read_text())
    gs["github_scope"]["agent_login"] = "declared-bot"
    (base / ".claude" / "foundry-project.json").write_text(json.dumps(gs))
    # the declared account is the agent even when github.actor is someone else (an edited/synchronize event)
    p, doc = _ci_trace(tmp_path, base, "declared-bot", agent="actor-bot")
    assert by(doc, "ticket-author")[0]["ok"] is False and "agent itself" in by(doc, "ticket-author")[0]["detail"]
    # the actor is an addition
    p, doc = _ci_trace(tmp_path, base, "actor-bot", agent="actor-bot")
    assert by(doc, "ticket-author")[0]["ok"] is False
    # control: a registered operator passes
    p, doc = _ci_trace(tmp_path, base, "alice", agent="actor-bot")
    assert by(doc, "ticket-author")[0]["ok"] is True
    # declared login alone (no actor argument) still lifts the CI refusal
    p, doc = _ci_trace(tmp_path, base, "alice")
    assert by(doc, "ticket-author")[0]["ok"] is True

