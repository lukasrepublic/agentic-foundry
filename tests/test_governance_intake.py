"""gv-15 intake: discover -> select -> declare -> apply (+ baseline, check drift, level/surface filters).

Every acceptance criterion has a failing-case test and a control. No real `gh` and no network: `gh` is a PATH shim
that records its argv, and the "remote" is a local bare repository.
"""
import io
import json
import os
import shutil
import stat
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import foundry_governance as gov  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "blueprints"
BLUEPRINTS = ["aws-eks-karpenter", "claude-plugin", "cli-tool", "data-pipeline", "node-web", "python-uv-lib",
              "python-uv-service", "service-event-driven"]
SCHEMA_LOCK = json.loads((ROOT / "schema" / "governance-lock.schema.json").read_text())


# ------------------------------------------------------------------ helpers ---

def run(proj, *argv, plugin=ROOT, stdin=None):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = gov.main(["--plugin-root", str(plugin), "--project-dir", str(proj), *argv])
    return rc, out.getvalue(), err.getvalue()


def tree_from_facts(dest: Path, facts: dict) -> Path:
    """Materialise a repository tree whose discovered facts equal `facts` (paths as empty files; dependencies in
    the root manifest the fixture already names)."""
    dest.mkdir(parents=True, exist_ok=True)
    deps = facts.get("dependencies", [])
    for rel in facts["paths"]:
        f = dest / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        if rel == "pyproject.toml":
            f.write_text('[project]\nname = "fixture"\nversion = "0.0.1"\ndependencies = ['
                         + ", ".join(f'"{d}>=0"' for d in deps) + "]\n")
        elif rel == "package.json":
            f.write_text(json.dumps({"name": "fixture", "dependencies": {d: "1.0.0" for d in deps}}))
        else:
            f.write_text("")
    return dest


def git(cwd, *argv):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["git", *argv], cwd=cwd, env=env, capture_output=True, text=True, check=True).stdout


def make_repo(path: Path, facts_name="cli-tool") -> Path:
    facts = json.loads((FIXTURES / f"{facts_name}.facts.json").read_text())
    tree_from_facts(path, facts)
    git(path, "init", "-q", "-b", "main")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "init")
    return path


def intake(proj, blueprint="cli-tool", plugin=ROOT):
    assert run(proj, "select", "--confirm", blueprint, plugin=plugin)[0] == 0
    assert run(proj, "declare", "--asvs-level", "2", "--data-classification", "internal", plugin=plugin)[0] == 0


def snapshot(proj: Path) -> dict:
    out = {}
    for p in sorted(proj.rglob("*")):
        if ".git" in p.parts or not p.is_file():
            continue
        out[str(p.relative_to(proj))] = p.read_bytes()
    return out


def gh_shim(tmp_path: Path, monkeypatch) -> Path:
    bindir, log = tmp_path / "shim-bin", tmp_path / "gh-argv.jsonl"
    bindir.mkdir()
    shim = bindir / "gh"
    shim.write_text("#!/usr/bin/env python3\nimport json, sys\n"
                    f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
                    "print('https://example.invalid/pull/1')\n")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return log


def make_plugin_copy(tmp_path: Path) -> Path:
    plugin = tmp_path / "plugin"
    for d in ("governance", "packs", "schema"):
        shutil.copytree(ROOT / d, plugin / d)
    (plugin / ".claude-plugin").mkdir()
    shutil.copy(ROOT / ".claude-plugin" / "plugin.json", plugin / ".claude-plugin" / "plugin.json")
    return plugin


# ----------------------------------------------- AC-1: discover + select ranking ---

@pytest.mark.parametrize("name", BLUEPRINTS)
def test_select_ranks_the_matching_blueprint_first_with_evidence(tmp_path, name):
    proj = tree_from_facts(tmp_path / "t", json.loads((FIXTURES / f"{name}.facts.json").read_text()))
    facts = tmp_path / "facts.json"
    rc, out, _ = run(proj, "discover")
    assert rc == 0
    facts.write_text(out)
    rc, out, _ = run(proj, "select", "--facts", str(facts), "--json")
    assert rc == 3
    doc = json.loads(out)
    top = doc["candidates"][0]
    assert top["id"] == name and top["above_threshold"] is True and top["evidence"]
    assert doc["above_threshold_count"] >= 1


