"""gv-13: foundry_prod_step.py (rule D2) -- PROD-BEFORE, run, PROD-AFTER or STOP.

Real local bare-remote git fixtures only (the message log is a git branch). No network, no gh.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import foundry_message_log as ml  # noqa: E402
import foundry_prod_step as ps  # noqa: E402

CLI = SCRIPTS / "foundry_prod_step.py"
PY = sys.executable
NOSLEEP = lambda s: None  # noqa: E731


@pytest.fixture(autouse=True)
def _isolated_git(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL",
              "CLAUDE_PROJECT_DIR", "FOUNDRY_MESSAGE_REMOTE"):
        monkeypatch.delenv(k, raising=False)


def make_clone(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(remote)], check=True)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(remote), str(clone)], check=True, capture_output=True)
    return remote, clone


def records(clone):
    ad = ml.Adapter()
    tip = ml.fetch_tip(ad, clone, "origin")
    return ml.replay(ad, clone, tip)[0] if tip else []


def step(clone, command, *, what="change the thing", extra=(), sender="session-a"):
    return ps.main(["--repo", str(clone), "--remote", "origin", "--from", sender, "--what", what, *extra,
                    "--", *command])


def test_success_posts_before_then_after_with_one_corr(tmp_path, capsys):
    _, clone = make_clone(tmp_path)
    marker = tmp_path / "ran.txt"
    rc = step(clone, [PY, "-c", f"open({str(marker)!r}, 'w').write('x')"])
    recs = records(clone)
    assert rc == 0 and marker.read_text() == "x"
    assert [r["kind"] for r in recs] == ["PROD-BEFORE", "PROD-AFTER"]
    assert recs[0]["corr"] == recs[1]["corr"] and recs[0]["corr"]
    assert recs[0]["from"] == recs[1]["from"] == "session-a"
    assert "change the thing" in recs[0]["body"]
    assert "outcome: ok" in recs[1]["body"]


def test_the_before_line_exists_before_the_command_runs(tmp_path):
    remote, clone = make_clone(tmp_path)
    probe = tmp_path / "seen.json"
    code = ("import subprocess,sys,json;"
            f"r=subprocess.run(['git','-C',{str(remote)!r},'cat-file','-p','foundry/messages:messages'],capture_output=True,text=True);"
            f"open({str(probe)!r},'w').write(json.dumps({{'rc':r.returncode,'out':r.stdout}}))")
    assert step(clone, [PY, "-c", code]) == 0
    seen = json.loads(probe.read_text())
    assert seen["rc"] == 0 and seen["out"].strip()      # the log branch already had content when the command ran


def test_failing_command_posts_stop_after_cleanup_and_exits_non_zero(tmp_path):
    _, clone = make_clone(tmp_path)
    order = tmp_path / "order.txt"
    cmd = [PY, "-c", f"open({str(order)!r}, 'a').write('run\\n'); raise SystemExit(7)"]
    cleanup = f"{PY} -c \"open({str(order)!r}, 'a').write('cleanup\\n')\""
    rc = step(clone, cmd, extra=("--on-failure", cleanup))
    recs = records(clone)
    assert rc == 7
    assert [r["kind"] for r in recs] == ["PROD-BEFORE", "STOP"]
    assert recs[0]["corr"] == recs[1]["corr"]
    assert "status 7" in recs[1]["body"] and "cleanup: ok" in recs[1]["body"]
    assert order.read_text().split() == ["run", "cleanup"]          # cleanup ran, and before STOP was posted
    assert not any(r["kind"] == "PROD-AFTER" for r in recs)


def test_a_failing_cleanup_is_reported_in_the_stop_and_does_not_hide_the_failure(tmp_path):
    _, clone = make_clone(tmp_path)
    rc = step(clone, [PY, "-c", "raise SystemExit(3)"], extra=("--on-failure", f"{PY} -c \"raise SystemExit(9)\""))
    recs = records(clone)
    assert rc == 3 and recs[-1]["kind"] == "STOP" and "cleanup: failed" in recs[-1]["body"]


def test_command_not_found_is_a_failure_with_a_stop(tmp_path):
    _, clone = make_clone(tmp_path)
    rc = step(clone, [str(tmp_path / "no-such-program")])
    recs = records(clone)
    assert rc == 127 and [r["kind"] for r in recs] == ["PROD-BEFORE", "STOP"]


def test_a_failed_exit_zero_is_never_reported_when_the_command_was_killed_by_a_signal(tmp_path):
    _, clone = make_clone(tmp_path)
    rc = step(clone, [PY, "-c", "import os,signal; os.kill(os.getpid(), signal.SIGTERM)"])
    assert rc == 128 + 15 and records(clone)[-1]["kind"] == "STOP"


def test_when_the_before_line_cannot_be_posted_the_command_does_not_run(tmp_path, capsys):
    _, clone = make_clone(tmp_path)
    marker = tmp_path / "ran.txt"
    rc = ps.main(["--repo", str(clone), "--remote", "nope", "--from", "s", "--what", "w", "--",
                  PY, "-c", f"open({str(marker)!r}, 'w').write('x')"])
    assert rc == 1 and not marker.exists()
    assert "before" in capsys.readouterr().err.lower()


def test_repo_and_remote_are_required_never_taken_from_the_cwd(tmp_path, monkeypatch, capsys):
    _, clone = make_clone(tmp_path)
    monkeypatch.chdir(clone)
    marker = tmp_path / "ran.txt"
    for argv in (["--remote", "origin"], ["--repo", str(clone)], []):
        rc = ps.main([*argv, "--from", "s", "--what", "w", "--", PY, "-c", f"open({str(marker)!r}, 'w')"])
        assert rc == 2 and not marker.exists()
    assert records(clone) == []


def test_what_is_required_and_a_command_is_required(tmp_path):
    _, clone = make_clone(tmp_path)
    assert ps.main(["--repo", str(clone), "--remote", "origin", "--from", "s", "--", PY, "-c", "pass"]) == 2
    assert ps.main(["--repo", str(clone), "--remote", "origin", "--from", "s", "--what", "w"]) == 2
    assert ps.main(["--repo", str(clone), "--remote", "origin", "--from", "s", "--what", "w", "--"]) == 2


def test_each_run_gets_a_fresh_corr(tmp_path):
    _, clone = make_clone(tmp_path)
    step(clone, [PY, "-c", "pass"])
    step(clone, [PY, "-c", "pass"])
    corrs = [r["corr"] for r in records(clone) if r["kind"] == "PROD-BEFORE"]
    assert len(corrs) == 2 and corrs[0] != corrs[1]


def test_the_audit_is_clean_after_a_wrapped_step_and_flags_a_missing_before(tmp_path):
    _, clone = make_clone(tmp_path)
    step(clone, [PY, "-c", "pass"])
    ad = ml.Adapter()
    assert ml.audit(ad, clone, "origin", 120)["findings"] == []
    # a process miss: an AFTER with no BEFORE (the step was done without the wrapper)
    ml.post(ad, clone, "origin", "s", "all", "PROD-AFTER", "outcome: ok", corr="orphan", sleep=NOSLEEP)
    findings = ml.audit(ad, clone, "origin", 120)["findings"]
    assert [f["rule"] for f in findings] == ["after-without-before"]


def test_the_command_text_and_its_arguments_are_not_posted(tmp_path):
    _, clone = make_clone(tmp_path)
    secret = "tok" + "en-value-" + "12345"
    step(clone, [PY, "-c", "pass", secret])
    body = " ".join(r["body"] for r in records(clone))
    assert secret not in body and "python" in body.lower()      # the program name only


def test_command_output_is_not_posted(tmp_path, capfd):
    _, clone = make_clone(tmp_path)
    step(clone, [PY, "-c", "print('LINE-FROM-THE-COMMAND')"])
    assert "LINE-FROM-THE-COMMAND" in capfd.readouterr().out          # shown to the caller
    assert not any("LINE-FROM-THE-COMMAND" in r["body"] for r in records(clone))


def test_cli_exit_status_matches_the_command(tmp_path):
    _, clone = make_clone(tmp_path)
    p = subprocess.run([PY, str(CLI), "--repo", str(clone), "--remote", "origin", "--from", "s", "--what", "w", "--",
                        PY, "-c", "raise SystemExit(4)"], capture_output=True, text=True, timeout=120,
                       env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
    assert p.returncode == 4


def test_the_script_never_goes_through_a_shell():
    src = CLI.read_text()
    assert "shell=True" not in src and "os.system" not in src
    assert ("c" + "t" + "x") not in src.lower()


# ---- signals, cleanup that raises, empty cleanup, repo validation (PR #304 round 1) ----------------------------
import signal  # noqa: E402
import time  # noqa: E402


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_a_signal_ends_the_child_runs_cleanup_and_still_posts_stop(tmp_path, sig):
    _, clone = make_clone(tmp_path)
    ready, order = tmp_path / "ready", tmp_path / "order.txt"
    child = f"import time; open({str(ready)!r}, 'w').write('1'); time.sleep(60)"
    cleanup = f"{PY} -c \"open({str(order)!r}, 'a').write('cleanup')\""
    proc = subprocess.Popen([PY, str(CLI), "--repo", str(clone), "--remote", "origin", "--from", "s", "--what", "w",
                             "--on-failure", cleanup, "--", PY, "-c", child],
                            env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
    for _ in range(100):
        if ready.exists():
            break
        time.sleep(0.1)
    assert ready.exists()
    proc.send_signal(sig)
    assert proc.wait(timeout=60) == 128 + int(sig)
    recs = records(clone)
    assert [r["kind"] for r in recs] == ["PROD-BEFORE", "STOP"]
    assert f"signal {int(sig)}" in recs[1]["body"] and "cleanup: ok" in recs[1]["body"]
    assert order.read_text() == "cleanup"


def test_a_cleanup_that_raises_is_a_failed_cleanup_and_stop_is_still_posted(tmp_path):
    _, clone = make_clone(tmp_path)
    calls = []

    def runner(argv):
        calls.append(argv)
        if len(calls) == 1:
            return 3, "the command exited with status 3"
        raise RuntimeError("cleanup blew up")

    rc = ps.run_step(["prog"], clone, "origin", "s", "all", "w", cleanups=["one", "two"], runner=runner)
    recs = records(clone)
    assert rc == 3 and [r["kind"] for r in recs] == ["PROD-BEFORE", "STOP"]
    assert "cleanup: failed (2 of 2 commands)" in recs[1]["body"]


def test_an_unexpected_exception_from_the_command_still_posts_stop(tmp_path):
    _, clone = make_clone(tmp_path)

    def runner(argv):
        raise MemoryError("boom")

    rc = ps.run_step(["prog"], clone, "origin", "s", "all", "w", runner=runner)
    recs = records(clone)
    assert rc == 1 and [r["kind"] for r in recs] == ["PROD-BEFORE", "STOP"] and "MemoryError" in recs[1]["body"]


def test_an_empty_on_failure_is_rejected_and_nothing_runs(tmp_path):
    _, clone = make_clone(tmp_path)
    marker = tmp_path / "ran.txt"
    for bad in ("", "   "):
        rc = ps.main(["--repo", str(clone), "--remote", "origin", "--from", "s", "--what", "w", "--on-failure", bad,
                      "--", PY, "-c", f"open({str(marker)!r}, 'w')"])
        assert rc == 2 and not marker.exists()
    assert records(clone) == []


@pytest.mark.parametrize("repo", ["-evil", "/no/such/dir/at/all"])
def test_a_bad_repo_is_rejected_before_anything_runs(tmp_path, repo):
    marker = tmp_path / "ran.txt"
    rc = ps.main(["--repo", repo, "--remote", "origin", "--from", "s", "--what", "w", "--",
                  PY, "-c", f"open({str(marker)!r}, 'w')"])
    assert rc == 2 and not marker.exists()


def test_the_skill_and_the_floor_say_never_to_persist_an_allow_for_prod_step():
    assert "never persist an allow" in (ROOT / "skills" / "command-deck" / "SKILL.md").read_text()
    for rel in ("docs/permission-floor.json", "cli/permission-floor.json"):
        rows = [e for e in json.load(open(ROOT / rel))["entries"] if e["rule"].endswith("foundry_prod_step.py:*)")]
        assert len(rows) == 1 and rows[0]["tier"] == "ask" and "NEVER persist an allow" in rows[0]["rationale"]


# ---- PR #304 round 2: process groups, no ignored signals, the BEFORE-to-run window ------------------------------
def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_a_signal_ends_the_whole_process_group_of_the_command(tmp_path):
    _, clone = make_clone(tmp_path)
    ready, pidfile = tmp_path / "ready", tmp_path / "grandchild.pid"
    grand = "import time; time.sleep(120)"
    child = ("import subprocess, sys, time; "
             f"g = subprocess.Popen([sys.executable, '-c', {grand!r}]); "
             f"open({str(pidfile)!r}, 'w').write(str(g.pid)); open({str(ready)!r}, 'w').write('1'); time.sleep(120)")
    proc = subprocess.Popen([PY, str(CLI), "--repo", str(clone), "--remote", "origin", "--from", "s", "--what", "w",
                             "--", PY, "-c", child],
                            env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
    for _ in range(100):
        if ready.exists():
            break
        time.sleep(0.1)
    assert ready.exists()
    gpid = int(pidfile.read_text())
    assert _alive(gpid)
    proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=60) == 143
    for _ in range(50):
        if not _alive(gpid):
            break
        time.sleep(0.1)
    assert not _alive(gpid)                       # the grandchild went with its group
    assert [r["kind"] for r in records(clone)] == ["PROD-BEFORE", "STOP"]


def test_cleanup_children_do_not_inherit_an_ignored_signal(tmp_path):
    _, clone = make_clone(tmp_path)
    ready, seen = tmp_path / "ready", tmp_path / "seen.txt"
    child = f"import time; open({str(ready)!r}, 'w').write('1'); time.sleep(60)"
    probe = ("import signal; open(%r, 'w').write(' '.join(str(signal.getsignal(s) != signal.SIG_IGN) "
             "for s in (signal.SIGINT, signal.SIGTERM)))" % str(seen))
    proc = subprocess.Popen([PY, str(CLI), "--repo", str(clone), "--remote", "origin", "--from", "s", "--what", "w",
                             "--on-failure", f"{PY} -c \"{probe}\"", "--", PY, "-c", child],
                            env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
    for _ in range(100):
        if ready.exists():
            break
        time.sleep(0.1)
    proc.send_signal(signal.SIGINT)
    assert proc.wait(timeout=60) == 130
    assert seen.read_text() == "True True"


def test_a_second_signal_during_cleanup_does_not_stop_the_stop_line(tmp_path, monkeypatch):
    _, clone = make_clone(tmp_path)
    calls = []

    def runner(argv):
        calls.append(argv)
        if len(calls) == 1:
            raise ps._Interrupted(signal.SIGTERM)
        return 0, "ok"

    rc = ps.run_step(["prog"], clone, "origin", "s", "all", "w", cleanups=["a b"], runner=runner)
    assert rc == 143 and [r["kind"] for r in records(clone)] == ["PROD-BEFORE", "STOP"]


def test_an_interrupt_right_after_the_before_post_still_posts_stop(tmp_path, monkeypatch):
    _, clone = make_clone(tmp_path)
    real, ran = ps._post, []

    def post(ad, repo, remote, sender, to, kind, body, corr, kw):
        rec = real(ad, repo, remote, sender, to, kind, body, corr, kw)
        if kind == "PROD-BEFORE":
            raise ps._Interrupted(signal.SIGTERM)       # lands in the window between the post and run()
        return rec

    monkeypatch.setattr(ps, "_post", post)
    rc = ps.run_step(["prog"], clone, "origin", "s", "all", "w", runner=lambda argv: ran.append(argv) or (0, "ok"))
    assert rc == 143 and ran == []                                  # the command never started
    recs = records(clone)
    assert [r["kind"] for r in recs] == ["PROD-BEFORE", "STOP"] and "signal 15" in recs[1]["body"]


def test_an_interrupt_during_the_before_post_exits_143_and_does_not_run_the_command(tmp_path, monkeypatch):
    _, clone = make_clone(tmp_path)
    real, ran = ps._post, []

    def post(ad, repo, remote, sender, to, kind, body, corr, kw):
        if kind == "PROD-BEFORE":
            raise ps._Interrupted(signal.SIGTERM)
        return real(ad, repo, remote, sender, to, kind, body, corr, kw)

    monkeypatch.setattr(ps, "_post", post)
    rc = ps.run_step(["prog"], clone, "origin", "s", "all", "w", runner=lambda argv: ran.append(argv) or (0, "ok"))
    assert rc == 143 and ran == []
    # control: an ordinary failure of the BEFORE post is exit 1 and posts nothing
    monkeypatch.setattr(ps, "_post", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert ps.run_step(["prog"], clone, "origin", "s", "all", "w", runner=lambda argv: (0, "ok")) == 1


def test_an_interrupt_while_the_after_line_is_posted_still_ends_with_after_and_143(tmp_path, monkeypatch):
    _, clone = make_clone(tmp_path)
    real, hit = ps._post, []

    def post(ad, repo, remote, sender, to, kind, body, corr, kw):
        if kind == "PROD-AFTER" and not hit:
            hit.append(1)
            raise ps._Interrupted(signal.SIGTERM)
        return real(ad, repo, remote, sender, to, kind, body, corr, kw)

    monkeypatch.setattr(ps, "_post", post)
    rc = ps.run_step(["prog"], clone, "origin", "s", "all", "w", runner=lambda argv: (0, "ok"))
    assert rc == 143 and [r["kind"] for r in records(clone)] == ["PROD-BEFORE", "PROD-AFTER"]


def test_git_children_of_the_posts_run_in_their_own_session():
    assert ps._Adapter.new_session is True and ml.Adapter.new_session is False


# ---- the plugin-scripts-allow hook (gv-13): reports are silent, anything that executes or writes asks ---------
def _hook():
    import importlib.util
    spec = importlib.util.spec_from_file_location("scripts_allow_hook", ROOT / "hooks" / "foundry-plugin-scripts-allow.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("script,args,expected", [
    ("foundry_deck_report.py", "--in /tmp/x/tick.json", True),
    ("foundry_deck_report.py", "--project-dir /tmp/x --in -", True),
    ("foundry_deck_report.py", "state", True),
    ("foundry_deck_report.py", "pause --until x", False),
    ("foundry_deck_report.py", "pause", False),
    ("foundry_deck_report.py", "resume", False),
    ("foundry_operating_model.py", "render-tick-section TEMPLATE --workspace /w --operator o", True),
    ("foundry_operating_model.py", "render-tick-section A1", True),
    ("foundry_prod_step.py", "--repo /r --remote origin --what x -- true", False),
    ("foundry_prod_step.py", "--help", False),
])
def test_hook_decisions(script, args, expected):
    hook = _hook()
    assert hook.allowed(f"python3 {ROOT}/scripts/{script} {args}", str(ROOT)) is expected


def test_prod_step_is_named_in_the_executes_input_set():
    assert "foundry_prod_step.py" in _hook().EXECUTES_INPUT
