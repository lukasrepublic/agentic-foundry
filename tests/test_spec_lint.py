"""tests/test_spec_lint.py — (feat-foundry-wave-plan / PR #271 security finding 6):
`scripts/foundry-spec-lint.py`, the small CLI `/foundry:spec-review`'s Phase 0 invokes by command.

Exercises `lint_spec` directly (over/under fixtures) plus the CLI's exit-code contract. Never
touches the live repo tree — every fixture is a throwaway `tmp_path` spec.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

from conftest import REPO_ROOT, load_module

sl = load_module("scripts/foundry-spec-lint.py", "foundry_spec_lint")
# feat-foundry-spec-size-subcriteria-count (AC-SSC-1..6, AC-SSC-9): drive `spec_size_metrics` and
# the AC-SSC-9 nonconforming-token detector directly, plus foundry_authz's LIVE `_AC_TOKEN` for the
# AC-SSC-2 family's differential (never a re-stated copy of either pattern).
prep = load_module("scripts/foundry-audit-prepare.py", "foundry_audit_prepare")
authz = load_module("scripts/foundry_authz.py", "foundry_authz")

SPEC_LINT_SCRIPT = os.path.join(REPO_ROOT, "scripts", "foundry-spec-lint.py")

_CLEAN_SPEC = """# Clean spec

<!-- normative -->
- **AC-OK-1** (Requirement): When X, the system SHALL Y.
<!-- /normative -->
"""


def _oversize_spec_text(n_acs=15, words_per_ac=60):
    lines = ["# Oversize spec", "", "<!-- normative -->"]
    for i in range(1, n_acs + 1):
        filler = "word " * words_per_ac
        lines.append(f"- **AC-BIG-{i}** (Requirement): When trigger {i}, the system SHALL do "
                     f"thing {i}. {filler}")
    lines.append("<!-- /normative -->")
    return "\n".join(lines) + "\n"


_DANGLING_REF_SPEC = """# Dangling-reference spec

<!-- normative -->
- **AC-DANG-1** (Requirement): See [[feat-does-not-exist]] for context; the system SHALL Y.
<!-- /normative -->
"""


class TestLintSpecUnderCeiling:
    def test_clean_spec_passes(self, tmp_path):
        spec = tmp_path / "clean.md"
        spec.write_text(_CLEAN_SPEC, encoding="utf-8")
        ok, findings = sl.lint_spec(str(spec), project_dir=str(tmp_path))
        assert ok is True
        assert findings == []

    def test_just_under_ceiling_passes(self, tmp_path):
        # 14 ACs (== HARD_ACS, not over) and well under 8000 words.
        spec = tmp_path / "under.md"
        spec.write_text(_oversize_spec_text(n_acs=14, words_per_ac=5), encoding="utf-8")
        ok, findings = sl.lint_spec(str(spec), project_dir=str(tmp_path))
        assert ok is True
        assert findings == []


class TestLintSpecOverCeiling:
    def test_over_ac_count_fails_closed(self, tmp_path):
        spec = tmp_path / "oversize.md"
        spec.write_text(_oversize_spec_text(n_acs=15, words_per_ac=5), encoding="utf-8")
        ok, findings = sl.lint_spec(str(spec), project_dir=str(tmp_path))
        assert ok is False
        assert any("OVERSIZE" in f for f in findings)

    def test_over_word_count_fails_closed(self, tmp_path):
        # Few ACs but each padded well past the 8000-word ceiling in aggregate.
        spec = tmp_path / "wordy.md"
        spec.write_text(_oversize_spec_text(n_acs=2, words_per_ac=5000), encoding="utf-8")
        ok, findings = sl.lint_spec(str(spec), project_dir=str(tmp_path))
        assert ok is False
        assert any("OVERSIZE" in f for f in findings)

    def test_ceiling_is_hardcoded_not_adopter_tunable(self, tmp_path):
        """The whole point of security finding 6: a `.claude/foundry-project.json` gates.audit_size
        override must NOT relax this lint's ceiling (unlike the dormant engine's own soft
        warn/hard thresholds) -- prove HARD_ACS/HARD_WORDS ignore it."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "foundry-project.json").write_text(
            '{"gates": {"audit_size": {"hard_acs": 999, "hard_words": 999999}}}', encoding="utf-8")
        spec = tmp_path / "oversize.md"
        spec.write_text(_oversize_spec_text(n_acs=15, words_per_ac=5), encoding="utf-8")
        ok, findings = sl.lint_spec(str(spec), project_dir=str(tmp_path))
        assert ok is False, "a would-be adopter override must not relax the hardcoded ceiling"
        assert any("OVERSIZE" in f for f in findings)
        assert sl.HARD_ACS == 14 and sl.HARD_WORDS == 8000