def test_select_over_an_empty_tree_ranks_none_above_threshold_and_says_so(tmp_path):
    proj = tmp_path / "empty"
    proj.mkdir()
    rc, out, _ = run(proj, "select", "--json")
    assert rc == 3
    doc = json.loads(out)
    assert doc["above_threshold_count"] == 0 and not any(c["above_threshold"] for c in doc["candidates"])
    rc, text, _ = run(proj, "select")
    assert "no candidate above threshold" in text


def test_discover_reports_every_detector_with_a_status(tmp_path):
    proj = tree_from_facts(tmp_path / "t", json.loads((FIXTURES / "node-web.facts.json").read_text()))
    rc, out, _ = run(proj, "discover")
    assert rc == 0
    facts = json.loads(out)
    names = [d["name"] for d in facts["detectors"]]
    assert names == ["manifests", "lockfiles", "ci", "iac", "containers", "dependencies", "auth-libraries"]
    assert all(d["status"] in ("found", "not-found", "could-not-look") for d in facts["detectors"])
    by = {d["name"]: d for d in facts["detectors"]}
    assert by["manifests"]["status"] == "found" and by["iac"]["status"] == "not-found"
    assert "fastify" in facts["dependencies"]


def test_discover_finds_auth_library_heuristics(tmp_path):
    proj = tmp_path / "t"
    proj.mkdir()
    (proj / "package.json").write_text(json.dumps({"dependencies": {"passport": "1", "left-pad": "1"}}))
    facts = json.loads(run(proj, "discover")[1])
    by = {d["name"]: d for d in facts["detectors"]}
    assert by["auth-libraries"]["status"] == "found" and facts["auth_libraries"] == ["passport"]


def test_discover_reports_could_not_look_for_an_unparseable_manifest(tmp_path):
    proj = tmp_path / "t"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("this is [not toml")
    facts = json.loads(run(proj, "discover")[1])
    by = {d["name"]: d for d in facts["detectors"]}
    assert by["dependencies"]["status"] == "could-not-look"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every directory")
def test_discover_reports_could_not_look_for_an_unreadable_directory(tmp_path):
    proj = tmp_path / "t"
    (proj / "locked").mkdir(parents=True)
    (proj / "locked" / "f").write_text("x")
    (proj / "locked").chmod(0)
    try:
        facts = json.loads(run(proj, "discover")[1])
    finally:
        (proj / "locked").chmod(0o755)
    assert any(d["status"] == "could-not-look" for d in facts["detectors"])


def test_discover_skips_dependency_and_vcs_directories(tmp_path):  # control
    proj = tmp_path / "t"
    (proj / "node_modules" / "x").mkdir(parents=True)
    (proj / "node_modules" / "x" / "package.json").write_text("{}")
    (proj / ".git").mkdir()
    (proj / ".git" / "config").write_text("")
    facts = json.loads(run(proj, "discover")[1])
    assert facts["paths"] == []


# ------------------------------------------------- AC-2: select needs a decision ---

def test_select_without_confirm_exits_3_and_writes_nothing(tmp_path):
    proj = make_repo(tmp_path / "r")
    before = snapshot(proj)
    rc, out, _ = run(proj, "select")
    assert rc == 3 and "cli-tool" in out and "--confirm" in out
    assert snapshot(proj) == before and not (proj / ".foundry").exists()


def test_select_with_confirm_records_the_choice(tmp_path):  # control
    proj = make_repo(tmp_path / "r")
    rc, out, _ = run(proj, "select", "--confirm", "cli-tool")
    assert rc == 0
    state = json.loads((proj / ".foundry" / "governance-intake.json").read_text())
    assert state["blueprint"] == "cli-tool"


