"""Governance core (gv-01): schemas, the resolver verbs, exception handling, no-network, paper-guard admission.

Every acceptance criterion has a failing-case test and a control (passing) test.
"""
import copy
import importlib.util
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import jsonschema
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
import foundry_governance as gov  # noqa: E402

SCHEMAS = {n: json.loads((ROOT / "schema" / f"governance-{n}.schema.json").read_text()) for n in ("rule", "kit", "lock")}


def rule(rid="FG-TST-001", **over):
    r = {"id": rid, "title": "Test rule", "statement": "An agent MUST test.", "category": "testing", "level": 1,
         "floor": False, "status": "active", "applies_to": {"paths": ["**"], "blueprints": ["*"]},
         "check": {"kind": "ci", "ref": "ci/test.yml"}, "exception": {"allowed": True, "requires": ["reason", "owner", "expires"]}}
    r.update(over)
    return r


def make_plugin(tmp_path, rules, kit="testing", version="1.0.0"):
    d = tmp_path / "plugin" / "governance" / "kits" / kit
    d.mkdir(parents=True)
    (d / "kit.yaml").write_text(yaml.safe_dump({"kit": kit, "version": version, "category": kit,
                                                "rules": [r["id"] for r in rules]}))
    (d / "rules.yaml").write_text(yaml.safe_dump({"kit": kit, "version": version, "rules": rules}))
    return tmp_path / "plugin"


def make_project(tmp_path, local_rules=None, overrides=None, exceptions=None):
    p = tmp_path / "proj"
    (p / "governance" / "local").mkdir(parents=True)
    if local_rules is not None or overrides is not None:
        (p / "governance" / "local" / "rules.yaml").write_text(
            yaml.safe_dump({"rules": local_rules or [], "overrides": overrides or []}))
    if exceptions is not None:
        (p / "governance" / "local" / "exceptions.yaml").write_text(yaml.safe_dump({"exceptions": exceptions}))
    return p


def run(plugin, proj, *argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = gov.main(["--plugin-root", str(plugin), "--project-dir", str(proj), *argv])
    return rc, out.getvalue(), err.getvalue()


# ---- schemas + shipped kits ------------------------------------------------------------------ #

def test_shipped_kits_validate_and_match_schemas():
    rc, out, _ = run(ROOT, ROOT / "nonexistent-project-dir", "validate")
    assert rc == 0 and "governance-valid" in out
    for kit in ("conduct", "exceptions"):
        d = ROOT / "governance" / "kits" / kit
        for f in ("kit.yaml", "rules.yaml", "README.md"):
            assert (d / f).is_file()
        assert (d / "checks").is_dir()
        jsonschema.validate(yaml.safe_load((d / "kit.yaml").read_text()), SCHEMAS["kit"])
        for r in yaml.safe_load((d / "rules.yaml").read_text())["rules"]:
            jsonschema.validate(r, SCHEMAS["rule"])
    ids = {r["id"] for k in gov.load_kits(ROOT) for r in k["rules"]}
    assert {f"FG-CND-00{i}" for i in range(1, 5)} | {f"FG-EXC-00{i}" for i in range(1, 5)} <= ids
    assert {r["id"]: r["floor"] for k in gov.load_kits(ROOT) for r in k["rules"]}["FG-EXC-001"] is True


def test_lock_schema_accepts_a_lock_and_rejects_a_bad_one():
    sha = "a" * 64
    lock = {"blueprint": {"id": "cli-tool", "version": "1.0.0", "sha256": sha},
            "kits": [{"id": "conduct", "version": "1.0.0", "sha256": sha, "level": 1}],
            "declarations": {"asvs_level": 1, "data_classification": [], "trust_boundaries": [], "surfaces": ["cli"]},
            "facts_sha256": sha, "intake_at": "2026-11-04T00:00:00Z", "plugin_version": "2.0.0"}
    jsonschema.validate(lock, SCHEMAS["lock"])
    bad = copy.deepcopy(lock)
    bad["kits"][0]["level"] = 9
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, SCHEMAS["lock"])


@pytest.mark.parametrize("over", [{"id": "fg-tst-1"}, {"level": 4}, {"status": "gone"}, {"check": {"kind": "magic"}}])
def test_python_validator_and_schema_agree_on_bad_rules(over):
    r = rule(**over)
    assert gov.rule_errors(r, upstream=True)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(r, SCHEMAS["rule"])


def test_python_validator_and_schema_agree_on_a_good_rule():
    assert gov.rule_errors(rule(), upstream=True) == []
    jsonschema.validate(rule(), SCHEMAS["rule"])


