"""tests/test_run_metrics.py — feat-foundry-run-duration-capture, AC-RDC-1..12.

Drives `scripts/foundry_run_metrics.py` (the write boundary) directly over throwaway `tmp_path`
fixtures, plus the real `hooks/foundry-run-metrics.sh` and the REAL wired `hooks/hooks.json` chain
via subprocess, per CONTRIBUTING.md's testing convention. Every test asserts EQUALITY or SET
MEMBERSHIP on a written row field — never merely that the writer ran without raising.
"""
from __future__ import annotations

import copy
import json
import os
import stat
import subprocess
import sys

import pytest

from conftest import REPO_ROOT, load_module

frm = load_module("scripts/foundry_run_metrics.py", "foundry_run_metrics")

HOOK_SCRIPT = os.path.join(REPO_ROOT, "hooks", "foundry-run-metrics.sh")
LEARNINGS_SCRIPT = os.path.join(REPO_ROOT, "hooks", "foundry-harvest-learnings.sh")
HOOKS_JSON = os.path.join(REPO_ROOT, "hooks", "hooks.json")

SPEC_SHA_A = "a" * 64
SPEC_SHA_B = "b1" * 32
FORGED_SHA = "f" * 64


# --------------------------------------------------------------------------- #
# Fixture helpers
# --------------------------------------------------------------------------- #
def _spec_ref(name: str) -> str:
    return f"specs/features/foundry/metrics/{name}/feat-foundry-{name}.md"


def _write_contract(project_dir, spec_ref, auth_seq=1, spec_sha256=SPEC_SHA_A):
    contract_path = frm.contract_path_for_spec(spec_ref, str(project_dir))
    os.makedirs(os.path.dirname(contract_path), exist_ok=True)
    with open(contract_path, "w", encoding="utf-8") as fh:
        fh.write(
            "spec_ref: %s\n"
            "authorized:\n"
            "  operator_id: op_test\n"
            "  auth_seq: %d\n"
            "  spec_sha256: %s\n" % (spec_ref, auth_seq, spec_sha256)
        )
    return contract_path


def _bump_auth_seq(contract_path, new_seq):
    with open(contract_path, encoding="utf-8") as fh:
        text = fh.read()
    lines = [
        (f"  auth_seq: {new_seq}\n" if line.strip().startswith("auth_seq:") else line)
        for line in text.splitlines(keepends=True)
    ]
    with open(contract_path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)


def _valid_unobserved_row(spec_ref="specs/features/foundry/x/y/feat-x.md", run_id="run-1",
                          auth_seq=1, spec_sha256=SPEC_SHA_A):
    return {
        "schema_version": 1,
        "run_id": run_id,
        "spec_ref": spec_ref,
        "spec_sha256": spec_sha256,
        "dispatched_at": "2026-01-01T00:00:00Z",
        "completed_at": "2026-01-01T00:10:00Z",
        "active_seconds": None,
        "measurement": "unobserved",
        "unobserved_reason": "queue-indistinguishable",
        "auth_seq_at_dispatch": auth_seq,
        "auth_seq_final": auth_seq,
        "rounds": 1,
        "outcome": "landed",
    }