def test_select_refuses_an_unknown_blueprint_id_and_writes_nothing(tmp_path):
    proj = make_repo(tmp_path / "r")
    rc, _, err = run(proj, "select", "--confirm", "not-a-blueprint")
    assert rc == 2 and "unknown-blueprint" in err and not (proj / ".foundry").exists()


def test_an_llm_proposal_is_only_a_labelled_suggestion(tmp_path):
    proj = make_repo(tmp_path / "r")
    rc, out, _ = run(proj, "select", "--suggest", "node-web")
    assert rc == 3 and "SUGGESTION" in out and "not a selection" in out
    assert not (proj / ".foundry").exists()


# ------------------------------------------------------------------- declare ---

def test_declare_refuses_out_of_range_and_unknown_values(tmp_path):
    proj = make_repo(tmp_path / "r")
    run(proj, "select", "--confirm", "cli-tool")
    assert run(proj, "declare", "--asvs-level", "4")[0] == 2
    assert run(proj, "declare", "--asvs-level", "2", "--surfaces", "spaceship")[0] == 2
    assert run(proj, "declare", "--asvs-level", "2", "--trust-boundary", "bad path with spaces")[0] == 2


def test_declare_writes_the_declarations_and_defaults_surfaces_from_the_blueprint(tmp_path):
    proj = make_repo(tmp_path / "r")
    run(proj, "select", "--confirm", "cli-tool")
    rc, _, _ = run(proj, "declare", "--asvs-level", "2", "--data-classification", "internal",
                   "--trust-boundary", "src/auth/**")
    assert rc == 0
    d = json.loads((proj / ".foundry" / "governance-intake.json").read_text())["declarations"]
    assert d["asvs_level"] == 2 and d["trust_boundaries"] == ["src/auth/**"] and d["surfaces"] == ["cli"]


# ------------------------------------------------------------- AC-3: apply ---

def test_apply_writes_lock_adopter_layer_snippets_and_is_idempotent(tmp_path):
    proj = make_repo(tmp_path / "r")
    intake(proj)
    rc, out, err = run(proj, "apply")
    assert rc == 0, err
    lock = yaml.safe_load((proj / ".foundry" / "governance.lock").read_text())
    import jsonschema
    jsonschema.Draft202012Validator(SCHEMA_LOCK).validate(lock)
    assert lock["blueprint"]["id"] == "cli-tool" and lock["declarations"]["asvs_level"] == 2
    assert {k["id"] for k in lock["kits"]}
    local = proj / "governance" / "local"
    for rel in ("README.md", "exceptions.yaml", "baselines", "adr"):
        assert (local / rel).exists(), rel
    assert yaml.safe_load((local / "exceptions.yaml").read_text()) == {"exceptions": []}
    assert (proj / ".github" / "workflows" / "ci-gitleaks.yml").is_file()
    assert (proj / "governance" / "rendered").is_dir()
    # a reviewable diff: everything apply wrote is visible to git
    changed = git(proj, "status", "--porcelain", "--untracked-files=all", "-uall")
    assert "governance/local/README.md" in changed and ".github/workflows/ci-gitleaks.yml" in changed
    before = snapshot(proj)
    rc2, out2, _ = run(proj, "apply")
    assert rc2 == 0 and snapshot(proj) == before


def test_apply_never_overwrites_an_adopter_file_that_differs(tmp_path):
    proj = make_repo(tmp_path / "r")
    intake(proj)
    wf = proj / ".github" / "workflows" / "ci-gitleaks.yml"
    wf.parent.mkdir(parents=True)
    wf.write_text("name: mine\n")
    rc, out, _ = run(proj, "apply")
    assert rc == 0 and wf.read_text() == "name: mine\n" and "kept" in out


def test_apply_without_a_selection_is_refused_and_writes_nothing(tmp_path):
    proj = make_repo(tmp_path / "r")
    before = snapshot(proj)
    rc, _, err = run(proj, "apply")
    assert rc == 2 and "intake-incomplete" in err and snapshot(proj) == before


