"""dl-19: brownfield intake derives DRAFT architecture blueprints (scripts/foundry_architecture_derive.py) and the
model source lint (governance/kits/architecture/checks/model_source_lint.py). No network, no installed tools: graphs and
plans come in as pre-computed JSON through the flags."""
import importlib.util
import json
import re
import shutil
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
    assert "could-not-look:depcruise" in rep["gaps"] and rep["gap_details"]["could-not-look:depcruise"] == "not installed"
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
    n_el = len(re.findall(r"^\s*(?:[\w.]+ = )?(softwareSystem|container|component|deploymentNode) \"", text, re.M))
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


# =============================================================================================================
# Fix round (PR #327 review): B1 B2 and risks R-a to R-i
# =============================================================================================================

def write_json(tmp_path, name, doc):
    p = tmp_path / name
    p.write_text(json.dumps(doc))
    return p


def js_graph(units, ext=None):
    """units: {"ui": ["services"], ...}; every unit is one file src/<unit>/f.ts."""
    mods = []
    for u, deps in units.items():
        d = [{"resolved": f"src/{x}/f.ts", "dependencyTypes": ["local"]} for x in deps]
        for pk in (ext or {}).get(u, []):
            d.append({"resolved": f"node_modules/{pk}/i.js", "module": pk, "dependencyTypes": ["npm"]})
        mods.append({"source": f"src/{u}/f.ts", "dependencies": d})
    return {"modules": mods}


def js_literals(cjs):
    return [json.loads('"' + m + '"') for m in re.findall(r'"((?:[^"\\]|\\.)*)"', cjs)]


# ---- B1: every JS string literal is built with json.dumps ----------------------------------------------------------

def test_b1_js_patterns_round_trip_and_match_exactly_what_the_python_pattern_matches(repo, tmp_path, capsys):
    g = write_json(tmp_path, "g.json", js_graph({"ui": ["a.b"], "a.b": ["c-d"], "c-d": []}, {"a.b": ["lodash.merge"]}))
    rc, rep = derive(repo, "--js-graph", g, "--js-source", "src", capsys=capsys)
    assert rep["ecosystems"][0]["layers"] == [["ui"], ["a.b"], ["c-d"]]
    cjs = (repo / ".dependency-cruiser.cjs").read_text()
    pats = [p for p in js_literals(cjs) if p.startswith("^src/") or p.startswith("node_modules/")]   # every literal parses back
    assert "^src/(a\\.b)/" in pats and "^src/(c\\-d)/" in pats and "^src/(ui|a\\.b)/" in pats
    for p in pats:
        re.compile(p)                                                   # every literal parses back to a valid regex
    dot = next(p for p in pats if p == "^src/(a\\.b)/")
    assert re.search(dot, "src/a.b/f.ts") and not re.search(dot, "src/aXb/f.ts")
    ext = next(p for p in pats if p.startswith("node_modules/"))
    assert re.search(ext, "node_modules/lodash.merge/x.js") and not re.search(ext, "node_modules/lodashXmerge/x.js")


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_b1_the_generated_cjs_loads_in_node(repo, tmp_path, capsys):
    g = write_json(tmp_path, "g.json", js_graph({"ui": ["a.b"], "a.b": []}))
    derive(repo, "--js-graph", g, "--js-source", "src", capsys=capsys)
    r = subprocess.run(["node", "-e", "const c=require('./.dependency-cruiser.cjs');"
                        "console.log(c.forbidden.map(r=>r.from&&r.from.path).filter(Boolean).join('\\n'))"],
                       cwd=repo, capture_output=True, text=True)
    assert r.returncode == 0 and "^src/(ui)/" in r.stdout


@pytest.mark.parametrize("name", ['src/ev"il/f.ts', "src/ev`il/f.ts", "src/ev'il/f.ts", "src/ev\\il/f.ts", "src/ev\nil/f.ts"])
def test_b1_ra_hostile_js_unit_names_never_reach_the_contract(repo, tmp_path, capsys, name):
    doc = js_graph({"ui": ["core"], "core": []})
    doc["modules"].append({"source": name, "dependencies": [{"resolved": "src/core/f.ts", "dependencyTypes": ["local"]}]})
    g = write_json(tmp_path, "g.json", doc)
    rc, rep = derive(repo, "--js-graph", g, "--js-source", "src", capsys=capsys)
    cjs = (repo / ".dependency-cruiser.cjs").read_text()
    assert "ev" not in cjs.replace("forbidden", "").replace("severity", "").replace("level", "")
    assert any(x.startswith("rejected-name:js module") for x in rep["gaps"])


