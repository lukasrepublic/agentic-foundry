"""tests/test_release.py — the verify executor + the pre-cut acceptance gate.

Ports the behavioral assertions of two drop-in selftests over the shipped surfaces:
`scripts/foundry-verify.py` (the profile-parameterized static-validation + test executor) and
`scripts/foundry-release-acceptance.py` (the pre-cut acceptance gate over a candidate plugin tree),
against throwaway temp fixtures and the real shipped tree. (The run-state ledger half of this module
went with `scripts/foundry_release.py` in v2.0.0.)
"""
from __future__ import annotations

import json
import os
import subprocess

import pytest
import yaml

from conftest import REPO_ROOT, load_module

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
