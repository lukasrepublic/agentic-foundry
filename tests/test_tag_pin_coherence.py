"""feat-foundry-tag-pin-coherence — the tag ref must serve an install pin that resolves to the release.

Adopters install by ref (`marketplace add <repo>#vX.Y.Z`), which resolves marketplace.json AT THE
TAG and installs the commit its source.sha names. The emitted publish plan tagged at the release
commit and re-pinned source.sha in a LATER commit, so the tag carried the PREVIOUS release's sha
and an install by ref delivered the previous version's code. That shipped twice — v1.0.0 and
v1.0.1 — and was hand-corrected both times.

These tests build real git fixture repos and drive the shipped `tag_pin_coherence` check over
them: a coherent cut, the defective ordering, a tag-object pin, an out-of-history pin, and the
first-cut (no tag yet) case.
"""
import importlib.util
import json
import os
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUT = os.path.join(REPO_ROOT, "scripts", "foundry-cut-release.py")

_spec = importlib.util.spec_from_file_location("_cutrelease", CUT)
cr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cr)


def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30)
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {r.stderr}")
    return r.stdout.strip()


def _write_manifests(repo, version, sha="0" * 40):
    os.makedirs(os.path.join(repo, ".claude-plugin"), exist_ok=True)
    with open(os.path.join(repo, ".claude-plugin", "plugin.json"), "w") as f:
        json.dump({"name": "foundry", "version": version}, f)
    with open(os.path.join(repo, ".claude-plugin", "marketplace.json"), "w") as f:
        json.dump({"plugins": [{"name": "foundry",
                                "source": {"ref": f"v{version}", "sha": sha}}]}, f)


@pytest.fixture
def repo(tmp_path):
    """A git repo with a v1.0.0 release already cut coherently."""
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.invalid")
    git(r, "config", "user.name", "t")
    # --- v1.0.0: content commit, then re-pin, then tag on the re-pin commit (the CORRECT order)
    _write_manifests(str(r), "1.0.0")
    git(r, "add", "-A"); git(r, "commit", "-qm", "v1.0.0 content")
    v100_content = git(r, "rev-parse", "HEAD")
    _write_manifests(str(r), "1.0.0", sha=v100_content)
    git(r, "add", "-A"); git(r, "commit", "-qm", "re-pin v1.0.0")
    git(r, "tag", "-a", "v1.0.0", "-m", "v1.0.0")
    return r


def cut_v101(repo, order):
    """Cut v1.0.1 in the given order. `correct` = re-pin then tag; `defective` = tag then re-pin."""
    _write_manifests(str(repo), "1.0.1", sha=git(repo, "rev-parse", "v1.0.0^{commit}"))
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "v1.0.1 content")
    content = git(repo, "rev-parse", "HEAD")
    if order == "defective":
        git(repo, "tag", "-a", "v1.0.1", "-m", "v1.0.1")           # tag FIRST — the bug
        _write_manifests(str(repo), "1.0.1", sha=content)
        git(repo, "add", "-A"); git(repo, "commit", "-qm", "re-pin v1.0.1")
    else:
        _write_manifests(str(repo), "1.0.1", sha=content)          # re-pin FIRST — the fix
        git(repo, "add", "-A"); git(repo, "commit", "-qm", "re-pin v1.0.1")
        git(repo, "tag", "-a", "v1.0.1", "-m", "v1.0.1")
    return content


# --------------------------------------------------------------------------- AC-TPC-2 / AC-TPC-7
def test_stale_pin_is_gated(repo):
    """THE defect. Tagging before the re-pin leaves the tag serving the PREVIOUS release's sha, so
    an install by ref delivers the previous version's code."""
    cut_v101(repo, "defective")
    ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1")
    assert not ok, f"an incoherent install pin passed the gate: {detail}"


def test_refusal_names_both_values(repo):
    """AC-TPC-7 — the message must name the sha the tag serves AND what it actually resolves to,
    so the operator sees the defect rather than a bare failure."""
    cut_v101(repo, "defective")
    _ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1")
    assert "1.0.0" in detail and "1.0.1" in detail, f"refusal names neither version: {detail}"
    assert "INCOHERENT" in detail.upper()


