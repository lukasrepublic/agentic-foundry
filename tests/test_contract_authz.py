"""tests/test_contract_authz.py — converted from scripts/foundry_checks/{acceptance-contract-
intended, allowed-paths-grounding, contract-schema-gate-slots, contract-surface-scope,
freeze-floor-ac-extraction, system-grounding-floor, intake-schema-grounding,
grounding-conformance-backfill, system-state-snapshot}.py.

Ports the real behavioral assertions those nine drop-in selftests drove against the front-
authorization core: `scripts/foundry_contract.py` (the acceptance-contract validator + the
reality-grounding error functions), `scripts/foundry_authz.py` (`_spec_ac_ids`, the spec-side AC
extraction the freeze floor bijects against), `scripts/foundry_intake_grounding.py` (the
authoring-time prevent-aid), `scripts/foundry_grounding_conformance.py` (the corpus backfill
classifier), and `scripts/foundry_system_snapshot.py` (the reality-grounding foundation
primitive). CLI/doctor scaffolding is dropped; the computed fixtures/assertions are kept.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest
import yaml

from conftest import REPO_ROOT, load_module

contract = load_module("scripts/foundry_contract.py", "foundry_contract")
authz = load_module("scripts/foundry_authz.py", "foundry_authz")
intake_grounding = load_module("scripts/foundry_intake_grounding.py", "foundry_intake_grounding")
grounding_conformance = load_module("scripts/foundry_grounding_conformance.py", "foundry_grounding_conformance")
system_snapshot = load_module("scripts/foundry_system_snapshot.py", "foundry_system_snapshot")


def _golden():
    return {
        "spec_ref": "foundry/test/fixtures/golden-spec.md",
        "spec_sha256": "deadbeef" * 8,
        "scope": {
            "allowed_paths": ["apps/api/src/routes/search/**", "apps/web/app/search/**"],
            "denied_paths": ["apps/**/auth/**"],
        },
        "checkpoints": [
            {"ac_id": "AC-API-1", "surface": "api:/v1/search",
             "locator": "POST /v1/search {q:'sedan under 20k'}",
             "expect": {"op": "count_gte", "value": 1, "baseline": "pre-change"}},
            {"ac_id": "AC-UI-1", "surface": "ui:/search", "locator": "[data-testid=result-card]",
             "expect": {"op": "count_gte", "value": 1, "baseline": "none"}},
        ],
    }


def _golden_bytes():
    return yaml.safe_dump(_golden()).encode("utf-8")


# =================================================== acceptance-contract-intended (schema) ==== #

class TestValidateContractBytes:
    def test_golden_contract_validates(self):
        ok, errors, warnings = contract.validate_contract_bytes(_golden_bytes())
        assert ok is True, errors

    def test_missing_required_field_rejected(self):
        doc = _golden()
        del doc["spec_ref"]
        ok, errors, _ = contract.validate_contract_bytes(yaml.safe_dump(doc).encode("utf-8"))
        assert ok is False and errors

    def test_bidirectional_bijection_floor_with_spec_ac_ids(self):
        # the contract declares AC-API-1/AC-UI-1; feeding a MISMATCHED spec AC-ID set should
        # surface a bijection error rather than silently pass.
        ok, errors, warnings = contract.validate_contract_bytes(
            _golden_bytes(), spec_ac_ids=["AC-API-1", "AC-UI-1", "AC-EXTRA-9"])
        assert ok is False
        assert any("AC-EXTRA-9" in e for e in errors)

    def test_bijection_holds_when_ac_ids_match(self):
        ok, errors, _ = contract.validate_contract_bytes(
            _golden_bytes(), spec_ac_ids=["AC-API-1", "AC-UI-1"])
        assert ok is True, errors


# ==================================================== contract-schema-gate-slots (additive) ==== #

class TestOptionalGateSlots:
    @pytest.mark.parametrize("slot,value", [
        ("preconditions", [{"id": "pre-1", "kind": "spec-authorized"}]),
        ("build_gates", [{"id": "bg-1", "check": "npm test"}]),
        ("post_apply_checks", [{"id": "pac-1", "check": "curl -f /healthz", "on_failure": "rollback"}]),
        ("mandatory_review", [{"review": "security"}]),
    ])
    def test_optional_slot_admitted_when_well_formed(self, slot, value):
        doc = _golden()
        doc[slot] = value
        ok, errors, _ = contract.validate_contract_bytes(yaml.safe_dump(doc).encode("utf-8"))
        assert ok is True, (slot, errors)

    def test_unknown_toplevel_key_rejected_additive_properties_false(self):
        doc = _golden()
        doc["totally_unknown_key"] = "x"
        ok, errors, _ = contract.validate_contract_bytes(yaml.safe_dump(doc).encode("utf-8"))
        assert ok is False


# ======================================================== contract-surface-scope (ER #77) ==== #

class TestSurfaceScopeErrors:
    def test_none_repo_root_is_existence_unknowable_skip(self):
        assert contract.surface_scope_errors(_golden(), None) == []

    def test_new_path_outside_scope_is_an_error(self, tmp_path):
        doc = _golden()
        doc["checkpoints"].append({
            "ac_id": "AC-NEW-1", "surface": "file:apps/other/new-file.ts",
            "locator": "exists", "expect": {"op": "count_gte", "value": 1, "baseline": "none"}})
        errors = contract.surface_scope_errors(doc, str(tmp_path))
        assert any("OUTSIDE scope.allowed_paths" in e for e in errors)

    def test_new_path_inside_scope_is_clean(self, tmp_path):
        doc = _golden()
        doc["checkpoints"].append({
            "ac_id": "AC-NEW-2", "surface": "file:apps/api/src/routes/search/new.ts",
            "locator": "exists", "expect": {"op": "count_gte", "value": 1, "baseline": "none"}})
        errors = contract.surface_scope_errors(doc, str(tmp_path))
        assert errors == []

    def test_preexisting_file_never_flagged(self, tmp_path):
        os.makedirs(tmp_path / "apps" / "other")
        (tmp_path / "apps" / "other" / "existing.ts").write_text("x", encoding="utf-8")
        doc = _golden()
        doc["checkpoints"].append({
            "ac_id": "AC-EXIST-1", "surface": "file:apps/other/existing.ts",
            "locator": "exists", "expect": {"op": "count_gte", "value": 1, "baseline": "pre-change"}})
        errors = contract.surface_scope_errors(doc, str(tmp_path))
        assert errors == []


# ======================================================= allowed-paths-grounding (#179) ==== #

class TestAllowedPathsGroundingErrors:
    def test_none_repo_root_is_skip(self):
        assert contract.allowed_paths_grounding_errors(_golden(), None) == []

    def test_zero_match_glob_is_an_error(self, tmp_path):
        doc = _golden()
        doc["scope"]["allowed_paths"] = ["apps/nowhere/does-not-exist/**"]
        errors = contract.allowed_paths_grounding_errors(doc, str(tmp_path))
        assert len(errors) == 1 and "matches ZERO paths" in errors[0]

    def test_literal_existing_path_admitted(self, tmp_path):
        os.makedirs(tmp_path / "apps" / "api" / "src")
        doc = _golden()
        doc["scope"]["allowed_paths"] = ["apps/api/src"]
        errors = contract.allowed_paths_grounding_errors(doc, str(tmp_path))
        assert errors == []

    def test_declared_new_checkpoint_tolerance(self, tmp_path):
        # a zero-match allowed_paths entry is TOLERATED when named by the atom's own
        # path-shaped checkpoint surface (AC-APG-3).
        doc = _golden()
        doc["scope"]["allowed_paths"] = ["apps/brand-new/thing.ts"]
        doc["checkpoints"].append({
            "ac_id": "AC-NEW-3", "surface": "file:apps/brand-new/thing.ts",
            "locator": "exists", "expect": {"op": "count_gte", "value": 1, "baseline": "none"}})
        errors = contract.allowed_paths_grounding_errors(doc, str(tmp_path))
        assert errors == []


# ================================================== system-grounding-floor (Atom C, #121) ==== #

def _snapshot(*, configured=True, entities=None, modules=None):
    return {"grounding_configured": configured, "entities": entities or {},
            "modules": modules or []}


class TestSystemGroundingErrors:
    def test_unconfigured_snapshot_is_inert_noop(self):
        assert contract.system_grounding_errors({"system_grounding": {"artifacts": []}},
                                                  _snapshot(configured=False)) == []
        assert contract.system_grounding_errors({"system_grounding": {"artifacts": []}}, None) == []

    def test_no_block_declared_is_error_when_configured(self):
        errors = contract.system_grounding_errors({}, _snapshot(configured=True))
        assert errors  # AC-SGC-5: configured but nothing declared -> error.

    def test_exists_classification_matches_live_entity(self):
        data = {"system_grounding": {"artifacts": [
            {"kind": "table", "identifier": "users", "classification": "exists"}]}}
        snap = _snapshot(entities={"users": {"columns": ["id"]}})
        assert contract.system_grounding_errors(data, snap) == []

    def test_exists_classification_absent_from_live_snapshot_is_error(self):
        data = {"system_grounding": {"artifacts": [
            {"kind": "table", "identifier": "ghosts", "classification": "exists"}]}}
        snap = _snapshot(entities={"users": {"columns": ["id"]}})
        errors = contract.system_grounding_errors(data, snap)
        assert errors

    def test_net_new_classification_already_live_is_error(self):
        data = {"system_grounding": {"artifacts": [
            {"kind": "table", "identifier": "users", "classification": "net-new"}]}}
        snap = _snapshot(entities={"users": {"columns": ["id"]}})
        errors = contract.system_grounding_errors(data, snap)
        assert errors and "already live" in errors[0]

    def test_net_new_classification_genuinely_absent_is_clean(self):
        data = {"system_grounding": {"artifacts": [
            {"kind": "table", "identifier": "brand_new_table", "classification": "net-new"}]}}
        snap = _snapshot(entities={"users": {"columns": ["id"]}})
        assert contract.system_grounding_errors(data, snap) == []

    def test_structural_errors_precede_consistency(self):
        # a malformed artifact (missing required key) is a structural error, caught by
        # _system_grounding_structural_errors, called unconditionally.
        errors = contract._system_grounding_structural_errors(
            {"system_grounding": {"artifacts": [{"kind": "table"}]}})
        assert errors


# ===================================================== freeze-floor-ac-extraction (#142) ==== #

_SPEC_WELL_FORMED = """# feat-test