def test_apply_without_declarations_is_refused(tmp_path):
    proj = make_repo(tmp_path / "r")
    run(proj, "select", "--confirm", "cli-tool")
    rc, _, err = run(proj, "apply")
    assert rc == 2 and "declarations-missing" in err


def test_apply_opens_a_pull_request_by_branch_push_and_gh(tmp_path, monkeypatch):
    log = gh_shim(tmp_path, monkeypatch)
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    proj = make_repo(tmp_path / "r")
    git(proj, "remote", "add", "origin", str(bare))
    git(proj, "push", "-q", "origin", "main")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@example.invalid")
    intake(proj)
    rc, out, err = run(proj, "apply", "--pr", "--base", "main")
    assert rc == 0, err
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(calls) == 1 and calls[0][:2] == ["pr", "create"]
    assert "--base" in calls[0] and calls[0][calls[0].index("--base") + 1] == "main"
    assert "governance/intake" in git(bare, "branch", "--list")
    assert ".foundry/governance.lock" in git(proj, "show", "--name-only", "--format=", "governance/intake")
    # the working branch of the adopter is not left on main with uncommitted generated files
    assert git(proj, "status", "--porcelain", "--untracked-files=no").strip() == ""


def test_apply_pr_refuses_a_dirty_tree_and_calls_nothing(tmp_path, monkeypatch):
    log = gh_shim(tmp_path, monkeypatch)
    proj = make_repo(tmp_path / "r")
    git(proj, "remote", "add", "origin", str(tmp_path / "x.git"))
    intake(proj)
    (proj / "pyproject.toml").write_text((proj / "pyproject.toml").read_text() + "\n# edit\n")
    rc, _, err = run(proj, "apply", "--pr")
    assert rc == 2 and "dirty-tree" in err and not log.exists()
    assert not (proj / ".foundry" / "governance.lock").exists()


# ----------------------------------------------------- stack-profile.lock absorbed ---

def test_stack_profile_lock_is_absorbed_by_a_one_time_migration(tmp_path):
    proj = make_repo(tmp_path / "r")
    (proj / ".foundry").mkdir()
    (proj / ".foundry" / "stack-profile.lock").write_text(json.dumps(
        {"profiles": [{"id": "cli-tool", "version": "0.1.0", "sha256": "0" * 64}]}))
    rc, out, err = run(proj, "apply")
    assert rc == 0, err
    lock = yaml.safe_load((proj / ".foundry" / "governance.lock").read_text())
    assert lock["blueprint"]["id"] == "cli-tool" and "absorbed" in out
    # once the governance lock exists the stack lock is never consulted again
    (proj / ".foundry" / "stack-profile.lock").write_text(json.dumps({"profiles": [{"id": "node-web"}]}))
    assert run(proj, "apply")[0] == 0
    assert yaml.safe_load((proj / ".foundry" / "governance.lock").read_text())["blueprint"]["id"] == "cli-tool"


def test_a_stack_profile_lock_naming_an_unshipped_blueprint_is_not_absorbed(tmp_path):
    proj = make_repo(tmp_path / "r")
    (proj / ".foundry").mkdir()
    (proj / ".foundry" / "stack-profile.lock").write_text(json.dumps({"profiles": [{"id": "private-thing"}]}))
    rc, _, err = run(proj, "apply")
    assert rc == 2 and "intake-incomplete" in err


# --------------------------------------------------------------- baseline verb ---

def test_baseline_freezes_findings_per_check_and_refuses_a_bad_name(tmp_path):
    proj = make_repo(tmp_path / "r")
    f = tmp_path / "f.json"
    f.write_text(json.dumps(["b", "a", "a"]))
    assert run(proj, "baseline", "--check", "semgrep", "--findings", str(f))[0] == 0
    doc = json.loads((proj / "governance" / "local" / "baselines" / "semgrep.json").read_text())
    assert doc == {"check": "semgrep", "findings": ["a", "b"]}
    assert run(proj, "baseline", "--check", "../x", "--findings", str(f))[0] == 2


# ----------------------------------------------- check: lock-vs-plugin drift ---