# ---- AC1: refusal names rule id and field ------------------------------------------------------ #

def test_bad_id_pattern_refused_naming_id_and_field(tmp_path):
    plugin = make_plugin(tmp_path, [rule("FG-bad-1")])
    rc, out, _ = run(plugin, make_project(tmp_path), "validate")
    assert rc == 1 and "FG-bad-1: id:" in out


def test_missing_check_binding_refused_naming_field(tmp_path):
    r = rule()
    del r["check"]
    rc, out, _ = run(make_plugin(tmp_path, [r]), make_project(tmp_path), "validate")
    assert rc == 1 and "FG-TST-001: check:" in out


def test_ref_less_ci_binding_refused(tmp_path):
    rc, out, _ = run(make_plugin(tmp_path, [rule(check={"kind": "ci"})]), make_project(tmp_path), "validate")
    assert rc == 1 and "FG-TST-001: check.ref:" in out


def test_floor_with_exception_allowed_refused(tmp_path):
    rc, out, _ = run(make_plugin(tmp_path, [rule(floor=True)]), make_project(tmp_path), "validate")
    assert rc == 1 and "FG-TST-001: exception.allowed:" in out


def test_valid_rule_and_floor_without_exception_pass(tmp_path):  # control
    plugin = make_plugin(tmp_path, [rule(), rule("FG-TST-002", floor=True, exception={"allowed": False})])
    rc, out, _ = run(plugin, make_project(tmp_path), "validate")
    assert rc == 0 and "governance-valid" in out


def test_duplicate_id_and_dangling_supersede_refused(tmp_path):
    plugin = make_plugin(tmp_path, [rule(), rule(), rule("FG-TST-002", status="superseded-by:FG-TST-099")])
    rc, out, _ = run(plugin, make_project(tmp_path), "validate")
    assert rc == 1 and "FG-TST-001: id: duplicate" in out and "FG-TST-099 does not exist" in out


def test_supersede_resolving_passes(tmp_path):  # control
    plugin = make_plugin(tmp_path, [rule(), rule("FG-TST-002", status="superseded-by:FG-TST-001")])
    assert run(plugin, make_project(tmp_path), "validate")[0] == 0


# ---- AC2: adopter namespace ------------------------------------------------------------------- #

def test_adopter_rule_in_fg_namespace_refused(tmp_path):
    plugin = make_plugin(tmp_path, [rule()])
    proj = make_project(tmp_path, local_rules=[rule("FG-ACM-001")])
    rc, out, _ = run(plugin, proj, "validate")
    assert rc == 1 and "adopter-rule-in-upstream-namespace:FG-ACM-001" in out


def test_adopter_rule_with_own_prefix_accepted(tmp_path):  # control
    plugin = make_plugin(tmp_path, [rule()])
    proj = make_project(tmp_path, local_rules=[rule("ACME-LCL-001")])
    assert run(plugin, proj, "validate")[0] == 0


def test_duplicate_between_local_and_upstream_refused(tmp_path):
    plugin = make_plugin(tmp_path, [rule("ACME-TST-001")])  # upstream must be FG-
    rc, out, _ = run(plugin, make_project(tmp_path), "validate")
    assert rc == 1 and "upstream kit rules MUST use the FG- namespace" in out


# ---- AC3: render = kits + local, overrides not on floor ---------------------------------------- #

def _two_kit_plugin(tmp_path):
    plugin = make_plugin(tmp_path, [rule("FG-TST-001"), rule("FG-TST-002", floor=True, exception={"allowed": False})])
    return plugin


def test_render_one_file_per_category_with_override_applied(tmp_path):
    plugin = _two_kit_plugin(tmp_path)
    proj = make_project(tmp_path, local_rules=[rule("ACME-LCL-001", category="local-cat")],
                        overrides=[{"id": "FG-TST-001", "level": 3}])
    rc, _, _ = run(plugin, proj, "render")
    assert rc == 0
    files = sorted(p.name for p in (proj / "governance" / "rendered").glob("*.md"))
    assert files == ["local-cat.md", "testing.md"]
    text = (proj / "governance" / "rendered" / "testing.md").read_text()
    assert "FG-TST-001" in text and "FG-TST-002" in text and "level 3" in text
    assert "ACME-LCL-001" in (proj / "governance" / "rendered" / "local-cat.md").read_text()


def test_floor_override_refused_naming_id_and_writes_nothing(tmp_path):
    plugin = _two_kit_plugin(tmp_path)
    proj = make_project(tmp_path, overrides=[{"id": "FG-TST-002", "level": 3}])
    rc, _, err = run(plugin, proj, "render")
    assert rc == 2 and "floor-override-refused:FG-TST-002" in err
    assert not (proj / "governance" / "rendered").exists()


