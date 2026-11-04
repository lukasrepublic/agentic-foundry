"""gv-06: the quality, delivery and operations kits validate, bind real primitives, and ship a working quarantine check."""
import io
import re
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

TOK = "c" + "tx"
KITS = ("quality", "delivery", "operations")
FIX = ROOT / "tests" / "fixtures" / "governance"
CHECK = ROOT / "governance" / "kits" / "quality" / "checks" / "quarantine_check.py"

# Floor rules and their bound primitive. The planned ones are shipped by gv-09, gv-10 and gv-13.
QUEUE = "scripts/foundry_queue_land.py"
FLOOR_REFS = {
    "FG-QA-001": "agents/pr-reviewer.md",
    "FG-QA-006": "scripts/foundry-ticket.py",
    "FG-DLV-001": "hooks/foundry-git-discipline.sh",
    "FG-DLV-004": QUEUE,
    "FG-DLV-014": "hooks/foundry-git-discipline.sh",
    "FG-DLV-010": "hooks/foundry-paper-guard.py",
    "FG-DLV-011": QUEUE,
    **{f"FG-DLV-{n:03d}": QUEUE for n in (12, 13, 15, 16, 17, 18, 19)},
    "FG-OPS-004": "scripts/foundry_prod_step.py",
    "FG-OPS-005": "scripts/foundry_message_log.py",
}
PLANNED = {QUEUE, "scripts/foundry_prod_step.py", "scripts/foundry_message_log.py", "scripts/foundry_merge_plan.py"}


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
    assert counts == {"quality": 6, "delivery": 19, "operations": 8}


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
                assert not re.search(r"\b" + TOK, f.read_text(), re.I), f


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


# one positive path per glob family of the CI-edit rule
CI_PATHS = [
    ".github/workflows/ci.yml", ".github/CODEOWNERS", ".gitlab-ci.yml", ".circleci/config.yml", "Jenkinsfile",
    "azure-pipelines.yml", "scripts/ci/run.sh", "ci/build.sh", "Makefile", "pyproject.toml", "setup.cfg",
    ".coveragerc", "pytest.ini", "tests/conftest.py", "tox.ini", "web/jest.config.js", "web/vitest.config.ts",
    "web/playwright.config.ts", "web/karma.conf.js", ".mocharc.json", "tests/quarantine.yaml",
    "tests/quarantine.yml", "tests/quarantine-list.json",
]


@pytest.mark.parametrize("path", CI_PATHS)
def test_ci_edit_rule_applies_to_each_glob_family(plugin, path):
    rc, out, _ = run(*plugin, "applicable", "--paths", path, "--json")
    assert rc == 0 and "FG-DLV-010" in json.loads(out), path


def test_every_statement_has_an_rfc2119_keyword_and_kit_ids_match():
    for k in KITS:
        base = ROOT / "governance" / "kits" / k
        rules = yaml.safe_load((base / "rules.yaml").read_text())["rules"]
        kit = yaml.safe_load((base / "kit.yaml").read_text())
        assert [r["id"] for r in rules] == kit["rules"]
        for r in rules:
            assert re.search(r"\b(MUST|MUST NOT|SHOULD|SHOULD NOT|MAY)\b", r["statement"]), r["id"]


def test_floor_statements_carry_no_exception_wording():
    floors = [r for r in load_rules().values() if r["floor"]]
    assert floors
    for r in floors:
        low = r["statement"].lower()
        for word in ("unless", "except", "allowed only"):
            assert word not in low, f"{r['id']}: {word}"


def test_dlv_014_binds_the_git_discipline_hook():
    r = load_rules()["FG-DLV-014"]
    assert r["check"] == {"kind": "hook", "ref": "hooks/foundry-git-discipline.sh"}


def test_notes_flag_lenses_added_by_gv16():
    rules = load_rules()
    for i in ("FG-QA-001", "FG-DLV-008", "FG-OPS-008"):
        assert "gv-16" in rules[i]["note"], i


