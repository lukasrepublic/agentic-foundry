"""tests/test_learnings.py — converted from scripts/foundry_checks/{learn-capture-entrypoint,
learn-distill, capture-projectdir-resolve}.py.

`bin/foundry-learn-capture` and `hooks/foundry-session-learnings.sh` already carry their own
comprehensive, hermetic `--selftest` batteries (AC-PUBCAP-1..7, AC-COMPACT-*, AC-LBC-*, AC-CLO-*
— including the FF-14 project-dir self-resolution precedence the capture-projectdir-resolve check
existed to prove). Rather than reimplement those fixtures, this module drives the REAL scripts'
own selftests directly (subprocess) and adds direct assertions over `scripts/foundry-distill.py`'s
real consumer functions (the learn-distill subject).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from conftest import REPO_ROOT, load_module

distill = load_module("scripts/foundry-distill.py", "foundry_distill")


def test_learn_capture_entrypoint_selftest():
    script = os.path.join(REPO_ROOT, "bin", "foundry-learn-capture")
    proc = subprocess.run(["bash", script, "--selftest"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "FOUNDRY-LEARN-CAPTURE-SELFTEST-GREEN" in proc.stdout


def test_session_learnings_hook_selftest():
    script = os.path.join(REPO_ROOT, "hooks", "foundry-session-learnings.sh")
    proc = subprocess.run(["bash", script, "--selftest"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "FOUNDRY-SESSION-LEARNINGS-SELFTEST-GREEN" in proc.stdout
    # feat-foundry-learnings-substance-gate-synthetic-turns: assert the three frozen contract
    # checkpoints by their exact PASS lines, not just the overall GREEN sentinel (structure, not
    # a substring of the aggregate) so a single AC regressing is diagnosable from pytest alone.
    for line in (
        "AC-SYNT-1 local-command-records-excluded-from-turn-count: PASS",
        "AC-SYNT-2 honest-once-per-session-cadence-wording: PASS",
        "AC-SYNT-3 selftest-real-oracle-anti-tautology: PASS",
    ):
        assert line in proc.stdout, proc.stdout


def _stop_hook(tmp_path, knob, sid):
    """Drive the REAL Stop entry point with a substantive (mutation-bearing) transcript."""
    tp = tmp_path / f"{sid}.jsonl"
    tp.write_text(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Edit", "input": {}}]}}) + "\n", encoding="utf-8")
    proj = tmp_path / "proj"
    (proj / ".foundry").mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k != "FOUNDRY_SESSION_LEARNINGS"}
    env.update({"CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_PROJECT_DIR": str(proj),
                "TMPDIR": str(tmp_path)})
    if knob is not None:
        env["FOUNDRY_SESSION_LEARNINGS"] = knob
    payload = json.dumps({"session_id": sid, "stop_hook_active": False, "transcript_path": str(tp)})
    script = os.path.join(REPO_ROOT, "hooks", "foundry-session-learnings.sh")
    return subprocess.run(["bash", script, "stop"], input=payload, capture_output=True, text=True,
                          env=env, timeout=60)


def test_session_learnings_stop_is_off_by_default(tmp_path):
    """AC-V118B-4: unset (and off / any unrecognized value) → exit 0, no block decision."""
    for knob, sid in ((None, "V118B4-unset"), ("off", "V118B4-off"), ("", "V118B4-empty"),
                      ("yes", "V118B4-garbage")):
        proc = _stop_hook(tmp_path, knob, sid)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert '"decision"' not in proc.stdout, (knob, proc.stdout)


def test_session_learnings_stop_blocks_only_when_opted_in(tmp_path):
    for knob, sid in (("on", "V118B4-on"), ("full", "V118B4-full")):
        proc = _stop_hook(tmp_path, knob, sid)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert json.loads(proc.stdout)["decision"] == "block", (knob, proc.stdout)


def test_foundry_distill_own_selftest():
    script = os.path.join(REPO_ROOT, "scripts", "foundry-distill.py")
    proc = subprocess.run([sys.executable, script, "--selftest"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


class TestScanDrift:
    def test_clean_buffer_has_zero_drift(self, tmp_path):
        buffer_dir = tmp_path / "buffer"
        part = buffer_dir / "2026-07-27"
        part.mkdir(parents=True)
        rec = {"ts": "2026-07-27T00:00:00Z", "kind": "note", "text": "hi"}
        with open(part / "records.jsonl", "w", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        drift = distill.scan_drift(str(buffer_dir))
        assert sum(drift["counts"].values()) == 0

    def test_wrong_file_name_counts_as_drift(self, tmp_path):
        buffer_dir = tmp_path / "buffer"
        part = buffer_dir / "2026-07-27"
        part.mkdir(parents=True)
        with open(part / "notes.txt", "w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": "2026-07-27T00:00:00Z", "kind": "note", "text": "hi"}) + "\n")
        drift = distill.scan_drift(str(buffer_dir))
        assert drift["counts"]["wrong_file"] == 1

    def test_unparseable_line_counts_as_drift(self, tmp_path):
        buffer_dir = tmp_path / "buffer"
        part = buffer_dir / "2026-07-27"
        part.mkdir(parents=True)
        with open(part / "records.jsonl", "w", encoding="utf-8") as f:
            f.write("not json {{\n")
        drift = distill.scan_drift(str(buffer_dir))
        assert drift["counts"]["unparseable_line"] == 1


class TestReadRecords:
    def test_reads_only_dated_partitions_records_file(self, tmp_path):
        buffer_dir = tmp_path / "buffer"
        part = buffer_dir / "2026-07-27"
        part.mkdir(parents=True)
        rec = {"ts": "2026-07-27T00:00:00Z", "kind": "note", "text": "hello"}
        with open(part / "records.jsonl", "w", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        (buffer_dir / "not-a-date-dir").mkdir()
        records = distill.read_records(str(buffer_dir))
        assert len(records) == 1
        assert records[0]["rec"]["text"] == "hello"

    def test_empty_buffer_has_no_records(self, tmp_path):
        buffer_dir = tmp_path / "buffer"
        buffer_dir.mkdir()
        assert distill.read_records(str(buffer_dir)) == []
