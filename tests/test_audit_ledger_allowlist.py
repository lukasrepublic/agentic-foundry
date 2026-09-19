"""tests/test_audit_ledger_allowlist.py — feat-foundry-authorization-audit-ledger-allowlist
(AC-ALAL-1..5).

`find_audit` used to answer "was this spec audited?" with a DENYLIST of three legacy verdicts
(`fail`, `rejected`, `abandoned`): any verdict NOT in that tiny set read as accepted — including
every v2 non-pass terminus (`killed`, `refused`, `needs-operator`, `needs-reground`,
`dedupe-skip`), which the denylist never learned about when the v2 taxonomy was added beside it.
This module proves the fix: an explicit PASS allowlist (`PASS_VERDICTS`) is now the single verdict
predicate, `--verdict` is mandatory on write, and the ledger stays append-only + byte-identical for
every row that predates the change.
"""
from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys

from conftest import REPO_ROOT, load_module

al = load_module("scripts/foundry_audit_ledger.py", "foundry_audit_ledger")

SPEC_SHA = "a" * 64

# The full v2 non-pass taxonomy plus the legacy v1 non-pass strings — every verdict that must
# read as "no audit found", never as clean (AC-ALAL-2).
_NONPASS_FOR_TEST = (
    "killed", "refused", "needs-operator", "needs-reground", "dedupe-skip",
    "fail", "rejected", "abandoned",
)

# AC-ALAL-1's explicit pass allowlist, verified against the module's own constant below.
_PASS_FOR_TEST = ("converged", "plateau-clean", "plateau-security", "plateau")


def _write_v2_row(ledger_path, verdict, spec_sha256=SPEC_SHA, run_id=None, kill_reason=None):
    os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
    row = {
        "schema_version": 2,
        "ts": "2026-09-18T00:00:00Z",
        "run_id": run_id or f"run-{verdict}",
        "tier": "T1",
        "rounds": 3,
        "findings": {"new": 0, "resolved": 0, "open": 0},
        "spec_ref": "specs/x.md",
        "spec_sha256": spec_sha256,
        "operator": "agent:test",
        "verdict": verdict,
    }
    if kill_reason:
        row["kill_reason"] = kill_reason
    with open(ledger_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def _write_v1_row(ledger_path, verdict, spec_sha256=SPEC_SHA):
    os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
    row = {"spec_ref": "specs/x.md", "spec_sha256": spec_sha256, "rounds": 3,
           "operator": "agent:test", "verdict": verdict}
    with open(ledger_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return row


# ==================================================================== AC-ALAL-1 ==== #

def test_find_audit_returns_row_only_for_pass_allowlist(tmp_path):
    """Every verdict in the explicit pass allowlist — including the v1 `plateau` alias — is
    found by `find_audit` (v1 rows via `record_audit`, v2 rows via a raw JSONL append; both
    shapes stay readable per AC-ALT-4)."""
    project_dir = str(tmp_path)
    ledger = al.ledger_path(project_dir)

    for verdict in _PASS_FOR_TEST:
        assert verdict in al.PASS_VERDICTS, f"{verdict!r} missing from PASS_VERDICTS"
        sha = f"{verdict}".ljust(64, "0")
        if verdict == "plateau":
            al.record_audit(spec_ref="specs/x.md", spec_sha256=sha, rounds=3,
                            operator="agent:test", verdict=verdict, project_dir=project_dir)
        else:
            _write_v2_row(ledger, verdict, spec_sha256=sha, run_id=f"pass-{verdict}")
        hit = al.find_audit(sha, project_dir=project_dir)
        assert hit is not None, f"find_audit found nothing for pass verdict {verdict!r}"
        assert hit["verdict"] == verdict


# ==================================================================== AC-ALAL-2 ==== #

def test_find_audit_returns_none_for_every_nonpass_verdict(tmp_path):
    """If the ONLY rows for a spec_sha256 carry a non-pass verdict — the full v2 non-pass
    taxonomy plus the legacy v1 non-pass strings — `find_audit` returns None."""
    project_dir = str(tmp_path)
    ledger = al.ledger_path(project_dir)

    for verdict in _NONPASS_FOR_TEST:
        assert verdict not in al.PASS_VERDICTS, f"{verdict!r} unexpectedly in PASS_VERDICTS"
        sha = f"{verdict}".ljust(64, "1")
        if verdict in ("fail", "rejected", "abandoned"):
            al.record_audit(spec_ref="specs/x.md", spec_sha256=sha, rounds=3,
                            operator="agent:test", verdict=verdict, project_dir=project_dir)
        else:
            kr = "watchdog" if verdict == "killed" else None
            _write_v2_row(ledger, verdict, spec_sha256=sha, run_id=f"nonpass-{verdict}",
                          kill_reason=kr)
        hit = al.find_audit(sha, project_dir=project_dir)
        assert hit is None, f"find_audit wrongly returned a row for non-pass verdict {verdict!r}"


# ==================================================================== AC-ALAL-3 ==== #

def test_audit_record_without_verdict_is_usage_error_and_writes_nothing(tmp_path):
    """Omitting `--verdict` is a usage error: the CLI exits non-zero and appends NOTHING to the
    ledger — a crashed/refused audit run must never silently default to a clean verdict."""
    project_dir = str(tmp_path)
    spec_path = os.path.join(project_dir, "spec.md")
    with open(spec_path, "w", encoding="utf-8") as fh:
        fh.write("# a spec\n\nsome content\n")

    ledger = al.ledger_path(project_dir)
    assert not os.path.isfile(ledger)

    env = dict(os.environ, CLAUDE_PROJECT_DIR=project_dir)
    r = subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "scripts", "foundry-audit-record.py"),
         "--spec", spec_path, "--rounds", "3", "--operator", "op_test"],
        capture_output=True, text=True, env=env,
    )
    assert r.returncode != 0, f"expected non-zero exit, got 0: stdout={r.stdout!r}"
    assert not os.path.isfile(ledger), "the ledger must not be created when --verdict is omitted"


