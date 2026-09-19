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


def _valid_handoff(**overrides):
    h = {
        "cwd": "/private/tmp/example-worktree",
        "command": "gh auth login",
        "why": "the operator's own AWS credential is required to provision the VPC",
        "expect": "prints 'Logged in to github.com'",
    }
    h.update(overrides)
    return h


def _valid_blocker(**overrides):
    b = {
        "claim": "the operator's own AWS credential is required to provision the VPC",
        "evidence": ["cli:aws sts get-caller-identity exited 254, AccessDenied"],
        "attempted": ["ran terraform plan with the ambient role", "checked IAM policy docs"],
        "why_operator": "external-provisioning",
        "handoff": _valid_handoff(),
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
    # AC-OHS-1: handoff is now constrained — cwd/command/why/expect, additionalProperties false.
    handoff_schema = doc["properties"]["handoff"]
    assert handoff_schema["type"] == "object"
    assert handoff_schema["additionalProperties"] is False
    assert set(handoff_schema["required"]) == {"cwd", "command", "why", "expect"}
    assert set(handoff_schema["properties"].keys()) == {"cwd", "command", "why", "expect"}
    # AC-OHS-2: credential-step / external-provisioning require handoff at the schema level too.
    assert doc["if"]["properties"]["why_operator"]["enum"] == ["credential-step", "external-provisioning"]
    assert doc["then"]["required"] == ["handoff"]


def test_schema_accepts_a_valid_blocker():
    jsonschema = pytest.importorskip("jsonschema")
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        schema = json.load(fh)
    jsonschema.validate(_valid_blocker(), schema)  # must not raise


def test_schema_accepts_a_valid_blocker_with_handoff():
    jsonschema = pytest.importorskip("jsonschema")
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        schema = json.load(fh)
    jsonschema.validate(_valid_blocker(handoff=_valid_handoff()), schema)
    jsonschema.validate(_valid_blocker(handoff=_valid_handoff(cwd="~/example-worktree")), schema)
    jsonschema.validate(_valid_blocker(handoff=_valid_handoff(cwd="~")), schema)


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
# AC-OHS-1 — schema/blocker.schema.json's `handoff` is constrained: cwd, command, why, expect
# =================================================================================================


@pytest.mark.parametrize("mutate,why", [
    (lambda h: h.pop("cwd"), "missing cwd"),
    (lambda h: h.__setitem__("cwd", "relative/path"), "cwd not absolute or ~-relative"),
    (lambda h: h.__setitem__("cwd", ""), "empty cwd"),
    (lambda h: h.pop("command"), "missing command"),
    (lambda h: h.__setitem__("command", "cd /x && rm -rf /"), "chained command (&&)"),
    (lambda h: h.__setitem__("command", "echo hi; echo bye"), "chained command (;)"),
    (lambda h: h.__setitem__("command", "cat foo | grep bar"), "chained command (|)"),
    (lambda h: h.__setitem__("command", "echo hi\necho bye"), "newline in command"),
    (lambda h: h.__setitem__("command", "set -e"), "top-level set -e"),
    (lambda h: h.__setitem__("command", "trap cleanup EXIT"), "top-level trap"),
    (lambda h: h.__setitem__("command", "exec bash"), "top-level exec"),
    (lambda h: h.pop("why"), "missing why"),
    (lambda h: h.pop("expect"), "missing expect"),
    (lambda h: h.__setitem__("extra_field", "nope"), "extra key (additionalProperties: false)"),
])
def test_schema_refuses_each_invalid_handoff_shape(mutate, why):
    jsonschema = pytest.importorskip("jsonschema")
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        schema = json.load(fh)
    b = _valid_blocker()
    mutate(b["handoff"])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(b, schema)


def test_schema_accepts_command_with_trap_or_exec_as_substring_of_a_longer_word():
    """Only a TOP-LEVEL set -e/trap/exec is refused -- a word that merely contains one is fine
    (e.g. a script literally named trap_handler.sh, or execute_tests.sh)."""
    jsonschema = pytest.importorskip("jsonschema")
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        schema = json.load(fh)
    jsonschema.validate(_valid_blocker(handoff=_valid_handoff(command="./trap_handler.sh --run")), schema)
    jsonschema.validate(_valid_blocker(handoff=_valid_handoff(command="./execute_tests.sh")), schema)


@pytest.mark.parametrize("command,offending", [
    ("cd /x && rm -rf /", "&&"),
    ("echo hi; echo bye", ";"),
    ("cat foo | grep bar", "|"),
    ("echo hi\necho bye", "newline"),
    ("echo $(cat ~/.aws/credentials)", "$("),
    ("echo `id`", "backtick"),
])
def test_handoff_check_refuses_chained_command_naming_the_token(command, offending):
    errs = bc._handoff_errors(_valid_handoff(command=command))
    assert errs, f"expected a refusal for {command!r}"
    assert any(offending in e for e in errs), errs


def test_handoff_check_refuses_relative_cwd():
    errs = bc._handoff_errors(_valid_handoff(cwd="some/relative/worktree"))
    assert errs
    assert any("cwd" in e for e in errs)


def test_handoff_check_accepts_absolute_and_tilde_cwd():
    assert bc._handoff_errors(_valid_handoff(cwd="/private/tmp/x")) == []
    assert bc._handoff_errors(_valid_handoff(cwd="~/x")) == []
    assert bc._handoff_errors(_valid_handoff(cwd="~")) == []


@pytest.mark.parametrize("command,offending_word", [
    ("set -e", "set -e"),
    ("trap cleanup EXIT", "trap"),
    ("exec bash", "exec"),
])
def test_handoff_check_refuses_top_level_shell_altering_words(command, offending_word):
    errs = bc._handoff_errors(_valid_handoff(command=command))
    assert errs
    assert any(offending_word.split()[0] in e for e in errs), errs


def test_handoff_check_does_not_flag_a_word_merely_containing_trap_or_exec():
    assert bc._handoff_errors(_valid_handoff(command="./trap_handler.sh --run")) == []
    assert bc._handoff_errors(_valid_handoff(command="./execute_tests.sh")) == []


def test_handoff_check_refuses_unknown_field():
    errs = bc._handoff_errors(_valid_handoff(extra="nope"))
    assert any("additionalProperties" in e for e in errs), errs


def test_handoff_check_accepts_a_valid_handoff():
    assert bc._handoff_errors(_valid_handoff()) == []


# =================================================================================================
# AC-OHS-2 — credential-step / external-provisioning REQUIRE a handoff
# =================================================================================================


@pytest.mark.parametrize("why_operator", ["credential-step", "external-provisioning"])
def test_credential_and_provisioning_blockers_require_handoff(why_operator):
    b = _valid_blocker(why_operator=why_operator)
    del b["handoff"]
    jsonschema = pytest.importorskip("jsonschema")
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        schema = json.load(fh)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(b, schema)
    # the hand-rolled floor refuses it too, and names why
    errors = bc.validate_candidate(b)
    assert any("requires handoff" in e for e in errors), errors


@pytest.mark.parametrize("why_operator", [
    "no-consensus-after-research", "security-widening", "irreversible-action",
])
def test_other_why_operator_values_do_not_require_handoff(why_operator):
    b = _valid_blocker(why_operator=why_operator)
    del b["handoff"]
    jsonschema = pytest.importorskip("jsonschema")
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        schema = json.load(fh)
    jsonschema.validate(b, schema)  # must not raise
    assert bc.validate_candidate(b) == []


def test_partition_demotes_credential_step_without_handoff_naming_the_reason():
    candidates = [{
        "claim": "need the operator's AWS credential",
        "evidence": ["e1"],
        "attempted": ["a1"],
        "why_operator": "credential-step",
    }]
    verdict = bc.partition(candidates)
    assert verdict["blockers"] == []
    assert len(verdict["next_tasks"]) == 1
    assert "requires handoff" in verdict["next_tasks"][0]["reason"]


def test_partition_accepts_credential_step_with_handoff():
    candidates = [_valid_blocker(why_operator="credential-step")]
    verdict = bc.partition(candidates)
    assert len(verdict["blockers"]) == 1
    assert verdict["next_tasks"] == []


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


# =================================================================================================
# AC-OHS-3 — the tick prompt: executive report cap, ids-in-fields, PushNotification discipline
# =================================================================================================


def _tick_prompt_text():
    template_path = os.path.join(REPO_ROOT, "skills", "command-deck", "tick-prompt.template.md")
    with open(template_path, encoding="utf-8") as fh:
        return fh.read()


def test_tick_prompt_caps_the_executive_report_at_twelve_bullet_lines():
    text = _tick_prompt_text()
    assert "TWELVE LINES" in text
    assert "ids:" in text
    # ids/PR numbers/shas live in fields, human names stay in prose
    assert "atom id, a PR number, a commit sha" in text or "atom id" in text


def test_tick_prompt_instructs_push_notification_only_when_blockers_nonempty():
    text = _tick_prompt_text()
    assert "PushNotification" in text
    assert "§5d" in text
    assert "non-empty" in text
    assert "If the partition is empty, send none." in text
    # never one per candidate
    assert "never one" in text.lower() or "never one notification per candidate" in text


def test_skill_md_documents_handoff_and_push_notification_discipline():
    skill_path = os.path.join(REPO_ROOT, "skills", "command-deck", "SKILL.md")
    with open(skill_path, encoding="utf-8") as fh:
        text = fh.read()
    assert "handoff" in text
    assert "PushNotification" in text
    assert "twelve bullet lines" in text


# =================================================================================================
# AC-OHS-4 — a rendered executive report stays within the twelve-line cap (pure fixture check;
# the deck's actual renderer is a prose instruction to the agent, not a script -- out of scope)
# =================================================================================================


def _count_bullet_lines(report_text: str) -> int:
    """Count top-level bullet lines (lines starting with '- ' after stripping leading
    whitespace) across an executive report's three sections. A continuation line (indented
    further, no leading '- ') is not a new bullet."""
    return sum(1 for line in report_text.splitlines() if line.strip().startswith("- "))


_FIXTURE_REPORT_WITHIN_CAP = """\
## Tasks Accomplished
- landed atom-admission-contract
- landed release-loader-vocabulary
ids: PR #247, PR #162

## Next Tasks
- waiting on CI for standing-grants-as-policy
- re-measure the ready set next tick

## Blockers
- the operator's own AWS credential is required to provision the VPC
ids: (see handoff)
"""

_FIXTURE_REPORT_OVER_CAP = "\n".join(f"- item {i}" for i in range(20))


def test_fixture_report_within_cap_counts_at_most_twelve():
    assert _count_bullet_lines(_FIXTURE_REPORT_WITHIN_CAP) <= 12


def test_fixture_report_over_cap_is_detected_by_the_same_counter():
    """Negative control: proves the counter actually convicts an over-cap report rather than
    trivially passing every input."""
    assert _count_bullet_lines(_FIXTURE_REPORT_OVER_CAP) > 12
