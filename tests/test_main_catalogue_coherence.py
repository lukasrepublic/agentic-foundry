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


# --------------------------------------------------------------------------- FAIL-CLOSED ABORT PATHS
# The module's own docstring promises: "anything that prevents a real verdict ... REFUSES, never
# reports not-applicable." Every control above drives a WELL-FORMED fixture; none of them ever walks
# an abort path. Security review named the concrete risk: someone later silences a noisy red on a
# shallow-clone CI job by turning the unresolvable-default-branch case into a soft pass, and the
# check then silently never runs again -- exactly the "not applicable" trap that already let
# `tag_pin_coherence` miss this class of defect once (spec, "tag_pin_coherence CANNOT catch this, by
# construction"). Each test below asserts BOTH `not ok` AND that the detail message names the actual
# cause, so a bare `return False, "nope"` refactor would satisfy none of them.

def test_a_non_git_directory_is_refused(tmp_path):
    """No `.git` at all -- the very first probe (`git rev-parse --git-dir`) must fail closed."""
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    ok, detail = mcc.main_catalogue_coherence(str(not_a_repo))
    assert not ok
    assert "not a readable git repository" in detail


def test_an_unresolvable_default_branch_is_refused(repo):
    """A real repo, but the NAMED default branch does not exist -- e.g. a shallow clone missing the
    branch, or a typo'd --default-branch. This is the reviewer's named scenario: silencing this by
    turning it into a soft pass would make the check never run again."""
    _write_manifests(str(repo), "1.0.0", sha="0" * 40)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "v1.0.0 content")

    ok, detail = mcc.main_catalogue_coherence(str(repo), default_branch="does-not-exist")
    assert not ok
    assert "does not resolve" in detail


def test_no_committed_marketplace_json_is_refused(repo):
    """The default branch exists and has commits, but none of them ever added
    .claude-plugin/marketplace.json."""
    (repo / "README.md").write_text("nothing to see here\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "unrelated content, no manifest ever added")

    ok, detail = mcc.main_catalogue_coherence(str(repo))
    assert not ok
    assert "carries no committed" in detail


def test_malformed_marketplace_json_is_refused(repo):
    """The committed marketplace.json is not valid JSON at all."""
    os.makedirs(str(repo / ".claude-plugin"), exist_ok=True)
    (repo / ".claude-plugin" / "marketplace.json").write_text("{not json")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "malformed marketplace.json")

    ok, detail = mcc.main_catalogue_coherence(str(repo))
    assert not ok
    assert "unreadable" in detail


def test_marketplace_json_with_no_foundry_entry_is_refused(repo):
    """Valid JSON, a real plugins[] list, but no entry named 'foundry' -- and MORE than one entry,
    so select_plugin_entry cannot fall through the single-plugin unambiguous case."""
    os.makedirs(str(repo / ".claude-plugin"), exist_ok=True)
    (repo / ".claude-plugin" / "marketplace.json").write_text(json.dumps(
        {"plugins": [{"name": "other-plugin-a", "version": "1.0.0"},
                     {"name": "other-plugin-b", "version": "1.0.0"}]}))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "marketplace.json with no foundry entry")

    ok, detail = mcc.main_catalogue_coherence(str(repo))
    assert not ok
    assert "foundry" in detail.lower()


def test_entry_with_no_version_is_refused(repo):
    """The 'foundry' entry exists but declares no `version` key at all."""
    os.makedirs(str(repo / ".claude-plugin"), exist_ok=True)
    (repo / ".claude-plugin" / "marketplace.json").write_text(json.dumps(
        {"plugins": [{"name": "foundry", "source": {"sha": "0" * 40}}]}))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "foundry entry with no version")

    ok, detail = mcc.main_catalogue_coherence(str(repo))
    assert not ok
    assert "no version" in detail


def test_entry_with_no_source_is_refused(repo):
    """The 'foundry' entry declares a version but no `source` key at all -- sha comes back "" and
    must be convicted the same way a malformed sha is, not silently skipped."""
    os.makedirs(str(repo / ".claude-plugin"), exist_ok=True)
    (repo / ".claude-plugin" / "marketplace.json").write_text(json.dumps(
        {"plugins": [{"name": "foundry", "version": "1.0.0"}]}))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "foundry entry with no source")

    ok, detail = mcc.main_catalogue_coherence(str(repo))
    assert not ok
    assert "40-character" in detail


def test_pinned_commit_missing_plugin_json_is_refused(repo):
    """source.sha resolves to a REAL commit, ancestor of main, but that commit never carried
    .claude-plugin/plugin.json at all -- e.g. it predates the plugin manifest entirely."""
    (repo / "README.md").write_text("pre-plugin.json content\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "root, before plugin.json existed")
    root_sha = git(repo, "rev-parse", "HEAD")

    _write_manifests(str(repo), "1.0.0", sha=root_sha)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "add manifests, pin to the pre-plugin.json root")

    ok, detail = mcc.main_catalogue_coherence(str(repo))
    assert not ok
    assert "has no .claude-plugin/plugin.json" in detail


def test_pinned_commit_unparseable_plugin_json_is_refused(repo):
    """source.sha resolves to a real, ancestor commit whose plugin.json is not valid JSON. The
    BRANCH TIP's own marketplace.json/plugin.json stay well-formed -- only the PINNED commit's
    plugin.json is malformed, isolating this from the malformed-marketplace.json case above."""
    os.makedirs(str(repo / ".claude-plugin"), exist_ok=True)
    (repo / ".claude-plugin" / "plugin.json").write_text("{not json")
    (repo / ".claude-plugin" / "marketplace.json").write_text(json.dumps(
        {"plugins": [{"name": "foundry", "version": "1.0.0", "source": {"sha": "0" * 40}}]}))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "content commit with malformed plugin.json")
    content_sha = git(repo, "rev-parse", "HEAD")

    _write_manifests(str(repo), "1.0.0", sha=content_sha)   # re-pin: well-formed manifests at HEAD
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "re-pin to the malformed-plugin.json content commit")

    ok, detail = mcc.main_catalogue_coherence(str(repo))
    assert not ok
    assert "unreadable" in detail


def test_git_unavailable_is_refused(repo, monkeypatch):
    """git itself cannot be RUN at all -- e.g. a CI image or session PATH missing the binary. The
    fixture is built with the REAL git before PATH is stripped, since building it needs git; only
    the check itself runs with git unreachable."""
    _write_manifests(str(repo), "1.0.0", sha="0" * 40)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "v1.0.0 content")

    empty_bin = repo.parent / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))

    ok, detail = mcc.main_catalogue_coherence(str(repo))
    assert not ok
    assert "could not run" in detail


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