def test_override_of_unknown_rule_and_invalid_merge_refused(tmp_path):
    plugin = _two_kit_plugin(tmp_path)
    rc, _, err = run(plugin, make_project(tmp_path, overrides=[{"id": "FG-TST-077", "level": 2}]), "render")
    assert rc == 2 and "override-unknown-rule:FG-TST-077" in err
    proj2 = tmp_path / "p2"
    (proj2 / "governance" / "local").mkdir(parents=True)
    (proj2 / "governance" / "local" / "rules.yaml").write_text(
        yaml.safe_dump({"overrides": [{"id": "FG-TST-001", "level": 9}]}))
    rc, _, err = run(plugin, proj2, "render")
    assert rc == 2 and "FG-TST-001: level" in err


def test_render_is_deterministic_and_removes_stale_category(tmp_path):
    plugin = _two_kit_plugin(tmp_path)
    proj = make_project(tmp_path)
    run(plugin, proj, "render")
    stale = proj / "governance" / "rendered" / "gone.md"
    stale.write_text("x")
    first = (proj / "governance" / "rendered" / "testing.md").read_text()
    run(plugin, proj, "render")
    assert not stale.exists()
    assert (proj / "governance" / "rendered" / "testing.md").read_text() == first


# ---- AC4: applicable --------------------------------------------------------------------------- #

def _scoped_plugin(tmp_path):
    return make_plugin(tmp_path, [
        rule("FG-TST-001", applies_to={"paths": ["**"], "blueprints": ["*"]}),
        rule("FG-TST-002", applies_to={"paths": ["src/auth/**"], "blueprints": ["*"]}),
        rule("FG-TST-003", applies_to={"paths": ["*.md"], "blueprints": ["*"]}),
        rule("FG-TST-004", status="retired", applies_to={"paths": ["src/auth/**"], "blueprints": ["*"]}),
    ])


def test_applicable_returns_exactly_matching_active_rules(tmp_path):
    rc, out, _ = run(_scoped_plugin(tmp_path), make_project(tmp_path), "applicable", "--paths", "src/auth/login.py", "--json")
    assert rc == 0 and json.loads(out) == ["FG-TST-001", "FG-TST-002"]


def test_applicable_unmatched_path_returns_only_double_star_rules(tmp_path):  # control
    rc, out, _ = run(_scoped_plugin(tmp_path), make_project(tmp_path), "applicable", "--paths", "lib/util.py", "--json")
    assert json.loads(out) == ["FG-TST-001"]


def test_applicable_single_star_does_not_cross_directories(tmp_path):
    plugin = _scoped_plugin(tmp_path)
    proj = make_project(tmp_path)
    assert json.loads(run(plugin, proj, "applicable", "--paths", "README.md", "--json")[1]) == ["FG-TST-001", "FG-TST-003"]
    assert json.loads(run(plugin, proj, "applicable", "--paths", "docs/a.md", "--json")[1]) == ["FG-TST-001"]


def test_applicable_unions_several_paths(tmp_path):
    rc, out, _ = run(_scoped_plugin(tmp_path), make_project(tmp_path), "applicable", "--paths", "lib/x.py", "src/auth/a.py", "--json")
    assert json.loads(out) == ["FG-TST-001", "FG-TST-002"]


# ---- AC5: exceptions --------------------------------------------------------------------------- #

def _exc(expires, rid="FG-TST-001"):
    return {"rule": rid, "scope": "src/legacy/**", "reason": "migration", "owner": "team-a", "expires": expires}


def test_expired_exception_fails_check_naming_it(tmp_path):
    plugin = make_plugin(tmp_path, [rule()])
    proj = make_project(tmp_path, exceptions=[_exc("2026-01-01")])
    for verb in ("check", "exceptions"):
        rc, out, _ = run(plugin, proj, verb, "--today", "2026-11-04")
        assert rc == 1 and "FG-TST-001: exception expired 2026-01-01" in out


def test_unexpired_exception_is_a_skip_never_a_pass(tmp_path):  # control
    plugin = make_plugin(tmp_path, [rule()])
    proj = make_project(tmp_path, exceptions=[_exc("2027-01-01")])
    rc, out, _ = run(plugin, proj, "check", "--today", "2026-11-04")
    assert rc == 0 and "SKIP exception until 2027-01-01" in out
    assert "governance-check-ok" in out and "pass" not in out.lower().replace("passes", "")