# --------------------------------------------------------------------------- AC-TPC-1 / AC-TPC-3
def test_coherent_pin_is_ready(repo):
    """No over-block: a correctly ordered cut passes."""
    cut_v101(repo, "correct")
    ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1")
    assert ok, f"a coherent cut was refused: {detail}"


def _step(plan, pred):
    return next((n for n, s in enumerate(plan) if pred(s)), -1)


def test_emitted_plan_order_yields_coherence(repo):
    """AC-TPC-3 — following the EMITTED plan verbatim must produce a coherent tag.

    Asserted on the index of the EXECUTABLE STEPS, never on substring positions in the joined text.
    The first version of this test did `plan.index("source.sha") < plan.index("tag -a")`, which
    matched "source.sha" inside the human-readable `# edit …` COMMENT that sits above the commit.
    Swapping the real `git commit` and `git tag -a` steps — the literal v1.0.0/v1.0.1 defect — left
    that comment untouched and the assertion still passed. It proved nothing.
    """
    plan = cr.publish_plan(str(repo), "1.0.1")
    repin_at = _step(plan, lambda s: s.startswith("git ") and " commit " in s and "source.sha" in s)
    tag_at = _step(plan, lambda s: s.startswith("git ") and " tag -a v1.0.1" in s)
    assert repin_at >= 0 and tag_at >= 0, f"plan is missing a re-pin or tag step:\n{plan}"
    assert repin_at < tag_at, (
        "the emitted plan still tags BEFORE re-pinning — following it verbatim reproduces the "
        f"stale-pin defect:\n{plan}")


def test_emitted_plan_order_assertion_is_not_vacuous(repo):
    """The meta-test: the order assertion above must FAIL on a plan with the steps swapped.

    Without this, a future refactor could reintroduce a substring-position check and nothing would
    notice that the guard stopped guarding."""
    plan = cr.publish_plan(str(repo), "1.0.1")
    repin_at = _step(plan, lambda s: s.startswith("git ") and " commit " in s and "source.sha" in s)
    tag_at = _step(plan, lambda s: s.startswith("git ") and " tag -a v1.0.1" in s)
    swapped = list(plan)
    swapped[repin_at], swapped[tag_at] = swapped[tag_at], swapped[repin_at]
    r2 = _step(swapped, lambda s: s.startswith("git ") and " commit " in s and "source.sha" in s)
    t2 = _step(swapped, lambda s: s.startswith("git ") and " tag -a v1.0.1" in s)
    assert not (r2 < t2), "the order assertion does not actually detect a swapped plan"


def test_repin_step_is_path_scoped(repo):
    """The reorder puts the tag on R2 — a commit created AFTER the acceptance gate ran. A bare
    `commit -am` would sweep every modified tracked file into it and publish that under the release
    tag, ungated. The re-pin must commit ONLY the manifest."""
    plan = cr.publish_plan(str(repo), "1.0.1")
    repin_at = _step(plan, lambda s: s.startswith("git ") and " commit " in s and "source.sha" in s)
    step = plan[repin_at]
    assert "commit -am" not in step, f"the re-pin step sweeps the whole working tree: {step}"
    assert step.split("#")[0].rstrip().endswith(".claude-plugin/marketplace.json"), (
        f"the re-pin step is not path-scoped to the manifest: {step}")


def test_plan_verifies_the_tag_before_pushing(repo):
    """AC-TPC-2 has no caller on the cut that CREATES the tag unless the plan makes one. The machine
    re-check must sit after the tag step and before either push."""
    plan = cr.publish_plan(str(repo), "1.0.1")
    tag_at = _step(plan, lambda s: " tag -a v1.0.1" in s)
    verify_at = _step(plan, lambda s: "--verify-tag" in s)
    push_at = _step(plan, lambda s: "push origin" in s)
    assert tag_at < verify_at < push_at, (
        f"the plan does not machine-verify the tag between tagging and pushing:\n{plan}")


def test_first_cut_with_no_tag_is_not_applicable(repo):
    """Before the tag exists the tree necessarily carries the previous sha; that is not a defect."""
    ok, detail = cr.tag_pin_coherence(str(repo), "9.9.9")
    assert ok and "not applicable" in detail


