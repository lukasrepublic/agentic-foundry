"""feat-foundry-release-suite-gate — AC-RSG-1..4.

The v0.27.0 incident in one sentence: the cut gate validated packaging metadata thoroughly and never ran the
tests, so `READY` was returned truthfully while the candidate tree was failing its own suite, and a tag
shipped with a stale install pin. These tests drive the REAL preflight over fixture trees and assert the
ordering hole is closed.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUT = os.path.join(REPO_ROOT, "scripts", "foundry-cut-release.py")


def _mod():
    spec = importlib.util.spec_from_file_location("cutrel", CUT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _tree(tmp_path, version="9.9.9", *, plugin_v=None, mp_v=None, mp_ref=None, changelog_v=None,
          tests=True, test_body="def test_ok():\n    assert True\n"):
    """A candidate tree whose metadata is CORRECT by default -- the v0.27.0 shape is 'good metadata, red
    test', so the fixture must be able to isolate the suite as the only variable."""
    root = tmp_path
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "foundry", "version": plugin_v or version}), encoding="utf-8")
    (root / ".claude-plugin" / "marketplace.json").write_text(json.dumps({
        "plugins": [{"name": "foundry", "version": mp_v or version,
                     "source": {"source": "github", "repo": "lukasrepublic/agentic-foundry",
                                "ref": mp_ref or ("v" + version), "sha": "0" * 40}}]}), encoding="utf-8")
    (root / "CHANGELOG.md").write_text("# Changelog\n\n## v%s — test\n\n- entry\n" % (changelog_v or version),
                                       encoding="utf-8")
    if tests:
        (root / "tests").mkdir(exist_ok=True)
        (root / "tests" / "test_fixture.py").write_text(test_body, encoding="utf-8")
    # A candidate tree is a COMMITTED checkout. preflight now fails closed when it cannot inspect
    # the repo (tag_pin_coherence) and when the tree is dirty (worktree_clean) — the tag lands on a
    # commit created after the acceptance verdict, so an uncommitted edit would ship under the
    # release tag ungated. A fixture without .git would be proving READY on a tree the gate is
    # structurally unable to check, which is the fail-open these preconditions exist to close.
    for cmd in (["init", "-q", "-b", "main"],
                ["config", "user.email", "t@example.invalid"], ["config", "user.name", "t"],
                ["add", "-A"], ["commit", "-qm", "fixture"]):
        subprocess.run(["git", "-C", str(root), *cmd], capture_output=True, text=True, timeout=30)
    return str(root)


def _real_runner(tree):
    p = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q"], cwd=tree,
                       capture_output=True, text=True, timeout=300)
    return p.returncode, p.stdout, p.stderr


# ------------------------------------------------------------------ AC-RSG-1 --
def test_suite_runs_and_refuses_on_failure(tmp_path):
    m = _mod()
    red = _tree(tmp_path / "red", test_body="def test_broken():\n    assert False\n")
    label, ok, detail = m.suite_check(red, runner=_real_runner)
    assert ok is False
    assert "TESTS FAILED" in detail
    green = _tree(tmp_path / "green")
    _l, ok2, _d = m.suite_check(green, runner=_real_runner)
    assert ok2 is True, "a green tree must pass -- otherwise the check is vacuously refusing"


# ------------------------------------------------------------------ AC-RSG-2 --
def test_unrunnable_suite_refuses_fail_closed(tmp_path):
    """Anything that prevents a REAL verdict refuses, and names its cause distinctly from a test failure."""
    m = _mod()
    no_tests = _tree(tmp_path / "notests", tests=False)
    _l, ok, detail = m.suite_check(no_tests)
    assert ok is False and "no tests/ directory" in detail and "TESTS FAILED" not in detail

    t = _tree(tmp_path / "t1")
    # pytest absent
    _l, ok, detail = m.suite_check(t, runner=lambda _t: (1, "", "No module named pytest"))
    assert ok is False and "not importable" in detail and "TESTS FAILED" not in detail
    # nothing collected
    _l, ok, detail = m.suite_check(t, runner=lambda _t: (5, "no tests ran", ""))
    assert ok is False and "no tests collected" in detail
    # internal/usage error
    _l, ok, detail = m.suite_check(t, runner=lambda _t: (4, "", "usage error"))
    assert ok is False and "could not complete" in detail
    # the runner itself explodes
    def boom(_t):
        raise OSError("exec failed")
    _l, ok, detail = m.suite_check(t, runner=boom)
    assert ok is False and "could not run the suite" in detail and "OSError" in detail
    # a genuine failure surfaces the suite's OWN failing-test names
    _l, ok, detail = m.suite_check(t, runner=lambda _t: (1, "FAILED tests/test_x.py::test_y - boom\n1 failed", ""))
    assert ok is False and "test_x.py::test_y" in detail


