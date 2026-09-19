"""tests/test_routine_wake.py — routine-wake (autonomy-continuation R3, AC-RWK-1..3).

Covers `scripts/foundry-routine-wake-prompt.py`'s rendered prompt (the deck name, the programme,
the UTC stamp shape it instructs the Routine to compute at send time, and the single-message
rule), its slug validation for both `<programme>` and `--deck-name`, and the new script's three
registration files (both `permission-floor.json` copies, the `floor-drift-corpus.json` full-floor
fixture rows, and command-position reachability from `skills/command-deck/SKILL.md`) — so a new
invocable script cannot silently fall outside the closed-world permission floor. The fifth message
kind (`TICK`) the lint gained for this atom is covered in `tests/test_message_kind.py`, imported
here by reference only (not re-tested) to avoid duplicating that coverage.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from conftest import REPO_ROOT, load_module

rwp = load_module("scripts/foundry-routine-wake-prompt.py", "foundry_routine_wake_prompt")

CLI_PATH = os.path.join(REPO_ROOT, "scripts", "foundry-routine-wake-prompt.py")


def _run_cli(*args):
    return subprocess.run([sys.executable, CLI_PATH, *args], capture_output=True, text=True)


# ================================================================================================ #
# AC-RWK-1 — the rendered prompt
# ================================================================================================ #

def test_render_names_the_deck_and_the_programme():
    out = rwp.render("autonomy-continuation", "my-deck")
    assert "my-deck" in out
    assert "autonomy-continuation" in out


def test_render_instructs_listing_agents_first():
    out = rwp.render("autonomy-continuation", "my-deck")
    assert "ListAgents" in out


def test_render_instructs_the_absent_or_offline_failure_report():
    out = rwp.render("autonomy-continuation", "my-deck")
    assert "absent or offline" in out
    assert "TICK not delivered" in out


def test_render_instructs_the_tick_message_shape_with_a_computed_stamp():
    out = rwp.render("autonomy-continuation", "my-deck")
    assert "TICK autonomy-continuation" in out
    assert "YYYY-MM-DDTHH:MM:SSZ" in out
    assert "compute" in out.lower()


def test_render_states_the_single_message_rule():
    out = rwp.render("autonomy-continuation", "my-deck")
    assert "exactly one" in out.lower() or "exactly ONE" in out


def test_render_states_a_message_is_never_consent():
    out = rwp.render("autonomy-continuation", "my-deck")
    assert "never consent" in out.lower()


def test_render_includes_the_schedule_recipe():
    out = rwp.render("autonomy-continuation", "my-deck")
    assert "/schedule" in out
    assert "Cadence" in out
    assert "Repository" in out
    assert "Environment" in out
    assert "Connectors" in out


def test_render_includes_the_prerequisites():
    out = rwp.render("autonomy-continuation", "my-deck")
    assert "Prerequisites" in out
    assert "--name my-deck" in out
    assert "Remote Control" in out
    assert "crossSessionInbound" in out
    assert "refuse" in out


# ================================================================================================ #
# slug validation — AC-RWK-1's "refuse otherwise, exit 2"
# ================================================================================================ #

@pytest.mark.parametrize("programme,deck_name", [
    ("autonomy-continuation", "my-deck"),
    ("a", "b"),
    ("ac-r3-native-swarm-substrate", "deck-2"),
])
def test_valid_slugs_render_and_exit_0(programme, deck_name):
    proc = _run_cli(programme, "--deck-name", deck_name)
    assert proc.returncode == 0, proc.stderr
    assert deck_name in proc.stdout
    assert programme in proc.stdout


@pytest.mark.parametrize("bad_programme", [
    "Bad_Name", "has spaces", "has/slash", "UPPER", "",
])
def test_bad_programme_slug_is_refused_exit_2(bad_programme):
    proc = _run_cli(bad_programme, "--deck-name", "my-deck")
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "REFUSED" in proc.stderr


@pytest.mark.parametrize("bad_deck_name", [
    "Bad_Name", "has spaces", "has/slash", "UPPER",
])
def test_bad_deck_name_slug_is_refused_exit_2(bad_deck_name):
    proc = _run_cli("autonomy-continuation", "--deck-name", bad_deck_name)
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "REFUSED" in proc.stderr


def test_missing_deck_name_flag_is_an_argparse_error():
    proc = _run_cli("autonomy-continuation")
    assert proc.returncode != 0
    assert proc.stdout == ""


def test_render_is_pure_and_deterministic():
    a = rwp.render("autonomy-continuation", "my-deck")
    b = rwp.render("autonomy-continuation", "my-deck")
    assert a == b


# ================================================================================================ #
# registration — the new script's three registration files
# ================================================================================================ #

_MAP_PATHS = (
    os.path.join(REPO_ROOT, "cli", "permission-floor.json"),
    os.path.join(REPO_ROOT, "docs", "permission-floor.json"),
)


@pytest.mark.parametrize("map_path", _MAP_PATHS)
def test_script_is_tiered_allow_in_both_permission_floor_copies(map_path):
    with open(map_path, encoding="utf-8") as fh:
        doc = json.load(fh)
    matches = [e for e in doc["entries"] if "foundry-routine-wake-prompt.py" in e["rule"]]
    assert matches, f"{map_path} has no entry naming foundry-routine-wake-prompt.py"
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
    assert "foundry-routine-wake-prompt.py" in text


_DRIFT_CORPUS_PATH = os.path.join(REPO_ROOT, "tests", "fixtures", "floor-drift-corpus.json")


@pytest.mark.parametrize("case_name", ["full-floor-verbatim", "full-floor-home-expanded"])
def test_drift_corpus_full_floor_cases_carry_the_new_allow_row(case_name):
    with open(_DRIFT_CORPUS_PATH, encoding="utf-8") as fh:
        corpus = json.load(fh)
    case = next(c for c in corpus["cases"] if c["name"] == case_name)
    rules = [r["rule"] for r in case["effective"]["allow"]]
    matches = [r for r in rules if "foundry-routine-wake-prompt.py" in r]
    assert matches, f"{case_name} effective.allow has no foundry-routine-wake-prompt.py row"


def test_merge_base_entries_digest_matches_docs_permission_floor():
    """R8's own rule, restated as a test: the digest pinned in test_permission_floor_check.py
    must match the map this atom shipped, in the SAME diff."""
    import hashlib
    import re as _re

    with open(os.path.join(REPO_ROOT, "docs", "permission-floor.json"), encoding="utf-8") as fh:
        doc = json.load(fh)
    canon = json.dumps(doc["entries"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()

    check_src = os.path.join(REPO_ROOT, "tests", "test_permission_floor_check.py")
    with open(check_src, encoding="utf-8") as fh:
        text = fh.read()
    m = _re.search(r'MERGE_BASE_ENTRIES_DIGEST = "([0-9a-f]+)"', text)
    assert m, "MERGE_BASE_ENTRIES_DIGEST not found in tests/test_permission_floor_check.py"
    assert digest == m.group(1), (
        "docs/permission-floor.json `entries` digest does not match the pinned "
        "MERGE_BASE_ENTRIES_DIGEST — re-pin it in the same diff (R8)"
    )


# ================================================================================================ #
# self-contained — no corpus/state read (charter: "reads no corpus state and writes nothing")
# ================================================================================================ #

def test_module_reads_no_release_or_filesystem_state(tmp_path, monkeypatch):
    """Run from a directory with no `.foundry/` at all — the rendered prompt must not care."""
    monkeypatch.chdir(tmp_path)
    proc = _run_cli("autonomy-continuation", "--deck-name", "my-deck")
    assert proc.returncode == 0, proc.stderr
    assert "autonomy-continuation" in proc.stdout


def test_render_names_the_send_tool_for_the_single_message():
    out = _render("ac-r3", "deck-main") if "_render" in globals() else None
    if out is None:
        import subprocess, sys
        out = subprocess.run([sys.executable, SCRIPT, "ac-r3", "--deck-name", "deck-main"],
                             capture_output=True, text=True).stdout
    assert "SendMessage" in out and "exactly ONE" in out
