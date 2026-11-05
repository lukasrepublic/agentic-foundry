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


# ------------------------------------------------------------------------------------------ 3 paper guard (a visibility control)
from test_gv16_followups import bash, repo  # noqa: E402,F401


@pytest.fixture
def prepo(repo):
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "conftest.py").write_text("x = 1\n")
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "x").mkdir(exist_ok=True)
    (repo / "x" / "a.txt").write_text("a\n")
    (repo / "pkgdir" / "workflows").mkdir(parents=True, exist_ok=True)
    (repo / "pkgdir" / "workflows" / "evil.yml").write_text("on: push\n")
    return repo


REFUSED_PAPER = [
    "cd .github/workflows && sed -i s/a/b/ ci.yml",
    "cd .github && cd workflows && echo x > ci.yml",
    "cp -t .github/workflows /tmp/x.yml",
    "cp --target-directory=.github/workflows /tmp/x.yml",
    "mv -t .github/workflows /tmp/x.yml",
    "cp -r x .github/",
    "cp -r pkgdir/workflows .github/",
    "rsync -a x/ .github/",
    "rsync -a pkgdir/ .github/",
    "rm -rf tests",
    "rm -rf *",
    "rm -rf ./",
    "echo '{}' > package.json",
    "cp -r x .worktrees/w1/.github/",
    "sed -i s/a/b/ .worktrees/w1/.github/workflows/ci.yml",
    "sed -i s/a/b/ nested-clone/.github/workflows/ci.yml",
]
ADMITTED_PAPER = [
    "cp -r x docs/",
    "cd docs && echo x > notes.md",
    "cp -t docs /tmp/x.txt",
    "rsync -a x/ docs/",
    "rm -rf x",
    "echo hi > docs/new.md",
    "cd .github/workflows && cat ci.yml",
    "cp -r x src/",
]


@pytest.mark.parametrize("cmd", REFUSED_PAPER)
def test_item3_refused(prepo, cmd):
    r = bash(prepo, cmd)
    assert r.returncode == 2, (cmd, r.stdout, r.stderr)
    assert "CI path" in r.stderr


@pytest.mark.parametrize("cmd", ADMITTED_PAPER)
def test_item3_control_admitted(prepo, cmd):
    r = bash(prepo, cmd)
    assert r.returncode == 0, (cmd, r.stdout, r.stderr)


def test_item3_the_docstring_still_says_visibility_control_and_no_boundary():
    doc = (ROOT / "hooks" / "foundry-paper-guard.py").read_text().split('"""')[1]
    assert "visibility control" in doc and "not a sandbox" in doc


# ------------------------------------------------------------------------------------------ 4 shell shape + secret guard
SHAPE = ROOT / "hooks" / "foundry-shell-shape.py"
SID = "11111111-2222-3333-4444-555555555555"
SCR = f"/tmp/claude-1000/-proj/{SID}/scratchpad"


def shape(command, *, session=SID, extra=None):
    payload = {"tool_input": {"command": command}, "cwd": str(ROOT), "session_id": session}
    payload.update(extra or {})
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_SCRATCHPAD", "FOUNDRY_SHELL_SHAPE_STRICT")}
    return subprocess.run([sys.executable, str(SHAPE)], input=json.dumps(payload), capture_output=True, text=True, env=env)


SHAPE_REFUSED = [
    "</dev/null rm -rf /x",
    "$'rm' -rf /x",
    "$'\\x72m' -rf /x",
    "FOO=1 2>/dev/null rm -rf /x",
    "rm -rf /x 2>/dev/null",
    'bash -c "$CMD"',
    "cat s.py | python3",
    "python3 < s.py",
    "perl -e 'unlink 1'",
    "ruby -e 'File.delete(1)'",
    "node -e 'require(1)'",
    "git clean -fdx",
    "git -C /x clean -fd",
    "rsync -a --delete src/ dst/",
    "rsync -a --remove-source-files src/ dst/",
    "truncate -s 0 /x/file",
    "shred /x/file",
    "unlink /x/file",
    f"rm -rf /tmp/claude-1000/-proj/other-session/scratchpad/x",
]
SHAPE_ADMITTED = [
    f"rm -rf {SCR}/x 2>/dev/null",
    f"rm -rf {SCR}/x >/dev/null 2>&1",
    f"rm -rf {SCR}/x > /dev/null",
    f"</dev/null rm -rf {SCR}/x",
    f"truncate -s 0 {SCR}/f",
    f"rsync -a --delete {SCR}/a/ {SCR}/b/",
    "git clean -n",
    "python3 script.py",
    "node script.js",
    "perl script.pl",
    "bash -c 'git status'",
    "ls -la",
]


@pytest.mark.parametrize("cmd", SHAPE_REFUSED)
def test_item4_shape_refused(cmd):
    r = shape(cmd)
    assert r.returncode == 2, (cmd, r.stdout, r.stderr)