def test_audit_record_with_explicit_verdict_still_succeeds(tmp_path):
    """Sanity companion to the usage-error test: supplying `--verdict` explicitly still writes
    exactly one row (the required flag is a floor, not a functional regression)."""
    project_dir = str(tmp_path)
    spec_path = os.path.join(project_dir, "spec.md")
    with open(spec_path, "w", encoding="utf-8") as fh:
        fh.write("# a spec\n\nsome content\n")

    env = dict(os.environ, CLAUDE_PROJECT_DIR=project_dir)
    r = subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "scripts", "foundry-audit-record.py"),
         "--spec", spec_path, "--rounds", "3", "--operator", "op_test",
         "--verdict", "plateau-clean"],
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    ledger = al.ledger_path(project_dir)
    assert os.path.isfile(ledger)
    rows = [json.loads(line) for line in open(ledger, encoding="utf-8") if line.strip()]
    assert len(rows) == 1
    assert rows[0]["verdict"] == "plateau-clean"


# ==================================================================== AC-ALAL-4 ==== #

def test_existing_ledger_rows_are_byte_identical_after_change(tmp_path):
    """Append-only invariant: every row present before this atom's change is byte-identical
    after it — reading the ledger (via `find_audit`, repeatedly, across pass and non-pass
    verdicts) never mutates a single byte of a pre-existing row, including non-pass rows."""
    project_dir = str(tmp_path)
    ledger = al.ledger_path(project_dir)

    al.record_audit(spec_ref="specs/legacy.md", spec_sha256="b" * 64, rounds=1,
                    operator="agent:test", verdict="plateau", project_dir=project_dir)
    al.record_audit(spec_ref="specs/legacy-fail.md", spec_sha256="c" * 64, rounds=1,
                    operator="agent:test", verdict="fail", project_dir=project_dir)
    _write_v2_row(ledger, "converged", spec_sha256="d" * 64, run_id="byte-identical-1")
    _write_v2_row(ledger, "killed", spec_sha256="e" * 64, run_id="byte-identical-2",
                  kill_reason="watchdog")

    before = open(ledger, "rb").read()

    al.find_audit("b" * 64, project_dir=project_dir)
    al.find_audit("c" * 64, project_dir=project_dir)
    al.find_audit("d" * 64, project_dir=project_dir)
    al.find_audit("e" * 64, project_dir=project_dir)
    al.find_audit("f" * 64, project_dir=project_dir)  # a miss, too

    after = open(ledger, "rb").read()
    assert after == before, "find_audit must never mutate an existing ledger row"


