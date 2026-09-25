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
import re
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


def _tip_sha(work, branch):
    return _run_git(work, "rev-parse", branch).stdout.strip()


@pytest.fixture()
def fixture_repo(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    work = _make_fixture_repo(home)
    return work, home


def test_dry_run_classifies_all_four_fixture_classes(fixture_repo):
    work, home = fixture_repo
    open_tip = _tip_sha(work, "atom/open-one")
    by_branch = json.dumps({"atom/open-one": {"open": [{"number": 7, "headRefOid": open_tip}]}})
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
    open_tip = _tip_sha(work, "atom/open-one")
    by_branch = json.dumps({"atom/open-one": {"open": [{"number": 7, "headRefOid": open_tip}]}})
    p = _run_gc(work, home, "--apply", GH_STUB_PR_LIST_BY_BRANCH=by_branch)
    assert p.returncode == 0, p.stdout + p.stderr
    doc = json.loads(p.stdout)
    assert doc["dry_run"] is False
    assert doc["deleted_local_branches"] == ["atom/merged-one"]
    assert doc["deleted_remote_branches"] == ["atom/merged-one"]
    assert any("wt-merged" in w for w in doc["removed_worktrees"])
    # atom/merged-one is a real --no-ff git-ancestor merge -- `git branch -d` succeeds outright,
    # never needing the -D force fallback (round-2 review finding 2's non-squash control).
    assert doc["force_deleted"] == []

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


# ================================================================================================ #
# Round-2 review fixes (PR #205 review round 1)
# ================================================================================================ #
# 1. [Block] a stale merged-PR record for a REUSED branch name must never make a later, genuinely
#    unmerged push at that same name look merged -- gh_pr_info only accepts a row whose headRefOid
#    equals the branch's OWN current tip.
# 2. [Risk] `git branch -d` first (git's own merged/ff check, second line of defense); `-D` is a
#    narrated fallback, ONLY on that refusal.
# 3. [Risk] the permission-floor rows are argv PREFIX rules -- every documented invocation must put
#    the mode flag (`--dry-run`/`--apply`) FIRST, or the rule never matches.


def _make_squash_fixture_repo(home):
    """A SEPARATE, minimal fixture (not `_make_fixture_repo`'s four-class repo, so the broad
    dry-run/counts tests above stay unaffected): `main` plus ONE branch, `atom/squash-one`, whose
    tip is deliberately NOT an ancestor of `main` (an independent commit lands on `main` instead,
    simulating a real squash-merge's new, unrelated commit) even though gh will report it as
    merged via a headRefOid-matched PR. Exercises the `-d` refusal -> `-D` fallback path (finding
    2) together with the tip-matched gh signal (finding 1)."""
    home = str(home)
    bare = os.path.join(home, "squash-origin.git")
    work = os.path.join(home, "squash-work")
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

    _run_git(work, "checkout", "-q", "-b", "atom/squash-one", env=env)
    with open(os.path.join(work, "squash.txt"), "w", encoding="utf-8") as f:
        f.write("squash\n")
    _run_git(work, "add", "squash.txt", env=env)
    _run_git(work, "commit", "-q", "-m", "squash change", env=env)
    # Deliberately pushed WITHOUT `-u` -- no local upstream tracking. `git branch -d`'s own safety
    # check accepts "merged into either HEAD or the branch's configured upstream"; WITH upstream
    # tracking the branch's tip trivially satisfies that against its own remote-tracking ref even
    # though it is genuinely unmerged into HEAD, and `-d` would silently succeed -- defeating this
    # fixture's whole point. No upstream tracking makes HEAD (`main`) the only reference point, so
    # `-d` refuses for real (verified against a live `git` invocation, not asserted from memory).
    _run_git(work, "push", "-q", "origin", "atom/squash-one", env=env)

    # main advances with an UNRELATED commit -- atom/squash-one's tip is never an ancestor of it,
    # exactly like a real squash-merge (GitHub writes a brand-new commit onto main).
    _run_git(work, "checkout", "-q", "main", env=env)
    with open(os.path.join(work, "unrelated.txt"), "w", encoding="utf-8") as f:
        f.write("simulated squash-merge landing\n")
    _run_git(work, "add", "unrelated.txt", env=env)
    _run_git(work, "commit", "-q", "-m", "squash-merge atom/squash-one (simulated)", env=env)
    _run_git(work, "push", "-q", "origin", "main", env=env)

    return work


@pytest.fixture()
def squash_fixture_repo(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    work = _make_squash_fixture_repo(home)
    return work, home


def test_stale_merged_pr_for_a_reused_branch_name_never_counts_as_merged(fixture_repo):
    # finding 1: a MERGED PR row exists for the branch name, but its headRefOid does NOT match
    # the branch's current tip (a stale record left behind by an earlier push at the same reused
    # name) -- must classify unmerged-no-pr, never merged.
    work, home = fixture_repo
    by_branch = json.dumps({
        "atom/open-one": {"merged": [{"number": 41, "headRefOid": "0" * 40}]},
    })
    p = _run_gc(work, home, "--dry-run", GH_STUB_PR_LIST_BY_BRANCH=by_branch)
    assert p.returncode == 0, p.stdout + p.stderr
    doc = json.loads(p.stdout)
    rows = {r["name"]: r for r in doc["branches"]}
    assert rows["atom/open-one"]["class"] == "unmerged-no-pr"
    assert rows["atom/open-one"]["pr_number"] is None


def test_tip_matched_merged_pr_counts_as_merged_even_without_ancestry(squash_fixture_repo):
    work, home = squash_fixture_repo
    tip = _tip_sha(work, "atom/squash-one")
    by_branch = json.dumps({"atom/squash-one": {"merged": [{"number": 99, "headRefOid": tip}]}})
    p = _run_gc(work, home, "--dry-run", GH_STUB_PR_LIST_BY_BRANCH=by_branch)
    assert p.returncode == 0, p.stdout + p.stderr
    doc = json.loads(p.stdout)
    rows = {r["name"]: r for r in doc["branches"]}
    assert rows["atom/squash-one"]["class"] == "merged"
    assert rows["atom/squash-one"]["pr_number"] == 99


def test_squash_merged_branch_falls_back_to_force_delete_and_narrates_the_pr(squash_fixture_repo):
    # finding 2: `git branch -d` refuses (not an ancestor) -> falls back to `-D`, and the record
    # narrates why, naming the gh/headRefOid-verified PR number.
    work, home = squash_fixture_repo
    tip = _tip_sha(work, "atom/squash-one")
    by_branch = json.dumps({"atom/squash-one": {"merged": [{"number": 99, "headRefOid": tip}]}})
    # sanity: -d alone really would refuse on this fixture (not an ancestor of main).
    d_probe = _run_git(work, "branch", "-d", "atom/squash-one", check=False)
    assert d_probe.returncode != 0, "fixture is not exercising the -d refusal path"

    p = _run_gc(work, home, "--apply", GH_STUB_PR_LIST_BY_BRANCH=by_branch)
    assert p.returncode == 0, p.stdout + p.stderr
    doc = json.loads(p.stdout)
    assert doc["deleted_local_branches"] == ["atom/squash-one"]
    assert doc["deleted_remote_branches"] == ["atom/squash-one"]
    assert doc["force_deleted"] == [
        {"name": "atom/squash-one", "reason": "squash-merged, PR #99 verified by headRefOid"}
    ]
    branches = _run_git(work, "branch", "-a").stdout
    assert "atom/squash-one" not in branches


# ------------------------------------------------------------------------------------------------ #
# finding 3 -- every documented invocation puts the mode flag FIRST, matching the floor rule prefix
# ------------------------------------------------------------------------------------------------ #

_INVOCATION_RE = re.compile(r'foundry-worktree-gc\.py"?\s+(--[\w-]+)')

_DOCUMENTED_SOURCES = [
    os.path.join(REPO_ROOT, "scripts", "foundry-worktree-gc.py"),
    os.path.join(REPO_ROOT, "context", "branch-discipline.md"),
    os.path.join(REPO_ROOT, "docs", "how-to", "branching-and-cleanup.md"),
    os.path.join(REPO_ROOT, "skills", "cut-release", "SKILL.md"),
]


def _floor_rule_prefixes():
    """The two `scripts/foundry-worktree-gc.py` rule bodies from the shipped floor map, each
    reduced to the argument token right after the script name (e.g. "--dry-run")."""
    with open(os.path.join(REPO_ROOT, "docs", "permission-floor.json"), encoding="utf-8") as f:
        doc = json.load(f)
    tokens = set()
    for entry in doc["entries"]:
        m = re.search(r"foundry-worktree-gc\.py (--\S+?):\*\)$", entry["rule"])
        if m:
            tokens.add(m.group(1))
    return tokens


def test_documented_invocations_match_the_floor_rules_argv_prefix():
    floor_tokens = _floor_rule_prefixes()
    assert floor_tokens == {"--dry-run", "--apply"}, floor_tokens  # sanity: both rows present

    found_any = False
    for path in _DOCUMENTED_SOURCES:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for m in _INVOCATION_RE.finditer(text):
            found_any = True
            token = m.group(1)
            assert token in floor_tokens, (
                f"{path}: documented invocation's first argument is {token!r}, not one of "
                f"{sorted(floor_tokens)} -- the floor rule is an argv PREFIX and will never match "
                f"an invocation that puts --repo (or anything else) first"
            )
    assert found_any, "no documented foundry-worktree-gc.py invocation found at all -- test is vacuous"


# ── ER #244 ──────────────────────────────────────────────────────────────────────────────────────

def _env(home):
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = "Test"
    env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = "test@example.com"
    return env


def _branch_with_commit(work, home, name, fname, *, merge=False, push=False):
    env = _env(home)
    _run_git(work, "checkout", "-q", "-b", name, "main", env=env)
    with open(os.path.join(work, fname), "w", encoding="utf-8") as f:
        f.write(name + "\n")
    _run_git(work, "add", fname, env=env)
    _run_git(work, "commit", "-q", "-m", name, env=env)
    _run_git(work, "checkout", "-q", "main", env=env)
    if merge:
        _run_git(work, "merge", "-q", "--no-ff", "-m", f"merge {name}", name, env=env)
        _run_git(work, "push", "-q", "origin", "main", env=env)
    if push:
        _run_git(work, "push", "-q", "-u", "origin", name, env=env)


def test_json_reports_the_filter_and_include_widens_it(fixture_repo):
    """(a) a ref outside the glob set is counted as filtered_out, never silently dropped; --include
    brings its prefix into scope and it classifies like any other branch."""
    work, home = fixture_repo
    _branch_with_commit(work, home, "spec/merged-two", "spec.txt", merge=True)
    doc = json.loads(_run_gc(work, home, "--dry-run", "--no-gh").stdout)
    assert "spec/merged-two" not in {r["name"] for r in doc["branches"]}
    assert doc["filtered_out_refs"] >= 1 and doc["scanned_refs"] > doc["filtered_out_refs"]
    assert "spec/*" not in doc["patterns"]
    doc2 = json.loads(_run_gc(work, home, "--dry-run", "--no-gh", "--include", "spec/*").stdout)
    rows = {r["name"]: r for r in doc2["branches"]}
    assert rows["spec/merged-two"]["class"] == "merged"
    assert "spec/*" in doc2["patterns"]
    assert doc2["filtered_out_refs"] == doc["filtered_out_refs"] - 1


def test_prune_runs_first_so_an_upstream_deleted_branch_is_never_a_candidate(fixture_repo):
    """(4) the 16-phantom case: the branch is gone upstream, its remote-tracking ref lingers."""
    work, home = fixture_repo
    bare = os.path.join(str(home), "origin.git")
    _run_git(bare, "update-ref", "-d", "refs/heads/atom/merged-one", env=_env(home))
    doc = json.loads(_run_gc(work, home, "--dry-run", "--no-gh").stdout)
    # --dry-run is READ-ONLY (its floor row is `allow`): it reads the would-prune set, changes no ref
    assert doc["prune"]["status"] == "dry-run" and doc["prune"]["would_prune"] == 1
    row = {r["name"]: r for r in doc["branches"]}["atom/merged-one"]
    assert row["remote"] is False, "a would-prune ref is classified as gone upstream"
    assert _run_git(work, "rev-parse", "-q", "--verify", "refs/remotes/origin/atom/merged-one").returncode == 0, \
        "the dry run must not delete the remote-tracking ref"
    doc2 = json.loads(_run_gc(work, home, "--apply", "--no-gh").stdout)
    assert doc2["prune"]["status"] == "ok" and doc2["prune"]["pruned"] == 1
    assert "atom/merged-one" not in doc2["deleted_remote_branches"] and doc2["failed"] == []


def test_apply_records_failures_and_reports_partial_never_ok(fixture_repo):
    """(c) a remote delete that fails is recorded with its reason and turns status to `partial`;
    a remote ref already gone upstream is named `remote_already_absent`, not a success."""
    work, home = fixture_repo
    bare = os.path.join(str(home), "origin.git")
    env = _env(home)
    # a merged branch whose upstream copy is gone (a phantom, kept by --no-prune)
    _branch_with_commit(work, home, "atom/phantom", "phantom.txt", merge=True, push=True)
    _run_git(bare, "update-ref", "-d", "refs/heads/atom/phantom", env=env)
    # a merged branch the remote REFUSES to delete (a pre-receive hook on the bare origin)
    _branch_with_commit(work, home, "atom/locked", "locked.txt", merge=True, push=True)
    hook = os.path.join(bare, "hooks", "pre-receive")
    with open(hook, "w", encoding="utf-8") as f:
        f.write('#!/bin/sh\nwhile read o n r; do [ "$r" = "refs/heads/atom/locked" ] && { echo "locked by policy" >&2; exit 1; }; done\nexit 0\n')
    os.chmod(hook, 0o755)
    p = _run_gc(work, home, "--apply", "--no-gh", "--no-prune")
    doc = json.loads(p.stdout)
    assert p.returncode == 0, p.stdout + p.stderr
    assert doc["status"] == "partial"
    failed = {f["name"]: f for f in doc["failed"]}
    assert failed["atom/locked"]["op"] == "remote-delete"
    assert failed["atom/locked"]["reason"]
    assert "atom/phantom" in doc["remote_already_absent"]
    assert "atom/phantom" not in doc["deleted_remote_branches"]
    assert "atom/merged-one" in doc["deleted_remote_branches"]


def test_a_clean_apply_still_reports_ok_with_an_empty_failed_list(fixture_repo):
    work, home = fixture_repo
    doc = json.loads(_run_gc(work, home, "--apply", "--no-gh").stdout)
    assert doc["status"] == "ok" and doc["failed"] == [] and doc["remote_already_absent"] == []


def test_doctor_line_says_what_it_measured(fixture_repo):
    """(b) ancestry-only is named; refs outside the glob set are counted."""
    work, home = fixture_repo
    _branch_with_commit(work, home, "spec/other", "other.txt")
    _ok, detail = doctor.check_branches_advisory(project_dir=str(work))
    assert "ancestry only" in detail
    assert "outside the glob set" in detail and "--include" in detail


def test_include_refuses_a_glob_without_a_literal_prefix(fixture_repo):
    """PR #246 review R1: `--include '*'` would make every long-lived branch a candidate."""
    work, home = fixture_repo
    for bad in ("*", "**", "?*", "*/x", "-x/*", "[ab]/*"):
        p = _run_gc(work, home, "--dry-run", "--no-gh", f"--include={bad}")
        assert p.returncode == 1 and json.loads(p.stdout)["status"] == "refused", bad
    assert _run_gc(work, home, "--dry-run", "--no-gh", "--include", "spec/*").returncode == 0


def test_long_lived_branches_are_always_protected(fixture_repo):
    """PR #246 review R1: a `develop` fast-forwarded from main reads merged by ancestry — never deleted."""
    work, home = fixture_repo
    env = _env(home)
    _run_git(work, "branch", "develop", "main", env=env)
    _run_git(work, "push", "-q", "origin", "develop", env=env)
    doc = json.loads(_run_gc(work, home, "--apply", "--no-gh").stdout)
    assert {r["name"]: r for r in doc["branches"]}["develop"]["class"] == "protected"
    assert "develop" not in doc["deleted_local_branches"] + doc["deleted_remote_branches"]
    assert _run_git(work, "rev-parse", "-q", "--verify", "refs/heads/develop").returncode == 0


def test_a_mirror_style_fetch_refspec_is_never_pruned(fixture_repo):
    """PR #246 review R4: prune deletes the refspec's destination side — refuse a non-standard one."""
    work, home = fixture_repo
    env = _env(home)
    _run_git(work, "config", "--add", "remote.origin.fetch", "+refs/heads/*:refs/heads/*", env=env)
    doc = json.loads(_run_gc(work, home, "--apply", "--no-gh").stdout)
    assert doc["prune"]["status"] == "skipped" and doc["prune"]["reason"] == "non-standard fetch refspec"


def test_failure_reasons_never_carry_a_url_credential():
    """PR #246 review R3."""
    p = subprocess.CompletedProcess([], 128, "", "fatal: unable to access 'https://x-access-token:ghs_SECRET@github.com/o/r.git/'")
    assert "ghs_SECRET" not in gc._why(p) and "://***@github.com" in gc._why(p)


def test_every_workflow_run_block_parses_as_bash():
    """PR #246 review B1: a comment joined onto a `for ... do` line commented the loop out and left a
    bare `done` — every release would have failed. Parse every `run:` block of every workflow."""
    import glob
    import yaml
    bad = []
    for wf in sorted(glob.glob(os.path.join(REPO_ROOT, ".github", "workflows", "*.yml"))):
        doc = yaml.safe_load(open(wf, encoding="utf-8"))
        for jname, job in (doc.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                if "run" not in step or job.get("defaults", {}).get("run", {}).get("shell", "bash") not in ("bash",):
                    continue
                r = subprocess.run(["bash", "-n"], input=step["run"], text=True, capture_output=True)
                if r.returncode != 0:
                    bad.append(f"{os.path.basename(wf)}:{jname}:{step.get('name')}: {r.stderr.strip()[:160]}")
    assert not bad, "\n".join(bad)
