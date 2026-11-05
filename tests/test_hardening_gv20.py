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


# ------------------------------------------------------------------------------------------ 2 prod-step
import signal  # noqa: E402
import time  # noqa: E402

import foundry_message_log as ml  # noqa: E402
import foundry_prod_step as ps  # noqa: E402
from test_prod_step import make_clone, records, step, PY  # noqa: E402,F401


@pytest.fixture
def isolated_git(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for k in ("CLAUDE_PROJECT_DIR", "FOUNDRY_MESSAGE_REMOTE"):
        monkeypatch.delenv(k, raising=False)


def test_item2_after_retry_is_idempotent_by_corr(tmp_path, monkeypatch, isolated_git):
    _, clone = make_clone(tmp_path)
    real, calls = ml._read_back, []

    def flaky(ad, repo, remote, recs):
        calls.append(recs[0]["kind"])
        real(ad, repo, remote, recs)
        if recs[0]["kind"] == "PROD-AFTER" and calls.count("PROD-AFTER") == 1:
            raise ps._Interrupted(signal.SIGTERM)       # the push landed; the read-back was interrupted

    monkeypatch.setattr(ml, "_read_back", flaky)
    rc = step(clone, [PY, "-c", "pass"])
    kinds = [r["kind"] for r in records(clone)]
    assert kinds == ["PROD-BEFORE", "PROD-AFTER"], kinds        # exactly one pair
    assert calls.count("PROD-AFTER") == 1 and rc == 128 + signal.SIGTERM


def test_item2_control_a_post_that_did_not_land_is_retried(tmp_path, monkeypatch, isolated_git):
    _, clone = make_clone(tmp_path)
    real, state = ml.publish, {"n": 0}

    def drop_first_after(ad, repo, remote, make, message, *a, **k):
        if "PROD-AFTER" in message and state["n"] == 0:
            state["n"] = 1
            raise ps._Interrupted(signal.SIGTERM)       # interrupted BEFORE anything was pushed
        return real(ad, repo, remote, make, message, *a, **k)

    monkeypatch.setattr(ml, "publish", drop_first_after)
    step(clone, [PY, "-c", "pass"])
    assert [r["kind"] for r in records(clone)] == ["PROD-BEFORE", "PROD-AFTER"]


def test_item2_sighup_and_sigquit_have_handlers():
    assert signal.SIGHUP in ps.SIGNALS and signal.SIGQUIT in ps.SIGNALS
    saved = ps._install_handlers()
    try:
        for sig in (signal.SIGHUP, signal.SIGQUIT):
            assert signal.getsignal(sig) is not signal.SIG_DFL
    finally:
        ps._restore_handlers(saved)


def test_item2_sighup_ends_the_child_and_posts_stop(tmp_path, isolated_git):
    remote, clone = make_clone(tmp_path)
    marker = tmp_path / "started"
    code = f"open({str(marker)!r},'w').write('1'); import time; time.sleep(60)"
    p = subprocess.Popen([PY, str(ROOT / "scripts" / "foundry_prod_step.py"), "--repo", str(clone), "--remote", "origin",
                          "--from", "s", "--what", "w", "--", PY, "-c", code],
                         env=dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull))
    for _ in range(100):
        if marker.exists():
            break
        time.sleep(0.1)
    p.send_signal(signal.SIGHUP)
    rc = p.wait(timeout=60)
    kinds = [r["kind"] for r in records(clone)]
    assert kinds == ["PROD-BEFORE", "STOP"] and rc == 128 + signal.SIGHUP


def test_item2_signal_in_the_popen_gap_ends_the_child(tmp_path, monkeypatch):
    """A signal delivered between Popen and the bind of `proc` is held and then ends the child (status 128+N)."""
    marker = tmp_path / "ran"
    real, procs = ps.subprocess.Popen, []

    def popen(*a, **k):
        proc = real(*a, **k)
        procs.append(proc)
        os.kill(os.getpid(), signal.SIGTERM)         # lands while the signals are masked
        return proc

    monkeypatch.setattr(ps.subprocess, "Popen", popen)
    saved = ps._install_handlers()
    try:
        with pytest.raises(ps._Interrupted) as ei:
            ps._run([PY, "-c", f"import time; time.sleep(30); open({str(marker)!r},'w')"])
    finally:
        ps._restore_handlers(saved)
    assert ei.value.signum == signal.SIGTERM
    assert procs[0].poll() is not None, "the child must have been ended and reaped, not left running"
    assert not marker.exists()


def test_item2_cleanup_wait_is_bounded(tmp_path, isolated_git):
    _, clone = make_clone(tmp_path)
    t0 = time.monotonic()
    rc = ps.run_step([PY, "-c", "import sys; sys.exit(3)"], clone, "origin", "s", "all", "w",
                     cleanups=[f"{PY} -c 'import time; time.sleep(60)'"], cleanup_timeout_s=1)
    assert time.monotonic() - t0 < 30 and rc == 3
    stop = [r for r in records(clone) if r["kind"] == "STOP"][0]
    assert "cleanup timed out" in stop["body"]


def test_item2_control_a_fast_cleanup_is_not_a_timeout(tmp_path, isolated_git):
    _, clone = make_clone(tmp_path)
    ps.run_step([PY, "-c", "import sys; sys.exit(3)"], clone, "origin", "s", "all", "w",
                cleanups=[f"{PY} -c pass"], cleanup_timeout_s=30)
    stop = [r for r in records(clone) if r["kind"] == "STOP"][0]
    assert "timed out" not in stop["body"] and "cleanup: ok" in stop["body"]


def test_item2_cleanup_timeout_policy_default_and_key(tmp_path):
    assert ps._cleanup_timeout(tmp_path) == 120
    (tmp_path / ".foundry").mkdir()
    (tmp_path / ".foundry" / "operating-model.yaml").write_text("schema_version: 1\nmessaging:\n  cleanup_timeout_s: 7\n")
    assert ps._cleanup_timeout(tmp_path) == 7