def _ledger_rows(project_dir):
    p = frm.ledger_path(str(project_dir))
    if not os.path.isfile(p):
        return []
    with open(p, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _wave_payload(records, tool_name="Workflow"):
    if tool_name == "Workflow":
        return {"session_id": "sess-1", "tool_name": "Workflow", "tool_response": records}
    return {"session_id": "sess-1", "tool_name": "Agent", "tool_output": records[0]}


# --------------------------------------------------------------------------- #
# AC-RDC-1 — exactly one row per atom per wave run, at collect.
# --------------------------------------------------------------------------- #
def test_wave_collect_appends_exactly_one_row_per_atom(tmp_path):
    spec_a, spec_b = _spec_ref("atom-a"), _spec_ref("atom-b")
    _write_contract(tmp_path, spec_a, auth_seq=1, spec_sha256=SPEC_SHA_A)
    _write_contract(tmp_path, spec_b, auth_seq=2, spec_sha256=SPEC_SHA_B)

    payload = _wave_payload([
        {"atom": spec_a, "impl": {"status": "ready"}, "review": {"verdict": "PASS"}},
        {"atom": spec_b, "impl": {"status": "ready"}, "review": {"verdict": "PASS"}},
    ])
    results = frm.process_posttooluse_payload(payload, run_id="wave-run-1", project_dir=str(tmp_path))

    assert all(r is not None for r in results)
    rows = _ledger_rows(tmp_path)
    assert len(rows) == 2
    by_spec = {r["spec_ref"]: r for r in rows}
    assert set(by_spec) == {spec_a, spec_b}
    assert by_spec[spec_a]["run_id"] == "wave-run-1"
    assert by_spec[spec_b]["run_id"] == "wave-run-1"
    assert by_spec[spec_a]["completed_at"]  # host clock stamped at collect
    # Running the SAME wave payload again under a DIFFERENT run_id (a fresh dispatch) adds new
    # rows rather than colliding — one row per (run_id, spec_ref), not per spec_ref alone.
    frm.process_posttooluse_payload(payload, run_id="wave-run-2", project_dir=str(tmp_path))
    assert len(_ledger_rows(tmp_path)) == 4


# --------------------------------------------------------------------------- #
# AC-RDC-2 — validate-and-refuse, table-driven: one refusal case per rule.
# --------------------------------------------------------------------------- #
def test_write_boundary_refuses_nonconforming_row(tmp_path):
    baseline = _valid_unobserved_row()
    assert frm.validate_row(baseline) == []
    frm.write_row(copy.deepcopy(baseline), project_dir=str(tmp_path))  # sanity: baseline is writable

    def mutate(**overrides):
        row = copy.deepcopy(baseline)
        row["run_id"] = "distinct-" + json.dumps(overrides, sort_keys=True)  # avoid AC-RDC-12 collision
        row.update(overrides)
        return row

    cases = []
    for field in frm._REQUIRED_FIELDS:
        row = copy.deepcopy(baseline)
        row["run_id"] = f"missing-{field}"
        del row[field]
        cases.append((f"missing:{field}", row))

    def without(row, key):
        row = copy.deepcopy(row)
        del row[key]
        return row

    negative_active = without(baseline, "unobserved_reason")
    negative_active.update({"measurement": "measured", "active_seconds": -1, "run_id": "neg-active"})

    cases += [
        ("bad-timestamp-no-Z", mutate(dispatched_at="2026-01-01T00:00:00")),
        ("bad-timestamp-format", mutate(completed_at="not-a-timestamp")),
        ("bad-spec_sha256-short", mutate(spec_sha256="abc123")),
        ("bad-spec_sha256-uppercase", mutate(spec_sha256="A" * 64)),
        ("negative-active_seconds", negative_active),
        ("active_seconds-non-null-when-unobserved", mutate(active_seconds=5)),
        ("rounds-zero", mutate(rounds=0)),
        ("rounds-negative", mutate(rounds=-2)),
        ("auth_seq_at_dispatch-null-not-backfill", mutate(auth_seq_at_dispatch=None)),
        ("auth_seq_final-zero", mutate(auth_seq_final=0)),
        ("out-of-enum-measurement", mutate(measurement="approximated")),
        ("out-of-enum-outcome", mutate(outcome="cancelled")),
        ("out-of-enum-unobserved_reason", mutate(unobserved_reason="unknown")),
        ("unobserved_reason-missing-when-unobserved",
         {**without(baseline, "unobserved_reason"), "run_id": "no-reason"}),
    ]
    # unobserved_reason present but measurement != unobserved (iff violation) — active_seconds
    # is set for schema-shape plausibility, but the baseline's unobserved_reason key is left in
    # place deliberately (that IS the violation under test).
    measured_row = mutate(measurement="measured", active_seconds=42)
    measured_row["run_id"] = "iff-violation"
    cases.append(("unobserved_reason-present-when-measured", measured_row))

    for label, row in cases:
        errors = frm.validate_row(row)
        assert errors, f"{label}: expected validation errors, got none for row={row}"
        with pytest.raises(frm.RunMetricsWriteError):
            frm.write_row(row, project_dir=str(tmp_path))

    # positive control: rows landed by the sanity write above, and none of the refused rows did
    rows = _ledger_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["run_id"] == baseline["run_id"]


# --------------------------------------------------------------------------- #
# AC-RDC-3a — the hook exits 0 for every payload shape.
# --------------------------------------------------------------------------- #
def test_metrics_hook_exits_zero_for_every_payload_shape(tmp_path):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(tmp_path))
    spec_ref = _spec_ref("hook-exit0")
    _write_contract(tmp_path, spec_ref, auth_seq=1)

    payloads = {
        "empty": "",
        "malformed": "not json {{{ at all",
        "well-formed-unresolvable": json.dumps(_wave_payload(
            [{"atom": "specs/features/foundry/nope/nope/feat-nope.md", "impl": {"status": "ready"}}])),
        "well-formed-resolvable": json.dumps(_wave_payload(
            [{"atom": spec_ref, "impl": {"status": "ready"}, "review": {"verdict": "PASS"}}])),
        "oversized": json.dumps({"tool_name": "Workflow", "tool_response": [
            {"atom": "x", "impl": {"status": "ready"}, "junk": "z" * 2_000_000}]}),
    }
    for label, stdin_text in payloads.items():
        proc = subprocess.run(["bash", HOOK_SCRIPT, "posttooluse"], input=stdin_text,
                              capture_output=True, text=True, env=env, timeout=30)
        assert proc.returncode == 0, f"{label}: rc={proc.returncode} stderr={proc.stderr}"

    # the resolvable case actually landed a row (the exit-0 guarantee isn't from a no-op)
    rows = _ledger_rows(tmp_path)
    assert any(r["spec_ref"] == spec_ref for r in rows)


