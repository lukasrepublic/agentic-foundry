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


def test_emitted_plan_order_yields_coherence(repo):
    """AC-TPC-3 — following the EMITTED plan verbatim must produce a coherent tag. Asserted on the
    plan's own text: the re-pin step must precede the tag step."""
    plan = "\n".join(cr.publish_plan(str(repo), "1.0.1"))
    tag_at = plan.index("tag -a")
    repin_at = plan.index("source.sha")
    assert repin_at < tag_at, (
        "the emitted plan still tags BEFORE re-pinning — following it verbatim reproduces the "
        f"stale-pin defect:\n{plan}")


def test_first_cut_with_no_tag_is_not_applicable(repo):
    """Before the tag exists the tree necessarily carries the previous sha; that is not a defect."""
    ok, detail = cr.tag_pin_coherence(str(repo), "9.9.9")
    assert ok and "not applicable" in detail


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
