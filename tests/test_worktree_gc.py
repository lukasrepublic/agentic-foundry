"""tests/test_worktree_gc.py — branch-and-worktree-discipline (AC-BWD-2/-3/-6), v1.16.0.

Two layers:
  1. PURE unit tests over `classify_branch` / `_age_days` / the refusal predicates — no git, no
     subprocess, exercises the classification logic in isolation (fast, no fixture repo needed).
  2. A REAL fixture repo (a bare "origin" + a clone, both under a fake $HOME so the --repo-outside-
     home refusal reads correctly either way) driving the shipped CLI end-to-end as a subprocess,
     over the SAME committed `gh` stub every other live seam in this suite uses — never a bespoke
     reimplementation of `git` or `gh`. Covers AC-BWD-6's four fixture classes (a merged branch
     with a remote + a linked worktree, an open-PR branch via the gh stub, an unmerged branch
     without a PR, and `main` itself as `protected`), the dry-run/--apply split, both refusals, and
     the doctor's `branches` advisory line (AC-BWD-3), which imports this module's own classifier.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from conftest import REPO_ROOT, load_module

gc = load_module("scripts/foundry-worktree-gc.py", "foundry_worktree_gc")
doctor = load_module("scripts/foundry-doctor.py", "foundry_doctor")

SCRIPT = os.path.join(REPO_ROOT, "scripts", "foundry-worktree-gc.py")
GH_STUB_DIR = os.path.join(REPO_ROOT, "tests", "fixtures", "gh-stub")


# ================================================================================================ #
# 1. pure classification unit tests
# ================================================================================================ #


def test_protected_wins_over_every_other_signal():
    assert gc.classify_branch(
        "main", protected_names={"main"}, ancestor_merged=True, pr_state="open",
    ) == "protected"


def test_ancestor_merged_is_sufficient_alone():
    assert gc.classify_branch(
        "atom/x", protected_names={"main"}, ancestor_merged=True, pr_state=None,
    ) == "merged"


def test_gh_merged_pr_state_is_sufficient_without_ancestry():
    # the squash/rebase case: tip is not literally an ancestor, but gh reports a merged PR.
    assert gc.classify_branch(
        "atom/x", protected_names={"main"}, ancestor_merged=False, pr_state="merged",
    ) == "merged"


def test_open_pr_when_not_merged():
    assert gc.classify_branch(
        "atom/x", protected_names={"main"}, ancestor_merged=False, pr_state="open",
    ) == "open-pr"


def test_unmerged_no_pr_is_the_conservative_default():
    assert gc.classify_branch(
        "atom/x", protected_names={"main"}, ancestor_merged=False, pr_state=None,
    ) == "unmerged-no-pr"


def test_age_days_computes_from_iso_committerdate():
    ts = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    assert gc._age_days(ts) in (4, 5, 6)  # tolerate a boundary second


def test_age_days_never_raises_on_garbage():
    assert gc._age_days("not-a-date") is None
    assert gc._age_days(None) is None


@pytest.mark.parametrize("name,matches", [
    ("atom/foo", True), ("release/1.16.0", True), ("hotfix/bar", True),
    ("fix/baz", True), ("feat/qux", True), ("docs/readme", True),
    ("main", False), ("random-branch", False), ("wip", False),
])
def test_glob_pattern_membership(name, matches):
    assert gc._matches_any(name, gc.GLOB_PATTERNS) is matches


def test_refuse_outside_home_raises(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    with pytest.raises(gc.GcRefused):
        gc._refuse_outside_home(str(outside))


def test_refuse_outside_home_allows_inside(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    inside = fake_home / "proj"
    inside.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    gc._refuse_outside_home(str(inside))  # must not raise


# ================================================================================================ #
# 2. a real fixture repo, driven end-to-end
# ================================================================================================ #


def _run_git(repo, *args, env=None, check=True):
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                       env=env, timeout=30)
    if check and p.returncode != 0:
        raise AssertionError(f"git -C {repo} {' '.join(args)} failed: {p.stdout}\n{p.stderr}")
    return p


def _make_fixture_repo(home):
    """Builds a bare `origin.git` + a clone `work`, both under `home`, with:
      - `main` — the default branch (one initial commit).
      - `atom/merged-one` — merged into main via --no-ff, pushed to origin, AND checked out into
        its own linked worktree (`wt-merged`) — the "merged branch with a remote" fixture class,
        plus the worktree AC-BWD-2 says `--apply` must also remove.
      - `atom/open-one` — an unmerged commit, pushed to origin (its class is driven by the gh
        stub's GH_STUB_PR_LIST_BY_BRANCH in each test, not baked in here).
      - `atom/stale-one` — an unmerged commit, LOCAL ONLY (no remote), the "unmerged branch
        without a PR" fixture class.
    Returns the `work` clone path.
    """
    home = str(home)
    bare = os.path.join(home, "origin.git")
    work = os.path.join(home, "work")
    env = dict(os.environ)
    env["HOME"] = home
    env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = "Test"
    env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = "test@example.com"

    _run_git(home, "init", "--bare", "-q", bare, env=env)
    _run_git(home, "clone", "-q", bare, work, env=env)
    _run_git(work, "checkout", "-q", "-b", "main", env=env)
    with open(os.path.join(work, "README.md"), "w", encoding="utf-8") as f:
        f.write("init\n")
    _run_git(work, "add", "README.md", env=env)
    _run_git(work, "commit", "-q", "-m", "initial", env=env)
    _run_git(work, "push", "-q", "-u", "origin", "main", env=env)
    _run_git(work, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main", env=env)

    # atom/merged-one -- merged into main, pushed, given its own linked worktree.
    _run_git(work, "checkout", "-q", "-b", "atom/merged-one", env=env)
    with open(os.path.join(work, "merged.txt"), "w", encoding="utf-8") as f:
        f.write("merged\n")
    _run_git(work, "add", "merged.txt", env=env)
    _run_git(work, "commit", "-q", "-m", "merged change", env=env)
    _run_git(work, "checkout", "-q", "main", env=env)
    _run_git(work, "merge", "-q", "--no-ff", "-m", "merge atom/merged-one", "atom/merged-one", env=env)
    _run_git(work, "push", "-q", "origin", "main", env=env)
    _run_git(work, "push", "-q", "-u", "origin", "atom/merged-one", env=env)
    wt_merged = os.path.join(home, "wt-merged")
    _run_git(work, "worktree", "add", "-q", wt_merged, "atom/merged-one", env=env)

    # atom/open-one -- unmerged, pushed (classified by the gh stub in each test).
    _run_git(work, "checkout", "-q", "-b", "atom/open-one", "main", env=env)
    with open(os.path.join(work, "open.txt"), "w", encoding="utf-8") as f:
        f.write("open\n")
    _run_git(work, "add", "open.txt", env=env)
    _run_git(work, "commit", "-q", "-m", "open change", env=env)
    _run_git(work, "checkout", "-q", "main", env=env)
    _run_git(work, "push", "-q", "-u", "origin", "atom/open-one", env=env)

    # atom/stale-one -- unmerged, local only.
    _run_git(work, "checkout", "-q", "-b", "atom/stale-one", "main", env=env)
    with open(os.path.join(work, "stale.txt"), "w", encoding="utf-8") as f:
        f.write("stale\n")
    _run_git(work, "add", "stale.txt", env=env)
    _run_git(work, "commit", "-q", "-m", "stale change", env=env)
    _run_git(work, "checkout", "-q", "main", env=env)

    return work


def _run_gc(work_dir, home, *extra_args, **stub_vars):
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["PATH"] = f"{GH_STUB_DIR}{os.pathsep}{env['PATH']}"
    for k, v in stub_vars.items():
        if v is not None:
            env[k] = str(v)
    args = [sys.executable, SCRIPT, "--repo", str(work_dir), *extra_args]
    return subprocess.run(args, capture_output=True, text=True, timeout=60, env=env)


@pytest.fixture()
def fixture_repo(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    work = _make_fixture_repo(home)
    return work, home


def test_dry_run_classifies_all_four_fixture_classes(fixture_repo):
    work, home = fixture_repo
    by_branch = json.dumps({"atom/open-one": {"open": [{"number": 7}]}})
    p = _run_gc(work, home, "--dry-run", GH_STUB_PR_LIST_BY_BRANCH=by_branch)
    assert p.returncode == 0, p.stdout + p.stderr
    doc = json.loads(p.stdout)
    assert doc["status"] == "ok"
    assert doc["dry_run"] is True
    rows = {r["name"]: r for r in doc["branches"]}
    assert rows["main"]["class"] == "protected"
    assert rows["atom/merged-one"]["class"] == "merged"
    assert rows["atom/merged-one"]["remote"] is True
    assert rows["atom/merged-one"]["worktree"] is not None
    assert rows["atom/open-one"]["class"] == "open-pr"
    assert rows["atom/stale-one"]["class"] == "unmerged-no-pr"
    assert rows["atom/stale-one"]["remote"] is False
    assert rows["atom/stale-one"]["age_days"] is not None
    assert doc["counts"]["merged"] == 1
    assert doc["counts"]["open-pr"] == 1
    assert doc["counts"]["unmerged-no-pr"] == 1


def test_dry_run_is_the_default_with_no_flag_at_all(fixture_repo):
    work, home = fixture_repo
    p = _run_gc(work, home)  # neither --dry-run nor --apply
    assert p.returncode == 0, p.stdout + p.stderr
    doc = json.loads(p.stdout)
    assert doc["dry_run"] is True


def test_dry_run_never_deletes_anything(fixture_repo):
    work, home = fixture_repo
    before = _run_git(work, "branch", "-a").stdout
    p = _run_gc(work, home, "--dry-run")
    assert p.returncode == 0, p.stdout + p.stderr
    after = _run_git(work, "branch", "-a").stdout
    assert before == after
    # the linked worktree for atom/merged-one must still be present
    wt_list = _run_git(work, "worktree", "list", "--porcelain").stdout
    assert "atom/merged-one" in wt_list or "wt-merged" in wt_list


def test_apply_deletes_only_the_merged_class(fixture_repo):
    work, home = fixture_repo
    by_branch = json.dumps({"atom/open-one": {"open": [{"number": 7}]}})
    p = _run_gc(work, home, "--apply", GH_STUB_PR_LIST_BY_BRANCH=by_branch)
    assert p.returncode == 0, p.stdout + p.stderr
    doc = json.loads(p.stdout)
    assert doc["dry_run"] is False
    assert doc["deleted_local_branches"] == ["atom/merged-one"]
    assert doc["deleted_remote_branches"] == ["atom/merged-one"]
    assert any("wt-merged" in w for w in doc["removed_worktrees"])

    branches = _run_git(work, "branch", "-a").stdout
    assert "atom/merged-one" not in branches
    assert "atom/open-one" in branches  # open-pr never deleted
    assert "atom/stale-one" in branches  # unmerged-no-pr never deleted
    assert not os.path.isdir(os.path.join(home, "wt-merged"))


def test_refuses_repo_outside_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside-repo"
    outside.mkdir()
    _run_git(outside, "init", "-q", "-b", "main")
    p = _run_gc(outside, home, "--dry-run")
    assert p.returncode == 1, p.stdout + p.stderr
    doc = json.loads(p.stdout)
    assert doc["status"] == "refused"
    assert "home" in doc["reason"]


def test_refuses_a_dirty_working_tree(fixture_repo):
    work, home = fixture_repo
    with open(os.path.join(work, "README.md"), "a", encoding="utf-8") as f:
        f.write("dirty\n")
    p = _run_gc(work, home, "--dry-run")
    assert p.returncode == 1, p.stdout + p.stderr
    doc = json.loads(p.stdout)
    assert doc["status"] == "refused"
    assert "dirty" in doc["reason"]


def test_no_gh_flag_classifies_unmerged_branches_as_no_pr_never_open(fixture_repo):
    # without gh, an unmerged branch can never be told apart from "no PR" -- the conservative
    # (never a false-negative-delete-risk) direction.
    work, home = fixture_repo
    p = _run_gc(work, home, "--dry-run", "--no-gh")
    assert p.returncode == 0, p.stdout + p.stderr
    doc = json.loads(p.stdout)
    rows = {r["name"]: r for r in doc["branches"]}
    assert rows["atom/open-one"]["class"] == "unmerged-no-pr"


def test_gh_unavailable_degrades_rather_than_raising(fixture_repo):
    work, home = fixture_repo
    env = dict(os.environ)
    env["HOME"] = str(home)
    # deliberately NOT prepending the gh stub -- gh may or may not be on the real PATH here;
    # either way the run must complete (never crash) and never over-report `merged`.
    args = [sys.executable, SCRIPT, "--repo", str(work), "--dry-run"]
    p = subprocess.run(args, capture_output=True, text=True, timeout=60, env=env)
    assert p.returncode == 0, p.stdout + p.stderr
    doc = json.loads(p.stdout)
    rows = {r["name"]: r for r in doc["branches"]}
    assert rows["atom/merged-one"]["class"] == "merged"  # ancestry alone still finds this


# ================================================================================================ #
# AC-BWD-3 — the doctor's `branches` advisory line, computed by importing this module's classifier
# ================================================================================================ #


def test_doctor_branches_advisory_line_over_a_real_repo(fixture_repo):
    work, home = fixture_repo
    ok, detail = doctor.check_branches_advisory(project_dir=str(work))
    assert ok is True or ok is doctor.ADVISORY
    assert "merged-not-deleted" in detail
    assert "stale worktrees" in detail
    assert "1 merged-not-deleted" in detail
    assert "1 stale worktrees" in detail


def test_doctor_branches_advisory_never_red_on_a_non_git_directory(tmp_path):
    ok, detail = doctor.check_branches_advisory(project_dir=str(tmp_path))
    assert ok is True or ok is doctor.ADVISORY
    assert "not a git checkout" in detail


def test_doctor_branches_advisory_never_raises_on_a_broken_repo(tmp_path, monkeypatch):
    # a `.git` directory that is not actually a valid repo -- the probe must degrade, never crash.
    broken = tmp_path / "broken"
    (broken / ".git").mkdir(parents=True)
    ok, detail = doctor.check_branches_advisory(project_dir=str(broken))
    assert ok is True or ok is doctor.ADVISORY


def test_doctor_row_renders_outside_the_run_call_registrations(tmp_path):
    # mirrors the agent-teams precedent this atom follows: the row must NOT be a `_run("<name>",
    # ...)` call-site literal, so it stays outside tests/test_doc_claims.py's probe-count bijection.
    src = os.path.join(REPO_ROOT, "scripts", "foundry-doctor.py")
    with open(src, encoding="utf-8") as f:
        text = f.read()
    assert '_run("branches"' not in text
    assert '_render_row("branches"' in text
