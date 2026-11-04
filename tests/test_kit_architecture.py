"""Governance kits architecture / stack / contracts (gv-05)."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
KITS = ROOT / "governance" / "kits"
FIX = ROOT / "tests" / "fixtures" / "governance"
GOV = ROOT / "scripts" / "foundry_governance.py"
MINE = ("architecture", "stack", "contracts")
ADR_LINT = KITS / "architecture" / "checks" / "adr_lint.py"
BINDINGS_PY = KITS / "architecture" / "checks" / "bindings.py"
REGO = KITS / "stack" / "checks" / "pinning.rego"
SHA_USES = re.compile(r"@[0-9a-f]{40}(\s|$)")


def run(*cmd):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True)


def rules_of(kit):
    return yaml.safe_load((KITS / kit / "rules.yaml").read_text())["rules"]


@pytest.fixture
def plugin(tmp_path):
    """A plugin root holding only the three kits, so sibling kits cannot affect the result."""
    root = tmp_path / "plugin"
    for k in MINE:
        shutil.copytree(KITS / k, root / "governance" / "kits" / k)
    shutil.copytree(ROOT / "schema", root / "schema")
    (root / "scripts").mkdir()
    for f in (ROOT / "scripts").glob("foundry_*.py"):
        shutil.copy(f, root / "scripts" / f.name)
    return root


def gov(plugin, proj, *args):
    return run(sys.executable, plugin / "scripts" / "foundry_governance.py", "--plugin-root", plugin,
               "--project-dir", proj, *args)


# ---------------------------------------------------------------- validate

def test_three_kits_validate_with_zero_findings(plugin, tmp_path):
    proj = tmp_path / "proj"
    (proj / "governance" / "local").mkdir(parents=True)
    r = gov(plugin, proj, "validate")
    assert r.returncode == 0 and r.stdout.strip() == "governance-valid", r.stderr


def test_real_tree_validates():
    r = run(sys.executable, GOV, "--plugin-root", ROOT, "--project-dir", ROOT, "validate")
    assert r.returncode == 0, r.stderr


def test_rule_counts_and_id_families():
    ids = {k: [r["id"] for r in rules_of(k)] for k in MINE}
    assert len(ids["architecture"]) == 8 and all(i.startswith("FG-ARCH-") for i in ids["architecture"])
    assert len(ids["stack"]) == 6 and all(i.startswith("FG-STK-") for i in ids["stack"])
    fams = {i.rsplit("-", 1)[0] for i in ids["contracts"]}
    assert fams == {"FG-API", "FG-EVT", "FG-DATA", "FG-CDC"}


@pytest.mark.parametrize("kit", MINE)
def test_every_rule_binds_a_real_check_and_has_sources(kit):
    for r in rules_of(kit):
        ck = r["check"]
        assert ck["kind"] in ("review-lens", "none", "declared") or ck.get("ref"), r["id"]
        assert re.search(r"\b(MUST|SHOULD|MAY)\b", r["statement"]), r["id"]
        assert r["exception"]["allowed"] is True and r["sources"], r["id"]
        assert any("2026-11-04" in s for s in r["sources"]), r["id"]
        ref = ck.get("ref", "")
        if ref.startswith(("governance/", "agents/")):
            assert (ROOT / ref.split("#")[0]).is_file(), f"{r['id']}: {ref} does not exist"


@pytest.mark.parametrize("kit", MINE)
def test_kit_manifest_lists_only_existing_files_and_all_of_them(kit):
    d = yaml.safe_load((KITS / kit / "kit.yaml").read_text())
    listed = set(d["checks"]) | set(d["templates"])
    for rel in listed:
        assert (KITS / kit / rel).is_file(), rel
    on_disk = {str(p.relative_to(KITS / kit)) for sub in ("checks", "templates") for p in (KITS / kit / sub).rglob("*") if p.is_file()}
    assert on_disk == listed


def test_render_prints_what_the_kit_declares(plugin, tmp_path):
    proj = tmp_path / "proj"
    (proj / "governance" / "local").mkdir(parents=True)
    assert gov(plugin, proj, "render").returncode == 0
    text = (proj / "governance" / "rendered" / "architecture.md").read_text()
    assert "checks/bindings.yaml#layers" in text and "FG-ARCH-005" in text


# ---------------------------------------------------------------- bindings

def test_bindings_table_covers_the_four_ecosystems():
    t = yaml.safe_load((KITS / "architecture" / "checks" / "bindings.yaml").read_text())["ecosystems"]
    assert {e: t[e]["tool"] for e in ("python", "typescript", "java", "monorepo")} == {
        "python": "import-linter", "typescript": "dependency-cruiser", "java": "ArchUnit", "monorepo": "nx-boundaries"}
    assert t["javascript"]["tool"] == "dependency-cruiser"
    for e in t.values():
        assert (KITS / "architecture" / e["template"]).is_file()


@pytest.mark.parametrize("eco,tool", [("python", "import-linter"), ("typescript", "dependency-cruiser"),
                                      ("java", "ArchUnit"), ("monorepo", "nx-boundaries")])
def test_bound_ecosystem_renders_its_tool(eco, tool):
    r = run(sys.executable, BINDINGS_PY, eco)
    assert r.returncode == 0 and r.stdout.strip() == tool


@pytest.mark.parametrize("eco", ["go", "rust", "cobol"])
def test_unbound_ecosystem_reports_no_check_never_passes_silently(eco):
    r = run(sys.executable, BINDINGS_PY, eco)
    assert r.returncode == 1 and r.stdout.strip() == f"no-check:{eco}"


def test_tool_bound_rules_exist_and_are_ci_gated():
    t = yaml.safe_load((KITS / "architecture" / "checks" / "bindings.yaml").read_text())
    by_id = {r["id"]: r for r in rules_of("architecture")}
    for rid in t["tool_bound_rules"]:
        assert by_id[rid]["check"]["kind"] == "ci"


def test_baseline_template_matches_the_baseline_verb_shape():
    b = json.loads((KITS / "architecture" / "templates" / "architecture-baseline.json").read_text())
    assert set(b) == {"check", "findings"} and isinstance(b["findings"], list)


# ---------------------------------------------------------------- ADR lint

def adr(name):
    return run(sys.executable, ADR_LINT, FIX / "adr" / name)


def test_adr_lint_control_passes():
    r = adr("control")
    assert r.returncode == 0, r.stdout


@pytest.mark.parametrize("case,needle", [
    ("missing-status", "0001-no-status.md: missing status"),
    ("reused-number", "0001-second.md: number 0001 reused"),
    ("broken-supersede", "0001-old.md: broken superseded-by 0009"),
])
def test_adr_lint_fails_naming_the_file(case, needle):
    r = adr(case)
    assert r.returncode == 1 and needle in r.stdout


def test_adr_lint_requires_confirmation_check_id_on_accepted(tmp_path):
    (tmp_path / "0001-x.md").write_text("---\nstatus: accepted\n---\n# x\n")
    r = run(sys.executable, ADR_LINT, tmp_path)
    assert r.returncode == 1 and "0001-x.md: accepted without confirmation.check_id" in r.stdout


def test_adr_lint_refuses_a_missing_directory(tmp_path):
    assert run(sys.executable, ADR_LINT, tmp_path / "nope").returncode == 2


def test_adr_template_is_skipped_and_has_the_binding_field():
    d = KITS / "architecture" / "templates" / "docs" / "decisions"
    assert run(sys.executable, ADR_LINT, d).returncode == 0
    assert "check_id" in (d / "0000-template.md").read_text()


# ---------------------------------------------------------------- stack: pure python SHA check

def uses_of(path):
    doc = yaml.safe_load(Path(path).read_text())
    return [s["uses"] for j in doc["jobs"].values() for s in j.get("steps", []) if s.get("uses") and not s["uses"].startswith("./")]


def workflow_templates():
    return sorted(p for k in MINE for sub in ("checks", "templates") for p in (KITS / k / sub).rglob("*.yml"))


def test_there_are_workflow_templates_to_check():
    assert len(workflow_templates()) >= 7


@pytest.mark.parametrize("path", workflow_templates(), ids=lambda p: p.name)
def test_shipped_workflow_templates_pin_actions_to_a_sha(path):
    refs = uses_of(path)
    assert all(re.search(r"@[0-9a-f]{40}$", u) for u in refs), refs


def test_pure_python_sha_check_flags_a_floating_tag():
    assert uses_of(FIX / "stack" / "bad-workflow.yaml") == ["actions/checkout@v5"]
    assert not re.search(r"@[0-9a-f]{40}$", uses_of(FIX / "stack" / "bad-workflow.yaml")[0])
    assert all(re.search(r"@[0-9a-f]{40}$", u) for u in uses_of(FIX / "stack" / "good-workflow.yaml"))


VERSION_BEARING = [p for k in MINE for sub in ("checks", "templates") for p in (KITS / k / sub).rglob("*")
                   if p.is_file() and p.suffix in (".yml", ".yaml", ".json", ".md", ".ini", ".cjs", ".java")
                   and p.name not in ("scorecard-mapping.md", "0000-template.md", "architecture-baseline.json")]


@pytest.mark.parametrize("path", VERSION_BEARING, ids=lambda p: str(p.relative_to(KITS)))
def test_version_bearing_templates_carry_a_research_timestamp(path):
    assert re.search(r"researched\W+2026-11-04", path.read_text(), re.I), path


def test_renovate_template_extends_a_preset_pinned_by_tag():
    d = json.loads((KITS / "stack" / "templates" / "renovate.json").read_text())
    presets = [e for e in d["extends"] if e.startswith("github>")]
    assert presets and all(re.search(r"#\d+\.\d+\.\d+$", e) for e in presets)


# ---------------------------------------------------------------- stack: rego

PRIVATE_LITERALS = ("agentic", "lukasrepublic", "foundry", "karpenter", "observability", "standing-versions.md")


def planted(text):
    low = text.lower()
    return [x for x in PRIVATE_LITERALS if x in low]


def test_ported_rego_carries_no_product_or_workspace_literal():
    assert planted(REGO.read_text()) == []
    for p in (KITS / "stack").rglob("*"):
        if p.is_file() and p.suffix in (".rego", ".json", ".yml", ".md") and p.name != "scorecard-mapping.md":
            assert "lukasrepublic" not in p.read_text(), p


def test_planted_literal_control_detects_a_planted_name():
    assert planted(REGO.read_text() + "\n# see agentic-workspace docs\n") == ["agentic"]


CONFTEST = shutil.which("conftest")


def conftest(fixture):
    r = run(CONFTEST, "test", "--policy", REGO.parent, "--namespace", "standing.versions", "-o", "json", FIX / "stack" / fixture)
    return r.returncode, json.loads(r.stdout)[0]


@pytest.mark.skipif(CONFTEST is None, reason="conftest binary not on PATH; rego evaluation needs it")
@pytest.mark.parametrize("fixture", ["good-workflow.yaml", "good-deployment.yaml", "good-application.yaml"])
def test_rego_passes_pinned_fixtures(fixture):
    rc, res = conftest(fixture)
    assert rc == 0 and not res.get("failures")


@pytest.mark.skipif(CONFTEST is None, reason="conftest binary not on PATH; rego evaluation needs it")
@pytest.mark.parametrize("fixture,needle", [("bad-workflow.yaml", "not SHA-pinned"),
                                            ("bad-deployment.yaml", "not immutably pinned"),
                                            ("bad-application.yaml", "not an exact pin")])
def test_rego_denies_unpinned_fixtures(fixture, needle):
    rc, res = conftest(fixture)
    assert rc == 1 and any(needle in f["msg"] for f in res["failures"])
