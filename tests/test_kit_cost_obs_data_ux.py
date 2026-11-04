"""gv-07: cost, observability, data, compliance and ux kits."""
import importlib.util
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import foundry_governance as gov  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "governance"
KITS = {"cost": "COST", "observability": "OBS", "data": "DATA", "compliance": "LIC", "ux": "UX"}
LOCK_SCHEMA = json.loads((ROOT / "schema" / "governance-lock.schema.json").read_text())


def kit_rules(name):
    return yaml.safe_load((ROOT / "governance" / "kits" / name / "rules.yaml").read_text())["rules"]


def run(*argv, project=None):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = gov.main(["--plugin-root", str(ROOT), "--project-dir", str(project or ROOT / "nonexistent"), *argv])
    return rc, out.getvalue(), err.getvalue()


def test_five_kits_validate_with_zero_findings():
    rc, out, _ = run("validate")
    assert rc == 0 and "governance-valid" in out
    kits = {k["dir"]: k for k in gov.load_kits(ROOT)}
    for name, prefix in KITS.items():
        assert name in kits
        assert gov.validate_all([kits[name]], {"rules": [], "overrides": [], "exceptions": []}) == []
        for r in kit_rules(name):
            assert r["id"].startswith(f"FG-{prefix}-") and r["category"] == name
            assert r["sources"] and all("2026-11-04" in s for s in r["sources"])
            assert r["check"]["ref"]


def test_kit_declared_files_exist():
    for name in KITS:
        d = ROOT / "governance" / "kits" / name
        k = yaml.safe_load((d / "kit.yaml").read_text())
        assert (d / "README.md").is_file()
        for rel in k["checks"] + k["templates"]:
            assert (d / rel).is_file(), f"{name}: {rel}"
        for r in kit_rules(name):
            ref = r["check"]["ref"]
            if ref.startswith("governance/kits/"):
                assert (ROOT / ref).is_file(), ref


def test_cost_001_is_a_floor_bound_to_priced_ask_and_scale_to_zero_template_taints():
    c1 = {r["id"]: r for r in kit_rules("cost")}["FG-COST-001"]
    assert c1["floor"] is True and c1["exception"]["allowed"] is False
    assert c1["check"]["ref"] == "scripts/foundry_blocker_check.py"
    np = yaml.safe_load_all((ROOT / "governance/kits/cost/templates/scale-to-zero-nodeclass.yaml").read_text())
    pool = next(d for d in np if d)
    assert pool["spec"]["template"]["spec"]["taints"][0]["effect"] == "NoSchedule"
    assert pool["spec"]["disruption"]["consolidationPolicy"] == "WhenEmpty"


def test_data_classification_enum_is_fixed():
    d = yaml.safe_load((ROOT / "governance/kits/data/templates/data-classification.yaml").read_text())
    assert d["classes"] == ["public", "internal", "confidential", "restricted"]
    assert set(d["handling"]) == set(d["classes"])


def test_compliance_kit_has_no_framework_text_or_private_names():
    for name in KITS:
        for f in (ROOT / "governance" / "kits" / name).rglob("*"):
            if f.is_file() and f.suffix != ".pyc":
                low = f.read_text().lower()
                for banned in ("soc 2", "soc2", "iso 27001", "iso27001", "pci-dss", "hipaa"):
                    assert banned not in low, f"{f}: {banned}"


# ---- license check ---------------------------------------------------------------------------- #

def _lic():
    spec = importlib.util.spec_from_file_location("lic", ROOT / "governance/kits/compliance/checks/license_policy_check.py")
    sys.dont_write_bytecode = True
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


POLICY = ROOT / "governance/kits/compliance/templates/license-policy.yaml"


def test_license_check_denies_unknown_license():
    lic = _lic()
    assert lic.main([str(POLICY), str(FIX / "packages-unknown.json")]) == 1
    bad = lic.evaluate(yaml.safe_load(POLICY.read_text()), json.loads((FIX / "packages-unknown.json").read_text()))
    assert len(bad) == 1 and "mystery-lib" in bad[0]


def test_license_check_passes_mit_and_dual_license_control():  # control
    assert _lic().main([str(POLICY), str(FIX / "packages-pass.json")]) == 0


def test_license_check_denies_empty_denied_and_honours_unexpired_exception():
    lic, pol = _lic(), yaml.safe_load(POLICY.read_text())
    assert lic.evaluate(pol, [{"name": "a", "license": ""}, {"name": "b", "license": "AGPL-3.0-only"}])
    pol["exceptions"] = [{"package": "b", "license": "AGPL-3.0-only", "reason": "r", "owner": "o", "expires": "2027-01-01"}]
    import datetime
    assert lic.evaluate(pol, [{"name": "b", "license": "AGPL-3.0-only"}], datetime.date(2026, 11, 4)) == []
    assert lic.evaluate(pol, [{"name": "b", "license": "AGPL-3.0-only"}], datetime.date(2027, 2, 1))


# ---- ux --------------------------------------------------------------------------------------- #

def surfaces(lock_name):
    lock = yaml.safe_load((FIX / lock_name).read_text())
    jsonschema.validate(lock, LOCK_SCHEMA)
    return lock["declarations"]["surfaces"]


def applicable_to(rule, surf):
    bps = rule["applies_to"]["blueprints"]
    return "*" in bps or any(s in bps for s in surf)


def test_ux_rules_restricted_to_ui_surface():
    rules = kit_rules("ux")
    assert rules and all(r["applies_to"]["blueprints"] == ["ui"] for r in rules)
    assert not any(applicable_to(r, surfaces("lock-noui.yaml")) for r in rules)
    assert all(applicable_to(r, surfaces("lock-ui.yaml")) for r in rules)  # control
    # every non-ux rule in the five kits stays universal
    for name in set(KITS) - {"ux"}:
        assert all(applicable_to(r, surfaces("lock-noui.yaml")) for r in kit_rules(name))


def test_render_emits_ux_category_from_shipped_kits(tmp_path):
    rc, _, _ = run("render", project=tmp_path)
    assert rc == 0 and (tmp_path / "governance/rendered/ux.md").is_file()


def test_ux_readme_states_wcag_coverage_is_a_minority_and_a_practice():
    text = " ".join((ROOT / "governance/kits/ux/README.md").read_text().split())
    assert "Automated accessibility checks cover a minority of WCAG" in text
    assert "is a practice, not a control" in text
