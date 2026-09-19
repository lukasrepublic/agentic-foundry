"""tests/test_autonomy_instrument.py — feat yield-and-silent-yield-instrument, AC-INS-1..6.

Drives `scripts/foundry-autonomy-instrument.py` directly over the throwaway fixtures under
`tests/fixtures/autonomy-instrument/` (per CONTRIBUTING.md's testing convention: import the
shipped module, assert on its COMPUTED output — never a standalone selftest CLI). One subprocess
test covers `--help` and the `--json` CLI surface end to end.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from conftest import REPO_ROOT, load_module

frm = load_module("scripts/foundry-autonomy-instrument.py", "foundry_autonomy_instrument")

FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures", "autonomy-instrument")
FAR_PAST = "2000-01-01"
FAR_FUTURE = "2099-01-01"
SCRIPT = os.path.join(REPO_ROOT, "scripts", "foundry-autonomy-instrument.py")


# --------------------------------------------------------------------------------------------- #
# AC-INS-3 — the validated operator-turn discriminator, copied verbatim
# --------------------------------------------------------------------------------------------- #


def test_is_operator_turn_human_origin_true():
    assert frm.is_operator_turn({"type": "user", "origin": {"kind": "human"}}) is True


def test_is_operator_turn_tool_result_echo_false():
    # a tool_result lands in a "user"-type record but is never a human origin
    r = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}}
    assert frm.is_operator_turn(r) is False


def test_is_operator_turn_sidechain_excluded():
    assert frm.is_operator_turn({"type": "user", "isSidechain": True, "origin": {"kind": "human"}}) is False


def test_is_operator_turn_legacy_prompt_source_fallback():
    assert frm.is_operator_turn({"type": "user", "origin": None, "promptSource": "typed"}) is True
    assert frm.is_operator_turn({"type": "user", "origin": None, "promptSource": "something-else"}) is False


# --------------------------------------------------------------------------------------------- #
# AC-INS-4 — reproduce the known 0.81 +/- 0.01 silent-yield fixture
# --------------------------------------------------------------------------------------------- #


def test_known_081_fixture_reproduces_silent_yield_rate():
    report = frm.build_report(FAR_PAST, [os.path.join(FIXTURES, "known-081")], repo=None)
    ratio = report["ratios"]["silent_yield"]
    assert ratio["denominator"] == 100
    assert ratio["numerator"] == 81
    assert abs(ratio["ratio"] - 0.81) <= 0.01


# --------------------------------------------------------------------------------------------- #
# AC-INS-3 — a session whose agent yields silently 4 of 5 handoffs reports 0.8
# --------------------------------------------------------------------------------------------- #


def test_silent_4_of_5_reports_point_eight():
    report = frm.build_report(FAR_PAST, [os.path.join(FIXTURES, "silent-4-of-5")], repo=None)
    ratio = report["ratios"]["silent_yield"]
    assert ratio == {"numerator": 4, "denominator": 5, "ratio": 0.8}


# --------------------------------------------------------------------------------------------- #
# §0 lie #2 — a rhetorical self-question followed by a tool call must NOT count as a question
# (and must not be the evaluated stop at all)
# --------------------------------------------------------------------------------------------- #


def test_rhetorical_question_with_tool_call_is_not_a_question():
    path = os.path.join(FIXTURES, "rhetorical-question-tool-call", "session.jsonl")
    result = frm.mine_file(path)
    # exactly one real stop (the final, tool-free, non-question assistant turn) — the rhetorical
    # question-plus-tool_use turn is never evaluated as a stop.
    assert len(result["stops"]) == 1
    assert result["stops"][0]["silent"] is True


def test_rhetorical_question_fixture_end_to_end_ratio_is_one():
    report = frm.build_report(FAR_PAST, [os.path.join(FIXTURES, "rhetorical-question-tool-call")], repo=None)
    assert report["ratios"]["silent_yield"] == {"numerator": 1, "denominator": 1, "ratio": 1.0}


# --------------------------------------------------------------------------------------------- #
# §0 lie #1 — the word "block" in a tool_result must NOT count as a denial (blockchain-shaped
# text), while a real, phrase-shaped denial elsewhere in the same session must.
# --------------------------------------------------------------------------------------------- #


def test_block_word_alone_is_not_a_denial():
    path = os.path.join(FIXTURES, "block-not-denial", "session.jsonl")
    result = frm.mine_file(path)
    assert len(result["denials"]) == 1
    assert "classifier" in result["denials"][0]["text"].lower()
    assert "processing block" not in json.dumps(result["denials"]).lower()


def test_deny_regex_has_no_bare_block_alternative():
    assert not frm.DENY.search("Processing block 454393353 of 454400000")
    assert frm.DENY.search("auto mode classifier blocked this command")


# --------------------------------------------------------------------------------------------- #
# AC-INS-1 — directive-reply: operator replies <=45 chars to an agent stop
# --------------------------------------------------------------------------------------------- #


def test_directive_reply_ratio():
    report = frm.build_report(FAR_PAST, [os.path.join(FIXTURES, "directive-reply")], repo=None)
    ratio = report["ratios"]["directive_reply"]
    assert ratio == {"numerator": 2, "denominator": 3, "ratio": round(2 / 3, 4)}


# --------------------------------------------------------------------------------------------- #
# AC-INS-1 — granted-verb-denial: a classifier block on a verb already in the settings allow list
# --------------------------------------------------------------------------------------------- #


def test_granted_verb_denial_ratio():
    report = frm.build_report(
        FAR_PAST,
        [os.path.join(FIXTURES, "granted-verb-denial")],
        repo=os.path.join(FIXTURES, "repo-sample"),
    )
    ratio = report["ratios"]["granted_verb_denial"]
    # 2 denials found (git push, tofu apply); only "git" is in the sample settings allow list.
    assert ratio == {"numerator": 1, "denominator": 2, "ratio": 0.5}


# --------------------------------------------------------------------------------------------- #
# AC-INS-1 — guard-false-positive: SAMPLED, never auto-classified
# --------------------------------------------------------------------------------------------- #


def test_guard_false_positive_is_a_sample_not_a_classification():
    report = frm.build_report(
        FAR_PAST, [os.path.join(FIXTURES, "granted-verb-denial")], repo=None, sample_size=1,
    )
    gfp = report["ratios"]["guard_false_positive_sample"]
    assert gfp["ratio"] is None  # never auto-classified
    assert gfp["numerator"] is None
    assert gfp["denials_total"] == 2
    assert gfp["denominator"] == 1  # capped by sample_size
    assert len(gfp["sample"]) == 1
    assert "text" in gfp["sample"][0] and "file" in gfp["sample"][0]


# --------------------------------------------------------------------------------------------- #
# AC-INS-1 — authorized->built conversion + rounds-per-shipped-atom (--repo)
# --------------------------------------------------------------------------------------------- #


def test_authorized_to_built_conversion():
    report = frm.build_report(FAR_PAST, [], repo=os.path.join(FIXTURES, "repo-sample"))
    ratio = report["ratios"]["authorized_to_built"]
    # "bar" carries a real spec_sha256 and has a build-provenance.yaml entry -> built.
    # "baz" carries the all-zero placeholder spec_sha256 -> not counted as authorized.
    assert ratio == {"numerator": 1, "denominator": 1, "ratio": 1.0}


def test_rounds_per_shipped_atom():
    report = frm.build_report(FAR_PAST, [], repo=os.path.join(FIXTURES, "repo-sample"))
    ratio = report["ratios"]["rounds_per_shipped_atom"]
    # audit-ledger.jsonl carries rounds 2 + 3 + 1 = 6, over 1 shipped atom.
    assert ratio == {"numerator": 6, "denominator": 1, "ratio": 6.0}


def test_placeholder_sha_is_not_authorized():
    assert frm.scan_specs_authorized(os.path.join(FIXTURES, "repo-sample")) == 1


# --------------------------------------------------------------------------------------------- #
# AC-INS-2 — --json emits one JSON object with the six ratios + numerators/denominators + window
# --------------------------------------------------------------------------------------------- #


def test_json_output_shape_has_all_six_ratios_and_corpus_window():
    report = frm.build_report(FAR_PAST, [os.path.join(FIXTURES, "known-081")], repo=os.path.join(FIXTURES, "repo-sample"))
    assert set(report["ratios"]) == {
        "silent_yield", "directive_reply", "granted_verb_denial",
        "guard_false_positive_sample", "authorized_to_built", "rounds_per_shipped_atom",
    }
    assert report["corpus"]["since"] == FAR_PAST
    assert "until" in report["corpus"]
    for name in ("silent_yield", "directive_reply", "granted_verb_denial", "authorized_to_built", "rounds_per_shipped_atom"):
        r = report["ratios"][name]
        assert "numerator" in r and "denominator" in r and "ratio" in r


# --------------------------------------------------------------------------------------------- #
# AC-INS-5 — gates nothing: exit 0 regardless of the computed result, no .foundry/ write unless
# --out is given
# --------------------------------------------------------------------------------------------- #


def test_empty_corpus_still_exits_zero(tmp_path):
    report = frm.build_report(FAR_FUTURE, [str(tmp_path)], repo=None)
    assert report["ratios"]["silent_yield"]["denominator"] == 0
    assert report["ratios"]["silent_yield"]["ratio"] is None


def test_cli_help_exits_zero():
    proc = subprocess.run([sys.executable, SCRIPT, "--help"], capture_output=True, text=True)
    assert proc.returncode == 0
    assert "--projects-dir" in proc.stdout


def test_cli_json_end_to_end_exits_zero_and_writes_no_foundry_dir(tmp_path):
    out_path = tmp_path / "report.json"
    proc = subprocess.run(
        [
            sys.executable, SCRIPT,
            "--since", FAR_PAST,
            "--projects-dir", os.path.join(FIXTURES, "known-081"),
            "--repo", os.path.join(FIXTURES, "repo-sample"),
            "--json",
            "--out", str(out_path),
        ],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["ratios"]["silent_yield"]["numerator"] == 81
    assert out_path.is_file()
    assert not (tmp_path / ".foundry").exists()


def test_cli_never_gates_on_any_computed_result():
    # a corpus with zero denials, zero stops, no --repo: every ratio degrades to None, exit 0.
    proc = subprocess.run(
        [sys.executable, SCRIPT, "--since", FAR_FUTURE, "--projects-dir", FIXTURES],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