def test_expiry_day_itself_is_still_a_skip(tmp_path):
    proj = make_project(tmp_path, exceptions=[_exc("2026-11-04")])
    rc, out, _ = run(make_plugin(tmp_path, [rule()]), proj, "exceptions", "--today", "2026-11-04")
    assert rc == 0 and "SKIP" in out


@pytest.mark.parametrize("exc,needle", [
    ({"rule": "FG-TST-001", "scope": "x", "reason": "r", "owner": "o"}, "missing expires"),
    (_exc("2027-01-01", "FG-TST-009"), "unknown rule"),
    (_exc("soon"), "not YYYY-MM-DD"),
])
def test_malformed_exceptions_are_findings(tmp_path, exc, needle):
    proj = make_project(tmp_path, exceptions=[exc])
    rc, out, _ = run(make_plugin(tmp_path, [rule()]), proj, "exceptions", "--today", "2026-11-04")
    assert rc == 1 and needle in out


def test_exception_on_a_rule_that_forbids_them_is_a_finding(tmp_path):
    plugin = make_plugin(tmp_path, [rule(floor=True, exception={"allowed": False})])
    rc, out, _ = run(plugin, make_project(tmp_path, exceptions=[_exc("2027-01-01")]), "exceptions", "--today", "2026-11-04")
    assert rc == 1 and "not allowed" in out


def test_no_exceptions_is_clean(tmp_path):  # control
    rc, out, _ = run(make_plugin(tmp_path, [rule()]), make_project(tmp_path), "exceptions", "--today", "2026-11-04")
    assert rc == 0 and "no exceptions" in out


# ---- check: lock and rendered drift ------------------------------------------------------------ #

def _lock(proj, version):
    (proj / ".foundry").mkdir(exist_ok=True)
    (proj / ".foundry" / "governance.lock").write_text(yaml.safe_dump(
        {"kits": [{"id": "testing", "version": version, "sha256": "a" * 64, "level": 1}]}))


def test_check_flags_lock_version_drift_and_stale_render(tmp_path):
    plugin, proj = make_plugin(tmp_path, [rule()]), make_project(tmp_path)
    _lock(proj, "0.9.0")
    rc, out, _ = run(plugin, proj, "check", "--today", "2026-11-04")
    assert rc == 1 and "lock-version-drift:testing: locked 0.9.0, installed 1.0.0" in out
    assert "rendered-stale:testing" in out


def test_check_clean_when_lock_and_render_match(tmp_path):  # control
    plugin, proj = make_plugin(tmp_path, [rule()]), make_project(tmp_path)
    _lock(proj, "1.0.0")
    run(plugin, proj, "render")
    rc, out, _ = run(plugin, proj, "check", "--today", "2026-11-04")
    assert rc == 0 and "governance-check-ok" in out


def test_check_flags_a_hand_edited_render(tmp_path):
    plugin, proj = make_plugin(tmp_path, [rule()]), make_project(tmp_path)
    run(plugin, proj, "render")
    f = proj / "governance" / "rendered" / "testing.md"
    f.write_text(f.read_text() + "tampered\n")
    rc, out, _ = run(plugin, proj, "check", "--today", "2026-11-04")
    assert rc == 1 and "rendered-stale:testing" in out


def test_check_on_a_workspace_without_governance_artifacts_is_clean(tmp_path):
    rc, out, _ = run(make_plugin(tmp_path, [rule()]), tmp_path / "bare", "check", "--today", "2026-11-04")
    assert rc == 0


# ---- baseline ---------------------------------------------------------------------------------- #

def test_baseline_writes_sorted_unique_findings(tmp_path):
    proj = make_project(tmp_path)
    f = tmp_path / "findings.json"
    f.write_text(json.dumps(["b.py:3", "a.py:1", "b.py:3"]))
    rc, _, _ = run(make_plugin(tmp_path, [rule()]), proj, "baseline", "--check", "lint-x", "--findings", str(f))
    assert rc == 0
    doc = json.loads((proj / "governance" / "local" / "baselines" / "lint-x.json").read_text())
    assert doc == {"check": "lint-x", "findings": ["a.py:1", "b.py:3"]}


@pytest.mark.parametrize("name", ["../evil", "A/b", "", "x" * 80])
def test_baseline_refuses_a_path_shaped_check_name(tmp_path, name):
    proj = make_project(tmp_path)
    f = tmp_path / "f.json"
    f.write_text("[]")
    rc, _, err = run(make_plugin(tmp_path, [rule()]), proj, "baseline", "--check", name, "--findings", str(f))
    assert rc == 2 and "baseline-bad-check-name" in err
    assert not (proj / "governance" / "local" / "baselines").exists()


