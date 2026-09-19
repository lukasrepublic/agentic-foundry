"""tests/test_amend.py — feat-foundry-authorization-amend-verb (AC-AMND-1..6).

Drives `scripts/foundry-amend.py` two ways: (1) direct import of its diff/classifier functions
(`load_module`, the sibling suites' own idiom) over throwaway dict fixtures for the pure
classification logic (AC-AMND-1), and (2) end-to-end subprocess runs over a hermetic git
fixture (mirrors `tests/test_repo_attach.py`'s `_git`/`_init_repo`/`_commit` idiom) for the
freeze/refusal behavior (AC-AMND-2..6), since amend diffs the working tree against the last
commit and a real `authorized:` trailer.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
import yaml

from conftest import REPO_ROOT, load_module

fc = load_module("scripts/foundry_contract.py", "foundry_contract")
az = load_module("scripts/foundry_authz.py", "foundry_authz")
amend = load_module("scripts/foundry-amend.py", "foundry_amend")

CLI = os.path.join(REPO_ROOT, "scripts", "foundry-amend.py")
AUTHORIZE_CLI = os.path.join(REPO_ROOT, "scripts", "foundry-authorize.py")


# =================================================================================================
# git/fixture helpers (the sibling suite's own idiom, tests/test_repo_attach.py)
# =================================================================================================
def _git(args, cwd):
    r = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {args} failed: {r.stderr}")
    return r


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q", "-b", "main"], path)
    _git(["config", "user.email", "t@example.com"], path)
    _git(["config", "user.name", "Test"], path)


def _commit_all(path, message="commit"):
    _git(["add", "-A"], path)
    _git(["commit", "-q", "-m", message], path)


SPEC_TEXT = """# Amend fixture (feat-amend-fixture)

<!-- normative -->
## Acceptance criteria

- **AC-FIX-1**: the probe surface returns hi.
<!-- /normative -->

## Clarifications

(none)

## Amendments

| date | what changed | why reality required it | auth_seq |
|---|---|---|---|
"""

CONTRACT_TEMPLATE = """spec_ref: specs/amend-fixture.md
spec_sha256: "{spec_hash}"
scope:
  allowed_paths:
    - "scripts/**"
checkpoints:
  - ac_id: AC-FIX-1
    surface: "cli:test"
    locator: "echo hi"
    expect: {{op: matches, value: "hi", baseline: pre-change}}
"""

CONTRACT_TEMPLATE_RICH = """spec_ref: specs/amend-fixture.md
spec_sha256: "{spec_hash}"
scope:
  allowed_paths:
    - "scripts/**"
checkpoints:
  - ac_id: AC-FIX-1
    surface: "cli:test"
    locator: "aws sts get-caller-identity → 875926135332"
    expect: {{op: count_gte, value: 4, baseline: pre-change}}
