"""tests/test_boot_recipe_precedence.py — feat-foundry-boot-recipe-precedence
(specs/features/foundry/certification/boot-recipe-precedence/feat-foundry-boot-recipe-precedence.md).

Drives the REAL resolver in `skills/certify-local/certify_local.py`
(`_project_repos_key`, `resolve_project_boot_command`, `resolve_effective_boot_recipe`) over
materialized `tmp_path` fixture trees — a `.claude/foundry-project.json` + (where the scenario
needs the fallback) a real `.foundry/stack-profile.lock` resolving one of the two shipped Python
stack-profile packs, following the SAME throwaway-plugin-root pattern
`tests/test_python_stack_profile.py`'s `_seed_plugin_root`/`_write_lock_for` already use.

No profile/boot command string is ever executed here — this file proves RESOLUTION (which source
wins, in what order, with what provenance, and what a refusal says), never dispatch.
"""
from __future__ import annotations

import json
import os

import pytest

from conftest import REPO_ROOT, load_module

sp = load_module("scripts/foundry-stack-profile.py", "foundry_stack_profile_boot_recipe_precedence")
cl = load_module("skills/certify-local/certify_local.py", "certify_local_boot_recipe_precedence")

PACKS_DIR = os.path.join(REPO_ROOT, "packs", "stack-profiles")

# python-uv-service's real, pinned boot recipe (tests/test_python_stack_profile.py's BOOT_CMD) —
# used here as "the profile's recipe" the project declaration must NOT be shadowed by, and must
# fall back to when it declares nothing.
PROFILE_BOOT_CMD = "uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000"


# ─────────────────────────────────────────────────────────────── shared fixture plumbing ──── #

def _write_manifest(project_dir, doc):
    claude_dir = project_dir / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    with open(claude_dir / "foundry-project.json", "w", encoding="utf-8") as f:
        json.dump(doc, f)


def _write_malformed_manifest(project_dir):
    claude_dir = project_dir / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    with open(claude_dir / "foundry-project.json", "w", encoding="utf-8") as f:
        f.write("{not valid json")


def _seed_plugin_root(tmp_path, pack_id):
    """A throwaway CLAUDE_PLUGIN_ROOT-shaped tree carrying ONE copy of a real shipped stack-profile
    pack + the real schema + a `.claude-plugin/plugin.json` — byte-identical to
    `tests/test_python_stack_profile.py`'s helper of the same name."""
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
    """A `.foundry/stack-profile.lock` resolving the ONE copied pack — byte-identical to
    `tests/test_python_stack_profile.py`'s helper of the same name."""
    doc, path, csha = sp.load_profile(pack_id, root=str(root), plugin_root=str(root))
    svsha = sp._standing_versions_sha256_of(os.path.dirname(path), doc)
    entry = {"id": pack_id, "version": doc["version"], "sha256": csha}
    if svsha is not None:
        entry["standing_versions_sha256"] = svsha
    foundry_dir = project_dir / ".foundry"
    foundry_dir.mkdir(parents=True, exist_ok=True)
    with open(foundry_dir / "stack-profile.lock", "w", encoding="utf-8") as f:
        json.dump({"profiles": [entry]}, f)


def _lock_only_marker(project_dir):
    """A `.foundry/stack-profile.lock` FILE present on disk (deliberately not a resolvable one) —
    just enough to make `_lock_already_exists` true, without needing a real pack."""
    foundry_dir = project_dir / ".foundry"
    foundry_dir.mkdir(parents=True, exist_ok=True)
    with open(foundry_dir / "stack-profile.lock", "w", encoding="utf-8") as f:
        json.dump({"profiles": []}, f)


# ═══════════════════════════ AC-BRP-1 : a project-declared boot_command WINS ═══════════════ #