# ------------------------------------------------------------------ AC-RSG-3 --
def test_metadata_precondition_refuses_without_running_suite(tmp_path):
    """The cheap checks short-circuit FIRST: a typo'd version must not pay for a full suite run."""
    m = _mod()
    ran = {"n": 0}

    def counting(_t):
        ran["n"] += 1
        return 0, "1 passed", ""

    bad = _tree(tmp_path / "bad", version="9.9.9", mp_v="1.2.3")     # manifests disagree
    pf = m.preflight(bad, "9.9.9", suite_runner=counting)
    assert any(not c[1] for c in pf)
    assert ran["n"] == 0, "the suite was run despite a cheap metadata precondition already failing"
    assert all(c[0] != m.SUITE_CHECK_LABEL for c in pf), "the suite check must be absent, not merely skipped"

    good = _tree(tmp_path / "good", version="9.9.9")
    pf2 = m.preflight(good, "9.9.9", suite_runner=counting)
    assert ran["n"] == 1, "a metadata-clean tree must actually run the suite"
    assert any(c[0] == m.SUITE_CHECK_LABEL and c[1] for c in pf2)


# ------------------------------------------------------------------ AC-RSG-4 --
def test_antitaut_good_metadata_red_test_refuses_no_plan(tmp_path):
    """THE v0.27.0 SHAPE. Metadata perfect, one red test. Before this atom the cut returned READY here."""
    m = _mod()
    tree = _tree(tmp_path / "v0270", version="9.9.9", test_body="def test_pin():\n    assert False\n")
    r = m.cut_release(tree, "9.9.9",
                      acceptance_fn=lambda **_k: {"verdict": "pass"},   # acceptance would have PASSED
                      er_state_fn=lambda *_a, **_k: {},
                      suite_runner=_real_runner)
    assert r["state"] == "refused", "a red suite must refuse the cut even when acceptance passes"
    assert r["stage"] == "preflight"
    assert r["plan"] is None, "no publish plan may be emitted"
    assert any("TESTS FAILED" in f for f in r["failures"])
    # the four metadata checks all PASSED -- proving the refusal came from the suite, not from bad metadata
    metadata = [c for c in r["preflight"] if c[0] != m.SUITE_CHECK_LABEL]
    assert all(c[1] for c in metadata), "fixture invalid: metadata must be clean for this to be the v0.27.0 shape"


def test_cli_path_never_injects_a_suite_runner(tmp_path):
    """THE GAP THAT LET A BROKEN CLI PASS 1051 TESTS: nothing drove main().

    A stub runner wired into the real cut path would make every release report a fabricated green suite --
    strictly worse than the v0.27.0 hole this atom closes, because v0.27.0 at least made no claim about
    tests. Asserted three ways: statically (no injection on the CLI line, the stub not module-level) and
    dynamically (the real CLI over a red tree must refuse).
    """
    src = open(CUT, encoding="utf-8").read()
    # Strip comments before matching: the CLI body deliberately DISCUSSES suite_runner in a warning
    # comment, and a naive substring check convicts the prose rather than the code. (Third time this
    # class of false positive has bitten in this repo -- match code, not commentary.)
    body_code = "\n".join(ln.split("#", 1)[0] for ln in src.split("def main(", 1)[1].splitlines())
    assert "suite_runner" not in body_code, "the CLI path must never pass a suite_runner"
    module_level = src.split("def _selftest(", 1)[0]
    assert "_GREEN_SUITE =" not in module_level, \
        "the stub runner must live inside _selftest, not at module level where main() can reach it"

    red = _tree(tmp_path / "cli_red", version="9.9.9", test_body="def test_pin():\n    assert False\n")
    p = subprocess.run([sys.executable, CUT, "--tree", red, "--version", "9.9.9"],
                       capture_output=True, text=True, timeout=300)
    assert p.returncode != 0, "the CLI must not exit 0 on a red candidate tree"
    combined = p.stdout + p.stderr
    assert "Traceback" not in combined, "the CLI must produce a verdict, not a traceback"
    assert "TESTS FAILED" in combined or "REFUSED" in combined.upper(), combined[-400:]


