"""tests/test_stack_profile.py — converted from scripts/foundry_checks/{app-stack-blueprint,
node-web-profile, aws-eks-karpenter-profile, stack-profile-infra-binding, profile-version-
immutability, foundry-profile-compat, gitops-optional}.py.

Ports the real behavioral assertions those seven drop-in selftests drove against the shipped
`scripts/foundry_blueprint.py` (pure discover/render/guard/digest) and `scripts/foundry-stack-
profile.py` (the profile loader/validator/lock resolver) — over the REAL shipped
`packs/stack-profiles/{node-web,aws-eks-karpenter}` packs and throwaway temp fixtures. CLI/doctor
scaffolding is dropped; the computed fixtures/assertions are kept.
"""
from __future__ import annotations

import json
import os
import shutil

import pytest
import yaml

from conftest import REPO_ROOT, load_module

blueprint = load_module("scripts/foundry_blueprint.py", "foundry_blueprint")
sp = load_module("scripts/foundry-stack-profile.py", "foundry_stack_profile")

PACKS_DIR = os.path.join(REPO_ROOT, "packs", "stack-profiles")


# ============================================================== foundry_blueprint.py (core) ==== #

FRONTMATTER_OK = """---
id: demo-blueprint
covers: something
parametrizes_from:
  - api_port
---
Body text referencing {{ profile.api_port }}.
"""


class TestBlueprintFrontmatter:
    def test_split_frontmatter_ok(self):
        fm, body = blueprint.split_frontmatter(FRONTMATTER_OK)
        assert fm["id"] == "demo-blueprint"
        assert "{{ profile.api_port }}" in body

    def test_split_frontmatter_missing_opening_fence_raises(self):
        with pytest.raises(blueprint.BlueprintError):
            blueprint.split_frontmatter("no fence here\n")

    def test_split_frontmatter_unterminated_raises(self):
        with pytest.raises(blueprint.BlueprintError):
            blueprint.split_frontmatter("---\nid: x\n")

    def test_split_frontmatter_non_mapping_raises(self):
        with pytest.raises(blueprint.BlueprintError):
            blueprint.split_frontmatter("---\n- a\n- b\n---\nbody\n")


class TestRenderBlueprint:
    def test_render_substitutes_scalar_slot(self):
        out = blueprint.render_blueprint("port is {{ profile.api_port }}", {"api_port": 8080}, ["api_port"])
        assert out == "port is 8080"

    def test_render_missing_placeholder_slot_raises(self):
        with pytest.raises(blueprint.BlueprintError):
            blueprint.render_blueprint("{{ profile.missing }}", {}, [])

    def test_render_non_scalar_slot_raises(self):
        with pytest.raises(blueprint.BlueprintError):
            blueprint.render_blueprint("{{ profile.thing }}", {"thing": ["a", "b"]}, [])

    def test_render_undeclared_parametrizes_from_ref_raises(self):
        with pytest.raises(blueprint.BlueprintError):
            blueprint.render_blueprint("no placeholder here", {"api_port": 8080}, ["api_port"])

    def test_render_parametrizes_from_ref_absent_from_profile_raises(self):
        with pytest.raises(blueprint.BlueprintError):
            blueprint.render_blueprint("{{ profile.api_port }}", {}, ["api_port"])

    def test_render_bool_is_scalar(self):
        out = blueprint.render_blueprint("{{ profile.flag }}", {"flag": True}, ["flag"])
        assert out == "True"


class TestGuardBlueprint:
    def test_clean_body_has_no_findings(self):
        findings = blueprint.guard_blueprint("uses {{ profile.api_port }}", {"api_port": 8080}, ["api_port"])
        assert findings == []

    def test_hardcoded_bound_slot_literal_flags(self):
        findings = blueprint.guard_blueprint(
            "bind to service-alpha directly", {"svc_name": "service-alpha"}, ["svc_name"])
        assert any("appears verbatim" in f for f in findings)

    def test_short_and_numeric_values_exempted(self):
        # a pure-numeric or <4-char value is exempted (non-distinctive).
        findings = blueprint.guard_blueprint("x is 80 here", {"x": 80}, ["x"])
        assert findings == []

    def test_domain_id_denylist_flags(self):
        findings = blueprint.guard_blueprint("see " + "HBK" + "-1234 for context", {}, [])
        assert any("deny-list" in f for f in findings)


