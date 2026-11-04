"""gv-12: the heavy-slot lock wrapper and the label-scoped hygiene sweep. Locks live in tmp dirs;
worktrees are throwaway repos under tmp_path; no real resource is touched."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

from conftest import REPO_ROOT, load_module

HEAVY = os.path.join(REPO_ROOT, "scripts", "foundry-deck-heavy")
REAP = os.path.join(REPO_ROOT, "hooks", "foundry-env-reap.sh")
hy = load_module("scripts/foundry-env-hygiene.py", "foundry_env_hygiene")


def heavy(args, lock_dir, **kw):
    return subprocess.Popen([sys.executable, HEAVY, "--lock-dir", str(lock_dir), *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kw)


def waited(stderr):
    m = re.search(r"waited ([0-9.]+)s", stderr)
    return float(m.group(1)) if m else None


def test_third_command_waits_with_two_slots_and_reports_it(tmp_path):
    procs = [heavy(["--slots", "2", "--", "sleep", "1.2"], tmp_path) for _ in range(2)]
    time.sleep(0.4)                       # both slots are held before the third starts
    third = heavy(["--slots", "2", "--", "true"], tmp_path)
    outs = [p.communicate(timeout=20) for p in procs + [third]]
    assert all(p.returncode == 0 for p in procs + [third])
    first_two = [waited(o[1]) for o in outs[:2]]
    assert all(w is not None and w < 0.5 for w in first_two)       # control: they did not wait
    w3 = waited(outs[2][1])
    assert w3 is not None and w3 >= 0.5                            # the wait is reported
    assert "slot" in outs[2][1]


def test_exit_status_is_the_wrapped_commands(tmp_path):
    p = heavy(["--slots", "1", "--", sys.executable, "-c", "import sys; sys.exit(7)"], tmp_path)
    p.communicate(timeout=20)
    assert p.returncode == 7


def test_timeout_reports_and_exits_nonzero_without_running(tmp_path):
    holder = heavy(["--slots", "1", "--", "sleep", "2"], tmp_path)
    time.sleep(0.4)
    marker = tmp_path / "ran"
    late = heavy(["--slots", "1", "--timeout", "0.5", "--", "touch", str(marker)], tmp_path)
    _, err = late.communicate(timeout=20)
    assert late.returncode == 75
    assert "timed out" in err
    assert not marker.exists()
    holder.communicate(timeout=20)


def test_killed_wrapper_keeps_the_slot_while_the_heavy_child_runs_then_frees_it(tmp_path):
    p = heavy(["--slots", "1", "--", "sleep", "2"], tmp_path)
    time.sleep(0.5)
    p.kill()                               # SIGKILL the wrapper; its child keeps running
    p.wait(timeout=10)
    held = heavy(["--slots", "1", "--timeout", "0.4", "--", "true"], tmp_path)
    held.communicate(timeout=20)
    assert held.returncode == 75           # the child still holds the slot
    nxt = heavy(["--slots", "1", "--timeout", "10", "--", "true"], tmp_path)
    nxt.communicate(timeout=30)
    assert nxt.returncode == 0             # the child ended: the slot is free again


def test_sigterm_is_forwarded_and_the_wrapper_exits_143(tmp_path):
    p = heavy(["--slots", "1", "--", "sleep", "30"], tmp_path)
    time.sleep(0.5)
    p.terminate()
    p.communicate(timeout=10)
    assert p.returncode == 143
    nxt = heavy(["--slots", "1", "--timeout", "5", "--", "true"], tmp_path)
    nxt.communicate(timeout=20)
    assert nxt.returncode == 0


def test_not_executable_is_126_and_missing_is_127(tmp_path):
    f = tmp_path / "noexec"
    f.write_text("x")
    f.chmod(0o644)
    p = heavy(["--slots", "1", "--", str(f)], tmp_path)
    p.communicate(timeout=10)
    assert p.returncode == 126
    q = heavy(["--slots", "1", "--", str(tmp_path / "missing")], tmp_path)
    q.communicate(timeout=10)
    assert q.returncode == 127


def test_no_lock_dir_and_no_git_or_xdg_is_refused(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "XDG_RUNTIME_DIR"}
    p = subprocess.run([sys.executable, HEAVY, "--slots", "1", "--", "true"], cwd=str(tmp_path),
                       capture_output=True, text=True, env={**env, "GIT_CEILING_DIRECTORIES": str(tmp_path.parent)})
    assert p.returncode == 2 and "--lock-dir" in p.stderr


def test_slots_come_from_policy_when_not_given(tmp_path):
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path)}
    (tmp_path / ".foundry").mkdir()
    (tmp_path / ".foundry" / "operating-model.yaml").write_text("deck:\n  heavy_slots: 3\n")
    p = subprocess.run([sys.executable, HEAVY, "--lock-dir", str(tmp_path / "l"), "--", "true"],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    assert "/3" in p.stderr


# ----------------------------------------------------------------------------------- hygiene
def git(*a, cwd):
    return subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True, check=True,
                          env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
                               "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t",
                               "GIT_COMMITTER_EMAIL": "t@e"}).stdout


def make_repo(tmp_path):
    """A bare 'origin', a clone with main pushed, and helper to add labelled worktrees."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True, capture_output=True)
    git("checkout", "-q", "-b", "main", cwd=repo)
    (repo / "f").write_text("x")
    git("add", "f", cwd=repo)
    git("commit", "-q", "-m", "init", cwd=repo)
    git("push", "-q", "-u", "origin", "main", cwd=repo)
    return repo


