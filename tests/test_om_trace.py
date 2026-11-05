"""gv-16 / AC-OM-10: the operating-model traceability table maps every port-prompt rule (A1..G3) to at least one acceptance
criterion in the programme's charters, and fails on an unmapped rule.

The charters live in the private workspace, not in this repository, so the tests build charter fixtures in `tmp_path`
from the script's own rule list: a full set (every rule mapped), the same set with one rule removed (the script and this test
must fail naming it), and edge cases for the id syntax. No network.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "foundry_om_trace.py"
sys.path.insert(0, str(ROOT / "scripts"))
import foundry_om_trace as om  # noqa: E402


def charter(goal: str, acs: list, name="x") -> str:
    body = "\n".join(f"- {a}" for a in acs)
    return f"# Charter {name}\n\n## Goal\n{goal}\n\n## Acceptance criteria\n{body}\n\n## Done when\n- test:t\n"


def write_full(d: Path, skip=()):
    """One charter per letter group, naming each rule in an AC bullet (AC-OM-n ids where useful)."""
    d.mkdir(parents=True, exist_ok=True)
    groups = {}
    for rid in om.RULE_IDS:
        if rid not in skip:
            groups.setdefault(rid[0], []).append(rid)
    for i, (letter, ids) in enumerate(sorted(groups.items()), 1):
        acs = [f"AC-OM-{i} WHEN a thing happens, THE SYSTEM SHALL enforce rule {rid}." for rid in ids]
        (d / f"gv-{i:02d}-{letter.lower()}.md").write_text(charter("Some goal.", acs, letter))
    return d


def run(charters, *extra):
    return subprocess.run([sys.executable, str(SCRIPT), "--charters", str(charters), *extra], capture_output=True, text=True, timeout=60)


def test_the_embedded_list_is_the_32_rules_a1_to_g3():
    ids = om.RULE_IDS
    assert len(ids) == len(set(ids)) == 32
    assert ids[0] == "A1" and ids[-1] == "G3"
    sizes = {l: sum(1 for r in ids if r[0] == l) for l in "ABCDEFG"}
    assert sizes == {"A": 6, "B": 10, "C": 3, "D": 3, "E": 3, "F": 4, "G": 3}


def test_every_rule_mapped_exits_0_and_the_table_has_a_row_per_rule(tmp_path):
    d = write_full(tmp_path / "ch")
    p = run(d, "--json")
    assert p.returncode == 0, p.stdout + p.stderr
    doc = json.loads(p.stdout)
    assert doc["unmapped"] == []
    assert [r["rule"] for r in doc["rules"]] == list(om.RULE_IDS)
    assert all(r["maps"] and r["maps"][0]["via"] == "ac" and r["maps"][0]["ac"].startswith("AC-OM-") for r in doc["rules"])
    table = run(d).stdout
    assert table.splitlines()[0].startswith("| Rule | Title |") and "all 32 rules map" in table


@pytest.mark.parametrize("rule", ["A1", "B7", "B10", "C2", "D3", "G2", "G3"])
def test_an_unmapped_rule_fails_naming_it(tmp_path, rule):
    d = write_full(tmp_path / "ch", skip=(rule,))
    p = run(d, "--json")
    assert p.returncode == 1
    assert json.loads(p.stdout)["unmapped"] == [rule]
    assert f"unmapped rule(s): {rule}" in run(d).stdout
    assert f"| {rule} |" in run(d).stdout and "UNMAPPED" in run(d).stdout


def test_ranges_and_lists_expand_within_one_letter(tmp_path):
    d = tmp_path / "ch"
    d.mkdir()
    (d / "gv-01.md").write_text(charter("Some goal.", ["AC-OM-1 WHEN x THE SYSTEM SHALL cover rules A1–A4 and B2, F1-F2, G1-3."]))
    got = {r: [m["charter"] for m in rows] for r, rows in ((x["rule"], x["maps"]) for x in om.trace(d)["rules"]) if rows}
    assert set(got) == {"A1", "A2", "A3", "A4", "B2", "F1", "F2", "G1", "G2", "G3"}


def test_a_cross_letter_span_is_not_a_range_and_maps_nothing(tmp_path):
    d = tmp_path / "ch"
    d.mkdir()
    (d / "gv-01.md").write_text(charter("The table maps every rule (A1..G3).", ["AC-OM-1 mapping every port-prompt rule (A1..G3)."]))
    assert om.trace(d)["unmapped"] == list(om.RULE_IDS)


def test_ids_inside_longer_words_are_not_rules(tmp_path):
    d = tmp_path / "ch"
    d.mkdir()
    (d / "gv-01.md").write_text(charter("An E2E test, the A1B2 token and the C3D build.", ["AC-OM-1 the E2E path."]))
    assert om.trace(d)["unmapped"] == list(om.RULE_IDS)


def test_a_goal_mention_is_not_a_mapping(tmp_path):
    d = tmp_path / "ch"
    d.mkdir()
    (d / "gv-01.md").write_text(charter("Rule G2 of the port prompt.", ["AC-OM-1 WHEN x THE SYSTEM SHALL y."]))
    assert "G2" in om.trace(d)["unmapped"]                       # the Goal names it; no acceptance criterion checks it
    (d / "gv-01.md").write_text(charter("Rule G2 of the port prompt.", ["AC-OM-1 WHEN x THE SYSTEM SHALL record G2."]))
    assert "G2" not in om.trace(d)["unmapped"]                   # control: the same rule inside an AC bullet maps


def test_a_spaced_hyphen_is_not_a_range(tmp_path):
    d = tmp_path / "ch"
    d.mkdir()
    (d / "gv-01.md").write_text(charter("g", ["AC-OM-1 WHEN x THE SYSTEM SHALL honour D3 - 4 PRs per tick."]))
    mapped = {r["rule"] for r in om.trace(d)["rules"] if r["maps"]}
    assert mapped == {"D3"}                                      # not D3..D4 and not 4 anything
    (d / "gv-01.md").write_text(charter("g", ["AC-OM-1 WHEN x THE SYSTEM SHALL honour D1 – D3 together."]))   # a spaced en dash that repeats the letter is one
    assert {r["rule"] for r in om.trace(d)["rules"] if r["maps"]} == {"D1", "D2", "D3"}


def test_an_amendment_row_with_a_new_ac_maps_the_rule_it_adds(tmp_path):
    d = tmp_path / "ch"
    d.mkdir()
    text = charter("g", ["AC-OM-1 WHEN x THE SYSTEM SHALL y."]) + (
        "\n## Amendments\n| date | what changed | why | auth_seq |\n|---|---|---|---|\n"
        "| 2026-11-05 | goal adds rule B7 (token discipline). New AC: WHEN rendered, THE SYSTEM SHALL include the hints. | found | 1 |\n"
        "| 2026-11-06 | goal mentions rule C1 and nothing else | why | 2 |\n")
    (d / "gv-01.md").write_text(text)
    t = om.trace(d)
    b7 = next(r for r in t["rules"] if r["rule"] == "B7")["maps"]
    assert b7 == [{"charter": "gv-01", "via": "ac", "ac": "Amendment 2026-11-05 New AC"}]
    assert "C1" in t["unmapped"]                                 # a row without `New AC:` maps nothing


# The CURRENT charters, copied (the retired-token line of gv-01 reworded). The deck runs the trace over the workspace's own
# charters directory; this copy keeps the repository's test self-contained. These rules still have no acceptance criterion in
# any charter: the run exits 1 naming them, which is the point of the tool. When a charter gains the AC, remove the rule here.
REAL = ROOT / "tests" / "fixtures" / "om-trace" / "charters"
KNOWN_UNMAPPED_IN_REAL_CHARTERS = {"A2", "A3", "A4", "B2", "B3", "B4", "B5", "B6", "B10", "C2", "C3", "D1", "D3", "E3", "F1", "F2",
                                   "F3", "F4", "G1", "G2", "G3"}


def test_the_trace_over_a_copy_of_the_real_charters():
    p = run(REAL, "--json")
    doc = json.loads(p.stdout)
    assert p.returncode == 1
    assert [r["rule"] for r in doc["rules"]] == list(om.RULE_IDS)
    assert set(doc["unmapped"]) == KNOWN_UNMAPPED_IN_REAL_CHARTERS
    b7 = next(r for r in doc["rules"] if r["rule"] == "B7")["maps"]
    assert b7 and b7[0]["charter"].startswith("gv-13") and b7[0]["ac"].startswith("Amendment")          # the New AC row
    assert len(doc["charters"]) == 19


def test_a_charter_without_acceptance_criteria_maps_nothing(tmp_path):
    d = tmp_path / "ch"
    d.mkdir()
    (d / "gv-01.md").write_text("# C\n\n## Goal\nRule G2.\n\n## Done when\n- test:t\n")
    assert "G2" in om.trace(d)["unmapped"]


def test_prompt_drift_and_bad_input_are_exit_2(tmp_path):
    d = write_full(tmp_path / "ch")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("\n".join(f"{r}. Title [TUNABLE]" for r in om.RULE_IDS if r != "B7"))
    p = run(d, "--prompt", str(prompt))
    assert p.returncode == 2 and "prompt-drift" in p.stderr and "B7" in p.stderr
    prompt.write_text("\n".join(f"{r}. Title [TUNABLE]" for r in om.RULE_IDS))
    assert run(d, "--prompt", str(prompt)).returncode == 0                               # control: same list
    assert run(tmp_path / "empty-nowhere").returncode == 2
    (tmp_path / "none").mkdir()
    assert run(tmp_path / "none").returncode == 2


def test_the_script_is_read_only(tmp_path):
    d = write_full(tmp_path / "ch")
    before = sorted((p.name, p.stat().st_mtime_ns) for p in d.iterdir())
    run(d)
    assert sorted((p.name, p.stat().st_mtime_ns) for p in d.iterdir()) == before
    assert [p.name for p in tmp_path.iterdir()] == ["ch"]