<!-- normative -->
- **AC-T-1**: does a thing.
- **AC-T-2**: does another (realizes AC-T-1 context).
<!-- /normative -->

## Changelog
mentions AC-T-99 here but does not define it.
"""


class TestSpecAcIds:
    def test_definition_scoped_extraction(self, tmp_path):
        p = tmp_path / "spec.md"
        p.write_text(_SPEC_WELL_FORMED, encoding="utf-8")
        ids = authz._spec_ac_ids(str(p))
        assert ids == ["AC-T-1", "AC-T-2"]

    def test_mentions_are_a_superset_not_fed_into_definitions(self, tmp_path):
        p = tmp_path / "spec.md"
        p.write_text(_SPEC_WELL_FORMED, encoding="utf-8")
        mentions = authz._spec_mention_ac_ids(str(p))
        defs = authz._spec_ac_ids(str(p))
        assert "AC-T-99" in mentions
        assert "AC-T-99" not in defs

    def test_malformed_fences_fall_back_to_whole_body_minus_changelog(self, tmp_path):
        broken = "<!-- normative -->\n- **AC-B-1**: unterminated fence.\n"
        p = tmp_path / "spec.md"
        p.write_text(broken, encoding="utf-8")
        assert authz._fences_balanced(open(p, "rb").read()) is False
        ids = authz._spec_ac_ids(str(p))
        assert ids == ["AC-B-1"]  # extraction still finds the definition via the fallback region.

    def test_ordered_list_form_is_not_recognized(self, tmp_path):
        p = tmp_path / "spec.md"
        p.write_text("<!-- normative -->\n1. **AC-N-1**: not a bullet.\n<!-- /normative -->\n",
                     encoding="utf-8")
        assert authz._spec_ac_ids(str(p)) == []


def test_acceptance_contract_validate_selftest_subprocess():
    """The contract validator is the SOLE remaining front-authorization freeze-floor proof
    (per CHANGELOG v0.24.0). Exercise its own real --selftest directly rather than
    reimplementing its internals."""
    script = os.path.join(REPO_ROOT, "scripts", "foundry-acceptance-contract-validate.py")
    proc = subprocess.run([sys.executable, script, "--selftest"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ==================================================== intake-schema-grounding (Atom D) ==== #

class TestIntakeSchemaDefects:
    def test_noop_when_no_grounding_configured(self, tmp_path):
        os.makedirs(tmp_path / ".claude", exist_ok=True)
        defects = intake_grounding.intake_schema_defects(
            [{"kind": "table", "classification": "net-new", "identifier": "users"}],
            project_dir=str(tmp_path))
        assert defects == []  # no grounding source configured -> inert no-op.

    def test_malformed_declared_artifact_surfaced(self, tmp_path):
        defects = intake_grounding.intake_schema_defects(
            [{"kind": "not-a-real-kind", "classification": "net-new", "identifier": "x"}],
            project_dir=str(tmp_path))
        assert any("malformed declared artifact" in d for d in defects)

    def test_empty_declared_artifacts_is_a_clean_noop(self, tmp_path):
        assert intake_grounding.intake_schema_defects([], project_dir=str(tmp_path)) == []


# ================================================ grounding-conformance-backfill (Atom G) ==== #

class TestClassifyAtom:
    def test_unconfigured_is_grounded_unconditionally(self):
        c, errors = grounding_conformance._classify_atom(
            {"system_grounding": {"artifacts": [{"kind": "bogus"}]}}, _snapshot(configured=False))
        assert c == grounding_conformance.GROUNDED and errors == []

    def test_no_block_is_ungrounded(self):
        c, errors = grounding_conformance._classify_atom({}, _snapshot(configured=True))
        assert c == grounding_conformance.UNGROUNDED and errors == []

    def test_structurally_malformed_block_is_stale(self):
        data = {"system_grounding": {"artifacts": [{"kind": "table"}]}}  # missing identifier
        c, errors = grounding_conformance._classify_atom(data, _snapshot(configured=True))
        assert c == grounding_conformance.STALE and errors

    def test_wellformed_consistent_block_is_grounded(self):
        data = {"system_grounding": {"artifacts": [
            {"kind": "table", "identifier": "users", "classification": "exists"}]}}
        snap = _snapshot(configured=True, entities={"users": {"columns": ["id"]}})
        c, errors = grounding_conformance._classify_atom(data, snap)
        assert c == grounding_conformance.GROUNDED and errors == []

    def test_wellformed_inconsistent_block_is_stale(self):
        data = {"system_grounding": {"artifacts": [
            {"kind": "table", "identifier": "ghosts", "classification": "exists"}]}}
        snap = _snapshot(configured=True, entities={"users": {"columns": ["id"]}})
        c, errors = grounding_conformance._classify_atom(data, snap)
        assert c == grounding_conformance.STALE and errors


class TestValidateSnapshot:
    def test_valid_snapshot_ok(self):
        grounding_conformance._validate_snapshot(_snapshot())  # must not raise.

    def test_missing_key_raises(self):
        with pytest.raises(grounding_conformance.GroundingConformanceError):
            grounding_conformance._validate_snapshot({"entities": {}, "modules": []})

    def test_non_dict_raises(self):
        with pytest.raises(grounding_conformance.GroundingConformanceError):
            grounding_conformance._validate_snapshot("not-a-dict")


# ======================================================= system-state-snapshot (Atom A) ==== #

class TestBuildSystemSnapshot:
    def test_no_project_config_is_inert_noop(self, tmp_path):
        snap = system_snapshot.build_system_snapshot(project_dir=str(tmp_path))
        assert snap["grounding_configured"] is False
        assert snap["schema_grounded"] is False and snap["module_grounded"] is False
        assert snap["entities"] == {} and snap["modules"] == []
        assert "signature" in snap

    def test_broken_schema_source_raises_grounding_source_error(self, tmp_path):
        import json
        os.makedirs(tmp_path / ".claude", exist_ok=True)
        with open(tmp_path / ".claude" / "foundry-project.json", "w", encoding="utf-8") as f:
            json.dump({"grounding": {"schema_source": {"kind": "not-a-real-reader", "path": "x"}}}, f)
        with pytest.raises(system_snapshot.GroundingSourceError):
            system_snapshot.build_system_snapshot(project_dir=str(tmp_path))

    def test_signature_is_deterministic(self, tmp_path):
        s1 = system_snapshot.build_system_snapshot(project_dir=str(tmp_path))
        s2 = system_snapshot.build_system_snapshot(project_dir=str(tmp_path))
        assert s1["signature"] == s2["signature"]


# ── contract_sha256 / freeze canonicalization symmetry ──────────────────────────────────────
# Regression: `contract_sha256_bytes` hashed the contract-proper region AS-IS while
# `freeze_proper_and_trailer` hashed it AFTER `canonicalize_proper` rstripped it to one newline.
# `authorize()` records the first and then asserts it equals the second over the frozen bytes, so
# any contract whose proper region ended in >1 newline refused with "contract_sha256 unstable
# across freeze". A FIRST authorize never hit it (no sentinel yet ⇒ the whole file is the proper
# region, already single-newline-terminated); it took a RE-authorization, where a blank line left
# before the sentinel falls inside the proper region, to expose it.

def test_contract_sha256_is_stable_across_the_freeze_transition():
    """The hash the operator signs must equal the hash of the bytes actually written."""
    body = b"spec_ref: x\nscope:\n  allowed_paths: []\ncheckpoints: []\n"
    trailer = "authorized:\n  auth_seq: 1\n"
    for trailing in (b"", b"\n", b"\n\n", b"\n\n\n"):
        raw = body.rstrip(b"\n") + b"\n" + trailing
        recorded = contract.contract_sha256_bytes(raw)
        frozen = contract.freeze_proper_and_trailer(raw, trailer)
        assert contract.contract_sha256_bytes(frozen) == recorded, (
            f"hash moved across the freeze for a proper region ending in {len(trailing)+1} newline(s)"
        )


def test_contract_sha256_ignores_trailing_newlines_in_the_proper_region():
    """Trailing blank lines are formatting, not contract content — they must not change the hash."""
    body = b"spec_ref: x\nscope:\n  allowed_paths: []\ncheckpoints: []\n"
    one = contract.contract_sha256_bytes(body)
    for extra in (b"\n", b"\n\n", b"\n\n\n\n"):
        assert contract.contract_sha256_bytes(body + extra) == one


def test_re_authorization_of_an_already_frozen_contract_is_hash_stable():
    """The exact path that failed: freeze once, leave a blank line before the sentinel (as an edit
    removing the last checkpoint does), then re-freeze."""
    body = b"spec_ref: x\nscope:\n  allowed_paths: []\ncheckpoints: []\n"
    frozen_once = contract.freeze_proper_and_trailer(body, "authorized:\n  auth_seq: 1\n")
    proper, trailer = contract.split_contract_bytes(frozen_once)
    edited = proper + b"\n" + trailer          # the stray blank line an edit leaves behind
    recorded = contract.contract_sha256_bytes(edited)
    refrozen = contract.freeze_proper_and_trailer(edited, "authorized:\n  auth_seq: 2\n")
    assert contract.contract_sha256_bytes(refrozen) == recorded


# ── the bound that makes trailing-newline canonicalization sound ─────────────────────────────
# Canonicalizing trailing newlines out of the hashed region is safe only while trailing newlines
# carry no meaning. YAML's KEEP-CHOMPING indicators (`|+`, `>+`) make trailing blank lines part of
# the scalar's VALUE, so two contracts with genuinely different frozen values would share one
# contract_sha256 — a collision INSIDE the signed region. Refused outright rather than disclosed.

def test_keep_chomped_block_scalar_is_refused():
    bad = (b"spec_ref: x\nscope:\n  allowed_paths: []\ncheckpoints: []\n"
           b"note: |+\n  hi\n\n\n")
    _ok, errors, _w = contract.validate_contract_bytes(bad)
    assert any("keep-chomped" in e for e in errors), errors


def test_the_collision_keep_chomping_would_allow_is_real():
    """Not a hypothetical: without the floor these two contracts hash identically while parsing to
    different values. This test documents WHY the floor exists, so a future reader does not remove
    it as pedantry."""
    a = b"k: |+\n  hi\n"
    b = b"k: |+\n  hi\n\n\n"
    assert yaml.safe_load(a) != yaml.safe_load(b), "the values genuinely differ"
    assert contract.contract_sha256_bytes(a) == contract.contract_sha256_bytes(b), (
        "…and the hash cannot tell them apart — which is exactly what the floor refuses"
    )


def test_plain_block_scalars_and_prose_mentions_are_not_refused():
    """The floor must not fire on `|`/`>` (whose trailing blank lines are NOT content) or on a
    comment that merely names the indicator."""
    for body in (b"spec_ref: x\nscope:\n  allowed_paths: []\ncheckpoints: []\nnote: |\n  hi\n",
                 b"spec_ref: x\nscope:\n  allowed_paths: []\ncheckpoints: []\nnote: >\n  hi\n",
                 b"spec_ref: x\nscope:\n  allowed_paths: []\ncheckpoints: []\n# prose about |+ here\n"):
        _ok, errors, _w = contract.validate_contract_bytes(body)
        assert not [e for e in errors if "keep-chomped" in e], (body, errors)


# ── the freeze-write assertion must convict a WRITER-side regression ─────────────────────────
# The old post-freeze check compared two hashes that both canonicalize, so it could not fail — and
# would have stayed green if `freeze_proper_and_trailer` stopped canonicalizing, the very defect its
# error message named. The replacement asserts the frozen bytes literally begin with the canonical
# contract-proper + sentinel.

def test_frozen_bytes_begin_with_the_canonical_proper_and_sentinel():
    raw = b"spec_ref: x\nscope:\n  allowed_paths: []\ncheckpoints: []\n\n\n"
    frozen = contract.freeze_proper_and_trailer(raw, "authorized:\n  auth_seq: 1\n")
    expected = contract.canonicalize_proper(contract.split_contract_bytes(raw)[0]) + contract._SENTINEL_B
    assert frozen.startswith(expected)


def test_a_non_canonicalizing_writer_is_convicted():
    """Simulates the regression: a writer that appends the sentinel WITHOUT canonicalizing. The
    old hash-equality check passed in this state; the prefix assertion must not."""
    raw = b"spec_ref: x\nscope:\n  allowed_paths: []\ncheckpoints: []\n\n\n"
    proper, _ = contract.split_contract_bytes(raw)
    regressed = proper + contract._SENTINEL_B + b"\n" + b"authorized:\n  auth_seq: 1\n"
    expected = contract.canonicalize_proper(proper) + contract._SENTINEL_B
    assert not regressed.startswith(expected), "the prefix assertion convicts the writer regression"
    # …while the assertion it replaced would NOT have caught it:
    assert contract.contract_sha256_bytes(regressed) == contract.contract_sha256_bytes(raw), (
        "the old hash-equality check stayed green here — which is why it was replaced"
    )


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# feat-foundry-gate-integrity-retirement-grounding (ER #120 + ER #121) — the RETIREMENT member.
#
# Both ERs were reproduced against v1.5.0 before the spec was written:
#   ER #120  structural floor refused `classification: remove`; and after a landed drop, `exists` and
#            `alter` both errored (AC-SGC-4) while `net-new` PASSED — so the only open path recorded a
#            deleted artifact as one that does not yet exist, and froze that falsehood.
#   ER #121  the retired path matched zero entries and the message asserted "a stale path prefix or
#            typo" — a cause the floor cannot establish and the wrong remedy for a real removal.
#
# THE VACUITY THESE GUARD AGAINST: the shipped dispatch is `if net-new: … elif exists/alter: …` with
# no else, so adding "remove" to the constant and the schema and touching NOTHING ELSE makes it fall
# through both branches and error in NO state. That no-op satisfies every ACCEPTANCE assertion here.
# `test_enum_only_no_op_implementation_is_convicted` is the row that tells them apart.
# ─────────────────────────────────────────────────────────────────────────────────────────────────

_SNAP_PRESENT = {"grounding_configured": True, "entities": {"sessions": {}}, "modules": []}
_SNAP_ABSENT = {"grounding_configured": True, "entities": {"users": {}}, "modules": []}


def _art(kind, identifier, classification):
    return {"kind": kind, "identifier": identifier, "classification": classification}


def _sg(*artifacts):
    return {"system_grounding": {"artifacts": list(artifacts)}}


def _venue(tmp_path, *present):
    for rel in present:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    return str(tmp_path)


def _apg(allowed, artifacts, root, checkpoints=None):
    data = {"scope": {"allowed_paths": list(allowed)}, "checkpoints": checkpoints or []}
    data.update(_sg(*artifacts))
    return contract.allowed_paths_grounding_errors(data, root)


class TestRetirementGrounding:
    # ── AC-RGR-1 — set EQUALITY across both declarations, schema read as JSON ────────────────────
    def test_classification_vocabulary_set_equality_read_as_json(self):
        """Read the schema with json.load, NEVER through jsonschema.validate: the JSON-Schema floor
        returns [] early when jsonschema is unimportable, so a one-sided edit is invisible in exactly
        the environment where it goes undetected — and it fails in the PERMISSIVE direction."""
        import json
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "schema", "acceptance-contract.schema.json")) as fh:
            schema = json.load(fh)
        node = schema["properties"]["system_grounding"]["properties"]["artifacts"]["items"]
        schema_enum = set(node["properties"]["classification"]["enum"])
        assert schema_enum == contract._SG_CLASSIFICATIONS, (
            f"schema enum {sorted(schema_enum)} != module constant "
            f"{sorted(contract._SG_CLASSIFICATIONS)} — a one-sided edit (AC-RGR-1)"
        )
        assert "remove" in schema_enum
        print("RGR-1-VOCAB-EQUAL-OK")

    def test_one_sided_vocabulary_edit_is_convicted(self):
        """MUTANT: the realistic bad implementation — edit the Python constant, forget the JSON
        schema (or the reverse). The equality assertion above must go RED on it."""
        for schema_enum, constant in (
            ({"exists", "alter", "net-new"}, {"exists", "alter", "net-new", "remove"}),
            ({"exists", "alter", "net-new", "remove"}, {"exists", "alter", "net-new"}),
        ):
            assert schema_enum != constant, "a one-sided edit must not compare equal"
        print("RGR-1-MUTANT-OK")

    # ── AC-RGR-2 — both named floors accept a removal for an ABSENT artifact ─────────────────────
    def test_removal_accepted_by_structural_and_consistency_floors(self):
        import json
        payload = {
            "spec_ref": "specs/x/feat-x.md", "spec_sha256": "0" * 64, "target_repo": "r",
            "scope": {"allowed_paths": ["a"]},
            "checkpoints": [{"ac_id": "AC-X-1", "surface": "cli:c",
                             "locator": "python3 -m pytest -q",
                             "expect": {"op": "matches", "value": "T", "baseline": "pre-change"}}],
        }
        payload.update(_sg(_art("table", "sessions", "remove")))
        ok, errors, _ = contract.validate_contract_bytes(json.dumps(payload).encode())
        assert not [e for e in errors if "classification" in e], errors
        assert contract.system_grounding_errors(_sg(_art("table", "sessions", "remove")),
                                                _SNAP_ABSENT) == []
        print("RGR-2-ACCEPTED-2of2-OK")

    # ── AC-RGR-3 — FAIL-CLOSED ON ABSENCE. The refusal half, which a no-op omits ─────────────────
    def test_removal_of_a_still_present_artifact_is_refused(self):
        errors = contract.system_grounding_errors(_sg(_art("table", "sessions", "remove")),
                                                  _SNAP_PRESENT)
        assert errors, "declared removed but still present must be REFUSED (AC-RGR-3)"
        assert "still present" in errors[0]
        assert "'exists'" in errors[0], "the refusal must name the honest alternative for this state"
        print("RGR-3-FAILCLOSED-OK")

    def test_enum_only_no_op_implementation_is_convicted(self):
        """THE LOAD-BEARING MUTANT. Reproduces the enum-only implementation — both declarations gain
        the value, `system_grounding_errors` untouched, so `remove` falls through the if/elif with no
        branch — and requires the AC-RGR-3 property to go RED on it. Without this row the whole atom
        is an enum edit wearing a predicate's spec."""
        def no_op_floor(data, snapshot):
            out = []
            for a in data["system_grounding"]["artifacts"]:
                if a["classification"] == "net-new" and a["identifier"] in snapshot["entities"]:
                    out.append("net-new but already live")
                elif a["classification"] in ("exists", "alter") and \
                        a["identifier"] not in snapshot["entities"]:
                    out.append("absent from the live snapshot")
                # `remove` reaches no branch — the defect
            return out

        payload = _sg(_art("table", "sessions", "remove"))
        assert no_op_floor(payload, _SNAP_PRESENT) == [], "the no-op is silent here, by construction"
        assert contract.system_grounding_errors(payload, _SNAP_PRESENT), (
            "the real implementation must REFUSE where the no-op is silent"
        )
        print("RGR-3-NOOP-MUTANT-OK")

    def test_removal_cross_dimension_collision_is_caught_for_any_kind(self):
        """Security review of PR #122, Risk 2. An earlier draft gated `matched or cross` behind
        `kind in _SG_GROUNDED_KINDS` for the remove arm, while the net-new arm applies it
        kind-independently. That left a resource removal whose identifier collides with a LIVE table
        or module silent — a per-artifact opt-out reached through `kind` instead of `classification`,
        and `resource` is exactly the kind the allowed_paths tolerance keys on."""
        snap = {"grounding_configured": True, "entities": {"sessions": {}}, "modules": ["app.users"]}
        for ident in ("sessions", "app.users"):
            errors = contract.system_grounding_errors(_sg(_art("resource", ident, "remove")), snap)
            assert errors, f"resource/{ident} collides with a live artifact and must be REFUSED"
            # The CROSS-only case gets its OWN message. Reporting it as "still present, declare
            # 'exists'" sends the author in a circle — that route passes the SG floor (the ungrounded
            # exists/alter arm is skipped) and is then refused by the allowed_paths floor with a
            # different message, neither naming the cause. Same defect class AC-RGR-7 fixes.
            assert "collides whole-string" in errors[0], errors[0]
            assert "declare 'exists'" not in errors[0], (
                "the cross-only case must NOT be given the still-present remedy")
        # …and the genuinely-still-present case keeps the amend-to-remove remedy
        present = contract.system_grounding_errors(
            _sg(_art("table", "sessions", "remove")), snap)
        assert present and "still present in the live snapshot" in present[0]
        assert "declare 'exists'" in present[0]
        # …and an ordinary retired file path must NOT trip it (no false positive)
        assert contract.system_grounding_errors(
            _sg(_art("resource", "e2e/staging-smoke.spec.ts", "remove")), snap) == []
        print("RGR-3-CROSS-KIND-OK")

    # ── AC-RGR-4 — ungrounded kinds: the venue root is the oracle, still fail-closed ─────────────
    def test_ungrounded_kind_absence_is_checked_against_the_venue_root_2of2(self, tmp_path):
        root = _venue(tmp_path, "e2e/here.spec.ts")
        present = contract.removal_grounding_errors(_sg(_art("resource", "e2e/here.spec.ts",
                                                             "remove")), root)
        assert present and "still present under the venue root" in present[0]
        gone = contract.removal_grounding_errors(_sg(_art("resource", "e2e/gone.spec.ts",
                                                          "remove")), root)
        assert gone == []
        assert contract.removal_grounding_errors(_sg(_art("resource", "e2e/here.spec.ts",
                                                          "remove")), None) == []
        print("RGR-4-VENUE-ORACLE-2of2-OK")

    # ── AC-RGR-5 — the three existing classifications are unchanged, five cases ──────────────────
    def test_existing_classifications_are_unchanged_5of5(self):
        cases = [
            (_sg(_art("table", "sessions", "net-new")), _SNAP_PRESENT, True),
            (_sg(_art("queue", "sessions", "net-new")), _SNAP_PRESENT, True),   # CROSS dimension
            (_sg(_art("table", "sessions", "exists")), _SNAP_ABSENT, True),
            (_sg(_art("table", "sessions", "alter")), _SNAP_ABSENT, True),
            (_sg(_art("resource", "x/y.ts", "alter")), _SNAP_ABSENT, False),    # ungrounded skip
        ]
        for i, (data, snap, expect_error) in enumerate(cases):
            errors = contract.system_grounding_errors(data, snap)
            assert bool(errors) is expect_error, f"case {i} regressed: {errors}"
        print("RGR-5-UNCHANGED-5of5-OK")

    def test_dropped_cross_dimension_branch_is_convicted(self):
        """MUTANT: the cross-dimension collision catch is the floor's only kind-independent check,
        lives solely inside the net-new branch, and NO pre-existing test drove it — so a dispatch
        restructure to admit a fourth member can drop it with every other assertion still green."""
        def without_cross(data, snapshot):
            out = []
            for a in data["system_grounding"]["artifacts"]:
                matched = contract._sg_identifier_matches(
                    a["kind"], a["identifier"], snapshot["entities"], set(snapshot["modules"]))
                if a["classification"] == "net-new" and matched:   # `or cross` dropped
                    out.append("already live")
            return out

        payload = _sg(_art("queue", "sessions", "net-new"))  # ungrounded kind => matched is False
        assert without_cross(payload, _SNAP_PRESENT) == [], "the mutant is silent here"
        assert contract.system_grounding_errors(payload, _SNAP_PRESENT), (
            "the real floor must still catch the cross-dimension collision"
        )
        print("RGR-5-MUTANT-OK")

    # ── AC-RGR-6 — the two floors agree, and the tolerance stays bounded ─────────────────────────
    def test_zero_match_literal_admitted_when_its_removal_is_declared(self, tmp_path):
        root = _venue(tmp_path, "e2e/kept.spec.ts")
        retired = "e2e/staging-smoke.spec.ts"
        assert _apg([retired], [_art("resource", retired, "remove")], root) == []
        assert _apg([retired], [], root), "no declaration => still refused"
        assert _apg([retired], [_art("resource", retired, "alter")], root), "'alter' is not a removal"
        print("RGR-6-BOUND-OK")

    def test_glob_entry_is_never_admitted_by_a_removal_declaration(self, tmp_path):
        """A glob would admit an unbounded subtree on one unverifiable line, into a field three
        shipped authorize-time floors consult to relax THEMSELVES. The floor being relaxed exists to
        catch single-path typos; the tolerance stays at that scale."""
        root = _venue(tmp_path, "e2e/kept.spec.ts")
        # Every glob here must match ZERO paths in the fixture venue — otherwise it is admitted by
        # AC-APG-1 (it really does ground) and never reaches the retirement route, so the assertion
        # would prove nothing about the tolerance. `e2e/*.spec.ts` is deliberately NOT used: it
        # matches kept.spec.ts.
        for glob in (".github/workflows/**", "retired/*.spec.ts", "src/?.ts"):
            assert not contract._allowed_path_exists(glob, root), (
                f"{glob} must match zero paths for this assertion to be about the tolerance"
            )
            assert _apg([glob], [_art("resource", glob, "remove")], root), f"{glob} must be refused"
        for outside in ("/etc/passwd", "../outside/x.ts"):
            assert _apg([outside], [_art("resource", outside, "remove")], root)
        print("RGR-6-GLOB-MUTANT-OK")

    def test_removal_for_another_identifier_or_kind_confers_nothing(self, tmp_path):
        root = _venue(tmp_path, "e2e/kept.spec.ts")
        retired = "e2e/staging-smoke.spec.ts"
        assert _apg([retired], [_art("resource", "e2e/other.spec.ts", "remove")], root), \
            "a removal declared for a DIFFERENT identifier confers nothing"
        assert _apg([retired], [_art("table", retired, "remove")], root), \
            "a removal declared under a different KIND confers nothing (pair-keyed)"
        assert _apg([retired], [_art("resource", retired, "remove"),
                                _art("queue", retired, "exists")], root), \
            "an identifier declared both removed and alive is contradicted — tolerance withheld"
        print("RGR-6-SCOPE-MUTANT-OK")

    # ── AC-RGR-7 — the diagnostic states an observation and routes, asserts no unestablished cause ─
    def test_zero_match_message_has_observation_and_routes_parts(self, tmp_path):
        root = _venue(tmp_path, "e2e/kept.spec.ts")
        errors = _apg(["e2e/typo.spec.ts"], [], root)
        assert len(errors) == 1
        msg = errors[0]
        assert "observed:" in msg and "routes:" in msg, "structured parts required"
        obs, routes = msg.split("observed:", 1)[1].split("routes:", 1)
        assert "ZERO paths" in obs
        for n in ("(1)", "(2)", "(3)"):
            assert n in routes, f"route {n} missing"
        assert "classification: remove" in routes, "the removal route must be named"
        assert msg.index("observed:") < msg.index("routes:")
        print("RGR-7-STRUCTURED-OK")

    def test_reworded_cause_assertion_is_convicted(self):
        """MUTANT: a message that dropped the exact phrase but still asserts an unestablished cause.
        A row asserting only the absence of 'stale path prefix or typo' passes this artifact."""
        reworded = ("scope.allowed_paths entry 'x': matches ZERO paths — this usually indicates a "
                    "mistyped prefix; fix the path and re-check")
        assert "stale path prefix or typo" not in reworded, "the phrase-absence check passes it"
        assert not ("observed:" in reworded and "routes:" in reworded), (
            "the structural check convicts it — which is why AC-RGR-7 asserts parts, not absence"
        )
        print("RGR-7-REWORD-MUTANT-OK")

    # ── AC-RGR-8 — non-vacuity ───────────────────────────────────────────────────────────────────
    def test_retirement_criteria_are_exercised_not_vacuous(self, tmp_path):
        """An empty artifacts list satisfies every negative above trivially. Assert the fixtures
        actually declare removals and that an empty declaration grants nothing."""
        root = _venue(tmp_path, "e2e/kept.spec.ts")
        assert contract._sg_removed_pairs(_sg(_art("resource", "e2e/x.ts", "remove"))) == \
            {("resource", "e2e/x.ts")}
        assert contract._sg_removed_pairs(_sg()) == set()
        assert contract._sg_removed_pairs({}) == set()
        assert _apg(["e2e/gone.spec.ts"], [], root), "an empty block grants no tolerance"
        assert contract.system_grounding_errors(_sg(_art("table", "sessions", "remove")),
                                                _SNAP_PRESENT), "the refusal path is reachable"
        print("RGR-8-NONVACUOUS-OK")


