"""feat-foundry-main-catalogue-coherence — the DEFAULT BRANCH's catalogue is the blob adopters
actually resolve once the sibling atom drops the `#vX.Y.Z` tag pin from this plugin's marketplace
registration. `tag_pin_coherence` (`scripts/foundry-cut-release.py:306`) grades the TAG's blob at
cut time; nothing shipped grades `main`'s blob. `scripts/foundry_main_catalogue.py` is that
replacement control.

Anti-vacuity is the point of this module. A checker exercised only against a healthy tree proves
nothing — it would be equally green if it returned True unconditionally. So the negative controls
(AC-MCC-1/-2/-3) each drive the shipped check over a SYNTHETIC, INCOHERENT git fixture built in
tmp and assert it is convicted; the positive control (AC-MCC-4) is a separate synthetic COHERENT
fixture, deliberately not the live tree (`main` is legitimately incoherent between the version-bump
PR and the re-pin PR — see the spec's R5). These fixtures mirror the technique
`tests/test_tag_pin_coherence.py:37-95` already uses: real `git init` repos in `tmp_path`, with
manifests written per-commit.
"""
import ast
import importlib.util
import hashlib
import json
import os
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_PATH = os.path.join(REPO_ROOT, "scripts", "foundry_main_catalogue.py")

_spec = importlib.util.spec_from_file_location("_maincatalogue", MODULE_PATH)
mcc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mcc)


def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30)
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {r.stderr}")
    return r.stdout.strip()


def _write_manifests(repo, version, sha):
    os.makedirs(os.path.join(repo, ".claude-plugin"), exist_ok=True)
    with open(os.path.join(repo, ".claude-plugin", "plugin.json"), "w") as f:
        json.dump({"name": "foundry", "version": version}, f)
    with open(os.path.join(repo, ".claude-plugin", "marketplace.json"), "w") as f:
        json.dump(
            {"plugins": [{"name": "foundry", "version": version,
                          "source": {"repo": "lukasrepublic/agentic-foundry", "sha": sha}}]},
            f,
        )


@pytest.fixture
def repo(tmp_path):
    """A bare git repo on `main`, no commits yet — each test builds its own history on top."""
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.invalid")
    git(r, "config", "user.name", "t")
    return r


# --------------------------------------------------------------------------------------- AC-MCC-1
def test_version_ahead_of_pinned_sha_is_convicted(repo):
    """THE R-window defect this whole atom exists to catch. The version-bump PR (R) legitimately
    advertises the NEW version while source.sha still names the PREVIOUS release's revision — an
    adopter resolving `main` and installing by that sha receives the previous version's code."""
    _write_manifests(str(repo), "1.0.0", sha="0" * 40)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "v1.0.0 content")
    v100 = git(repo, "rev-parse", "HEAD")

    # R: advertise 1.0.1, but source.sha still names the v1.0.0 content revision.
    _write_manifests(str(repo), "1.0.1", sha=v100)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "R: version bump to 1.0.1 (re-pin not yet landed)")

    ok, detail = mcc.main_catalogue_coherence(str(repo))
    assert not ok, f"an incoherent default-branch catalogue passed the check: {detail}"


def test_version_refusal_names_both_versions(repo):
    """The refusal must name BOTH the advertised version and the version the pin actually
    resolves to, and the pinned revision, so a human reads the defect instead of a bare failure."""
    _write_manifests(str(repo), "1.0.0", sha="0" * 40)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "v1.0.0 content")
    v100 = git(repo, "rev-parse", "HEAD")

    _write_manifests(str(repo), "1.0.1", sha=v100)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "R: version bump to 1.0.1")

    ok, detail = mcc.main_catalogue_coherence(str(repo))
    assert not ok
    assert "1.0.0" in detail and "1.0.1" in detail, f"refusal names neither version: {detail}"
    assert v100[:12] in detail, f"refusal does not name the pinned revision: {detail}"


# --------------------------------------------------------------------------------------- AC-MCC-2
def test_non_hex_sha_is_convicted(repo):
    """source.sha is a REF NAME ("main") rather than a resolved id — a MUTABLE pin. Distinct cause
    from the well-formed-but-wrong-object-type case below."""
    _write_manifests(str(repo), "1.0.0", sha="main")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "v1.0.0 content, mutable pin")

    ok, detail = mcc.main_catalogue_coherence(str(repo))
    assert not ok, f"a ref-name pin passed the 40-hex check: {detail}"
    assert "40" in detail or "hex" in detail.lower(), (
        f"refusal does not name the not-40-hex cause distinctly: {detail}")


def test_sha_not_naming_a_commit_object_is_convicted(repo):
    """source.sha is a well-formed 40-hex id that resolves — but to an ANNOTATED TAG object, never
    the plain revision `git cat-file -t` reports for real history. An annotated tag ref's OWN sha
    (not `<tag>^{commit}`) IS the tag object's id."""
    _write_manifests(str(repo), "1.0.0", sha="0" * 40)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "v1.0.0 content")
    git(repo, "tag", "-a", "v1.0.0", "-m", "v1.0.0")
    tag_object_sha = git(repo, "rev-parse", "v1.0.0")   # the ANNOTATED TAG object's own id

    _write_manifests(str(repo), "1.0.0", sha=tag_object_sha)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "re-pin to the tag object, not the commit it names")

    ok, detail = mcc.main_catalogue_coherence(str(repo))
    assert not ok, f"a pin naming an annotated-tag object passed as a commit: {detail}"
    assert "tag" in detail.lower(), f"refusal does not distinguish the tag-object cause: {detail}"


