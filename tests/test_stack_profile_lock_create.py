"""tests/test_stack_profile_lock_create.py — feat-foundry-stack-profile-lock-create.

The missing CREATE half of the stack-profile lock lifecycle (`--lock <id>[,<id>…]`). Live-seam:
the REAL CLI's `create_lock()` (and, where noted, the real `--lock` argv path) driven over the
REAL shipped `packs/stack-profiles/` tree for the round-trip proofs, plus throwaway
root/plugin_root/project_dir fixtures — the SAME `_seed_stack_profile`-shaped pattern
`tests/test_certify_fixture.py` already uses — for the negative-path cases (unknown id,
core-incompatible/schema-invalid profile, an already-existing lock, a corrupt lock,
validate-before-write). Nothing here mutates the real repo tree or the real `.foundry/` state;
every test uses `tmp_path` or an explicit `root=`/`plugin_root=`/`project_dir=` override.

THE ROUND-TRIP IS THE LOAD-BEARING CONSTRAINT (AC-SPLC-6, acceptance-contract.yaml). A create that
writes a lock `resolve_lock()` then rejects, or that the doctor then reds, is worse than no create
at all. `test_every_shipped_profile_round_trips` parametrizes over the WHOLE shipped catalogue so a
future profile cannot land into the same gap the omitted `standing_versions_sha256` digest proved
live (AC-SPLC-1's changelog).
"""
from __future__ import annotations

import json
import os

import pytest
import yaml

from conftest import REPO_ROOT, load_module

sp = load_module("scripts/foundry-stack-profile.py", "foundry_stack_profile_lock_create")
doctor = load_module("scripts/foundry-doctor.py", "foundry_doctor_lock_create")

PACKS_DIR = os.path.join(REPO_ROOT, "packs", "stack-profiles")
SHIPPED_PROFILE_IDS = sorted(
    d for d in os.listdir(PACKS_DIR) if os.path.isdir(os.path.join(PACKS_DIR, d))
)


# ══════════════════════════════════ shared fixture plumbing (throwaway packs/ trees) ═══════════ #

def _seed_plugin_root(tmp_path, name="plugin"):
    """A throwaway CLAUDE_PLUGIN_ROOT carrying `schema/` + `.claude-plugin/plugin.json` (core
    0.99.0) — the SAME minimal shape `tests/test_certify_fixture.py`'s `_seed_stack_profile` builds
    — with an initially-empty `packs/stack-profiles/` tree the caller populates via `_write_profile`
    below. Returns the throwaway root path (a `pathlib.Path`)."""
    root = tmp_path / name
    root.mkdir()
    import shutil
    shutil.copytree(os.path.join(REPO_ROOT, "schema"), root / "schema")
    (root / ".claude-plugin").mkdir()
    with open(root / ".claude-plugin" / "plugin.json", "w", encoding="utf-8") as f:
        json.dump({"name": "foundry", "version": "0.99.0"}, f)
    (root / "packs" / "stack-profiles").mkdir(parents=True)
    return root