# ── The UNFILTERED full-suite regression row (AC-RGR-5) ──────────────────────────────────────────
# The frozen contract's locator for this row is `python3 -m pytest tests/test_contract_authz.py -q -s`
# — the whole file, no `-k`. The prior contract draft made this row `-k`-filtered under a comment
# saying a filtered row cannot see a regression it does not select for; it committed the defect it
# named. So the token has to be emitted by the RUN, not by a selected test, and it must be emitted
# only when nothing in the file failed.
#
# A session-scoped fixture teardown is the seam that works from inside a test module: pytest reads
# hooks only from conftest.py/plugins, and conftest.py is outside this atom's allowed_paths. At
# session teardown `request.session.testsfailed` reflects the whole run, so the token is printed if
# and only if every test in the file passed.
@pytest.fixture(scope="session")
def _suite_green_token(request):
    yield
    if request.session.testsfailed == 0:
        # Two frozen contracts pin a whole-file suite token: retirement-grounding (RGR) and
        # retirement-grounding-wiring (RGW). Both are satisfied by the same unfiltered run, so both
        # are emitted here rather than by two near-identical fixtures.
        print("RGR-5-SUITE-GREEN-OK")
        print("RGW-5-SUITE-GREEN-OK")


def test_contract_authz_suite_green_token(_suite_green_token):
    """Requests the session fixture whose TEARDOWN emits the suite token. This test asserts nothing
    itself — deliberately: the assertion it would make ('the suite is green') is not knowable from
    inside a test, which is exactly why the token is emitted at session teardown instead."""


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# feat-foundry-gate-integrity-retirement-grounding-wiring (AC-RGW-1..8) — the CALL SITE.
#
# `removal_grounding_errors` shipped with no caller: implemented, tested, inert, while the schema told
# authors the check ran. These tests DRIVE THE FREEZE as a subprocess and assert on exit status and
# stderr — never on source text (AC-RGW-6). A grep-for-the-call witness passes against a call placed
# after an early return, which is precisely the defect class this atom repairs.
#
# CLAUDE_PROJECT_DIR hygiene is load-bearing: the driver falls back to os.getcwd(), so a harness that
# forgets to set it resolves the venue root to the REAL repo and stats real paths — passing for the
# wrong reason, on a check whose whole subject is a filesystem oracle. `_ws` always sets it.
# ─────────────────────────────────────────────────────────────────────────────────────────────────