def test_ra_hostile_npm_specifier_is_rejected(repo, tmp_path, capsys):
    doc = js_graph({"ui": ["core"], "core": []}, {"ui": ['x");process.exit(1);//']})
    rc, rep = derive(repo, "--js-graph", write_json(tmp_path, "g.json", doc), "--js-source", "src", capsys=capsys)
    assert "process.exit" not in (repo / ".dependency-cruiser.cjs").read_text()
    assert any(x.startswith("rejected-name:js package specifier") for x in rep["gaps"])


# ---- B2: deploymentEnvironment carries no tags or properties; the lint does not treat it as an element --------------

def test_b2_deployment_environment_has_no_tags_or_properties(repo, capsys):
    derive(repo, "--tofu-plan-json", FX / "tofu-plan.json", capsys=capsys)
    text = (repo / "docs/architecture/workspace.dsl").read_text()
    lines = text.splitlines()
    i = next(n for n, l in enumerate(lines) if l.strip().startswith("deploymentEnvironment"))
    indent = len(lines[i]) - len(lines[i].lstrip())
    children = []
    for l in lines[i + 1:]:
        if len(l) - len(l.lstrip()) <= indent:
            break
        if len(l) - len(l.lstrip()) == indent + 4 and l.strip() != "}":
            children.append(re.sub(r"^\w+ = ", "", l.strip()).split()[0])
    assert children and set(children) == {"deploymentNode"}      # only group, deploymentGroup, deploymentNode and relationships are legal
    assert L.lint_text(text) == []


def test_b2_lint_does_not_demand_a_source_on_the_environment():
    t = 'workspace "x" {\n model {\n  deploymentEnvironment "Live" {\n  }\n }\n}\n'
    assert L.lint_text(t) == []
    assert "deploymentEnvironment" not in L.KINDS


# ---- R-a: injection through names -----------------------------------------------------------------------------------

def test_ra_hostile_python_module_names_are_rejected_and_reported(repo, tmp_path, capsys):
    doc = json.loads((FX / "python-graph.json").read_text())
    doc["edges"].append({"from": 'shop.x"\n!script groovy {', "to": "shop.domain.order"})
    doc["edges"].append({"from": "shop.interface.http", "to": "shop.dom\nain.y\n[evil]"})
    g = write_json(tmp_path, "g.json", doc)
    rc, rep = derive(repo, "--python-graph", g, capsys=capsys)
    assert rc == 0 and any(x.startswith("rejected-name:python module") for x in rep["gaps"])
    for rel in (".importlinter", "docs/architecture/workspace.dsl", "docs/architecture/DERIVE-CONFIRM.md"):
        text = (repo / rel).read_text()
        assert "!script" not in text and "[evil]" not in text, rel
    assert rep["ecosystems"][0]["layers"] == [["shop.interface"], ["shop.application"], ["shop.domain"]]


def test_ra_hostile_infra_names_are_rejected(repo, tmp_path, capsys):
    plan = {"planned_values": {"root_module": {"resources": [
        {"address": 'aws_instance.a"\n!script x', "type": "aws_instance", "name": "a"},
        {"address": "aws_instance.ok", "type": "aws_instance", "name": "ok"}]}}}
    rc, rep = derive(repo, "--tofu-plan-json", write_json(tmp_path, "p.json", plan), capsys=capsys)
    text = (repo / "docs/architecture/workspace.dsl").read_text()
    assert "!script" not in text and "aws_instance.ok" in text
    assert any(x.startswith("rejected-name:tofu address") for x in rep["gaps"])
    assert "!script" not in (repo / "docs/architecture/DERIVE-CONFIRM.md").read_text()


# ---- R-b: draft writes stay inside the repo ------------------------------------------------------------------------

def tracked_symlink(repo, rel, target):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.symlink_to(target)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "symlink")


@pytest.mark.parametrize("link", ["docs", "docs/architecture", ".importlinter"])
def test_rb_symlinked_targets_and_parents_are_refused(repo, tmp_path, capsys, link):
    outside = tmp_path / "outside"
    outside.mkdir()
    if link == "docs/architecture":
        (repo / "docs").mkdir()
        tracked_symlink(repo, link, outside)
    elif link == "docs":
        tracked_symlink(repo, link, outside)
    else:
        (outside / "target").write_text("keep")
        tracked_symlink(repo, link, outside / "target")
    rc = D.main(["--repo", str(repo), "--python-graph", str(FX / "python-graph.json")])
    assert rc == 2 and "symlink" in capsys.readouterr().err
    assert sorted(p.name for p in outside.iterdir()) in ([], ["target"])
    assert not (outside / "target").exists() or (outside / "target").read_text() == "keep"