def dead_pid():
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


def add_wt(repo, name, label, pushed=True, commit=False, pid=None, start="Thu Jan  1 00:00:00 1970"):
    wt = repo.parent / "wts" / name
    git("worktree", "add", "-q", "-b", name, str(wt), "main", cwd=repo)
    if pushed:
        git("push", "-q", "-u", "origin", name, cwd=wt)
    if commit:
        (wt / "g").write_text("y")
        git("add", "g", cwd=wt)
        git("commit", "-q", "-m", "wip", cwd=wt)
    assert hy.mark_worktree(str(wt), label, pid=dead_pid() if pid is None else pid, start=start)
    return wt


def test_reaper_removes_only_the_labelled_pushed_worktree(tmp_path):
    repo = make_repo(tmp_path)
    mine = add_wt(repo, "mine", "proj-a")
    foreign = add_wt(repo, "foreign", "proj-b")
    unpushed = add_wt(repo, "unpushed", "proj-a", commit=True)
    unmarked = repo.parent / "wts" / "unmarked"
    git("worktree", "add", "-q", "-b", "unmarked", str(unmarked), "main", cwd=repo)
    cands = hy.sample_worktrees(str(repo))
    assert {os.path.basename(c["id"]) for c in cands} == {"mine", "foreign", "unpushed", "unmarked"}  # main excluded
    reap = hy.decide_reap(cands, "proj-a")
    assert [os.path.basename(c["id"]) for c in reap] == ["mine"]
    for c in reap:
        hy.reap_resource(c)
    assert not mine.exists()
    assert foreign.exists() and unpushed.exists() and unmarked.exists()   # controls survive


def test_locked_and_dirty_worktrees_survive(tmp_path):
    repo = make_repo(tmp_path)
    locked = add_wt(repo, "locked", "proj-a")
    dirty = add_wt(repo, "dirty", "proj-a")
    git("worktree", "lock", str(locked), cwd=repo)
    (dirty / "scratch.txt").write_text("unsaved")
    reap = hy.decide_reap(hy.sample_worktrees(str(repo)), "proj-a")
    assert reap == []