@pytest.mark.parametrize("cmd", SHAPE_ADMITTED)
def test_item4_shape_control_admitted(cmd):
    r = shape(cmd)
    assert r.returncode == 0, (cmd, r.stdout, r.stderr)


def test_item4_fallback_admits_only_this_sessions_scratchpad():
    assert shape(f"rm -rf {SCR}/x").returncode == 0
    assert shape(f"rm -rf {SCR}/x", session="someone-else").returncode == 2
    assert shape(f"rm -rf {SCR}/x", session="").returncode == 2         # no session id: the fallback admits nothing


def test_item4_ansi_c_tokenizer_models():
    import foundry_shell_scan as fss
    assert [(i.cmd, i.args) for i in fss.invocations("$'r\\x6d' -rf /x")] == [("rm", ["-rf", "/x"])]
    assert [(i.cmd, i.args) for i in fss.invocations("echo $'a\\'b' c")] == [("echo", ["a'b", "c"])]
    assert fss.redirect_operand("2>/dev/null") == "/dev/null" and fss.redirect_operand("2>") == "" and fss.redirect_operand("-rf") is None

SECRET = ROOT / "hooks" / "foundry-secret-read-guard.py"


def secret(command, cwd=None):
    env = {k: v for k, v in os.environ.items()}
    return subprocess.run([sys.executable, str(SECRET)], input=json.dumps({"tool_input": {"command": command}, "cwd": cwd or str(ROOT)}),
                          capture_output=True, text=True, env=env)


SECRET_REFUSED = [
    "aws secretsmanager --region x get-secret-value --secret-id s",
    "aws --profile p secretsmanager --region eu-west-1 get-secret-value --secret-id s",
    "aws ssm --region x get-parameter --name n --with-decryption",
    "vault -address http://x kv get secret/a",
    "dd if=~/.ssh/id_rsa of=/dev/null",
    "dd if=$HOME/.aws/credentials",
    "grep -r TOKEN ~",
    "grep -rn password $HOME",
    "rg token ~",
    "tar czf x.tgz ~",
    "zip -r x.zip /home/someone",
    "cp -r ~ /tmp/x",
    "cat /proc/$$/environ",
    "cat /proc/self/task/1/environ",
    "</root/.ssh/id_rsa cat",
]
SECRET_ADMITTED = [
    "aws secretsmanager describe-secret --secret-id s",
    "aws secretsmanager --region x describe-secret --secret-id s",
    "vault kv metadata get secret/a",
    "vault kv --namespace n metadata get secret/a",
    "dd if=/dev/zero of=/tmp/x count=1",
    "grep -r TOKEN src",
    "grep TOKEN ~/notes.txt",
    "tar czf x.tgz src",
    "cat /proc/cpuinfo",
    "ls ~",
]


@pytest.mark.parametrize("cmd", SECRET_REFUSED)
def test_item4_secret_refused(cmd):
    r = secret(cmd)
    assert r.returncode == 2, (cmd, r.stdout, r.stderr)


@pytest.mark.parametrize("cmd", SECRET_ADMITTED)
def test_item4_secret_control_admitted(cmd):
    r = secret(cmd)
    assert r.returncode == 0, (cmd, r.stdout, r.stderr)


# ------------------------------------------------------------------------------------------ 5 claims: draft-then-ready, pr new, REST
from test_claims_expiry import gd, refused_claim, set_claim, set_ticket, team  # noqa: E402,F401

CLAIM_ROUTES = [
    "gh pr ready",
    "gh pr ready 12",
    "gh pr ready -R example-org/app 12",
    "gh pr new --title t --body b",
    "gh api repos/example-org/app/pulls -X POST -f title=t -f head=b -f base=main",
    "gh api repos/example-org/app/pulls --method POST -f title=t",
    "gh api /repos/example-org/app/pulls -f title=t -f head=b -f base=main",       # a field implies POST
    "gh api repos/example-org/app/pulls -XPOST -F draft=false -f title=t",
]
CLAIM_ADMITTED = [
    "gh pr ready --undo 12",
    "gh pr new --draft --title t",
    "gh api repos/example-org/app/pulls",                                          # a GET
    "gh api repos/example-org/app/pulls -X GET",
    "gh api repos/example-org/app/pulls -X POST -F draft=true -f title=t",
    "gh api repos/example-org/app/issues -X POST -f title=t",                      # not the pulls route
    "gh pr list",
    "gh pr view 12",
]


@pytest.mark.parametrize("cmd", CLAIM_ROUTES)
def test_item5_unclaimed_routes_are_refused_and_a_live_claim_admits(team, cmd):
    set_ticket(team)
    assert refused_claim(gd(team, cmd)), cmd
    set_claim(team, age=60)
    assert gd(team, cmd).returncode == 0, cmd


@pytest.mark.parametrize("cmd", CLAIM_ADMITTED)
def test_item5_control_admitted_without_a_claim(team, cmd):
    set_ticket(team)
    r = gd(team, cmd)
    assert r.returncode == 0, (cmd, r.stdout, r.stderr)
