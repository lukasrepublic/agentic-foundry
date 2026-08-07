"""tests/test_ceremony_tier.py — feat-foundry-ceremony-tiering (GP-2).

Exercises `scripts/foundry_ceremony_tier.py`, the deterministic blast-radius classifier that
sizes an atom (`files`, `new_contract_surface`, `ambiguity`) into a tier from the closed set
`{trivial, small, standard, large}` under the MAXIMUM rule, applies the one-way security
sensitivity override, and returns the verb mask that tier runs.

Naming convention (load-bearing for the acceptance-contract checkpoints): every test function
name below carries its AC token (`test_ctr1_...`, `test_ctr2_...`, ...) so
`pytest -k ctr<n>` selects exactly that AC's tests.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest
import yaml

from conftest import REPO_ROOT, load_module

ct = load_module("scripts/foundry_ceremony_tier.py", "foundry_ceremony_tier")

CLI = os.path.join(REPO_ROOT, "scripts", "foundry_ceremony_tier.py")
# `security-path` moved to the base-evaluated workflow (pull_request_target) so a fork cannot
# rewrite the gate that grades it. This locator follows the JOB, not the filename — the CTR-4
# "one source of truth" claim is about the shipped gate literal, wherever it ships.
BTB_GATES = os.path.join(REPO_ROOT, ".github", "workflows", "btb-gates-base.yml")
FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "ceremony_tier_fixture.md")


def _run_cli(*args):
    return subprocess.run(
        [sys.executable, CLI, *args], capture_output=True, text=True, cwd=REPO_ROOT
    )


# ---------------------------------------------------------------------------
# AC-CTR-1 — tier resolution: exactly one tier from the closed set, the MAXIMUM of the signals.
# ---------------------------------------------------------------------------


class TestCTR1TierResolution:
    @pytest.mark.parametrize(
        "files, new_contract_surface, ambiguity, expected",
        [
            (1, False, "none", "trivial"),
            (0, False, "none", "trivial"),
            (3, False, "low", "small"),
            (5, False, "low", "small"),
            (10, False, "medium", "standard"),
            (15, False, "medium", "standard"),
            (16, False, "medium", "large"),
            (100, False, "high", "large"),
            # the maximum rule: a single elevated signal pulls the whole result up.
            (1, False, "high", "large"),
            (1, True, "none", "standard"),
            (2, True, "none", "standard"),
        ],
    )
    def test_ctr1_tier_is_the_max_of_the_three_signals(
        self, files, new_contract_surface, ambiguity, expected
    ):
        record = ct.classify(files, new_contract_surface, ambiguity)
        assert record.tier == expected
        assert record.classified_tier == expected

    def test_ctr1_tier_is_always_in_the_closed_set(self):
        for files in (0, 1, 2, 5, 6, 15, 16, 40):
            for new_contract_surface in (False, True):
                for ambiguity in ct.AMBIGUITY_LEVELS:
                    record = ct.classify(files, new_contract_surface, ambiguity)
                    assert record.tier in ct.TIERS

    def test_ctr1_scenario_one_file_new_contract_surface_is_standard(self):
        # Spec scenario: 1 file, ambiguity none, new-contract-surface True -> standard (the two
        # trivial signals do not pull the contract-surface signal down).
        record = ct.classify(1, True, "none")
        assert record.tier == "standard"

    def test_ctr1_unknown_ambiguity_is_rejected(self):
        with pytest.raises(ValueError):
            ct.classify(1, False, "extreme")


# ---------------------------------------------------------------------------
# AC-CTR-2 — deterministic and pure: identical inputs -> identical tier/mask/line, no
# dependence on wall clock, network, env vars, or filesystem state beyond the declared paths.
# ---------------------------------------------------------------------------


class TestCTR2DeterministicAndPure:
    def test_ctr2_repeated_invocations_are_identical(self):
        kwargs = dict(
            files=7,
            new_contract_surface=True,
            ambiguity="medium",
            scope_paths=("scripts/foo.py",),
            spec_text="a boring change",
        )
        a = ct.classify(**kwargs)
        b = ct.classify(**kwargs)
        assert a.tier == b.tier
        assert a.mask == b.mask
        assert a.line == b.line
        assert a.signals == b.signals
        assert a.triggers == b.triggers

    def test_ctr2_no_env_var_dependence(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/somewhere/nonexistent")
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", "/somewhere/else")
        record = ct.classify(3, False, "low")
        assert record.tier == "small"

    def test_ctr2_cli_is_deterministic_across_two_real_processes(self):
        result_a = _run_cli("--files", "3", "--ambiguity", "low")
        result_b = _run_cli("--files", "3", "--ambiguity", "low")
        assert result_a.returncode == result_b.returncode == 0
        assert result_a.stdout == result_b.stdout


# ---------------------------------------------------------------------------
# AC-CTR-3 — the security trigger forces at least `standard`, names the match in the rendered
# line, and marks the security question REQUIRED when and only when the trigger fired.
# ---------------------------------------------------------------------------


class TestCTR3SecurityTrigger:
    def test_ctr3_matched_scope_path_forces_at_least_standard(self):
        record = ct.classify(1, False, "none", scope_paths=("hooks/pre-commit.sh",))
        assert record.tier == "standard"
        assert record.triggers["security_fired"] is True
        assert "hooks/pre-commit.sh" in record.line

    def test_ctr3_matched_keyword_forces_at_least_standard(self):
        record = ct.classify(1, False, "none", spec_text="rotate the signing key")
        assert record.tier == "standard"
        assert record.triggers["security_fired"] is True
        assert "signing" in record.line

    def test_ctr3_trigger_never_lowers_an_already_higher_tier(self):
        record = ct.classify(20, False, "high", spec_text="uses a credential")
        assert record.tier == "large"
        assert record.triggers["security_fired"] is True

    def test_ctr3_security_question_required_exactly_when_trigger_fires(self):
        fired = ct.classify(1, False, "none", spec_text="a shared secret")
        not_fired = ct.classify(1, False, "none", spec_text="nothing sensitive here")
        assert fired.mask["security_question"] is True
        assert not_fired.mask["security_question"] is False

    def test_ctr3_no_trigger_leaves_tier_and_line_unaffected(self):
        record = ct.classify(1, False, "none", spec_text="just formatting")
        assert record.tier == "trivial"
        assert record.mask["security_question"] is False
        assert "security override" not in record.line

    def test_ctr3_whole_word_match_not_substring(self):
        # "authored" contains "auth" as a substring but is not the whole word "auth".
        record = ct.classify(1, False, "none", spec_text="the author authored this doc")
        assert record.triggers["security_fired"] is False
        assert record.tier == "trivial"


# ---------------------------------------------------------------------------
# AC-CTR-4 — one source of truth per security input: the path pattern equals (as a string) the
# shipped `security-path` CI gate's literal, and the keyword set equals the closed ten-member set.
# ---------------------------------------------------------------------------


def _extract_security_path_job_block(workflow_text):
    match = re.search(r"\n  security-path-base:\n(.*)", workflow_text, re.DOTALL)
    assert match, "security-path-base: job not found in btb-gates-base.yml"
    return match.group(1)


def _extract_ci_gate_pattern(workflow_text):
    block = _extract_security_path_job_block(workflow_text)
    candidates = re.findall(r"grep -E '([^']*)'", block)
    assert len(candidates) == 1, (
        f"expected exactly one grep -E '...' literal in the security-path job block, "
        f"found {len(candidates)}"
    )
    return candidates[0]


# Transcribed literal copy of the spec's normative closed blast-radius keyword set
# (feat-foundry-ceremony-tiering.md, AC-CTR-3). Adding/removing a member is a spec
# re-authorization, not a code-only edit — this test is what makes that binding.
_SPEC_BLAST_RADIUS_KEYWORDS = {
    "auth",
    "authentication",
    "authorization",
    "secret",
    "credential",
    "token",
    "signing",
    "supply-chain",
    "privilege",
    "permission",
}


class TestCTR4OneSourceOfTruth:
    def test_ctr4_path_pattern_equals_the_shipped_ci_gate_literal(self):
        with open(BTB_GATES, encoding="utf-8") as handle:
            workflow_text = handle.read()
        ci_pattern = _extract_ci_gate_pattern(workflow_text)
        assert ct.SECURITY_PATH_PATTERN == ci_pattern

    def test_ctr4_keyword_set_equals_the_closed_spec_set(self):
        assert set(ct.BLAST_RADIUS_KEYWORDS) == _SPEC_BLAST_RADIUS_KEYWORDS
        assert len(ct.BLAST_RADIUS_KEYWORDS) == 10


# ---------------------------------------------------------------------------
# AC-CTR-5 — tier selects the verb mask (the mask table).
# ---------------------------------------------------------------------------


class TestCTR5MaskSelection:
    def test_ctr5_trivial_mask_shape(self):
        mask = ct.mask_for("trivial", security_fired=False)
        assert mask["spec_review"] == "skipped-pending-operator-token"
        assert mask["phase1_questions"] == ()

    def test_ctr5_small_mask_is_steel_man_adversarial_only(self):
        mask = ct.mask_for("small", security_fired=False)
        assert mask["spec_review"] == "required"
        assert mask["phase1_questions"] == ("steel_man_adversarial",)

    def test_ctr5_standard_mask_is_all_three_questions(self):
        mask = ct.mask_for("standard", security_fired=False)
        assert mask["phase1_questions"] == ("prior_art", "steel_man_adversarial", "per_ac_rubric")
        assert mask["decomposition_check"] is False

    def test_ctr5_large_mask_is_all_three_plus_decomposition(self):
        mask = ct.mask_for("large", security_fired=False)
        assert mask["phase1_questions"] == ("prior_art", "steel_man_adversarial", "per_ac_rubric")
        assert mask["decomposition_check"] is True

    def test_ctr5_unknown_tier_rejected(self):
        with pytest.raises(ValueError):
            ct.mask_for("gigantic", security_fired=False)


# ---------------------------------------------------------------------------
# AC-CTR-6 — the floor is required at every tier, no exceptions.
# ---------------------------------------------------------------------------


class TestCTR6FloorAtEveryTier:
    @pytest.mark.parametrize("tier", ct.TIERS)
    @pytest.mark.parametrize("security_fired", (False, True))
    def test_ctr6_floor_fields_always_required(self, tier, security_fired):
        mask = ct.mask_for(tier, security_fired=security_fired)
        assert mask["phase0_lints"] is True
        assert mask["front_authorization"] is True
        assert mask["merge_floor"] is True
        assert mask["security_review_routing"] is True


# ---------------------------------------------------------------------------
# AC-CTR-7 — `trivial` is operator-gated, never silent.
# ---------------------------------------------------------------------------


class TestCTR7TrivialIsOperatorGated:
    def test_ctr7_trivial_carries_the_verbatim_skip_token(self):
        record = ct.classify(1, False, "none")
        assert record.tier == "trivial"
        assert record.mask["spec_review"] == "skipped-pending-operator-token"
        assert record.mask["skip_token"] == "skip review; reason: <one-line>"

    def test_ctr7_non_trivial_tiers_carry_no_skip_token(self):
        for files, ambiguity in ((3, "low"), (10, "medium"), (20, "high")):
            record = ct.classify(files, False, ambiguity)
            assert record.mask["skip_token"] is None


# ---------------------------------------------------------------------------
# AC-CTR-8 — one-line classification at intake: exactly one stdout line, exit 0.
# ---------------------------------------------------------------------------


class TestCTR8OneLineAtIntake:
    def test_ctr8_line_matches_the_anchored_form(self):
        record = ct.classify(3, False, "low")
        assert record.line == "tier: small — 3 files, no new contract, low ambiguity"

    def test_ctr8_line_names_new_contract_surface(self):
        record = ct.classify(1, True, "none")
        assert record.line == "tier: standard — 1 files, new contract surface, none ambiguity"

    def test_ctr8_security_override_suffix_present_when_triggered(self):
        record = ct.classify(1, False, "none", spec_text="holds a secret")
        assert record.line.endswith("(security override: secret)")

    def test_ctr8_cli_prints_exactly_one_line_and_exits_zero(self):
        result = _run_cli("--files", "3", "--ambiguity", "low", "--spec", FIXTURE)
        assert result.returncode == 0
        lines = result.stdout.splitlines()
        assert len(lines) == 1
        assert re.match(r"^tier: small — 3 files, no new contract, low ambiguity$", lines[0])

    def test_ctr8_cli_new_contract_surface_flag(self):
        result = _run_cli("--files", "1", "--ambiguity", "none", "--new-contract-surface")
        assert result.returncode == 0
        assert result.stdout.strip() == "tier: standard — 1 files, new contract surface, none ambiguity"


# ---------------------------------------------------------------------------
# AC-CTR-9 — operator override, either direction.
# ---------------------------------------------------------------------------


class TestCTR9OperatorOverride:
    def test_ctr9_override_up_is_adopted(self):
        record = ct.classify(1, False, "none", override="large", override_reason="raise it")
        assert record.tier == "large"
        assert record.classified_tier == "trivial"
        assert record.mask["decomposition_check"] is True

    def test_ctr9_override_down_is_adopted(self):
        record = ct.classify(20, False, "high", override="small", override_reason="mechanical")
        assert record.tier == "small"
        assert record.classified_tier == "large"
        assert record.mask["phase1_questions"] == ("steel_man_adversarial",)

    def test_ctr9_line_names_classified_adopted_and_reason(self):
        record = ct.classify(1, False, "none", override="standard", override_reason="be safe")
        assert "trivial" in record.line
        assert "standard" in record.line
        assert "be safe" in record.line

    def test_ctr9_override_mask_is_the_adopted_tiers_mask(self):
        record = ct.classify(1, False, "none", override="small", override_reason="ok")
        assert record.mask == ct.mask_for("small", security_fired=False)


# ---------------------------------------------------------------------------
# AC-CTR-10 — the override is recorded (skill-side; see the file-level checkpoint on
# skills/intake/SKILL.md in the acceptance contract). Here: the record fields classify() exposes
# are exactly what the template needs.
# ---------------------------------------------------------------------------


class TestCTR10OverrideRecordFields:
    def test_ctr10_override_dict_carries_classified_adopted_reason(self):
        record = ct.classify(1, False, "none", override="small", override_reason="trivial rename")
        assert record.override == {
            "classified": "trivial",
            "adopted": "small",
            "reason": "trivial rename",
        }

    def test_ctr10_no_override_leaves_override_field_none(self):
        record = ct.classify(1, False, "none")
        assert record.override is None


# ---------------------------------------------------------------------------
# AC-CTR-11 — an inadmissible override is refused.
# ---------------------------------------------------------------------------


class TestCTR11InadmissibleOverrideRefused:
    def test_ctr11_override_below_standard_refused_when_security_fired(self):
        with pytest.raises(ct.OverrideRefused) as excinfo:
            ct.classify(
                2, False, "low", scope_paths=("hooks/foo.sh",), override="small",
                override_reason="downplay it",
            )
        assert excinfo.value.classified_tier == "standard"
        assert excinfo.value.refused_tier == "small"

    def test_ctr11_empty_reason_refused(self):
        with pytest.raises(ct.OverrideRefused):
            ct.classify(1, False, "none", override="large", override_reason="")

    def test_ctr11_whitespace_only_reason_refused(self):
        with pytest.raises(ct.OverrideRefused):
            ct.classify(1, False, "none", override="large", override_reason="   \t  ")

    def test_ctr11_refused_override_retains_classified_tier(self):
        # The classify() call itself raises rather than silently returning a lowered tier —
        # the caller must catch OverrideRefused and fall back to classified_tier.
        try:
            ct.classify(
                2, False, "low", scope_paths=("hooks/foo.sh",), override="trivial",
                override_reason="skip it",
            )
            pytest.fail("expected OverrideRefused")
        except ct.OverrideRefused as exc:
            assert exc.classified_tier == "standard"

    def test_ctr11_scenario_two_file_secrets_path_override_refused(self):
        # Spec scenario: 2 files, no new contract, ambiguity low, a scope path matching the
        # security-surface pattern; operator attempts an override to `small`.
        with pytest.raises(ct.OverrideRefused) as excinfo:
            ct.classify(
                2, False, "low", scope_paths=("hooks/secret-rotate.sh",), override="small",
                override_reason="just docs",
            )
        assert excinfo.value.classified_tier == "standard"

    def test_ctr11_cli_exits_nonzero_and_names_ground_on_stderr(self):
        result = _run_cli(
            "--files", "1", "--ambiguity", "none", "--override", "trivial",
            "--override-reason", "",
        )
        assert result.returncode != 0
        assert "refused" in result.stderr


# ---------------------------------------------------------------------------
# AC-CTR-12 — `large` demands a decomposition check.
# ---------------------------------------------------------------------------


class TestCTR12LargeDemandsDecomposition:
    def test_ctr12_large_mask_requires_decomposition_check(self):
        record = ct.classify(30, False, "high")
        assert record.tier == "large"
        assert record.mask["decomposition_check"] is True

    def test_ctr12_non_large_tiers_do_not_require_decomposition_check(self):
        for files, ambiguity in ((1, "none"), (3, "low"), (10, "medium")):
            record = ct.classify(files, False, ambiguity)
            assert record.mask["decomposition_check"] is False


# ---------------------------------------------------------------------------
# AC-CTR-14 — declared-scope tripwire (advisory stderr notice only).
# ---------------------------------------------------------------------------


class TestCTR14ScopeTripwire:
    def _write_contract(self, tmp_path, allowed_paths):
        contract_path = tmp_path / "acceptance-contract.yaml"
        contract_path.write_text(
            yaml.safe_dump({"scope": {"allowed_paths": list(allowed_paths)}}),
            encoding="utf-8",
        )
        return str(contract_path)

    def test_ctr14_mismatch_emits_stderr_notice(self, tmp_path):
        contract_path = self._write_contract(tmp_path, ["a", "b", "c", "d", "e"])
        notice = ct.scope_tripwire(1, contract_path)
        assert notice is not None
        assert notice.startswith("scope-signal mismatch:")
        assert "1" in notice
        assert "5" in notice

    def test_ctr14_within_tolerance_is_silent(self, tmp_path):
        contract_path = self._write_contract(tmp_path, ["a", "b", "c"])
        assert ct.scope_tripwire(2, contract_path) is None
        assert ct.scope_tripwire(3, contract_path) is None

    def test_ctr14_absent_contract_is_silent(self):
        assert ct.scope_tripwire(1, None) is None
        assert ct.scope_tripwire(1, "/nonexistent/contract.yaml") is None

    def test_ctr14_cli_notice_goes_to_stderr_not_stdout(self, tmp_path):
        contract_path = self._write_contract(tmp_path, ["a", "b", "c", "d", "e"])
        result = _run_cli("--files", "1", "--ambiguity", "none", "--contract", contract_path)
        assert result.returncode == 0
        assert "scope-signal mismatch:" in result.stderr
        assert "scope-signal mismatch:" not in result.stdout
        # stdout still carries exactly the classification line, unchanged.
        assert result.stdout.strip() == "tier: trivial — 1 files, no new contract, none ambiguity"