def test_suite_runner_env_is_scrubbed_of_pytest_addopts(tmp_path):
    """The verdict must be a function of the TREE, not the operator's shell: a leftover
    PYTEST_ADDOPTS deselecting the pin test would otherwise reproduce v0.27.0 through the gate."""
    m = _mod()
    red = _tree(tmp_path / "addopts", version="9.9.9",
                test_body="def test_pin():\n    assert False\n")
    os.environ["PYTEST_ADDOPTS"] = "--deselect tests/test_fixture.py::test_pin"
    try:
        _l, ok, detail = m.suite_check(red)     # real runner, no injection
    finally:
        os.environ.pop("PYTEST_ADDOPTS", None)
    assert ok is False, "PYTEST_ADDOPTS from the environment silently weakened the gate: %s" % detail


# ------------------------------------------------------------ AC-ILU-13 (feat-foundry-install-line-unpinning) --
def _add_preceding_changelog_entry(tree, preceding_version):
    """Appends a `## v<preceding_version> — test` section AFTER the target's own section (the
    `_tree` fixture writes exactly one) and commits, so `_immediately_preceding_release` has a real
    CHANGELOG-recorded prior release to find -- grounding the target-or-preceding tolerance in the
    project's own recorded history rather than a bare semver guess."""
    path = Path(tree) / "CHANGELOG.md"
    path.write_text(
        path.read_text(encoding="utf-8") + "\n## v%s — test\n\n- entry\n" % preceding_version,
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(tree), "add", "-A"], capture_output=True, text=True, timeout=30)
    subprocess.run(["git", "-C", str(tree), "commit", "-qm", "preceding changelog entry"],
                   capture_output=True, text=True, timeout=30)