def test_rb_symlinked_derived_sibling_is_refused(repo, tmp_path, capsys):
    outside = tmp_path / "o"
    outside.write_text("keep")
    (repo / ".importlinter").write_text("[x]\n")
    tracked_symlink(repo, ".importlinter.derived", outside)
    rc = D.main(["--repo", str(repo), "--python-graph", str(FX / "python-graph.json")])
    assert rc == 2 and outside.read_text() == "keep"


def test_rb_new_files_are_created_exclusively(repo, monkeypatch, capsys):
    seen = []
    real = D.os.open
    monkeypatch.setattr(D.os, "open", lambda p, flags, mode=0o777: (seen.append(flags), real(p, flags, mode))[1])
    derive(repo, "--python-graph", FX / "python-graph.json", capsys=capsys)
    assert seen and all(f & D.os.O_NOFOLLOW and f & D.os.O_EXCL for f in seen)


# ---- R-c / R-d: option values --------------------------------------------------------------------------------------

@pytest.mark.parametrize("flag", ["--js-source", "--tofu-plan", "--helm-chart", "--kustomize", "--python-package"])
def test_rc_option_values_starting_with_a_dash_are_refused(repo, capsys, flag):
    assert D.main(["--repo", str(repo), f"{flag}=-x"]) == 2
    assert "starts with '-'" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--helm-chart", "--kustomize"])
@pytest.mark.parametrize("value", ["oci://registry/chart", "https://example.org/k", "../outside", "nodir", "github.com/org/repo//path"])
def test_rd_helm_and_kustomize_take_only_local_directories_inside_the_repo(repo, capsys, flag, value):
    assert D.main(["--repo", str(repo), f"{flag}={value}"]) == 2
    assert "refused" in capsys.readouterr().err


def test_rc_rd_tools_get_double_dash_and_a_repo_relative_path(repo, monkeypatch, capsys):
    (repo / "charts" / "web").mkdir(parents=True)
    (repo / "charts" / "web" / "x").write_text("")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "chart")
    calls = []
    real_run = D.run_tool
    rendered = "apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: web, namespace: s}\n"
    monkeypatch.setattr(D, "run_tool", lambda argv, cwd=None: (calls.append(argv), rendered)[1]
                        if argv[0] in ("helm", "kustomize") else real_run(argv, cwd))
    rc, rep = derive(repo, "--helm-chart", "charts/web", "--kustomize", "charts/web", capsys=capsys)
    assert rc == 0
    assert ["helm", "template", "--", "charts/web"] in calls and ["kustomize", "build", "--", "charts/web"] in calls
    assert "helm:charts/web#Deployment/web" in (repo / "docs/architecture/workspace.dsl").read_text()


# ---- R-e: Grimp runs isolated and writes nothing into the repo --------------------------------------------------------

def test_re_grimp_runs_in_an_isolated_subprocess_without_a_cache(repo, monkeypatch, capsys):
    monkeypatch.setattr(D, "import_grimp", lambda: object())
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, stdout=(FX / "python-graph.json").read_text(), stderr="")
    real = subprocess.run
    monkeypatch.setattr(D.subprocess, "run", lambda argv, **kw: fake_run(argv, **kw) if "-I" in argv else real(argv, **kw))
    rc, rep = derive(repo, "--python-package", "shop", capsys=capsys)
    argv = seen["argv"]
    assert argv[0] == sys.executable and "-I" in argv[:3] and "-B" in argv[:4]
    script = argv[argv.index("-c") + 1]
    assert "cache_dir=None" in script and "sys.path.append" in script and "sys.path.insert" not in script
    assert rep["ecosystems"][0]["layers"][0] == ["shop.interface"]
    assert "sys.path.insert" not in (ROOT / "scripts" / "foundry_architecture_derive.py").read_text()


# ---- R-f: tool stderr and local paths stay out of committed drafts ------------------------------------------------------

def test_rf_tool_stderr_is_console_only(repo, tmp_path, monkeypatch, capsys):
    (repo / "package.json").write_text("{}")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "js")
    real = D.run_tool

    def boom(argv, cwd=None):
        if argv[0] == "depcruise":
            raise D.CouldNotLook("depcruise", "SECRET-STDERR /home/someone/token")
        return real(argv, cwd)
    monkeypatch.setattr(D, "run_tool", boom)
    rc, rep = derive(repo, "--python-graph", FX / "python-graph.json", capsys=capsys)
    assert "SECRET-STDERR" in rep["gap_details"]["could-not-look:depcruise"]
    for p in repo.rglob("*"):
        if p.is_file() and ".git/" not in p.as_posix():
            assert "SECRET-STDERR" not in p.read_text(errors="ignore"), p
    D.main(["--repo", str(repo), "--dry-run", "--python-graph", str(FX / "python-graph.json")])
    assert "SECRET-STDERR" in capsys.readouterr().out


