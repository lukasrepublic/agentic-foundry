"""v1.18.2: the plugin declares every third-party module its scripts and hooks import, the updater's
list equals it, and the doctor checks it — a fresh agent container shipped without yaml/jsonschema and
nothing noticed until a session broke."""
import ast
import glob
import importlib.util
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST_TO_MODULE = {"pyyaml": "yaml"}


def _declared():
    out = {}
    with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if line:
                dist = re.split(r"[=<>!~\[;\s]", line, maxsplit=1)[0].strip().lower()
                out[DIST_TO_MODULE.get(dist, dist.replace("-", "_"))] = line
    return out


def _imported_third_party():
    files = glob.glob(os.path.join(ROOT, "scripts", "**", "*.py"), recursive=True) + \
        glob.glob(os.path.join(ROOT, "hooks", "**", "*.py"), recursive=True)
    local = {os.path.splitext(os.path.basename(p))[0] for p in files}
    found = set()
    for p in files:
        try:
            tree = ast.parse(open(p, encoding="utf-8").read())
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                mods = [n.module]
            else:
                continue
            for m in mods:
                top = m.split(".")[0]
                if top not in sys.stdlib_module_names and top not in local:
                    found.add(top)
    return found


def test_every_third_party_import_is_declared():
    assert _imported_third_party() <= set(_declared()), \
        f"undeclared runtime imports: {sorted(_imported_third_party() - set(_declared()))}"


def test_updater_list_equals_requirements_txt():
    doc = json.load(open(os.path.join(ROOT, "cli", "python-requirements.json"), encoding="utf-8"))
    assert {q["module"]: q["requirement"] for q in doc["requirements"]} == _declared()


def test_runtime_pins_equal_ci_pins():
    """The updater installs exactly what CI tests against."""
    dev = {}
    for line in open(os.path.join(ROOT, "requirements-dev.txt"), encoding="utf-8"):
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            name, ver = line.split("==", 1)
            dev[name.strip().lower()] = ver.strip()
    for req in _declared().values():
        name, ver = req.split("==", 1)
        assert dev.get(name.strip().lower()) == ver.strip(), f"{req} differs from requirements-dev.txt"


def test_packaged_with_the_cli():
    pkg = json.load(open(os.path.join(ROOT, "cli", "package.json"), encoding="utf-8"))
    assert "python-requirements.json" in pkg["files"]


def _doctor():
    spec = importlib.util.spec_from_file_location("foundry_doctor_pydeps", os.path.join(ROOT, "scripts", "foundry-doctor.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_doctor_python_deps_ok_and_missing(tmp_path, monkeypatch):
    doc = _doctor()
    ok, detail = doc.check_python_deps(ROOT)
    assert ok, detail
    (tmp_path / "requirements.txt").write_text("PyYAML==6.0.2\nnot-a-real-module-xyz==1.0\n", encoding="utf-8")
    ok, detail = doc.check_python_deps(str(tmp_path))
    assert ok is False
    assert "not_a_real_module_xyz" in detail and "update-agentic-workspace" in detail


def test_install_constraints_equal_ci_transitive_pins():
    """The updater's -c constraints are exactly the transitive pins CI installs."""
    doc = json.load(open(os.path.join(ROOT, "cli", "python-requirements.json"), encoding="utf-8"))
    dev = set()
    for line in open(os.path.join(ROOT, "requirements-dev.txt"), encoding="utf-8"):
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            dev.add(line.lower())
    for c in doc["constraints"]:
        assert c.lower() in dev, f"{c} not pinned in requirements-dev.txt"
