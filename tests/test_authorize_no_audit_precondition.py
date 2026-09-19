"""tests/test_authorize_no_audit_precondition.py — feat-foundry-authorization-authorize-drops-
audit-precondition (AC-ADAP-1..4, AC-ADAP-6).

Drives `scripts/foundry-authorize.py` end-to-end (subprocess, hermetic fixture — same pattern
as `test_doc_claims.py`'s `_run_authorize_degrade_fixture`) to prove the §8 audit-ledger row is
no longer a precondition of the freeze: a missing row, a non-passing-verdict row, and the
deprecated `--skip-audit-reason` flag all leave the freeze unaffected. AC-ADAP-5 (the existing
64-test authz suite + `test_doc_claims.py::test_control_plane_authorize_degrade` staying green)
is exercised by simply running those suites unchanged — nothing here duplicates them.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from conftest import REPO_ROOT, load_module

fc = load_module("scripts/foundry_contract.py", "foundry_contract")

SCRIPT = Path(REPO_ROOT) / "scripts" / "foundry-authorize.py"


def _write_fixture(fixture: Path) -> tuple[Path, Path]:
    """Build a hermetic project fixture (operator registry + an unresolvable target_repo, so
    the venue-grounding floors degrade to a disclosed warn-skip rather than needing a real
    cloned product repo — mirrors test_doc_claims.py's own authorize fixture) and return
    (spec_path, contract_path)."""
    (fixture / ".claude").mkdir(parents=True)
    (fixture / ".claude" / "foundry-operators.json").write_text(json.dumps({
        "schema_version": 1,
        "operators": {"op_adap": {"name": "T", "github": "t", "added_at": "2026-01-01"}},
    }), encoding="utf-8")
    (fixture / ".claude" / "foundry-project.json").write_text(
        json.dumps({"schema_version": 1, "repos": {}}), encoding="utf-8",
    )
    specs_dir = fixture / "specs"
    specs_dir.mkdir()
    spec_path = specs_dir / "adap-probe.md"
    contract_path = specs_dir / "adap-probe.yaml"
    spec_path.write_text(
        "# ADAP probe (feat-adap-probe)\n\n"
        "<!-- normative -->\n## Acceptance criteria\n\n"
        "- **AC-ADP-1**: the probe surface returns hi.\n"
        "<!-- /normative -->\n",
        encoding="utf-8",
    )
    contract_path.write_text(
        "spec_ref: specs/adap-probe.md\n"
        'spec_sha256: "' + "0" * 64 + '"\n'
        "target_repo: unresolvable-ghost-key\n"
        "scope:\n  allowed_paths: [\"src/**\"]\n"
        "checkpoints:\n"
        "  - ac_id: AC-ADP-1\n"
        "    surface: \"cli:test\"\n"
        "    locator: \"echo hi\"\n"
        "    expect: {op: matches, value: \"hi\", baseline: pre-change}\n",
        encoding="utf-8",
    )
    return spec_path, contract_path


def _run(fixture: Path, spec_path: Path, contract_path: Path, extra_args: list[str],
          yes: bool) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(fixture)
    env["FOUNDRY_OPERATOR"] = "op_adap"
    args = [sys.executable, str(SCRIPT), "--spec", str(spec_path), "--contract", str(contract_path),
            "--operator", "op_adap", "--mode", "lean", *extra_args]
    if yes:
        args.append("--yes")
    return subprocess.run(args, cwd=str(fixture), capture_output=True, text=True, timeout=60, env=env)


def test_yes_freezes_without_audit_row():
    """AC-ADAP-1: no matching row in .foundry/audit-ledger.jsonl (the file does not even exist)
    — `--yes` still proceeds to the freeze, exit 0, `authorized:` trailer written."""
    with tempfile.TemporaryDirectory(prefix="adap-") as td:
        fixture = Path(td)
        spec_path, contract_path = _write_fixture(fixture)
        assert not (fixture / ".foundry" / "audit-ledger.jsonl").exists()
        proc = _run(fixture, spec_path, contract_path, [], yes=True)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        doc = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        auth_seq = ((doc or {}).get("authorized") or {}).get("auth_seq")
        assert isinstance(auth_seq, int) and auth_seq >= 1, proc.stdout + proc.stderr


def test_dry_run_prints_no_enforcement_warning():
    """AC-ADAP-2: dry-run (no --yes) over a spec with no audit-ledger row prints no
    AUDIT-ENFORCEMENT warning and does not name /foundry:audit or --skip-audit-reason as a
    required next step."""
    with tempfile.TemporaryDirectory(prefix="adap-") as td:
        fixture = Path(td)
        spec_path, contract_path = _write_fixture(fixture)
        proc = _run(fixture, spec_path, contract_path, [], yes=False)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out
        assert "AUDIT-ENFORCEMENT" not in out, out
        assert "--skip-audit-reason" not in out, out
        assert "/foundry:audit" not in out, out


def test_nonpass_audit_row_is_informational():
    """AC-ADAP-3: an audit-ledger row exists for this spec's content hash with a verdict outside
    the passing set (verdict=killed) — `--yes` still proceeds to the freeze and prints the
    informational line naming that verdict."""
    with tempfile.TemporaryDirectory(prefix="adap-") as td:
        fixture = Path(td)
        spec_path, contract_path = _write_fixture(fixture)
        spec_hash = fc.spec_sha256(str(spec_path))
        ledger_path = fixture / ".foundry" / "audit-ledger.jsonl"
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(json.dumps({
            "spec_ref": "specs/adap-probe.md",
            "spec_sha256": spec_hash,
            "rounds": 1,
            "operator": "op_adap",
            "verdict": "killed",
        }) + "\n", encoding="utf-8")
        proc = _run(fixture, spec_path, contract_path, [], yes=True)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out
        assert "§8 audit: recorded (verdict=killed) — informational" in out, out


def test_skip_audit_reason_is_deprecated_noop():
    """AC-ADAP-4: `--skip-audit-reason` is accepted; stdout carries the 'SKIPPED' token + 'no
    effect'; security-audit.jsonl gains exactly one `authorize-audit-flag-deprecated` record with
    the reason, and no `authorize-audit-skip` record."""
    with tempfile.TemporaryDirectory(prefix="adap-") as td:
        fixture = Path(td)
        spec_path, contract_path = _write_fixture(fixture)
        reason = "operator override — AC-ADAP-4 fixture"
        proc = _run(fixture, spec_path, contract_path, ["--skip-audit-reason", reason], yes=True)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out
        assert "SKIPPED" in out, out
        assert "no effect" in out, out

        audit_log = fixture / ".foundry" / "security-audit.jsonl"
        records = [json.loads(line) for line in audit_log.read_text(encoding="utf-8").splitlines() if line.strip()]
        deprecated = [r for r in records if r.get("action") == "authorize-audit-flag-deprecated"]
        skip = [r for r in records if r.get("action") == "authorize-audit-skip"]
        assert len(deprecated) == 1, records
        assert deprecated[0].get("reason") == reason, deprecated
        assert skip == [], records


def test_skill_doc_has_no_precondition_language():
    """AC-ADAP-6: skills/authorize/SKILL.md contains no statement that an audit-ledger row is a
    precondition of authorization, and states that /foundry:audit is operator-invoked only and
    the ledger is informational."""
    text = (Path(REPO_ROOT) / "skills" / "authorize" / "SKILL.md").read_text(encoding="utf-8")
    lowered = text.lower()
    # No sentence claiming find_audit / the ledger row is a precondition of authorization.
    assert "find_audit" in lowered
    for bad in ("find_audit) is\n   unchanged and is the **normal path**",
                "fail-closes on a spec with no matching"):
        assert bad not in lowered, f"stale precondition language survived: {bad!r}"
    assert "informational" in lowered
    assert "operator-invoked only" in lowered