"""


def _build_authorized_fixture(tmp_path, contract_template=CONTRACT_TEMPLATE, spec_text=SPEC_TEXT):
    """A hermetic git repo with a spec + acceptance-contract.yaml already frozen at `auth_seq: 1`
    and committed — the baseline every scenario below edits from. A real `scripts/` dir is
    materialized so ER #179's allowed_paths reality-grounding floor (consulted by both amend's
    AC-AMND-4 floor call and, in the AC-AMND-5 case, a subsequent `foundry-authorize.py` dry-run)
    grounds `scope.allowed_paths: ["scripts/**"]` against something real."""
    _init_repo(tmp_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "placeholder.py").write_text("# placeholder\n", encoding="utf-8")

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir()
    spec_path = specs_dir / "amend-fixture.md"
    contract_path = specs_dir / "acceptance-contract.yaml"
    spec_path.write_text(spec_text, encoding="utf-8")
    spec_hash = fc.spec_sha256(str(spec_path))
    contract_path.write_text(contract_template.format(spec_hash=spec_hash), encoding="utf-8")

    block = az.authorize(
        spec_path=str(spec_path),
        contract_path=str(contract_path),
        operator_id="op_fixture",
        merge_autonomy_mode="lean",
        authorized_at="2026-09-18T00:00:00Z",
    )
    assert block["auth_seq"] == 1
    _commit_all(tmp_path, "initial authorization")
    return spec_path, contract_path


def _run_amend(tmp_path, spec_path, contract_path, extra_args=()):
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    args = [sys.executable, CLI, "--spec", str(spec_path), "--contract", str(contract_path),
            "--repo-root", str(tmp_path), *extra_args]
    return subprocess.run(args, cwd=str(tmp_path), capture_output=True, text=True,
                           timeout=60, env=env)


def _last_json_line(stdout):
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise AssertionError(f"no JSON record found in stdout:\n{stdout}")


# =================================================================================================
# AC-AMND-1 — the diff mechanism covers normative text (a), boundary fields (b), identifier
# tokens (c), and checkpoint-rigor reductions (d).
# =================================================================================================
class TestDiffReportsNormativeAndBoundaryFields:
    def test_diff_reports_normative_and_boundary_fields(self):
        old_data = {
            "scope": {"allowed_paths": ["scripts/**"]},
            "mandatory_review": [],
            "checkpoints": [
                {"ac_id": "AC-X-1", "surface": "cli:test",
                 "locator": "aws sts get-caller-identity → 875926135332",
                 "expect": {"op": "count_gte", "value": 4, "baseline": "pre-change"}},
            ],
        }
        new_data = {
            "scope": {"allowed_paths": ["scripts/**", "hooks/**"]},
            "mandatory_review": [{"review": "security"}],
            "checkpoints": [
                {"ac_id": "AC-X-1", "surface": "cli:test2",
                 "locator": "aws sts get-caller-identity → 362928919784",
                 "expect": {"op": "matches", "value": "x", "baseline": "none"}},
            ],
        }
        widened, summary = amend.compute_diff(old_data, new_data)

        assert "scope.allowed_paths" in widened, widened                        # (b) boundary
        assert "mandatory_review" in widened, widened                           # (b) boundary
        assert "checkpoints[AC-X-1].locator" in widened, widened                # (c) identifier
        assert any("aws-account-id" in line for line in summary), summary
        assert "checkpoints[AC-X-1].surface" in widened, widened                # (d) rigor
        assert "checkpoints[AC-X-1].expect.baseline" in widened, widened        # (d) rigor
        assert "checkpoints[AC-X-1].expect.op" in widened, widened              # (d) rigor

        # (a) the normative-region text diff is a plain textual diff, computed independently of
        # the boundary/identifier/rigor classifier above (it never itself signals widening).
        diff_text = amend.unified_diff_text(b"old normative text\n", b"new normative text\n")
        assert "-old normative text" in diff_text
        assert "+new normative text" in diff_text

    def test_unchanged_checkpoint_identifier_is_not_a_widening(self):
        """Design/notes: the identifier-token classifier is applied to the NEW value only — an
        identifier already present and UNCHANGED is not a widening."""
        cp = {"ac_id": "AC-X-1", "surface": "cli:test",
              "locator": "aws sts get-caller-identity → 875926135332",
              "expect": {"op": "count_gte", "value": 4, "baseline": "pre-change"}}
        old_data = {"checkpoints": [dict(cp)]}
        new_data = {"checkpoints": [dict(cp)]}
        widened, _ = amend.compute_diff(old_data, new_data)
        assert widened == []


# =================================================================================================
# AC-AMND-2 — non-widening re-freeze: new hashes, auth_seq+1, supersedes, an Amendments row, and
# ONE security-audit record naming spec + both hashes + auth_seq.
# =================================================================================================
class TestNonWideningRefreezesAndAppendsAuditRecord:
    def test_non_widening_refreezes_and_appends_audit_record(self, tmp_path):
        spec_path, contract_path = _build_authorized_fixture(tmp_path)

        text = spec_path.read_text(encoding="utf-8")
        spec_path.write_text(text.replace("returns hi.", "returns hi (case-insensitive)."),
                              encoding="utf-8")

        proc = _run_amend(tmp_path, spec_path, contract_path)
        assert proc.returncode == 0, proc.stdout + proc.stderr

        doc = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        block = doc["authorized"]
        assert block["auth_seq"] == 2
        assert block["supersedes"] is not None
        assert block["spec_sha256"] == fc.spec_sha256(str(spec_path))
        assert block["contract_sha256"] == fc.contract_sha256(str(contract_path))

        amendments_section = spec_path.read_text(encoding="utf-8").split("## Amendments", 1)[1]
        table_rows = [ln for ln in amendments_section.splitlines() if ln.strip().startswith("|")]
        assert len(table_rows) >= 3, table_rows  # header + separator + >=1 data row
        assert " 2 " in table_rows[-1] or table_rows[-1].strip().endswith("| 2 |"), table_rows[-1]

        audit_log = (tmp_path / ".foundry" / "security-audit.jsonl").read_text(encoding="utf-8")
        records = [json.loads(ln) for ln in audit_log.splitlines() if ln.strip()]
        complete = [r for r in records if r.get("action") == "amend-complete"]
        assert len(complete) == 1, records
        rec = complete[0]
        assert rec["spec_sha256"] == block["spec_sha256"]
        assert rec["contract_sha256"] == block["contract_sha256"]
        assert rec["auth_seq"] == 2

    def test_no_drift_is_idempotent_noop(self, tmp_path):
        spec_path, contract_path = _build_authorized_fixture(tmp_path)
        proc = _run_amend(tmp_path, spec_path, contract_path)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        doc = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        assert doc["authorized"]["auth_seq"] == 1


# =================================================================================================
# AC-AMND-3 — four independent refusals, each: contract left byte-unchanged, non-zero exit, a
# structured needs-operator record on stdout.
# =================================================================================================
def _mutate_scope_widen(text):
    return text.replace('    - "scripts/**"\n', '    - "scripts/**"\n    - "hooks/**"\n', 1)


def _mutate_add_security_review(text):
    return text.replace("scope:\n", "mandatory_review:\n  - review: security\nscope:\n", 1)


def _mutate_account_id_swap(text):
    assert "875926135332" in text
    return text.replace("875926135332", "362928919784")


def _mutate_expect_value_lowered(text):
    assert "value: 4" in text
    return text.replace("value: 4", "value: 2")


WIDENING_CASES = [
    pytest.param(CONTRACT_TEMPLATE, _mutate_scope_widen, "scope.allowed_paths",
                 id="scope-widening"),
    pytest.param(CONTRACT_TEMPLATE, _mutate_add_security_review, "mandatory_review",
                 id="mandatory-review-names-security"),
    pytest.param(CONTRACT_TEMPLATE_RICH, _mutate_account_id_swap,
                 "checkpoints[AC-FIX-1].locator", id="account-id-swap-in-locator"),
    pytest.param(CONTRACT_TEMPLATE_RICH, _mutate_expect_value_lowered,
                 "checkpoints[AC-FIX-1].expect.value", id="expect-value-lowered"),
]


class TestWideningRefusedWithStructuredRecord:
    @pytest.mark.parametrize("template,mutate,expected_field", WIDENING_CASES)
    def test_widening_refused_with_structured_record(self, tmp_path, template, mutate,
                                                       expected_field):
        spec_path, contract_path = _build_authorized_fixture(tmp_path, contract_template=template)
        mutated = mutate(contract_path.read_text(encoding="utf-8"))
        contract_path.write_text(mutated, encoding="utf-8")

        proc = _run_amend(tmp_path, spec_path, contract_path)
        assert proc.returncode != 0, proc.stdout + proc.stderr

        record = _last_json_line(proc.stdout)
        assert record["status"] == "needs-operator", record
        assert expected_field in record["widened_fields"], record
        assert record["remediation"] == f"/foundry:authorize {spec_path}", record
        assert isinstance(record["diff_summary"], str) and record["diff_summary"], record

        # the contract is left byte-unchanged (still exactly the mutated-but-unauthorized text we
        # wrote before invoking amend — the CLI itself wrote nothing).
        assert contract_path.read_text(encoding="utf-8") == mutated


# =================================================================================================
# AC-AMND-4 — the file:scripts/foundry-amend.py checkpoint's own literal-string requirement.
# =================================================================================================
def test_cli_source_calls_validate_spec_contract():
    text = open(CLI, encoding="utf-8").read()
    assert "validate_spec_contract" in text


# =================================================================================================
# AC-AMND-5 — after re-freeze, validate_spec_contract accepts the pair, hashes match the trailer,
# and a foundry-authorize dry-run reports it current.
# =================================================================================================
class TestRefrozenPairValidatesAndDryRunReportsCurrent:
    def test_refrozen_pair_validates_and_dry_run_reports_current(self, tmp_path):
        spec_path, contract_path = _build_authorized_fixture(tmp_path)
        text = spec_path.read_text(encoding="utf-8")
        spec_path.write_text(text.replace("returns hi.", "returns hi, robustly."),
                              encoding="utf-8")

        proc = _run_amend(tmp_path, spec_path, contract_path)
        assert proc.returncode == 0, proc.stdout + proc.stderr

        ok, errors, _warnings = az.validate_spec_contract(str(spec_path), str(contract_path))
        assert ok, errors

        doc = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        block = doc["authorized"]
        assert block["spec_sha256"] == fc.spec_sha256(str(spec_path))
        assert block["contract_sha256"] == fc.contract_sha256(str(contract_path))

        state, notes = az.spec_state(str(spec_path), str(contract_path))
        assert state == az.AUTHORIZED, notes

        (tmp_path / ".claude").mkdir(exist_ok=True)
        (tmp_path / ".claude" / "foundry-operators.json").write_text(json.dumps({
            "schema_version": 1,
            "operators": {"op_fixture": {"name": "T", "github": "t", "added_at": "2026-01-01"}},
        }), encoding="utf-8")
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
        proc2 = subprocess.run(
            [sys.executable, AUTHORIZE_CLI, "--spec", str(spec_path), "--contract", str(contract_path),
             "--operator", "op_fixture", "--mode", "lean"],
            cwd=str(tmp_path), capture_output=True, text=True, timeout=60, env=env,
        )
        assert proc2.returncode == 0, proc2.stdout + proc2.stderr
        assert "Already AUTHORIZED and hashes match" in proc2.stdout, proc2.stdout


# =================================================================================================
# AC-AMND-6 — no audit-ledger row required or consulted by amend.
# =================================================================================================
def test_cli_never_imports_the_audit_ledger():
    text = open(CLI, encoding="utf-8").read()
    assert "import foundry_audit_ledger" not in text
    assert "az.find_audit" not in text and "ledger.find_audit" not in text


def test_skill_states_no_audit_ledger_row_required():
    skill = os.path.join(REPO_ROOT, "skills", "amend", "SKILL.md")
    text = open(skill, encoding="utf-8").read().lower()
    assert "no audit-ledger row" in text, text