def test_pillar_map_entries_are_proxies_without_self_reference():
    pm = yaml.safe_load((ROOT / "governance/kits/operations/checks/pillar-map.yaml").read_text())["pillars"]
    assert pm and all(e["proxy"] is True and e["fact"] for e in pm)
    assert not any(p.startswith("governance/kits") for e in pm for p in e["paths"])
    own = next(e for e in pm if "CODEOWNERS" in e["fact"])
    assert {".github/CODEOWNERS", "docs/CODEOWNERS"} <= set(own["paths"])


def run_check(fixture, today="2026-11-04"):
    p = subprocess.run([sys.executable, str(CHECK), str(FIX / fixture), "--today", today], capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def test_quarantine_control_passes():
    assert run_check("quarantine_ok.json") == (0, "", "")


def test_quarantine_missing_field_fails_naming_it():
    rc, out, _ = run_check("quarantine_missing_owner.json")
    assert rc == 1 and out.splitlines() == ["tests/test_a.py::test_x: owner: missing"]


def test_quarantine_expired_reported():
    rc, out, _ = run_check("quarantine_expired.json")
    assert rc == 1 and out.splitlines() == ["tests/test_a.py::test_x: expiry: expired on 2026-06-01"]


def test_quarantine_unquoted_yaml_dates_expired():
    rc, out, _ = run_check("quarantine_expired_unquoted.yaml")
    assert rc == 1 and out.splitlines() == ["tests/test_a.py::test_x: expiry: expired on 2026-06-01"]


def test_quarantine_expiry_today_passes():
    assert run_check("quarantine_expiry_today.yaml") == (0, "", "")


def test_quarantine_bad_entry_named():
    rc, out, _ = run_check("quarantine_bad_entry.json")
    assert rc == 1 and out.splitlines() == ["tests/test_a.py::test_x: entry: not a YYYY-MM-DD date"]


def test_quarantine_unknown_key_rejected():
    rc, out, _ = run_check("quarantine_extra_key.json")
    assert rc == 1 and out.splitlines() == ["tests/test_a.py::test_x: skip: unknown field"]


def test_quarantine_expiry_before_entry_rejected(tmp_path):
    f = tmp_path / "q.json"
    f.write_text(json.dumps({"quarantine": [{"test": "t", "owner": "o", "issue": "i", "entry": "2026-10-02", "expiry": "2026-10-01"}]}))
    p = subprocess.run([sys.executable, str(CHECK), str(f), "--today", "2026-10-01"], capture_output=True, text=True)
    assert p.returncode == 1 and p.stdout.splitlines() == ["t: expiry: before entry"]


def test_quarantine_broken_yaml_exits_2_typed_no_traceback():
    rc, out, err = run_check("quarantine_broken.yaml")
    assert rc == 2 and out == "" and err.startswith("refused: yaml-parse-error:") and "Traceback" not in err


def test_quarantine_missing_pyyaml_exits_2(tmp_path):
    runner = tmp_path / "run.py"
    runner.write_text("import sys\nsys.modules['yaml'] = None\nimport runpy\nsys.argv = ['q', sys.argv[1]]\n"
                      "runpy.run_path(%r, run_name='__main__')\n" % str(CHECK))
    p = subprocess.run([sys.executable, str(runner), str(FIX / "quarantine_expiry_today.yaml")], capture_output=True, text=True)
    assert p.returncode == 2 and p.stderr.startswith("refused: pyyaml-unavailable") and "Traceback" not in p.stderr


def test_quarantine_schema_agrees_with_check():
    schema = json.loads((ROOT / "governance/kits/quality/checks/quarantine.schema.json").read_text())
    v = jsonschema.Draft202012Validator(schema)
    assert not list(v.iter_errors(json.loads((FIX / "quarantine_ok.json").read_text())))
    for bad in ("quarantine_missing_owner.json", "quarantine_bad_entry.json", "quarantine_extra_key.json"):
        assert list(v.iter_errors(json.loads((FIX / bad).read_text()))), bad