class TestDiscoverBlueprints:
    def test_no_blueprints_dir_returns_empty(self, tmp_path):
        assert blueprint.discover_blueprints(str(tmp_path), plugin_root=REPO_ROOT) == []

    def test_symlinked_blueprints_dir_rejected(self, tmp_path):
        real = tmp_path / "real_bp"
        real.mkdir()
        pack = tmp_path / "pack"
        pack.mkdir()
        os.symlink(real, pack / "blueprints")
        with pytest.raises(blueprint.BlueprintError):
            blueprint.discover_blueprints(str(pack), plugin_root=REPO_ROOT)

    def test_discovers_real_node_web_blueprints_if_any(self):
        # node-web may or may not carry a blueprints/ subtree; either way this must not raise.
        got = blueprint.discover_blueprints(os.path.join(PACKS_DIR, "node-web"), plugin_root=REPO_ROOT)
        assert isinstance(got, list)


class TestBlueprintsSha256:
    def test_none_when_no_subtree(self, tmp_path):
        assert blueprint.blueprints_sha256(str(tmp_path)) is None

    def test_deterministic_and_content_sensitive(self, tmp_path):
        bp = tmp_path / "blueprints" / "x"
        bp.mkdir(parents=True)
        (bp / "SKILL.md").write_text("hello", encoding="utf-8")
        h1 = blueprint.blueprints_sha256(str(tmp_path))
        h2 = blueprint.blueprints_sha256(str(tmp_path))
        assert h1 == h2 and h1 is not None
        (bp / "SKILL.md").write_text("hello world", encoding="utf-8")
        h3 = blueprint.blueprints_sha256(str(tmp_path))
        assert h3 != h1

    def test_symlinked_file_rejected(self, tmp_path):
        bp = tmp_path / "blueprints"
        bp.mkdir()
        real = tmp_path / "real.md"
        real.write_text("x", encoding="utf-8")
        os.symlink(real, bp / "SKILL.md")
        with pytest.raises(blueprint.BlueprintError):
            blueprint.blueprints_sha256(str(tmp_path))


# ==================================================== foundry-stack-profile.py: real packs ==== #

class TestLoadRealProfiles:
    def test_node_web_loads_and_validates(self):
        doc, path, sha = sp.load_profile("node-web", root=REPO_ROOT, plugin_root=REPO_ROOT)
        assert doc["id"] == "node-web"
        assert sp.profile_kind(doc) == "app"
        assert sp.infra_binding_of(doc) is None
        assert len(sha) == 64

    def test_aws_eks_karpenter_loads_and_validates_as_infra(self):
        doc, path, sha = sp.load_profile("aws-eks-karpenter", root=REPO_ROOT, plugin_root=REPO_ROOT)
        assert sp.profile_kind(doc) == "infra"
        ib = sp.infra_binding_of(doc)
        assert ib is not None
        assert "work_dirs" in ib and "gitops_enabled" in ib  # normalized defaults always present.
        tiers = [r.get("tier") for r in ib.get("blast_radius", [])]
        assert "HIGH" in tiers

    def test_referenced_files_exist_on_disk(self):
        for pid in ("node-web", "aws-eks-karpenter"):
            doc, path, _ = sp.load_profile(pid, root=REPO_ROOT, plugin_root=REPO_ROOT)
            pack_dir = os.path.dirname(path)
            conv = doc["architecture"]["conventions_doc"]
            assert os.path.isfile(os.path.join(pack_dir, conv))
            for skill in doc.get("implementation_skills", []):
                assert os.path.isfile(os.path.join(pack_dir, skill))

    def test_profile_path_rejects_traversal(self):
        with pytest.raises(sp.StackProfileError):
            sp.profile_path("../etc/passwd", root=REPO_ROOT)
        with pytest.raises(sp.StackProfileError):
            sp.profile_path("node-web/../../etc", root=REPO_ROOT)


# ================================================ validate_infra_binding / gitops toggle ==== #

