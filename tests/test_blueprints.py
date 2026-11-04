"""tests/test_blueprints.py -- gv-08: blueprint kits = stack profiles extended with `governance`,
`surfaces`, `detect`, `readiness_checklist`, `test_strategy`; pure `detect_score` / `rank`.

Read-only over the REAL shipped packs/ tree plus path/dependency fact fixtures
(tests/fixtures/blueprints/<profile>.facts.json); nothing is executed, no network.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess

import pytest
import yaml

from conftest import REPO_ROOT, load_module

sp = load_module("scripts/foundry-stack-profile.py", "foundry_stack_profile_gv08")
bp = load_module("scripts/foundry_blueprint.py", "foundry_blueprint_gv08")

PACKS = os.path.join(REPO_ROOT, "packs", "stack-profiles")
FIX = os.path.join(REPO_ROOT, "tests", "fixtures", "blueprints")
IDS = ("python-uv-service", "python-uv-lib", "node-web", "aws-eks-karpenter",
       "service-event-driven", "cli-tool", "claude-plugin", "data-pipeline")
KIT_IDS = {"product", "architecture", "stack", "security", "contracts", "quality", "delivery",
           "operations", "cost", "observability", "data", "compliance", "ux", "exceptions", "conduct"}


def _load(pid):
    doc, path, _sha = sp.load_profile(pid, root=REPO_ROOT, plugin_root=REPO_ROOT)
    return doc


def _facts(pid):
    with open(os.path.join(FIX, f"{pid}.facts.json"), encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def profiles():
    return [_load(p) for p in IDS]


def test_shipped_set_is_exactly_the_eight():
    shipped = sorted(d for d in os.listdir(PACKS) if os.path.isfile(os.path.join(PACKS, d, "stack-profile.yaml")))
    assert shipped == sorted(IDS)


@pytest.mark.parametrize("pid", IDS)
def test_profile_validates_with_new_blocks(pid):
    doc = _load(pid)
    assert set(doc["governance"]["kits"]) <= KIT_IDS and doc["governance"]["kits"]
    assert all(v["level"] in (1, 2, 3) for v in doc["governance"]["kits"].values())
    assert doc["surfaces"] and doc["detect"]["signals"] and 0 < doc["detect"]["confidence_threshold"] <= 1
    for key in ("readiness_checklist", "test_strategy"):
        assert os.path.isfile(os.path.join(PACKS, pid, doc[key])), (pid, key)
        assert doc[key].startswith("blueprints/")


@pytest.mark.parametrize("block", ["governance", "surfaces", "detect"])
@pytest.mark.parametrize("pid", IDS)
def test_missing_block_fails_naming_it(pid, block):
    doc = copy.deepcopy(_load(pid))
    del doc[block]
    with pytest.raises(sp.StackProfileError) as e:
        sp.validate_profile(doc, plugin_root=REPO_ROOT)
    assert repr(block) in str(e.value) or block in str(e.value)


def test_closed_kit_set_and_level_range_enforced():
    doc = copy.deepcopy(_load("cli-tool"))
    doc["governance"]["kits"]["astrology"] = {"level": 1}
    with pytest.raises(sp.StackProfileError):
        sp.validate_profile(doc, plugin_root=REPO_ROOT)
    doc = copy.deepcopy(_load("cli-tool"))
    doc["governance"]["kits"]["security"]["level"] = 4
    with pytest.raises(sp.StackProfileError):
        sp.validate_profile(doc, plugin_root=REPO_ROOT)


def test_bad_surface_signal_kind_and_escaping_path_rejected():
    for mut in (lambda d: d["surfaces"].append("fax"),
                lambda d: d["detect"]["signals"][0].update(kind="vibes"),
                lambda d: d.update(readiness_checklist="blueprints/../../x.md"),
                lambda d: d.update(test_strategy="../test-strategy.md")):
        doc = copy.deepcopy(_load("cli-tool"))
        mut(doc)
        with pytest.raises(sp.StackProfileError):
            sp.validate_profile(doc, plugin_root=REPO_ROOT)


def test_matches_remains_a_valid_deprecated_alias():
    doc = copy.deepcopy(_load("cli-tool"))
    doc["matches"] = {"languages": ["python"], "frameworks": [], "package_managers": ["uv"]}
    assert sp.validate_profile(doc, plugin_root=REPO_ROOT) is True
    # the four pre-existing profiles still carry it
    assert "matches" in _load("node-web")


def test_pack_doc_paths_must_exist(tmp_path):
    import shutil
    root = tmp_path / "r"
    shutil.copytree(os.path.join(REPO_ROOT, "packs"), root / "packs")
    os.remove(root / "packs" / "stack-profiles" / "cli-tool" / "blueprints" / "test-strategy.md")
    with pytest.raises(sp.StackProfileError, match="test_strategy"):
        sp.load_profile("cli-tool", root=str(root), plugin_root=REPO_ROOT)


# ── detect: pure scoring ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pid", IDS)
def test_rank_puts_matching_profile_first_above_threshold(profiles, pid):
    rows = bp.rank(profiles, _facts(pid))
    assert rows[0]["id"] == pid, [(r["id"], round(r["score"], 2)) for r in rows]
    assert rows[0]["above_threshold"] and rows[0]["score"] >= rows[0]["threshold"]
    assert rows[0]["evidence"]
    # exactly one profile claims the tree (the siblings are disambiguated by negative weights/thresholds)
    assert [r["id"] for r in rows if r["above_threshold"]] == [pid]


def test_rank_empty_tree_nothing_above_threshold(profiles):
    rows = bp.rank(profiles, {"paths": [], "dependencies": []})
    assert len(rows) == 8 and not any(r["above_threshold"] for r in rows)
    assert all(r["score"] == 0 and r["evidence"] == [] for r in rows)


def test_rank_is_deterministic_and_total_order(profiles):
    a = bp.rank(profiles, _facts("cli-tool"))
    b = bp.rank(list(reversed(profiles)), _facts("cli-tool"))
    assert [r["id"] for r in a] == [r["id"] for r in b]


def test_detect_score_is_pure_and_normalised(profiles):
    facts = _facts("node-web")
    snap = json.dumps(facts, sort_keys=True)
    score, ev = bp.detect_score(_load("node-web"), facts)
    assert 0 <= score <= 1 and all({"kind", "pattern", "weight", "matched_by"} <= set(e) for e in ev)
    assert json.dumps(facts, sort_keys=True) == snap
    assert bp.detect_score({"id": "x"}, facts) == (0.0, [])  # no detect block (legacy `matches` only)


def test_glob_grammar():
    g = bp.glob_match
    assert g("uv.lock", "a/b/uv.lock") and g("uv.lock", "uv.lock") and not g("uv.lock", "uv.lock.bak")
    assert g("skills/*/SKILL.md", "skills/x/SKILL.md") and not g("skills/*/SKILL.md", "skills/x/y/SKILL.md")
    assert g("**/consumers/**", "src/app/consumers/o.py") and g("a/**/b", "a/b") and g("a/**/b", "a/x/y/b")
    assert g("package-lock.json|pnpm-lock.yaml", "web/pnpm-lock.yaml")
    assert g("asyncapi.y*ml", "asyncapi.yml") and not g("src/*/__init__.py", "src/a/b/__init__.py")


def test_negative_weights_disambiguate_service_from_event_driven(profiles):
    facts = {"paths": ["pyproject.toml", "uv.lock"], "dependencies": ["fastapi", "aiokafka"]}
    top = bp.rank(profiles, facts)[0]
    assert top["id"] == "service-event-driven"


def test_dependency_names_are_normalised():
    prof = {"id": "p", "detect": {"confidence_threshold": 0.5,
            "signals": [{"kind": "dependency", "pattern": "confluent-kafka", "weight": 1}]}}
    assert bp.detect_score(prof, {"paths": [], "dependencies": ["Confluent_Kafka"]})[0] == 1.0


def test_claude_plugin_ranks_first_over_this_repository():
    """Dogfood control: the file list is computed from THIS worktree."""
    out = subprocess.run(["git", "-C", REPO_ROOT, "ls-files"], capture_output=True, text=True, check=True).stdout
    paths = [p for p in out.splitlines() if p]
    assert ".claude-plugin/plugin.json" in paths and "hooks/hooks.json" in paths
    rows = bp.rank([_load(p) for p in IDS], {"paths": paths, "dependencies": []})
    assert rows[0]["id"] == "claude-plugin" and rows[0]["above_threshold"], \
        [(r["id"], round(r["score"], 2)) for r in rows]
    assert [r["id"] for r in rows if r["above_threshold"]] == ["claude-plugin"]


# ── digest + ledger ──────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pid", IDS)
def test_blueprint_digest_reproducible_and_covers_new_docs(pid, tmp_path):
    import shutil
    a = sp.blueprint_digest(pid, root=REPO_ROOT, plugin_root=REPO_ROOT)
    assert a == sp.blueprint_digest(pid, root=REPO_ROOT, plugin_root=REPO_ROOT)
    assert a[0] == pid and len(a[2]) == 64
    root = tmp_path / "r"
    shutil.copytree(os.path.join(REPO_ROOT, "packs"), root / "packs")
    doc = _load(pid)
    for key in ("readiness_checklist", "test_strategy"):
        target = root / "packs" / "stack-profiles" / pid / doc[key]
        before = sp.blueprint_digest(pid, root=str(root), plugin_root=REPO_ROOT)
        assert before == a
        with open(target, "a", encoding="utf-8") as f:
            f.write("\nperturbation\n")
        assert sp.blueprint_digest(pid, root=str(root), plugin_root=REPO_ROOT)[2] != a[2]
        with open(target, "rb+") as f:           # restore
            data = f.read()
            f.seek(0); f.truncate(); f.write(data.replace(b"\nperturbation\n", b""))


@pytest.mark.parametrize("pid", IDS)
def test_ledger_registers_every_profile_consistently(pid):
    with open(os.path.join(PACKS, "profile-version-ledger.json"), encoding="utf-8") as f:
        led = json.load(f)["profiles"]
    doc, path, csha = sp.load_profile(pid, root=REPO_ROOT, plugin_root=REPO_ROOT)
    d = os.path.dirname(path)
    rec = led[pid][doc["version"]]
    assert rec["sha256"] == csha
    assert rec["blueprints_sha256"] == sp._blueprints_sha256_of(d) and rec["blueprints_sha256"]
    assert rec.get("standing_versions_sha256") == sp._standing_versions_sha256_of(d, doc)


def test_extended_profiles_bumped_past_their_prior_ledger_entry():
    with open(os.path.join(PACKS, "profile-version-ledger.json"), encoding="utf-8") as f:
        led = json.load(f)["profiles"]
    prior = {"python-uv-service": "0.1.0", "python-uv-lib": "0.1.0", "node-web": "0.3.1", "aws-eks-karpenter": "0.5.3"}
    for pid, old in prior.items():
        assert old in led[pid], "history is append-only"
        assert sp._pad(_load(pid)["version"]) > sp._pad(old)


@pytest.mark.parametrize("pid", ("service-event-driven", "cli-tool", "claude-plugin", "data-pipeline"))
def test_new_profiles_carry_timestamped_exact_pins(pid):
    d = os.path.join(PACKS, pid, "standing-versions")
    man = yaml.safe_load(open(os.path.join(d, "manifest.yaml"), encoding="utf-8"))
    comp = yaml.safe_load(open(os.path.join(d, "compatibility.yaml"), encoding="utf-8"))
    assert man["pins_valid_as_of"] == "2026-11-04" and comp["pins_valid_as_of"] == "2026-11-04"
    for pin in man["pins"]:
        if pin["kind"] == "pypi_package":
            assert pin["version"].count(".") == 2 and pin["version"][0].isdigit()   # exact, no range
    assert "pypi.org" in open(os.path.join(d, "manifest.yaml"), encoding="utf-8").read()


def test_relock_behaviour_unchanged_for_new_digest_inputs(tmp_path):
    """create_lock then relock over an extended profile keeps the same entry shape (no new lock keys)."""
    proj = str(tmp_path)
    sp.create_lock(["cli-tool"], proj, root=REPO_ROOT, plugin_root=REPO_ROOT)
    lock = json.load(open(os.path.join(proj, ".foundry", "stack-profile.lock"), encoding="utf-8"))
    assert set(lock["profiles"][0]) == {"id", "version", "sha256", "blueprints_sha256", "standing_versions_sha256"}
    assert sp.relock_lock(proj, root=REPO_ROOT, plugin_root=REPO_ROOT) == [("cli-tool", "0.1.0", "0.1.0")]
    assert len(sp.resolve_lock(proj, root=REPO_ROOT, plugin_root=REPO_ROOT)) == 1
