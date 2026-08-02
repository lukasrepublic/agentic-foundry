"""feat-foundry-release-suite-gate — AC-RSG-1..4.

The v0.27.0 incident in one sentence: the cut gate validated packaging metadata thoroughly and never ran the
tests, so `READY` was returned truthfully while the candidate tree was failing its own suite, and a tag
shipped with a stale install pin. These tests drive the REAL preflight over fixture trees and assert the
ordering hole is closed.
"""
import importlib.util
import json
import os
import subprocess
import sys

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
