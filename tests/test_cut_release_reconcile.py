"""tests/test_cut_release_reconcile.py — the cut-release wiring-pin reconcile atom
(AC-CRRC-1..8, feat-foundry-cut-release-reconcile.md).

Verifies the eleven grounded stale-claim sites across the seven enumerated release surfaces
carry either a conformant retired disclosure (naming the retired mechanism, the word "Retired", and
today's load-bearer) or no mention at all, that the one dead step
(`foundry-release-acceptance.py`'s wiring-pin seeding block) is removed and shown dead-at-removal,
that behaviour is preserved (`--selftest` stays GREEN; the doctor-seeding helper still writes a
well-shaped operator registry), that `scripts/foundry-cut-release.py`'s docstrings match the
shipped `preflight()`, that the shipped widened deleted-module absence sweep does not regress, and
that `hooks/foundry-git-discipline.sh`'s CONFIG SOURCE comment is honest with its executable
content byte-unchanged (AC-CRRC-8, SECURITY-FLAGGED — `^hooks/`).

The frozen token set, surface set, allowlist, disclosure matrix, hook-comment expectation,
preflight expectation and operator-registry shape are NOT inlined here — they live in the single
fixture tests/fixtures/release/cut-release-reconcile-sweep.yaml, mirrored verbatim from the
sibling acceptance-contract.yaml preamble (AC-CRRC-1 additionally asserts an independently
transcribed copy below matches that fixture, so a drift between contract, fixture and test fails
the suite).

EVIDENCE RULE (AC-CRRC-7, second limb): the non-regression checks below derive their expected
tokens/paths by READING tests/fixtures/subtraction/deleted-module-sweep.yaml (another atom's
frozen definition, denied_paths here — read-only, never edited) at test time. No restated copy of
that sweep's token_set/surface_set/allowlist exists anywhere in this file or in
tests/fixtures/release/cut-release-reconcile-sweep.yaml.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

import pytest
import yaml

from conftest import REPO_ROOT, load_module

REPO_ROOT_PATH = Path(REPO_ROOT)
FIXTURE_PATH = REPO_ROOT_PATH / "tests" / "fixtures" / "release" / "cut-release-reconcile-sweep.yaml"

# The sibling atom's OWN frozen definition — read at test time, never restated (AC-CRRC-7).
SUBTRACTION_FIXTURE_RELPATH = "tests/fixtures/subtraction/deleted-module-sweep.yaml"
SUBTRACTION_FIXTURE_PATH = REPO_ROOT_PATH / SUBTRACTION_FIXTURE_RELPATH


def _load_fixture():
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_subtraction_fixture():
    with open(SUBTRACTION_FIXTURE_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


FIXTURE = _load_fixture()


# --------------------------------------------------------------------------- sweep primitives ---

def sweep_occurrences(root, token_set, surface_set):
    """{(relpath, token_pattern): [(lineno, matched_text), ...]} for every real hit of a frozen
    token pattern inside an enumerated (explicit-path, not glob) surface file, under `root`."""
    out = {}
    for rel in surface_set:
        f = os.path.join(root, rel)
        if not os.path.isfile(f):
            continue
        text = open(f, encoding="utf-8", errors="replace").read()
        lines = text.split("\n")
        for tok in token_set:
            rx = re.compile(tok)
            hits = [(i, m.group(0)) for i, line in enumerate(lines, 1) for m in [rx.search(line)] if m]
            if hits:
                out[(rel, tok)] = hits
    return out


def uncovered_occurrences(occurrences, allowlist):
    allow_pairs = {(e.get("path"), e.get("token")) for e in allowlist}
    return {k: v for k, v in occurrences.items() if k not in allow_pairs}


def allowlist_well_formed_errors(root, allowlist, token_set):
    errors = []
    tokset = set(token_set)
    for e in allowlist:
        path, token, reason = e.get("path"), e.get("token"), e.get("reason")
        if not path or not os.path.isfile(os.path.join(root, path)):
            errors.append(f"entry path does not resolve in the candidate tree: {e!r}")
        if token not in tokset:
            errors.append(f"entry token is not a member of the frozen token set: {e!r}")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"entry reason is empty/whitespace: {e!r}")
    return errors


def _make_candidate_tree(tmp_path):
    """A throwaway copy of the seven enumerated surfaces, relative-path-preserved, so an
    injected occurrence never touches the real repo."""
    for rel in FIXTURE["surface_set"]:
        src = REPO_ROOT_PATH / rel
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dst)
    return tmp_path


def exec_content(path):
    """The file's content with full-line comments (first non-whitespace char `#`, shebang
    retained) removed — the AC-CRRC-8 'executable content' definition."""
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    out = []
    for i, ln in enumerate(lines):
        stripped = ln.lstrip()
        if i == 0 and stripped.startswith("#!"):
            out.append(ln)
            continue
        if stripped.startswith("#"):
            continue
        out.append(ln)
    return "".join(out)


def _extract_comment_block(text, heading_prefix):
    """The contiguous `#`-prefixed block starting at the first line matching `heading_prefix`,
    ending at the next blank comment line (`#` alone) or non-comment line."""
    lines = text.split("\n")
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith(heading_prefix):
            start = i
            break
    if start is None:
        return ""
    block = [lines[start]]
    for ln in lines[start + 1:]:
        if ln.strip() == "#" or not ln.startswith("#"):
            break
        block.append(ln)
    return "\n".join(block)


# ==================================================================== AC-CRRC-1 =================

# Independently-authored copy of the FROZEN SWEEP DEFINITION from the sibling
# acceptance-contract.yaml preamble (transcribed by hand, not read off the fixture file) — the
# cross-check that catches fixture drift.
_EXPECTED_SURFACE_SET = [
    "scripts/foundry-cut-release.py",
    "scripts/foundry-release-acceptance.py",
    "scripts/foundry-bootstrap.sh",
    "skills/cut-release/SKILL.md",
    "skills/release/SKILL.md",
    "skills/init/SKILL.md",
    "hooks/foundry-git-discipline.sh",
]
_EXPECTED_TOKEN_SET = [
    "WIRING_FILES", "PINHEAL", "write[- ]pin", "write[- ]manifest", "wiring[- ]manifest",
    "wiring[_-]hash\\.pin", "wiring[- ]pin", "wiring[- ]hash", "branch-protection",
    "needs (its own|a dedicated) follow-up", "flagged honestly",
]
_EXPECTED_ZERO_OCC = [
    "scripts/foundry-cut-release.py",
    "scripts/foundry-release-acceptance.py",
    "scripts/foundry-bootstrap.sh",
    "hooks/foundry-git-discipline.sh",
]
_EXPECTED_ALLOWLIST_PAIRS = {
    ("skills/release/SKILL.md", "wiring[- ]pin"),
    ("skills/release/SKILL.md", "wiring[- ]hash"),
    ("skills/release/SKILL.md", "wiring[_-]hash\\.pin"),
    ("skills/release/SKILL.md", "wiring[- ]manifest"),
    ("skills/cut-release/SKILL.md", "wiring[- ]pin"),
    ("skills/cut-release/SKILL.md", "wiring[- ]hash"),
    ("skills/cut-release/SKILL.md", "wiring[_-]hash\\.pin"),
    ("skills/init/SKILL.md", "wiring[- ]pin"),
    ("skills/init/SKILL.md", "wiring[- ]hash"),
    ("skills/init/SKILL.md", "branch-protection"),
}
_EXPECTED_DISCLOSURE_MATRIX = {
    "skills/release/SKILL.md": {
        "mechanism": "foundry-wiring-hash.py", "release": "RETIRED", "load_bearer": "source.sha",
    },
    "skills/cut-release/SKILL.md": {
        "mechanism": "foundry-wiring-hash.py", "release": "Retired",
        "load_bearer": "foundry-release-acceptance",
    },
    "skills/init/SKILL.md": {
        "mechanism": "foundry-wiring-hash.py", "release": "Retired", "load_bearer": "DOCTOR-GREEN",
    },
}


def test_frozen_sweep_definition_matches_the_contract_preamble():
    assert FIXTURE["surface_set"] == _EXPECTED_SURFACE_SET, "fixture surface_set drifted from the frozen preamble"
    assert FIXTURE["token_set"] == _EXPECTED_TOKEN_SET, "fixture token_set drifted from the frozen preamble"
    assert FIXTURE["zero_occurrence_surfaces"] == _EXPECTED_ZERO_OCC, "fixture zero_occurrence_surfaces drifted"

    fixture_pairs = {(e["path"], e["token"]) for e in FIXTURE["allowlist"]}
    assert fixture_pairs == _EXPECTED_ALLOWLIST_PAIRS, "fixture allowlist (path,token) set drifted from the frozen preamble"

    fixture_matrix = {
        row["path"]: {"mechanism": row["mechanism"], "release": row["release"], "load_bearer": row["load_bearer"]}
        for row in FIXTURE["disclosure_matrix"]
    }
    assert fixture_matrix == _EXPECTED_DISCLOSURE_MATRIX, "fixture disclosure_matrix drifted from the frozen preamble"


# ==================================================================== AC-CRRC-2 =================

@pytest.mark.parametrize("row", ["coverage", "well-formedness", "injected-occurrence-fails"])
def test_stale_claim_sweep_is_clean_and_allowlist_governs_both_directions(row, tmp_path):
    if row == "coverage":
        occ = sweep_occurrences(REPO_ROOT, FIXTURE["token_set"], FIXTURE["surface_set"])
        uncovered = uncovered_occurrences(occ, FIXTURE["allowlist"])
        assert uncovered == {}, f"un-allowlisted stale-claim occurrence(s): {sorted(uncovered)}"
        # The strong claim: the four zero-occurrence surfaces carry NOTHING.
        for rel in FIXTURE["zero_occurrence_surfaces"]:
            hits = {k: v for k, v in occ.items() if k[0] == rel}
            assert hits == {}, f"{rel} must carry zero frozen-token occurrences: {hits}"
        return

    if row == "well-formedness":
        errors = allowlist_well_formed_errors(REPO_ROOT, FIXTURE["allowlist"], FIXTURE["token_set"])
        assert errors == [], f"malformed allowlist entries: {errors}"

        bad_path = [{"path": "skills/does-not-exist/SKILL.md", "token": FIXTURE["token_set"][0], "reason": "x"}]
        assert allowlist_well_formed_errors(REPO_ROOT, bad_path, FIXTURE["token_set"]) != []
        bad_token = [{"path": "skills/release/SKILL.md", "token": "not-a-frozen-token", "reason": "x"}]
        assert allowlist_well_formed_errors(REPO_ROOT, bad_token, FIXTURE["token_set"]) != []
        bad_reason = [{"path": "skills/release/SKILL.md", "token": FIXTURE["token_set"][0], "reason": "   "}]
        assert allowlist_well_formed_errors(REPO_ROOT, bad_reason, FIXTURE["token_set"]) != []
        return

    # row == "injected-occurrence-fails": plant a fresh, un-allowlisted stale-claim token in a
    # throwaway candidate-tree copy of a zero-occurrence surface; the sweep must catch it.
    candidate = _make_candidate_tree(tmp_path)
    target = candidate / "scripts" / "foundry-bootstrap.sh"
    with open(target, "a", encoding="utf-8") as f:
        f.write("\n# flagged honestly, this is an injected negative control\n")
    occ = sweep_occurrences(str(candidate), FIXTURE["token_set"], FIXTURE["surface_set"])
    uncovered = uncovered_occurrences(occ, FIXTURE["allowlist"])
    assert ("scripts/foundry-bootstrap.sh", "flagged honestly") in uncovered, (
        "an injected un-allowlisted occurrence must be caught by the sweep"
    )


# ==================================================================== AC-CRRC-3 =================

@pytest.mark.parametrize("row", ["release", "cut-release", "init", "forbidden-deferral"])
def test_each_prose_surface_carries_a_conformant_retired_disclosure(row):
    if row in ("release", "cut-release", "init"):
        path_map = {
            "release": "skills/release/SKILL.md",
            "cut-release": "skills/cut-release/SKILL.md",
            "init": "skills/init/SKILL.md",
        }
        rel = path_map[row]
        matrix_row = next(r for r in FIXTURE["disclosure_matrix"] if r["path"] == rel)
        text = (REPO_ROOT_PATH / rel).read_text(encoding="utf-8")
        assert matrix_row["mechanism"] in text, f"{rel} missing mechanism token {matrix_row['mechanism']!r}"
        assert matrix_row["release"] in text, f"{rel} missing release token {matrix_row['release']!r}"
        assert matrix_row["load_bearer"] in text, f"{rel} missing load-bearer token {matrix_row['load_bearer']!r}"
        if "closure_token" in matrix_row:
            assert matrix_row["closure_token"] in text, f"{rel} missing closure token {matrix_row['closure_token']!r}"
            assert matrix_row["closure_token_2"] in text, f"{rel} missing closure token {matrix_row['closure_token_2']!r}"
        return

    # row == "forbidden-deferral": no enumerated surface may defer to a follow-up.
    for rel in FIXTURE["surface_set"]:
        text = (REPO_ROOT_PATH / rel).read_text(encoding="utf-8")
        for tok in FIXTURE["forbidden_deferral_tokens"]:
            assert re.search(tok, text) is None, f"{rel} still carries forbidden deferral token {tok!r}"


# ==================================================================== AC-CRRC-4 =================

@pytest.mark.parametrize("row", ["no-dead-step", "deadness-evidence"])
def test_release_acceptance_no_longer_seeds_a_wiring_pin(row):
    rel_acc_path = REPO_ROOT_PATH / "scripts" / "foundry-release-acceptance.py"
    text = rel_acc_path.read_text(encoding="utf-8")

    if row == "no-dead-step":
        assert "tree_pin" not in text, "wiring-pin path construction still present"
        assert "wiring-hash.pin" not in text, "wiring-pin file copy target still present"
        assert "wiring-hash check" not in text, "comment still claims a wiring-hash check consumes the seeded pin"
        assert "shutil.copy" not in text, "the dead shutil.copy of the tree's wiring-hash.pin was not removed"
        # shutil stays imported (still used by _check_validators' shutil.which probe).
        assert "import shutil" in text
        assert "shutil.which(" in text
        return

    # row == "deadness-evidence": the branch was provably unreachable, shown by construction.
    tree_pin = REPO_ROOT_PATH / FIXTURE["deadness_evidence"]["tree_pin_path"]
    assert not tree_pin.is_file(), f"the candidate tree must not ship {tree_pin} (the seeding guard's condition)"

    doctor_path = REPO_ROOT_PATH / FIXTURE["deadness_evidence"]["doctor_script"]
    doctor_text = doctor_path.read_text(encoding="utf-8")
    check_names = re.findall(r'_run\(\s*"([a-z-]+)"', doctor_text)
    assert check_names, "could not locate the doctor's registered check names — locator drifted"
    assert not any("wiring" in name for name in check_names), (
        f"foundry-doctor.py still performs a wiring check: {check_names}"
    )


# ==================================================================== AC-CRRC-5 =================

@pytest.mark.parametrize("row", ["selftest-green", "seeding-preserved"])
def test_cut_release_selftest_green_and_acceptance_seeding_preserved(row):
    if row == "selftest-green":
        import subprocess
        import sys
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT_PATH / "scripts" / "foundry-cut-release.py"), "--selftest"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "CUT-RELEASE-SELFTEST-GREEN" in proc.stdout, proc.stdout + proc.stderr
        return

    # row == "seeding-preserved": the REAL _check_doctor helper still writes a well-shaped
    # operator registry into its synthetic project directory. Intercept tempfile.TemporaryDirectory
    # so the (real) temp dir survives the `with` block for inspection — the helper's own code path
    # is exercised unmodified; only its cleanup is deferred.
    fra = load_module("scripts/foundry-release-acceptance.py", "foundry_release_acceptance_crrc5")
    captured = []

    class _KeepDir:
        def __init__(self, *a, **kw):
            self._d = tempfile.mkdtemp()
            captured.append(self._d)

        def __enter__(self):
            return self._d

        def __exit__(self, *exc):
            return False

    orig = fra.tempfile.TemporaryDirectory
    fra.tempfile.TemporaryDirectory = _KeepDir
    try:
        fra._check_doctor(REPO_ROOT)
    finally:
        fra.tempfile.TemporaryDirectory = orig

    assert captured, "the doctor-seeding helper never opened a synthetic project directory"
    proj = captured[-1]
    try:
        reg_path = os.path.join(proj, ".claude", "foundry-operators.json")
        with open(reg_path, encoding="utf-8") as f:
            reg = json.load(f)
        shape = FIXTURE["operator_registry_shape"]
        assert shape["schema_version_type"] == "int"
        assert isinstance(reg.get("schema_version"), int), f"schema_version must be an int: {reg!r}"
        assert shape["operators_type"] == "mapping"
        assert isinstance(reg.get("operators"), dict), f"operators must be a mapping keyed by id: {reg!r}"
        # And no .foundry/ dir is seeded any more (the dead wiring-pin copy is gone).
        assert not os.path.isdir(os.path.join(proj, ".foundry")), ".foundry/ must no longer be seeded"
    finally:
        shutil.rmtree(proj, ignore_errors=True)


# ==================================================================== AC-CRRC-6 =================

def _get_docstrings(path, func_name):
    source = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    module_doc = ast.get_docstring(tree) or ""
    func_doc = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            func_doc = ast.get_docstring(node) or ""
    return module_doc, func_doc


@pytest.mark.parametrize("row", ["preconditions-match", "no-forbidden-claim"])
def test_cut_release_docstrings_name_only_the_shipped_preconditions(row):
    cut_release = load_module("scripts/foundry-cut-release.py", "foundry_cut_release_crrc6")
    expected = FIXTURE["preflight_expectation"]

    if row == "preconditions-match":
        checks = cut_release.preflight(REPO_ROOT, "999.999.999")
        names = [c[0] for c in checks]
        assert names == expected["expected_preconditions"], (
            f"preflight()'s returned check names drifted from the fixture's expected list: {names}"
        )
        return

    # row == "no-forbidden-claim"
    module_doc, func_doc = _get_docstrings(
        REPO_ROOT_PATH / "scripts" / "foundry-cut-release.py", "preflight"
    )
    for tok in expected["forbidden_claim_tokens"]:
        assert tok not in module_doc, f"module docstring still claims {tok!r}"
        assert tok not in func_doc, f"preflight() docstring still claims {tok!r}"


# ==================================================================== AC-CRRC-7 =================

@pytest.mark.parametrize("row", ["sweep-clean", "no-vacuous-entry"])
def test_subtraction_sweep_stays_clean_and_no_allowlist_entry_goes_vacuous(row):
    sub = _load_subtraction_fixture()  # read at test time from the denied, read-only sibling fixture

    if row == "sweep-clean":
        # Re-run the shipped widened deleted-module sweep's own mechanism (glob-based, reused —
        # not the frozen DATA, which is read from `sub` above) over the post-reconcile tree.
        def surface_files(root, surface_set):
            import glob
            files = []
            for pat in surface_set:
                files.extend(sorted(glob.glob(os.path.join(root, pat))))
            return files

        occ = {}
        for f in surface_files(REPO_ROOT, sub["surface_set"]):
            text = open(f, encoding="utf-8", errors="replace").read()
            rel = os.path.relpath(f, REPO_ROOT)
            lines = text.split("\n")
            for tok in sub["token_set"]:
                rx = re.compile(tok)
                hits = [(i, m.group(0)) for i, line in enumerate(lines, 1) for m in [rx.search(line)] if m]
                if hits:
                    occ[(rel, tok)] = hits
        allow_pairs = {(e["path"], e["token"]) for e in sub["allowlist"]}
        uncovered = {k: v for k, v in occ.items() if k not in allow_pairs}
        assert uncovered == {}, f"the shipped widened deleted-module sweep regressed: {sorted(uncovered)}"
        return

    # row == "no-vacuous-entry": each of the three enumerated skill surfaces still contains the
    # deleted-module token its EXISTING (sibling-fixture) allowlist entry tolerates.
    lookup_paths = FIXTURE["sibling_sweep_lookup_paths"]
    relevant = [e for e in sub["allowlist"] if e["path"] in lookup_paths]
    assert relevant, "no sibling-fixture allowlist entries found for the three enumerated skill surfaces"
    for entry in relevant:
        text = (REPO_ROOT_PATH / entry["path"]).read_text(encoding="utf-8")
        assert re.search(entry["token"], text) is not None, (
            f"{entry['path']}'s sibling-sweep allowlist entry for {entry['token']!r} is now VACUOUS "
            "(no matching occurrence left) — this atom must not leave a dead allowlist entry behind"
        )


# ==================================================================== AC-CRRC-8 =================

@pytest.mark.parametrize("row", ["forbidden-absent", "coverage-present", "byte-unchanged"])
def test_git_discipline_config_comment_is_honest_and_body_byte_unchanged(row):
    hook_path = REPO_ROOT_PATH / "hooks" / "foundry-git-discipline.sh"
    text = hook_path.read_text(encoding="utf-8")
    expected = FIXTURE["hook_comment_expectation"]

    if row == "forbidden-absent":
        for frag in expected["forbidden_claim_fragments"]:
            assert frag not in text, f"the hook still carries the false claim fragment {frag!r}"
        return

    if row == "coverage-present":
        config_block = _extract_comment_block(text, "# CONFIG SOURCE")
        assert config_block, "could not locate the CONFIG SOURCE comment block — locator drifted"
        for frag in expected["required_coverage_fragments"]:
            assert frag in config_block, f"CONFIG SOURCE comment missing required coverage fragment {frag!r}"
        return

    # row == "byte-unchanged": the executable content digest matches the pre-change fixture value.
    digest = hashlib.sha256(exec_content(hook_path).encode("utf-8")).hexdigest()
    assert digest == expected["executable_content_sha256"], (
        "hooks/foundry-git-discipline.sh's EXECUTABLE content changed — this must be a comment-only edit"
    )