def test_check_is_clean_straight_after_apply_and_flags_each_drift(tmp_path):
    plugin = make_plugin_copy(tmp_path)
    proj = make_repo(tmp_path / "r")
    intake(proj, plugin=plugin)
    assert run(proj, "apply", plugin=plugin)[0] == 0
    rc, out, _ = run(proj, "check", plugin=plugin)
    assert rc == 0 and "governance-check-ok" in out
    # the plugin advances a blueprint, a kit and its own version
    bp = plugin / "packs" / "stack-profiles" / "cli-tool" / "stack-profile.yaml"
    bp.write_text(bp.read_text().replace("version: 0.1.0", "version: 0.2.0", 1))
    kit = plugin / "governance" / "kits" / "delivery" / "kit.yaml"
    kit.write_text(kit.read_text().replace("version: 1.0.0", "version: 1.1.0", 1))
    pj = plugin / ".claude-plugin" / "plugin.json"
    doc = json.loads(pj.read_text())
    doc["version"] = "9.9.9"
    pj.write_text(json.dumps(doc))
    rc, out, _ = run(proj, "check", plugin=plugin)
    assert rc == 1
    assert "lock-blueprint-drift:cli-tool" in out and "lock-version-drift:delivery" in out
    assert "lock-plugin-version-drift" not in out                         # provenance only: never a finding by itself


# ------------------------------------- amendment: --level / --surfaces filters ---

def _rules():
    def r(rid, level, bps, floor=False):
        return {"id": rid, "title": "t", "statement": "An agent MUST x.", "category": "testing", "level": level,
                "floor": floor, "status": "active", "applies_to": {"paths": ["**"], "blueprints": bps},
                "check": {"kind": "declared"}, "exception": {"allowed": not floor}}
    return [r("FG-TST-001", 1, ["*"]), r("FG-TST-002", 3, ["*"]), r("FG-TST-003", 2, ["ui"]),
            r("FG-TST-004", 2, ["cli-tool", "ui"]), r("FG-TST-005", 3, ["ui"], floor=True)]


def _synthetic_plugin(tmp_path):
    d = tmp_path / "p" / "governance" / "kits" / "testing"
    d.mkdir(parents=True)
    rs = _rules()
    (d / "kit.yaml").write_text(yaml.safe_dump({"kit": "testing", "version": "1.0.0", "category": "testing",
                                                "rules": [x["id"] for x in rs]}))
    (d / "rules.yaml").write_text(yaml.safe_dump({"kit": "testing", "version": "1.0.0", "rules": rs}))
    return tmp_path / "p"


def test_applicable_level_filter_keeps_floors_and_drops_higher_levels(tmp_path):
    plugin, proj = _synthetic_plugin(tmp_path), tmp_path / "proj"
    proj.mkdir()
    rc, out, _ = run(proj, "applicable", "--paths", "x", "--level", "1", "--json", plugin=plugin)
    assert json.loads(out) == ["FG-TST-001", "FG-TST-005"]          # level 1 plus the floor, never relaxed
    rc, out, _ = run(proj, "applicable", "--paths", "x", "--json", plugin=plugin)  # control: no filter
    assert len(json.loads(out)) == 5


def test_surface_filter_omits_ui_only_rules_when_there_is_no_ui(tmp_path):
    plugin, proj = _synthetic_plugin(tmp_path), tmp_path / "proj"
    proj.mkdir()
    ids = json.loads(run(proj, "applicable", "--paths", "x", "--surfaces", "cli", "--json", plugin=plugin)[1])
    assert "FG-TST-003" not in ids and "FG-TST-001" in ids and "FG-TST-004" in ids and "FG-TST-005" in ids
    ids = json.loads(run(proj, "applicable", "--paths", "x", "--surfaces", "ui,cli", "--json", plugin=plugin)[1])
    assert "FG-TST-003" in ids                                          # control: a ui surface keeps it
    assert run(proj, "applicable", "--paths", "x", "--surfaces", "bogus", plugin=plugin)[0] == 2