def test_baseline_refuses_non_list_findings(tmp_path):
    f = tmp_path / "f.json"
    f.write_text('{"a": 1}')
    rc, _, err = run(make_plugin(tmp_path, [rule()]), make_project(tmp_path), "baseline", "--check", "x", "--findings", str(f))
    assert rc == 2 and "must-be-a-list" in err


# ---- load -------------------------------------------------------------------------------------- #

def test_load_lists_kits_and_effective_rules(tmp_path):
    rc, out, _ = run(make_plugin(tmp_path, [rule()]), make_project(tmp_path, local_rules=[rule("ACME-LCL-001")]), "load", "--json")
    doc = json.loads(out)
    assert rc == 0 and doc["kits"][0]["id"] == "testing" and [r["id"] for r in doc["rules"]] == ["FG-TST-001", "ACME-LCL-001"]


def test_load_refuses_an_invalid_kit(tmp_path):
    rc, _, err = run(make_plugin(tmp_path, [rule("FG-bad-1")]), make_project(tmp_path), "load")
    assert rc == 2 and "FG-bad-1" in err


# ---- AC7: no network, no subprocess ------------------------------------------------------------ #

def test_no_verb_spawns_a_process_or_calls_the_network(tmp_path, monkeypatch):
    calls = []
    for name in ("run", "Popen", "check_output", "check_call", "call"):
        monkeypatch.setattr(subprocess, name, lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(os, "system", lambda *a: calls.append(a))
    plugin = make_plugin(tmp_path, [rule()])
    proj = make_project(tmp_path, exceptions=[_exc("2027-01-01")])
    f = tmp_path / "f.json"
    f.write_text("[]")
    for argv in (["load"], ["validate"], ["render"], ["applicable", "--paths", "a"], ["check", "--today", "2026-11-04"],
                 ["exceptions", "--today", "2026-11-04"], ["baseline", "--check", "c", "--findings", str(f)]):
        run(plugin, proj, *argv)
    assert calls == []


def test_the_scripts_import_no_process_or_network_module():
    for name in ("foundry_governance.py", "foundry_brief.py"):
        src = (SCRIPTS / name).read_text()
        for mod in ("subprocess", "socket", "urllib", "http.client", "requests"):
            assert f"import {mod}" not in src and f"from {mod}" not in src


# ---- paper guard: the four named dirs are admitted, nothing else under .foundry ---------------- #

def _guard(root, rel):
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(root / rel)}}
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    return subprocess.run([sys.executable, str(ROOT / "hooks" / "foundry-paper-guard.py")], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, cwd=str(root), timeout=60)


@pytest.fixture()
def guard_repo(tmp_path):
    root = tmp_path / "w"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


@pytest.mark.parametrize("rel", [".foundry/research/notes.md", ".foundry/decisions/0001-x.md",
                                 "governance/local/rules.yaml", "governance/rendered/security.md"])
def test_paper_guard_admits_rule_mandated_paper(guard_repo, rel):
    assert _guard(guard_repo, rel).returncode == 0


@pytest.mark.parametrize("rel", [".foundry/other/notes.md", ".foundry/research.md", "docs/a.md", "specs/x.md"])
def test_paper_guard_still_refuses_other_paper(guard_repo, rel):  # control: names, not a free pass
    assert _guard(guard_repo, rel).returncode == 2


def test_paper_guard_does_not_admit_a_lookalike_prefix(guard_repo):
    assert _guard(guard_repo, ".foundry/research-extra/x.md").returncode == 2


# ---- permission floor + hook carve-outs -------------------------------------------------------- #

def _psa():
    spec = importlib.util.spec_from_file_location("psa_gv", ROOT / "hooks" / "foundry-plugin-scripts-allow.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.mark.parametrize("verb,silent", [("render", False), ("baseline", False), ("validate", True),
                                         ("applicable", True), ("check", True), ("exceptions", True), ("load", True)])
def test_allow_hook_carves_out_the_writing_verbs(verb, silent):
    psa = _psa()
    assert psa.allowed(f"python3 {ROOT}/scripts/foundry_governance.py {verb}", str(ROOT)) is silent


def test_floor_rows_exist_and_are_mirrored():
    docs = json.loads((ROOT / "docs" / "permission-floor.json").read_text())
    cli = (ROOT / "cli" / "permission-floor.json").read_bytes()
    assert cli == (ROOT / "docs" / "permission-floor.json").read_bytes()
    tiers = {e["rule"].split("/scripts/")[1]: e["tier"] for e in docs["entries"] if "/scripts/foundry_" in e["rule"]}
    assert tiers["foundry_governance.py:*)"] == "ask" and tiers["foundry_brief.py:*)"] == "allow"