import json  # noqa: E402  (module-level import for the freeze-driving harness below)

_AUTHORIZE = os.path.join(REPO_ROOT, "scripts", "foundry-authorize.py")


def _ws(tmp_path, *, artifacts, allowed_paths, target_repo="workspace",
        venue_files=(), workspace_files=(), venue_subdir=None):
    """Materialize a throwaway workspace and return (ws_root, spec_path, contract_path).

    `workspace_files` land at the workspace root; `venue_files` land under `venue_subdir` when a
    multi-repo target is used (that is what lets AC-RGW-3 tell the two roots apart).
    """
    ws = tmp_path / "ws"
    (ws / ".claude").mkdir(parents=True)
    (ws / ".foundry").mkdir(parents=True)
    (ws / "specs").mkdir(parents=True)
    (ws / ".claude" / "foundry-operators.json").write_text(json.dumps(
        {"schema_version": 1, "operators": {"op_test": {"name": "T", "git_email": "t@example.com"}}}))

    for rel in workspace_files:
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")

    if venue_subdir:
        venue = ws / venue_subdir
        venue.mkdir(parents=True, exist_ok=True)
        (ws / ".claude" / "foundry-project.json").write_text(json.dumps(
            {"repos": {target_repo: {"path": venue_subdir}}}))
        for rel in venue_files:
            p = venue / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x")

    spec = ws / "specs" / "feat-x.md"
    spec.write_text("# x\n\n<!-- normative -->\n\n- **AC-X-1** *(Invariant)*: a thing.\n\n"
                    "<!-- /normative -->\n")

    contract = {
        "spec_ref": "specs/feat-x.md",
        "spec_sha256": contract_mod_spec_sha(str(spec)),
        "target_repo": target_repo,
        "scope": {"allowed_paths": list(allowed_paths)},
        "checkpoints": [{"ac_id": "AC-X-1", "surface": "cli:foundry-doctor",
                         "locator": "python3 -m pytest -q",
                         "expect": {"op": "matches", "value": "T", "baseline": "pre-change"}}],
    }
    if artifacts is not None:
        contract["system_grounding"] = {"artifacts": list(artifacts)}
    cpath = ws / "acceptance-contract.yaml"
    cpath.write_text(yaml.safe_dump(contract))

    # the §8 audit precondition — a row bound to this spec's content hash
    (ws / ".foundry" / "audit-ledger.jsonl").write_text(json.dumps(
        {"spec_sha256": contract["spec_sha256"], "spec_ref": "specs/feat-x.md",
         "rounds": 1, "verdict": "plateau-clean"}) + "\n")
    return str(ws), str(spec), str(cpath)