# ------------------------------------------------------------------- FAIL CLOSED WHEN UNCHECKABLE
def test_non_git_tree_refuses_rather_than_reporting_not_applicable(tmp_path):
    """THE FAIL-OPEN. `git rev-parse --verify refs/tags/X` exits non-zero for 'not a git
    repository', 'dubious ownership' (safe.directory — routine in CI containers and any checkout
    owned by another uid), a corrupt object store and permission errors — all indistinguishable
    from 'the tag is absent'. Reading those as not-applicable let a tree the gate could not inspect
    sail through to READY."""
    plain = tmp_path / "notarepo"
    plain.mkdir()
    _write_manifests(str(plain), "1.0.1")
    ok, detail = cr.tag_pin_coherence(str(plain), "1.0.1")
    assert not ok, f"a non-git tree reported OK: {detail}"
    assert "not a readable git repository" in detail


def test_dirty_tree_refuses(repo):
    """The tag now lands on R2, created AFTER the acceptance verdict. Anything left uncommitted
    would be swept into it by the re-pin step and published under the release tag ungated."""
    assert cr.worktree_clean(str(repo))[0], "fixture should start clean"
    with open(os.path.join(repo, "stray.txt"), "w") as f:
        f.write("uncommitted")
    git(repo, "add", "-A")
    ok, detail = cr.worktree_clean(str(repo))
    assert not ok and "stray.txt" in detail, detail


def test_worktree_clean_fails_closed_off_a_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    ok, detail = cr.worktree_clean(str(plain))
    assert not ok and "cannot check the working tree" in detail


# ------------------------------------------------------------------- THE PIN MUST BE AN IMMUTABLE ID
@pytest.mark.parametrize("bad", ["main", "HEAD", "v1.0.0", "abc1234", "0" * 39, "A" * 40])
def test_sha_must_be_full_lowercase_hex(repo, bad):
    """git resolves arbitrary revision expressions, so an unvalidated `source.sha` of "main" types
    as a commit and passes every downstream check — while being a MUTABLE pin whose resolution
    changes as the branch moves. That destroys the immutable-artifact property AC-TPC-1 asserts."""
    cut_v101(repo, "correct")
    git(repo, "tag", "-d", "v1.0.1")
    _write_manifests(str(repo), "1.0.1", sha=bad)
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "bad pin: not a full hex id")
    git(repo, "tag", "-a", "v1.0.1", "-m", "v1.0.1")
    ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1")
    assert not ok, f"a non-immutable pin {bad!r} was accepted: {detail}"
    assert "40-character lowercase hex" in detail


def test_distant_ancestor_pin_is_refused(repo):
    """AC-TPC-5 tightened. `merge-base --is-ancestor` admits EVERY commit reachable from the tag.
    The version bump lands at the START of release work, so several ancestors carry the target
    version — an ancestor-plus-version check would admit a pin naming the version-bump commit and
    silently omit everything committed after it. Same wrong-code-delivered outcome, displaced by a
    few commits. The pin must be the tag commit or its first parent."""
    _write_manifests(str(repo), "1.0.1", sha=git(repo, "rev-parse", "v1.0.0^{commit}"))
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "v1.0.1 bump (carries the target version)")
    early_bump = git(repo, "rev-parse", "HEAD")
    with open(os.path.join(repo, "feature.txt"), "w") as f:
        f.write("the code that would be omitted")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "v1.0.1 content")
    _write_manifests(str(repo), "1.0.1", sha=early_bump)      # pin the BUMP, not the content
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "re-pin (to the wrong, earlier commit)")
    git(repo, "tag", "-a", "v1.0.1", "-m", "v1.0.1")
    ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1")
    assert not ok, f"a distant ancestor pin passed — feature.txt would be omitted: {detail}"
    assert "tag commit" in detail and "first parent" in detail


