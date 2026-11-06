"""dl-19: brownfield intake derives DRAFT architecture blueprints (scripts/foundry_architecture_derive.py) and the
model source lint (governance/kits/architecture/checks/model_source_lint.py). No network, no installed tools: graphs and
plans come in as pre-computed JSON through the flags."""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
FX = ROOT / "tests" / "fixtures" / "architecture-derive"
LINT = ROOT / "governance" / "kits" / "architecture" / "checks" / "model_source_lint.py"
ADR_LINT = ROOT / "governance" / "kits" / "architecture" / "checks" / "adr_lint.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    keep, sys.dont_write_bytecode = sys.dont_write_bytecode, True   # no __pycache__ inside the kit directory
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = keep
    return mod


D = _load(ROOT / "scripts" / "foundry_architecture_derive.py", "foundry_architecture_derive")
L = _load(LINT, "model_source_lint")


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True,
                   capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "shop"
    r.mkdir()
    (r / "pyproject.toml").write_text("[project]\nname='shop'\n")
    _git(r, "init", "-q")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "init")
    return r


def derive(repo, *flags, capsys=None):
    rc = D.main(["--repo", str(repo), "--json", *map(str, flags)])
    out = capsys.readouterr().out if capsys else ""
    return rc, (json.loads(out) if out.strip().startswith("{") else out)


def layers_block(text):
    lines = text.splitlines()
    i = lines.index("layers =")
    out = []
    for l in lines[i + 1:]:
        if not l.startswith("    "):
            break
        out.append([x.strip() for x in l.split(":")])
    return out


# ---- AC1: layers contract in the ecosystem format, order matches the known layering --------------------------------

def test_python_contract_layer_order_matches_fixture(repo, capsys):
    rc, rep = derive(repo, "--python-graph", FX / "python-graph.json", capsys=capsys)
    assert rc == 0
    assert layers_block((repo / ".importlinter").read_text()) == [["shop.interface"], ["shop.application"], ["shop.domain"]]
    assert rep["ecosystems"][0]["layers"] == [["shop.interface"], ["shop.application"], ["shop.domain"]]
    # per-layer allowed externals come from the observed imports
    assert rep["ecosystems"][0]["externals_per_layer"] == {"0": ["flask"], "1": ["sqlalchemy"], "2": []}
    assert "root_package = shop" in (repo / ".importlinter").read_text()


def test_js_contract_layer_order_matches_fixture(repo, capsys):
    rc, rep = derive(repo, "--js-graph", FX / "depcruise.json", "--js-source", "src", capsys=capsys)
    assert rc == 0
    assert rep["ecosystems"][0]["layers"] == [["ui"], ["services"], ["core"]]
    cjs = (repo / ".dependency-cruiser.cjs").read_text()
    names = re.findall(r'name: "(layer-\d-not-to-higher)"', cjs)
    assert names == ["layer-2-not-to-higher", "layer-3-not-to-higher"]
    # layer 3 (core) may not depend on the two layers above it
    assert re.search(r'layer-3-not-to-higher.*from: \{ path: "\^src/\(core\)/" \}, to: \{ path: "\^src/\(ui\|services\)/" \}', cjs)
    assert re.search(r'layer-2-external-allowlist.*pathNot: "node_modules/\(axios\)', cjs)
    assert "no-circular" in cjs


# ---- AC2: a cycle is a finding and its members share one layer ----------------------------------------------------

def test_cycle_members_never_split_across_layers(repo, capsys):
    rc, rep = derive(repo, "--python-graph", FX / "python-graph-cycle.json", capsys=capsys)
    eco = rep["ecosystems"][0]
    assert eco["cycles"] == [{"units": ["shop.billing", "shop.orders"], "internal_edges": 2}]
    assert eco["layers"] == [["shop.web"], ["shop.billing", "shop.orders"], ["shop.core"]]
    assert layers_block((repo / ".importlinter").read_text())[1] == ["shop.billing", "shop.orders"]
    assert "cycle" in (repo / "docs/architecture/DERIVE-CONFIRM.md").read_text().lower()