def test_render_from_lock_uses_the_locked_kit_levels_and_check_accepts_it(tmp_path):
    plugin, proj = _synthetic_plugin(tmp_path), tmp_path / "proj"
    (proj / ".foundry").mkdir(parents=True)
    sha = "a" * 64
    (proj / ".foundry" / "governance.lock").write_text(yaml.safe_dump(
        {"blueprint": {"id": "cli-tool", "version": "1.0.0", "sha256": sha},
         "kits": [{"id": "testing", "version": "1.0.0", "sha256": sha, "level": 1}],
         "declarations": {"asvs_level": 1, "data_classification": [], "trust_boundaries": [], "surfaces": ["cli"]},
         "facts_sha256": sha, "intake_at": "2026-11-04", "plugin_version": "2.0.0"}))
    assert run(proj, "render", "--from-lock", plugin=plugin)[0] == 0
    text = (proj / "governance" / "rendered" / "testing.md").read_text()
    assert "FG-TST-001" in text and "FG-TST-002" not in text and "FG-TST-005" in text and "FG-TST-003" not in text
    rc, out, _ = run(proj, "check", "--today", "2026-11-04", plugin=plugin)
    assert rc == 0, out


def test_applicable_extends_the_security_review_globs_with_the_declared_trust_boundaries(tmp_path):
    proj = make_repo(tmp_path / "r")
    intake(proj)
    assert run(proj, "declare", "--asvs-level", "2", "--trust-boundary", "billing/ledger/**")[0] == 0
    assert run(proj, "apply")[0] == 0
    ids = json.loads(run(proj, "applicable", "--paths", "billing/ledger/post.py", "--json")[1])
    assert "FG-SEC-032" in ids
    control = json.loads(run(proj, "applicable", "--paths", "docs/notes.md", "--json")[1])
    assert "FG-SEC-032" not in control


# ------------------------------------------ AC-OM-1: a fresh workspace defaults ---

def test_fresh_workspace_enforces_every_floor_and_defaults_every_tunable(tmp_path):
    import foundry_operating_model as om
    proj = tmp_path / "fresh"
    proj.mkdir()
    assert not (proj / ".foundry" / "governance.lock").exists()
    assert not (proj / ".foundry" / "operating-model.yaml").exists()
    rc, out, _ = run(proj, "load", "--json")
    assert rc == 0
    rules = json.loads(out)["rules"]
    floors = {r["id"] for r in rules if r["floor"]}
    kit_floors = {r["id"] for k in gov.load_kits(ROOT) for r in k["rules"] if r.get("floor")}
    assert floors == kit_floors and floors                       # every floor rule is in force with no lock at all
    on_any_path = set(json.loads(run(proj, "applicable", "--paths", "anything/at/all.txt", "--json")[1]))
    assert {r["id"] for r in rules if r["floor"] and r["applies_to"]["paths"] == ["**"]} <= on_any_path
    assert run(proj, "validate")[0] == 0 and run(proj, "check", "--today", "2026-11-04")[0] == 0
    policy = om.load(str(proj))
    assert policy.file_present is False and policy.sources
    assert set(policy.sources.values()) == {"default"}            # every tunable defaults, every floor holds
    assert policy.floors and all(policy.values[k] is not None for k in policy.floors)


# ------------------------------------------------------------- dogfood ---

def test_dogfood_the_plugin_tree_selects_claude_plugin_first(tmp_path):
    rc, out, _ = run(ROOT, "select", "--json")
    assert rc == 3
    doc = json.loads(out)
    assert doc["candidates"][0]["id"] == "claude-plugin" and doc["candidates"][0]["above_threshold"]


# ================================================================ review round 1 ===

def test_write_text_never_follows_a_planted_temp_symlink(tmp_path):
    proj, outside = tmp_path / "p", tmp_path / "outside.txt"
    proj.mkdir()
    outside.write_text("untouched")
    (proj / "x.yml.tmp").symlink_to(outside)
    assert gov._write_text(proj, "x.yml", "new: 1\n") is True
    assert outside.read_text() == "untouched" and (proj / "x.yml").read_text() == "new: 1\n"
    assert (proj / "x.yml.tmp").is_symlink()                              # the planted link is left alone
    assert gov._write_text(proj, "x.yml", "new: 1\n") is False            # control: identical content is a no-op


