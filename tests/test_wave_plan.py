"""tests/test_wave_plan.py — (feat-foundry-wave-plan): `scripts/foundry-wave-plan.py`.

Exercises the pure `compute_wave_plan`/`build_plan_doc` functions directly (never shelling out for
the behavioral assertions) plus one subprocess round-trip for the CLI surface (`--check`, exit codes,
determinism across two REAL process invocations, and the committed 3-atom golden fixture).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
import yaml

from conftest import REPO_ROOT, load_module

wp = load_module("scripts/foundry-wave-plan.py", "foundry_wave_plan")
fr = load_module("scripts/foundry_release.py", "foundry_release_wp")

WAVE_PLAN_SCRIPT = os.path.join(REPO_ROOT, "scripts", "foundry-wave-plan.py")
FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures")


def _write_manifest(tmp_path, doc, name="release.yaml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return str(path)


def _atom(id, depends_on=None, paths=None, journeys=None):
    d = {
        "id": id,
        "spec_ref": f"specs/{id}.md",
        "contract_ref": f"specs/{id}-contract.yaml",
        "depends_on": depends_on or [],
    }
    if paths is not None:
        d["paths"] = paths
    if journeys is not None:
        d["journeys"] = journeys
    return d


def _manifest(atoms, id="rel"):
    return {"id": id, "description": "test", "state": "backlog", "atoms": atoms}


# ============================================================================== cycle refused === #

class TestCycleRefused:
    def test_two_atom_cycle_raises_naming_both(self, tmp_path):
        doc = _manifest([_atom("a", depends_on=["b"]), _atom("b", depends_on=["a"])])
        path = _write_manifest(tmp_path, doc)
        with pytest.raises(wp.WavePlanError) as exc:
            wp.load_manifest(path)
        msg = str(exc.value)
        assert "cycle" in msg
        assert "a" in msg and "b" in msg

    def test_three_atom_cycle_raises(self, tmp_path):
        doc = _manifest([
            _atom("a", depends_on=["c"]),
            _atom("b", depends_on=["a"]),
            _atom("c", depends_on=["b"]),
        ])
        path = _write_manifest(tmp_path, doc)
        with pytest.raises(wp.WavePlanError):
            wp.load_manifest(path)

    def test_cycle_refused_via_cli_exit_2(self, tmp_path):
        doc = _manifest([_atom("a", depends_on=["b"]), _atom("b", depends_on=["a"])])
        path = _write_manifest(tmp_path, doc)
        r = subprocess.run([sys.executable, WAVE_PLAN_SCRIPT, path], capture_output=True, text=True)
        assert r.returncode == 2
        assert "cycle" in r.stderr
        assert r.stdout == ""   # fail-closed: no partial/best-effort JSON ever emitted


# ======================================================================= overlap splits waves === #

class TestOverlapSplitsWaves:
    def test_overlapping_paths_no_dep_edge_forces_separate_waves(self, tmp_path):
        doc = _manifest([
            _atom("core", paths=["src/shared/**"]),
            _atom("widget", paths=["src/shared/utils.py"]),   # overlaps core, no depends_on edge
        ])
        path = _write_manifest(tmp_path, doc)
        release = wp.load_manifest(path)
        waves, meta = wp.compute_wave_plan(release)
        assert meta["core"]["wave"] != meta["widget"]["wave"]
        assert waves == [["core"], ["widget"]]

    def test_disjoint_paths_no_dep_edge_share_a_wave(self, tmp_path):
        doc = _manifest([
            _atom("alpha", paths=["src/alpha/**"]),
            _atom("beta", paths=["src/beta/**"]),
        ])
        path = _write_manifest(tmp_path, doc)
        release = wp.load_manifest(path)
        waves, meta = wp.compute_wave_plan(release)
        assert meta["alpha"]["wave"] == meta["beta"]["wave"] == 0
        assert waves == [["alpha", "beta"]]

    def test_no_declared_paths_never_forces_a_split(self, tmp_path):
        doc = _manifest([_atom("a"), _atom("b")])   # neither declares `paths`
        path = _write_manifest(tmp_path, doc)
        release = wp.load_manifest(path)
        waves, _meta = wp.compute_wave_plan(release)
        assert waves == [["a", "b"]]

    @pytest.mark.parametrize("a,b,expect_overlap", [
        ("src/foo/**", "src/foo/bar.py", True),
        ("src/foo/bar.py", "src/foo/**", True),
        ("src/foo/**", "src/foo/**", True),
        ("src/foo", "src/foo/bar/**", True),
        ("src/foo/**", "src/foobar/**", False),
        ("src/foo/**", "src/bar/**", False),
        # PR #271 security finding 1 (under-split fixes):
        ("scripts/*.py", "scripts/foo.py", True),   # a mid-name glob metachar, not a bare trailing /*
        ("./src/foo", "src/foo", True),             # a leading './' must normalize away
        ("src/foo", "src/foo2", False),             # a same-prefix SIBLING dir must NOT overlap
    ])
    def test_paths_overlap_prefix_semantics(self, a, b, expect_overlap):
        assert wp.paths_overlap(a, b) is expect_overlap

    def test_paths_overlap_normalizes_leading_slash(self):
        assert wp.paths_overlap("/src/foo", "src/foo") is True

    def test_paths_overlap_mid_segment_glob_truncates_to_literal_prefix(self):
        # 'src/*/logs/**' -- a metachar segment BEFORE the trailing '/**' -- truncates to 'src',
        # so it overlaps anything under src/, not just literal 'src/*/logs/...' paths.
        assert wp.paths_overlap("src/*/logs/**", "src/other/logs/x.py") is True
        assert wp.paths_overlap("src/*/logs/**", "unrelated/**") is False


# ===================================================================== dependency ordering === #

class TestDependencyOrdering:
    def test_dependent_lands_strictly_after_its_dependency(self, tmp_path):
        doc = _manifest([_atom("a"), _atom("b", depends_on=["a"])])
        path = _write_manifest(tmp_path, doc)
        release = wp.load_manifest(path)
        _waves, meta = wp.compute_wave_plan(release)
        assert meta["b"]["wave"] > meta["a"]["wave"]

    def test_diamond_dependency_chain(self, tmp_path):
        # a -> {b, c} -> d : d must be strictly after both b and c.
        doc = _manifest([
            _atom("a"),
            _atom("b", depends_on=["a"]),
            _atom("c", depends_on=["a"]),
            _atom("d", depends_on=["b", "c"]),
        ])
        path = _write_manifest(tmp_path, doc)
        release = wp.load_manifest(path)
        waves, meta = wp.compute_wave_plan(release)
        assert meta["a"]["wave"] == 0
        assert meta["b"]["wave"] == 1 and meta["c"]["wave"] == 1
        assert meta["d"]["wave"] == 2
        assert waves == [["a"], ["b", "c"], ["d"]]

    def test_foundation_atoms_first(self, tmp_path):
        # An atom with no dependents and no dependencies is a foundation atom -> wave 0.
        doc = _manifest([_atom("foundation"), _atom("leaf", depends_on=["foundation"])])
        path = _write_manifest(tmp_path, doc)
        release = wp.load_manifest(path)
        _waves, meta = wp.compute_wave_plan(release)
        assert meta["foundation"]["wave"] == 0


# ================================================================================ determinism === #

class TestDeterminism:
    def test_two_in_process_runs_byte_identical(self, tmp_path):
        doc = _manifest([
            _atom("z", paths=["src/z/**"]),
            _atom("a", paths=["src/z/child/**"]),   # overlaps z -> exercises the split path too
            _atom("m", depends_on=["a"]),
        ])
        path = _write_manifest(tmp_path, doc)
        release1 = wp.load_manifest(path)
        release2 = wp.load_manifest(path)
        doc1 = json.dumps(wp.build_plan_doc(release1), indent=2, sort_keys=True)
        doc2 = json.dumps(wp.build_plan_doc(release2), indent=2, sort_keys=True)
        assert doc1 == doc2

    def test_two_real_process_invocations_byte_identical(self, tmp_path):
        doc = _manifest([_atom("a"), _atom("b", depends_on=["a"]), _atom("c", depends_on=["a"])])
        path = _write_manifest(tmp_path, doc)
        r1 = subprocess.run([sys.executable, WAVE_PLAN_SCRIPT, path], capture_output=True, text=True)
        r2 = subprocess.run([sys.executable, WAVE_PLAN_SCRIPT, path], capture_output=True, text=True)
        assert r1.returncode == 0 and r2.returncode == 0
        assert r1.stdout == r2.stdout
        assert r1.stdout != ""


# =============================================================== --check mode (validate only) === #

class TestCheckMode:
    def test_check_mode_exits_zero_and_emits_no_json(self, tmp_path):
        doc = _manifest([_atom("a"), _atom("b", depends_on=["a"])])
        path = _write_manifest(tmp_path, doc)
        r = subprocess.run([sys.executable, WAVE_PLAN_SCRIPT, "--check", path],
                            capture_output=True, text=True)
        assert r.returncode == 0
        assert "OK" in r.stdout
        with pytest.raises(json.JSONDecodeError):
            json.loads(r.stdout)   # --check never emits the wave-plan JSON body

    def test_check_mode_still_refuses_a_cycle(self, tmp_path):
        doc = _manifest([_atom("a", depends_on=["b"]), _atom("b", depends_on=["a"])])
        path = _write_manifest(tmp_path, doc)
        r = subprocess.run([sys.executable, WAVE_PLAN_SCRIPT, "--check", path],
                            capture_output=True, text=True)
        assert r.returncode == 2


# ========================== --check: paths[] vs contract scope.allowed_paths (PR #271 sec-2) === #

def _write_contract(tmp_path, name, allowed_paths):
    path = tmp_path / name
    doc = {"spec_ref": "specs/x.md", "spec_sha256": "0" * 64,
           "scope": {"allowed_paths": allowed_paths, "denied_paths": []}, "checkpoints": []}
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return name   # contract_ref is manifest-relative-to-project-dir, so return the bare name


class TestPathsSubsetOfContract:
    def test_subset_passes_no_violation(self, tmp_path):
        _write_contract(tmp_path, "a-contract.yaml", ["src/a/**", "src/shared/**"])
        doc = _manifest([_atom("a", paths=["src/a/**"])])
        for a in doc["atoms"]:
            a["contract_ref"] = "a-contract.yaml"
        path = _write_manifest(tmp_path, doc)
        release = wp.load_manifest(path)
        violations, skipped = wp.check_paths_subset_of_contract(release, project_dir=str(tmp_path))
        assert violations == []
        assert skipped == []

    def test_non_subset_paths_is_a_violation(self, tmp_path):
        _write_contract(tmp_path, "a-contract.yaml", ["src/a/**"])
        doc = _manifest([_atom("a", paths=["src/a/**", "src/OTHER/**"])])
        for a in doc["atoms"]:
            a["contract_ref"] = "a-contract.yaml"
        path = _write_manifest(tmp_path, doc)
        release = wp.load_manifest(path)
        violations, skipped = wp.check_paths_subset_of_contract(release, project_dir=str(tmp_path))
        assert len(violations) == 1
        assert "src/OTHER/**" in violations[0]
        assert skipped == []

    def test_unresolvable_contract_skips_not_violates(self, tmp_path):
        doc = _manifest([_atom("a", paths=["src/a/**"])])
        doc["atoms"][0]["contract_ref"] = "does-not-exist.yaml"
        path = _write_manifest(tmp_path, doc)
        release = wp.load_manifest(path)
        violations, skipped = wp.check_paths_subset_of_contract(release, project_dir=str(tmp_path))
        assert violations == []
        assert len(skipped) == 1
        assert "does-not-exist.yaml" in skipped[0]

    def test_atom_with_no_declared_paths_is_never_checked(self, tmp_path):
        # no `paths` at all -- not even a candidate for the subset check (nothing declared to
        # compare), regardless of whether its contract resolves.
        doc = _manifest([_atom("a")])
        doc["atoms"][0]["contract_ref"] = "does-not-exist.yaml"
        path = _write_manifest(tmp_path, doc)
        release = wp.load_manifest(path)
        violations, skipped = wp.check_paths_subset_of_contract(release, project_dir=str(tmp_path))
        assert violations == [] and skipped == []

    def test_cli_check_mode_refuses_on_violation(self, tmp_path):
        _write_contract(tmp_path, "a-contract.yaml", ["src/a/**"])
        doc = _manifest([_atom("a", paths=["src/a/**", "src/OTHER/**"])])
        doc["atoms"][0]["contract_ref"] = "a-contract.yaml"
        path = _write_manifest(tmp_path, doc)
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(tmp_path))
        r = subprocess.run([sys.executable, WAVE_PLAN_SCRIPT, "--check", path],
                            capture_output=True, text=True, env=env)
        assert r.returncode == 2
        assert "src/OTHER/**" in r.stderr

    def test_cli_check_mode_ok_on_subset(self, tmp_path):
        _write_contract(tmp_path, "a-contract.yaml", ["src/a/**"])
        doc = _manifest([_atom("a", paths=["src/a/**"])])
        doc["atoms"][0]["contract_ref"] = "a-contract.yaml"
        path = _write_manifest(tmp_path, doc)
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(tmp_path))
        r = subprocess.run([sys.executable, WAVE_PLAN_SCRIPT, "--check", path],
                            capture_output=True, text=True, env=env)
        assert r.returncode == 0
        assert "OK" in r.stdout


# ================================================================= missing manifest / schema === #

class TestFailClosedInputs:
    def test_missing_manifest_file_fails_closed(self, tmp_path):
        with pytest.raises(wp.WavePlanError):
            wp.load_manifest(str(tmp_path / "does-not-exist.yaml"))

    def test_malformed_yaml_fails_closed(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text("atoms: [this is not: valid: yaml: at all", encoding="utf-8")
        with pytest.raises(wp.WavePlanError):
            wp.load_manifest(str(path))

    def test_dangling_depends_on_fails_closed(self, tmp_path):
        doc = _manifest([_atom("a", depends_on=["ghost"])])
        path = _write_manifest(tmp_path, doc)
        with pytest.raises(wp.WavePlanError):
            wp.load_manifest(path)


# ============================================================ the golden fixture (committed) === #

class TestGoldenFixture:
    def test_golden_manifest_matches_committed_expected_json(self):
        manifest_path = os.path.join(FIXTURES, "wave-plan-golden-manifest.yaml")
        expected_path = os.path.join(FIXTURES, "wave-plan-golden-expected.json")
        release = wp.load_manifest(manifest_path)
        actual = json.dumps(wp.build_plan_doc(release), indent=2, sort_keys=True)
        with open(expected_path, encoding="utf-8") as f:
            expected = f.read()
        assert actual.rstrip("\n") == expected.rstrip("\n")

    def test_golden_fixture_demonstrates_both_rules(self):
        manifest_path = os.path.join(FIXTURES, "wave-plan-golden-manifest.yaml")
        release = wp.load_manifest(manifest_path)
        waves, meta = wp.compute_wave_plan(release)
        # path-overlap split: core & widget-a share no depends_on edge but DO overlap paths.
        assert meta["core"]["wave"] != meta["widget-a"]["wave"]
        # dependency ordering: widget-b depends_on widget-a.
        assert meta["widget-b"]["wave"] > meta["widget-a"]["wave"]
        assert waves == [["core"], ["widget-a"], ["widget-b"]]

    def test_golden_fixture_check_mode_green_via_cli(self):
        manifest_path = os.path.join(FIXTURES, "wave-plan-golden-manifest.yaml")
        r = subprocess.run([sys.executable, WAVE_PLAN_SCRIPT, "--check", manifest_path],
                            capture_output=True, text=True)
        assert r.returncode == 0
        assert "OK" in r.stdout


# ======================================================= foundry_release.py schema additions === #

class TestReleaseSchemaAdditions:
    def test_paths_and_journeys_optional_default_empty(self, tmp_path):
        doc = _manifest([_atom("a")])
        path = os.path.join(str(tmp_path), "r.yaml")
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(doc, f)
        release = fr._validate(yaml.safe_load(open(path, encoding="utf-8")), expected_id=None)
        assert release.by_id["a"].paths == []
        assert release.by_id["a"].journeys == []

    def test_unknown_atom_field_still_rejected(self, tmp_path):
        doc = _manifest([_atom("a")])
        doc["atoms"][0]["bogus"] = "nope"
        with pytest.raises(fr.ReleaseError):
            fr._validate(doc, expected_id=None)

    def test_save_release_round_trips_populated_fields_only(self, tmp_path):
        atoms_doc = [_atom("a", paths=["src/a/**"], journeys=["jt-1"]), _atom("b")]
        doc = _manifest(atoms_doc, id="rt")
        release = fr._validate(doc, expected_id=None)
        out_path = os.path.join(str(tmp_path), ".foundry", "releases", "rt", "release.yaml")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        fr.save_release(release, project_dir=str(tmp_path))
        with open(out_path, encoding="utf-8") as f:
            saved = yaml.safe_load(f)
        a_doc = next(a for a in saved["atoms"] if a["id"] == "a")
        b_doc = next(a for a in saved["atoms"] if a["id"] == "b")
        assert a_doc["paths"] == ["src/a/**"]
        assert a_doc["journeys"] == ["jt-1"]
        assert "paths" not in b_doc and "journeys" not in b_doc


# =========== the release-wave.js dispatch contract (PR #271 BLOCK — id vs spec-path) =========== #
#
# `foundry-wave-plan.py`'s `waves` are ATOM IDS; `workflows/release-wave.js` dispatches each wave
# element as a SPEC PATH (interpolated into an `agent()` prompt). `skills/release/SKILL.md` step 3
# documents the required mapping (`plan.atoms[id].spec_ref`) BEFORE calling the workflow, and
# `release-wave.js` now independently validates every dispatched element against a fail-closed
# spec-path shape (charset `^[A-Za-z0-9._/-]+$`, contains '/', ends '.md', no '..' segment) as an
# injection guard. This is a PURE PYTHON PORT of that exact JS rule (`isValidSpecPath` in
# `workflows/release-wave.js`) — kept byte-for-byte equivalent so the contract is pinned on BOTH
# sides: a change to either regex without the other should make ONE of these tests fail.

import re

_SPEC_PATH_CHARSET_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


def _is_valid_spec_path(x):
    """Python port of `isValidSpecPath` in workflows/release-wave.js — keep in sync by hand; this
    duplication IS the pinning mechanism (a divergence between the two shows up as a test failure
    here without needing a JS test runner)."""
    if not isinstance(x, str) or not x:
        return False
    if not _SPEC_PATH_CHARSET_RE.match(x):
        return False
    if "/" not in x:
        return False
    if not x.endswith(".md"):
        return False
    if ".." in x.split("/"):
        return False
    return True


class TestReleaseWaveDispatchContract:
    def test_ported_regex_matches_js_examples(self):
        # Positive: a well-formed spec path.
        assert _is_valid_spec_path("specs/features/foundry/example/core/feat-core.md") is True
        # Negative: a bare atom id (the exact BLOCK this test class exists to pin against).
        assert _is_valid_spec_path("core") is False
        assert _is_valid_spec_path("widget-a") is False
        # Negative: no '/' at all.
        assert _is_valid_spec_path("feat-core.md") is False
        # Negative: wrong extension.
        assert _is_valid_spec_path("specs/features/foundry/example/core/feat-core.txt") is False
        # Negative: '..' traversal segment.
        assert _is_valid_spec_path("specs/../../etc/passwd.md") is False
        # Negative: a shell/prompt-injection-shaped fragment (disallowed charset).
        assert _is_valid_spec_path("specs/foo.md; rm -rf /") is False
        assert _is_valid_spec_path("specs/foo.md`whoami`") is False
        # Negative: non-string / empty.
        assert _is_valid_spec_path(None) is False
        assert _is_valid_spec_path("") is False
        assert _is_valid_spec_path(123) is False

    def test_golden_fixture_atom_ids_are_NOT_valid_spec_paths(self):
        """The wave-plan JSON's `waves` are atom ids — proves the id-vs-path BLOCK was real: every
        raw id in the golden fixture's `waves` FAILS the spec-path shape check."""
        expected_path = os.path.join(FIXTURES, "wave-plan-golden-expected.json")
        with open(expected_path, encoding="utf-8") as f:
            plan = json.load(f)
        raw_ids = [aid for wave in plan["waves"] for aid in wave]
        assert raw_ids  # sanity: the golden fixture actually has atoms
        for aid in raw_ids:
            assert _is_valid_spec_path(aid) is False, (
                f"atom id {aid!r} unexpectedly passed the spec-path check — "
                f"the golden fixture's ids should never look like paths"
            )

    def test_golden_fixture_mapped_spec_refs_ARE_valid_spec_paths(self):
        """Apply the DOCUMENTED mapping (skills/release/SKILL.md step 3:
        `plan.atoms[id].spec_ref`) over the golden fixture's `waves`, then assert every mapped
        element passes release-wave.js's fail-closed dispatch-validation rule — the mapping step
        is what makes the wave-plan's output dispatch-ready, and this pins that contract on both
        the Python (wave-plan) and JS (release-wave.js) sides at once."""
        expected_path = os.path.join(FIXTURES, "wave-plan-golden-expected.json")
        with open(expected_path, encoding="utf-8") as f:
            plan = json.load(f)
        mapped_waves = [[plan["atoms"][aid]["spec_ref"] for aid in wave] for wave in plan["waves"]]
        for wave in mapped_waves:
            for spec_ref in wave:
                assert _is_valid_spec_path(spec_ref) is True, (
                    f"mapped spec_ref {spec_ref!r} failed the release-wave.js dispatch-validation "
                    f"rule — the documented id->spec_ref mapping should always produce a "
                    f"dispatch-ready path"
                )