class TestLintSpecReferenceClosure:
    def test_dangling_reference_fails_closed(self, tmp_path):
        spec = tmp_path / "dangling.md"
        spec.write_text(_DANGLING_REF_SPEC, encoding="utf-8")
        ok, findings = sl.lint_spec(str(spec), project_dir=str(tmp_path))
        assert ok is False
        assert any("DANGLING-REFERENCE" in f for f in findings)

    def test_no_cross_spec_references_passes(self, tmp_path):
        spec = tmp_path / "clean.md"
        spec.write_text(_CLEAN_SPEC, encoding="utf-8")
        ok, findings = sl.lint_spec(str(spec), project_dir=str(tmp_path))
        assert ok is True

    def _seed_corpus_atom(self, tmp_path, filename, ac_id):
        """Write one corpus atom (spec + sibling contract carrying `ac_id`) and return its dir."""
        d = tmp_path / "specs" / "some" / "grouping" / "cap"
        d.mkdir(parents=True, exist_ok=True)
        (d / filename).write_text(
            "# Target atom\n\n<!-- normative -->\n"
            f"- **{ac_id}** (Invariant): The thing SHALL always hold.\n"
            "<!-- /normative -->\n", encoding="utf-8")
        # AUTHORIZED, deliberately: a reference cited from the subject's own normative region is
        # load-bearing, so `gate_reference_closure` refuses it if the target atom is unauthorized.
        # That refusal is a SEPARATE, correct behaviour from the indexing bug under test here —
        # without this trailer these tests would go green/red for the wrong reason.
        (d / "acceptance-contract.yaml").write_text(
            f"checkpoints:\n  - ac_id: {ac_id}\n    surface: \"file:x\"\n    locator: \"x\"\n"
            "authorized:\n  operator_id: op_test\n  auth_seq: 1\n",
            encoding="utf-8")
        return d

    def _subject_citing(self, tmp_path, ac_id):
        spec = tmp_path / "subject.md"
        spec.write_text(
            "# Subject\n\n<!-- normative -->\n"
            f"- **AC-SUBJ-1** (Requirement): When X, the system SHALL Y, per {ac_id}.\n"
            "<!-- /normative -->\n", encoding="utf-8")
        return spec

    def test_reference_to_a_DELIVERY_atom_resolves(self, tmp_path):
        """REGRESSION. `_walk_specs` used to yield only `feat-*.md`, so in a workspace using the
        two-tree taxonomy — delivery atoms as `spec-<target_repo>-<capability>.md`, audit corpus as
        `feat-*.md` — NO delivery atom was ever indexed and EVERY reference to one was reported
        DANGLING. That is a fail-closed FALSE POSITIVE: it blocks correct specs, and it blocked
        Phase 0 of `/foundry:spec-review` for all 65 delivery atoms of the corpus where it was
        found."""
        self._seed_corpus_atom(tmp_path, "spec-infra-some-capability.md", "AC-DELIV-1")
        spec = self._subject_citing(tmp_path, "AC-DELIV-1")
        ok, findings = sl.lint_spec(str(spec), project_dir=str(tmp_path))
        assert ok is True, f"a reference to a spec-*.md delivery atom must resolve; got {findings}"
        assert findings == []

    def test_reference_to_an_AUDIT_CORPUS_atom_still_resolves(self, tmp_path):
        """The other half of the same bijection — widening the walker must not drop `feat-*.md`."""
        self._seed_corpus_atom(tmp_path, "feat-piiq-some-capability.md", "AC-AUDIT-1")
        spec = self._subject_citing(tmp_path, "AC-AUDIT-1")
        ok, findings = sl.lint_spec(str(spec), project_dir=str(tmp_path))
        assert ok is True, f"a reference to a feat-*.md audit atom must still resolve; got {findings}"

    def test_reference_to_a_NONEXISTENT_ac_still_fails_closed(self, tmp_path):
        """Falsifiability: widening the walker must not make reference closure vacuous. An AC that
        exists in NO atom of either tree must still be caught."""
        self._seed_corpus_atom(tmp_path, "spec-infra-some-capability.md", "AC-DELIV-1")
        spec = self._subject_citing(tmp_path, "AC-NOSUCH-9")
        ok, findings = sl.lint_spec(str(spec), project_dir=str(tmp_path))
        assert ok is False
        assert any("DANGLING-REFERENCE" in f for f in findings)


class TestLintSpecMissingFile:
    def test_missing_spec_fails_closed(self, tmp_path):
        ok, findings = sl.lint_spec(str(tmp_path / "nope.md"), project_dir=str(tmp_path))
        assert ok is False
        assert findings and "not found" in findings[0]