# --------------------------------------------------------------------------- #
# AC-RDC-3a — integration: the REAL wired hooks.json chain lands both records, metrics entry LAST.
# --------------------------------------------------------------------------- #
def test_wired_posttooluse_chain_lands_both_records_metrics_entry_last(tmp_path):
    with open(HOOKS_JSON, encoding="utf-8") as fh:
        hooks_config = json.load(fh)
    matchers = hooks_config["hooks"]["PostToolUse"]
    entry = next(m for m in matchers if m.get("matcher") == "Agent|Workflow")
    commands = [h["command"] for h in entry["hooks"]]
    learnings_idx = next(i for i, c in enumerate(commands) if "foundry-harvest-learnings.sh" in c)
    metrics_idx = next(i for i, c in enumerate(commands) if "foundry-run-metrics.sh" in c)
    assert metrics_idx > learnings_idx, "the metrics entry must sit AFTER the learnings entry"

    spec_ref = _spec_ref("chain-integration")
    _write_contract(tmp_path, spec_ref, auth_seq=1)
    payload_text = json.dumps(_wave_payload([
        {"atom": spec_ref, "impl": {"branch": "chain-branch", "status": "ready",
                                    "learnings": [{"note": "chain test"}]},
         "review": {"verdict": "PASS"}},
    ]))
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(tmp_path))

    proc_learn = subprocess.run(["bash", LEARNINGS_SCRIPT, "posttooluse"], input=payload_text,
                                capture_output=True, text=True, env=env, timeout=30)
    assert proc_learn.returncode == 0
    proc_metrics = subprocess.run(["bash", HOOK_SCRIPT, "posttooluse"], input=payload_text,
                                  capture_output=True, text=True, env=env, timeout=30)
    assert proc_metrics.returncode == 0

    learnings_dir = os.path.join(str(tmp_path), ".foundry", "session-learnings")
    learnings_files = []
    for root, _dirs, files in os.walk(learnings_dir):
        learnings_files += [f for f in files if f.endswith(".jsonl") and "chain-branch" in f]
    assert learnings_files, "the learnings record must land via the real wired hook"

    rows = _ledger_rows(tmp_path)
    assert any(r["spec_ref"] == spec_ref for r in rows), "the metrics row must land via the real wired hook"


# --------------------------------------------------------------------------- #
# AC-RDC-3b — a metrics failure never changes the run's outcome (unwritable ledger).
# --------------------------------------------------------------------------- #
def test_metrics_failure_leaves_run_outcome_unchanged(tmp_path, capsys):
    spec_ref = _spec_ref("unwritable-ledger")
    _write_contract(tmp_path, spec_ref, auth_seq=1)
    foundry_dir = os.path.join(str(tmp_path), ".foundry")
    os.makedirs(foundry_dir, exist_ok=True)
    os.chmod(foundry_dir, 0o500)  # read+execute, no write
    try:
        payload = _wave_payload([{"atom": spec_ref, "impl": {"status": "ready"},
                                  "review": {"verdict": "PASS"}}])
        # simulate "the measured run" as this call's own caller: it must not raise.
        results = frm.process_posttooluse_payload(payload, run_id="unwritable-run",
                                                   project_dir=str(tmp_path))
        assert results == [None]
        captured = capsys.readouterr()
        assert "row refused" in captured.err or "unwritable" in captured.err
    finally:
        os.chmod(foundry_dir, 0o700)
    assert not os.path.isfile(frm.ledger_path(str(tmp_path)))


# --------------------------------------------------------------------------- #
# AC-RDC-3b — precedence: an unreadable acceptance-contract.yaml refuses the ROW, names the read
# failure + path, leaves the run untouched.
# --------------------------------------------------------------------------- #
def test_contract_read_failure_refuses_row_and_leaves_run_untouched(tmp_path, capsys):
    missing_spec = _spec_ref("missing-contract")
    payload = _wave_payload([{"atom": missing_spec, "impl": {"status": "ready"},
                              "review": {"verdict": "PASS"}}])
    results = frm.process_posttooluse_payload(payload, run_id="missing-contract-run",
                                               project_dir=str(tmp_path))
    assert results == [None]
    err = capsys.readouterr().err
    assert "acceptance-contract.yaml" in err
    assert frm.contract_path_for_spec(missing_spec, str(tmp_path)) in err
    assert _ledger_rows(tmp_path) == []

    # direct read_contract_fidelity failure modes, each naming the path
    with pytest.raises(frm.ContractReadError, match="not found"):
        frm.read_contract_fidelity(missing_spec, str(tmp_path))

    malformed_spec = _spec_ref("malformed-contract")
    bad_path = frm.contract_path_for_spec(malformed_spec, str(tmp_path))
    os.makedirs(os.path.dirname(bad_path), exist_ok=True)
    with open(bad_path, "w", encoding="utf-8") as fh:
        fh.write("not: [valid: yaml: at: all\n")
    with pytest.raises(frm.ContractReadError, match="YAML parse error"):
        frm.read_contract_fidelity(malformed_spec, str(tmp_path))

    no_auth_spec = _spec_ref("no-authorized-block")
    no_auth_path = frm.contract_path_for_spec(no_auth_spec, str(tmp_path))
    os.makedirs(os.path.dirname(no_auth_path), exist_ok=True)
    with open(no_auth_path, "w", encoding="utf-8") as fh:
        fh.write(f"spec_ref: {no_auth_spec}\n")  # matches, so the cross-check passes and the
    with pytest.raises(frm.ContractReadError, match="no 'authorized:' block"):  # REAL gap is exercised
        frm.read_contract_fidelity(no_auth_spec, str(tmp_path))


