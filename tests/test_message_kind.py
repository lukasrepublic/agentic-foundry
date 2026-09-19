"""tests/test_message_kind.py — fleet-is-listagents (AC-FIL-3, AC-FIL-5).

Covers `scripts/foundry_message_kind.py`'s lint: the four message kinds, the HANDOFF json-block
shape (reused by import from `foundry_blocker_check._handoff_errors`, never re-copied), the
invalid/malformed exit split, and the new script's three registration files (both
`permission-floor.json` copies + the `floor-drift-corpus.json` full-floor fixture rows) — so a new
invocable script cannot silently fall outside the closed-world permission floor.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from conftest import REPO_ROOT, load_module

mk = load_module("scripts/foundry_message_kind.py", "foundry_message_kind")

CLI_PATH = os.path.join(REPO_ROOT, "scripts", "foundry_message_kind.py")

VALID_HANDOFF_JSON = {
    "cwd": "/tmp/work",
    "command": "ls -la",
    "why": "the operator holds the credential this needs",
    "expect": "a directory listing",
}


def _msg(*lines):
    return "\n".join(lines) + "\n"


def _run_cli(text, tmp_path, use_stdin=False):
    if use_stdin:
        proc = subprocess.run([sys.executable, CLI_PATH, "--in", "-"], input=text,
                               capture_output=True, text=True)
        return proc
    p = tmp_path / "msg.txt"
    p.write_text(text, encoding="utf-8")
    proc = subprocess.run([sys.executable, CLI_PATH, "--in", str(p)],
                           capture_output=True, text=True)
    return proc


# ================================================================================================ #
# the four kinds
# ================================================================================================ #

def test_finding_with_evidence_is_valid():
    verdict = mk.lint(_msg("FINDING: the watcher stalled", "evidence: docs/troubleshooting.md"))
    assert verdict == {"valid": True, "kind": "FINDING"}


def test_needs_interface_needs_no_evidence():
    verdict = mk.lint(_msg("NEEDS-INTERFACE: which container name should I use?"))
    assert verdict == {"valid": True, "kind": "NEEDS-INTERFACE"}


def test_challenge_with_evidence_is_valid():
    verdict = mk.lint(_msg("CHALLENGE: this count looks stale", "$ pytest tests/ -q"))
    assert verdict == {"valid": True, "kind": "CHALLENGE"}


def test_handoff_with_valid_json_block_is_valid():
    text = _msg("HANDOFF: operator, please run this",
                "```json", json.dumps(VALID_HANDOFF_JSON), "```")
    verdict = mk.lint(text)
    assert verdict == {"valid": True, "kind": "HANDOFF"}


def test_first_line_kind_is_case_sensitive_and_exact():
    verdict = mk.lint(_msg("finding: lowercase is not a kind"))
    assert verdict["valid"] is False
    assert verdict["rule"] == "first-line-kind"
    assert verdict["kind"] is None


def test_claimed_and_done_are_never_valid_kinds():
    for word in ("CLAIMED", "DONE"):
        verdict = mk.lint(_msg(f"{word}: task status belongs on the task list"))
        assert verdict["valid"] is False
        assert verdict["rule"] == "first-line-kind"


# ================================================================================================ #
# evidence requirement (FINDING / CHALLENGE)
# ================================================================================================ #

@pytest.mark.parametrize("kind", ["FINDING", "CHALLENGE"])
def test_evidence_kinds_without_any_evidence_line_are_invalid(kind):
    verdict = mk.lint(_msg(f"{kind}: a bare assertion with nothing behind it"))
    assert verdict["valid"] is False
    assert verdict["rule"] == "evidence-required"
    assert verdict["kind"] == kind


@pytest.mark.parametrize("evidence_line", [
    "evidence: tests/test_message_kind.py",
    "see https://example.com/run/42",
    "scripts/foundry_message_kind.py:88",
    "$ python3 -m pytest tests/test_message_kind.py -q",
    "> 12 passed in 0.4s",
])
def test_each_evidence_shape_satisfies_the_requirement(evidence_line):
    verdict = mk.lint(_msg("FINDING: something is true", evidence_line))
    assert verdict == {"valid": True, "kind": "FINDING"}


# ================================================================================================ #
# HANDOFF json-block shape — reused from foundry_blocker_check, never re-copied
# ================================================================================================ #

def test_handoff_missing_json_block_is_invalid():
    verdict = mk.lint(_msg("HANDOFF: run this", "(no fenced block at all)"))
    assert verdict["valid"] is False
    assert verdict["kind"] == "HANDOFF"
    assert verdict["rule"] == "handoff-json-block-missing"


def test_handoff_unparseable_json_is_invalid():
    text = _msg("HANDOFF: run this", "```json", "{not valid json", "```")
    verdict = mk.lint(text)
    assert verdict["valid"] is False
    assert verdict["rule"] == "handoff-json-not-parseable"


def test_handoff_shape_reuses_blocker_check_handoff_errors():
    """The exact same objects `foundry_blocker_check` refuses (a chained command) are refused
    here too — proving reuse-by-import, not a re-copied and possibly-diverged floor."""
    bad = dict(VALID_HANDOFF_JSON, command="ls && rm -rf /tmp/x")
    text = _msg("HANDOFF: run this", "```json", json.dumps(bad), "```")
    verdict = mk.lint(text)
    assert verdict["valid"] is False
    assert verdict["rule"] == "handoff-shape"
    assert "&&" in verdict["reason"]


def test_handoff_missing_required_field_is_invalid():
    bad = {k: v for k, v in VALID_HANDOFF_JSON.items() if k != "expect"}
    text = _msg("HANDOFF: run this", "```json", json.dumps(bad), "```")
    verdict = mk.lint(text)
    assert verdict["valid"] is False
    assert verdict["rule"] == "handoff-shape"
    assert "expect" in verdict["reason"]


def test_handoff_relative_cwd_is_invalid():
    bad = dict(VALID_HANDOFF_JSON, cwd="relative/path")
    text = _msg("HANDOFF: run this", "```json", json.dumps(bad), "```")
    verdict = mk.lint(text)
    assert verdict["valid"] is False
    assert verdict["rule"] == "handoff-shape"


def test_handoff_errors_is_the_same_function_object_as_blocker_check():
    import foundry_blocker_check as bc
    assert mk._bc is bc
    assert mk._handoff_json_block_errors.__globals__["_bc"]._handoff_errors is bc._handoff_errors


# ================================================================================================ #
# CLI: exit codes 0 / 3 / 2, and JSON on stdout for the first two
# ================================================================================================ #

def test_cli_exit_0_on_valid(tmp_path):
    proc = _run_cli(_msg("NEEDS-INTERFACE: ok?"), tmp_path)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out == {"valid": True, "kind": "NEEDS-INTERFACE"}


def test_cli_exit_3_on_invalid_names_the_rule(tmp_path):
    proc = _run_cli(_msg("CHALLENGE: nothing behind this"), tmp_path)
    assert proc.returncode == 3
    out = json.loads(proc.stdout)
    assert out["valid"] is False
    assert out["rule"] == "evidence-required"


def test_cli_exit_2_on_malformed_empty_input(tmp_path):
    proc = _run_cli("", tmp_path)
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "REFUSED" in proc.stderr


def test_cli_exit_2_on_unreadable_path():
    proc = subprocess.run([sys.executable, CLI_PATH, "--in", "/nonexistent/path/message.txt"],
                           capture_output=True, text=True)
    assert proc.returncode == 2
    assert proc.stdout == ""


def test_cli_reads_stdin_with_dash(tmp_path):
    proc = _run_cli(_msg("FINDING: x", "evidence: y"), tmp_path, use_stdin=True)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["valid"] is True


# ================================================================================================ #
# registration — the new script's three registration files (AC-FIL-5)
# ================================================================================================ #

_MAP_PATHS = (
    os.path.join(REPO_ROOT, "cli", "permission-floor.json"),
    os.path.join(REPO_ROOT, "docs", "permission-floor.json"),
)


@pytest.mark.parametrize("map_path", _MAP_PATHS)
def test_script_is_tiered_allow_in_both_permission_floor_copies(map_path):
    with open(map_path, encoding="utf-8") as fh:
        doc = json.load(fh)
    matches = [e for e in doc["entries"] if "foundry_message_kind.py" in e["rule"]]
    assert matches, f"{map_path} has no entry naming foundry_message_kind.py"
    assert all(e["tier"] == "allow" for e in matches), matches
    assert all(e.get("rationale") for e in matches)


def test_both_permission_floor_copies_agree():
    with open(_MAP_PATHS[0], encoding="utf-8") as fh:
        cli_doc = json.load(fh)
    with open(_MAP_PATHS[1], encoding="utf-8") as fh:
        docs_doc = json.load(fh)
    assert cli_doc == docs_doc


def test_script_appears_in_command_position_in_command_deck_skill():
    """The closed-world truth-check (test_permission_floor_map.py's not_invoked_truth_violations)
    requires this: a script tiered `allow` must actually be reachable in command position from a
    shipped skill, or the map itself would be lying."""
    skill_path = os.path.join(REPO_ROOT, "skills", "command-deck", "SKILL.md")
    with open(skill_path, encoding="utf-8") as fh:
        text = fh.read()
    assert "foundry_message_kind.py" in text


_DRIFT_CORPUS_PATH = os.path.join(REPO_ROOT, "tests", "fixtures", "floor-drift-corpus.json")


@pytest.mark.parametrize("case_name", ["full-floor-verbatim", "full-floor-home-expanded"])
def test_drift_corpus_full_floor_cases_carry_the_new_allow_row(case_name):
    with open(_DRIFT_CORPUS_PATH, encoding="utf-8") as fh:
        corpus = json.load(fh)
    case = next(c for c in corpus["cases"] if c["name"] == case_name)
    rules = [r["rule"] for r in case["effective"]["allow"]]
    matches = [r for r in rules if "foundry_message_kind.py" in r]
    assert matches, f"{case_name} effective.allow has no foundry_message_kind.py row"


def test_merge_base_entries_digest_matches_docs_permission_floor():
    """R8's own rule, restated as a test: the digest pinned in test_permission_floor_check.py
    must match the map this atom shipped, in the SAME diff."""
    import hashlib

    perm = load_module("scripts/foundry_permission_floor.py", "foundry_permission_floor")
    del perm  # imported only to prove the module still loads cleanly alongside the digest check

    with open(os.path.join(REPO_ROOT, "docs", "permission-floor.json"), encoding="utf-8") as fh:
        doc = json.load(fh)
    canon = json.dumps(doc["entries"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()

    check_src = os.path.join(REPO_ROOT, "tests", "test_permission_floor_check.py")
    with open(check_src, encoding="utf-8") as fh:
        text = fh.read()
    import re as _re
    m = _re.search(r'MERGE_BASE_ENTRIES_DIGEST = "([0-9a-f]+)"', text)
    assert m, "MERGE_BASE_ENTRIES_DIGEST not found in tests/test_permission_floor_check.py"
    assert digest == m.group(1), (
        "docs/permission-floor.json `entries` digest does not match the pinned "
        "MERGE_BASE_ENTRIES_DIGEST — re-pin it in the same diff (R8)"
    )