def test_levelling_never_splits_an_scc_even_with_a_longer_cycle():
    edges = {("a", "b"): 1, ("b", "c"): 1, ("c", "a"): 1, ("c", "d"): 1, ("e", "a"): 1}
    ext = {"modules": [f"r.{u}.m" for u in "abcde"], "unit": lambda m: m.split(".")[1],
           "edges": {(f"r.{a}.m", f"r.{b}.m"): n for (a, b), n in edges.items()}, "externals": {}}
    plan = D.propose_layers(ext)
    assert [l["units"] for l in plan["layers"]] == [["e"], ["a", "b", "c"], ["d"]]


# ---- AC3: a missing tool is could-not-look and writes no contract --------------------------------------------------

def test_missing_grimp_is_could_not_look_and_writes_no_contract(repo, monkeypatch, capsys):
    monkeypatch.setattr(D, "import_grimp", lambda: None)
    rc, rep = derive(repo, "--python-package", "shop", capsys=capsys)
    assert rc == 1
    assert any(g.startswith("could-not-look:grimp") for g in rep["gaps"])
    assert not (repo / ".importlinter").exists() and not (repo / "docs/architecture").exists()
    assert rep["ecosystems"] == []


def test_missing_depcruise_is_could_not_look_and_writes_no_contract(repo, monkeypatch, capsys):
    (repo / "package.json").write_text("{}")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "js")
    real = D.shutil.which
    monkeypatch.setattr(D.shutil, "which", lambda name: None if name == "depcruise" else real(name))
    rc, rep = derive(repo, capsys=capsys)
    assert rc == 1
    assert "could-not-look:depcruise (not installed)" in rep["gaps"]
    assert not (repo / ".dependency-cruiser.cjs").exists()


def test_failing_tool_is_could_not_look_not_an_empty_graph(repo, monkeypatch, capsys):
    (repo / "package.json").write_text("{}")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "js")
    real = D.shutil.which
    # runs python with depcruise args: exits non-zero
    monkeypatch.setattr(D.shutil, "which", lambda name: sys.executable if name == "depcruise" else real(name))
    rc, rep = derive(repo, capsys=capsys)
    assert rc == 1 and any(g.startswith("could-not-look:depcruise") for g in rep["gaps"])
    assert not (repo / ".dependency-cruiser.cjs").exists()


@pytest.mark.parametrize("marker,gap", [("pom.xml", "no-derive:java"), ("nx.json", "no-derive:nx")])
def test_java_and_nx_report_the_named_gap(repo, capsys, marker, gap):
    (repo / marker).write_text("")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "m")
    rc, rep = derive(repo, capsys=capsys)
    assert gap in rep["gaps"] and rep["ecosystems"] == []


# ---- AC4: infra adds deployment nodes, each with a source ----------------------------------------------------------

def dsl_elements(text):
    return L.lint_text(text), [m for m in re.findall(r"^\s*(?:\w+ = )?deploymentNode \"([^\"]+)\"", text, re.M)]


def test_tofu_plan_adds_deployment_nodes_with_source(repo, capsys):
    rc, rep = derive(repo, "--tofu-plan-json", FX / "tofu-plan.json", capsys=capsys)
    assert rc == 0
    text = (repo / "docs/architecture/workspace.dsl").read_text()
    findings, nodes = dsl_elements(text)
    assert nodes == ["aws_instance.web", "aws_db_instance.main", "module.net.aws_vpc.main"] or sorted(nodes) == sorted(
        ["aws_instance.web", "aws_db_instance.main", "module.net.aws_vpc.main"])
    assert findings == []
    assert 'deploymentEnvironment "Live"' in text
    assert re.search(r'"source" "[^"]*tofu-plan\.json#aws_instance\.web"', text)
    adrs = sorted((repo / "docs/decisions").glob("*.md"))
    assert len(adrs) == 1 and "aws-db-instance-main" in adrs[0].name