# --------------------------------------------------------------------------- #
# AC-RDC-4 — THE checkpoint: exact active_seconds against a fixture whose elapsed time is an
# order of magnitude larger, preceded by a long slot-wait, with idle in the middle.
# --------------------------------------------------------------------------- #
def test_active_seconds_equals_summed_execution_intervals_excluding_idle_and_slot_wait(tmp_path):
    spec_ref = _spec_ref("active-seconds")
    _write_contract(tmp_path, spec_ref, auth_seq=1)

    dispatched_at = "2026-01-01T00:00:00Z"          # T0 — dispatch requested
    impl_start = "2026-01-01T00:40:00Z"             # 40-minute slot-wait before execution begins
    impl_end = "2026-01-01T00:52:00Z"               # 12 minutes of real execution
    verify_start = "2026-01-01T06:52:00Z"           # 6-hour idle gap between agents
    verify_end = "2026-01-01T06:55:00Z"             # 3 minutes of real execution
    completed_at = verify_end                       # T0 + 6h55m

    intervals = [(impl_start, impl_end), (verify_start, verify_end)]
    active_seconds = frm.sum_execution_intervals(intervals)
    assert active_seconds == 900  # 12*60 + 3*60 — NOT elapsed, NOT elapsed-1, NOT 0

    row = frm.compose_row(spec_ref=spec_ref, run_id="active-run", dispatched_at=dispatched_at,
                          completed_at=completed_at, outcome="landed",
                          execution_intervals=intervals, project_dir=str(tmp_path))
    assert row["measurement"] == "measured"
    assert row["active_seconds"] == 900

    from datetime import datetime, timezone
    def _parse(ts):
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    elapsed = int((_parse(row["completed_at"]) - _parse(row["dispatched_at"])).total_seconds())
    assert elapsed == 24900  # 415 minutes — an order of magnitude larger than active_seconds
    assert row["active_seconds"] != elapsed
    assert elapsed - row["active_seconds"] == 24000  # exactly the excluded wait (2400s) + idle (21600s)

    written = frm.write_row(row, project_dir=str(tmp_path))
    assert written["active_seconds"] == 900


def test_active_seconds_rejects_would_be_elapsed_shortcut():
    # negative control: an implementation that used completed_at-dispatched_at would get 24900,
    # not 900 — a fixture assertion of "< elapsed" alone would pass a broken implementation too.
    intervals = [("2026-01-01T00:40:00Z", "2026-01-01T00:52:00Z"),
                 ("2026-01-01T06:52:00Z", "2026-01-01T06:55:00Z")]
    assert frm.sum_execution_intervals(intervals) == 900
    assert frm.sum_execution_intervals(intervals) != 24900


# --------------------------------------------------------------------------- #
# AC-RDC-5 — the degraded row is asserted POSITIVELY, and the elapsed proxy's ABSENCE is asserted.
# --------------------------------------------------------------------------- #
def test_unobserved_reason_code_from_closed_set_and_never_the_elapsed_proxy(tmp_path):
    spec_ref = _spec_ref("unobserved")
    _write_contract(tmp_path, spec_ref, auth_seq=1)
    dispatched_at, completed_at = "2026-01-01T00:00:00Z", "2026-01-01T00:01:40Z"  # elapsed=100s

    for reason in ("queue-indistinguishable", "self-reported-timing"):
        row = frm.compose_row(spec_ref=spec_ref, run_id=f"unobs-{reason}", dispatched_at=dispatched_at,
                              completed_at=completed_at, outcome="failed",
                              unobserved_reason=reason, project_dir=str(tmp_path))
        assert row["measurement"] == "unobserved"
        assert row["active_seconds"] is None
        assert row["unobserved_reason"] == reason
        assert row["unobserved_reason"] in frm.UNOBSERVED_REASONS
        # the elapsed proxy (100) must appear in NO field of the row
        assert 100 not in row.values()
        assert "100" not in [str(v) for v in row.values()]
        frm.write_row(row, project_dir=str(tmp_path))

    # closed-set membership, not mere non-emptiness: an "unknown" code must be REFUSED.
    bad_row = frm.compose_row(spec_ref=spec_ref, run_id="unobs-bad-reason-attempt",
                              dispatched_at=dispatched_at, completed_at=completed_at,
                              outcome="failed", unobserved_reason="queue-indistinguishable",
                              project_dir=str(tmp_path))
    bad_row["unobserved_reason"] = "unknown"
    assert frm.validate_row(bad_row) != []
    with pytest.raises(frm.RunMetricsWriteError):
        frm.write_row(bad_row, project_dir=str(tmp_path))

    with pytest.raises(ValueError):
        frm.compose_row(spec_ref=spec_ref, run_id="unobs-invalid-code", dispatched_at=dispatched_at,
                        completed_at=completed_at, outcome="failed", unobserved_reason="not-a-real-code",
                        project_dir=str(tmp_path))


