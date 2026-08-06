"""The NON-HERMETIC post-push check: the tag an adopter fetches must be the one this release verified.

`tag_pin_coherence` proves the pin is coherent in the LOCAL object store. It — and every other test
in this suite — is structurally blind to whether the tag exists UPSTREAM, because that answer lives
on a remote. That blindness published twice in one day: the F3 zero-history reset deleted the
`v0.26.x` tags upstream while every local check stayed green, so every copy-pasted install line
resolved a dead ref for every stranger. A human reading the published artifact caught it, which is
not a control.

These tests build REAL git fixtures with a REAL bare remote and drive the shipped
`published_tag_coherence` over them — pushed, unpushed, and diverged. The `ls_remote` injection seam
is used only for the two conditions a local bare remote cannot produce (a transport failure and a
lightweight-tag listing), never as a substitute for the real path.
"""
import ast
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
        json.dump({"plugins": [{"name": "foundry", "source": {"ref": f"v{version}", "sha": sha}}]}, f)


@pytest.fixture
def cut(tmp_path):
    """A coherently-cut v1.0.0 (re-pin THEN tag) with a real bare `origin`, tag NOT yet pushed.

    Returning it pre-push is deliberate: the unpushed state IS the F3 defect, so the fixture's
    default is the failing world and each test pushes only what it means to assert.
    """
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True, timeout=30)

    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.invalid")
    git(r, "config", "user.name", "t")
    git(r, "remote", "add", "origin", str(bare))

    _write_manifests(str(r), "1.0.0")
    git(r, "add", "-A"); git(r, "commit", "-qm", "v1.0.0 content")
    content = git(r, "rev-parse", "HEAD")
    _write_manifests(str(r), "1.0.0", sha=content)
    git(r, "add", "-A"); git(r, "commit", "-qm", "re-pin v1.0.0")
    git(r, "tag", "-a", "v1.0.0", "-m", "v1.0.0")
    git(r, "push", "-q", "origin", "main")
    return r


# --------------------------------------------------------------------------- the F3 class
def test_unpushed_tag_is_caught(cut):
    """THE defect this check exists for. The pin is locally coherent and `--verify-tag` is green,
    but the ref an adopter fetches is not there. Nothing hermetic can see this."""
    ok, detail = cr.tag_pin_coherence(str(cut), "1.0.0")
    assert ok, f"precondition: the local pin must be coherent, else this proves nothing — {detail}"

    ok, detail = cr.published_tag_coherence(str(cut), "1.0.0")
    assert not ok, f"an unpublished tag passed the upstream check: {detail}"
    assert "PUBLISHED TAG MISSING" in detail
    assert "git push origin v1.0.0" in detail, f"the refusal must name the remedy: {detail}"


def test_pushed_tag_passes(cut):
    """No over-block: once the tag is actually pushed, the check goes green."""
    git(cut, "push", "-q", "origin", "v1.0.0")
    ok, detail = cr.published_tag_coherence(str(cut), "1.0.0")
    assert ok, f"a correctly published tag was refused: {detail}"
    assert "resolves upstream" in detail


def test_diverged_tag_is_caught(cut, tmp_path):
    """Upstream carries a tag of the same NAME resolving to a different commit — an adopter receives
    something this release never verified. Built by pushing a tag made at an earlier commit."""
    git(cut, "push", "-q", "origin", "v1.0.0")
    other = git(cut, "rev-parse", "HEAD~1")
    git(cut, "tag", "-a", "-f", "v1.0.0-alt", other, "-m", "alt")
    # force the REMOTE's v1.0.0 onto the other commit, leaving the local tag untouched
    git(cut, "push", "-q", "--force", "origin", "refs/tags/v1.0.0-alt:refs/tags/v1.0.0")

    ok, detail = cr.published_tag_coherence(str(cut), "1.0.0")
    assert not ok, f"a diverged published tag passed: {detail}"
    assert "PUBLISHED TAG DIVERGED" in detail
    assert other[:12] in detail, f"the refusal must name what upstream actually serves: {detail}"
    assert "force-push" in detail, f"the refusal must warn against the wrong remedy: {detail}"


# --------------------------------------------------------------------------- fail-closed behaviour
def test_transport_failure_refuses_rather_than_passes(cut):
    """The whole point of this check is that it leaves the hermetic world. 'I could not reach the
    remote' must never render as 'the remote is fine' — the failure mode that would make the check
    worse than useless, because it would carry a green."""
    git(cut, "push", "-q", "origin", "v1.0.0")
    ok, detail = cr.published_tag_coherence(
        str(cut), "1.0.0", ls_remote=lambda tree, tag: (128, ""))
    assert not ok, f"an unreachable remote reported coherent: {detail}"
    assert "REFUSED" in detail.upper()