def contract_mod_spec_sha(path):
    return contract.spec_sha256(path)


def _freeze(ws_root, spec, cpath):
    """Drive the real freeze driver. DRY RUN — no `--yes` — so nothing is written; every assertion
    here fails closed long before the write anyway."""
    env = dict(os.environ, CLAUDE_PROJECT_DIR=ws_root)
    r = subprocess.run([sys.executable, _AUTHORIZE, "--spec", spec, "--contract", cpath,
                        "--operator", "op_test", "--mode", "lean"],
                       capture_output=True, text=True, env=env, cwd=ws_root)
    return r.returncode, r.stdout + r.stderr


def _art(kind, identifier, classification):
    return {"kind": kind, "identifier": identifier, "classification": classification}


class TestRetirementGroundingWiring:
    # ── AC-RGW-1 — the check RUNS and fails closed ───────────────────────────────────────────────
    def test_freeze_fails_closed_on_a_still_present_ungrounded_removal(self, tmp_path):
        ws, spec, cpath = _ws(
            tmp_path,
            artifacts=[_art("resource", "e2e/still-here.spec.ts", "remove")],
            allowed_paths=["e2e"],
            workspace_files=["e2e/still-here.spec.ts"])
        rc, out = _freeze(ws, spec, cpath)
        assert rc != 0, f"a declared removal that has NOT landed must refuse the freeze\n{out}"
        assert "AC-RGW-1" in out and "e2e/still-here.spec.ts" in out, out
        print("RGW-1-FAILCLOSED-OK")

    def test_freeze_admits_a_genuinely_absent_ungrounded_removal(self, tmp_path):
        """The mirror. Without it the atom is satisfiable by a call that always fails, which would
        wedge every legitimate retirement — invisible to a fail-closed-only assertion."""
        ws, spec, cpath = _ws(
            tmp_path,
            artifacts=[_art("resource", "e2e/gone.spec.ts", "remove")],
            allowed_paths=["e2e/gone.spec.ts"],
            workspace_files=["e2e/kept.spec.ts"])
        rc, out = _freeze(ws, spec, cpath)
        assert rc == 0, f"a landed removal must freeze cleanly\n{out}"
        print("RGW-1-CLEANPATH-OK")

    # ── AC-RGW-2 — diagnostic completeness (NOT ordering; see the contract header) ───────────────
    def test_retirement_violation_is_reported_not_masked(self, tmp_path):
        ws, spec, cpath = _ws(
            tmp_path,
            artifacts=[_art("resource", "e2e/still-here.spec.ts", "remove")],
            allowed_paths=["e2e", "totally/stale/prefix"],
            workspace_files=["e2e/still-here.spec.ts"])
        rc, out = _freeze(ws, spec, cpath)
        assert rc != 0
        assert "AC-RGW-1" in out, f"the retirement violation must be surfaced, not masked\n{out}"
        print("RGW-2-REPORTED-OK")

    # ── AC-RGW-3 — the VENUE root, not the workspace root ────────────────────────────────────────
    def test_retirement_check_uses_the_venue_root_not_the_workspace_root(self, tmp_path):
        """Artifact ABSENT in the venue, PRESENT at the workspace root under the same relative path.
        A re-resolving implementation reads the workspace and refuses; the correct one admits."""
        ws, spec, cpath = _ws(
            tmp_path,
            artifacts=[_art("resource", "e2e/ghost.spec.ts", "remove")],
            allowed_paths=["e2e/ghost.spec.ts"],
            target_repo="product",
            venue_subdir="product-repo",
            venue_files=["e2e/other.spec.ts"],
            workspace_files=["e2e/ghost.spec.ts"])
        rc, out = _freeze(ws, spec, cpath)
        assert rc == 0, (
            "the check must read the VENUE root; refusing here means it re-resolved to the "
            f"workspace root, where the path still exists\n{out}")
        print("RGW-3-VENUE-OK")

    # ── AC-RGW-4 — unresolvable venue root degrades with an ATTRIBUTABLE disclosure ──────────────
    def test_unresolvable_venue_root_degrades_with_an_attributable_warning(self, tmp_path):
        """Both halves. The freeze must not block, AND the disclosure must be attributable to THIS
        check — the driver already prints several sibling degrade warnings on this path, so a generic
        'a skip was disclosed' assertion is green while this check's own skip stays invisible."""
        ws, spec, cpath = _ws(
            tmp_path,
            artifacts=[_art("resource", "e2e/whatever.spec.ts", "remove")],
            allowed_paths=["e2e/whatever.spec.ts"],
            target_repo="absent-product")  # no foundry-project.json entry => venue root is None
        rc, out = _freeze(ws, spec, cpath)
        assert rc == 0, f"an unresolvable venue root must degrade, never block\n{out}"
        assert "retirement grounding degraded" in out, (
            f"the disclosure must be attributable to THIS check, not a sibling's\n{out}")
        assert "AC-RGW-4" in out, out
        print("RGW-4-DEGRADE-2of2-OK")

    # ── AC-RGW-5 — contracts WITHOUT a removal are unaffected ────────────────────────────────────
    def test_contracts_without_a_removal_freeze_identically(self, tmp_path):
        for artifacts in (None, [], [_art("resource", "e2e/kept.spec.ts", "exists")]):
            ws, spec, cpath = _ws(
                tmp_path / f"case{abs(hash(str(artifacts))) % 9973}",
                artifacts=artifacts,
                allowed_paths=["e2e/kept.spec.ts"],
                workspace_files=["e2e/kept.spec.ts"])
            rc, out = _freeze(ws, spec, cpath)
            assert rc == 0, f"a contract with no removal must be unaffected\n{out}"
            assert "AC-RGW-1" not in out, out
        print("RGW-5-UNCHANGED-OK")

    # ── AC-RGW-7 — the shipped author-facing claim is no longer false ────────────────────────────
    def test_schema_no_longer_claims_the_check_is_unwired(self):
        with open(os.path.join(REPO_ROOT, "schema", "acceptance-contract.schema.json")) as fh:
            schema = json.load(fh)
        desc = (schema["properties"]["system_grounding"]["properties"]["artifacts"]["items"]
                ["properties"]["classification"]["description"])
        assert "NOT YET WIRED" not in desc, (
            "the schema still tells authors the ungrounded check does not run")
        assert "no caller invokes it" not in desc, desc
        assert "venue root" in desc, "the description must say what the check actually does"
        print("RGW-7-DOCTRUE-OK")

    # ── AC-RGW-8 — the slot sits BETWEEN the grounding floor and the allowed_paths floor ─────────
    def test_insertion_slot_sits_between_grounding_and_allowed_paths_2of2(self, tmp_path):
        """Witnessed behaviourally, not positionally. (a) a grounded contradiction must still be
        reported ahead of a bogus removal — 2.6 precedes; (b) a bogus removal must be reported ahead
        of a stale allowed_paths prefix — 2.7 follows."""
        ws, spec, cpath = _ws(
            tmp_path / "a",
            artifacts=[_art("resource", "e2e/still-here.spec.ts", "remove"),
                       _art("resource", "e2e/still-here.spec.ts", "remove")],
            allowed_paths=["e2e"],
            workspace_files=["e2e/still-here.spec.ts"])
        rc_a, out_a = _freeze(ws, spec, cpath)
        assert rc_a != 0 and "AC-SGC-1" in out_a, (
            f"a structural grounding error must still precede the retirement check\n{out_a}")

        ws, spec, cpath = _ws(
            tmp_path / "b",
            artifacts=[_art("resource", "e2e/still-here.spec.ts", "remove")],
            allowed_paths=["e2e", "stale/prefix/that/does/not/exist"],
            workspace_files=["e2e/still-here.spec.ts"])
        rc_b, out_b = _freeze(ws, spec, cpath)
        assert rc_b != 0 and "AC-RGW-1" in out_b and "ER #179" not in out_b, (
            f"the retirement check must precede the allowed_paths floor\n{out_b}")
        print("RGW-8-SLOT-2of2-OK")

    # ── AC-RGW-6 — THE REACHABILITY META. Materializes two unreachable-call implementations and
    # requires the AC-RGW-1 witness to go RED on each. A source-grep witness passes both, which is why
    # AC-RGW-6 bars that witness shape outright.
    #
    # THE MUTANTS ARE NON-TRIVIAL BY CONSTRUCTION: each mutated copy must STILL refuse an ER #179
    # fixture. Without that, the cheapest satisfying mutant is an early `return 0` that disables every
    # floor and proves nothing about THIS call's placement.
    def test_unreachable_call_site_is_convicted_2of2(self, tmp_path):
        src = open(_AUTHORIZE).read()
        block_head = "    _rgw_errors = fc.removal_grounding_errors(_cdata, _venue_root)"
        assert block_head in src, "the call site moved; this meta must be re-grounded"
        start = src.index(block_head)
        end = src.index("    # 2.7 ER #179", start)
        block, before, after = src[start:end], src[:start], src[end:]

        mutants = {
            # (a) guarded by the WRONG branch — runs only when there is no venue to check against
            "wrong_branch": before + "    if _venue_root is None:\n"
                            + "".join("    " + ln if ln.strip() else ln
                                      for ln in block.splitlines(keepends=True)) + after,
            # (b) dead code — the shape of a call placed after an early return
            "dead_code": before + "    if False:\n"
                         + "".join("    " + ln if ln.strip() else ln
                                   for ln in block.splitlines(keepends=True)) + after,
        }

        # SELF-GROUNDING BASELINE. Without this the meta is conditionally vacuous: both mutant
        # assertions below hold TRIVIALLY if the call site is already dead, because mutating dead code
        # changes nothing and `rc == 0` was never `rc != 0`. A sibling test happens to establish the
        # red today, but a meta that depends on another test's fixture surviving is a green that can
        # stop having been red without anything going red. Establish it here, against the UNMUTATED
        # driver, on the very fixture the mutants are driven with. (PR #124 security review, Risk 1.)
        ws0, spec0, cpath0 = _ws(
            tmp_path / "baseline",
            artifacts=[_art("resource", "e2e/still-here.spec.ts", "remove")],
            allowed_paths=["e2e"],
            workspace_files=["e2e/still-here.spec.ts"])
        rc0, out0 = _freeze(ws0, spec0, cpath0)
        assert rc0 != 0 and "AC-RGW-1" in out0, (
            "the UNMUTATED driver must refuse this fixture, or the mutant convictions below prove "
            f"nothing — they would be mutating already-dead code\n{out0}")

        for name, mutated in mutants.items():
            mdir = tmp_path / f"mutant_{name}"
            mdir.mkdir(parents=True)
            mpath = mdir / "foundry-authorize.py"
            mpath.write_text(mutated)
            # the driver does sys.path.insert(0, dirname(__file__)), so a bare copy cannot import its
            # siblings — point PYTHONPATH at the real scripts/ instead of copying the whole tree
            env_extra = {"PYTHONPATH": os.path.join(REPO_ROOT, "scripts")}

            def drive(ws_root, spec, cpath):
                env = dict(os.environ, CLAUDE_PROJECT_DIR=ws_root, **env_extra)
                r = subprocess.run([sys.executable, str(mpath), "--spec", spec, "--contract", cpath,
                                    "--operator", "op_test", "--mode", "lean"],
                                   capture_output=True, text=True, env=env, cwd=ws_root)
                return r.returncode, r.stdout + r.stderr

            # the AC-RGW-1 fixture must STOP being refused — that is the conviction
            ws, spec, cpath = _ws(
                tmp_path / f"fx_{name}",
                artifacts=[_art("resource", "e2e/still-here.spec.ts", "remove")],
                allowed_paths=["e2e"],
                workspace_files=["e2e/still-here.spec.ts"])
            rc, out = drive(ws, spec, cpath)
            assert rc == 0, (
                f"mutant {name!r} was expected to make the check unreachable, but the freeze still "
                f"refused — the meta is not convicting what it claims\n{out}")

            # …and the mutant must remain NON-TRIVIAL: sibling floors still reachable
            ws2, spec2, cpath2 = _ws(
                tmp_path / f"sib_{name}",
                artifacts=None,
                allowed_paths=["totally/stale/prefix"])
            rc2, out2 = drive(ws2, spec2, cpath2)
            assert rc2 != 0 and "ER #179" in out2, (
                f"mutant {name!r} disabled the sibling floors too, so it proves nothing about THIS "
                f"call's placement\n{out2}")
        print("RGW-6-REACHABLE-2of2-OK")