# --------------------------------------------------------------------------------------- AC-MCC-3
def test_sha_off_the_default_branch_is_convicted(repo):
    """A REAL revision, carrying the RIGHT version — but on a side branch that was never merged
    into the default branch. This is the case that fails ONLY on the ancestry predicate, so it
    cannot be satisfied by the version check passing."""
    _write_manifests(str(repo), "0.0.0", sha="0" * 40)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "root")

    git(repo, "checkout", "-qb", "side")
    _write_manifests(str(repo), "1.0.0", sha="0" * 40)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "v1.0.0 content, on a side branch never merged to main")
    side_sha = git(repo, "rev-parse", "HEAD")

    git(repo, "checkout", "-q", "main")
    _write_manifests(str(repo), "1.0.0", sha=side_sha)   # right version, wrong reachability
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "re-pin to a revision main never merged")

    ok, detail = mcc.main_catalogue_coherence(str(repo))
    assert not ok, f"a pin off the default branch's history passed the ancestry check: {detail}"


# --------------------------------------------------------------------------------------- AC-MCC-4
def test_coherent_default_branch_passes(repo):
    """No over-block: a synthetic fixture cut in the CORRECT order — content revision, then a
    re-pin naming it, both carrying the target version — must pass. Deliberately synthetic, never
    the live tree: `main` is legitimately incoherent between R and R2 (spec R5), which would make a
    live-tree positive control flaky by design rather than a real signal."""
    _write_manifests(str(repo), "1.0.0", sha="0" * 40)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "v1.0.0 content")
    content_sha = git(repo, "rev-parse", "HEAD")

    _write_manifests(str(repo), "1.0.0", sha=content_sha)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "R2: re-pin to the content revision")

    ok, detail = mcc.main_catalogue_coherence(str(repo))
    assert ok, f"a coherent default-branch catalogue was refused: {detail}"


# --------------------------------------------------------------------------------------- AC-MCC-6
def _fixture_digest(repo):
    """A recursive digest over every path under the fixture (including .git) so a mutation on ANY
    file — working tree, index, refs, or the object store — is caught, not just the paths a
    specific test happened to touch."""
    h = hashlib.sha256()
    for root, dirs, files in os.walk(repo):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(root, name)
            rel = os.path.relpath(path, repo)
            h.update(rel.encode())
            try:
                with open(path, "rb") as f:
                    h.update(f.read())
            except OSError:
                h.update(b"<unreadable>")
    return h.hexdigest()


def test_check_leaves_the_fixture_byte_identical(repo):
    """RUNTIME proof: drive the check over a real fixture and assert the working tree, index, refs
    and object store are byte-identical afterwards. A static source scan alone cannot see a
    mutation performed through an alias, an f-string, or an unexpected git subcommand."""
    _write_manifests(str(repo), "1.0.0", sha="0" * 40)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "v1.0.0 content")
    content_sha = git(repo, "rev-parse", "HEAD")
    _write_manifests(str(repo), "1.0.0", sha=content_sha)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "re-pin")

    before = _fixture_digest(repo)
    ok, _detail = mcc.main_catalogue_coherence(str(repo))
    after = _fixture_digest(repo)

    assert ok
    assert before == after, "the check mutated the fixture repository"


def test_check_names_no_mutating_or_network_operation(repo):
    """STATIC proof: the module's source names no network module and no mutating git verb call.
    Parsed with `ast` rather than the CI checkpoint's plain grep so this node is meaningful on its
    own terms (it does not merely re-run the same substring scan under a different name)."""
    with open(MODULE_PATH, encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source, filename=MODULE_PATH)

    forbidden_imports = {"urllib", "requests", "http", "socket", "http.client"}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    hit = imported & forbidden_imports
    assert not hit, f"module imports a network-capable module: {hit}"

    # Every subprocess.run call in the module must invoke the literal program "git" (never a
    # different binary), and every call THROUGH the module's own `_git(tree, verb, ...)` wrapper
    # must name a verb from a fixed read-only allowlist. Assert SUBSET-of-allowlist rather than
    # merely absent-from-a-denylist, so an unreviewed new git call must be added here deliberately.
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "run" and node.args
                and isinstance(node.args[0], ast.List) and node.args[0].elts):
            first = node.args[0].elts[0]
            assert isinstance(first, ast.Constant) and first.value == "git", (
                "a subprocess.run call invokes a program other than the literal 'git'")

    read_only_verbs = {"rev-parse", "cat-file", "show", "merge-base"}
    verbs_used = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_git" and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)):
            verbs_used.add(node.args[1].value)
    assert verbs_used, "no git subcommand invocation found — the static scan located nothing to check"
    assert verbs_used <= read_only_verbs, (
        f"module invokes git verb(s) outside the read-only allowlist: {verbs_used - read_only_verbs}")
