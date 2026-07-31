"""tests/test_python_stack_profile.py — feat-foundry-python-stack-profile (atom GP-2).

Validates the two shipped Python stack-profile packs (`python-uv-service`, `python-uv-lib`) against
the shipped loader (`scripts/foundry-stack-profile.py`) and the certification driver
(`skills/certify-local/certify_local.py`), and proves the additive `profile_kind: library` schema
extension is back-compatible and fail-closed — over the REAL shipped tree plus throwaway `tmp_path`
fixtures for the two criteria that need a resolvable lock (AC-PSP-7, AC-PSP-8), following the same
throwaway-plugin-root pattern `tests/test_certify_fixture.py`'s `_seed_stack_profile`/`_write_lock`
already use. Read-only over the live tree; no profile command string is ever executed here.
"""
from __future__ import annotations

import json
import os
import re
from urllib.parse import urlparse

import pytest
import yaml

from conftest import REPO_ROOT, load_module

sp = load_module("scripts/foundry-stack-profile.py", "foundry_stack_profile_python_stack_profile")
cl = load_module("skills/certify-local/certify_local.py", "certify_local_python_stack_profile")

PACKS_DIR = os.path.join(REPO_ROOT, "packs", "stack-profiles")
PACK_IDS = ("python-uv-service", "python-uv-lib")

# ── the Declared-content command-string table (spec, byte-identical between the two packs) ──────
RECIPE_TABLE = [
    ("static_validation", "format", "uv run --locked ruff format --check ."),
    ("static_validation", "lint", "uv run --locked ruff check ."),
    ("static_validation", "typecheck", "uv run --locked mypy src"),
    ("static_validation", "build", "uv build"),
    ("test_recipe", "unit",
     "uv run --locked pytest tests/unit --cov=src --cov-report=term-missing --cov-fail-under=80"),
    ("test_recipe", "integration", "uv run --locked pytest tests/integration"),
    ("test_recipe", "e2e", "uv run --locked pytest tests/e2e"),
    ("test_recipe", "coverage_gate", 80),
]

# ── the Declared-content boot-recipe paragraph ────────────────────────────────────────────────
BOOT_CMD = "uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000"
SURFACE_EXERCISE = ("GET the ASGI health endpoint at http://127.0.0.1:8000/healthz and assert a "
                     "200 JSON {status:ok}.")

# ── the Declared-content pin table (14 rows: 7 per pack) ─────────────────────────────────────────
PIN_TABLE = [
    ("python_interpreter", "cpython", "python_version_file", "3.13"),
    ("python_requires", "project-metadata", "requires_python", ">=3.11"),
    ("pypi_package", "uv", "version", "0.11.33"),
    ("pypi_package", "ruff", "version", "0.16.0"),
    ("pypi_package", "mypy", "version", "2.3.0"),
    ("pypi_package", "pytest", "version", "9.1.1"),
    ("pypi_package", "pytest-cov", "version", "7.1.0"),
]
SHAPE_RE = {
    "pypi_package": re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$"),
    "python_interpreter": re.compile(r"^[0-9]+\.[0-9]+$"),
    "python_requires": re.compile(r"^>=[0-9]+\.[0-9]+$"),
}
PINS_VALID_AS_OF = "2026-07-28"


# ═══════════════════════════════════════════════════════════════════════ shared helpers ═══════ #

def _load(pid):
    return sp.load_profile(pid, root=REPO_ROOT, plugin_root=REPO_ROOT)


