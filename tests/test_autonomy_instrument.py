"""tests/test_autonomy_instrument.py — feat yield-and-silent-yield-instrument, AC-INS-1..6.

Drives `scripts/foundry-autonomy-instrument.py` directly over the throwaway fixtures under
`tests/fixtures/autonomy-instrument/` (per CONTRIBUTING.md's testing convention: import the
shipped module, assert on its COMPUTED output — never a standalone selftest CLI). One subprocess
test covers `--help` and the `--json` CLI surface end to end.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

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
# AC-INS-1 — granted-verb-denial: a classifier block on a command already covered by the
# settings allow list, by LONGEST-PREFIX match (round-2 review fix — never a first-token match)
# --------------------------------------------------------------------------------------------- #


def test_granted_verb_denial_ratio():
    report = frm.build_report(
        FAR_PAST,
        [os.path.join(FIXTURES, "granted-verb-denial")],
        repo=os.path.join(FIXTURES, "repo-sample"),
    )
    ratio = report["ratios"]["granted_verb_denial"]
    # 2 denials found (git push, tofu apply); "Bash(git push:*)" is in the sample settings allow
    # list and covers "git push origin main" by prefix; "tofu apply" has no covering rule.
    assert ratio == {"numerator": 1, "denominator": 2, "ratio": 0.5}


def test_granted_verb_denial_uses_longest_prefix_not_first_token():
    # RISK regression: a first-token ("verb") match would incorrectly read "Bash(git commit:*)"
    # as granting ANY git subcommand, including the denied "git push origin main" in this same
    # fixture. The longest-prefix match must compare the full command, not just "git".
    report = frm.build_report(
        FAR_PAST,
        [os.path.join(FIXTURES, "granted-verb-denial")],
        repo=os.path.join(FIXTURES, "repo-sample-verb-prefix"),  # allow: only "Bash(git commit:*)"
    )
    ratio = report["ratios"]["granted_verb_denial"]
    assert ratio == {"numerator": 0, "denominator": 2, "ratio": 0.0}


def test_bash_command_granted_prefix_boundary_is_word_aware():
    allow = ["Bash(git commit:*)"]
    # "git push" must not be granted merely because "git" is a shared prefix word of "git commit"
    assert frm.is_granted("Bash", "git push origin main", allow) is False
    assert frm.is_granted("Bash", "git commit -m msg", allow) is True
    # a rule prefix must not match a command that merely SHARES CHARACTERS beyond a word boundary
    # (e.g. "git commitment-check" must not be granted by "Bash(git commit:*)")
    assert frm.is_granted("Bash", "git commitment-check", allow) is False


# --------------------------------------------------------------------------------------------- #
# AC-INS-1 — guard-false-positive: SAMPLED, never auto-classified
# RISK (round-2 review): excerpts can carry secrets — opt-in via --with-excerpts, default off.
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
    assert "file" in gfp["sample"][0] and "record_index" in gfp["sample"][0]


def test_with_excerpts_default_off_reports_counts_and_references_only():
    report = frm.build_report(
        FAR_PAST, [os.path.join(FIXTURES, "granted-verb-denial")], repo=None, sample_size=1,
    )
    gfp = report["ratios"]["guard_false_positive_sample"]
    assert gfp["with_excerpts"] is False
    # no "text" key at all when excerpts are off — counts + file/turn references only
    assert "text" not in gfp["sample"][0]
    assert set(gfp["sample"][0]) == {"file", "record_index", "tool_name", "verb"}


def test_with_excerpts_on_includes_a_redacted_text_field():
    report = frm.build_report(
        FAR_PAST, [os.path.join(FIXTURES, "granted-verb-denial")], repo=None,
        sample_size=1, with_excerpts=True,
    )
    gfp = report["ratios"]["guard_false_positive_sample"]
    assert gfp["with_excerpts"] is True
    assert "text" in gfp["sample"][0]


def test_redact_scrubs_akia_key_and_bearer_token_before_ex():
    akia = "AKIAABCDEFGHIJKLMNOP"
    bearer = "sk-test-abcdefghijklmnopqrstuvwxyz0123456789"
    text = f"Authorization: Bearer {bearer} and access key {akia} were present"
    redacted = frm.redact(text)
    assert akia not in redacted
    assert bearer not in redacted
    assert "[REDACTED]" in redacted


def test_akia_and_bearer_token_never_appear_in_json_output_with_excerpts_on():
    # AC: "a fake AKIA... and a long bearer token do not appear in --json output" — end to end,
    # through the CLI, with --with-excerpts on (the only mode that surfaces excerpt text at all).
    akia = "AKIAABCDEFGHIJKLMNOP"
    bearer = "sk-test-abcdefghijklmnopqrstuvwxyz0123456789"
    proc = subprocess.run(
        [
            sys.executable, SCRIPT,
            "--since", FAR_PAST,
            "--projects-dir", os.path.join(FIXTURES, "secret-in-denial"),
            "--json", "--with-excerpts",
        ],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    assert akia not in proc.stdout
    assert bearer not in proc.stdout
    payload = json.loads(proc.stdout)
    sample_text = json.dumps(payload["ratios"]["guard_false_positive_sample"]["sample"])
    assert "[REDACTED]" in sample_text


def test_akia_and_bearer_token_never_appear_in_json_output_with_excerpts_off():
    akia = "AKIAABCDEFGHIJKLMNOP"
    bearer = "sk-test-abcdefghijklmnopqrstuvwxyz0123456789"
    proc = subprocess.run(
        [
            sys.executable, SCRIPT,
            "--since", FAR_PAST,
            "--projects-dir", os.path.join(FIXTURES, "secret-in-denial"),
            "--json",
        ],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    assert akia not in proc.stdout
    assert bearer not in proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["ratios"]["guard_false_positive_sample"]["with_excerpts"] is False
    for entry in payload["ratios"]["guard_false_positive_sample"]["sample"]:
        assert "text" not in entry


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


def test_authorized_to_built_is_the_intersection_a_provenance_marker_for_an_unauthorized_spec_does_not_count():
    # RISK (round-2 review): a provenance marker exists for BOTH "bar" (authorized: real
    # spec_sha256) and "baz" (placeholder spec_sha256, never authorized). The ratio must be
    # computed over the INTERSECTION of {built} and {authorized}, divided by |authorized| — the
    # baz marker must not inflate the numerator past the authorized count.
    report = frm.build_report(
        FAR_PAST, [], repo=os.path.join(FIXTURES, "repo-sample-unauthorized-marker"),
    )
    ratio = report["ratios"]["authorized_to_built"]
    assert ratio == {"numerator": 1, "denominator": 1, "ratio": 1.0}


def test_scan_provenance_includes_the_unauthorized_marker_but_the_ratio_excludes_it():
    repo = os.path.join(FIXTURES, "repo-sample-unauthorized-marker")
    built_refs = frm.scan_provenance_spec_refs(repo)
    authorized_refs = frm.scan_specs_authorized_refs(repo)
    # the built side legitimately contains both (a marker was written for each) ...
    assert built_refs == {
        "specs/features/foo/bar/feat-foo-bar.md",
        "specs/features/foo/baz/feat-foo-baz.md",
    }
    # ... but only "bar" was ever authorized, so only "bar" belongs in the intersection.
    assert authorized_refs == {"specs/features/foo/bar/feat-foo-bar.md"}
    assert built_refs & authorized_refs == {"specs/features/foo/bar/feat-foo-bar.md"}


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


# --------------------------------------------------------------------------------------------- #
# RISK (round-2 review) — --out must refuse a path that resolves outside the cwd or --repo
# --------------------------------------------------------------------------------------------- #


def test_validate_out_path_inside_cwd_is_accepted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resolved = frm.validate_out_path("report.json", None)
    assert resolved == os.path.realpath(str(tmp_path / "report.json"))


def test_validate_out_path_outside_cwd_and_no_repo_is_refused(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(frm.OutPathError):
        frm.validate_out_path("/etc/passwd", None)


def test_validate_out_path_inside_repo_but_outside_cwd_is_accepted(tmp_path, monkeypatch):
    cwd_dir = tmp_path / "cwd"
    repo_dir = tmp_path / "repo"
    cwd_dir.mkdir()
    repo_dir.mkdir()
    monkeypatch.chdir(cwd_dir)
    resolved = frm.validate_out_path(str(repo_dir / "report.json"), str(repo_dir))
    assert resolved == os.path.realpath(str(repo_dir / "report.json"))


def test_cli_refuses_out_path_outside_cwd_with_nonzero_exit_and_clear_message(tmp_path):
    outside = tmp_path.parent / f"escaped-{tmp_path.name}.json"
    proc = subprocess.run(
        [
            sys.executable, SCRIPT,
            "--since", FAR_PAST,
            "--projects-dir", os.path.join(FIXTURES, "known-081"),
            "--out", str(outside),
        ],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert proc.returncode != 0
    assert "outside" in proc.stderr.lower()
    assert not outside.exists()


def test_cli_accepts_out_path_inside_cwd(tmp_path):
    out_path = tmp_path / "nested" / "report.json"
    out_path.parent.mkdir()
    proc = subprocess.run(
        [
            sys.executable, SCRIPT,
            "--since", FAR_PAST,
            "--projects-dir", os.path.join(FIXTURES, "known-081"),
            "--out", str(out_path),
        ],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert proc.returncode == 0
    assert out_path.is_file()


# --------------------------------------------------------------------------------------------- #
# AC-CBR-1 — the three-way turn-end classification (certify-by-remeasure): human-resumed /
# harness-resumed (task-notification, cross-session-message, wakeup-or-hook) / session-end.
# silent_yield is computed over human-resumed turn ends only; the three counts are reported
# alongside in corpus.turn_ends.
# --------------------------------------------------------------------------------------------- #

ENVELOPE_FIXTURE = os.path.join(FIXTURES, "envelope-classification")


def test_classify_envelope_kind_matches_the_four_documented_shapes():
    task_notification = {"type": "user", "origin": {"kind": "task-notification"}, "promptSource": "system"}
    cross_session = {"type": "user", "origin": {"kind": "peer", "from": "x"}, "isMeta": True, "promptSource": "system"}
    wakeup = {"type": "user", "origin": None, "isMeta": True, "promptSource": "system"}
    human = {"type": "user", "origin": {"kind": "human"}, "promptSource": "typed"}
    assert frm.classify_envelope_kind(task_notification) == "task-notification"
    assert frm.classify_envelope_kind(cross_session) == "cross-session-message"
    assert frm.classify_envelope_kind(wakeup) == "wakeup-or-hook"
    assert frm.classify_envelope_kind(human) is None


def test_classify_envelope_kind_matches_stop_hook_feedback_promptsource_absent():
    # round-2 review finding: a real Stop-hook feedback record carries NO promptSource key at
    # all (not merely a different value) — the same "wakeup-or-hook" bucket as a scheduled
    # wakeup / hook notification, distinguished from a real human turn only by isMeta+origin.
    stop_hook_feedback = {
        "type": "user", "origin": None, "isMeta": True,
        "message": {"content": [{"type": "text", "text": "Stop hook feedback: keep going"}]},
    }
    assert "promptSource" not in stop_hook_feedback
    assert frm.classify_envelope_kind(stop_hook_feedback) == "wakeup-or-hook"


def test_classify_envelope_kind_returns_none_for_an_unrecognized_dict_shaped_origin():
    unrecognized = {"type": "user", "origin": {"kind": "some-future-shape"}}
    assert frm.classify_envelope_kind(unrecognized) is None


def test_classify_turn_end_three_way():
    human = {"type": "user", "origin": {"kind": "human"}, "promptSource": "typed"}
    notification = {"type": "user", "origin": {"kind": "task-notification"}, "promptSource": "system"}
    assert frm.classify_turn_end(None) == "session-end"
    assert frm.classify_turn_end(human) == "human-resumed"
    assert frm.classify_turn_end(notification) == "harness-resumed"


def test_mine_file_classifies_six_turn_ends_one_human_four_harness_one_session_end():
    path = os.path.join(ENVELOPE_FIXTURE, "session.jsonl")
    result = frm.mine_file(path)
    # exactly one human-resumed stop (the first turn, "continue" from a genuine human) ...
    assert len(result["stops"]) == 1
    assert result["stops"][0]["silent"] is True
    # ... four harness-resumed turn ends (task-notification, cross-session-message, a scheduled
    # wakeup, and a Stop-hook feedback — the latter two both "wakeup-or-hook") ...
    assert result["harness_resumed_count"] == 4
    # ... and one session-end (the file's final assistant turn, nothing follows it).
    assert result["session_end_count"] == 1


def test_mine_file_harness_resumed_by_kind_breakdown():
    path = os.path.join(ENVELOPE_FIXTURE, "session.jsonl")
    result = frm.mine_file(path)
    assert result["harness_resumed_by_kind"] == {
        "task-notification": 1,
        "cross-session-message": 1,
        "wakeup-or-hook": 2,  # the scheduled wakeup AND the Stop-hook feedback record
        "other-harness": 0,
    }


def test_harness_resumed_by_kind_uses_classify_envelope_kind_not_dead_code(monkeypatch):
    # RISK regression (round-2 review): classify_envelope_kind() must actually be consulted per
    # harness-resumed turn end — if mine_file() stopped calling it, every one of these four would
    # collapse into "other-harness" instead of its real sub-kind.
    path = os.path.join(ENVELOPE_FIXTURE, "session.jsonl")
    baseline = frm.mine_file(path)
    assert baseline["harness_resumed_by_kind"]["other-harness"] == 0
    monkeypatch.setattr(frm, "classify_envelope_kind", lambda r: None)
    patched = frm.mine_file(path)
    assert patched["harness_resumed_by_kind"]["other-harness"] == 4
    assert patched["harness_resumed_by_kind"]["task-notification"] == 0
    assert patched["harness_resumed_by_kind"]["cross-session-message"] == 0
    assert patched["harness_resumed_by_kind"]["wakeup-or-hook"] == 0


def test_silent_yield_denominator_is_human_resumed_only_not_all_six_turn_ends():
    report = frm.build_report(FAR_PAST, [ENVELOPE_FIXTURE], repo=None)
    ratio = report["ratios"]["silent_yield"]
    # RISK regression (the R3-boundary blind spot this atom removes): if harness-resumed turn
    # ends leaked into the denominator, this would read 5/6 instead of 1/1.
    assert ratio == {"numerator": 1, "denominator": 1, "ratio": 1.0}


def test_corpus_reports_the_three_turn_end_counts_and_the_harness_by_kind_breakdown():
    report = frm.build_report(FAR_PAST, [ENVELOPE_FIXTURE], repo=None)
    te = report["corpus"]["turn_ends"]
    assert te["human_resumed"] == 1
    assert te["harness_resumed"] == 4
    assert te["session_end"] == 1
    assert te["harness_resumed_by_kind"] == {
        "task-notification": 1,
        "cross-session-message": 1,
        "wakeup-or-hook": 2,
        "other-harness": 0,
    }


def test_known_081_fixture_has_no_harness_resumed_or_session_end_turn_ends():
    # the pre-existing known-081/silent-4-of-5 fixtures predate this atom and use ONLY genuine
    # human turns — confirms their exact numerator/denominator (AC-INS-4) is unaffected by the
    # new classification (every one of their turn ends is already human-resumed).
    report = frm.build_report(FAR_PAST, [os.path.join(FIXTURES, "known-081")], repo=None)
    te = report["corpus"]["turn_ends"]
    assert te["harness_resumed"] == 0
    assert te["session_end"] == 0
    assert te["human_resumed"] == 100


# --------------------------------------------------------------------------------------------- #
# AC-CBR-2 — --certify compares the six ratios against the programme thresholds and prints
# CERTIFY-PASS / CERTIFY-OPEN, exit 0 either way.
# --------------------------------------------------------------------------------------------- #


def test_shipped_thresholds_file_loads_the_four_programme_ratios():
    thresholds = frm.load_thresholds(frm._SHIPPED_THRESHOLDS_PATH)
    assert thresholds["silent_yield"]["max"] == 0.20
    assert thresholds["directive_reply"]["max"] == 0.15
    assert thresholds["granted_verb_denial"]["max"] == 0.0
    assert thresholds["guard_false_positive_sample"]["sampled"] is True
    # informational-only ratios carry no programme exit threshold
    assert "authorized_to_built" not in thresholds
    assert "rounds_per_shipped_atom" not in thresholds


def test_certify_pass_on_a_lenient_fixture_thresholds_file():
    report = frm.build_report(FAR_PAST, [os.path.join(FIXTURES, "known-081")], repo=None)
    thresholds = frm.load_thresholds(os.path.join(FIXTURES, "thresholds-pass.yaml"))
    misses = frm.certify(report, thresholds)
    assert misses == []
    assert frm.render_certify_verdict(misses) == "CERTIFY-PASS"


def test_certify_open_on_a_strict_fixture_thresholds_file_names_the_missing_ratio():
    report = frm.build_report(FAR_PAST, [os.path.join(FIXTURES, "known-081")], repo=None)
    thresholds = frm.load_thresholds(os.path.join(FIXTURES, "thresholds-open.yaml"))
    misses = frm.certify(report, thresholds)
    assert misses == ["silent_yield 0.81 vs 0.5"]
    verdict = frm.render_certify_verdict(misses)
    assert verdict.startswith("CERTIFY-OPEN:")
    assert "silent_yield" in verdict


def test_certify_treats_an_unmeasured_ratio_as_trivially_within_threshold():
    # FAR_FUTURE excludes every fixture file by mtime -> zero files scanned -> ratio None.
    report = frm.build_report(FAR_FUTURE, [os.path.join(FIXTURES, "known-081")], repo=None)
    assert report["ratios"]["silent_yield"]["ratio"] is None
    misses = frm.certify(report, frm.load_thresholds(os.path.join(FIXTURES, "thresholds-open.yaml")))
    assert misses == []


def test_certify_guard_false_positive_sample_passes_on_zero_denials_stays_open_on_nonzero():
    # a COMPLETE thresholds dict (all four PROGRAMME_RATIOS) so this test isolates the sampled-
    # ratio behavior alone, without also tripping the "no threshold configured" completeness
    # check below on the other three.
    thresholds = {
        "silent_yield": {"max": 1.0},
        "directive_reply": {"max": 1.0},
        "granted_verb_denial": {"max": 1.0},
        "guard_false_positive_sample": {"sampled": True},
    }
    zero_denials_report = frm.build_report(FAR_PAST, [os.path.join(FIXTURES, "known-081")], repo=None)
    assert frm.certify(zero_denials_report, thresholds) == []
    nonzero_report = frm.build_report(FAR_PAST, [os.path.join(FIXTURES, "granted-verb-denial")], repo=None)
    misses = frm.certify(nonzero_report, thresholds)
    assert len(misses) == 1
    assert "guard_false_positive_sample" in misses[0]
    assert "manual review" in misses[0]


# --------------------------------------------------------------------------------------------- #
# AC-CBR-2 round-2 review — a thresholds file missing one of PROGRAMME_RATIOS certifies OPEN
# naming it "no threshold configured", never a silent pass by omission; a malformed thresholds
# file is reported as "thresholds unreadable", never a bare CERTIFY-PASS.
# --------------------------------------------------------------------------------------------- #


def test_certify_open_on_a_thresholds_file_missing_a_core_ratio_key():
    report = frm.build_report(FAR_PAST, [os.path.join(FIXTURES, "known-081")], repo=None)
    thresholds = frm.load_thresholds(os.path.join(FIXTURES, "thresholds-missing-ratio.yaml"))
    assert "directive_reply" not in thresholds
    misses = frm.certify(report, thresholds)
    assert misses == ["directive_reply no threshold configured"]
    verdict = frm.render_certify_verdict(misses)
    assert verdict == "CERTIFY-OPEN: directive_reply no threshold configured"


def test_load_thresholds_strict_raises_thresholds_error_on_malformed_yaml():
    path = os.path.join(FIXTURES, "thresholds-malformed.yaml")
    with pytest.raises(frm.ThresholdsError) as excinfo:
        frm.load_thresholds_strict(path)
    assert path in str(excinfo.value)


def test_load_thresholds_lenient_degrades_silently_on_malformed_yaml_by_design():
    # the LENIENT loader (used by direct callers/tests with an already-trusted dict) keeps its
    # pre-existing silent-degrade contract; only load_thresholds_strict() (the CLI --certify
    # path) distinguishes a malformed file from "nothing configured".
    path = os.path.join(FIXTURES, "thresholds-malformed.yaml")
    assert frm.load_thresholds(path) == {}


def test_load_thresholds_strict_missing_file_is_not_an_error(tmp_path):
    # a file that does not exist at all is a normal state (thresholds_path() already resolved
    # the shipped default), never a parse failure.
    assert frm.load_thresholds_strict(str(tmp_path / "does-not-exist.yaml")) == {}


def test_cli_certify_reports_thresholds_unreadable_for_a_malformed_workspace_override(tmp_path):
    workspace = tmp_path / "workspace"
    override_dir = workspace / "docs" / "programs" / "autonomy-continuation"
    override_dir.mkdir(parents=True)
    shutil.copy(
        os.path.join(FIXTURES, "thresholds-malformed.yaml"), override_dir / "thresholds.yaml",
    )
    proc = subprocess.run(
        [
            sys.executable, SCRIPT,
            "--since", FAR_PAST,
            "--projects-dir", os.path.join(FIXTURES, "known-081"),
            "--certify",
        ],
        capture_output=True, text=True,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(workspace)},
    )
    assert proc.returncode == 0  # report-only: unreadable thresholds is a printed line, not a gate
    assert "CERTIFY-OPEN: thresholds unreadable" in proc.stdout
    assert "thresholds.yaml" in proc.stdout
    assert "CERTIFY-PASS" not in proc.stdout


def test_cli_certify_prints_verdict_line_and_exits_zero_pass():
    proc = subprocess.run(
        [
            sys.executable, SCRIPT,
            "--since", FAR_PAST,
            "--projects-dir", os.path.join(FIXTURES, "known-081"),
            "--certify",
        ],
        capture_output=True, text=True,
        env={**os.environ, "CLAUDE_PROJECT_DIR": FIXTURES},  # no workspace override in FIXTURES
    )
    assert proc.returncode == 0
    assert "CERTIFY-PASS" in proc.stdout or "CERTIFY-OPEN" in proc.stdout


def test_workspace_thresholds_override_shipped_default_when_present(tmp_path):
    workspace = tmp_path / "workspace"
    override_dir = workspace / "docs" / "programs" / "autonomy-continuation"
    override_dir.mkdir(parents=True)
    (override_dir / "thresholds.yaml").write_text("thresholds:\n  silent_yield:\n    max: 0.9\n")
    path = frm.thresholds_path(str(workspace))
    assert path == str(override_dir / "thresholds.yaml")
    thresholds = frm.load_thresholds(path)
    assert thresholds["silent_yield"]["max"] == 0.9


def test_thresholds_path_falls_back_to_shipped_default_when_no_workspace_override(tmp_path):
    path = frm.thresholds_path(str(tmp_path))
    assert path == frm._SHIPPED_THRESHOLDS_PATH


# --------------------------------------------------------------------------------------------- #
# AC-CBR-1 round-2 review — the extended-thinking phantom-stop bug: Claude Code writes one
# logical assistant turn as MULTIPLE `type:"assistant"` records sharing one `message.id` (a
# thinking-only fragment, `stop_reason: "tool_use"`, then a tool_use fragment). Per-record
# evaluation misread the thinking-only fragment as its own tool-free turn end. Fixed by grouping
# consecutive same-`message.id` fragments into one logical turn before evaluating anything.
# --------------------------------------------------------------------------------------------- #

EXTENDED_THINKING_FIXTURE = os.path.join(FIXTURES, "extended-thinking-turn")


def test_group_logical_assistant_turns_merges_same_message_id_fragments():
    thinking = {"type": "assistant", "message": {"id": "m1", "stop_reason": "tool_use", "content": [{"type": "thinking", "thinking": "..."}]}}
    tool_use = {"type": "assistant", "message": {"id": "m1", "stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "tu", "name": "Bash", "input": {}}]}}
    filtered = [(0, thinking), (1, tool_use)]
    groups = list(frm._group_logical_assistant_turns(filtered))
    assert len(groups) == 1
    fragments, last_j = groups[0]
    assert fragments == [thinking, tool_use]
    assert last_j == 1  # the tool_use fragment's position, never the thinking-only fragment's


def test_group_logical_assistant_turns_keeps_none_id_fragments_singleton():
    # backward compatibility (AC-CBR-4): every pre-existing fixture predates message.id and must
    # keep evaluating each raw assistant record as its own turn.
    a = {"type": "assistant", "message": {"content": [{"type": "text", "text": "a"}]}}
    b = {"type": "assistant", "message": {"content": [{"type": "text", "text": "b"}]}}
    filtered = [(0, a), (1, b)]
    groups = list(frm._group_logical_assistant_turns(filtered))
    assert len(groups) == 2
    assert groups[0] == ([a], 0)
    assert groups[1] == ([b], 1)


def test_logical_turn_has_tool_use_true_from_stop_reason_alone():
    # round-2 review: a thinking-only fragment ALONE already carries stop_reason "tool_use" in
    # real transcripts, even before its sibling tool_use fragment is considered — this must be
    # enough on its own, redundantly with the content-block check.
    thinking_only = {"type": "assistant", "message": {"id": "m1", "stop_reason": "tool_use", "content": [{"type": "thinking", "thinking": "..."}]}}
    assert frm._logical_turn_has_tool_use([thinking_only]) is True


def test_logical_turn_has_tool_use_false_for_a_genuine_end_turn_group():
    thinking = {"type": "assistant", "message": {"id": "m2", "stop_reason": "end_turn", "content": [{"type": "thinking", "thinking": "..."}]}}
    text = {"type": "assistant", "message": {"id": "m2", "stop_reason": "end_turn", "content": [{"type": "text", "text": "Ready?"}]}}
    assert frm._logical_turn_has_tool_use([thinking, text]) is False


def test_extended_thinking_tool_use_group_produces_zero_stops_and_zero_of_anything_else():
    # scenario (a): thinking-only + tool_use fragments sharing a message.id, followed by a
    # tool_result. The merged logical turn correctly reads as "uses tools" and is skipped
    # entirely — not a stop, not harness-resumed, not session-end. Pre-fix, the thinking-only
    # fragment alone would have read as a tool-free turn end whose next_record is the
    # tool_result (a plain "user" record with no origin) -> misclassified harness-resumed
    # ("other-harness").
    path = os.path.join(EXTENDED_THINKING_FIXTURE, "session.jsonl")
    result = frm.mine_file(path)
    # exactly one stop total in this file — scenario (b)'s question, below; scenario (a)
    # contributes nothing to stops, harness_resumed_count, OR session_end_count.
    assert len(result["stops"]) == 1
    assert result["harness_resumed_count"] == 0
    assert result["session_end_count"] == 0


def test_extended_thinking_question_group_is_one_non_silent_human_resumed_stop():
    # scenario (b): a thinking fragment + a text fragment ending in a question, sharing a
    # message.id, followed by a genuine human reply -> exactly one human-resumed stop, NOT
    # silent (the merged text's tail carries the question mark from the text fragment).
    path = os.path.join(EXTENDED_THINKING_FIXTURE, "session.jsonl")
    result = frm.mine_file(path)
    assert len(result["stops"]) == 1
    assert result["stops"][0]["silent"] is False


def test_extended_thinking_fixture_end_to_end_silent_yield_ratio_is_zero():
    report = frm.build_report(FAR_PAST, [EXTENDED_THINKING_FIXTURE], repo=None)
    assert report["ratios"]["silent_yield"] == {"numerator": 0, "denominator": 1, "ratio": 0.0}


def test_write_out_report_refuses_to_follow_a_symlink_at_the_final_write_step(tmp_path):
    real_target = tmp_path / "real.json"
    real_target.write_text("{}")
    link_path = tmp_path / "link.json"
    link_path.symlink_to(real_target)
    # the containment check (realpath) would accept this — it resolves inside tmp_path — but the
    # write step itself must refuse to write THROUGH the symlink (O_NOFOLLOW), per "follow no
    # symlinks" for the actual write.
    with pytest.raises(OSError):
        frm.write_out_report(str(link_path), {"ok": True})