def test_live_owner_keeps_the_worktree_but_a_recycled_pid_does_not(tmp_path):
    repo = make_repo(tmp_path)
    alive, _c, start = hy._probe_pid(os.getpid())
    assert alive
    live = add_wt(repo, "live", "proj-a", pid=os.getpid(), start=start)
    recycled = add_wt(repo, "recycled", "proj-a", pid=os.getpid(), start="Thu Jan  1 00:00:00 1970")
    reap = hy.decide_reap(hy.sample_worktrees(str(repo)), "proj-a")
    assert [os.path.basename(c["id"]) for c in reap] == ["recycled"]
    assert live.exists()


def test_legacy_marker_without_a_pid_survives(tmp_path):
    repo = make_repo(tmp_path)
    wt = add_wt(repo, "legacy", "proj-a")
    gd = git("rev-parse", "--absolute-git-dir", cwd=wt).strip()
    open(os.path.join(gd, "foundry-owner"), "w").write("proj-a\n")
    assert hy.decide_reap(hy.sample_worktrees(str(repo)), "proj-a") == []


def test_mark_refuses_a_different_label_and_has_no_session_fallback(tmp_path):
    repo = make_repo(tmp_path)
    wt = add_wt(repo, "w", "proj-a")
    assert hy.mark_worktree(str(wt), "proj-b") is False                       # not overwritten
    assert hy.mark_worktree(str(wt), "proj-a", pid=dead_pid(), start="x") is True   # same label: refresh ok
    assert hy.mark_worktree(str(wt), "bad label\n") is False
    env = {**os.environ, "CLAUDE_SESSION_ID": "proj-z"}
    p = subprocess.run([sys.executable, os.path.join(REPO_ROOT, "scripts", "foundry-env-hygiene.py"),
                        "mark-worktree", "--path", str(wt)], capture_output=True, text=True, env=env)
    assert p.returncode == 2          # no --label: the session id is not used


def test_worktree_with_ignored_files_survives_unless_included(tmp_path):
    repo = make_repo(tmp_path)
    wt = add_wt(repo, "ign", "proj-a")
    (repo / ".gitignore").write_text("*.cache\n")
    git("add", ".gitignore", cwd=repo)
    git("commit", "-q", "-m", "ign", cwd=repo)
    git("push", "-q", cwd=repo)
    wt2 = add_wt(repo, "ign2", "proj-a")
    (wt2 / "data.cache").write_text("precious")
    assert [os.path.basename(c["id"]) for c in hy.decide_reap(hy.sample_worktrees(str(repo)), "proj-a")] == ["ign"]
    inc = hy.decide_reap(hy.sample_worktrees(str(repo), include_ignored=True), "proj-a")
    assert {os.path.basename(c["id"]) for c in inc} == {"ign", "ign2"}