def test_rf_plan_paths_are_repo_relative_or_a_basename(repo, tmp_path, capsys):
    outside = tmp_path / "elsewhere" / "plan.json"
    outside.parent.mkdir()
    outside.write_text((FX / "tofu-plan.json").read_text())
    derive(repo, "--tofu-plan-json", outside, capsys=capsys)
    text = (repo / "docs/architecture/workspace.dsl").read_text() + "".join(p.read_text() for p in (repo / "docs/decisions").glob("*.md"))
    assert str(tmp_path) not in text and "plan.json#aws_instance.web" in text


# ---- R-g: malformed input is a named gap or a refusal, never a traceback -------------------------------------------------

def test_rg_malformed_rendered_yaml_is_a_named_gap(repo, monkeypatch, capsys):
    (repo / "c").mkdir()
    (repo / "c" / "x").write_text("")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c")
    real = D.run_tool
    monkeypatch.setattr(D, "run_tool", lambda argv, cwd=None: "a: [unclosed\n  - : :" if argv[0] == "helm" else real(argv, cwd))
    rc, rep = derive(repo, "--helm-chart", "c", capsys=capsys)
    assert rc == 1 and "could-not-look:helm" in rep["gaps"]


@pytest.mark.parametrize("edge", [{"from": "shop.a.x"}, {"to": "shop.a.x"}, "shop.a.x", {"from": 1, "to": 2}])
def test_rg_a_graph_edge_without_from_or_to_is_refused(repo, tmp_path, capsys, edge):
    g = write_json(tmp_path, "g.json", {"root_package": "shop", "edges": [edge]})
    assert D.main(["--repo", str(repo), "--python-graph", str(g)]) == 2
    assert "refused" in capsys.readouterr().err


def test_rg_malformed_manifest_yaml_is_refused(repo, tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("a: [unclosed\n")
    assert D.main(["--repo", str(repo), "--manifests", str(bad)]) == 2


def test_rg_a_git_failure_while_reading_the_status_is_exit_2(repo, monkeypatch, capsys):
    real = D.run_tool

    def flaky(argv, cwd=None):
        if argv[:2] == ["git", "status"]:
            raise D.CouldNotLook("git", "exit 128")
        return real(argv, cwd)
    monkeypatch.setattr(D, "run_tool", flaky)
    assert D.main(["--repo", str(repo), "--python-graph", str(FX / "python-graph.json")]) == 2
    assert "refused" in capsys.readouterr().err


# ---- R-h: ':' within a cycle group, '|' between independent groups -------------------------------------------------------

def test_rh_siblings_use_pipe_and_cycle_members_use_colon(repo, capsys):
    derive(repo, "--python-graph", FX / "python-graph-siblings.json", capsys=capsys)
    lines = (repo / ".importlinter").read_text().splitlines()
    block = lines[lines.index("layers =") + 1:lines.index("layers =") + 4]
    # import-linter forbids mixing separators on one line, so a layer with a cycle group uses `:` throughout
    assert block == ["    shop.web", "    shop.api : shop.billing : shop.orders", "    shop.core"]


def test_rh_no_layer_line_mixes_separators(repo, capsys):
    derive(repo, "--python-graph", FX / "python-graph-siblings.json", capsys=capsys)
    lines = (repo / ".importlinter").read_text().splitlines()
    i, checked = lines.index("layers =") + 1, 0
    while i < len(lines) and lines[i].startswith("    "):
        assert not (" | " in lines[i] and " : " in lines[i]), lines[i]
        i, checked = i + 1, checked + 1
    assert checked == 3  # the fixture has three layers; a vacuous loop fails here


# ---- R-i: lint keyword case, the generic element keyword, unquoted tokens -------------------------------------------------

@pytest.mark.parametrize("kw", ["Container", "COMPONENT", "SoftwareSystem", "element", "Person", "DeploymentNode", "INFRASTRUCTURENODE"])
def test_ri_keywords_match_case_insensitively(kw):
    t = f'workspace "x" {{\n model {{\n  {kw} "N" {{\n   tags "draft"\n  }}\n }}\n}}\n'
    assert L.lint_text(t) == [f"line 3: {kw} 'N' has no source property"]


def test_ri_unquoted_tags_and_model_status_are_read_like_quoted():
    t = ('workspace "x" {\n properties {\n  "model-status" confirmed\n }\n model {\n  container "N" {\n   tags draft\n'
         '   properties {\n    source "f"\n   }\n  }\n }\n}\n')
    assert L.lint_text(t) == ["line 6: container 'N' is still tagged draft in a confirmed model"]
    t2 = t.replace('"model-status" confirmed', 'model-status "Confirmed"').replace("tags draft", 'tags "a, Draft"')
    assert len(L.lint_text(t2)) == 1
    assert L.lint_text(t.replace("tags draft", "tags other")) == []