def test_could_not_run_is_distinct_from_a_clean_nonzero_exit(cut):
    """rc=-1 (could not run at all) and rc>0 (ran, exited non-zero) must both refuse and must read
    differently, mirroring tag_pin_coherence's own convention."""
    git(cut, "push", "-q", "origin", "v1.0.0")
    _ok, could_not = cr.published_tag_coherence(
        str(cut), "1.0.0", ls_remote=lambda tree, tag: (-1, ""))
    _ok, exited = cr.published_tag_coherence(
        str(cut), "1.0.0", ls_remote=lambda tree, tag: (128, ""))
    assert "could not run" in could_not
    assert "exited 128" in exited


def test_incoherent_local_pin_refuses_before_probing(cut):
    """The soundness derivation (ref equality => the pinned sha exists upstream) rests on AC-TPC-5
    adjacency. Without a coherent local pin the comparison proves nothing, so it must refuse rather
    than emit a green built on a premise that does not hold."""
    # Break the pin by LENGTHENING history, leaving source.sha on the original content commit. It
    # is then neither the tag commit nor its first parent, so AC-TPC-5 adjacency fails — which is
    # precisely the premise this check requires. (Rewriting source.sha to HEAD~1 does NOT work: the
    # fixture already pins that commit, so the "break" is a no-op and git refuses an empty commit.)
    (cut / "filler.txt").write_text("filler 1\n")
    git(cut, "add", "-A"); git(cut, "commit", "-qm", "filler 1")
    (cut / "filler.txt").write_text("filler 2\n")
    git(cut, "add", "-A"); git(cut, "commit", "-qm", "filler 2")
    git(cut, "tag", "-a", "-f", "v1.0.0", "-m", "v1.0.0")
    git(cut, "push", "-q", "--force", "origin", "refs/tags/v1.0.0")

    ok, why = cr.tag_pin_coherence(str(cut), "1.0.0")
    assert not ok, f"fixture failed to break the local pin, so this test proves nothing: {why}"

    ok, detail = cr.published_tag_coherence(str(cut), "1.0.0")
    assert not ok
    assert "refusing the upstream check" in detail
    assert "prove nothing" in detail


def test_absent_local_tag_refuses(cut):
    """Nothing to compare against — must not report the upstream side as coherent."""
    ok, detail = cr.published_tag_coherence(str(cut), "9.9.9")
    assert not ok
    assert "9.9.9" in detail


# --------------------------------------------------------------------------- ref parsing
def test_lightweight_tag_upstream_is_read_from_the_unpeeled_ref(cut):
    """A lightweight tag emits no `^{}` line. Preferring the peeled ref must not mean IGNORING a
    listing that only has the plain one, or every lightweight-tagged release would read as missing."""
    local = git(cut, "rev-parse", "v1.0.0^{commit}")
    ok, detail = cr.published_tag_coherence(
        str(cut), "1.0.0",
        ls_remote=lambda tree, tag: (0, f"{local}\trefs/tags/{tag}"))
    assert ok, f"a lightweight upstream tag read as missing: {detail}"


def test_peeled_ref_wins_over_the_tag_object(cut):
    """For an ANNOTATED tag, `refs/tags/X` is the tag OBJECT, not a commit. Comparing against it
    would refuse every annotated release, so the peeled line must win when both are listed."""
    local = git(cut, "rev-parse", "v1.0.0^{commit}")
    tagobj = git(cut, "rev-parse", "v1.0.0")
    assert tagobj != local, "fixture must use an annotated tag for this to be meaningful"
    ok, detail = cr.published_tag_coherence(
        str(cut), "1.0.0",
        ls_remote=lambda tree, tag: (0, f"{tagobj}\trefs/tags/{tag}\n{local}\trefs/tags/{tag}^{{}}"))
    assert ok, f"the tag object was compared instead of the peeled commit: {detail}"


# --------------------------------------------------------------------------- the never-inject guard
def test_cli_path_never_injects_an_ls_remote():
    """Mirrors test_cli_path_never_injects_a_suite_runner. A stub reaching the CLI path would make
    every release report a fabricated upstream — strictly worse than having no check, because the
    fabrication would carry a green. Asserted on the PARSED call, not on a substring of the source.
    """
    with open(CUT) as f:
        tree = ast.parse(f.read())

    main_fn = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "main")
    calls = [n for n in ast.walk(main_fn)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name)
             and n.func.id == "published_tag_coherence"]
    assert calls, "main() no longer calls published_tag_coherence — the CLI step is gone"
    for c in calls:
        assert not c.keywords, (
            f"main() passes keyword args to published_tag_coherence ({[k.arg for k in c.keywords]}); "
            f"the CLI path must never inject ls_remote")


def test_the_plan_emits_the_post_push_step():
    """The check only runs if the emitted plan tells the operator to run it — and it must come
    AFTER both pushes, because before them the tag is deliberately local-only."""
    plan = cr.publish_plan("/tmp/x", "1.2.3")
    idx = [i for i, s in enumerate(plan) if "--verify-published" in s]
    assert idx, "the publish plan never tells the operator to run the upstream check"
    push_tag = max(i for i, s in enumerate(plan) if "push origin v1.2.3" in s)
    assert idx[0] > push_tag, (
        "the upstream check is emitted BEFORE the tag push, where it would fail every correct "
        "release — the tag is local-only until then")