class TestCli:
    def test_cli_exit_0_on_clean_spec(self, tmp_path):
        spec = tmp_path / "clean.md"
        spec.write_text(_CLEAN_SPEC, encoding="utf-8")
        r = subprocess.run([sys.executable, SPEC_LINT_SCRIPT, str(spec), "--project-dir", str(tmp_path)],
                            capture_output=True, text=True)
        assert r.returncode == 0
        assert "OK" in r.stdout

    def test_cli_exit_1_on_oversize_spec(self, tmp_path):
        spec = tmp_path / "oversize.md"
        spec.write_text(_oversize_spec_text(n_acs=15, words_per_ac=5), encoding="utf-8")
        r = subprocess.run([sys.executable, SPEC_LINT_SCRIPT, str(spec), "--project-dir", str(tmp_path)],
                            capture_output=True, text=True)
        assert r.returncode == 1
        assert "OVERSIZE" in r.stderr


# =============================================================================================== #
# feat-foundry-spec-size-subcriteria-count (AC-SSC-1..6, AC-SSC-9): `spec_size_metrics` cannot see
# a letter-suffixed sub-criterion AC-ID at all (it counts ZERO, not one) while `foundry_authz`'s
# authorize-path grammar already accepts exactly that shape. Every test below drives the SHIPPED
# `_SIZE_AC_RE` / `nonconforming_ac_id_tokens` directly (never a re-implementation).
# =============================================================================================== #

_AC_TOKEN_ANCHORED_RE = re.compile("^" + authz._AC_TOKEN + "$")


def _fenced(*body_lines):
    return "\n".join(["<!-- normative -->", *body_lines, "<!-- /normative -->", ""])


class TestSuffixedAcCounting:
    """AC-SSC-1: a letter-suffixed AC-ID is no longer invisible to the counter."""

    def test_size_counts_letter_suffixed_ac_id(self):
        text = _fenced("- **AC-FOO-1a** (Requirement): a lettered sub-criterion.")
        ac_count, _ = prep.spec_size_metrics(text)
        assert ac_count == 1


class TestDifferentialGrammar:
    """AC-SSC-2a/2b/2c: the size grammar's TERMINAL now agrees with `foundry_authz._AC_TOKEN`
    (a single trailing lowercase letter), while its PREFIX ARITY stays deliberately divergent
    (one-or-more here, vs. `_AC_TOKEN`'s zero-or-more) — pinned as a function of the live patterns,
    with a mutation negative control so the assertions cannot be satisfied vacuously."""

    @staticmethod
    def _assert_terminal_agreement(size_re):
        """A probe set of PREFIXED tokens (so prefix arity never enters it) — valid (no suffix, or
        a single trailing lowercase letter) and each of the three "matches nothing at all" ill-formed
        shapes (the fourth, hyphen-before-letter, collapses onto its base rather than disagreeing on
        the terminal, so it belongs to AC-SSC-9's detector, not this terminal-agreement probe).
        Raises AssertionError (via a plain `assert`) the moment the two patterns disagree, or either
        disagrees with the expected outcome — this is what makes the mutation control convict a
        pattern with the optional-letter class removed."""
        probes = {
            "no_suffix": ("AC-FOO-3", True),
            "single_lower_suffix": ("AC-FOO-3a", True),
            "uppercase_suffix": ("AC-FOO-3A", False),
            "multiletter_suffix": ("AC-FOO-3ab", False),
            "digit_after_letter_suffix": ("AC-FOO-3a5", False),
        }
        for label, (tok, want) in probes.items():
            got_authz = bool(_AC_TOKEN_ANCHORED_RE.fullmatch(tok))
            got_size = bool(size_re.fullmatch(tok))
            assert got_authz is want, (label, tok, "foundry_authz._AC_TOKEN disagreed with expectation")
            assert got_size == got_authz, (label, tok, got_size, got_authz)

    def test_size_grammar_matches_authorize_terminal(self):
        self._assert_terminal_agreement(prep._SIZE_AC_RE)

    def test_prefix_arity_divergence_is_deliberate(self):
        """A prefix-less token (the shape NIST SP 800-53 uses for a control identifier) is
        recognized by `_AC_TOKEN` (authorize path, definition-scoped) but NOT by `_SIZE_AC_RE`
        (free-text size counter) — deliberately, per spec Clarification C2 / prior-art family 4."""
        tok = "AC-3"
        assert _AC_TOKEN_ANCHORED_RE.fullmatch(tok) is not None
        assert prep._SIZE_AC_RE.fullmatch(tok) is None
        text = _fenced(f"See control {tok} for the least-privilege discussion.")
        ac_count, _ = prep.spec_size_metrics(text)
        assert ac_count == 0

    def test_size_pattern_mutation_convicts_the_differential(self):
        """THE MUTATION NEGATIVE CONTROL (AC-SSC-2c). A mutant copy of `_SIZE_AC_RE` with the
        optional-letter class deleted (i.e. today's shipped defect) must FAIL the same terminal
        assertions `test_size_grammar_matches_authorize_terminal` runs against the live pattern —
        proving the assertions are a function of the pattern, not an assertion-free pass."""
        mutant = re.compile(r"\bAC(?:-[A-Z0-9]+)+-\d+\b")
        with pytest.raises(AssertionError):
            self._assert_terminal_agreement(mutant)


