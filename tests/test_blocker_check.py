"""tests/test_blocker_check.py — feat-foundry-blocker-requires-evidence (AC-BRE-1, AC-BRE-2,
AC-BRE-4).

Drives `schema/blocker.schema.json` (accept/refuse) and `scripts/foundry_blocker_check.py` (the
partition + the malformed-input floor) both by direct import (`conftest.load_module`, the sibling
suites' own idiom) and end-to-end over the shipped CLI, so the CLI's argv/exit-code contract is
exercised in addition to the pure logic.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from conftest import REPO_ROOT, load_module

bc = load_module("scripts/foundry_blocker_check.py", "foundry_blocker_check")

CLI = os.path.join(REPO_ROOT, "scripts", "foundry_blocker_check.py")
SCHEMA_PATH = os.path.join(REPO_ROOT, "schema", "blocker.schema.json")


def _run_cli(args, input_text=None):
    return subprocess.run(
        [sys.executable, CLI] + args,
        input=input_text, capture_output=True, text=True,
    )


# =================================================================================================
# AC-BRE-1 — schema/blocker.schema.json accept/refuse
# =================================================================================================


def _valid_blocker(**overrides):
    b = {
        "claim": "the operator's own AWS credential is required to provision the VPC",
        "evidence": ["cli:aws sts get-caller-identity exited 254, AccessDenied"],
        "attempted": ["ran terraform plan with the ambient role", "checked IAM policy docs"],
        "why_operator": "external-provisioning",
    }
    b.update(overrides)
    return b


def test_schema_is_well_formed_json_schema():
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        doc = json.load(fh)
    assert doc["type"] == "object"
    assert doc["additionalProperties"] is False
    assert set(doc["required"]) == {"claim", "evidence", "attempted", "why_operator"}
    assert set(doc["properties"].keys()) == {"claim", "evidence", "attempted", "why_operator", "handoff"}
    assert doc["properties"]["why_operator"]["enum"] == [
        "external-provisioning", "credential-step", "no-consensus-after-research",
        "security-widening", "irreversible-action",
    ]
    # handoff is deliberately unconstrained (no nested schema) — out of scope here per the charter
    assert doc["properties"]["handoff"]["type"] == "object"
    assert "properties" not in doc["properties"]["handoff"]


def test_schema_accepts_a_valid_blocker():
    jsonschema = pytest.importorskip("jsonschema")
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        schema = json.load(fh)
    jsonschema.validate(_valid_blocker(), schema)  # must not raise


def test_schema_accepts_a_valid_blocker_with_handoff():
    jsonschema = pytest.importorskip("jsonschema")
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        schema = json.load(fh)
    jsonschema.validate(_valid_blocker(handoff={"anything": "goes", "for": ["now"]}), schema)


@pytest.mark.parametrize("mutate,why", [
    (lambda b: b.pop("evidence"), "missing evidence"),
    (lambda b: b.__setitem__("evidence", []), "empty evidence list"),
    (lambda b: b.pop("attempted"), "missing attempted"),
    (lambda b: b.__setitem__("attempted", []), "empty attempted list"),
    (lambda b: b.__setitem__("why_operator", "operator-vibes"), "unknown why_operator"),
    (lambda b: b.pop("why_operator"), "missing why_operator"),
    (lambda b: b.pop("claim"), "missing claim"),
    (lambda b: b.__setitem__("claim", ""), "empty claim"),
    (lambda b: b.__setitem__("extra_field", "nope"), "extra key (additionalProperties: false)"),
])
def test_schema_refuses_each_invalid_shape(mutate, why):
    jsonschema = pytest.importorskip("jsonschema")
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        schema = json.load(fh)
    b = _valid_blocker()
    mutate(b)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(b, schema)


# =================================================================================================
# AC-BRE-2 — the partition (direct import over pure `partition`)
# =================================================================================================


def test_partition_on_a_mixed_list():
    candidates = [
        _valid_blocker(claim="valid one"),
        {"claim": "no evidence at all", "attempted": ["looked"], "why_operator": "credential-step"},
        _valid_blocker(claim="valid two", why_operator="irreversible-action"),
        {"claim": "bad why_operator", "evidence": ["e"], "attempted": ["a"], "why_operator": "vibes"},
    ]
    verdict = bc.partition(candidates)
    assert [b["claim"] for b in verdict["blockers"]] == ["valid one", "valid two"]
    assert len(verdict["next_tasks"]) == 2
    reasons = " ".join(nt["reason"] for nt in verdict["next_tasks"])
    assert "evidence" in reasons
    assert "why_operator" in reasons
    # every demoted candidate carries the ORIGINAL object back, unmutated
    demoted_claims = {nt["candidate"]["claim"] for nt in verdict["next_tasks"]}
    assert demoted_claims == {"no evidence at all", "bad why_operator"}


def test_partition_non_object_items_are_demoted_not_fatal():
    """PR #164 review Risk: a non-object ITEM inside an otherwise-valid list is a per-candidate shape
    failure — it lands in next_tasks with its reason, never exit 2 (only the top-level shape is the
    instrument's hard precondition)."""
    candidates = [_valid_blocker(claim="valid one"), "just a string", 42, None]
    verdict = bc.partition(candidates)
    assert [b["claim"] for b in verdict["blockers"]] == ["valid one"]
    assert len(verdict["next_tasks"]) == 3
    assert all("not a JSON object" in nt["reason"] for nt in verdict["next_tasks"])
    assert [nt["candidate"] for nt in verdict["next_tasks"]] == ["just a string", 42, None]


def test_partition_empty_list_is_both_empty():
    verdict = bc.partition([])
    assert verdict == {"blockers": [], "next_tasks": []}


def test_partition_all_valid():
    candidates = [_valid_blocker(claim=f"c{i}") for i in range(3)]
    verdict = bc.partition(candidates)
    assert len(verdict["blockers"]) == 3
    assert verdict["next_tasks"] == []


def test_partition_all_invalid():
    candidates = [{"claim": "x"}, {"claim": "y"}]
    verdict = bc.partition(candidates)
    assert verdict["blockers"] == []
    assert len(verdict["next_tasks"]) == 2


def test_structural_floor_runs_even_without_jsonschema(monkeypatch):
    """UL-0011-style guard: block the optional `jsonschema` import and confirm the hand-rolled
    structural floor alone still refuses an invalid candidate and accepts a valid one — the
    check never silently degrades to a no-op when the dependency is absent."""
    real_import = __import__

    def fake_import(name, *a, **kw):
        if name == "jsonschema":
            raise ImportError("simulated absence")
        return real_import(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", fake_import)
    assert bc.validate_candidate(_valid_blocker()) == []
    errors = bc.validate_candidate({"claim": "x"})
    assert errors  # still refuses


# =================================================================================================
# AC-BRE-2 — CLI: verdict shape, exit codes, stdin/file input
# =================================================================================================


def test_cli_exits_0_with_json_verdict_from_stdin():
    candidates = [_valid_blocker(), {"claim": "bad"}]
    proc = _run_cli(["--in", "-"], input_text=json.dumps(candidates))
    assert proc.returncode == 0, proc.stderr
    verdict = json.loads(proc.stdout)
    assert set(verdict.keys()) == {"blockers", "next_tasks"}
    assert len(verdict["blockers"]) == 1
    assert len(verdict["next_tasks"]) == 1
    assert "reason" in verdict["next_tasks"][0]


def test_cli_exits_0_with_json_verdict_from_file(tmp_path):
    p = tmp_path / "candidates.json"
    p.write_text(json.dumps([_valid_blocker()]))
    proc = _run_cli(["--in", str(p)])
    assert proc.returncode == 0, proc.stderr
    verdict = json.loads(proc.stdout)
    assert len(verdict["blockers"]) == 1
    assert verdict["next_tasks"] == []


def test_cli_malformed_json_exits_2():
    proc = _run_cli(["--in", "-"], input_text="not json at all")
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "REFUSED" in proc.stderr


def test_cli_non_array_json_exits_2():
    proc = _run_cli(["--in", "-"], input_text=json.dumps({"not": "a list"}))
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "REFUSED" in proc.stderr


def test_cli_missing_file_exits_2():
    proc = _run_cli(["--in", "/does/not/exist/candidates.json"])
    assert proc.returncode == 2
    assert "REFUSED" in proc.stderr


def test_cli_empty_array_exits_0_both_empty():
    proc = _run_cli(["--in", "-"], input_text="[]")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"blockers": [], "next_tasks": []}


# =================================================================================================
# AC-BRE-3 — the deck tick prompt instructs the lint before it reports a blocker
# =================================================================================================


def test_tick_prompt_template_instructs_the_blocker_check():
    template_path = os.path.join(REPO_ROOT, "skills", "command-deck", "tick-prompt.template.md")
    with open(template_path, encoding="utf-8") as fh:
        text = fh.read()
    assert "schema/blocker.schema.json" in text
    assert "foundry_blocker_check.py" in text
    assert "blockers" in text and "next_tasks" in text
    # instructs reporting only the blockers partition under Blockers, the rest under Next Tasks
    assert "Next Tasks" in text