def test_deferred_catalogue_bump_is_not_refused(tmp_path):
    """AC-ILU-13: the deferred-bump sequencing AC-ILU-11 prescribes -- release commit R bumps
    plugin.json ALONE, leaving marketplace.json's version/ref deliberately at the PREVIOUS release
    (the bump lands one commit later, in the re-pin commit R2) -- must NOT be refused on that basis,
    and the publish plan must still be emitted. Before this atom, preflight's
    'marketplace.json version == target' AND 'marketplace source.ref == vTARGET' preconditions BOTH
    refused every such tree with "bump BOTH manifests", making the prescribed sequencing
    unexecutable against the gate that enforces it -- this covers both halves explicitly."""
    m = _mod()
    # R-shape: plugin.json at the target, marketplace.json deliberately left at the
    # CHANGELOG-recorded PRECEDING release (both version and source.ref lagging, self-consistent
    # with each other) -- exactly what AC-ILU-11's deferred sequencing produces.
    tree = _tree(tmp_path / "deferred", version="9.9.9", plugin_v="9.9.9",
                 mp_v="9.9.8", mp_ref="v9.9.8")
    _add_preceding_changelog_entry(tree, "9.9.8")
    r = m.cut_release(tree, "9.9.9",
                      acceptance_fn=lambda **_k: {"verdict": "pass"},
                      er_state_fn=lambda *_a, **_k: {},
                      suite_runner=_real_runner)
    assert r["state"] == "ready", (
        "a deferred catalogue bump must not be refused: %r" % (r.get("failures"),)
    )
    assert r["plan"], "a deferred catalogue bump must still emit the publish plan"
    metadata = [c for c in r["preflight"] if c[0] != m.SUITE_CHECK_LABEL]
    assert all(c[1] for c in metadata), (
        "the deferred (lagging-but-self-consistent) marketplace.json must pass every metadata "
        "precondition: %r" % (metadata,)
    )

    # the converse: marketplace.json AHEAD of target must still refuse -- proving the relaxation
    # tolerates the recorded PRECEDING release only, not an arbitrary mismatch.
    ahead_tree = _tree(tmp_path / "ahead", version="9.9.9", plugin_v="9.9.9",
                       mp_v="10.0.0", mp_ref="v10.0.0")
    pf = m.preflight(ahead_tree, "9.9.9", suite_runner=lambda _t: (0, "1 passed", ""))
    assert any(not c[1] for c in pf), "marketplace.json AHEAD of target must still refuse"

    # a marketplace.json lagging by MORE than the one recorded preceding release must still
    # refuse -- "never arbitrary": only target or the immediately-preceding release is tolerated.
    too_far_tree = _tree(tmp_path / "too-far", version="9.9.9", plugin_v="9.9.9",
                         mp_v="9.9.7", mp_ref="v9.9.7")
    _add_preceding_changelog_entry(too_far_tree, "9.9.8")   # the recorded preceding release is 9.9.8, not 9.9.7
    pf3 = m.preflight(too_far_tree, "9.9.9", suite_runner=lambda _t: (0, "1 passed", ""))
    assert any(not c[1] for c in pf3), \
        "a marketplace.json further behind than the one recorded preceding release must still refuse"

    # THE SOURCE.REF HALF, EXPLICITLY (the coordinator's correction): a marketplace.json whose
    # version is the tolerated preceding release, but whose OWN source.ref does not name that same
    # version, must still refuse -- self-consistency is still required on the ref side too, proving
    # a future change that re-tightens only the version check (and not source.ref) is convicted.
    inconsistent_tree = _tree(tmp_path / "inconsistent", version="9.9.9", plugin_v="9.9.9",
                              mp_v="9.9.8", mp_ref="v9.9.9")
    _add_preceding_changelog_entry(inconsistent_tree, "9.9.8")
    pf2 = m.preflight(inconsistent_tree, "9.9.9", suite_runner=lambda _t: (0, "1 passed", ""))
    assert any(not c[1] for c in pf2), \
        "a marketplace.json source.ref naming a version it does not itself carry must still refuse"
    ref_check = next(c for c in pf2 if c[0] == "marketplace source.ref == vTARGET")
    assert ref_check[1] is False, "the source.ref self-consistency check specifically must refuse"


def test_antitaut_same_tree_green_test_proceeds(tmp_path):
    """The positive control: identical tree, test passing -> reaches acceptance and emits a plan. Without
    this, the refusal above could be caused by a malformed fixture rather than by the suite."""
    m = _mod()
    tree = _tree(tmp_path / "ok", version="9.9.9", test_body="def test_pin():\n    assert True\n")
    r = m.cut_release(tree, "9.9.9",
                      acceptance_fn=lambda **_k: {"verdict": "pass"},
                      er_state_fn=lambda *_a, **_k: {},
                      suite_runner=_real_runner)
    assert r["state"] == "ready", r.get("failures")
    assert r["plan"], "a green tree must still emit the publish plan"
    assert any(c[0] == m.SUITE_CHECK_LABEL and c[1] for c in r["preflight"])




# ------------------------------------------------------------ security review FIX 1/6 (feat-foundry-install-line-unpinning) --
def _repin_comment(plan):
    """The plan's human-readable `# edit .claude-plugin/marketplace.json: ...` re-pin instruction
    comment, or None if no such comment is present. Isolated as a pure function so the positive
    test and its negative control drive the exact same locator."""
    return next(
        (s for s in plan if isinstance(s, str) and s.lstrip().startswith("#")
         and ".claude-plugin/marketplace.json" in s and "source.sha" in s),
        None,
    )


