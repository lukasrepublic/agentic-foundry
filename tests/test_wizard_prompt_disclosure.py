"""feat-foundry-wizard-prompt-disclosure (ER #88) — the wizard's prompts must EXPLAIN themselves.

Every test name below is the exact `-k` filter the acceptance contract's checkpoints use.

Two layers, matching the house split established by tests/test_bootstrap_cli.py: the node:test
suite at cli/test/prompt-disclosure.test.mjs is the in-process unit half (driven here by
--test-name-pattern), and the mutation negative controls live here, copying cli/ to a temp root,
patching the copy, and asserting the SAME node test that guards the property goes red.

Fail-closed throughout: no skip/xfail marker, no warn-only branch.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_DIR = REPO_ROOT / "cli"
NODE = shutil.which("node") or "node"
REQUIRED_NODE_MAJOR = 22


def _node_major():
    try:
        out = subprocess.run([NODE, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    m = re.search(r"v(\d+)", out.stdout)
    return int(m.group(1)) if m else None


def _require_node():
    major = _node_major()
    if major is None:
        pytest.fail("node is not on PATH; this suite requires it to FAIL, never skip")
    if major < REQUIRED_NODE_MAJOR:
        pytest.fail(f"node major {major} is below the declared engines.node floor {REQUIRED_NODE_MAJOR}")


# `--test-name-pattern` is a JS REGEX, not a literal. Several of these test names carry regex
# metacharacters — "[blank]", "[]", "(y/n)", "[false]" — which silently become character classes
# and groups, matching the WRONG tests (or none) while node still exits 0. Escape before passing.
_JS_RE_META = r"\^$.|?*+()[]{}/"


def _as_literal_pattern(name):
    return "".join(("\\" + ch) if ch in _JS_RE_META else ch for ch in name)


def run_node_test(name, cwd=None, timeout=180):
    """Run ONE named node test by pattern. Returns the CompletedProcess."""
    _require_node()
    cmd = [NODE, "--test", "--test-name-pattern", _as_literal_pattern(name), "test/**/*.test.mjs"]
    return subprocess.run(
        cmd, cwd=str(cwd or CLI_DIR), capture_output=True, text=True, timeout=timeout
    )


def assert_named_test_passed(rc, name):
    """House convention (tests/test_bootstrap_cli.py): assert on the exact test NAME in both the
    default-reporter and TAP spellings PLUS `fail 0` — never on a pass COUNT. Under
    --test-name-pattern node still visits every file and reports a non-matching file as itself a
    passing test, so counts break the moment another cli/test/*.test.mjs file lands."""
    assert rc.returncode == 0, rc.stdout + rc.stderr
    passed = re.search(rf"^(?:✔|ok \d+ -) {re.escape(name)}", rc.stdout, re.M)
    assert passed, f"the named test did not run/pass:\n{rc.stdout}"
    assert re.search(r"(?:^|\s)(?:#|ℹ) fail 0\b", rc.stdout, re.M), rc.stdout


def _named_test_went_red(name, cwd):
    """True when the named test FAILED in the mutated copy (a crash counts — a mutation that
    breaks the module outright is still the control reddening)."""
    rc = run_node_test(name, cwd=cwd)
    if rc.returncode != 0:
        return True
    return bool(re.search(rf"^not ok \d+ - {re.escape(name)}", rc.stdout, re.M))


# ── AC-WPD-1 ────────────────────────────────────────────────────────────────────────────────
def test_every_record_declares_a_non_empty_description():
    assert_named_test_passed(
        run_node_test("AC-WPD-1: every record declares a non-empty description"),
        "AC-WPD-1: every record declares a non-empty description",
    )


# ── AC-WPD-2 / AC-WPD-3 ─────────────────────────────────────────────────────────────────────
def test_description_prints_immediately_above_the_prompt_line():
    name = "AC-WPD-2: the description renders above the prompt line"
    assert_named_test_passed(run_node_test(name), name)


def test_one_indented_line_per_choice_above_the_prompt():
    name = "AC-WPD-3: one indented line per choice, between the description and the prompt"
    assert_named_test_passed(run_node_test(name), name)


# ── AC-WPD-4 — the ER #88 regression, plus its mutation control ─────────────────────────────
def test_a_record_with_a_default_renders_it_even_when_it_has_choices():
    name = "AC-WPD-4: a record with a default renders it even when it declares choices"
    assert_named_test_passed(run_node_test(name), name)


def test_mutation_exclusive_suffix_ternary_is_caught(tmp_path):
    """Reinstating the exact defect ER #88 reported — a mutually-exclusive choices-or-default
    ternary — must turn the AC-WPD-4 test red. Without this control, a promptSuffix() that simply
    always returned the choice list would still satisfy a weaker assertion."""
    alt = tmp_path / "ctrl-ternary"
    shutil.copytree(CLI_DIR, alt)
    target = alt / "src" / "answers.mjs"
    text = target.read_text()
    mutated = text.replace(
        """export function promptSuffix(rec) {
  const parts = [];
  if (rec.choices) parts.push(`(${rec.choices.join('/')})`);
  else if (rec.type === 'boolean') parts.push('(y/n)');
  const dflt = defaultToken(rec);
  if (dflt) parts.push(dflt);
  return parts.length ? ` ${parts.join(' ')}` : '';
}""",
        """export function promptSuffix(rec) {
  return rec.choices ? ` (${rec.choices.join('/')})` : (defaultToken(rec) ? ` ${defaultToken(rec)}` : '');
}""",
    )
    assert mutated != text, "the mutation did not land — promptSuffix's shape changed upstream"
    target.write_text(mutated)
    name = "AC-WPD-4: a record with a default renders it even when it declares choices"
    assert _named_test_went_red(name, cwd=alt), "the exclusive-ternary defect was NOT caught"
    # sanity: the same test passes in the UNMUTATED tree, so the control is not always-red
    assert_named_test_passed(run_node_test(name), name)


# ── AC-WPD-5 / -6 / -7 — default rendering ──────────────────────────────────────────────────
def test_non_empty_string_default_renders_in_brackets():
    name = "AC-WPD-5: a non-empty string default renders in brackets"
    assert_named_test_passed(run_node_test(name), name)


def test_empty_string_default_renders_blank():
    name = "AC-WPD-6: an empty-string default renders [blank], never [] and never nothing"
    assert_named_test_passed(run_node_test(name), name)


def test_boolean_record_renders_y_n_and_letter_default():
    name = "AC-WPD-7: a boolean record renders (y/n) and a letter default, never [false]"
    assert_named_test_passed(run_node_test(name), name)


# ── AC-WPD-8 / AC-WPD-14 — out-of-project disclosure, plus its mutation control ─────────────
def test_out_of_project_record_discloses_scope_and_each_artifact():
    name = "AC-WPD-8: an out-of-project record states the scope of its effect"
    assert_named_test_passed(run_node_test(name), name)


def test_out_of_project_record_names_each_artifact():
    name = "AC-WPD-14: an out-of-project record names each artifact written outside the root"
    assert_named_test_passed(run_node_test(name), name)


def test_mutation_dropping_the_scope_sentence_is_caught(tmp_path):
    """Deleting the scope statement while KEEPING the artifact list must turn AC-WPD-8 red.

    This is the control that encodes the real incident: the draft copy named the artifacts
    ("writes global git config", the includeIf keys) without stating that the effect is confined
    to this folder, and the framework's own operator read it as changing their global identity.
    Copy that names the file without naming the scope is worse than no copy, so the assertion must
    not be satisfiable by the artifact list alone."""
    alt = tmp_path / "ctrl-scope"
    shutil.copytree(CLI_DIR, alt)
    target = alt / "src" / "questions.mjs"
    text = target.read_text()
    mutated = text.replace(
        "  If you enter a slug, your global git identity is NOT changed. Git is\\n' +\n"
        "      '  taught a rule that applies ONLY inside this folder:\\n' +\n",
        "  If you enter a slug, the following are written:\\n' +\n",
    )
    assert mutated != text, "the mutation did not land — the ghAccount copy changed upstream"
    target.write_text(mutated)
    name = "AC-WPD-8: an out-of-project record states the scope of its effect"
    assert _named_test_went_red(name, cwd=alt), "dropping the scope sentence was NOT caught"
    assert_named_test_passed(run_node_test(name), name)


# ── AC-WPD-9 ────────────────────────────────────────────────────────────────────────────────
def test_gh_account_declares_writes_outside_project():
    name = "AC-WPD-9: the ghAccount record declares writesOutsideProject"
    assert_named_test_passed(run_node_test(name), name)


# ── AC-WPD-10 / -11 — --help ────────────────────────────────────────────────────────────────
def test_help_renders_description_continuation_line():
    name = "AC-WPD-10: --help renders each description as an indented continuation"
    assert_named_test_passed(run_node_test(name), name)


def test_help_renders_one_line_per_choice():
    name = "AC-WPD-11: --help renders one line per choice"
    assert_named_test_passed(run_node_test(name), name)


# ── AC-WPD-12 — single source of truth ──────────────────────────────────────────────────────
def test_rendered_copy_derives_from_the_question_table_only():
    name = "AC-WPD-12: every rendered line derives from the question table, not a second list"
    assert_named_test_passed(run_node_test(name), name)
