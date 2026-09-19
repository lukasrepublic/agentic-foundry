"""tests/test_contract_done_when.py — feat-foundry-contract-done-when-escalate-when
(AC-DWE-1..5, ac-r1-stop-stopping charter contract-done-when-escalate-when).

Drives `schema/acceptance-contract.schema.json` + `scripts/foundry_contract.py`'s
`validate_contract_bytes` over the three new OPTIONAL top-level fields (`done_when`,
`escalate_when`, `requires_capabilities`): schema accept/refuse (AC-DWE-1), contract_sha256 hash
coverage (AC-DWE-5), and the amend-verb's pre-existing BOUNDARY_FIELDS membership (AC-DWE-5,
"already in BOUNDARY_FIELDS — assert, do not re-implement").

Follows `tests/test_contract_authz.py`'s own idiom (`_golden()` dict + `yaml.safe_dump` +
`contract.validate_contract_bytes`) rather than inventing a second one.
"""
from __future__ import annotations

import os

import pytest
import yaml

from conftest import REPO_ROOT, load_module

contract = load_module("scripts/foundry_contract.py", "foundry_contract")
amend = load_module("scripts/foundry-amend.py", "foundry_amend")

FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures", "done-when")


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


def _bytes(doc):
    return yaml.safe_dump(doc).encode("utf-8")


# =================================================================== AC-DWE-1: schema accept ==== #

class TestDoneWhenEscalateWhenAccepted:
    def test_done_when_each_valid_prefix_accepted(self):
        for prefix in ("test:tests/x.py::y", "cli:python3 foo.py", "file:some/path.txt",
                       "checkpoint:AC-API-1-1"):
            doc = _golden()
            doc["done_when"] = [prefix]
            ok, errors, _ = contract.validate_contract_bytes(_bytes(doc))
            assert ok is True, (prefix, errors)

    def test_escalate_when_each_closed_member_accepted(self):
        for member in ("external-provisioning", "credential-step", "no-consensus-after-research",
                       "security-widening", "irreversible-action", "operator-approval"):
            doc = _golden()
            doc["escalate_when"] = [member]
            ok, errors, _ = contract.validate_contract_bytes(_bytes(doc))
            assert ok is True, (member, errors)

    def test_all_three_fields_together_accepted(self):
        doc = _golden()
        doc["done_when"] = ["test:tests/x.py::y"]
        doc["escalate_when"] = ["external-provisioning", "credential-step"]
        doc["requires_capabilities"] = ["network-egress", "aws-credential"]
        ok, errors, _ = contract.validate_contract_bytes(_bytes(doc))
        assert ok is True, errors

    def test_absent_fields_still_validate_backcompat(self):
        ok, errors, _ = contract.validate_contract_bytes(_bytes(_golden()))
        assert ok is True, errors

    def test_fixture_contract_file_validates(self):
        with open(os.path.join(FIXTURES, "sample-contract.yaml"), "rb") as fh:
            ok, errors, _ = contract.validate_contract_bytes(fh.read())
        assert ok is True, errors


# =================================================================== AC-DWE-1: schema refuse ==== #

class TestDoneWhenEscalateWhenRefused:
    def test_empty_done_when_refused(self):
        doc = _golden()
        doc["done_when"] = []
        ok, errors, _ = contract.validate_contract_bytes(_bytes(doc))
        assert ok is False
        assert any("done_when" in e for e in errors), errors

    def test_done_when_bad_prefix_refused(self):
        doc = _golden()
        doc["done_when"] = ["review:someone looked at it"]
        ok, errors, _ = contract.validate_contract_bytes(_bytes(doc))
        assert ok is False
        assert any("done_when" in e for e in errors), errors

    def test_escalate_when_unknown_member_refused(self):
        doc = _golden()
        doc["escalate_when"] = ["scope-creep"]
        ok, errors, _ = contract.validate_contract_bytes(_bytes(doc))
        assert ok is False
        assert any("escalate_when" in e for e in errors), errors

    def test_escalate_when_duplicate_refused(self):
        doc = _golden()
        doc["escalate_when"] = ["credential-step", "credential-step"]
        ok, errors, _ = contract.validate_contract_bytes(_bytes(doc))
        assert ok is False
        assert any("escalate_when" in e or "uniqueItems" in e or "duplicate" in e
                   for e in errors), errors

    def test_requires_capabilities_empty_string_refused(self):
        doc = _golden()
        doc["requires_capabilities"] = [""]
        ok, errors, _ = contract.validate_contract_bytes(_bytes(doc))
        assert ok is False
        assert any("requires_capabilities" in e for e in errors), errors

    @pytest.mark.parametrize("field,bad_value", [
        ("done_when", []),
        ("escalate_when", ["not-a-real-member"]),
        ("requires_capabilities", [""]),
    ])
    def test_refused_even_without_jsonschema_UL0011(self, monkeypatch, field, bad_value):
        """The jsonschema floor is OPPORTUNISTIC (skipped when the library is absent, UL-0011).
        `_done_when_escalate_when_structural_errors` is the hand-rolled floor that must refuse
        the SAME shapes regardless — assert it does, with jsonschema forced unavailable."""
        monkeypatch.setattr(contract, "_jsonschema_available", lambda: False)
        doc = _golden()
        doc[field] = bad_value
        ok, errors, _ = contract.validate_contract_bytes(_bytes(doc))
        assert ok is False, (field, bad_value, errors)