def test_the_project_dir_and_the_cwd_are_never_reaped(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    wt = add_wt(repo, "here", "proj-a")
    monkeypatch.chdir(wt)
    assert hy.decide_reap(hy.sample_worktrees(str(repo)), "proj-a") == []
    monkeypatch.chdir(tmp_path)
    assert hy.decide_reap(hy.sample_worktrees(str(wt)), "proj-a") == []          # project_dir is the worktree


def test_no_upstream_means_unknown_and_survives(tmp_path):
    repo = make_repo(tmp_path)
    wt = add_wt(repo, "local-only", "proj-a", pushed=False)
    assert hy.decide_reap(hy.sample_worktrees(str(repo)), "proj-a") == []
    assert wt.exists()


def test_scratch_scope_is_label_exact(tmp_path):
    root = tmp_path / ".foundry" / "scratch"
    (root / "proj-a").mkdir(parents=True)
    (root / "proj-a" / "out.txt").write_text("x")
    (root / "proj-b").mkdir()
    cands = hy.sample_scratch(str(tmp_path))
    reap = hy.decide_reap(cands, "proj-a")
    assert [os.path.basename(c["id"]) for c in reap] == ["proj-a"]
    for c in reap:
        hy.reap_resource(c)
    assert not (root / "proj-a").exists() and (root / "proj-b").exists()


def test_scratch_symlink_is_never_followed(tmp_path):
    root = tmp_path / ".foundry" / "scratch"
    root.mkdir(parents=True)
    target = tmp_path / "precious"
    target.mkdir()
    (target / "keep").write_text("x")
    os.symlink(target, root / "proj-a")
    assert hy.decide_reap(hy.sample_scratch(str(tmp_path)), "proj-a") == []
    assert (target / "keep").exists()


def test_subagent_stop_sweeps_by_label_through_the_hook(tmp_path):
    repo = make_repo(tmp_path)
    mine = add_wt(repo, "mine", "proj-a")
    foreign = add_wt(repo, "foreign", "proj-b")
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo), "CLAUDE_PLUGIN_ROOT": REPO_ROOT}
    env.pop("CLAUDE_SESSION_ID", None)
    p = subprocess.run(["bash", REAP, "--subagent-stop"], input=json.dumps({"project_label": "proj-a"}),
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    assert not mine.exists() and foreign.exists()


def test_subagent_stop_without_a_label_sweeps_nothing(tmp_path):
    repo = make_repo(tmp_path)
    mine = add_wt(repo, "mine", "proj-a")
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo), "CLAUDE_PLUGIN_ROOT": REPO_ROOT,
           "CLAUDE_SESSION_ID": "proj-a"}
    env.pop("FOUNDRY_PROJECT_LABEL", None)
    p = subprocess.run(["bash", REAP, "--subagent-stop"], input=json.dumps({"session_id": "proj-a"}),
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0
    assert mine.exists()            # the parent's session id is not a label: a subagent stop must not reap it


def test_brief_hygiene_text_carries_f1_to_f4():
    t = open(os.path.join(REPO_ROOT, "context", "brief-hygiene.md"), encoding="utf-8").read().lower()
    for needle in ("teardown", "mount", "push", "worktree", "stash", "diff"):
        assert needle in t
    assert ("c" + "tx") not in t


# ----------------------------------------------------------------------- permission floor tiers
def _allow_decision(tmp_path, script, args):
    root = tmp_path / "plugin"
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / script).write_text("print('x')\n")
    event = {"tool_name": "Bash", "tool_input": {"command": f"python3 {root}/scripts/{script} {args}"}}
    p = subprocess.run([sys.executable, os.path.join(REPO_ROOT, "hooks", "foundry-plugin-scripts-allow.py")],
                       input=json.dumps(event), capture_output=True, text=True,
                       env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(root)})
    return "allow" if "allow" in p.stdout else None


def test_deck_resources_measuring_is_silent_but_baseline_reset_asks(tmp_path):
    assert _allow_decision(tmp_path, "foundry_deck_resources.py", "--path /w") == "allow"
    assert _allow_decision(tmp_path, "foundry_deck_resources.py", "--reset-oom-baseline") is None
    assert _allow_decision(tmp_path, "foundry_deck_resources.py", "--reset=1") is None   # abbreviation


def test_floor_tiers_both_scripts_and_mirrors_agree():
    docs = json.load(open(os.path.join(REPO_ROOT, "docs", "permission-floor.json")))
    cli = json.load(open(os.path.join(REPO_ROOT, "cli", "permission-floor.json")))
    assert docs == cli
    tier = {e["rule"].split("/scripts/")[1].split(":")[0]: e["tier"] for e in docs["entries"] if "/scripts/" in e["rule"]}
    assert tier["foundry_deck_resources.py"] == "allow"
    assert tier["foundry-deck-heavy"] == "ask"


def test_hygiene_mark_and_labelled_reap_are_never_silent(tmp_path):
    for args in ("mark-worktree --path /w --label a", "reap --label a", "reap --lab=a"):
        assert _allow_decision(tmp_path, "foundry-env-hygiene.py", args) is None
    assert _allow_decision(tmp_path, "foundry-env-hygiene.py", "status") == "allow"


def test_from_json_is_never_silent_including_abbreviations(tmp_path):
    for args in ("--from-json /x.json", "--from=/x.json", "--from /x.json"):
        assert _allow_decision(tmp_path, "foundry_deck_resources.py", args) is None