# --------------------------------------------------------------------------- #
# AC-RDC-6 — backfill-proxy with null auth_seq accepted at schema_version 1.
# --------------------------------------------------------------------------- #
def test_backfill_proxy_with_null_auth_seq_accepted_at_schema_version_1(tmp_path):
    row = frm.build_backfill_proxy_row(
        spec_ref=_spec_ref("backfill"), run_id="backfill-run-1",
        dispatched_at="2025-01-01T00:00:00Z", completed_at="2025-01-01T01:00:00Z",
        outcome="landed", auth_seq_at_dispatch=None, auth_seq_final=None,
        active_seconds=1800,  # a PROXY estimate — only auth_seq_* is null-eligible for backfill
    )
    assert row["schema_version"] == 1
    assert row["measurement"] == "backfill-proxy"
    assert row["auth_seq_at_dispatch"] is None and row["auth_seq_final"] is None
    assert frm.validate_row(row) == []
    written = frm.write_row(row, project_dir=str(tmp_path))
    assert written["measurement"] == "backfill-proxy"
    rows = _ledger_rows(tmp_path)
    assert any(r["run_id"] == "backfill-run-1" and r["auth_seq_final"] is None for r in rows)


# --------------------------------------------------------------------------- #
# AC-RDC-7 — outcome from the CONSOLIDATED review verdict, disagreement fixture.
# --------------------------------------------------------------------------- #
def test_outcome_follows_consolidated_review_verdict_not_the_raw_verdict(tmp_path):
    # 1. raw PASS, consolidated FAIL (a confirmed Block) -> failed
    disagreement = {"atom": "x", "impl": {"status": "ready"},
                    "verdict": {"verdict": "PASS"}, "review": {"verdict": "FAIL"}}
    assert frm.outcome_from_wave_atom(disagreement) == "failed"

    # 2. ready + NO review key at all (self-gate short-circuit) -> failed
    no_review = {"atom": "x", "impl": {"status": "ready"}, "verdict": {"verdict": "FAIL"}}
    assert frm.outcome_from_wave_atom(no_review) == "failed"

    # 3. ready + consolidated PASS -> landed
    clean_pass = {"atom": "x", "impl": {"status": "ready"},
                 "verdict": {"verdict": "PASS"}, "review": {"verdict": "PASS"}}
    assert frm.outcome_from_wave_atom(clean_pass) == "landed"

    # end-to-end: the WRITTEN row's outcome reflects the disagreement fixture, not the raw verdict
    spec_ref = _spec_ref("outcome-disagreement")
    _write_contract(tmp_path, spec_ref, auth_seq=1)
    payload = _wave_payload([{**disagreement, "atom": spec_ref}])
    frm.process_posttooluse_payload(payload, run_id="outcome-run", project_dir=str(tmp_path))
    rows = _ledger_rows(tmp_path)
    assert rows[0]["outcome"] == "failed"


# --------------------------------------------------------------------------- #
# AC-RDC-8 — append-only, BYTE-PREFIX equality (not a row count).
# --------------------------------------------------------------------------- #
def test_prior_rows_preserved_byte_for_byte_at_original_offsets(tmp_path):
    for i in range(3):
        row = _valid_unobserved_row(spec_ref=_spec_ref(f"append-{i}"), run_id=f"append-run-{i}")
        frm.write_row(row, project_dir=str(tmp_path))

    p = frm.ledger_path(str(tmp_path))
    with open(p, "rb") as fh:
        snapshot = fh.read()

    frm.write_row(_valid_unobserved_row(spec_ref=_spec_ref("append-3"), run_id="append-run-3"),
                  project_dir=str(tmp_path))

    with open(p, "rb") as fh:
        after = fh.read()
    assert len(after) > len(snapshot)
    assert after[:len(snapshot)] == snapshot


# --------------------------------------------------------------------------- #
# AC-RDC-9 — auth_seq read at dispatch AND AGAIN at collect; a re-freeze shows on one row.
# --------------------------------------------------------------------------- #
def test_auth_seq_read_at_dispatch_and_again_at_collect(tmp_path):
    spec_ref = _spec_ref("reauth")
    contract_path = _write_contract(tmp_path, spec_ref, auth_seq=1, spec_sha256=SPEC_SHA_A)

    dispatch_fid = frm.read_contract_fidelity(spec_ref, str(tmp_path))
    assert dispatch_fid["auth_seq"] == 1

    _bump_auth_seq(contract_path, 2)  # a re-freeze between dispatch and collect

    row = frm.compose_row(spec_ref=spec_ref, run_id="reauth-run", dispatched_at="2026-01-01T00:00:00Z",
                          completed_at="2026-01-01T00:05:00Z", outcome="landed",
                          dispatch_fidelity=dispatch_fid, unobserved_reason="queue-indistinguishable",
                          project_dir=str(tmp_path))
    assert row["auth_seq_at_dispatch"] == 1
    assert row["auth_seq_final"] == 2
    assert row["auth_seq_at_dispatch"] != row["auth_seq_final"]

    # negative control: an implementation that reads once and copies would get 1 == 1
    written = frm.write_row(row, project_dir=str(tmp_path))
    assert written["auth_seq_at_dispatch"] == 1 and written["auth_seq_final"] == 2