def _manifest(pid):
    path = os.path.join(PACKS_DIR, pid, "standing-versions", "manifest.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _compat(pid):
    path = os.path.join(PACKS_DIR, pid, "standing-versions", "compatibility.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _find_row(manifest, kind, subject):
    for row in manifest.get("pins", []):
        if row.get("kind") == kind and row.get("subject") == subject:
            return row
    return None


def _boot_authority(boot_cmd):
    """The `--host`:`--port` authority a boot command string declares (parsed values, not a
    substring test — AC-PSP-8's "compared as parsed values" requirement)."""
    toks = boot_cmd.split()
    host = toks[toks.index("--host") + 1]
    port = toks[toks.index("--port") + 1]
    return f"{host}:{port}"


def _exercise_authority(exercise_text):
    m = re.search(r"https?://[^\s/]+", exercise_text)
    assert m, f"no absolute URL found in exercise text: {exercise_text!r}"
    parsed = urlparse(m.group(0))
    return f"{parsed.hostname}:{parsed.port}"


def _full_app_doc(**overrides):
    """A minimal, otherwise-valid `app`-shaped profile document (every schema-required key
    present) — the fixture AC-PSP-3 perturbs with an unrecognized `profile_kind`."""
    doc = {
        "id": "x-app", "version": "0.1.0", "requires_core": ">=0.1",
        "matches": {"languages": ["python"], "frameworks": [], "package_managers": ["uv"]},
        "architecture": {"layers": ["a"], "allowed_dependencies": [], "conventions_doc": "c.md"},
        "implementation_skills": ["s.md"],
        "security_checklist": "n/a", "performance_checklist": "n/a",
        "observability": "n/a", "documentation": "n/a",
        "static_validation": {"format": "x", "lint": "x", "typecheck": "x", "build": "x"},
        "test_recipe": {"unit": "x", "integration": "x", "e2e": "x", "coverage_gate": 0},
        "app_exercise_binding": {"boot": "x", "surfaces": [{"kind": "api", "exercise": "x"}]},
    }
    doc.update(overrides)
    return doc


def _full_lib_doc(**overrides):
    """A minimal, otherwise-valid `library`-shaped profile document (no `app_exercise_binding`) —
    the fixture AC-PSP-2 perturbs by dropping `static_validation`/`test_recipe`."""
    doc = {
        "id": "x-lib", "version": "0.1.0", "requires_core": ">=0.1",
        "matches": {"languages": ["python"], "frameworks": [], "package_managers": ["uv"]},
        "architecture": {"layers": ["a"], "allowed_dependencies": [], "conventions_doc": "c.md"},
        "implementation_skills": ["s.md"],
        "security_checklist": "n/a", "performance_checklist": "n/a",
        "observability": "n/a", "documentation": "n/a",
        "profile_kind": "library",
        "static_validation": {"format": "x", "lint": "x", "typecheck": "x", "build": "x"},
        "test_recipe": {"unit": "x", "integration": "x", "e2e": "x", "coverage_gate": 0},
    }
    doc.update(overrides)
    return doc


def _seed_plugin_root(tmp_path, pack_id):
    """A throwaway CLAUDE_PLUGIN_ROOT-shaped tree carrying ONE copy of the real shipped pack, the
    real schema, and a `.claude-plugin/plugin.json` — the same shape
    `tests/test_certify_fixture.py`'s `_seed_stack_profile` builds. `certify_local.py` itself always
    imports the REAL `scripts/foundry_release.py`/`foundry-stack-profile.py` from THIS repo (never
    from the throwaway tree); only the packs/schema/plugin.json vary."""
    import shutil
    root = tmp_path / "plugin"
    shutil.copytree(os.path.join(REPO_ROOT, "schema"), root / "schema")
    (root / ".claude-plugin").mkdir(parents=True)
    with open(root / ".claude-plugin" / "plugin.json", "w", encoding="utf-8") as f:
        json.dump({"name": "foundry", "version": "0.99.0"}, f)
    pack_dst = root / "packs" / "stack-profiles" / pack_id
    shutil.copytree(os.path.join(PACKS_DIR, pack_id), pack_dst)
    return root


def _write_lock_for(root, pack_id, project_dir):
    """A `.foundry/stack-profile.lock` resolving the ONE copied pack, with the digests computed
    exactly as `resolve_lock` verifies them (content sha256 + the conditioned standing-versions
    digest) — never a hand-typed/guessed value."""
    doc, path, csha = sp.load_profile(pack_id, root=str(root), plugin_root=str(root))
    svsha = sp._standing_versions_sha256_of(os.path.dirname(path), doc)
    entry = {"id": pack_id, "version": doc["version"], "sha256": csha}
    if svsha is not None:
        entry["standing_versions_sha256"] = svsha
    foundry_dir = project_dir / ".foundry"
    foundry_dir.mkdir(parents=True, exist_ok=True)
    with open(foundry_dir / "stack-profile.lock", "w", encoding="utf-8") as f:
        json.dump({"profiles": [entry]}, f)


# ═══════════════════════════════════ AC-PSP-1 : back-compat (pre-change-attributable half) ═══════ #

class TestBackCompat:
    def test_existing_profiles_validate_unchanged(self):
        node_doc, _p, _s = sp.load_profile("node-web", root=REPO_ROOT, plugin_root=REPO_ROOT)
        assert sp.profile_kind(node_doc) == "app"
        infra_doc, _p2, _s2 = sp.load_profile("aws-eks-karpenter", root=REPO_ROOT, plugin_root=REPO_ROOT)
        assert sp.profile_kind(infra_doc) == "infra"


# ═══════════════════════════════════ AC-PSP-2 : the library kind is expressible ═══════════════ #

class TestLibraryKindShape:
    def test_library_kind_shape(self):
        doc = _full_lib_doc()
        assert sp.validate_profile(doc, plugin_root=REPO_ROOT) is True

        missing_sv = dict(doc)
        del missing_sv["static_validation"]
        with pytest.raises(sp.StackProfileError, match="static_validation"):
            sp.validate_profile(missing_sv, plugin_root=REPO_ROOT)

        missing_tr = dict(doc)
        del missing_tr["test_recipe"]
        with pytest.raises(sp.StackProfileError, match="test_recipe"):
            sp.validate_profile(missing_tr, plugin_root=REPO_ROOT)


# ═══════════════════════════════════ AC-PSP-3 : an unrecognized kind fails closed ═════════════ #

class TestUnknownProfileKindFailsClosed:
    def test_unknown_profile_kind_fails_closed(self):
        doc = _full_app_doc(profile_kind="bogus")
        with pytest.raises(sp.StackProfileError, match="profile_kind"):
            sp.validate_profile(doc, plugin_root=REPO_ROOT)


# ═══════════════════════════════════ AC-PSP-4 : the service pack ships/loads/resolves ═════════ #

class TestServicePackShips:
    def test_service_profile_loads(self):
        doc, _path, sha = _load("python-uv-service")
        assert doc["id"] == "python-uv-service"
        assert sp.profile_kind(doc) == "app"
        assert sp.core_satisfies(doc["requires_core"], sp.core_version(REPO_ROOT)) is True
        assert len(sha) == 64

    def test_service_referenced_files_exist(self):
        doc, path, _sha = _load("python-uv-service")
        pack_dir = os.path.dirname(path)
        conv = doc["architecture"]["conventions_doc"]
        assert os.path.isfile(os.path.join(pack_dir, conv))
        for skill in doc["implementation_skills"]:
            assert os.path.isfile(os.path.join(pack_dir, skill))

    def test_service_implementation_skill_resolves(self):
        expected = os.path.join(
            PACKS_DIR, "python-uv-service", "skills", "implement-python-uv-service.md")
        assert os.path.isfile(expected)
        doc, _path, _sha = _load("python-uv-service")
        assert "skills/implement-python-uv-service.md" in doc["implementation_skills"]


# ═══════════════════════════════════ AC-PSP-5 : the library pack ships/loads/claims ═══════════ #

class TestLibraryPackShips:
    def test_library_profile_loads(self):
        doc, _path, sha = _load("python-uv-lib")
        assert doc["id"] == "python-uv-lib"
        assert doc.get("profile_kind") == "library"
        assert sp.profile_kind(doc) == "library"
        assert "app_exercise_binding" not in doc
        assert sp.core_satisfies(doc["requires_core"], sp.core_version(REPO_ROOT)) is True
        assert len(sha) == 64

    def test_library_referenced_files_exist(self):
        doc, path, _sha = _load("python-uv-lib")
        pack_dir = os.path.dirname(path)
        conv = doc["architecture"]["conventions_doc"]
        assert os.path.isfile(os.path.join(pack_dir, conv))
        for skill in doc["implementation_skills"]:
            assert os.path.isfile(os.path.join(pack_dir, skill))

    def test_library_implementation_skill_resolves(self):
        expected = os.path.join(
            PACKS_DIR, "python-uv-lib", "skills", "implement-python-uv-lib.md")
        assert os.path.isfile(expected)
        doc, _path, _sha = _load("python-uv-lib")
        assert "skills/implement-python-uv-lib.md" in doc["implementation_skills"]

    def test_library_kind_claim_documented(self):
        conv_path = os.path.join(PACKS_DIR, "python-uv-lib", "conventions.md")
        with open(conv_path, encoding="utf-8") as f:
            text = f.read()
        assert "## Profile-kind claim: this pack asserts no runtime surface" in text
        # operator-facing terms: the claim states REFUSE-by-design and where a bootable atom belongs.
        assert "REFUSE" in text
        assert "python-uv-service" in text


# ═══════════════════════════════════ AC-PSP-6 : sixteen recipe-slot cases ═════════════════════ #

class TestRecipeStringsPinned:
    @pytest.mark.parametrize("pid", PACK_IDS)
    @pytest.mark.parametrize("section,key,expected", RECIPE_TABLE)
    def test_recipe_strings_pinned(self, pid, section, key, expected):
        doc, _path, _sha = _load(pid)
        actual = doc[section][key]
        assert actual == expected, f"{pid} {section}.{key}: {actual!r} != {expected!r}"


# ═══════════════════════════════════ AC-PSP-7 : the negative control — library REFUSEs ═══════ #

class TestCertifyRefusesForLibraryProfile:
    def test_certify_refuses_for_library_profile(self, tmp_path):
        root = _seed_plugin_root(tmp_path, "python-uv-lib")
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        _write_lock_for(root, "python-uv-lib", project_dir)
        with pytest.raises(cl.CertifyError) as excinfo:
            cl.resolve_boot_recipe(str(project_dir), str(root), root=str(root))
        msg = str(excinfo.value)
        assert msg.startswith(cl.REFUSED_PREFIX)
        assert "no boot recipe" in msg


# ═══════════════════════════════════ AC-PSP-8 : the service pack's real, pinned boot target ═══ #

class TestServiceBootTarget:
    def test_service_boot_recipe_byte_equal(self, tmp_path):
        root = _seed_plugin_root(tmp_path, "python-uv-service")
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        _write_lock_for(root, "python-uv-service", project_dir)
        _profile, binding = cl.resolve_boot_recipe(str(project_dir), str(root), root=str(root))
        assert binding["boot"] == BOOT_CMD

    def test_service_health_surface_bound_to_boot_authority(self, tmp_path):
        root = _seed_plugin_root(tmp_path, "python-uv-service")
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        _write_lock_for(root, "python-uv-service", project_dir)
        profile, binding = cl.resolve_boot_recipe(str(project_dir), str(root), root=str(root))
        surfaces = profile["app_exercise_binding"]["surfaces"]
        assert len(surfaces) == 1
        surf = surfaces[0]
        assert surf["kind"] == "api"
        assert surf["exercise"] == SURFACE_EXERCISE
        boot_authority = _boot_authority(binding["boot"])
        exercise_authority = _exercise_authority(surf["exercise"])
        assert boot_authority == "127.0.0.1:8000"
        assert exercise_authority == boot_authority  # bound — never independently edited
        assert "/healthz" in surf["exercise"]
        assert "200 JSON {status:ok}" in surf["exercise"]


# ═══════════════════════════════════ AC-PSP-9 : every pin carries the research date ═══════════ #

class TestPinsTimestamped:
    def _assert_timestamped(self, pid):
        doc, path, _sha = _load(pid)
        pack_dir = os.path.dirname(path)
        sv = doc["standing-versions"]
        mpath = os.path.join(pack_dir, sv["manifest"]["path"])
        cpath = os.path.join(pack_dir, sv["compatibility"]["path"])
        assert os.path.isfile(mpath)
        assert os.path.isfile(cpath)
        with open(mpath, encoding="utf-8") as f:
            m = yaml.safe_load(f)
        with open(cpath, encoding="utf-8") as f:
            c = yaml.safe_load(f)
        assert m["pins_valid_as_of"] == PINS_VALID_AS_OF
        assert c["pins_valid_as_of"] == PINS_VALID_AS_OF

    def test_service_pins_timestamped(self):
        self._assert_timestamped("python-uv-service")

    def test_library_pins_timestamped(self):
        self._assert_timestamped("python-uv-lib")


# ═══════════════════════════════════ AC-PSP-10 : fourteen pin rows x2 passes ══════════════════ #

class TestPinsExactShaped:
    @pytest.mark.parametrize("pid", PACK_IDS)
    @pytest.mark.parametrize("kind,subject,field,expected", PIN_TABLE)
    def test_pins_are_exact_shaped(self, pid, kind, subject, field, expected):
        man = _manifest(pid)
        row = _find_row(man, kind, subject)
        assert row is not None, f"{pid}: no pin row kind={kind!r} subject={subject!r}"
        val = row.get(field)
        assert val is not None, f"{pid} {subject}: missing value field {field!r}"
        assert SHAPE_RE[kind].match(str(val)), \
            f"{pid} {subject}: {val!r} does not match the {kind!r} exact-pin shape"


class TestPinValuesMatchDeclared:
    @pytest.mark.parametrize("pid", PACK_IDS)
    @pytest.mark.parametrize("kind,subject,field,expected", PIN_TABLE)
    def test_pin_values_match_declared(self, pid, kind, subject, field, expected):
        man = _manifest(pid)
        row = _find_row(man, kind, subject)
        assert row is not None, f"{pid}: no pin row kind={kind!r} subject={subject!r}"
        val = row.get(field)
        assert val == expected, f"{pid} {subject}: {val!r} != declared {expected!r}"


# ═══════════════════════════════════ AC-PSP-11 : compatibility anchored on the interpreter ═══ #

class TestCompatibilityAnchor:
    def _assert_anchor(self, pid):
        c = _compat(pid)
        assert c["python_minor"] == "3.13"
        skew = c.get("skew")
        assert isinstance(skew, list) and len(skew) >= 1
        for row in skew:
            assert row.get("component")
            assert row.get("anchor")
            assert row.get("policy")

    def test_service_compatibility_anchor(self):
        self._assert_anchor("python-uv-service")

    def test_library_compatibility_anchor(self):
        self._assert_anchor("python-uv-lib")


# ═══════════════════════════════════ AC-PSP-12 : both packs registered + version-immutable ═══ #

class TestLedgerRegistersPythonPacks:
    def test_ledger_registers_python_packs(self):
        ledger_path = os.path.join(PACKS_DIR, "profile-version-ledger.json")
        with open(ledger_path, encoding="utf-8") as f:
            led = (json.load(f) or {}).get("profiles", {})
        for pid in PACK_IDS:
            doc, path, csha = _load(pid)
            pack_dir = os.path.dirname(path)
            bsha = sp._blueprints_sha256_of(pack_dir)
            svsha = sp._standing_versions_sha256_of(pack_dir, doc)
            ver = doc["version"]
            assert pid in led, f"{pid} not registered in ledger"
            assert ver in led[pid], f"{pid}@{ver} not registered in ledger"
            rec = led[pid][ver] or {}
            assert rec.get("sha256") == csha, f"{pid}@{ver}: sha256 drift"
            assert rec.get("blueprints_sha256") == bsha, f"{pid}@{ver}: blueprints_sha256 drift"
            assert rec.get("standing_versions_sha256") == svsha, \
                f"{pid}@{ver}: standing_versions_sha256 drift"


# ═══════════════════════════════════ AC-PSP-13 : one test module, existing lane ══════════════ #

class TestModuleCollected:
    def test_python_profile_module_collected(self):
        assert os.path.basename(__file__) == "test_python_stack_profile.py"


# ═══════════════════════════════════ AC-PSP-14 : pinned interpreter >= requires-python floor ═ #

class TestInterpreterLineSatisfiesFloor:
    @pytest.mark.parametrize("pid", PACK_IDS)
    def test_interpreter_line_satisfies_floor(self, pid):
        man = _manifest(pid)
        interp_row = _find_row(man, "python_interpreter", "cpython")
        req_row = _find_row(man, "python_requires", "project-metadata")
        interp = tuple(int(x) for x in interp_row["python_version_file"].split("."))
        floor = tuple(int(x) for x in req_row["requires_python"].lstrip(">=").split("."))
        assert interp >= floor, \
            f"{pid}: pinned interpreter {interp} is below the requires-python floor {floor}"