# =================================================== AC-DWE-3/-4 fixture: markdown charter list ==== #

def test_fixture_charter_exists_and_carries_both_sections():
    """Sanity check on the fixture the command-deck-watch test suite parses — kept here so a
    fixture edit that breaks the shape fails loudly beside the contract behavior it accompanies."""
    with open(os.path.join(FIXTURES, "sample-charter.md"), encoding="utf-8") as fh:
        text = fh.read()
    assert "## Done when" in text
    assert "## Escalate when" in text


# ========================================================================== AC-DWE-5: hash ==== #

class TestContractShaCoversNewFields:
    def test_done_when_edit_changes_contract_sha256(self):
        base = _bytes(_golden())
        with_dw = _bytes({**_golden(), "done_when": ["test:tests/x.py::y"]})
        assert contract.contract_sha256_bytes(base) != contract.contract_sha256_bytes(with_dw)

    def test_escalate_when_edit_changes_contract_sha256(self):
        base = _bytes({**_golden(), "escalate_when": ["credential-step"]})
        changed = _bytes({**_golden(), "escalate_when": ["external-provisioning"]})
        assert contract.contract_sha256_bytes(base) != contract.contract_sha256_bytes(changed)

    def test_requires_capabilities_edit_changes_contract_sha256(self):
        base = _bytes({**_golden(), "requires_capabilities": ["network-egress"]})
        changed = _bytes({**_golden(), "requires_capabilities": ["network-egress", "gpu"]})
        assert contract.contract_sha256_bytes(base) != contract.contract_sha256_bytes(changed)

    def test_new_fields_are_contract_proper_not_only_trailer(self):
        """The integrity (sentinel-injection) guard names every normative field it protects
        (`scripts/foundry_contract.py`'s `validate_contract_bytes`); done_when/escalate_when/
        requires_capabilities must be in that list, or a sentinel injected right after them would
        silently exclude them from contract_sha256 while they still parse and display. Drive this
        through the PUBLIC behavior (an injected sentinel is refused), not by reading the source
        list — that would only prove the list mentions the field, not that the guard enforces it."""
        doc = _golden()
        doc["done_when"] = ["test:tests/x.py::y"]
        raw = _bytes(doc)
        # Inject the sentinel INSIDE the mapping, ahead of done_when serialized after it in the
        # dict — yaml.safe_dump is insertion-ordered for a plain dict on py3.7+, and `done_when`
        # was inserted last, so appending the sentinel to the byte stream and reparsing the
        # proper-only prefix (which STOPS at the sentinel) drops done_when from the proper view
        # while the full-doc view still sees it below the trailer, exactly the attack the guard
        # exists to catch.
        injected = raw.rstrip(b"\n") + b"\n" + contract._SENTINEL_B + b"\ndone_when:\n  - other\n"
        ok, errors, _ = contract.validate_contract_bytes(injected)
        assert ok is False
        assert any("done_when" in e and "integrity" in e for e in errors), errors


# ============================================== AC-DWE-5: amend BOUNDARY_FIELDS (assert only) ==== #

def test_requires_capabilities_already_in_amend_boundary_fields():
    """AC-DWE-5: `requires_capabilities` is ALREADY in `scripts/foundry-amend.py`'s
    BOUNDARY_FIELDS (a change to it already classifies AC-AMND-1(b)) — this atom's write boundary
    denies scripts/foundry-amend.py outright, so this test only ASSERTS the pre-existing fact,
    never re-implements or edits the classifier."""
    assert "requires_capabilities" in amend.BOUNDARY_FIELDS