def test_rendered_manifests_add_deployment_nodes_with_source(repo, capsys):
    rc, rep = derive(repo, "--manifests", FX / "k8s", capsys=capsys)
    assert rc == 0
    text = (repo / "docs/architecture/workspace.dsl").read_text()
    findings, nodes = dsl_elements(text)
    assert sorted(nodes) == ["shop/Deployment/web", "shop/Service/web", "shop/StatefulSet/db"]
    assert findings == []
    assert "rendered.yaml#Deployment/web" in text


# ---- draft outputs: tags, ADRs, checklist, dry-run -----------------------------------------------------------------

def test_every_generated_element_is_draft_and_has_a_source(repo, capsys):
    (repo / "Dockerfile").write_text("FROM scratch\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "d")
    derive(repo, "--python-graph", FX / "python-graph.json", "--tofu-plan-json", FX / "tofu-plan.json", capsys=capsys)
    text = (repo / "docs/architecture/workspace.dsl").read_text()
    n_el = len(re.findall(r"^\s*(?:[\w.]+ = )?(softwareSystem|container|component|deploymentEnvironment|deploymentNode) \"", text, re.M))
    assert n_el >= 8
    assert len(re.findall(r'tags "draft"', text)) == n_el
    assert len(re.findall(r'"source" "[^"]+"', text)) == n_el
    assert '"model-status" "draft"' in text
    assert L.lint_text(text) == []
    assert 'container "Dockerfile' not in text and 'container "root-image"' in text


def test_retroactive_adr_drafts(repo, capsys):
    derive(repo, "--python-graph", FX / "python-graph.json", "--tofu-plan-json", FX / "tofu-plan.json", capsys=capsys)
    adrs = sorted((repo / "docs/decisions").glob("*.md"))
    assert [a.name[:4] for a in adrs] == ["0001", "0002"]
    for a in adrs:
        fm = yaml.safe_load(a.read_text().split("---")[1])
        assert fm["retroactive"] is True and fm["confidence"] in {"low", "medium", "high"}
        assert fm["status"] == "proposed" and fm["evidence"]
        assert "TODO(human)" in a.read_text()
    r = subprocess.run([sys.executable, "-B", str(ADR_LINT), str(repo / "docs/decisions")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    chk = (repo / "docs/architecture/DERIVE-CONFIRM.md").read_text()
    for needle in ("Layer order and layer names", "Module-to-layer mapping", "Allowed external packages per layer",
                   "Container boundaries and external systems", "Decision and rationale of docs/decisions/0001"):
        assert needle in chk


def test_rerun_does_not_duplicate_adrs_or_overwrite_files(repo, capsys):
    derive(repo, "--tofu-plan-json", FX / "tofu-plan.json", capsys=capsys)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "drafts")
    rc, rep = derive(repo, "--tofu-plan-json", FX / "tofu-plan.json", capsys=capsys)
    assert len(list((repo / "docs/decisions").glob("*.md"))) == 1
    assert "docs/architecture/workspace.dsl.derived" in rep["writes"]


def test_dry_run_prints_the_plan_and_writes_nothing(repo, capsys):
    rc = D.main(["--repo", str(repo), "--dry-run", "--python-graph", str(FX / "python-graph.json")])
    out = capsys.readouterr().out
    assert rc == 0 and "dry-run" in out and "shop.interface > shop.application > shop.domain" in out
    assert "would write: .importlinter" in out
    assert sorted(p.name for p in repo.iterdir() if p.name != ".git") == ["pyproject.toml"]


# ---- AC6: no baseline, no apply, no dirty tree ---------------------------------------------------------------------

def test_dirty_tracked_tree_is_refused_and_nothing_is_written(repo, capsys):
    (repo / "pyproject.toml").write_text("changed\n")
    rc = D.main(["--repo", str(repo), "--python-graph", str(FX / "python-graph.json")])
    assert rc == 2 and "dirty" in capsys.readouterr().err
    assert not (repo / ".importlinter").exists()


def test_untracked_files_do_not_block(repo, capsys):
    (repo / "scratch.txt").write_text("x")
    rc, _ = derive(repo, "--python-graph", FX / "python-graph.json", capsys=capsys)
    assert rc == 0


def test_a_non_git_directory_is_refused(tmp_path, capsys):
    assert D.main(["--repo", str(tmp_path)]) == 2


def test_never_writes_a_baseline(repo, capsys):
    derive(repo, "--python-graph", FX / "python-graph-cycle.json", "--js-graph", FX / "depcruise.json",
           "--tofu-plan-json", FX / "tofu-plan.json", "--manifests", FX / "k8s", capsys=capsys)
    written = [p.relative_to(repo).as_posix() for p in repo.rglob("*") if p.is_file() and ".git/" not in p.as_posix()]
    assert written and not [w for w in written if re.search(r"baseline|known-violations|ignore_imports|archunit_store", w)]
    assert "ignore_imports" not in (repo / ".importlinter").read_text()


@pytest.mark.parametrize("argv", [["tofu", "apply"], ["tofu", "destroy"], ["helm", "install", "x"], ["rm", "-rf", "x"],
                                  ["git", "push"], ["kustomize", "edit"], ["depcruise", "--baseline"]])
def test_only_allowed_read_only_commands_can_run(argv):
    with pytest.raises(D.Refused):
        D.run_tool(argv)


def test_script_text_never_names_an_apply(repo):
    text = (ROOT / "scripts" / "foundry_architecture_derive.py").read_text()
    assert not re.search(r"""["']apply["']""", text)


# ---- AC5: model lint ----------------------------------------------------------------------------------------------

def run_lint(path):
    return subprocess.run([sys.executable, "-B", str(LINT), str(path)], capture_output=True, text=True)


def test_lint_control_passes():
    r = run_lint(FX / "good.dsl")
    assert r.returncode == 0 and "clean" in r.stdout


def test_lint_fails_on_an_element_without_source_and_names_it():
    r = run_lint(FX / "no-source.dsl")
    assert r.returncode == 1
    assert "container 'api' has no source property" in r.stdout
    assert "Shop" not in r.stdout


def test_lint_fails_on_an_element_with_no_body():
    assert L.lint_text('workspace "x" {\n model {\n  s = softwareSystem "Bare"\n }\n}\n') == [
        "line 3: softwareSystem 'Bare' has no source property"]


def test_lint_rejects_an_empty_source_value():
    t = (FX / "good.dsl").read_text().replace('"source" "Dockerfile"', '"source" ""')
    assert any("container 'api'" in f for f in L.lint_text(t))


def test_lint_fails_on_draft_elements_once_confirmed():
    r = run_lint(FX / "confirmed-with-draft.dsl")
    assert r.returncode == 1
    assert "softwareSystem 'Shop' is still tagged draft" in r.stdout and "container 'api' is still tagged draft" in r.stdout


def test_lint_passes_a_confirmed_model_without_draft_tags():
    t = (FX / "confirmed-with-draft.dsl").read_text().replace('tags "draft"', 'tags "confirmed"')
    assert L.lint_text(t) == []


def test_lint_missing_file_is_exit_2(tmp_path):
    assert run_lint(tmp_path / "nope.dsl").returncode == 2


def test_lint_ignores_commented_out_elements():
    t = (FX / "good.dsl").read_text().replace("    model {", '    model {\n        // x = container "ghost"')
    assert L.lint_text(t) == []


# ---- registration --------------------------------------------------------------------------------------------------

def test_kit_registers_the_model_lint():
    kit = yaml.safe_load((ROOT / "governance/kits/architecture/kit.yaml").read_text())
    assert "checks/model_source_lint.py" in kit["checks"]


def test_intake_skill_runs_the_derive_step_between_select_and_baseline():
    text = (ROOT / "skills/intake/SKILL.md").read_text()
    sec = text[text.index("## `intake existing`"):]
    assert "foundry_architecture_derive.py" in sec
    pos = {k: sec.index(k) for k in ("**Select.**", "foundry_architecture_derive.py", "**Baseline.**")}
    assert pos["**Select.**"] < pos["foundry_architecture_derive.py"] < pos["**Baseline.**"]
    assert (ROOT / "docs/how-to/derive-architecture.md").is_file()