def test_named_plugin_entry_is_used_not_index_zero(repo):
    """The gate must select the 'foundry' entry BY NAME. Indexing [0] silently grades a different
    plugin's pin the moment the marketplace lists a second plugin first."""
    cut_v101(repo, "correct")
    git(repo, "tag", "-d", "v1.0.1")
    # the two-plugin manifest lands as a NEW commit, which becomes the tag commit — so the pin must
    # name the commit that will be its first parent, i.e. today's HEAD.
    content = git(repo, "rev-parse", "HEAD")
    with open(os.path.join(repo, ".claude-plugin", "marketplace.json"), "w") as f:
        json.dump({"plugins": [
            {"name": "other", "source": {"ref": "v9.9.9", "sha": "1" * 40}},   # FIRST, and wrong
            {"name": "foundry", "source": {"ref": "v1.0.1", "sha": content}},
        ]}, f)
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "two-plugin manifest")
    git(repo, "tag", "-a", "v1.0.1", "-m", "v1.0.1")
    ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1")
    assert ok, f"the gate graded the wrong plugins[] entry: {detail}"


def test_verify_tag_cli_convicts_and_exits_nonzero(repo):
    """AC-TPC-2's caller. During a first cut the tag does not exist, so the check is a no-op — the
    post-tag CLI is the point at which it stops being one."""
    cut_v101(repo, "defective")
    r = subprocess.run(["python3", CUT, "--tree", str(repo), "--version", "1.0.1", "--verify-tag"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "TAG-PIN-INCOHERENT" in r.stdout
    git(repo, "tag", "-d", "v1.0.1")
    git(repo, "tag", "-a", "v1.0.1", "-m", "v1.0.1")           # move the tag onto the re-pin commit
    r = subprocess.run(["python3", CUT, "--tree", str(repo), "--version", "1.0.1", "--verify-tag"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "TAG-PIN-COHERENT" in r.stdout, r.stdout + r.stderr


# --------------------------------------------------------------------------- AC-TPC-4 / AC-TPC-5
def test_tag_object_pin_is_refused(repo):
    """source.sha must name a COMMIT. An annotated-tag object is the documented trap."""
    cut_v101(repo, "correct")
    tag_obj = git(repo, "rev-parse", "v1.0.0")            # the annotated-tag object, not ^{commit}
    if tag_obj == git(repo, "rev-parse", "v1.0.0^{commit}"):
        pytest.skip("v1.0.0 is not an annotated tag in this fixture")
    git(repo, "tag", "-d", "v1.0.1")
    _write_manifests(str(repo), "1.0.1", sha=tag_obj)
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "bad pin: tag object")
    git(repo, "tag", "-a", "v1.0.1", "-m", "v1.0.1")
    ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1")
    assert not ok and "not a commit" in detail, detail


def test_pin_outside_history_is_refused(repo):
    """A sha that is not an ancestor of the tag commit is outside the released history."""
    cut_v101(repo, "correct")
    git(repo, "checkout", "-q", "-b", "sidebranch", "v1.0.0^{commit}")
    with open(os.path.join(repo, "stray.txt"), "w") as f:
        f.write("x")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "off-history commit")
    stray = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    git(repo, "tag", "-d", "v1.0.1")
    _write_manifests(str(repo), "1.0.1", sha=stray)
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "bad pin: off-history")
    git(repo, "tag", "-a", "v1.0.1", "-m", "v1.0.1")
    ok, detail = cr.tag_pin_coherence(str(repo), "1.0.1")
    assert not ok and "released history" in detail, detail


# --------------------------------------------------------------------------------- AC-TPC-6
def test_cut_release_executes_nothing(repo):
    """The publish boundary is unchanged: the plan is DATA. Building it must create no tag, no
    commit, and no ref in the tree."""
    cut_v101(repo, "correct")
    before_refs = git(repo, "show-ref")
    before_head = git(repo, "rev-parse", "HEAD")
    plan = cr.publish_plan(str(repo), "1.0.1")
    assert isinstance(plan, (list, tuple)) and plan, "the plan is not emitted as data"
    assert git(repo, "show-ref") == before_refs, "building the plan mutated refs"
    assert git(repo, "rev-parse", "HEAD") == before_head, "building the plan moved HEAD"
    assert git(repo, "status", "--porcelain") == "", "building the plan dirtied the tree"