class TestProjectDeclaredRecipeWins:
    def test_project_declared_recipe_wins_over_an_active_profile(self, tmp_path, monkeypatch):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        _write_manifest(project_dir, {"repos": {"myapp": {"path": "myapp",
                                                           "boot_command": "python3 -m http.server 9001"}}})

        # the profile loader must NEVER even be consulted when the project wins — assert that
        # structurally, not just that its output is discarded.
        def _boom():
            raise AssertionError("the stack profile must not be consulted when the project "
                                 "declares a usable boot_command (AC-BRP-1)")
        monkeypatch.setattr(cl, "_load_stack_profile_module", lambda: _boom())

        cmd, prov, profile, repos_key = cl.resolve_effective_boot_recipe(
            str(project_dir), "unused-plugin-root", "myapp")
        assert cmd == "python3 -m http.server 9001"
        assert prov == "project"
        assert profile is None
        assert repos_key == "myapp"

    def test_project_declared_recipe_wins_even_with_a_resolvable_lock_present(self, tmp_path):
        """The load-bearing case: an active, RESOLVABLE `.foundry/stack-profile.lock` exists (so
        the profile path would otherwise succeed) — the project's own declaration still wins and
        the profile's DIFFERENT boot command never surfaces."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        root = _seed_plugin_root(tmp_path, "python-uv-service")
        _write_lock_for(root, "python-uv-service", project_dir)
        _write_manifest(project_dir, {"repos": {"workspace": {"boot_command": "echo project-wins"}}})

        cmd, prov, profile, repos_key = cl.resolve_effective_boot_recipe(
            str(project_dir), str(root), "workspace", root=str(root))
        assert cmd == "echo project-wins"
        assert cmd != PROFILE_BOOT_CMD
        assert prov == "project"


# ═══════════════════════ AC-BRP-2 (regression floor) : falls back to the profile ═══════════ #

class TestFallsBackToStackProfile:
    @pytest.mark.parametrize("manifest_doc", [
        None,                                                              # no manifest at all
        {"repos": {}},                                                     # key absent
        {"repos": {"workspace": {"path": "x"}}},                          # no boot_command field
        {"repos": {"workspace": {"path": "x", "boot_command": ""}}},      # empty string
        {"repos": {"workspace": {"path": "x", "boot_command": "   "}}},   # whitespace-only
    ])
    def test_falls_back_to_stack_profile_when_project_declares_nothing_usable(self, tmp_path, manifest_doc):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        root = _seed_plugin_root(tmp_path, "python-uv-service")
        _write_lock_for(root, "python-uv-service", project_dir)
        if manifest_doc is not None:
            _write_manifest(project_dir, manifest_doc)

        cmd, prov, profile, repos_key = cl.resolve_effective_boot_recipe(
            str(project_dir), str(root), "workspace", root=str(root))
        assert cmd == PROFILE_BOOT_CMD
        assert prov == "profile"
        assert profile is not None and profile.get("id") == "python-uv-service"
        assert repos_key == "workspace"

    def test_falls_back_to_stack_profile_byte_equal_to_resolve_boot_recipe_directly(self, tmp_path):
        """Pins the regression floor itself: the fallback path's boot_command is BYTE-EQUAL to
        what the UNTOUCHED `resolve_boot_recipe` returns directly — the precedence wrapper adds no
        transformation of its own on the fallback leg."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        root = _seed_plugin_root(tmp_path, "python-uv-service")
        _write_lock_for(root, "python-uv-service", project_dir)

        _profile, binding = cl.resolve_boot_recipe(str(project_dir), str(root), root=str(root))
        cmd, prov, _profile2, _repos_key = cl.resolve_effective_boot_recipe(
            str(project_dir), str(root), None, root=str(root))
        assert cmd == binding["boot"]
        assert prov == "profile"


# ═══════════════ AC-BRP-3 : neither source — refuse, naming remedies correctly ═════════════ #