# --------------------------------------------------------------------------- #
# AC-RDC-10 — standing capture: no kill switch, no path redirect, over the enumerated surface.
# --------------------------------------------------------------------------- #
def test_capture_is_unconditional_under_enumerated_kill_switches_and_path_redirect(tmp_path, monkeypatch):
    disable_env_cases = [
        {"FOUNDRY_RUN_METRICS": "0"}, {"FOUNDRY_RUN_METRICS": "off"},
        {"FOUNDRY_RUN_METRICS": "false"}, {"FOUNDRY_RUN_METRICS": "disabled"},
        {"FOUNDRY_RUN_METRICS_ENABLED": "0"}, {"FOUNDRY_DISABLE_METRICS": "1"},
        {"NO_METRICS": "1"},
        {"FOUNDRY_RUN_METRICS_PATH": "/dev/null"}, {"FOUNDRY_RUN_METRICS_LEDGER": "/dev/null"},
    ]
    for i, env_vars in enumerate(disable_env_cases):
        for k, v in env_vars.items():
            monkeypatch.setenv(k, v)
        spec_ref = _spec_ref(f"disable-surface-{i}")
        _write_contract(tmp_path, spec_ref, auth_seq=1)
        payload = _wave_payload([{"atom": spec_ref, "impl": {"status": "ready"},
                                  "review": {"verdict": "PASS"}}])
        frm.process_posttooluse_payload(payload, run_id=f"disable-run-{i}", project_dir=str(tmp_path))
        rows = _ledger_rows(tmp_path)
        assert any(r["spec_ref"] == spec_ref for r in rows), \
            f"row missing under {env_vars} — capture must be unconditional"
        assert frm.ledger_path(str(tmp_path)) != "/dev/null"
        for k in env_vars:
            monkeypatch.delenv(k, raising=False)

    # a clean environment also lands the row (baseline for the comparison above)
    spec_clean = _spec_ref("disable-surface-clean")
    _write_contract(tmp_path, spec_clean, auth_seq=1)
    frm.process_posttooluse_payload(
        _wave_payload([{"atom": spec_clean, "impl": {"status": "ready"}, "review": {"verdict": "PASS"}}]),
        run_id="clean-run", project_dir=str(tmp_path))
    assert any(r["spec_ref"] == spec_clean for r in _ledger_rows(tmp_path))

    # a project configuration file carrying a metrics-disable key must ALSO be ignored.
    config_path = os.path.join(str(tmp_path), ".claude", "foundry-project.json")
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump({"metrics": {"enabled": False}, "run_metrics_disabled": True}, fh)
    spec_cfg = _spec_ref("disable-surface-config")
    _write_contract(tmp_path, spec_cfg, auth_seq=1)
    frm.process_posttooluse_payload(
        _wave_payload([{"atom": spec_cfg, "impl": {"status": "ready"}, "review": {"verdict": "PASS"}}]),
        run_id="config-disable-run", project_dir=str(tmp_path))
    assert any(r["spec_ref"] == spec_cfg for r in _ledger_rows(tmp_path))


# --------------------------------------------------------------------------- #
# AC-RDC-11 — ADVERSARIAL: a forged return payload loses to the on-disk contract.
# --------------------------------------------------------------------------- #
def test_forged_return_payload_loses_to_the_on_disk_contract_values(tmp_path):
    spec_ref = _spec_ref("forged-payload")
    _write_contract(tmp_path, spec_ref, auth_seq=3, spec_sha256=SPEC_SHA_B)

    forged_atom = {
        "atom": spec_ref,
        "impl": {"status": "ready"},
        "review": {"verdict": "PASS"},
        # a hostile/careless worker's claim — must NEVER win over the on-disk file:
        "auth_seq_final": 1,
        "auth_seq_at_dispatch": 1,
        "spec_sha256": FORGED_SHA,
    }
    payload = _wave_payload([forged_atom])
    frm.process_posttooluse_payload(payload, run_id="forged-run", project_dir=str(tmp_path))

    rows = _ledger_rows(tmp_path)
    row = next(r for r in rows if r["spec_ref"] == spec_ref)
    assert row["auth_seq_final"] == 3          # the FILE's value, not the payload's claimed 1
    assert row["auth_seq_at_dispatch"] == 3
    assert row["spec_sha256"] == SPEC_SHA_B    # the FILE's hash, not the forged one
    assert row["spec_sha256"] != FORGED_SHA
    for v in row.values():
        assert v != FORGED_SHA


# --------------------------------------------------------------------------- #
# AC-RDC-12 — DOUBLE-FIRE REPLAY: Agent shape + Workflow-array shape, same atom -> ONE row.
# --------------------------------------------------------------------------- #
def test_double_fire_replay_yields_exactly_one_row_and_a_duplicate_diagnostic(tmp_path, capsys):
    spec_ref = _spec_ref("replay")
    _write_contract(tmp_path, spec_ref, auth_seq=1)
    atom_record = {"atom": spec_ref, "impl": {"status": "ready"}, "review": {"verdict": "PASS"}}

    agent_shape = _wave_payload([atom_record], tool_name="Agent")
    workflow_shape = _wave_payload([atom_record], tool_name="Workflow")

    first = frm.process_posttooluse_payload(agent_shape, run_id="replay-run", project_dir=str(tmp_path))
    assert first[0] is not None

    second = frm.process_posttooluse_payload(workflow_shape, run_id="replay-run", project_dir=str(tmp_path))
    assert second == [None]  # refused as a duplicate, not written again

    err = capsys.readouterr().err
    assert "duplicate" in err
    assert spec_ref in err or "replay-run" in err

    rows = [r for r in _ledger_rows(tmp_path) if r["spec_ref"] == spec_ref and r["run_id"] == "replay-run"]
    assert len(rows) == 1