def _write_profile(root, pid, *, requires_core=">=0.1", extra=None):
    """Write a minimal, otherwise-valid `app`-shaped profile doc at
    <root>/packs/stack-profiles/<pid>/stack-profile.yaml — the SAME shape
    `tests/test_python_stack_profile.py`'s `_full_app_doc` uses. `extra` overrides/adds top-level
    keys (e.g. an unrecognized key, to synthesize a schema-invalid profile)."""
    pack_dir = root / "packs" / "stack-profiles" / pid
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "conventions.md").write_text("conventions", encoding="utf-8")
    doc = {
        "id": pid, "version": "1.0.0", "requires_core": requires_core,
        "matches": {"languages": ["x"], "frameworks": ["x"], "package_managers": ["x"]},
        "architecture": {"layers": ["a"], "allowed_dependencies": [], "conventions_doc": "conventions.md"},
        "implementation_skills": ["conventions.md"],
        "static_validation": {"format": "true", "lint": "true", "typecheck": "true", "build": "true"},
        "test_recipe": {"unit": "true", "integration": "true", "e2e": "true", "coverage_gate": 0},
        "security_checklist": "n/a", "performance_checklist": "n/a",
        "observability": "n/a", "documentation": "n/a",
        "app_exercise_binding": {
            "boot": "true",
            "surfaces": [{"kind": "cli", "exercise": "run true; assert exit 0."}],
        },
    }
    if extra:
        doc.update(extra)
    with open(pack_dir / "stack-profile.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f)
    return pack_dir


def _proj(tmp_path, name="proj"):
    p = tmp_path / name
    p.mkdir(exist_ok=True)
    return p


# ═══════════════════════════════════════════ AC-SPLC-1 — a clean create from the real packs/ ═══ #

class TestCreatesLockFromPacks:
    def test_creates_lock_from_packs(self, tmp_path):
        proj = _proj(tmp_path)
        doc, _path, csha = sp.load_profile("node-web", root=REPO_ROOT, plugin_root=REPO_ROOT)
        created = sp.create_lock(["node-web"], project_dir=str(proj), root=REPO_ROOT, plugin_root=REPO_ROOT)
        assert created == [("node-web", doc["version"])]
        lpath = sp.lock_path(str(proj))
        assert os.path.isfile(lpath)
        with open(lpath, encoding="utf-8") as f:
            lock = json.load(f)
        assert len(lock["profiles"]) == 1
        entry = lock["profiles"][0]
        # The per-entry field set is NOT enumerated here (AC-SPLC-1) — only the fields the SAME
        # code path relock_lock()/resolve_lock() are known to require are pinned; any additional
        # digest (e.g. blueprints_sha256, when the pack has a blueprints/ subtree) is left alone.
        assert entry["id"] == "node-web"
        assert entry["version"] == doc["version"]
        assert entry["sha256"] == csha
        assert not os.path.isfile(lpath + ".tmp")


# ══════════════ AC-SPLC-1 the omitted digest — every shipped profile round-trips, parametrized ═ #

class TestEveryShippedProfileRoundTrips:
    @pytest.mark.parametrize("pid", SHIPPED_PROFILE_IDS)
    def test_every_shipped_profile_round_trips(self, tmp_path, pid):
        proj = _proj(tmp_path, pid)
        sp.create_lock([pid], project_dir=str(proj), root=REPO_ROOT, plugin_root=REPO_ROOT)
        resolved = sp.resolve_lock(str(proj), root=REPO_ROOT, plugin_root=REPO_ROOT)
        assert [d["id"] for d in resolved] == [pid]


# ═══════════════════════════ AC-SPLC-1b — ONE entry-builder, shared with relock, not duplicated ═ #

class TestEntryBuilderIsSharedWithRelock:
    def test_entry_builder_is_shared_with_relock(self, tmp_path, monkeypatch):
        calls = []
        orig = sp._resolve_profile_entry

        def spy(pid, **kw):
            calls.append(pid)
            return orig(pid, **kw)

        monkeypatch.setattr(sp, "_resolve_profile_entry", spy)

        proj = _proj(tmp_path)
        sp.create_lock(["node-web"], project_dir=str(proj), root=REPO_ROOT, plugin_root=REPO_ROOT)
        assert calls == ["node-web"], "create_lock must resolve entries through the shared helper"

        calls.clear()
        sp.relock_lock(str(proj), root=REPO_ROOT, plugin_root=REPO_ROOT)
        assert calls == ["node-web"], "relock_lock must resolve entries through the SAME shared helper"


# ══════════════════════════════ AC-SPLC-2 — the trusted-resolve guardrails, enforced on create ═ #

class TestRefusesUntrustedOrIncompatible:
    def test_refuses_untrusted_or_incompatible_core(self, tmp_path):
        root = _seed_plugin_root(tmp_path)
        _write_profile(root, "bad-core", requires_core=">=99.0")
        proj = _proj(tmp_path)
        with pytest.raises(sp.StackProfileError, match="excludes running core"):
            sp.create_lock(["bad-core"], project_dir=str(proj), root=str(root), plugin_root=str(root))
        assert not os.path.isfile(sp.lock_path(str(proj)))

    def test_refuses_untrusted_or_incompatible_schema_invalid(self, tmp_path):
        root = _seed_plugin_root(tmp_path)
        _write_profile(root, "bad-schema", extra={"unexpected_top_level_key": "x"})
        proj = _proj(tmp_path)
        with pytest.raises(sp.StackProfileError):
            sp.create_lock(["bad-schema"], project_dir=str(proj), root=str(root), plugin_root=str(root))
        assert not os.path.isfile(sp.lock_path(str(proj)))

    def test_refuses_untrusted_or_incompatible_bundle_leak(self, tmp_path):
        root = _seed_plugin_root(tmp_path)
        _write_profile(root, "leaky")
        # D4 guardrail 3: a profile resolvable from the core plugin's skills/ bundle is untrusted.
        leak_dir = root / "skills" / "leaky"
        leak_dir.mkdir(parents=True)
        (leak_dir / "stack-profile.yaml").write_text("id: leaky\n", encoding="utf-8")
        proj = _proj(tmp_path)
        with pytest.raises(sp.StackProfileError, match="leaks into"):
            sp.create_lock(["leaky"], project_dir=str(proj), root=str(root), plugin_root=str(root))
        assert not os.path.isfile(sp.lock_path(str(proj)))


# ══════════════════════════════════ AC-SPLC-3 — VALIDATE-BEFORE-WRITE, no partial residue ══════ #

class TestValidateBeforeWriteLeavesNothing:
    def test_validate_before_write_leaves_nothing(self, tmp_path):
        root = _seed_plugin_root(tmp_path)
        _write_profile(root, "good-one")
        _write_profile(root, "bad-one", requires_core=">=99.0")
        proj = tmp_path / "proj"  # deliberately NOT pre-created — proves no .foundry/ dir is ever made
        with pytest.raises(sp.StackProfileError):
            sp.create_lock(["good-one", "bad-one"], project_dir=str(proj), root=str(root), plugin_root=str(root))
        lpath = sp.lock_path(str(proj))
        assert not os.path.isfile(lpath)
        assert not os.path.isfile(lpath + ".tmp")
        assert not os.path.isdir(proj / ".foundry")
        assert not proj.exists()


# ══════════════════════════════════════ AC-SPLC-4 — an existing (parseable) lock is never re-pointed #

class TestRefusesWhenLockExists:
    def test_refuses_when_lock_exists(self, tmp_path):
        root = _seed_plugin_root(tmp_path)
        _write_profile(root, "existing-id")
        proj = _proj(tmp_path)
        (proj / ".foundry").mkdir()
        lpath = proj / ".foundry" / "stack-profile.lock"
        lock_obj = {"profiles": [{"id": "existing-id", "version": "1.0.0", "sha256": "deadbeef"}]}
        with open(lpath, "w", encoding="utf-8") as f:
            json.dump(lock_obj, f)
        before = lpath.read_text(encoding="utf-8")

        with pytest.raises(sp.StackProfileError, match="/foundry:relock"):
            sp.create_lock(["existing-id"], project_dir=str(proj), root=str(root), plugin_root=str(root))

        assert lpath.read_text(encoding="utf-8") == before, "an existing lock must be byte-unmodified"


# ══════════════ AC-SPLC-4b THE STRANDING — a CORRUPT lock is named as corrupt, never deleted ══ #

class TestCorruptLockIsNamedAndNotDeleted:
    def test_corrupt_lock_is_named_and_not_deleted_malformed_json(self, tmp_path):
        root = _seed_plugin_root(tmp_path)
        _write_profile(root, "some-id")
        proj = _proj(tmp_path)
        (proj / ".foundry").mkdir()
        lpath = proj / ".foundry" / "stack-profile.lock"
        lpath.write_text("{not valid json", encoding="utf-8")
        before = lpath.read_bytes()

        with pytest.raises(sp.StackProfileError, match="(?i)corrupt") as exc:
            sp.create_lock(["some-id"], project_dir=str(proj), root=str(root), plugin_root=str(root))

        msg = str(exc.value)
        assert "remove" in msg.lower() and "re-run" in msg.lower(), "the refusal must state a remedy"
        assert "relock" not in msg.lower(), "corrupt must be distinguished from the plain exists refusal"
        assert lpath.exists() and lpath.read_bytes() == before

    def test_corrupt_lock_is_named_and_not_deleted_structurally_invalid(self, tmp_path):
        root = _seed_plugin_root(tmp_path)
        _write_profile(root, "some-id")
        proj = _proj(tmp_path)
        (proj / ".foundry").mkdir()
        lpath = proj / ".foundry" / "stack-profile.lock"
        # Parseable JSON, but not a mapping with a non-empty `profiles` list (AC-SPLC-4b's second clause).
        lpath.write_text(json.dumps(["not", "a", "mapping"]), encoding="utf-8")
        before = lpath.read_bytes()

        with pytest.raises(sp.StackProfileError, match="(?i)corrupt") as exc:
            sp.create_lock(["some-id"], project_dir=str(proj), root=str(root), plugin_root=str(root))

        msg = str(exc.value)
        assert "remove" in msg.lower() and "re-run" in msg.lower()
        assert lpath.exists() and lpath.read_bytes() == before

    def test_corrupt_lock_is_named_and_not_deleted_empty_profiles(self, tmp_path):
        root = _seed_plugin_root(tmp_path)
        _write_profile(root, "some-id")
        proj = _proj(tmp_path)
        (proj / ".foundry").mkdir()
        lpath = proj / ".foundry" / "stack-profile.lock"
        lpath.write_text(json.dumps({"profiles": []}), encoding="utf-8")
        before = lpath.read_bytes()

        with pytest.raises(sp.StackProfileError, match="(?i)corrupt"):
            sp.create_lock(["some-id"], project_dir=str(proj), root=str(root), plugin_root=str(root))

        assert lpath.exists() and lpath.read_bytes() == before


# ═══════════════════════════════ AC-SPLC-5 — an unknown id lists the ids that ARE available ═══ #

class TestUnknownIdListsAvailable:
    def test_unknown_id_lists_available(self, tmp_path):
        root = _seed_plugin_root(tmp_path)
        _write_profile(root, "known-a")
        _write_profile(root, "known-b")
        proj = _proj(tmp_path)

        with pytest.raises(sp.StackProfileError) as exc:
            sp.create_lock(["nope-typo"], project_dir=str(proj), root=str(root), plugin_root=str(root))

        msg = str(exc.value)
        assert "known-a" in msg and "known-b" in msg
        assert "nope-typo" in msg
        assert not os.path.isfile(sp.lock_path(str(proj)))

    def test_unknown_id_mixed_with_known_still_refuses_and_lists(self, tmp_path):
        root = _seed_plugin_root(tmp_path)
        _write_profile(root, "known-a")
        proj = _proj(tmp_path)

        with pytest.raises(sp.StackProfileError) as exc:
            sp.create_lock(["known-a", "nope-typo"], project_dir=str(proj), root=str(root), plugin_root=str(root))

        assert "known-a" in str(exc.value)
        assert not os.path.isfile(sp.lock_path(str(proj)))


# ═══════════════════ AC-SPLC-6 THE ROUND-TRIP — resolve_lock AND the doctor both report ok ═══ #

class TestCreatedLockRoundTrips:
    def test_created_lock_round_trips(self, tmp_path):
        proj = _proj(tmp_path)
        sp.create_lock(SHIPPED_PROFILE_IDS, project_dir=str(proj), root=REPO_ROOT, plugin_root=REPO_ROOT)

        resolved = sp.resolve_lock(str(proj), root=REPO_ROOT, plugin_root=REPO_ROOT)
        assert sorted(d["id"] for d in resolved) == sorted(SHIPPED_PROFILE_IDS)

        ok, detail = doctor.check_stack_profile_lock(plugin_root=REPO_ROOT, project_dir=str(proj))
        assert ok is True, detail
        assert str(len(SHIPPED_PROFILE_IDS)) in detail


# ═══════════════════════ AC-SPLC-7 — init drives the SCRIPTED create, never prose ═══════════ #

class TestInitInvokesScriptedCreate:
    def test_init_invokes_scripted_create(self):
        path = os.path.join(REPO_ROOT, "skills", "init", "SKILL.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        open_marker = "<!-- foundry:stack-profile-lock-create-prescribed"
        close_marker = "<!-- /foundry:stack-profile-lock-create-prescribed -->"
        assert open_marker in text, "init must carry the prescribed-create anchor block"
        assert close_marker in text
        open_idx = text.index(open_marker)
        close_idx = text.index(close_marker)
        assert open_idx < close_idx

        # The prescribed command block itself must invoke the SCRIPTED path (never hand-written prose).
        block = text[open_idx:close_idx]
        assert "foundry-stack-profile.py" in block
        assert "--lock" in block

        # The step's surrounding prose (a stable heading anchor, not a numeric step marker that could
        # shift) must separately state the no-profile-selected completion path (AC-SPLC-7's second
        # clause: selecting no profile completes init normally, with no lock).
        step_heading = "Stack-profile lock (opt-in adoption"
        assert step_heading in text
        step_start = text.index(step_heading)
        step_text = text[step_start:close_idx]
        assert "no lock" in step_text.lower() or "no profile" in step_text.lower()

    def test_init_names_doctor_green_as_valid_lockless_state(self):
        path = os.path.join(REPO_ROOT, "skills", "init", "SKILL.md")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        assert "DOCTOR-GREEN" in text  # already true pre-atom; guards against an accidental deletion


# ══════════════════════════ AC-SPLC-8 THE NEGATIVE CONTROL — lockless stays green ═══════════ #

class TestLocklessWorkspaceStaysGreen:
    def test_lockless_workspace_stays_green(self, tmp_path):
        proj = _proj(tmp_path)  # no .foundry/stack-profile.lock ever written here
        ok, detail = doctor.check_stack_profile_lock(plugin_root=REPO_ROOT, project_dir=str(proj))
        assert ok is True
        assert "not applicable" in detail
