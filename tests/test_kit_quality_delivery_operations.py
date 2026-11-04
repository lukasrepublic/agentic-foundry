"""gv-06: the quality, delivery and operations kits validate, bind real primitives, and ship a working quarantine check."""
import io
import json
import shutil
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import jsonschema
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import foundry_governance as gov  # noqa: E402

KITS = ("quality", "delivery", "operations")
FIX = ROOT / "tests" / "fixtures" / "governance"
CHECK = ROOT / "governance" / "kits" / "quality" / "checks" / "quarantine_check.py"

# Floor rules and their bound primitive. The planned ones are shipped by gv-09, gv-10 and gv-13.
FLOOR_REFS = {
    "FG-QA-001": "agents/pr-reviewer.md",
    "FG-QA-006": "skills/command-deck",
    "FG-DLV-001": "hooks/foundry-git-discipline.sh",
    "FG-DLV-004": "scripts/foundry_queue_land.py",
    "FG-DLV-010": "hooks/foundry-paper-guard.py",
    "FG-OPS-004": "scripts/foundry_prod_step.py",
    "FG-OPS-005": "scripts/foundry_message_log.py",
}
PLANNED = {"scripts/foundry_queue_land.py", "hooks/foundry-paper-guard.py", "scripts/foundry_prod_step.py",
           "scripts/foundry_message_log.py", "scripts/foundry_merge_plan.py"}


def load_rules():
    out = {}
    for k in KITS:
        for r in yaml.safe_load((ROOT / "governance" / "kits" / k / "rules.yaml").read_text())["rules"]:
            out[r["id"]] = r
    return out


@pytest.fixture
def plugin(tmp_path):
    for k in KITS:
        shutil.copytree(ROOT / "governance" / "kits" / k, tmp_path / "plugin" / "governance" / "kits" / k)
    (tmp_path / "proj").mkdir()
    return tmp_path / "plugin", tmp_path / "proj"


def run(plugin, proj, *argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = gov.main(["--plugin-root", str(plugin), "--project-dir", str(proj), *argv])
    return rc, out.getvalue(), err.getvalue()


def test_kits_validate_clean(plugin):
    rc, out, err = run(*plugin, "validate")
    assert rc == 0, out + err


def test_rule_counts():
    rules = load_rules()
    counts = {k: sum(1 for i in rules if i.startswith({"quality": "FG-QA-", "delivery": "FG-DLV-", "operations": "FG-OPS-"}[k])) for k in KITS}
    assert counts == {"quality": 6, "delivery": 10, "operations": 8}


def test_floor_rules_bind_exact_primitives():
    rules = load_rules()
    floors = {i: r["check"]["ref"] for i, r in rules.items() if r["floor"]}
    assert floors == FLOOR_REFS
    for i, r in rules.items():
        if r["floor"]:
            assert r["exception"] == {"allowed": False}
            ref = r["check"]["ref"]
            assert (ROOT / ref).exists() or ref in PLANNED, f"{i}: {ref} neither shipped nor planned"


def test_every_planned_ref_is_named():
    refs = {r["check"].get("ref") for r in load_rules().values()}
    assert PLANNED <= refs


def test_shipped_refs_exist():
    for i, r in load_rules().items():
        ref = r["check"].get("ref")
        if ref and ref not in PLANNED:
            assert (ROOT / ref).exists(), f"{i}: {ref} missing"


def test_rules_have_sources_dated_and_no_private_names():
    for k in KITS:
        base = ROOT / "governance" / "kits" / k
        for r in load_rules().values():
            assert r["sources"] and all("2026-11-04" in s for s in r["sources"]), r["id"]
        for f in base.rglob("*"):
            if f.is_file():
                assert "ctx" not in f.read_text().lower().split(), f


def test_kit_yaml_files_listed_exist():
    for k in KITS:
        kit = yaml.safe_load((ROOT / "governance" / "kits" / k / "kit.yaml").read_text())
        for rel in kit["checks"] + kit["templates"]:
            assert (ROOT / "governance" / "kits" / k / rel).is_file(), rel


def test_ci_edit_rule_is_applicable_to_workflow(plugin):
    rc, out, _ = run(*plugin, "applicable", "--paths", ".github/workflows/ci.yml", "--json")
    assert rc == 0
    assert "FG-DLV-010" in json.loads(out)


def test_ci_edit_rule_not_applicable_to_source(plugin):
    rc, out, _ = run(*plugin, "applicable", "--paths", "src/app.py", "--json")
    assert "FG-DLV-010" not in json.loads(out)


def run_check(fixture, today="2026-11-04"):
    p = subprocess.run([sys.executable, str(CHECK), str(FIX / fixture), "--today", today], capture_output=True, text=True)
    return p.returncode, p.stdout


def test_quarantine_control_passes():
    assert run_check("quarantine_ok.json") == (0, "")


def test_quarantine_missing_field_fails_naming_it():
    rc, out = run_check("quarantine_missing_owner.json")
    assert rc == 1 and "owner: missing" in out


def test_quarantine_expired_reported():
    rc, out = run_check("quarantine_expired.json")
    assert rc == 1 and "expired" in out


def test_quarantine_schema_agrees_with_check():
    schema = json.loads((ROOT / "governance/kits/quality/checks/quarantine.schema.json").read_text())
    v = jsonschema.Draft202012Validator(schema)
    assert not list(v.iter_errors(json.loads((FIX / "quarantine_ok.json").read_text())))
    assert list(v.iter_errors(json.loads((FIX / "quarantine_missing_owner.json").read_text())))