# --------------------------------------------------------------------------- #
# SECURITY REVIEW Risk 1 — a forged/hostile `atom` payload value cannot point the host-side
# contract read at an arbitrary readable file: absolute path, `..` traversal, symlink-assisted
# realpath escape, and a contract whose own `spec_ref:` disagrees with the caller-supplied value
# are all refused BEFORE (or without ever trusting) the read.
# --------------------------------------------------------------------------- #
def test_forged_spec_ref_absolute_path_is_refused(tmp_path, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside-abs")
    secret = outside / "secret-contract.yaml"
    secret.write_text("authorized:\n  auth_seq: 99\n  spec_sha256: %s\n" % SPEC_SHA_B, encoding="utf-8")

    with pytest.raises(frm.ContractReadError, match="absolute path"):
        frm.read_contract_fidelity(str(secret), str(tmp_path))

    payload = _wave_payload([{"atom": str(secret), "impl": {"status": "ready"},
                              "review": {"verdict": "PASS"}}])
    results = frm.process_posttooluse_payload(payload, run_id="abs-path-run", project_dir=str(tmp_path))
    assert results == [None]
    assert _ledger_rows(tmp_path) == []


def test_forged_spec_ref_dotdot_traversal_is_refused(tmp_path, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside-dotdot")
    (outside / "acceptance-contract.yaml").write_text(
        "spec_ref: whatever\nauthorized:\n  auth_seq: 99\n  spec_sha256: %s\n" % SPEC_SHA_B,
        encoding="utf-8")
    # a spec_ref shaped to walk OUT of the project dir via '..' — no leading '/', so it is not
    # caught by the absolute-path check alone.
    traversal_spec_ref = f"../{outside.name}/feat-x.md"

    with pytest.raises(frm.ContractReadError, match="path segment"):
        frm.read_contract_fidelity(traversal_spec_ref, str(tmp_path))

    payload = _wave_payload([{"atom": traversal_spec_ref, "impl": {"status": "ready"},
                              "review": {"verdict": "PASS"}}])
    results = frm.process_posttooluse_payload(payload, run_id="dotdot-run", project_dir=str(tmp_path))
    assert results == [None]
    assert _ledger_rows(tmp_path) == []


def test_forged_spec_ref_symlink_assisted_realpath_escape_is_refused(tmp_path, tmp_path_factory):
    # A spec_ref that is well-formed on its face (no '..', not absolute) but whose
    # acceptance-contract.yaml is a SYMLINK whose realpath escapes the project dir.
    outside = tmp_path_factory.mktemp("outside-symlink")
    spec_ref = _spec_ref("symlink-victim")
    real_target = outside / "real-contract.yaml"
    real_target.write_text(
        "spec_ref: %s\nauthorized:\n  auth_seq: 42\n  spec_sha256: %s\n" % (spec_ref, SPEC_SHA_B),
        encoding="utf-8")

    inside_dir = os.path.join(str(tmp_path), os.path.dirname(spec_ref))
    os.makedirs(inside_dir, exist_ok=True)
    os.symlink(str(real_target), os.path.join(inside_dir, "acceptance-contract.yaml"))

    with pytest.raises(frm.ContractReadError, match="outside the project dir"):
        frm.read_contract_fidelity(spec_ref, str(tmp_path))

    payload = _wave_payload([{"atom": spec_ref, "impl": {"status": "ready"},
                              "review": {"verdict": "PASS"}}])
    results = frm.process_posttooluse_payload(payload, run_id="symlink-run", project_dir=str(tmp_path))
    assert results == [None]
    assert _ledger_rows(tmp_path) == []


def test_contract_spec_ref_disagrees_with_payload_atom_is_refused(tmp_path):
    # a contract reachable at the RIGHT path (no traversal) but whose own spec_ref: key names a
    # DIFFERENT atom — e.g. one atom's directory reused/misplaced/symlinked to another's contract.
    spec_ref = _spec_ref("mismatch")
    contract_path = frm.contract_path_for_spec(spec_ref, str(tmp_path))
    os.makedirs(os.path.dirname(contract_path), exist_ok=True)
    with open(contract_path, "w", encoding="utf-8") as fh:
        fh.write("spec_ref: %s\nauthorized:\n  auth_seq: 1\n  spec_sha256: %s\n"
                 % (_spec_ref("a-totally-different-atom"), SPEC_SHA_A))

    with pytest.raises(frm.ContractReadError, match="does not match"):
        frm.read_contract_fidelity(spec_ref, str(tmp_path))

    payload = _wave_payload([{"atom": spec_ref, "impl": {"status": "ready"},
                              "review": {"verdict": "PASS"}}])
    results = frm.process_posttooluse_payload(payload, run_id="mismatch-run", project_dir=str(tmp_path))
    assert results == [None]
    assert _ledger_rows(tmp_path) == []


# --------------------------------------------------------------------------- #
# SECURITY REVIEW Risk 2/4 — the default run_id derives from the payload's OWN session_id, so two
# SEPARATE module invocations for the same wave (the real nested Agent+Workflow overlap) actually
# collide on (run_id, spec_ref); the prior per-invocation-uuid default made AC-RDC-12's dedup
# structurally unreachable in production.
# --------------------------------------------------------------------------- #
def test_default_run_id_derives_from_session_id_and_dedups_across_separate_invocations(tmp_path):
    spec_ref = _spec_ref("session-dedup")
    _write_contract(tmp_path, spec_ref, auth_seq=1)
    atom_record = {"atom": spec_ref, "impl": {"status": "ready"}, "review": {"verdict": "PASS"}}
    payload = _wave_payload([atom_record])  # session_id="sess-1"; NO explicit run_id override

    first = frm.process_posttooluse_payload(payload, project_dir=str(tmp_path))
    assert first[0] is not None
    assert first[0]["run_id"] == "sess-1"

    # a SECOND, wholly separate call — simulating a second real hook process/invocation for the
    # same wave — over the SAME payload (same session_id, same atom).
    second = frm.process_posttooluse_payload(payload, project_dir=str(tmp_path))
    assert second == [None]

    rows = [r for r in _ledger_rows(tmp_path) if r["spec_ref"] == spec_ref]
    assert len(rows) == 1
    assert rows[0]["run_id"] == "sess-1"


def test_default_run_id_falls_back_env_then_uuid(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    assert frm._default_run_id(None) != frm._default_run_id(None)  # freestanding uuid, each call
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "env-session-42")
    assert frm._default_run_id(None) == "env-session-42"
    assert frm._default_run_id("payload-session") == "payload-session"  # payload session_id wins


# --------------------------------------------------------------------------- #
# SECURITY REVIEW Risk 3 — every refusal is persisted to the durable, never-model-visible
# `.foundry/run-metrics.loss.jsonl`, and the shipped hook's stdout/stderr stay empty.
# --------------------------------------------------------------------------- #
def test_refused_row_persists_loss_log_entry_naming_the_cause(tmp_path):
    missing_spec = _spec_ref("loss-log-missing-contract")
    payload = _wave_payload([{"atom": missing_spec, "impl": {"status": "ready"},
                              "review": {"verdict": "PASS"}}])
    results = frm.process_posttooluse_payload(payload, run_id="loss-log-run", project_dir=str(tmp_path))
    assert results == [None]

    loss_path = frm.loss_log_path(str(tmp_path))
    assert os.path.isfile(loss_path)
    with open(loss_path, encoding="utf-8") as fh:
        loss_rows = [json.loads(line) for line in fh if line.strip()]
    assert any(
        r.get("spec_ref") == missing_spec and r.get("run_id") == "loss-log-run"
        and "acceptance-contract.yaml" in (r.get("detail") or "")
        for r in loss_rows
    )


def test_hook_stdout_stderr_empty_but_loss_log_persists(tmp_path):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(tmp_path))
    payload_text = json.dumps(_wave_payload(
        [{"atom": _spec_ref("hook-loss-log"), "impl": {"status": "ready"},
         "review": {"verdict": "PASS"}}]))  # no contract written -> refused
    proc = subprocess.run(["bash", HOOK_SCRIPT, "posttooluse"], input=payload_text,
                          capture_output=True, text=True, env=env, timeout=30)
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert proc.stderr == ""  # nothing surfaced to the harness/model (redirected, unchanged)

    loss_path = frm.loss_log_path(str(tmp_path))
    assert os.path.isfile(loss_path)  # the refusal is durably recorded anyway


# --------------------------------------------------------------------------- #
# SECURITY REVIEW Risk 5 — a payload cannot make this never-block hook do unbounded work: atom
# records beyond MAX_ATOMS_PER_PAYLOAD are truncated, and the truncation itself is loss-logged.
# --------------------------------------------------------------------------- #
def test_atom_records_per_payload_capped_with_loss_log_on_truncation(tmp_path):
    records = []
    for i in range(frm.MAX_ATOMS_PER_PAYLOAD + 5):
        spec_ref = _spec_ref(f"cap-{i}")
        _write_contract(tmp_path, spec_ref, auth_seq=1)
        records.append({"atom": spec_ref, "impl": {"status": "ready"}, "review": {"verdict": "PASS"}})
    payload = _wave_payload(records)

    results = frm.process_posttooluse_payload(payload, run_id="cap-run", project_dir=str(tmp_path))
    assert len(results) == frm.MAX_ATOMS_PER_PAYLOAD

    rows = [r for r in _ledger_rows(tmp_path) if r["run_id"] == "cap-run"]
    assert len(rows) == frm.MAX_ATOMS_PER_PAYLOAD

    loss_path = frm.loss_log_path(str(tmp_path))
    with open(loss_path, encoding="utf-8") as fh:
        loss_rows = [json.loads(line) for line in fh if line.strip()]
    assert any(r.get("reason") == "payload-truncated" for r in loss_rows)
