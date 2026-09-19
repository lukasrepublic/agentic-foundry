"""tests/test_release.py — converted from scripts/foundry_checks/{foundry-verify,
foundry-release-acceptance, release-run-state, self-host-code-repo}.py.

Ports the real behavioral assertions those four drop-in selftests drove against the shipped
release/verify surface: `scripts/foundry-verify.py` (the profile-parameterized static-validation +
test executor), `scripts/foundry-release-acceptance.py` (the pre-cut acceptance gate over a
candidate plugin tree), `scripts/foundry_release.py` (the machine-derived run-state ledger +
`_resolve_repo` self-host default), over throwaway temp fixtures and the real shipped tree.
CLI/doctor scaffolding is dropped; the computed fixtures/assertions are kept.

NOTE (finding, not papered over): the release-run-state fixture below seeds a `src/` directory
under the fixture project root before authorizing — the ORIGINAL drop-in check's fixture used
`scope.allowed_paths: ["src/**"]` with NO matching directory, which the (later-added)
allowed-paths-grounding reality gate (ER #179, `foundry_contract.allowed_paths_grounding_errors`)
now fail-closes on, so EVERY atom's real `/foundry:authorize` step failed and the entire original
selftest reported RED on current main (confirmed by directly invoking `foundry-audit-record.py` +
`foundry-authorize.py` against the unmodified original fixture shape). That is a rotted TEST
FIXTURE colliding with a later, correctly-behaving hardening — not a product-code regression — so
the fixture is corrected here (a directory is seeded to satisfy the glob) rather than the assertion
being weakened; every downstream assertion is otherwise unchanged from the original.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import pytest
import yaml

from conftest import REPO_ROOT, load_module

release = load_module("scripts/foundry_release.py", "foundry_release")
sp = load_module("scripts/foundry-stack-profile.py", "foundry_stack_profile_verify")


# ==================================================================== foundry-verify.py ==== #

def _seed_verify_plugin_root(tmp_path, *, static_cmds, test_cmds):
    """A throwaway plugin_root carrying the real schema/ + a minimal synthetic app profile +
    a matching stack-profile.lock, so run_verify dispatches real (trivial) shell commands."""
    plugin_root = tmp_path / "plugin"
    project_dir = tmp_path / "project"
    plugin_root.mkdir()
    project_dir.mkdir()
    import shutil
    shutil.copytree(os.path.join(REPO_ROOT, "schema"), plugin_root / "schema")
    (plugin_root / ".claude-plugin").mkdir()
    with open(plugin_root / ".claude-plugin" / "plugin.json", "w", encoding="utf-8") as f:
        json.dump({"name": "foundry", "version": "0.99.0"}, f)

    pack_dir = plugin_root / "packs" / "stack-profiles" / "demo"
    pack_dir.mkdir(parents=True)
    (pack_dir / "conventions.md").write_text("conventions", encoding="utf-8")
    doc = {
        "id": "demo", "version": "1.0.0", "requires_core": ">=0.1",
        "matches": {"languages": ["x"], "frameworks": ["x"], "package_managers": ["x"]},
        "architecture": {"layers": ["a"], "allowed_dependencies": [], "conventions_doc": "conventions.md"},
        "implementation_skills": ["conventions.md"],
        "static_validation": static_cmds,
        "test_recipe": dict(test_cmds, coverage_gate=80),
        "security_checklist": "n/a", "performance_checklist": "n/a",
        "observability": "n/a", "documentation": "n/a",
        "app_exercise_binding": {"boot": "true", "surfaces": [
            {"kind": "cli", "exercise": "run `true` and assert exit 0."}]},
    }
    with open(pack_dir / "stack-profile.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f)

    csha = sp.content_sha256(str(pack_dir / "stack-profile.yaml"))
    lock = {"profiles": [{"id": "demo", "version": "1.0.0", "sha256": csha}]}
    os.makedirs(project_dir / ".foundry", exist_ok=True)
    with open(project_dir / ".foundry" / "stack-profile.lock", "w", encoding="utf-8") as f:
        json.dump(lock, f)
    return str(plugin_root), str(project_dir)


STATIC_KEYS_OK = {"format": "true", "lint": "true", "typecheck": "true", "build": "true"}
TEST_KEYS_OK = {"unit": "true", "integration": "true", "e2e": "true"}


class TestRunVerify:
    def test_no_lock_is_skip(self, tmp_path):
        fv = load_module("scripts/foundry-verify.py", "foundry_verify_a")
        plugin_root, _ = _seed_verify_plugin_root(tmp_path, static_cmds=STATIC_KEYS_OK, test_cmds=TEST_KEYS_OK)
        empty_project = tmp_path / "empty"
        empty_project.mkdir()
        result = fv.run_verify(project_dir=str(empty_project), plugin_root=plugin_root, root=plugin_root)
        assert result["verdict"] == "skip"

    def test_passing_recipe_passes(self, tmp_path):
        fv = load_module("scripts/foundry-verify.py", "foundry_verify_b")
        plugin_root, project_dir = _seed_verify_plugin_root(
            tmp_path, static_cmds=STATIC_KEYS_OK, test_cmds=TEST_KEYS_OK)
        result = fv.run_verify(project_dir=project_dir, plugin_root=plugin_root, root=plugin_root)
        assert result["verdict"] == "pass", result
        assert result["profile_id"] == "demo"
        assert result["coverage_gate"] == 80
        assert len(result["records"]) == 7  # 4 static_validation + 3 test_recipe, dispatch not redefinition.

    def test_failing_command_fails_closed(self, tmp_path):
        fv = load_module("scripts/foundry-verify.py", "foundry_verify_c")
        bad_static = dict(STATIC_KEYS_OK, lint="false")
        plugin_root, project_dir = _seed_verify_plugin_root(
            tmp_path, static_cmds=bad_static, test_cmds=TEST_KEYS_OK)
        result = fv.run_verify(project_dir=project_dir, plugin_root=plugin_root, root=plugin_root)
        assert result["verdict"] == "fail"
        lint_record = next(r for r in result["records"] if r["key"] == "lint")
        assert lint_record["passed"] is False

    def test_lock_pointing_at_unresolvable_profile_is_hard_fail(self, tmp_path):
        fv = load_module("scripts/foundry-verify.py", "foundry_verify_d")
        plugin_root, project_dir = _seed_verify_plugin_root(
            tmp_path, static_cmds=STATIC_KEYS_OK, test_cmds=TEST_KEYS_OK)
        with open(os.path.join(project_dir, ".foundry", "stack-profile.lock"), "w", encoding="utf-8") as f:
            json.dump({"profiles": [{"id": "demo", "version": "1.0.0", "sha256": "0" * 64}]}, f)
        result = fv.run_verify(project_dir=project_dir, plugin_root=plugin_root, root=plugin_root)
        assert result["verdict"] == "fail"
        assert "does not resolve" in result["reason"]


# ==================================================== foundry-release-acceptance.py ==== #

class TestReleaseAcceptance:
    def test_hooks_declared_present_and_executable_on_real_tree(self):
        fra = load_module("scripts/foundry-release-acceptance.py", "foundry_release_acceptance")
        checks = fra._check_hooks_executable(REPO_ROOT)
        failures = [c for c in checks if not c["ok"]]
        assert failures == [], failures

    def test_candidate_tree_own_doctor_is_green(self):
        """AC-RELACC-3: the candidate (this very) tree's own scripts/foundry-doctor.py must be
        DOCTOR-GREEN over a minimal synthetic project dir — the acceptance evidence that matters
        most after the Phase 3 doctor rewrite."""
        fra = load_module("scripts/foundry-release-acceptance.py", "foundry_release_acceptance2")
        result = fra._check_doctor(REPO_ROOT)
        assert result[0]["ok"] is True, result[0]["detail"]

    def test_manifest_validates_via_first_party_cli(self):
        """AC-RELACC-1 (manifest half only — `plugin tag --dry-run` requires a clean git tree,
        which is not guaranteed in a working-branch pytest run)."""
        import shutil
        if shutil.which("claude") is None:
            pytest.skip("`claude` CLI not on PATH")
        proc = subprocess.run(["claude", "plugin", "validate", REPO_ROOT, "--strict"],
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stdout + proc.stderr


# ======================================================== foundry_release.py: resolve_repo ==== #

class TestResolveRepo:
    def test_workspace_sentinel_resolves_to_project_dir(self, tmp_path):
        assert release._resolve_repo("workspace", str(tmp_path)) == str(tmp_path)

    def test_unset_target_repo_self_host_default_no_config(self, tmp_path):
        assert release._resolve_repo(None, str(tmp_path)) == str(tmp_path)

    def test_unset_target_repo_honors_self_host_code_repo(self, tmp_path):
        os.makedirs(tmp_path / ".claude")
        os.makedirs(tmp_path / "code-repo")
        with open(tmp_path / ".claude" / "foundry-project.json", "w", encoding="utf-8") as f:
            json.dump({"self_host_code_repo": "code-repo",
                       "repos": {"code-repo": {"path": "code-repo"}}}, f)
        resolved = release._resolve_repo(None, str(tmp_path))
        assert resolved == str(tmp_path / "code-repo")

    def test_explicit_target_repo_key_resolves_via_inventory(self, tmp_path):
        os.makedirs(tmp_path / ".claude")
        os.makedirs(tmp_path / "elsewhere")
        with open(tmp_path / ".claude" / "foundry-project.json", "w", encoding="utf-8") as f:
            json.dump({"repos": {"my-key": {"path": "elsewhere"}}}, f)
        resolved = release._resolve_repo("my-key", str(tmp_path))
        assert resolved == str(tmp_path / "elsewhere")

    def test_unresolvable_key_falls_back_to_project_dir(self, tmp_path):
        assert release._resolve_repo("no-such-key", str(tmp_path)) == str(tmp_path)


# ======================================================== foundry_release.py: acceptance ==== #
# (feat-*-certification): the `acceptance:` optional top-level field + `append_acceptance`
# + the `accept` CLI verb — a PRACTICE record (date, operator, verdict, note), never a gate.

def _write_minimal_release(project_dir, rel_id, *, acceptance=None):
    """The smallest manifest `_validate()` accepts (no spec/contract file existence needed — that's
    only checked at the `plan` transition, not at `load_release`/schema-validate time)."""
    rel_dir = os.path.join(project_dir, ".foundry", "releases", rel_id)
    os.makedirs(rel_dir, exist_ok=True)
    doc = {
        "id": rel_id, "description": "d", "state": "backlog",
        "atoms": [{"id": "a1", "spec_ref": "specs/a1.md", "contract_ref": "specs/a1.yaml",
                  "depends_on": []}],
    }
    if acceptance is not None:
        doc["acceptance"] = acceptance
    with open(os.path.join(rel_dir, "release.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, sort_keys=False)
    return rel_dir


class TestAcceptance:
    def test_append_shape(self, tmp_path):
        _write_minimal_release(str(tmp_path), "r-acc")
        rel = release.append_acceptance("r-acc", "op1", "accepted", "looks good",
                                        project_dir=str(tmp_path), date="2026-07-27")
        assert rel.acceptance == [{"date": "2026-07-27", "operator": "op1",
                                   "verdict": "accepted", "note": "looks good"}]
        reloaded = release.load_release("r-acc", project_dir=str(tmp_path))
        assert reloaded.acceptance == rel.acceptance   # round-trips through disk

    def test_idempotent_per_operator_date_verdict(self, tmp_path):
        _write_minimal_release(str(tmp_path), "r-acc2")
        release.append_acceptance("r-acc2", "op1", "accepted", "first note",
                                  project_dir=str(tmp_path), date="2026-07-27")
        rel = release.append_acceptance("r-acc2", "op1", "accepted", "a different note text",
                                        project_dir=str(tmp_path), date="2026-07-27")
        assert len(rel.acceptance) == 1
        assert rel.acceptance[0]["note"] == "first note"   # first-write-wins, no duplicate grown

    def test_distinct_verdict_same_operator_same_day_appends_a_second_record(self, tmp_path):
        _write_minimal_release(str(tmp_path), "r-acc3")
        release.append_acceptance("r-acc3", "op1", "accepted", "n1",
                                  project_dir=str(tmp_path), date="2026-07-27")
        rel = release.append_acceptance("r-acc3", "op1", "rejected", "n2",
                                        project_dir=str(tmp_path), date="2026-07-27")
        assert len(rel.acceptance) == 2

    def test_unknown_release_refused(self, tmp_path):
        with pytest.raises(release.ReleaseError):
            release.append_acceptance("does-not-exist-xyz", "op1", "accepted", "n",
                                      project_dir=str(tmp_path))

    def test_non_terminal_verdict_refused(self, tmp_path):
        _write_minimal_release(str(tmp_path), "r-acc4")
        with pytest.raises(release.ReleaseError):
            release.append_acceptance("r-acc4", "op1", "pending", "n", project_dir=str(tmp_path))

    def test_empty_operator_refused(self, tmp_path):
        _write_minimal_release(str(tmp_path), "r-acc4b")
        with pytest.raises(release.ReleaseError):
            release.append_acceptance("r-acc4b", "  ", "accepted", "n", project_dir=str(tmp_path))

    def test_malformed_manifest_acceptance_shape_refused_at_load(self, tmp_path):
        rel_dir = _write_minimal_release(str(tmp_path), "r-acc5")
        with open(os.path.join(rel_dir, "release.yaml"), encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        doc["acceptance"] = [{"date": "2026-07-27", "operator": "op1", "verdict": "accepted"}]  # no note
        with open(os.path.join(rel_dir, "release.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(doc, f)
        with pytest.raises(release.ReleaseError):
            release.load_release("r-acc5", project_dir=str(tmp_path))

    def test_malformed_manifest_non_terminal_verdict_refused_at_load(self, tmp_path):
        rel_dir = _write_minimal_release(str(tmp_path), "r-acc5b")
        with open(os.path.join(rel_dir, "release.yaml"), encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        doc["acceptance"] = [{"date": "2026-07-27", "operator": "op1", "verdict": "maybe", "note": ""}]
        with open(os.path.join(rel_dir, "release.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(doc, f)
        with pytest.raises(release.ReleaseError):
            release.load_release("r-acc5b", project_dir=str(tmp_path))

    def test_cli_accept_verb_end_to_end(self, tmp_path):
        _write_minimal_release(str(tmp_path), "r-acc6")
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(tmp_path))
        release_cli = os.path.join(REPO_ROOT, "scripts", "foundry_release.py")
        p = _run([sys.executable, release_cli, "accept", "r-acc6", "--operator", "op1",
                 "--verdict", "accepted", "--note", "cli path"], env=env)
        assert p.returncode == 0, p.stdout + p.stderr
        rel = release.load_release("r-acc6", project_dir=str(tmp_path))
        assert len(rel.acceptance) == 1 and rel.acceptance[0]["operator"] == "op1"

    def test_cli_accept_unknown_release_nonzero_exit(self, tmp_path):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(tmp_path))
        release_cli = os.path.join(REPO_ROOT, "scripts", "foundry_release.py")
        p = _run([sys.executable, release_cli, "accept", "does-not-exist-xyz", "--operator", "op1",
                 "--verdict", "accepted"], env=env)
        assert p.returncode != 0

    def test_acceptance_absent_round_trips_byte_stable_no_noise(self, tmp_path):
        rel_dir = _write_minimal_release(str(tmp_path), "r-acc7")
        rel = release.load_release("r-acc7", project_dir=str(tmp_path))
        release.save_release(rel, project_dir=str(tmp_path))
        with open(os.path.join(rel_dir, "release.yaml"), encoding="utf-8") as f:
            assert "acceptance" not in f.read()


# ================================================ release-loader-vocabulary: AC-RLV-1..5 ==== #
#
# The autonomy-continuation programme manifests carry a vocabulary the ORIGINAL loader (base
# f7cfa25) rejected outright: extra top-level fields (program/version/target_repo/...) and
# charter-lane atoms (`charter_ref` instead of a frozen spec+contract). `_load_release_at_rev`
# below proves the RED side directly (loads the base commit's `foundry_release.py` as an isolated
# module and shows it refusing the exact same fixture this atom's own tests show GREEN) — a
# real before/after over the same input, not an assertion about history.

FIXTURES_DIR = os.path.join(REPO_ROOT, "tests", "fixtures", "releases")


def _seed_release_fixture(project_dir, release_id):
    """Copy the shipped `tests/fixtures/releases/<release_id>/release.yaml` into
    `<project_dir>/.foundry/releases/<release_id>/release.yaml`, the layout `load_release` expects."""
    import shutil
    src = os.path.join(REPO_ROOT, "tests", "fixtures", "releases", release_id, "release.yaml")
    dst_dir = os.path.join(project_dir, ".foundry", "releases", release_id)
    os.makedirs(dst_dir, exist_ok=True)
    shutil.copyfile(src, os.path.join(dst_dir, "release.yaml"))
    return dst_dir


def _load_release_module_at_rev(rev):
    """Load `scripts/foundry_release.py` AS IT WAS AT `rev` (a git blob, via `git show`) into an
    isolated module object — never touching the working tree/sys.modules cache — so a test can
    prove what the loader used to do, over the SAME fixture input the current loader now accepts."""
    import types
    p = subprocess.run(["git", "-C", REPO_ROOT, "show", f"{rev}:scripts/foundry_release.py"],
                       capture_output=True, text=True)
    assert p.returncode == 0, f"git show {rev}:scripts/foundry_release.py failed: {p.stderr}"
    mod = types.ModuleType(f"foundry_release_at_{rev}")
    mod.__file__ = f"<git {rev}:scripts/foundry_release.py>"
    sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))   # so its `import foundry_authz` resolves
    exec(compile(p.stdout, mod.__file__, "exec"), mod.__dict__)
    return mod


BASE_REV = "f7cfa25"   # the commit release-loader-vocabulary's worktree branched from


class TestReleaseLoaderVocabulary:
    # ---- AC-RLV-1: top-level programme-manifest vocabulary ------------------------------------

    def test_top_level_vocabulary_fields_accepted(self, tmp_path):
        rel_dir = os.path.join(str(tmp_path), ".foundry", "releases", "r-vocab1")
        os.makedirs(rel_dir, exist_ok=True)
        doc = {
            "id": "r-vocab1", "description": "d", "state": "backlog",
            "program": "autonomy-continuation", "version": "1.12.0",
            "target_repo": "agentic-foundry", "target_version": "1.12.0",
            "depends_on_release": ["r-prior"], "value": ["V1 thing"],
            "subtraction": ["some prose"], "lane": "charter by default",
            "gate_before_authorize": True, "supersedes_atoms": ["old-atom"],
            "exit": ["exit condition prose"],
            "atoms": [{"id": "a1", "spec_ref": "specs/a1.md", "contract_ref": "specs/a1.yaml",
                      "depends_on": []}],
        }
        with open(os.path.join(rel_dir, "release.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(doc, f, sort_keys=False)
        rel = release.load_release("r-vocab1", project_dir=str(tmp_path))   # must not raise
        assert rel.id == "r-vocab1"

    def test_unknown_top_level_field_still_refused_by_name(self, tmp_path):
        rel_dir = os.path.join(str(tmp_path), ".foundry", "releases", "r-vocab2")
        os.makedirs(rel_dir, exist_ok=True)
        doc = {
            "id": "r-vocab2", "description": "d", "state": "backlog",
            "totally_unknown_field": "x",
            "atoms": [{"id": "a1", "spec_ref": "specs/a1.md", "contract_ref": "specs/a1.yaml",
                      "depends_on": []}],
        }
        with open(os.path.join(rel_dir, "release.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(doc, f, sort_keys=False)
        with pytest.raises(release.ReleaseError, match="totally_unknown_field"):
            release.load_release("r-vocab2", project_dir=str(tmp_path))

    # ---- AC-RLV-2: charter-lane atoms ----------------------------------------------------------

    def test_charter_lane_atom_accepted_no_contract(self, tmp_path):
        rel_dir = os.path.join(str(tmp_path), ".foundry", "releases", "r-vocab3")
        os.makedirs(rel_dir, exist_ok=True)
        doc = {
            "id": "r-vocab3", "description": "d", "state": "backlog",
            "atoms": [{"id": "charter-atom", "charter_ref": "charters/x.md", "depends_on": [],
                      "kind": "NS", "lane": "charter", "security": False}],
        }
        with open(os.path.join(rel_dir, "release.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(doc, f, sort_keys=False)
        rel = release.load_release("r-vocab3", project_dir=str(tmp_path))
        atom = rel.by_id["charter-atom"]
        assert atom.charter_ref == "charters/x.md"
        assert atom.spec_ref is None and atom.contract_ref is None
        assert atom.kind == "NS" and atom.lane == "charter" and atom.security is False

    def test_atom_neither_ref_shape_refused_naming_atom(self, tmp_path):
        rel_dir = os.path.join(str(tmp_path), ".foundry", "releases", "r-vocab4")
        os.makedirs(rel_dir, exist_ok=True)
        doc = {
            "id": "r-vocab4", "description": "d", "state": "backlog",
            "atoms": [{"id": "bare-atom", "depends_on": []}],
        }
        with open(os.path.join(rel_dir, "release.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(doc, f, sort_keys=False)
        with pytest.raises(release.ReleaseError, match="bare-atom"):
            release.load_release("r-vocab4", project_dir=str(tmp_path))

    def test_atom_only_one_of_spec_or_contract_refused(self, tmp_path):
        rel_dir = os.path.join(str(tmp_path), ".foundry", "releases", "r-vocab4b")
        os.makedirs(rel_dir, exist_ok=True)
        doc = {
            "id": "r-vocab4b", "description": "d", "state": "backlog",
            "atoms": [{"id": "half-atom", "spec_ref": "specs/a1.md", "depends_on": []}],
        }
        with open(os.path.join(rel_dir, "release.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(doc, f, sort_keys=False)
        with pytest.raises(release.ReleaseError, match="half-atom"):
            release.load_release("r-vocab4b", project_dir=str(tmp_path))

    def test_atom_lane_value_closed_set(self, tmp_path):
        rel_dir = os.path.join(str(tmp_path), ".foundry", "releases", "r-vocab5")
        os.makedirs(rel_dir, exist_ok=True)
        doc = {
            "id": "r-vocab5", "description": "d", "state": "backlog",
            "atoms": [{"id": "bad-lane", "charter_ref": "charters/x.md", "depends_on": [],
                      "lane": "not-a-real-lane"}],
        }
        with open(os.path.join(rel_dir, "release.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(doc, f, sort_keys=False)
        with pytest.raises(release.ReleaseError, match="bad-lane"):
            release.load_release("r-vocab5", project_dir=str(tmp_path))

    def test_atom_security_must_be_boolean(self, tmp_path):
        rel_dir = os.path.join(str(tmp_path), ".foundry", "releases", "r-vocab6")
        os.makedirs(rel_dir, exist_ok=True)
        doc = {
            "id": "r-vocab6", "description": "d", "state": "backlog",
            "atoms": [{"id": "bad-sec", "charter_ref": "charters/x.md", "depends_on": [],
                      "security": "true"}],   # a string, not a bool
        }
        with open(os.path.join(rel_dir, "release.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(doc, f, sort_keys=False)
        with pytest.raises(release.ReleaseError, match="bad-sec"):
            release.load_release("r-vocab6", project_dir=str(tmp_path))

    def test_charter_atom_round_trips_no_null_noise(self, tmp_path):
        rel_dir = os.path.join(str(tmp_path), ".foundry", "releases", "r-vocab7")
        os.makedirs(rel_dir, exist_ok=True)
        doc = {
            "id": "r-vocab7", "description": "d", "state": "backlog",
            "atoms": [{"id": "charter-atom", "charter_ref": "charters/x.md", "depends_on": []}],
        }
        with open(os.path.join(rel_dir, "release.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(doc, f, sort_keys=False)
        rel = release.load_release("r-vocab7", project_dir=str(tmp_path))
        release.save_release(rel, project_dir=str(tmp_path))
        with open(os.path.join(rel_dir, "release.yaml"), encoding="utf-8") as f:
            text = f.read()
        assert "spec_ref" not in text and "contract_ref" not in text
        assert "charter_ref: charters/x.md" in text
        release.load_release("r-vocab7", project_dir=str(tmp_path))   # still loads after round-trip

    # ---- AC-RLV-3: charter-lane authorization + merged-on-main / dependency gate --------------

    def test_charter_authorized_iff_file_exists_and_committed(self, tmp_path):
        pd = str(tmp_path)
        _mkgit(pd)
        charter_dir = os.path.join(pd, "charters")
        os.makedirs(charter_dir, exist_ok=True)
        charter_path = os.path.join(charter_dir, "x.md")
        atom = release.Atom("c1", None, None, [], charter_ref="charters/x.md")

        # 1. file absent -> not authorized
        assert release._default_authorized(atom, pd) is False

        # 2. file present but UNCOMMITTED -> not authorized (git log -1 -- <file> is empty)
        with open(charter_path, "w", encoding="utf-8") as f:
            f.write("# charter\n")
        assert release._default_authorized(atom, pd) is False

        # 3. file committed -> authorized
        _run(["git", "-C", pd, "add", "charters/x.md"])
        _run(["git", "-C", pd, "commit", "-q", "-m", "add charter"])
        assert release._default_authorized(atom, pd) is True

    def test_dependency_gate_and_merged_on_main_unchanged_for_charter_atom(self, tmp_path):
        """AC-RLV-3: merged_on_main + the depends_on gate are UNCHANGED — a charter-lane atom has
        no contract_sha256 mechanism, so its `merged_on_main` is a well-defined False (never
        unresolvable), and a factory atom depending on it stays `blocked` naming it, exactly the
        same as a factory atom depending on an unmerged factory atom would."""
        pd = str(tmp_path)
        _mkgit(pd)
        charter_dir = os.path.join(pd, "charters")
        os.makedirs(charter_dir, exist_ok=True)
        with open(os.path.join(charter_dir, "c1.md"), "w", encoding="utf-8") as f:
            f.write("# charter\n")
        _run(["git", "-C", pd, "add", "-A"])
        _run(["git", "-C", pd, "commit", "-q", "-m", "add charter"])
        _run(["git", "-C", pd, "branch", "-M", "main"])   # derive_run_state's default branch

        os.makedirs(os.path.join(pd, ".claude"), exist_ok=True)
        json.dump({"schema_version": 1, "operators": {"op_test": {"name": "T", "github": "t",
                                                                   "added_at": "2026-01-01"}}},
                  open(os.path.join(pd, ".claude", "foundry-operators.json"), "w"))
        spec_rel, contract_rel = _make_spec_contract(pd, "f1", "AC-F1-1")
        ok, out = _authorize(pd, spec_rel, contract_rel)
        assert ok, out   # f1 itself must be authorized so the test reaches the DEPENDENCY gate,
                         # not the (already-covered) "not authorized" bucket
        rel_dir = os.path.join(pd, ".foundry", "releases", "r-charter-dep")
        os.makedirs(rel_dir, exist_ok=True)
        doc = {
            "id": "r-charter-dep", "description": "d", "state": "active",
            "atoms": [
                {"id": "c1", "charter_ref": "charters/c1.md", "depends_on": []},
                {"id": "f1", "spec_ref": spec_rel, "contract_ref": contract_rel,
                 "depends_on": ["c1"]},
            ],
        }
        with open(os.path.join(rel_dir, "release.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(doc, f, sort_keys=False)
        rel = release.load_release("r-charter-dep", project_dir=pd)
        rows = release.derive_run_state(rel, project_dir=pd)
        by_id = {r["id"]: r for r in rows}
        assert by_id["c1"]["authorized"] is True         # committed charter -> authorized
        assert by_id["c1"]["merged_on_main"] is False     # no contract mechanism -> definitively False
        assert by_id["c1"]["probe_error"] is None         # a definite False, not "unresolvable"
        assert by_id["f1"]["state"] == "blocked"
        assert "c1" in (by_id["f1"]["blocked_reason"] or "")

    def test_require_specs_and_contracts_accepts_charter_ref(self, tmp_path):
        """`transition(..., "planned")` — AC-RLV-3's "unchanged" mechanism must not crash on a
        charter atom; it checks the charter file exists instead of spec_ref/contract_ref."""
        pd = str(tmp_path)
        rel_dir = os.path.join(pd, ".foundry", "releases", "r-plan-charter")
        os.makedirs(rel_dir, exist_ok=True)
        charter_dir = os.path.join(pd, "charters")
        os.makedirs(charter_dir, exist_ok=True)
        doc = {
            "id": "r-plan-charter", "description": "d", "state": "backlog",
            "atoms": [{"id": "c1", "charter_ref": "charters/c1.md", "depends_on": []}],
        }
        with open(os.path.join(rel_dir, "release.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(doc, f, sort_keys=False)
        rel = release.load_release("r-plan-charter", project_dir=pd)
        with pytest.raises(release.ReleaseError, match="c1:charter_ref"):
            release.transition(rel, "planned", project_dir=pd)   # charter file absent -> refused
        with open(os.path.join(charter_dir, "c1.md"), "w", encoding="utf-8") as f:
            f.write("# c1\n")
        # `rel.state` was never mutated by the failed call above (the precondition raises BEFORE
        # `release.state = target`) — the same in-memory Release is reusable for the retry.
        release.transition(rel, "planned", project_dir=pd)             # now succeeds

    # ---- AC-RLV-4: `proposed` reads as `planned` ------------------------------------------------

    def test_state_proposed_reads_as_planned(self, tmp_path):
        rel_dir = os.path.join(str(tmp_path), ".foundry", "releases", "r-proposed")
        os.makedirs(rel_dir, exist_ok=True)
        doc = {
            "id": "r-proposed", "description": "d", "state": "proposed",
            "atoms": [{"id": "a1", "spec_ref": "specs/a1.md", "contract_ref": "specs/a1.yaml",
                      "depends_on": []}],
        }
        with open(os.path.join(rel_dir, "release.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(doc, f, sort_keys=False)
        rel = release.load_release("r-proposed", project_dir=str(tmp_path))
        assert rel.state == "planned"
        # the forward-only transition table is unchanged: "planned" -> "active" is still legal
        assert release._LEGAL["planned"] == "active"

    def test_state_unknown_synonym_still_refused(self, tmp_path):
        rel_dir = os.path.join(str(tmp_path), ".foundry", "releases", "r-badstate")
        os.makedirs(rel_dir, exist_ok=True)
        doc = {
            "id": "r-badstate", "description": "d", "state": "not-a-real-state",
            "atoms": [{"id": "a1", "spec_ref": "specs/a1.md", "contract_ref": "specs/a1.yaml",
                      "depends_on": []}],
        }
        with open(os.path.join(rel_dir, "release.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(doc, f, sort_keys=False)
        with pytest.raises(release.ReleaseError, match="not-a-real-state"):
            release.load_release("r-badstate", project_dir=str(tmp_path))

    # ---- AC-RLV-5: the two real programme-manifest fixtures ------------------------------------

    def test_ac_r1_fixture_manifest_loads_clean(self, tmp_path):
        pd = str(tmp_path)
        _seed_release_fixture(pd, "ac-r1-stop-stopping")
        rel = release.load_release("ac-r1-stop-stopping", project_dir=pd)
        assert rel.id == "ac-r1-stop-stopping"
        assert rel.state == "active"
        ids = {a.id for a in rel.atoms}
        assert ids == {"release-loader-vocabulary", "contract-done-when-escalate-when",
                       "standing-grants-as-policy", "blocker-requires-evidence",
                       "operator-handoff-schema"}
        charter_atom = rel.by_id["release-loader-vocabulary"]
        assert charter_atom.charter_ref and charter_atom.spec_ref is None
        factory_atom = rel.by_id["standing-grants-as-policy"]
        assert factory_atom.spec_ref and factory_atom.contract_ref and factory_atom.security is True

    def test_ac_r1_fixture_refused_by_the_pre_change_loader_RED_before_GREEN(self, tmp_path):
        """RED-before-GREEN: the SAME fixture bytes, loaded by `foundry_release.py` as it stood at
        BASE_REV (before this atom), are refused; loaded by the current module (previous test),
        they succeed. This is the atom's own change-attribution evidence, not a claim about the
        current code — it re-derives the RED side every run rather than asserting history."""
        pd = str(tmp_path)
        _seed_release_fixture(pd, "ac-r1-stop-stopping")
        old = _load_release_module_at_rev(BASE_REV)
        with pytest.raises(old.ReleaseError):
            old.load_release("ac-r1-stop-stopping", project_dir=pd)

    def test_ac_r0_fixture_top_level_vocabulary_loads_clean(self, tmp_path):
        """AC-RLV-1 over the R0 fixture: every top-level field the real manifest carries
        (program/version/target_repo/target_version/depends_on_release/value/subtraction/lane/
        exit) is in the closed optional set — demonstrated directly against `_TOP_OPTIONAL_FIELDS`
        rather than via `load_release` (see the next test for why the FULL load does not succeed)."""
        with open(os.path.join(FIXTURES_DIR, "ac-r0-living-spec-process", "release.yaml"),
                 encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        top_keys = set(doc.keys())
        required, optional = release._TOP_REQUIRED_FIELDS, release._TOP_OPTIONAL_FIELDS
        assert top_keys - required - optional == set(), \
            f"unexpected top-level field(s) in the real R0 manifest: {top_keys - required - optional}"

    def test_ac_r0_fixture_atoms_refused_no_ref_shape_FINDING(self, tmp_path):
        """FINDING, not a spec violation of this atom: R0 predates the charter_ref convention — its
        8 atoms (`.foundry/releases/ac-r0-living-spec-process/release.yaml` in the real workspace,
        confirmed 2026-09-19) carry NONE of spec_ref/contract_ref/charter_ref (R0 was "driven by
        hand", per its own state.yaml). AC-RLV-2, as frozen, refuses that atom shape BY NAME — so
        the fixture's full `load_release` correctly RAISES here; AC-RLV-5's literal "SHALL load
        without error" is UNMET for the R0 fixture specifically, and fixing it needs either (a) the
        real R0 manifest amended with a ref for each atom (a workspace edit outside this atom's
        write boundary — the workspace is read-only to this atom), or (b) an operator-authorized
        widening of AC-RLV-2 to also accept a fully ref-less atom. Reported, not resolved, here."""
        pd = str(tmp_path)
        _seed_release_fixture(pd, "ac-r0-living-spec-process")
        with pytest.raises(release.ReleaseError,
                           match="authorize-drops-audit-precondition"):   # first atom in the manifest
            release.load_release("ac-r0-living-spec-process", project_dir=pd)


# ============================================================ foundry_release.py: run-state ==== #

def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _mkgit(repo):
    os.makedirs(repo, exist_ok=True)
    _run(["git", "init", "-q", repo])
    _run(["git", "-C", repo, "config", "user.email", "t@t"])
    _run(["git", "-C", repo, "config", "user.name", "t"])


def _make_spec_contract(project_dir, atom_id, ac_id, *, target_repo=None, superseded=False):
    """FIXTURE FIX (see module docstring): a `src/` dir is seeded under project_dir so the
    `scope.allowed_paths: ["src/**"]` glob has >=1 real match under the allowed-paths-grounding
    gate (ER #179) — the ORIGINAL fixture never created it, which is why this selftest reported
    RED on current main before this fix."""
    os.makedirs(os.path.join(project_dir, "src"), exist_ok=True)
    placeholder = os.path.join(project_dir, "src", "placeholder.txt")
    if not os.path.isfile(placeholder):
        with open(placeholder, "w", encoding="utf-8") as f:
            f.write("seed file so scope.allowed_paths: ['src/**'] has >=1 real match\n")
    specs_dir = os.path.join(project_dir, "specs")
    os.makedirs(specs_dir, exist_ok=True)
    spec_path = os.path.join(specs_dir, f"{atom_id}.md")
    contract_path = os.path.join(specs_dir, f"{atom_id}.yaml")
    body = f"# Test {atom_id}  (feat-{atom_id})\n\n"
    if superseded:
        body += "Status: superseded\n\n"
    body += (f"<!-- normative -->\n## Acceptance criteria\n\n"
             f"- **{ac_id}**: the test surface returns hi.\n<!-- /normative -->\n")
    with open(spec_path, "w", encoding="utf-8") as f:
        f.write(body)
    tr_line = f"target_repo: {target_repo}\n" if target_repo else ""
    with open(contract_path, "w", encoding="utf-8") as f:
        f.write(
            f'spec_ref: specs/{atom_id}.md\nspec_sha256: "' + "0" * 64 + '"\n' + tr_line +
            'scope:\n  allowed_paths: ["src/**"]\n'
            f'checkpoints:\n  - ac_id: {ac_id}\n    surface: "cli:test"\n    locator: "echo hi"\n'
            '    expect: {op: matches, value: "hi", baseline: pre-change}\n'
        )
    return f"specs/{atom_id}.md", f"specs/{atom_id}.yaml"


def _authorize(project_dir, spec_rel, contract_rel, operator="op_test"):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=project_dir)
    spec_path = os.path.join(project_dir, spec_rel)
    contract_path = os.path.join(project_dir, contract_rel)
    _run([sys.executable, os.path.join(REPO_ROOT, "scripts", "foundry-audit-record.py"),
          "--spec", spec_path, "--rounds", "3", "--operator", operator,
          "--verdict", "plateau-clean"], env=env)
    r = _run([sys.executable, os.path.join(REPO_ROOT, "scripts", "foundry-authorize.py"),
              "--spec", spec_path, "--contract", contract_path, "--operator", operator,
              "--mode", "lean", "--yes"], env=env)
    return r.returncode == 0, (r.stdout + r.stderr)


def _land_marker(code_repo, spec_ref, csha):
    marker_path = os.path.join(code_repo, ".foundry", "build-provenance.yaml")
    doc = {"authorizations": []}
    if os.path.isfile(marker_path):
        try:
            doc = yaml.safe_load(open(marker_path, encoding="utf-8")) or {"authorizations": []}
        except (OSError, yaml.YAMLError):
            doc = {"authorizations": []}
    doc.setdefault("authorizations", []).append({"spec_ref": spec_ref, "contract_sha256": csha})
    with open(marker_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f)
    _run(["git", "-C", code_repo, "add", "."])
    _run(["git", "-C", code_repo, "commit", "-q", "-m", f"foundry: build-provenance marker ({spec_ref})"])


@pytest.fixture(scope="module")
def run_state_fixture(tmp_path_factory):
    """The AC-RRS-1..8 seven-state + precedence fixture (ported from the original selftest,
    fixture-fixed per the module docstring). Session-scoped-ish (module) because it shells out to
    real git + real authorize/record subprocess flows several times — expensive to rebuild per
    test."""
    pd = str(tmp_path_factory.mktemp("release_run_state"))
    os.makedirs(os.path.join(pd, ".claude"))
    json.dump({"schema_version": 1, "operators": {"op_test": {"name": "T", "github": "t",
                                                               "added_at": "2026-01-01"}}},
              open(os.path.join(pd, ".claude", "foundry-operators.json"), "w"))

    code_repo = os.path.join(pd, "code-repo")
    _mkgit(code_repo)
    os.makedirs(os.path.join(code_repo, ".foundry"))
    open(os.path.join(code_repo, "seed.txt"), "w").write("seed")
    _run(["git", "-C", code_repo, "add", "."])
    _run(["git", "-C", code_repo, "commit", "-q", "-m", "seed"])
    _run(["git", "-C", code_repo, "branch", "-M", "main"])

    bad_dir = os.path.join(pd, "not-a-git-dir")
    os.makedirs(bad_dir)

    # FIXTURE FIX (module docstring): the allowed-paths-grounding gate (ER #179) resolves the
    # venue root via the atom's OWN target_repo (code_repo for "dis", bad_dir for "unk") — so
    # `scope.allowed_paths: ["src/**"]` needs >=1 real match under EACH resolved venue root, not
    # just under the primary project dir `pd`, or /foundry:authorize fails closed for those atoms
    # too (exactly the all-atoms-False regression this fix addresses).
    for venue in (code_repo, bad_dir):
        os.makedirs(os.path.join(venue, "src"), exist_ok=True)
        with open(os.path.join(venue, "src", "placeholder.txt"), "w", encoding="utf-8") as f:
            f.write("seed file so scope.allowed_paths: ['src/**'] has >=1 real match\n")

    json.dump({"schema_version": 1, "self_host_code_repo": "code-repo",
               "repos": {"code-repo": {"path": "code-repo"}, "bad-key": {"path": "not-a-git-dir"}}},
              open(os.path.join(pd, ".claude", "foundry-project.json"), "w"))

    atoms_spec = [
        ("sup", "AC-SUP-1", None, True),
        ("mrg", "AC-MRG-1", None, False),
        ("dis", "AC-DIS-1", "code-repo", False),
        ("una", "AC-UNA-1", None, False),
        ("run", "AC-RUN-1", None, False),
        ("depnm", "AC-DEP-1", None, False),
        ("blk", "AC-BLK-1", None, False),
        ("unk", "AC-UNK-1", "bad-key", True),
    ]
    refs, authz_results = {}, {}
    for atom_id, ac_id, target_repo, superseded in atoms_spec:
        spec_rel, contract_rel = _make_spec_contract(pd, atom_id, ac_id, target_repo=target_repo,
                                                      superseded=superseded)
        refs[atom_id] = (spec_rel, contract_rel)
        if atom_id == "una":
            continue
        authz_results[atom_id] = _authorize(pd, spec_rel, contract_rel)

    for atom_id in ("sup", "mrg"):
        atom = release.Atom(atom_id, refs[atom_id][0], refs[atom_id][1], [])
        _tr, csha = release._contract_info(atom, pd)
        _land_marker(code_repo, refs[atom_id][0], csha)

    fanout_dir = os.path.join(pd, ".foundry", "fanout")
    os.makedirs(fanout_dir)
    json.dump({"report": [{"key": "code-repo", "task": "dis", "agent": "x", "worktree": "/w",
                          "exit": 0, "terminus": "OK", "output": ""}]},
              open(os.path.join(fanout_dir, "wave1.report.json"), "w"))
    json.dump({"report": [{"key": "code-repo", "task": "sup", "agent": "x", "worktree": "/w",
                          "exit": 0, "terminus": "OK", "output": ""}]},
              open(os.path.join(fanout_dir, "wave-sup.report.json"), "w"))

    rel_dir = os.path.join(pd, ".foundry", "releases", "r0")
    os.makedirs(rel_dir)
    manifest = {
        "id": "r0", "description": "AC-RRS-1..8 seven-state fixture", "state": "active",
        "atoms": [
            {"id": "sup", "spec_ref": refs["sup"][0], "contract_ref": refs["sup"][1], "depends_on": []},
            {"id": "mrg", "spec_ref": refs["mrg"][0], "contract_ref": refs["mrg"][1], "depends_on": []},
            {"id": "dis", "spec_ref": refs["dis"][0], "contract_ref": refs["dis"][1], "depends_on": []},
            {"id": "una", "spec_ref": refs["una"][0], "contract_ref": refs["una"][1], "depends_on": []},
            {"id": "run", "spec_ref": refs["run"][0], "contract_ref": refs["run"][1], "depends_on": []},
            {"id": "depnm", "spec_ref": refs["depnm"][0], "contract_ref": refs["depnm"][1], "depends_on": []},
            {"id": "blk", "spec_ref": refs["blk"][0], "contract_ref": refs["blk"][1], "depends_on": ["depnm"]},
            {"id": "unk", "spec_ref": refs["unk"][0], "contract_ref": refs["unk"][1], "depends_on": []},
        ],
    }
    with open(os.path.join(rel_dir, "release.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(manifest, f, sort_keys=False)

    env = dict(os.environ, CLAUDE_PROJECT_DIR=pd)
    release_cli = os.path.join(REPO_ROOT, "scripts", "foundry_release.py")
    p_plain = _run([sys.executable, release_cli, "run-state", "r0"], env=env)
    p_summary = _run([sys.executable, release_cli, "run-state", "r0", "--summary"], env=env)
    p_strict = _run([sys.executable, release_cli, "run-state", "r0", "--strict"], env=env)

    rows = yaml.safe_load(p_plain.stdout) or []
    by_id = {r.get("id"): r for r in rows if isinstance(r, dict)}
    summ = yaml.safe_load(p_summary.stdout) or {}
    return {"pd": pd, "authz_results": authz_results, "rows": rows, "by_id": by_id,
            "summary": summ, "p_plain": p_plain, "p_summary": p_summary, "p_strict": p_strict,
            "atoms_spec": atoms_spec}


class TestReleaseRunState:
    def test_all_atoms_authorized_after_fixture_fix(self, run_state_fixture):
        """The fixture-fix control: every deliberately-authorized atom's real /foundry:authorize
        subprocess flow now SUCCEEDS (this was the all-False regression on current main before the
        `src/` fixture fix — see module docstring)."""
        for atom_id, (ok, output) in run_state_fixture["authz_results"].items():
            assert ok, f"{atom_id}: {output}"

    def test_cli_plain_exits_zero(self, run_state_fixture):
        assert run_state_fixture["p_plain"].returncode == 0

    def test_row_per_atom_exact_field_set(self, run_state_fixture):
        expected_ids = {a[0] for a in run_state_fixture["atoms_spec"]}
        assert set(run_state_fixture["by_id"]) == expected_ids
        for r in run_state_fixture["rows"]:
            assert set(r) == set(release.FIELDS)

    def test_dispatched_claim_and_absence(self, run_state_fixture):
        by_id = run_state_fixture["by_id"]
        assert isinstance(by_id["dis"]["dispatched"], str) and "dis" in by_id["dis"]["dispatched"]
        assert by_id["mrg"]["dispatched"] == "none"
        assert by_id["mrg"]["pr"] is None
        assert by_id["mrg"]["walk_verdict"] == "none"

    def test_superseded_dominates_precedence(self, run_state_fixture):
        row = run_state_fixture["by_id"]["sup"]
        assert row["state"] == "superseded"
        assert row["authorized"] is True and row["merged_on_main"] is True
        assert row["dispatched"] not in ("none", "unknown")
        assert row["runnable"] is False

    def test_runnable_dependency_gate(self, run_state_fixture):
        by_id = run_state_fixture["by_id"]
        assert by_id["run"]["state"] == "runnable" and by_id["run"]["runnable"] is True
        assert by_id["blk"]["state"] == "blocked" and "depnm" in (by_id["blk"]["blocked_reason"] or "")

    def test_absent_dependency_edge_fails_closed(self, run_state_fixture):
        edge_atom = release.Atom("edge-x", "specs/run.md", "specs/run.yaml", ["ghost-does-not-exist"])
        edge_release = release.Release("edge-rel", "d", "active", [edge_atom], ["edge-x"])
        rows = release.derive_run_state(edge_release, project_dir=run_state_fixture["pd"], branch="main")
        assert rows[0]["state"] == "blocked"
        assert "ghost-does-not-exist" in (rows[0]["blocked_reason"] or "")

    def test_summary_counts_and_ceiling(self, run_state_fixture):
        summ = run_state_fixture["summary"]
        assert "release" in summ and "counts" in summ
        assert set(summ["counts"]) == set(release.STATE_ORDER)
        assert summ["counts"]["superseded"] == 1
        assert summ["counts"]["merged"] == 1
        assert summ["counts"]["dispatched"] == 1
        assert summ["counts"]["unauthorized"] == 1
        assert summ["counts"]["runnable"] == 2
        assert summ["counts"]["blocked"] == 1
        assert summ["counts"]["UNKNOWN"] == 1
        assert len(run_state_fixture["p_summary"].stdout.encode("utf-8")) <= 2048

    def test_declaration_order_tiebreak(self):
        atoms = [release.Atom("z-atom", "s.md", "c.yaml", []),
                 release.Atom("a-atom", "s.md", "c.yaml", []),
                 release.Atom("b-atom", "s.md", "c.yaml", ["z-atom"])]
        rel = release.Release("order-rel", "d", "active", atoms, [a.id for a in atoms])
        rows = [{"id": a.id, "state": "runnable", "authorized": True, "superseded": False,
                "dispatched": "none", "pr": None, "merged_on_main": False, "walk_verdict": "none",
                "runnable": True, "blocked_reason": None, "probe_error": None} for a in atoms]
        summary = yaml.safe_load(release.render_summary(rel, rows))
        assert summary["next_runnable"] == ["z-atom", "a-atom", "b-atom"]

    def test_summary_truncates_under_ceiling(self):
        atoms = [release.Atom(f"atom-{i:04d}", "s.md", "c.yaml", []) for i in range(300)]
        rel = release.Release("big-rel", "d", "active", atoms, [a.id for a in atoms])
        rows = [{"id": a.id, "state": "runnable", "authorized": True, "superseded": False,
                "dispatched": "none", "pr": None, "merged_on_main": False, "walk_verdict": "none",
                "runnable": True, "blocked_reason": None, "probe_error": None} for a in atoms]
        text = release.render_summary(rel, rows)
        doc = yaml.safe_load(text)
        assert len(text.encode("utf-8")) <= 2048
        assert any(isinstance(x, str) and x.startswith("…+") for x in doc["next_runnable"])
        assert doc["counts"]["runnable"] == 300
        assert doc["next_runnable"][:3] == ["atom-0000", "atom-0001", "atom-0002"]

    def test_seven_state_taxonomy_and_unknown_dominance(self, run_state_fixture):
        by_id = run_state_fixture["by_id"]
        assert by_id["mrg"]["state"] == "merged"
        assert by_id["dis"]["state"] == "dispatched"
        assert by_id["una"]["state"] == "unauthorized"
        assert by_id["depnm"]["state"] == "runnable"
        assert by_id["blk"]["state"] == "blocked"
        assert by_id["unk"]["state"] == "UNKNOWN"
        assert by_id["unk"]["probe_error"]
        assert by_id["unk"]["superseded"] is True and by_id["unk"]["authorized"] is True
        assert {r["state"] for r in run_state_fixture["rows"]} == {
            "superseded", "merged", "dispatched", "unauthorized", "runnable", "blocked", "UNKNOWN"}
        assert run_state_fixture["p_strict"].returncode != 0

    def test_selfhost_default_and_explicit_target_repo(self, run_state_fixture):
        by_id = run_state_fixture["by_id"]
        assert by_id["mrg"]["merged_on_main"] is True
        assert by_id["dis"]["probe_error"] is None

    def test_bad_release_id_hard_errors_with_no_emission(self, run_state_fixture):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=run_state_fixture["pd"])
        release_cli = os.path.join(REPO_ROOT, "scripts", "foundry_release.py")
        p = _run([sys.executable, release_cli, "run-state", "does-not-exist-xyz"], env=env)
        assert p.returncode != 0 and p.stdout.strip() == ""

    def test_dispatched_probe_negative_controls(self, tmp_path):
        empty_atom = release.Atom("empty-probe", "s.md", "c.yaml", [])
        assert release._probe_dispatched(empty_atom, str(tmp_path)) == "none"
        corrupt_dir = tmp_path / ".foundry" / "fanout"
        corrupt_dir.mkdir(parents=True)
        (corrupt_dir / "bad.report.json").write_text("not json {{{", encoding="utf-8")
        assert release._probe_dispatched(empty_atom, str(tmp_path)) == "unknown"
