"""tests/test_contract_review_outcome.py — `mandatory_review[].outcome`.

Drives `schema/acceptance-contract.schema.json` through `scripts/foundry_contract.py`'s
`validate_contract_bytes` over the new OPTIONAL `mandatory_review[].outcome` block: schema
accept/refuse both arms, the closed `status` vocabulary, back-compat for a contract that omits it,
and hash coverage (the block is in the contract-proper region, so writing an outcome re-hashes
`contract_sha256` and routes through /foundry:authorize via the amend verb's BOUNDARY_FIELDS).

WHY THE FIELD EXISTS, since a test is the wrong place to discover it: `review`/`required`/`rationale`
can state only that a review is REQUIRED. Nothing could state that one was PERFORMED, by whom, when,
or with what conclusion -- so an adopter governed by ISO/IEC 27001:2022 A.8.26 or SOC 2 CC8.1
("identified, specified AND APPROVED") could evidence the first two and structurally could not
evidence the third. The third `status` member, `performed-blocked`, is the one that previously had
nowhere to live: a review that HAPPENED and WITHHELD approval.

Follows `tests/test_contract_done_when.py`'s idiom (`_golden()` dict + `yaml.safe_dump` +
`contract.validate_contract_bytes`) rather than inventing a second one.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

from conftest import load_module

contract = load_module("scripts/foundry_contract.py", "foundry_contract")
amend = load_module("scripts/foundry-amend.py", "foundry_amend")


def _golden():
    return {
        "spec_ref": "foundry/test/fixtures/golden-spec.md",
        "spec_sha256": "deadbeef" * 8,
        "scope": {"allowed_paths": ["apps/api/src/routes/search/**"]},
        "checkpoints": [
            {"ac_id": "AC-API-1", "surface": "api:/v1/search", "locator": "POST /v1/search",
             "expect": {"op": "count_gte", "value": 1, "baseline": "pre-change"}},
        ],
    }


def _with_outcome(**kw):
    outcome = {"status": "performed-approved", "reviewed_at": "2026-10-08",
               "reviewed_by": "op_test"}
    outcome.update(kw)
    doc = _golden()
    doc["mandatory_review"] = [
        {"review": "security", "required": True, "rationale": "auth surface", "outcome": outcome}]
    return doc


def _bytes(doc):
    return yaml.safe_dump(doc).encode("utf-8")


def _validate(doc):
    return contract.validate_contract_bytes(_bytes(doc))


# ================================================================================= accepted ===== #

class TestOutcomeAccepted:
    def test_minimal_outcome_accepted(self):
        ok, errors, _ = _validate(_with_outcome())
        assert ok is True, errors

    @pytest.mark.parametrize("status", ["performed-approved", "performed-blocked",
                                        "not-performed", "waived"])
    def test_every_closed_status_member_accepted(self, status):
        ok, errors, _ = _validate(_with_outcome(status=status))
        assert ok is True, (status, errors)

    def test_optional_evidence_and_blocks_accepted(self):
        ok, errors, _ = _validate(_with_outcome(
            status="performed-blocked",
            review_evidence="docs/reviews/2026-10-07-credential-plane.md",
            blocks=["DP-F20"]))
        assert ok is True, errors

    def test_contract_WITHOUT_outcome_still_validates(self):
        """Back-compat: the field is optional and every pre-existing contract must be unaffected."""
        doc = _golden()
        doc["mandatory_review"] = [{"review": "security", "required": True,
                                    "rationale": "STATUS: PERFORMED. the prose convention"}]
        ok, errors, _ = _validate(doc)
        assert ok is True, errors

    def test_contract_with_NO_mandatory_review_still_validates(self):
        ok, errors, _ = _validate(_golden())
        assert ok is True, errors


# ================================================================================== refused ===== #

class TestOutcomeRefused:
    @pytest.mark.parametrize("missing", ["status", "reviewed_at", "reviewed_by"])
    def test_each_required_subfield_is_required(self, missing):
        doc = _with_outcome()
        del doc["mandatory_review"][0]["outcome"][missing]
        ok, errors, _ = _validate(doc)
        assert ok is False, (missing, errors)

    @pytest.mark.parametrize("bogus", ["approved", "PERFORMED", "performed_approved",
                                       "blocked", "done", ""])
    def test_status_outside_the_closed_enum_is_refused(self, bogus):
        """A typo must NOT read as approval -- that is the whole reason this is an enum."""
        ok, errors, _ = _validate(_with_outcome(status=bogus))
        assert ok is False, (bogus, errors)

    def test_unknown_subfield_is_refused(self):
        ok, errors, _ = _validate(_with_outcome(reviewer_mood="confident"))
        assert ok is False, errors

    @pytest.mark.parametrize("bad_date", ["2026-13-99x", "08/10/2026", "2026-10", "yesterday"])
    def test_reviewed_at_must_be_an_iso_date(self, bad_date):
        ok, errors, _ = _validate(_with_outcome(reviewed_at=bad_date))
        assert ok is False, (bad_date, errors)

    def test_empty_reviewed_by_is_refused(self):
        ok, errors, _ = _validate(_with_outcome(reviewed_by=""))
        assert ok is False

    def test_outcome_must_be_an_object_not_a_string(self):
        doc = _golden()
        doc["mandatory_review"] = [{"review": "security", "outcome": "performed-approved"}]
        ok, errors, _ = _validate(doc)
        assert ok is False, errors


# ============================================================ attestation / hash coverage ======= #

class TestOutcomeIsAttested:
    def test_outcome_is_hash_covered_so_writing_one_re_authorizes(self):
        """`mandatory_review` is in the contract-proper region: an outcome changes contract_sha256.

        This is the property that makes the record an ATTESTATION rather than an editable note.
        """
        without = _golden()
        without["mandatory_review"] = [{"review": "security", "required": True}]
        h1 = contract.contract_sha256_bytes(_bytes(without))
        h2 = contract.contract_sha256_bytes(_bytes(_with_outcome()))
        assert h1 != h2, "writing an outcome MUST move contract_sha256, or it is not attested"
        # CONTROL: without this the inequality above could come from any instability in hashing.
        assert h1 == contract.contract_sha256_bytes(_bytes(without))

    def test_mandatory_review_is_already_a_boundary_field(self):
        """Assert the pre-existing route, do not re-implement it: a security review always
        routes to /foundry:authorize (AC-AMND-3), so an outcome is operator-attested."""
        assert "mandatory_review" in amend.BOUNDARY_FIELDS
        assert amend.mandatory_review_names_security(_with_outcome()) is True


# ================================================ the adopter-detector contract this unblocks ==== #

class TestSatisfiesOutcomeFieldDetection:
    def test_field_names_are_discoverable_as_review_outcome_fields(self):
        """An adopter's A.8.26 detector looks for a review-OUTCOME field by NAME, structurally.

        `status` alone would not be discoverable -- `reviewed_at` / `reviewed_by` /
        `review_evidence` are. Pinned here so a later rename does not silently re-open the limb
        this field exists to close.
        """
        import re
        outcome_key = re.compile(
            r"review(?:ed)?_(?:outcome|by|at|evidence|result|status)"
            r"|security_review_(?:performed|outcome|evidence)|reviewer", re.I)
        import json
        from conftest import REPO_ROOT
        schema = json.loads((pathlib.Path(REPO_ROOT) / "schema"
                             / "acceptance-contract.schema.json").read_text(encoding="utf-8"))
        names = list(schema["properties"]["mandatory_review"]["items"]
                     ["properties"]["outcome"]["properties"])
        matched = [n for n in names if outcome_key.search(n)]
        assert matched, f"no field in {names} is discoverable as a review-outcome field"
        assert "reviewed_at" in matched and "reviewed_by" in matched