class TestDistinctTokenCounting:
    """AC-SSC-3: a base ID and each of its lettered forms are SEPARATE tokens — splitting one
    bundled criterion into two lettered sub-criteria INCREASES the count."""

    def test_distinct_ac_tokens_each_count_once(self):
        unsplit = _fenced(
            "- **AC-FOO-1** (Requirement): a bundled criterion.",
            "- **AC-FOO-2** (Requirement): another criterion.",
        )
        split = _fenced(
            "- **AC-FOO-1a** (Requirement): the first half.",
            "- **AC-FOO-1b** (Requirement): the second half.",
            "- **AC-FOO-2** (Requirement): another criterion.",
        )
        ac_unsplit, _ = prep.spec_size_metrics(unsplit)
        ac_split, _ = prep.spec_size_metrics(split)
        assert ac_unsplit == 2
        assert ac_split == 3
        assert ac_split == ac_unsplit + 1


def _at_ceiling_spec_text():
    lines = ["# At-ceiling spec (AC-SSC-5)", "", "<!-- normative -->"]
    for i in range(1, 14):
        lines.append(f"- **AC-CEIL-{i}** (Requirement): item {i} shall pass.")
    lines.append("- **AC-CEIL-14a** (Requirement): the fourteenth criterion, lettered.")
    lines.append("<!-- /normative -->")
    return "\n".join(lines) + "\n"


class TestAtCeilingBoundary:
    """AC-SSC-5: exactly fourteen distinct AC-ID tokens, at least one letter-suffixed, still
    passes — the over-correction guard."""

    def test_at_ceiling_with_suffixed_ac_passes(self, tmp_path):
        spec = tmp_path / "at_ceiling.md"
        spec.write_text(_at_ceiling_spec_text(), encoding="utf-8")
        ac_count, _ = prep.spec_size_metrics(spec.read_text(encoding="utf-8"))
        assert ac_count == 14
        ok, findings = sl.lint_spec(str(spec), project_dir=str(tmp_path))
        assert ok is True
        assert findings == []


def _measured_shape_text(n_plain):
    """`n_plain` plain AC-IDs plus one criterion replaced by two lettered sub-criteria — the two
    shapes measured on the live corpus 2026-07-29 (spec Design/notes, Clarification C4: in-module,
    not a vendored 600-line spec)."""
    lines = ["# Measured-shape fixture (AC-SSC-6)", "", "<!-- normative -->"]
    for i in range(1, n_plain + 1):
        lines.append(f"- **AC-SHAPE-{i}** (Requirement): plain criterion {i}.")
    idx = n_plain + 1
    lines.append(f"- **AC-SHAPE-{idx}a** (Requirement): sub-criterion a.")
    lines.append(f"- **AC-SHAPE-{idx}b** (Requirement): sub-criterion b.")
    lines.append("<!-- /normative -->")
    return "\n".join(lines) + "\n"


class TestMeasuredCorpusShapes:
    """AC-SSC-6: the two measured shapes (12 plain + one 2-way split -> 14; 11 plain + one 2-way
    split -> 13) become regression fixtures."""

    def test_measured_corpus_shapes(self):
        ac14, _ = prep.spec_size_metrics(_measured_shape_text(12))
        ac13, _ = prep.spec_size_metrics(_measured_shape_text(11))
        assert ac14 == 14
        assert ac13 == 13


class TestNonconformingAcIdWarning:
    """AC-SSC-9: an AC-ID-shaped token whose suffix satisfies neither grammar is never silently
    dropped — all four ill-formed shapes are detected by name."""

    def test_nonconforming_ac_id_shapes_all_warn(self):
        shapes = ["AC-FOO-1A", "AC-FOO-1ab", "AC-FOO-4a5", "AC-FOO-4-a"]
        for token in shapes:
            text = _fenced(token)
            assert prep.nonconforming_ac_id_tokens(text) == [token], token