def _remote(tmp_path, monkeypatch, name="remote.git"):
    bare = tmp_path / name
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    for k, v in (("NAME", "t"), ("EMAIL", "t@example.invalid")):
        monkeypatch.setenv(f"GIT_AUTHOR_{k}", v)
        monkeypatch.setenv(f"GIT_COMMITTER_{k}", v)
    return bare


def test_a_failed_push_restores_the_branch_and_the_tree_and_a_rerun_retries(tmp_path, monkeypatch):
    log = gh_shim(tmp_path, monkeypatch)
    _remote(tmp_path, monkeypatch)
    proj = make_repo(tmp_path / "r")
    git(proj, "remote", "add", "origin", str(tmp_path / "does-not-exist.git"))
    intake(proj)
    before = snapshot(proj)
    rc, _, err = run(proj, "apply", "--pr", "--base", "main")
    assert rc == 2 and "git-push-failed" in err
    assert git(proj, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert git(proj, "status", "--porcelain", "--untracked-files=no").strip() == ""
    assert "governance/intake" not in git(proj, "branch", "--list")
    assert snapshot(proj) == before and not log.exists()                  # nothing left behind, gh never reached
    good = tmp_path / "good.git"
    subprocess.run(["git", "init", "-q", "--bare", str(good)], check=True)
    git(proj, "remote", "set-url", "origin", str(good))
    rc, out, err = run(proj, "apply", "--pr", "--base", "main")
    assert rc == 0 and "already current" not in out, err                 # the rerun retries


def test_pr_preflight_checks_gh_and_origin_before_writing_anything(tmp_path, monkeypatch):
    proj = make_repo(tmp_path / "r")
    intake(proj)
    before = snapshot(proj)
    rc, _, err = run(proj, "apply", "--pr")                                # no origin
    assert rc == 2 and "no-origin" in err and snapshot(proj) == before
    git(proj, "remote", "add", "origin", str(tmp_path / "x.git"))
    monkeypatch.setattr(gov.shutil, "which", lambda name: None)
    rc, _, err = run(proj, "apply", "--pr")
    assert rc == 2 and "gh-not-found" in err and snapshot(proj) == before
    assert git(proj, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"


def test_a_failed_gh_restores_the_original_branch(tmp_path, monkeypatch):
    bindir = tmp_path / "shim-bin"
    bindir.mkdir()
    (bindir / "gh").write_text("#!/bin/sh\necho 'boom https://u:hunter2@example.invalid/x' >&2\nexit 1\n")
    (bindir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    bare = _remote(tmp_path, monkeypatch)
    proj = make_repo(tmp_path / "r")
    git(proj, "remote", "add", "origin", str(bare))
    git(proj, "push", "-q", "origin", "main")
    intake(proj)
    rc, out, err = run(proj, "apply", "--pr", "--base", "main")
    assert rc == 2 and "hunter2" not in out + err and "***@" in err         # credentials in git/gh stderr are redacted
    assert git(proj, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert not (proj / ".foundry" / "governance.lock").exists()


def test_the_pr_is_opened_from_the_branch_and_head_is_restored(tmp_path, monkeypatch):
    log = gh_shim(tmp_path, monkeypatch)
    bare = _remote(tmp_path, monkeypatch)
    proj = make_repo(tmp_path / "r")
    git(proj, "remote", "add", "origin", str(bare))
    git(proj, "push", "-q", "origin", "main")
    intake(proj)
    assert run(proj, "apply", "--pr", "--base", "main")[0] == 0
    call = json.loads(log.read_text().splitlines()[0])
    assert call[call.index("--head") + 1] == "governance/intake"
    assert git(proj, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"


def test_pr_never_force_adds_an_adopters_gitignored_layer_but_does_add_the_lock(tmp_path, monkeypatch):
    gh_shim(tmp_path, monkeypatch)
    bare = _remote(tmp_path, monkeypatch)
    proj = make_repo(tmp_path / "r")
    (proj / ".gitignore").write_text(".foundry/*\ngovernance/local/\n")
    git(proj, "add", ".gitignore")
    git(proj, "commit", "-q", "-m", "ignore")
    git(proj, "remote", "add", "origin", str(bare))
    git(proj, "push", "-q", "origin", "main")
    intake(proj)
    assert run(proj, "apply", "--pr", "--base", "main")[0] == 0
    files = git(proj, "show", "--name-only", "--format=", "governance/intake").split()
    assert ".foundry/governance.lock" in files
    assert not [f for f in files if f.startswith("governance/local/")]


def test_a_second_run_while_the_pr_branch_exists_is_a_skip_not_a_failure(tmp_path, monkeypatch):
    gh_shim(tmp_path, monkeypatch)
    bare = _remote(tmp_path, monkeypatch)
    proj = make_repo(tmp_path / "r")
    git(proj, "remote", "add", "origin", str(bare))
    git(proj, "push", "-q", "origin", "main")
    intake(proj)
    assert run(proj, "apply", "--pr", "--base", "main")[0] == 0
    rc, out, _ = run(proj, "apply", "--pr", "--base", "main")
    assert rc == 0 and "PR open: governance/intake" in out


def test_discover_out_is_confined_to_the_runtime_directory(tmp_path):
    proj = make_repo(tmp_path / "r")
    (proj / ".claude").mkdir()
    (proj / ".claude" / "settings.json").write_text("{}")
    rc, _, err = run(proj, "discover", "--out", ".claude/settings.json")
    assert rc == 2 and "output-outside-runtime-dir" in err and (proj / ".claude" / "settings.json").read_text() == "{}"
    assert run(proj, "discover", "--out", ".foundry/facts.json")[0] == 0
    assert json.loads((proj / ".foundry" / "facts.json").read_text())["schema"] == 1


def test_discover_skips_symlinks_and_never_reads_a_huge_manifest(tmp_path):
    proj = tmp_path / "t"
    proj.mkdir()
    secret = tmp_path / "outside.json"
    secret.write_text(json.dumps({"dependencies": {"leaked-dep": "1"}}))
    (proj / "package.json").symlink_to(secret)
    facts = json.loads(run(proj, "discover")[1])
    assert "package.json" not in facts["paths"] and "leaked-dep" not in facts["dependencies"]
    (proj / "package.json").unlink()
    (proj / "package.json").write_text('{"dependencies": {"a": "1"}, "pad": "' + "x" * (gov.MAX_READ_BYTES + 10) + '"}')
    facts = json.loads(run(proj, "discover")[1])
    by = {d["name"]: d for d in facts["detectors"]}
    assert by["dependencies"]["status"] == "could-not-look" and facts["dependencies"] == []


def test_ecosystems_without_a_dependency_parser_report_could_not_look_not_not_found(tmp_path):
    for manifest in ("go.mod", "Cargo.toml", "Gemfile"):
        proj = tmp_path / manifest.replace(".", "_")
        proj.mkdir()
        (proj / manifest).write_text("")
        by = {d["name"]: d for d in json.loads(run(proj, "discover")[1])["detectors"]}
        assert by["dependencies"]["status"] == "could-not-look", manifest
        assert by["auth-libraries"]["status"] == "could-not-look", manifest


def test_the_lock_schema_pins_the_blueprint_id_and_check_refuses_a_traversal_id(tmp_path):
    proj = tmp_path / "p"
    (proj / ".foundry").mkdir(parents=True)
    sha = "a" * 64
    (proj / ".foundry" / "governance.lock").write_text(yaml.safe_dump(
        {"blueprint": {"id": "../../etc", "version": "1", "sha256": sha}, "kits": [],
         "declarations": {"asvs_level": 1, "data_classification": [], "trust_boundaries": [], "surfaces": []},
         "facts_sha256": sha, "intake_at": "d", "plugin_version": "1"}))
    rc, _, err = run(proj, "check")
    assert rc == 2 and "lock-malformed" in err