def _repin_instruction_clause(comment):
    """Security-review FIX 6: the shipped comment is shaped
    `# edit .claude-plugin/marketplace.json: <INSTRUCTION>  # <RATIONALE prose>` -- two comment
    markers, not one. The FIX 1 check originally searched the WHOLE string for the substrings
    "version"/"source.ref"/the version number, but the RATIONALE prose independently contains
    "plugins[].version", "advertising the wrong version", and (inside its own worked example)
    the literal version digits -- so reverting ONLY the instruction while leaving the rationale
    untouched left that check green, which is exactly the regression it exists to catch. This
    isolates the instruction clause: the text between the comment's own leading '#' and the
    SECOND '#' (the rationale's opening marker). A comment with no second '#' is entirely
    instruction."""
    if comment is None:
        return ""
    parts = comment.split("#", 2)
    return parts[1] if len(parts) > 1 else ""


# Structural (regex-captured assignment), never substring presence: a `plugins[foundry].version =
# <value>` assignment naming exactly the target version, and a `source.ref = ...` assignment
# present at all -- both anchored to a `key <spacing> = <spacing> value` shape, not merely to the
# words "version"/"source.ref" occurring anywhere (which rationale prose also satisfies).
_VERSION_ASSIGNMENT_RE = re.compile(r"plugins\[foundry\]\.version\s*=\s*([0-9][0-9.]*)")
_SOURCE_SHA_ASSIGNMENT_RE = re.compile(r"source\.sha\s*=\s*\S")
_SOURCE_REF_ASSIGNMENT_RE = re.compile(r"source\.ref\s*=\s*v?[0-9][0-9.]*")


def _repin_comment_names_the_version_bump(comment, version):
    """True iff the re-pin comment's INSTRUCTION CLAUSE (never its rationale prose) structurally
    assigns `plugins[foundry].version` to the target version, AND still names a `source.ref`
    assignment. Pure and parameterized so both the real plan (positive) and synthetic pre-fix and
    realistic-regression comments (negative controls) drive the exact same check."""
    clause = _repin_instruction_clause(comment)
    if not clause:
        return False
    if not _SOURCE_SHA_ASSIGNMENT_RE.search(clause):
        return False
    m = _VERSION_ASSIGNMENT_RE.search(clause)
    if not m or m.group(1) != version:
        return False
    return bool(_SOURCE_REF_ASSIGNMENT_RE.search(clause))


def test_publish_plan_names_the_version_bump_in_r2(tmp_path):
    """Security-review FIX 1: with the deferred-bump sequencing (AC-ILU-11), R2 is the ONLY commit
    that ever bumps marketplace.json's `version` -- so the emitted publish plan's re-pin step MUST
    tell the operator to set it there, alongside `source.sha` and `source.ref`. Before this fix the
    emitted comment named only `source.sha` and `source.ref`; an operator following it verbatim
    produced an R2 with `version` still at the PREVIOUS release while `source.ref` named the new
    tag -- a mismatch `tag_pin_coherence` does NOT convict (it inspects `source.ref`, never
    `plugins[].version`), so `--verify-tag` would print TAG-PIN-COHERENT over a catalogue
    advertising a version its own commit does not carry."""
    m = _mod()
    plan = m.publish_plan(str(tmp_path), "9.9.9")
    comment = _repin_comment(plan)
    assert comment is not None, "no re-pin instruction comment found in the emitted plan"
    assert _repin_comment_names_the_version_bump(comment, "9.9.9"), (
        "the re-pin instruction must name the `version` bump, not just source.sha/source.ref -- "
        "otherwise the emitted plan under-specifies R2: %r" % (comment,)
    )