def _valid_infra_binding(**over):
    ib = {
        "plan": "tofu plan -out=plan.out",
        "apply": "tofu apply -auto-approve",
        "verify": "tofu plan -detailed-exitcode",
        "policy": "conftest test plan.json",
        "blast_radius": [{"rule": "node-replace", "tier": "HIGH"}],
        "gitops_paths": [],
    }
    ib.update(over)
    return ib


def _infra_doc(**over):
    ib = _valid_infra_binding(**{k: v for k, v in over.items() if k in (
        "plan", "apply", "verify", "policy", "blast_radius", "gitops_paths",
        "gitops_enabled", "work_dirs")})
    return {"profile_kind": "infra", "infra_binding": ib}


class TestValidateInfraBinding:
    def test_valid_binding_passes(self):
        assert sp.validate_infra_binding(_infra_doc()) is True

    def test_app_kind_is_a_noop(self):
        assert sp.validate_infra_binding({"profile_kind": "app"}) is True
        assert sp.validate_infra_binding({}) is True  # absent kind defaults to app.

    def test_missing_high_tier_rejected(self):
        doc = _infra_doc(blast_radius=[{"rule": "x", "tier": "LOW"}])
        with pytest.raises(sp.StackProfileError):
            sp.validate_infra_binding(doc)

    def test_mutating_verb_in_readonly_slot_rejected(self):
        doc = _infra_doc(plan="tofu apply -auto-approve")
        with pytest.raises(sp.StackProfileError):
            sp.validate_infra_binding(doc)

    def test_gitops_enabled_false_with_nonempty_paths_contradicts(self):
        doc = _infra_doc(gitops_enabled=False, gitops_paths=["clusters/prod/app.yaml"])
        with pytest.raises(sp.StackProfileError):
            sp.validate_infra_binding(doc)

    def test_gitops_enabled_true_with_empty_paths_contradicts(self):
        doc = _infra_doc(gitops_enabled=True, gitops_paths=[])
        with pytest.raises(sp.StackProfileError):
            sp.validate_infra_binding(doc)

    def test_gitops_enabled_true_with_paths_ok(self):
        doc = _infra_doc(gitops_enabled=True, gitops_paths=["clusters/prod/app.yaml"])
        assert sp.validate_infra_binding(doc) is True

    def test_gitops_absent_is_legacy_semantics_either_way(self):
        assert sp.validate_infra_binding(_infra_doc(gitops_paths=[])) is True
        assert sp.validate_infra_binding(
            _infra_doc(gitops_paths=["clusters/prod/app.yaml"])) is True

    def test_infra_binding_of_normalizes_gitops_enabled_and_work_dirs_defaults(self):
        doc = _infra_doc()
        del doc["infra_binding"]["gitops_paths"]
        doc["infra_binding"]["gitops_paths"] = []
        ib = sp.infra_binding_of(doc)
        assert ib["gitops_enabled"] is None
        assert ib["work_dirs"] is None