# ==================================================================== AC-ALAL-5 ==== #

def test_no_nonpass_denylist_remains_in_ledger_module():
    """The pass allowlist is the SINGLE verdict predicate in `foundry_audit_ledger.py` — no
    denylist of non-pass verdicts remains anywhere as a gate input."""
    assert not hasattr(al, "_NONPASS_VERDICTS"), (
        "a non-pass denylist constant must not exist as a module attribute")
    assert hasattr(al, "PASS_VERDICTS")
    assert isinstance(al.PASS_VERDICTS, frozenset)
    assert al.PASS_VERDICTS == frozenset(
        {"converged", "plateau-clean", "plateau-security", "plateau"})

    src = inspect.getsource(al.find_audit)
    assert "NONPASS" not in src, "find_audit must not reference a denylist symbol"
    assert "PASS_VERDICTS" in src, "find_audit must read the explicit pass allowlist"


# ============================================================= hardening (round-3 review) ==== #

def test_malformed_rounds_row_is_skipped_not_raised(tmp_path):
    """A row whose `rounds` is not int-parsable (missing, `None`, a non-numeric string, a list)
    must be treated as NON-matching — skipped, convict direction — never raise ValueError/
    TypeError out of `find_audit` and hard-abort the caller (`foundry-authorize.py`) on junk
    ledger data. A later well-formed row for the same spec_sha256 must still be found."""
    project_dir = str(tmp_path)
    ledger = al.ledger_path(project_dir)
    sha = "9" * 64

    os.makedirs(os.path.dirname(ledger), exist_ok=True)
    malformed_rows = [
        {"schema_version": 2, "ts": "2026-09-18T00:00:00Z", "run_id": "malformed-none",
         "tier": "T1", "rounds": None, "findings": {"new": 0, "resolved": 0, "open": 0},
         "spec_ref": "specs/x.md", "spec_sha256": sha, "operator": "agent:test",
         "verdict": "converged"},
        {"schema_version": 2, "ts": "2026-09-18T00:00:00Z", "run_id": "malformed-str",
         "tier": "T1", "rounds": "not-a-number",
         "findings": {"new": 0, "resolved": 0, "open": 0}, "spec_ref": "specs/x.md",
         "spec_sha256": sha, "operator": "agent:test", "verdict": "converged"},
        {"schema_version": 2, "ts": "2026-09-18T00:00:00Z", "run_id": "malformed-list",
         "tier": "T1", "rounds": [1, 2], "findings": {"new": 0, "resolved": 0, "open": 0},
         "spec_ref": "specs/x.md", "spec_sha256": sha, "operator": "agent:test",
         "verdict": "converged"},
        # `rounds` key entirely absent.
        {"schema_version": 2, "ts": "2026-09-18T00:00:00Z", "run_id": "malformed-missing",
         "tier": "T1", "findings": {"new": 0, "resolved": 0, "open": 0},
         "spec_ref": "specs/x.md", "spec_sha256": sha, "operator": "agent:test",
         "verdict": "converged"},
    ]
    with open(ledger, "a", encoding="utf-8") as fh:
        for row in malformed_rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")

    # No exception, and none of the malformed rows count as a match (min_rounds=1 default,
    # but "missing" coerces to 0 via the historical `rounds=0` default, which still fails
    # `>= 1`; the point is nothing here raises and nothing here is wrongly found).
    hit = al.find_audit(sha, project_dir=project_dir)
    assert hit is None

    # A subsequent WELL-FORMED row for the same spec_sha256 is still found — one malformed row
    # never poisons the lookup for the rest of the ledger.
    _write_v2_row(ledger, "converged", spec_sha256=sha, run_id="well-formed-after-malformed")
    hit = al.find_audit(sha, project_dir=project_dir)
    assert hit is not None
    assert hit["run_id"] == "well-formed-after-malformed"
