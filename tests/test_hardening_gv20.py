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


@pytest.mark.parametrize("bad", ["-bad", "a b", "x;y", "a/b", "ünï", "bot[bot", "a[bot]x"])
def test_item1_agent_login_pattern_validated(bad, monkeypatch):
    live = lambda a: {"permission": "write"}      # noqa: E731
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    ok, why = ft.author_trusted("o/r", "carol", live, operators=("carol",), agent_login=bad)
    assert ok is False and "not a valid GitHub login" in why and "[A-Za-z0-9]" in why        # the error names the pattern


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


# ------------------------------------------------------------------------------------------ 6 governance lock + leak guard
import hashlib  # noqa: E402
import test_governance_intake as gi  # noqa: E402
import foundry_governance as gov  # noqa: E402
from test_governance_upgrade import adopted_repo, make_plugin_copy, run as urun, WF  # noqa: E402
import shutil  # noqa: E402


def test_item6_a_hand_edited_snippet_is_reported_tampered(tmp_path):
    plugin = make_plugin_copy(tmp_path)
    proj = adopted_repo(tmp_path, plugin)
    lock = json.loads(json.dumps(__import__("yaml").safe_load((proj / ".foundry" / "governance.lock").read_text())))
    assert lock["snippets"][WF] == hashlib.sha256((proj / WF).read_bytes()).hexdigest()      # recorded per file
    rc, out, _ = urun(proj, "check", plugin=plugin)
    assert "snippet-tampered" not in out                                                    # control: untouched
    (proj / WF).write_text((proj / WF).read_text() + "# hand edit\n")
    rc, out, _ = urun(proj, "check", plugin=plugin)
    assert rc == 1 and f"snippet-tampered:{WF}" in out


def test_item6_discover_out_is_limited_to_the_facts_file(tmp_path):
    proj = gi.make_repo(tmp_path / "r")
    (proj / ".foundry").mkdir(exist_ok=True)
    rc, _, err = gi.run(proj, "discover", "--out", ".foundry/permissions.yaml")
    assert rc == 2 and "output-not-facts-path" in err and not (proj / ".foundry" / "permissions.yaml").exists()
    rc, _, err = gi.run(proj, "discover", "--out", ".foundry/other.json")
    assert rc == 2
    rc, _, err = gi.run(proj, "discover", "--out", ".foundry/facts.json")                   # control
    assert rc == 0 and (proj / ".foundry" / "facts.json").is_file()


def test_item6_leak_guard_catches_confusables_and_a_doubled_slash():
    hy = "FG‑SEC‑001"                                                              # U+2011 hyphens
    cy = "FG-SEС-001"                                                                   # a Cyrillic letter
    wide = "ＦＧ-SEC-001"                                                            # fullwidth
    for text in (hy, cy, wide):
        found = gov.leak_findings({"docs/x.md": f"see {text} here"})
        assert found, text
        assert all(text not in f for f in found)                                              # the matched text is never echoed
    assert gov.leak_findings({"docs/x.md": "see FG-SEC-001 here"}) == []                    # control: a plain FG id passes
    assert gov.leak_findings({"governance//local/x.yaml": "x"}) == ["governance-local-path:governance/local/x.yaml"]
    assert gov.leak_findings({"governance/local//x.yaml": "x"})
    assert gov.leak_findings({"docs/x.md": "acme‑sec‑001"})                       # a private id behind U+2011