class TestRefusesNamingBothRemedies:
    def test_refuses_naming_only_the_primary_remedy_when_no_lock_exists(self, tmp_path):
        """No project declaration, no lock at all — 'activate a profile' is NOT reachable
        (nothing creates a lock yet), so it must NOT be named."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        with pytest.raises(cl.CertifyError) as excinfo:
            cl.resolve_effective_boot_recipe(str(project_dir), "unused-plugin-root", "workspace")
        msg = str(excinfo.value)
        assert msg.startswith(cl.REFUSED_PREFIX)
        assert "no boot recipe" in msg
        assert "boot_command" in msg
        assert "repos.workspace.boot_command" in msg
        assert "activate a different stack profile" not in msg

    def test_refuses_naming_both_remedies_when_a_lock_already_exists(self, tmp_path):
        """No project declaration, and a lock exists but does not resolve to a bootable profile
        (a `python-uv-lib` library pack — no `app_exercise_binding`) — 'activate a different
        stack profile' IS reachable (relock has something to relock), so it MUST be named
        alongside the always-actionable primary remedy."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        root = _seed_plugin_root(tmp_path, "python-uv-lib")
        _write_lock_for(root, "python-uv-lib", project_dir)

        with pytest.raises(cl.CertifyError) as excinfo:
            cl.resolve_effective_boot_recipe(str(project_dir), str(root), "workspace", root=str(root))
        msg = str(excinfo.value)
        assert msg.startswith(cl.REFUSED_PREFIX)
        assert "no boot recipe" in msg
        assert "repos.workspace.boot_command" in msg
        assert "activate a different stack profile" in msg

    def test_never_a_vacuous_pass_when_neither_source_yields_a_recipe(self, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        _lock_only_marker(project_dir)   # a lock FILE exists but resolves to zero profiles
        with pytest.raises(cl.CertifyError, match="no boot recipe"):
            cl.resolve_effective_boot_recipe(str(project_dir), "unused-plugin-root", "workspace")


# ═════════════════ AC-BRP-4 : a malformed manifest degrades, never raises ══════════════════ #

class TestMalformedManifestDegrades:
    def test_malformed_manifest_degrades_to_the_stack_profile_never_raises(self, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        root = _seed_plugin_root(tmp_path, "python-uv-service")
        _write_lock_for(root, "python-uv-service", project_dir)
        _write_malformed_manifest(project_dir)

        cmd, prov, profile, _repos_key = cl.resolve_effective_boot_recipe(
            str(project_dir), str(root), "workspace", root=str(root))
        assert cmd == PROFILE_BOOT_CMD
        assert prov == "profile (manifest malformed)"
        assert profile is not None

    def test_malformed_manifest_reported_distinctly_from_absent_manifest(self, tmp_path):
        """AC-BRP-4's own distinctness clause: `resolve_project_boot_command`'s provenance for a
        malformed manifest must NEVER equal the provenance for a manifest that simply declares
        nothing — collapsing them is exactly the silent-degrade AC-BRP-4 forbids."""
        project_dir_absent = tmp_path / "absent"
        project_dir_absent.mkdir()
        _cmd_a, prov_absent = cl.resolve_project_boot_command(str(project_dir_absent), "workspace")

        project_dir_malformed = tmp_path / "malformed"
        project_dir_malformed.mkdir()
        _write_malformed_manifest(project_dir_malformed)
        _cmd_b, prov_malformed = cl.resolve_project_boot_command(str(project_dir_malformed), "workspace")

        assert prov_absent == "manifest-empty"
        assert prov_malformed == "manifest-malformed"
        assert prov_absent != prov_malformed

    def test_malformed_manifest_no_lock_still_refuses_cleanly(self, tmp_path):
        """A defective manifest AND no lock is still a clean, named refusal — never an unhandled
        exception escaping the resolver."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        _write_malformed_manifest(project_dir)
        with pytest.raises(cl.CertifyError, match="no boot recipe"):
            cl.resolve_effective_boot_recipe(str(project_dir), "unused-plugin-root", "workspace")


# ══════════════════════ AC-BRP-5 : provenance names FOUR distinct outcomes ═════════════════ #

class TestReportsRecipeProvenance:
    def test_reports_recipe_provenance_for_all_three_successful_outcomes_distinctly(self, tmp_path):
        root = _seed_plugin_root(tmp_path, "python-uv-service")

        # (a) project supplied it.
        proj_a = tmp_path / "proj-a"
        proj_a.mkdir()
        _write_manifest(proj_a, {"repos": {"workspace": {"boot_command": "echo a"}}})
        _cmd_a, prov_a, _p, _k = cl.resolve_effective_boot_recipe(str(proj_a), str(root), "workspace", root=str(root))

        # (b) manifest declared nothing, the profile supplied it.
        proj_b = tmp_path / "proj-b"
        proj_b.mkdir()
        _write_lock_for(root, "python-uv-service", proj_b)
        _cmd_b, prov_b, _p, _k = cl.resolve_effective_boot_recipe(str(proj_b), str(root), "workspace", root=str(root))

        # (c) manifest unreadable/malformed, the profile supplied it.
        proj_c = tmp_path / "proj-c"
        proj_c.mkdir()
        _write_lock_for(root, "python-uv-service", proj_c)
        _write_malformed_manifest(proj_c)
        _cmd_c, prov_c, _p, _k = cl.resolve_effective_boot_recipe(str(proj_c), str(root), "workspace", root=str(root))

        assert prov_a == "project"
        assert prov_b == "profile"
        assert prov_c == "profile (manifest malformed)"
        # pairwise distinct — (b) and (c) never collapse to the same line (AC-BRP-5's core clause).
        assert len({prov_a, prov_b, prov_c}) == 3

    def test_certify_result_surfaces_the_winning_provenance(self, tmp_path, monkeypatch):
        """The provenance is reported in `certify-local`'s own composed OUTPUT, not just an
        internal resolver return — drives the REAL `certify()` through release-manifest
        resolution, target-repo resolution, and boot-recipe resolution, with only the Playwright
        dispatch faked out (a real, cheap `sleep` boot process is still launched and torn down —
        this proves the WIRING, not just the resolver in isolation)."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "playwright.config.js").write_text("module.exports = {};", encoding="utf-8")
        _write_manifest(repo, {"repos": {"workspace": {"boot_command": "sleep 5"}}})

        rel_dir = repo / ".foundry" / "releases" / "brp-fixture"
        rel_dir.mkdir(parents=True)
        (rel_dir / "release.yaml").write_text(
            "id: brp-fixture\ndescription: t\nstate: active\natoms:\n"
            "  - id: a1\n    spec_ref: specs/a1.md\n    contract_ref: specs/a1.yaml\n"
            "    depends_on: []\n    journeys: [\"BRP-FIX-1\"]\n", encoding="utf-8")
        specs_dir = repo / "specs"
        specs_dir.mkdir()
        (specs_dir / "a1.md").write_text("# t\n", encoding="utf-8")
        (specs_dir / "a1.yaml").write_text("spec_ref: specs/a1.md\n", encoding="utf-8")

        import subprocess as _subprocess

        class _FakeCompleted:
            returncode = 0
            stdout = ""
            stderr = ""

        monkeypatch.setattr(_subprocess, "run", lambda *a, **k: _FakeCompleted())

        result = cl.certify("brp-fixture", project_dir=str(repo), plugin_root=str(tmp_path),
                            boot_wait=0.05)
        assert result["boot_recipe"]["provenance"] == "project"
        assert result["boot_recipe"]["command"] == "sleep 5"
        assert result["boot_recipe"]["repos_key"] == "workspace"
        assert result["profile_id"] is None   # AC-BRP-1: the profile was never consulted


# ═══════════════ AC-BRP-6 : boot_command is executed VERBATIM, never templated ═════════════ #

class TestExecutedVerbatimNoTemplating:
    SPECIAL = "echo {not-a-format-field} $NOT_EXPANDED_HERE %s %(also-not)s `nor-this`"

    def test_executed_verbatim_no_templating_through_project_declaration(self, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        _write_manifest(project_dir, {"repos": {"workspace": {"boot_command": self.SPECIAL}}})
        cmd, prov, _p, _k = cl.resolve_effective_boot_recipe(str(project_dir), "unused-plugin-root", "workspace")
        assert cmd == self.SPECIAL   # byte-identical — no interpolation, no shell pre-expansion
        assert prov == "project"

    def test_executed_verbatim_no_templating_at_the_manifest_read_layer(self, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        _write_manifest(project_dir, {"repos": {"x": {"boot_command": self.SPECIAL}}})
        cmd, prov = cl.resolve_project_boot_command(str(project_dir), "x")
        assert cmd == self.SPECIAL
        assert prov == "project"


# ═══════════ AC-BRP-7 : every `_resolve_repo` venue-resolution path names its own key ═══════ #

class TestWorkspaceSelfEntryResolves:
    def test_workspace_self_entry_resolves_the_literal_workspace_key(self, tmp_path):
        """Path 1: `target_repo == 'workspace'` — the merge-gate sentinel/single-repo adopter's
        self entry — resolves `repos.workspace.boot_command` with no other lookup."""
        assert cl._project_repos_key("workspace", str(tmp_path)) == "workspace"

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        _write_manifest(project_dir, {"repos": {"workspace": {"boot_command": "echo self-entry"}}})
        cmd, prov, _p, repos_key = cl.resolve_effective_boot_recipe(
            str(project_dir), "unused-plugin-root", "workspace")
        assert cmd == "echo self-entry"
        assert prov == "project"
        assert repos_key == "workspace"

    def test_workspace_self_entry_resolves_falsy_target_repo_falls_back_to_workspace_key(self, tmp_path):
        """Path 2b: `target_repo` falsy (the self-host default) with NO `self_host_code_repo`
        declared falls back to the literal `workspace` key — never a crash on a missing field."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        assert cl._project_repos_key(None, str(project_dir)) == "workspace"
        assert cl._project_repos_key("", str(project_dir)) == "workspace"

        _write_manifest(project_dir, {"repos": {"workspace": {"boot_command": "echo self-host-default"}}})
        cmd, prov, _p, repos_key = cl.resolve_effective_boot_recipe(
            str(project_dir), "unused-plugin-root", None)
        assert cmd == "echo self-host-default"
        assert repos_key == "workspace"

    def test_workspace_self_entry_resolves_falsy_target_repo_honors_self_host_code_repo(self, tmp_path):
        """Path 2a: `target_repo` falsy with `self_host_code_repo` SET — the recipe is looked up
        under THAT key, not the literal `workspace`."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        _write_manifest(project_dir, {
            "self_host_code_repo": "code",
            "repos": {"code": {"boot_command": "echo self-host-code-repo"},
                     "workspace": {"boot_command": "echo WRONG-KEY"}},
        })
        assert cl._project_repos_key(None, str(project_dir)) == "code"
        cmd, _prov, _p, repos_key = cl.resolve_effective_boot_recipe(
            str(project_dir), "unused-plugin-root", None)
        assert cmd == "echo self-host-code-repo"
        assert repos_key == "code"

    def test_workspace_self_entry_resolves_an_explicit_key(self, tmp_path):
        """Path 3: `target_repo` names an explicit key — looked up under that key (AC-BRP-1)."""
        assert cl._project_repos_key("other-repo", str(tmp_path)) == "other-repo"


# ═══════════════════ AC-BRP-8 : docs state the precedence, honestly ════════════════════════ #

class TestDocsStateThePrecedence:
    DOCS_PATH = os.path.join(REPO_ROOT, "docs", "troubleshooting.md")

    def _read(self):
        with open(self.DOCS_PATH, encoding="utf-8") as f:
            return f.read()

    def test_docs_state_the_precedence_order(self):
        text = self._read()
        idx_boot_command = text.find("boot_command")
        idx_profile_fallback = text.find("active stack profile", idx_boot_command if idx_boot_command >= 0 else 0)
        assert idx_boot_command != -1, "docs/troubleshooting.md no longer mentions boot_command"
        assert idx_profile_fallback != -1, "docs/troubleshooting.md no longer mentions the profile fallback"
        assert idx_boot_command < idx_profile_fallback, (
            "the project-declared boot_command must be documented as resolved BEFORE the "
            "stack-profile fallback (AC-BRP-8's precedence-order clause)")

    def test_docs_state_the_precedence_not_declarative_only(self):
        text = self._read()
        assert "not read by certification" not in text, (
            "docs/troubleshooting.md must not describe boot_command as declarative-only "
            "(AC-BRP-8)")

    def test_docs_state_the_precedence_known_limitation_narrowed_not_deleted(self):
        text = self._read()
        assert "Known limitation" in text, "the v1 known-limitation note must be NARROWED, not deleted"
        assert "stack-profile.lock" in text
        # the note must still be honest that the PROFILE path (not the boot_command path) remains
        # unreached until the sibling lock-create atom ships.
        assert "feat-foundry-stack-profile-lock-create" in text