class TestValidateWorkDirs:
    def test_none_ok(self):
        assert sp.validate_work_dirs(None) is True

    def test_absolute_path_rejected(self):
        with pytest.raises(sp.StackProfileError):
            sp.validate_work_dirs({"infra": "/etc"})

    def test_traversal_rejected(self):
        with pytest.raises(sp.StackProfileError):
            sp.validate_work_dirs({"infra": "../../etc"})

    def test_escape_via_repo_root_rejected(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        with pytest.raises(sp.StackProfileError):
            sp.validate_work_dirs({"infra": "../outside"}, repo_root=str(repo))

    def test_valid_relative_dir_ok(self):
        assert sp.validate_work_dirs({"infra": "infra/", "gitops": "gitops/"}) is True


# ============================================================= foundry-profile-compat.py ==== #

def _scan_compat(plugin_root):
    """Mirrors the retired check()'s scan-and-collect-offenders loop over the SHIPPED
    core_satisfies/core_version primitives (the real logic under test)."""
    import glob
    cv = sp.core_version(plugin_root)
    packs_dir = os.path.join(plugin_root, "packs", "stack-profiles")
    profile_dirs = sorted(d for d in glob.glob(os.path.join(packs_dir, "*")) if os.path.isdir(d))
    if not profile_dirs:
        return False, "empty packs tree"
    offenders = []
    for pdir in profile_dirs:
        pid = os.path.basename(pdir)
        ypath = os.path.join(pdir, "stack-profile.yaml")
        with open(ypath, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        rc = doc["requires_core"]
        try:
            if not sp.core_satisfies(rc, cv):
                offenders.append(pid)
        except Exception:
            offenders.append(pid)
    return (not offenders), offenders


def _write_temp_plugin_root(tmp, *, core, profiles):
    os.makedirs(os.path.join(tmp, ".claude-plugin"), exist_ok=True)
    with open(os.path.join(tmp, ".claude-plugin", "plugin.json"), "w", encoding="utf-8") as f:
        json.dump({"name": "foundry", "version": core}, f)
    for pid, rc in profiles:
        pdir = os.path.join(tmp, "packs", "stack-profiles", pid)
        os.makedirs(pdir, exist_ok=True)
        with open(os.path.join(pdir, "stack-profile.yaml"), "w", encoding="utf-8") as f:
            f.write(f'id: {pid}\nrequires_core: "{rc}"\n')


class TestCoreSatisfies:
    CORE = "0.3.8"

    def test_lower_bound_only_admits_far_future_core(self):
        assert sp.core_satisfies(">=0.1", self.CORE) is True

    def test_two_bound_range(self):
        assert sp.core_satisfies(">=0.1,<0.4", self.CORE) is True
        assert sp.core_satisfies(">=0.1,<0.2", self.CORE) is False

    def test_reversed_bounds_unsatisfiable(self):
        assert sp.core_satisfies(">=0.3,<0.2", self.CORE) is False

    @pytest.mark.parametrize("bad", [">=0.1.2.3", ">=0.", ">=.1", ">=0..1", "0.1", ">=0.1,<", "<0.2"])
    def test_malformed_range_raises_stackprofileerror(self, bad):
        with pytest.raises(sp.StackProfileError):
            sp.core_satisfies(bad, self.CORE)


class TestProfileCompatShipTimeGuard:
    def test_all_admitting_profiles_pass(self, tmp_path):
        _write_temp_plugin_root(str(tmp_path), core="0.3.8", profiles=[("alpha", ">=0.1"), ("beta", ">=0.2")])
        ok, offenders = _scan_compat(str(tmp_path))
        assert ok is True and offenders == []

    def test_capped_profile_excluded_and_named(self, tmp_path):
        _write_temp_plugin_root(str(tmp_path), core="0.3.8",
                                profiles=[("alpha", ">=0.1"), ("capped", ">=0.2,<0.3")])
        ok, offenders = _scan_compat(str(tmp_path))
        assert ok is False and "capped" in offenders

    def test_unparseable_requires_core_named(self, tmp_path):
        _write_temp_plugin_root(str(tmp_path), core="0.3.8",
                                profiles=[("alpha", ">=0.1"), ("broken", ">=0.1.2.3")])
        ok, offenders = _scan_compat(str(tmp_path))
        assert ok is False and "broken" in offenders

    def test_live_shipped_packs_all_admit_current_core(self):
        """The acceptance evidence: the REAL shipped profiles admit the REAL current core — this is
        exactly the regression class ("recurring on essentially every core release") the check exists
        to catch, over the actual repo tree (not a synthetic fixture)."""
        ok, offenders = _scan_compat(REPO_ROOT)
        assert ok is True, f"shipped profile(s) excluded from current core: {offenders}"


# ========================================================= profile-version-immutability.py ==== #

def _copy_packs(dst_root):
    dst = os.path.join(dst_root, "packs", "stack-profiles")
    shutil.copytree(PACKS_DIR, dst)
    return dst


def _computed_digests(pack_dir, yaml_path):
    with open(yaml_path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    pid = doc.get("id")
    ver = doc.get("version")
    csha = sp.content_sha256(yaml_path)
    bsha = sp._blueprints_sha256_of(pack_dir)
    svsha = sp._standing_versions_sha256_of(pack_dir, doc)
    return pid, ver, csha, bsha, svsha


def _immutability_check(root):
    """Mirrors the retired check()'s ledger-vs-computed-digest comparison — the real invariant under
    test lives in the shipped content_sha256/_blueprints_sha256_of/_standing_versions_sha256_of
    primitives; this loop is the (small) comparison scaffolding around them."""
    packs_dir = os.path.join(root, "packs", "stack-profiles")
    ledger_path = os.path.join(packs_dir, "profile-version-ledger.json")
    if not os.path.isfile(ledger_path):
        return False, "ledger missing"
    with open(ledger_path, encoding="utf-8") as f:
        led = (json.load(f) or {}).get("profiles", {})
    for name in sorted(os.listdir(packs_dir)):
        pdir = os.path.join(packs_dir, name)
        yml = os.path.join(pdir, "stack-profile.yaml")
        if not os.path.isfile(yml):
            continue
        pid, ver, csha, bsha, svsha = _computed_digests(pdir, yml)
        if pid not in led:
            return False, f"{pid} not registered in ledger"
        if ver not in led[pid]:
            return False, f"{pid} version {ver} not registered in ledger"
        rec = led[pid][ver] or {}
        if rec.get("sha256") != csha or rec.get("blueprints_sha256") != bsha \
                or rec.get("standing_versions_sha256") != svsha:
            return False, f"{pid}@{ver}: same-version content drift"
    return True, "all profiles version-immutable"


class TestProfileVersionImmutability:
    def test_live_shipped_tree_is_immutable_vs_ledger(self):
        ok, detail = _immutability_check(REPO_ROOT)
        assert ok is True, detail

    def test_same_version_blueprint_edit_without_bump_is_drift(self, tmp_path):
        packs = _copy_packs(str(tmp_path))
        bp_target = None
        for name in os.listdir(packs):
            bpdir = os.path.join(packs, name, "blueprints")
            if os.path.isdir(bpdir):
                for r, _dirs, files in os.walk(bpdir):
                    if files:
                        bp_target = os.path.join(r, sorted(files)[0])
                        break
            if bp_target:
                break
        assert bp_target, "fixture assumption: some pack carries a blueprints/ subtree"
        with open(bp_target, "a", encoding="utf-8") as f:
            f.write("\n# selftest perturbation\n")
        ok, detail = _immutability_check(str(tmp_path))
        assert ok is False and "drift" in detail

    def test_unregistered_version_is_red(self, tmp_path):
        packs = _copy_packs(str(tmp_path))
        yml = os.path.join(packs, "node-web", "stack-profile.yaml")
        body = open(yml, encoding="utf-8").read()
        import re
        nb = re.sub(r"(?m)^version:\s*\S+\s*$", "version: 99.99.99", body, count=1)
        assert nb != body
        open(yml, "w", encoding="utf-8").write(nb)
        ok, detail = _immutability_check(str(tmp_path))
        assert ok is False

    def test_unregistered_id_is_red(self, tmp_path):
        packs = _copy_packs(str(tmp_path))
        lp = os.path.join(packs, "profile-version-ledger.json")
        ledger = json.load(open(lp, encoding="utf-8"))
        dropped = sorted(ledger["profiles"])[0]
        del ledger["profiles"][dropped]
        json.dump(ledger, open(lp, "w", encoding="utf-8"))
        ok, detail = _immutability_check(str(tmp_path))
        assert ok is False

    def test_standing_versions_artifact_drift_detected(self, tmp_path):
        packs = _copy_packs(str(tmp_path))
        target = os.path.join(packs, "aws-eks-karpenter", "standing-versions", "manifest.yaml")
        assert os.path.isfile(target)
        with open(target, "a", encoding="utf-8") as f:
            f.write("\n# perturbation\n")
        ok, detail = _immutability_check(str(tmp_path))
        assert ok is False and "drift" in detail

    def test_node_web_has_no_standing_versions_block_svsha_none(self):
        pack_dir = os.path.join(PACKS_DIR, "node-web")
        with open(os.path.join(pack_dir, "stack-profile.yaml"), encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        assert "standing-versions" not in doc
        assert sp._standing_versions_sha256_of(pack_dir, doc) is None