def test_item6_pr_failure_keeps_files_from_an_earlier_local_apply_and_the_body_lists_managed_files(tmp_path, monkeypatch):
    # (a) gh fails after the push: the files from the earlier local apply are still in the work tree
    bindir = tmp_path / "shim-bin"
    bindir.mkdir()
    (bindir / "gh").write_text("#!/bin/sh\nexit 1\n")
    (bindir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    bare = gi._remote(tmp_path, monkeypatch)
    proj = gi.make_repo(tmp_path / "r")
    gi.git(proj, "remote", "add", "origin", str(bare))
    gi.git(proj, "push", "-q", "origin", "main")
    gi.intake(proj)
    assert gi.run(proj, "apply")[0] == 0
    names = [".foundry/governance.lock", ".github/workflows/ci-gitleaks.yml", "governance/local/exceptions.yaml"]
    before = {n: (proj / n).read_bytes() for n in names}
    rc, _, _ = gi.run(proj, "apply", "--pr", "--base", "main")
    assert rc == 2
    assert {n: (proj / n).read_bytes() for n in names} == before
    assert gi.git(proj, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"


def test_item6_pr_body_lists_the_managed_files(tmp_path, monkeypatch):
    log = gi.gh_shim(tmp_path, monkeypatch)
    bare = gi._remote(tmp_path, monkeypatch)
    proj = gi.make_repo(tmp_path / "r")
    gi.git(proj, "remote", "add", "origin", str(bare))
    gi.git(proj, "push", "-q", "origin", "main")
    gi.intake(proj)
    assert gi.run(proj, "apply", "--pr", "--base", "main")[0] == 0
    call = json.loads(log.read_text().splitlines()[0])
    body = call[call.index("--body") + 1]
    assert "Managed files in this pull request" in body and "- governance/local/exceptions.yaml" in body
    assert "- .foundry/governance.lock" in body


# ------------------------------------------------------------------------------------------ 7 shared message log
import test_message_log as tml  # noqa: E402
from test_message_log import make_clone as ml_clone, make_remote as ml_remote, _post as ml_post, git as mgit  # noqa: E402,F401


@pytest.fixture
def mlenv(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for k in ("CLAUDE_PROJECT_DIR", "FOUNDRY_MESSAGE_REMOTE"):
        monkeypatch.delenv(k, raising=False)


def test_item7_a_configured_uploadpack_does_not_run_on_read(tmp_path, mlenv):
    remote = ml_remote(tmp_path)
    w = ml_clone(tmp_path, remote, "w")
    ml_post(w, "all")
    marker = tmp_path / "ran"
    evil = tmp_path / "evil.sh"
    evil.write_text(f"#!/bin/sh\ntouch {marker}\nexec git-upload-pack \"$@\"\n")
    evil.chmod(0o755)
    mgit(w, "config", "remote.origin.uploadpack", str(evil))
    mgit(w, "config", "credential.helper", f"!touch {marker}.cred")
    r = ml.read(ml.Adapter(), w, "origin", "all", advance=False)
    assert r["tip"] and not marker.exists()
    ml_post(w, "all")                                                  # a push goes through the same neutralisation
    assert not marker.exists()


def test_item7_control_git_calls_carry_the_neutralising_flags(tmp_path, mlenv, monkeypatch):
    remote = ml_remote(tmp_path)
    w = ml_clone(tmp_path, remote, "w")
    ml_post(w, "all")
    seen = []
    real = ml.subprocess.run
    monkeypatch.setattr(ml.subprocess, "run", lambda argv, *a, **k: (seen.append(list(argv)), real(argv, *a, **k))[1])
    ml_post(w, "all")
    ad = ml.Adapter()
    tip = ml.fetch_tip(ad, w, "origin")
    ml_post(w, "all")
    ml.read(ad, w, "origin", "all", since=tip, advance=False)
    fetches = [a for a in seen if "fetch" in a]
    assert fetches and all("remote.origin.uploadpack=git-upload-pack" in a and "credential.helper=" in a for a in fetches)
    assert any("remote.origin.receivepack=git-receive-pack" in a for a in seen if "push" in a)
    diffs = [a for a in seen if "diff" in a and "-U0" in a]
    assert diffs and all("--no-ext-diff" in a and "--no-textconv" in a for a in diffs)


def test_item7_the_allow_hook_asks_when_its_own_environment_names_a_remote(monkeypatch):
    hook, root = tml._hook(), os.path.realpath(ROOT)
    script = os.path.join(root, "scripts", "foundry_message_log.py")
    monkeypatch.delenv("FOUNDRY_MESSAGE_REMOTE", raising=False)
    assert hook.allowed(f"python3 {script} read --to me", root) is True                 # control
    monkeypatch.setenv("FOUNDRY_MESSAGE_REMOTE", "https://attacker.invalid/x")
    assert hook.allowed(f"python3 {script} read --to me", root) is False


def test_item7_malformed_is_capped_at_source_and_filtered_by_recipient(tmp_path, mlenv):
    remote = ml_remote(tmp_path)
    w = ml_clone(tmp_path, remote, "w")
    ml_post(w, "all")
    for n in range(ml.MAX_MALFORMED + 30):
        tml._plant_raw(w, json.dumps({"to": "other", "kind": "NOPE", "n": n}))
    tml._plant_raw(w, json.dumps({"to": "me", "kind": "NOPE"}))
    tip = ml.fetch_tip(ml.Adapter(), w, "origin")
    recs, bad = ml.replay(ml.Adapter(), w, tip)
    assert len(bad) <= ml.MAX_MALFORMED + 1 and any("malformed-truncated" in b["reason"] for b in bad)
    res = ml.read(ml.Adapter(), w, "origin", "me", advance=False)
    assert all(b.get("to") in (None, "me", "all") for b in res["malformed"]) and len(res["malformed"]) <= ml.MAX_MALFORMED
    # control: a malformed PRODUCTION record is never dropped by the cap (audit turns it into a finding)
    tml._plant_raw(w, json.dumps({"to": "all", "kind": "PROD-BEFORE"}))
    tip = ml.fetch_tip(ml.Adapter(), w, "origin")
    assert any(b["kind"] == "PROD-BEFORE" for b in ml.replay(ml.Adapter(), w, tip)[1])


def test_item7_userinfo_redaction_stops_at_the_first_at_after_the_slashes():
    assert "tok" not in ml.redact("fatal: https://us er:tok en@host.example/x.git")
    assert "tok" not in ml.redact("https://user:tok@host/x")
    assert ml.redact("no url here, a@b") == "no url here, a@b"                           # control


def test_item7_audit_flags_a_rewrite_of_earlier_lines_and_passes_an_append(tmp_path, mlenv):
    remote = ml_remote(tmp_path)
    w = ml_clone(tmp_path, remote, "w")
    a = ml_post(w, "all", text="first")
    ml_post(w, "all", text="second")
    ad = ml.Adapter()
    assert not [f for f in ml.audit(ad, w, "origin", open_minutes=120)["findings"] if f["rule"] == "history-rewritten"]
    ml_post(w, "all", text="third")                                                      # control: an append is not a rewrite
    assert not [f for f in ml.audit(ad, w, "origin", open_minutes=120)["findings"] if f["rule"] == "history-rewritten"]
    # a fast-forward commit by another writer that deletes the first record
    x = ml_clone(tmp_path, remote, "x")
    mgit(x, "fetch", "-q", "origin", "foundry/messages")
    mgit(x, "checkout", "-q", "-b", "m", "FETCH_HEAD")
    f = [p for p in (x / "messages").glob("*.jsonl")][0]
    f.write_text("".join(ln for ln in f.read_text().splitlines(True) if a["id"] not in ln))
    mgit(x, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qam", "tidy")
    mgit(x, "push", "-q", "origin", "m:foundry/messages")
    res = ml.audit(ad, w, "origin", open_minutes=120)
    hits = [f for f in res["findings"] if f["rule"] == "history-rewritten"]
    assert hits and hits[0]["removed_lines"] >= 1 and "first" not in json.dumps(hits)
    res2 = ml.audit(ad, w, "origin", open_minutes=120)                                    # the cursor is not advanced past a rewrite
    assert [f for f in res2["findings"] if f["rule"] == "history-rewritten"]


# ------------------------------------------------------------------------------------------ 8 research note ruled path
import foundry_research_note as rn  # noqa: E402
from test_research_note import sweep_with, DATE as RN_DATE  # noqa: E402


def test_item8_a_ruled_run_does_not_overwrite_a_foreign_decision_file(tmp_path):
    d = tmp_path / ".foundry" / "decisions"
    d.mkdir(parents=True)
    (d / f"{RN_DATE}-mine.md").write_text("# an operator-written decision\n")
    with pytest.raises(rn.SweepError, match="ruling-conflict"):
        rn.write_all(sweep_with(), "mine", str(tmp_path), RN_DATE)
    assert (d / f"{RN_DATE}-mine.md").read_text() == "# an operator-written decision\n"


def test_item8_a_ruled_run_does_not_write_through_a_symlink(tmp_path):
    d = tmp_path / ".foundry" / "decisions"
    d.mkdir(parents=True)
    target = tmp_path / "elsewhere.md"
    target.write_text("keep")
    (d / f"{RN_DATE}-lnk.md").symlink_to(target)
    with pytest.raises(rn.SweepError, match="ruling-conflict"):
        rn.write_all(sweep_with(), "lnk", str(tmp_path), RN_DATE)
    assert target.read_text() == "keep"


def test_item8_control_an_own_ruling_is_rewritten_by_a_ruled_rerun(tmp_path):
    first = rn.write_all(sweep_with(), "again", str(tmp_path), RN_DATE)
    second = rn.write_all(sweep_with(), "again", str(tmp_path), RN_DATE)
    assert first["status"] == second["status"] == "ruled" and Path(second["ruling"]).is_file()


# ------------------------------------------------------------------------------------------ amendment: chat mirror escaping
import shutil as _shutil  # noqa: E402


@pytest.mark.skipif(_shutil.which("jq") is None, reason="jq is not installed")
def test_chat_mirror_jq_filter_neutralises_mentions_and_link_markup(tmp_path):
    text = (ROOT / "cli" / "templates" / "workflows" / "message-log-chat-mirror.yml").read_text()
    start = text.index("jq -rR --argjson since")
    prog = text[text.index("'", start) + 1:text.index("' |", start)]
    lines = [
        {"kind": "PROD-BEFORE", "corr": "c1", "from": "ops <!channel>", "time": "2026-11-04T10:00:00Z",
         "body": "@channel @here @everyone deploy <!here> <@U123> <https://evil.example|click> & done\nnext"},
        {"kind": "PROD-AFTER", "corr": "c2", "from": "plain", "time": "2026-11-04T10:01:00Z", "body": "all good"},
        {"kind": "FINDING", "corr": "c3", "from": "x", "time": "2026-11-04T10:02:00Z", "body": "not mirrored"},
    ]
    f = tmp_path / "m.jsonl"
    f.write_text("".join(json.dumps(x) + "\n" for x in lines) + "not json\n")
    r = subprocess.run(["jq", "-rR", "--argjson", "since", "0", prog, str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = r.stdout.splitlines()
    assert len(out) == 2 and out[0].startswith("PROD-BEFORE c1 from ops &lt;!channel&gt;")
    first = out[0]
    for bad in ("@", "<", ">"):
        assert bad not in first, (bad, first)
    assert "(at)channel" in first and "&lt;https://evil.example|click&gt;" in first and "&amp; done next" in first
    assert out[1] == "PROD-AFTER c2 from plain: all good"                                  # control: plain text is unchanged


# ------------------------------------------------------------------------------------------ round 1: shell scan redirect forms
ROUND1_REFUSED = [
    "rm -rf 2>&1 /home/u/proj",
    "rm -rf >&/dev/null /x",
    "rm -rf &>/dev/null /x",
    "rm -rf 2>&1",
    "rm -rf '>' /x",
    "rm -rf \">\" /x",
    "rm -rf '2>' /x",
    "$\"rm\" -rf /x",
    "rm -rf",
    "rm -rf 2>/dev/null",
    "echo '>'&rm -rf /x",
]
ROUND1_ADMITTED = [
    f"rm -rf {SCR}/x 2>/dev/null",
    f"rm -rf {SCR}/x 2>&1",
    f"rm -rf {SCR}/x >&/dev/null",
    f"rm -rf {SCR}/x &>/dev/null",
    f"rm -rf {SCR}/x > /dev/null 2>&1",
    f"truncate -o -s 5 {SCR}/f",
    "node --version",
    "perl -v",
    "ruby -v",
    "python3 -V",
    "node -r ./preload.js script.js",
]


@pytest.mark.parametrize("cmd", ROUND1_REFUSED)
def test_round1_shape_refused(cmd):
    r = shape(cmd)
    assert r.returncode == 2, (cmd, r.stdout, r.stderr)


@pytest.mark.parametrize("cmd", ROUND1_ADMITTED)
def test_round1_shape_control_admitted(cmd):
    r = shape(cmd)
    assert r.returncode == 0, (cmd, r.stdout, r.stderr)


def test_round1_tokenizer_keeps_a_redirection_in_one_word_and_marks_quoting():
    import foundry_shell_scan as fss
    for cmd, word in (("rm x 2>&1", "2>&1"), ("rm x >&f", ">&f"), ("rm x &>f", "&>f"), ("rm x <&3", "<&3")):
        inv = fss.invocations(cmd)
        assert len(inv) == 1 and word in inv[0].args, (cmd, [(i.cmd, i.args) for i in inv])
    assert [i.cmd for i in fss.invocations("echo '>'&rm x")] == ["echo", "rm"]          # a quoted > does not glue the &
    assert fss.redirect_operand(fss.invocations("rm '>' x")[0].args[0]) is None         # quoted: a literal
    assert [(i.cmd, i.args) for i in fss.invocations('$"rm" -rf /x')] == [("rm", ["-rf", "/x"])]


# ------------------------------------------------------------------------------------------ round 1: Risks
def test_round1_agent_login_accepts_an_app_bot_suffix(monkeypatch):
    live = lambda a: {"permission": "read"}      # noqa: E731
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    for bot in ("dependabot[bot]", "github-actions[bot]"):
        assert ft.author_trusted("o/r", "alice", live, operators=("alice",), agent_login=bot)[0] is True
        ok, why = ft.author_trusted("o/r", bot, live, operators=(bot,), agent_login=bot)
        assert ok is False and "agent itself" in why


def test_round1_a_receivepack_in_the_repo_config_is_overridden_on_push(tmp_path, mlenv, monkeypatch):
    remote = ml_remote(tmp_path)
    w = ml_clone(tmp_path, remote, "w")
    marker = tmp_path / "ran"
    evil = tmp_path / "evil.sh"
    evil.write_text(f"#!/bin/sh\ntouch {marker}\nexec git-receive-pack \"$@\"\n")
    evil.chmod(0o755)
    mgit(w, "config", "remote.origin.receivepack", str(evil))
    mgit(w, "config", "push.gpgSign", "true")
    seen = []
    real = ml.subprocess.run
    monkeypatch.setattr(ml.subprocess, "run", lambda argv, *a, **k: (seen.append(list(argv)), real(argv, *a, **k))[1])
    ml_post(w, "all")
    pushes = [a for a in seen if "push" in a]
    assert pushes and all("--receive-pack=git-receive-pack" in a and "push.gpgSign=false" in a and "core.gitProxy=" in a for a in pushes)
    assert not marker.exists()


def test_round1_the_allow_hook_asks_on_the_repo_environment_too(monkeypatch):
    hook, root = tml._hook(), os.path.realpath(ROOT)
    script = os.path.join(root, "scripts", "foundry_message_log.py")
    monkeypatch.delenv("FOUNDRY_MESSAGE_REPO", raising=False)
    assert hook.allowed(f"python3 {script} audit", root) is True
    monkeypatch.setenv("FOUNDRY_MESSAGE_REPO", "/tmp/elsewhere")
    assert hook.allowed(f"python3 {script} audit", root) is False


def test_round1_audit_no_advance_leaves_the_cursor_and_a_pruned_cursor_commit_is_reported(tmp_path, mlenv):
    remote = ml_remote(tmp_path)
    w = ml_clone(tmp_path, remote, "w")
    ml_post(w, "all")
    ad = ml.Adapter()
    ml.audit(ad, w, "origin", open_minutes=120, advance=False)
    cur = ml._audit_cursor_path(ad, w)
    assert not cur.exists()                                                                # --no-advance writes nothing
    ml.audit(ad, w, "origin", open_minutes=120)
    assert cur.exists()
    cur.write_text("a" * 40 + "\n")                                                         # a commit this clone does not hold
    res = ml.audit(ad, w, "origin", open_minutes=120)
    assert [f["rule"] for f in res["findings"] if f["rule"] == "cursor-commit-missing"] == ["cursor-commit-missing"]
    assert cur.read_text().strip() == "a" * 40                                              # pinned, not advanced
    # CLI: --no-advance is accepted
    assert ml.build_parser().parse_args(["audit", "--no-advance"]).no_advance is True


def test_round1_malformed_production_lines_are_capped_too_and_the_marker_survives_read(tmp_path, mlenv):
    remote = ml_remote(tmp_path)
    w = ml_clone(tmp_path, remote, "w")
    ml_post(w, "all")
    for n in range(ml.MAX_MALFORMED + 20):
        tml._plant_raw(w, json.dumps({"to": "all", "kind": "PROD-BEFORE", "n": n}))
    ad = ml.Adapter()
    tip = ml.fetch_tip(ad, w, "origin")
    recs, bad = ml.replay(ad, w, tip)
    assert len(bad) <= ml.MAX_MALFORMED + 1 and bad[-1]["reason"].startswith("malformed-truncated") and bad[-1]["prod_truncated"] is True
    res = ml.read(ad, w, "origin", "me", advance=False)
    assert len(res["malformed"]) <= ml.MAX_MALFORMED and res["malformed"][-1]["reason"].startswith("malformed-truncated")
    audit = ml.audit(ad, w, "origin", open_minutes=120, advance=False)
    assert sum(1 for f in audit["findings"] if f["rule"] == "malformed-prod-record") >= ml.MAX_MALFORMED + 1


def test_round1_secret_guard_resolves_tar_operands_against_dash_C():
    assert secret("tar -C ~/.ssh -czf /tmp/x.tgz .").returncode == 2
    assert secret("tar --directory=/home/someone/.aws -cf /tmp/x.tar credentials").returncode == 2
    assert secret("tar -C /tmp/work -czf /tmp/x.tgz .").returncode == 0                      # control


PAPER_R1_REFUSED = [
    "cd .github/workflows && sed -i s/a/b/ ci.yml",
    "cd tests && cd .. && rm .github/workflows/ci.yml",
    "(cd x && true); sed -i s/a/b/ .github/workflows/ci.yml",
    "cd $(git rev-parse --show-toplevel) && sed -i s/a/b/ .github/workflows/ci.yml",       # unresolvable base: payload cwd
    "cd docs && sed -i s/a/b/ .github/workflows/ci.yml",                                     # a relative target also hits from the cwd
]
PAPER_R1_ADMITTED = [
    "cd docs && echo x > notes.md",
    "cd .github/workflows; echo x > notes.md",       # the base is not carried across `;`
    "rm -rf node_modules",
    "rm -rf .venv",
    "(cd docs && true); echo x > docs/ok.md",
]


@pytest.fixture
def prepo2(prepo):
    for d in ("node_modules/pkg", ".venv/lib"):
        (prepo / d).mkdir(parents=True, exist_ok=True)
    (prepo / "node_modules" / "pkg" / "jest.config.js").write_text("x")
    (prepo / ".venv" / "lib" / "conftest.py").write_text("x")
    return prepo


@pytest.mark.parametrize("cmd", PAPER_R1_REFUSED)
def test_round1_paper_refused(prepo2, cmd):
    r = bash(prepo2, cmd)
    assert r.returncode == 2, (cmd, r.stdout, r.stderr)


@pytest.mark.parametrize("cmd", PAPER_R1_ADMITTED)
def test_round1_paper_control_admitted(prepo2, cmd):
    r = bash(prepo2, cmd)
    assert r.returncode == 0, (cmd, r.stdout, r.stderr)


def test_round1_governance_kept_snippet_keeps_its_hash_and_a_missing_one_is_reported(tmp_path):
    plugin = make_plugin_copy(tmp_path)
    proj = adopted_repo(tmp_path, plugin)
    lockp = proj / ".foundry" / "governance.lock"
    yaml_ = __import__("yaml")
    first = yaml_.safe_load(lockp.read_text())["snippets"][WF]
    (proj / WF).write_text((proj / WF).read_text() + "# hand edit\n")
    assert urun(proj, "check", plugin=plugin)[1].count(f"snippet-tampered:{WF}") == 1
    rc, out, _ = urun(proj, "apply", plugin=plugin)                                         # apply KEEPS the edited file ...
    assert yaml_.safe_load(lockp.read_text())["snippets"][WF] == first                      # ... and the lock still names the old hash
    assert f"snippet-tampered:{WF}" in urun(proj, "check", plugin=plugin)[1]
    (proj / WF).unlink()
    assert f"snippet-missing:{WF}" in urun(proj, "check", plugin=plugin)[1]


def test_round1_governance_trace_compares_with_the_base_lock(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("gtrace", ROOT / "governance" / "kits" / "delivery" / "checks" / "governance-trace.py")
    gt = importlib.util.module_from_spec(spec)
    old, sys.dont_write_bytecode = sys.dont_write_bytecode, True          # no __pycache__ inside the kit directory
    try:
        spec.loader.exec_module(gt)
    finally:
        sys.dont_write_bytecode = old
    plugin = make_plugin_copy(tmp_path)
    base = adopted_repo(tmp_path, plugin)
    head = tmp_path / "head"
    shutil.copytree(base, head)
    assert gt._snippet_findings(base, head) == []                                            # control
    (head / WF).write_text((head / WF).read_text() + "# pr edit\n")
    new = hashlib.sha256((head / WF).read_bytes()).hexdigest()
    lock = (head / ".foundry" / "governance.lock")
    lock.write_text(lock.read_text().replace(__import__("yaml").safe_load(lock.read_text())["snippets"][WF], new))
    assert urun(head, "check", plugin=plugin)[1].count("snippet-") == 0                      # the PR's own lock agrees with its edit ...
    assert gt._snippet_findings(base, head) == [f"snippet-tampered:{WF}"]                    # ... the base lock does not
    (head / WF).unlink()
    assert gt._snippet_findings(base, head) == [f"snippet-missing:{WF}"]


def test_round1_every_hook_the_pass_changed_is_digest_pinned():
    import hashlib
    from test_cut_release_reconcile import FIXTURE, exec_content
    pins = FIXTURE["gv20_hook_pins"]
    assert set(pins) == {"foundry-paper-guard.py", "foundry-shell-shape.py", "foundry-secret-read-guard.py",
                         "foundry-plugin-scripts-allow.py"}
    for name, want in pins.items():
        got = hashlib.sha256(exec_content(ROOT / "hooks" / name).encode("utf-8")).hexdigest()
        assert got == want, f"hooks/{name} executable content changed: re-baseline gv20_hook_pins with a log line"