def test_publish_plan_version_bump_assertion_is_not_vacuous():
    """Security-review FIX 1/6 meta-test. Two negative controls, not one:

    (a) the bare pre-fix comment (no rationale at all) -- the original control, kept because a
        comment carrying NO rationale must still be convicted.
    (b) THE REALISTIC REGRESSION (security-review FIX 6): derived from the REAL, current plan's
        comment -- its instruction clause reverted to the pre-fix shape, its rationale prose left
        COMPLETELY INTACT. The rationale independently contains "version", the digits "9.9.9" (in
        its own worked source.ref example) and "plugins[].version" -- so the prior whole-string
        substring check passed this shape, which is precisely the defect FIX 6 closes. Both
        controls must be convicted for the assertion above to be trusted."""
    pre_fix_comment = (
        "# edit .claude-plugin/marketplace.json: set plugins[foundry].source.sha = $CONTENT "
        "(and source.ref = v9.9.9)"
    )
    assert not _repin_comment_names_the_version_bump(pre_fix_comment, "9.9.9"), (
        "the pre-fix-shaped comment (no `version` mention, no rationale) was wrongly accepted"
    )

    m = _mod()
    real_comment = _repin_comment(m.publish_plan("/tmp/x", "9.9.9"))
    assert real_comment is not None, "setup: the real plan carries no re-pin comment to derive from"
    real_clause = _repin_instruction_clause(real_comment)
    assert "version" in real_clause, "setup invalid: the real instruction clause lost the version bump"
    rationale = real_comment.split("#", 2)[2] if real_comment.count("#") >= 2 else ""
    assert rationale and ("version" in rationale), (
        "setup invalid: the real comment's rationale no longer independently mentions "
        "version -- the realistic-regression control needs it to"
    )
    reverted_instruction = " edit .claude-plugin/marketplace.json: set plugins[foundry].source.sha = $CONTENT, and source.ref = v9.9.9  "
    realistic_regression = "#" + reverted_instruction + "#" + rationale
    assert not _repin_comment_names_the_version_bump(realistic_regression, "9.9.9"), (
        "THE REALISTIC REGRESSION was wrongly accepted: instruction reverted to the pre-fix shape "
        "while the rationale prose (which independently mentions version/9.9.9) was left intact -- "
        "a whole-string substring check passes this; the structural, clause-scoped check must not: "
        "%r" % (realistic_regression,)
    )

    # and the positive form, with `version` present in the INSTRUCTION clause, must be accepted --
    # proving the check is not unconditionally red either.
    assert _repin_comment_names_the_version_bump(real_comment, "9.9.9"), (
        "the real (fixed) plan comment was wrongly rejected: %r" % (real_comment,)
    )


# ------------------------------------------------------------ security review FIX 4 (feat-foundry-install-line-unpinning) --
def test_unhashable_marketplace_version_refuses_named_not_traceback(tmp_path):
    """Security-review FIX 4: `plugins[].version` is read straight out of a JSON manifest with no
    schema validation upstream of preflight(). A malformed-but-valid-JSON manifest can carry a
    JSON object/array there instead of a string -- UNHASHABLE, so a bare `value in a_set` raises an
    unhandled TypeError. main() catches only CutReleaseError, so an unguarded raise here would
    traceback instead of naming the precondition that failed. preflight() must instead return a
    NAMED refusal on that check, never raise."""
    m = _mod()
    tree = _tree(tmp_path / "malformed", version="9.9.9", plugin_v="9.9.9")
    manifest_path = Path(tree) / ".claude-plugin" / "marketplace.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["plugins"][0]["version"] = {"not": "a string"}   # malformed but valid JSON
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    subprocess.run(["git", "-C", str(tree), "add", "-A"], capture_output=True, text=True, timeout=30)
    subprocess.run(["git", "-C", str(tree), "commit", "-qm", "malformed marketplace version"],
                   capture_output=True, text=True, timeout=30)

    # must not raise
    checks = m.preflight(tree, "9.9.9", suite_runner=lambda _t: (0, "1 passed", ""))
    named = next((c for c in checks if c[0] == "marketplace.json version == target (bump BOTH manifests)"), None)
    assert named is not None, "the malformed-version check must still be NAMED in the returned checks"
    assert named[1] is False, "an unhashable marketplace.json version must refuse, not silently pass"
