"""gv-15 upgrade and the upstream leak guard.

Every acceptance criterion has a failing-case test and a control. No real `gh` and no network.
"""
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import foundry_governance as gov  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "blueprints"
WF = ".github/workflows/ci-gitleaks.yml"
WF_KIT = "governance/kits/security/checks/ci-gitleaks.yml.tmpl"


def run(proj, *argv, plugin=ROOT):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = gov.main(["--plugin-root", str(plugin), "--project-dir", str(proj), *argv])
    return rc, out.getvalue(), err.getvalue()


def git(cwd, *argv):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid", "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["git", *argv], cwd=cwd, env=env, capture_output=True, text=True, check=True).stdout


def make_plugin_copy(tmp_path: Path) -> Path:
    plugin = tmp_path / "plugin"
    for d in ("governance", "packs", "schema"):
        shutil.copytree(ROOT / d, plugin / d)
    (plugin / ".claude-plugin").mkdir()
    shutil.copy(ROOT / ".claude-plugin" / "plugin.json", plugin / ".claude-plugin" / "plugin.json")
    return plugin


def adopted_repo(tmp_path: Path, plugin: Path) -> Path:
    """A committed fixture repository that has completed intake against `plugin`."""
    proj = tmp_path / "r"
    proj.mkdir()
    (proj / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.0.1"\ndependencies = ["typer"]\n')
    (proj / "uv.lock").write_text("")
    git(proj, "init", "-q", "-b", "main")
    git(proj, "add", "-A")
    git(proj, "commit", "-q", "-m", "init")
    assert run(proj, "select", "--confirm", "cli-tool", plugin=plugin)[0] == 0
    assert run(proj, "declare", "--asvs-level", "2", "--data-classification", "internal", plugin=plugin)[0] == 0
    assert run(proj, "apply", plugin=plugin)[0] == 0
    git(proj, "add", "-A", "-f")
    git(proj, "commit", "-q", "-m", "adopt governance")
    return proj


def advance_kit(plugin: Path, kit: str, new_version: str, template: str = None, edit=None):
    kdir = plugin / "governance" / "kits" / kit
    y = kdir / "kit.yaml"
    old = yaml.safe_load(y.read_text())["version"]
    y.write_text(y.read_text().replace(f"version: {old}", f"version: {new_version}", 1))
    if template:
        t = plugin / template
        t.write_text(edit(t.read_text()))


def gh_shim(tmp_path: Path, monkeypatch) -> Path:
    bindir, log = tmp_path / "shim-bin", tmp_path / "gh-argv.jsonl"
    bindir.mkdir()
    shim = bindir / "gh"
    shim.write_text("#!/usr/bin/env python3\nimport json, sys\n"
                    f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
                    "print('https://example.invalid/pull/2')\n")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return log


# ------------------------------------------------------------ AC-4: upgrade ---

def test_upgrade_preserves_a_non_overlapping_adopter_edit_and_takes_the_kit_change(tmp_path):
    plugin = make_plugin_copy(tmp_path)
    proj = adopted_repo(tmp_path, plugin)
    wf = proj / WF
    wf.write_text(wf.read_text() + "# adopter: our own trailing note\n")
    git(proj, "commit", "-q", "-am", "adopter edit")
    advance_kit(plugin, "security", "1.0.1", WF_KIT, lambda t: "# kit 1.0.1 header line\n" + t)
    rc, out, err = run(proj, "upgrade", plugin=plugin)
    assert rc == 0, (out, err)
    text = wf.read_text()
    assert "# adopter: our own trailing note" in text and text.startswith("# kit 1.0.1 header line")
    assert "<<<<<<<" not in text
    lock = yaml.safe_load((proj / ".foundry" / "governance.lock").read_text())
    assert {k["id"]: k["version"] for k in lock["kits"]}["security"] == "1.0.1"


def test_upgrade_never_writes_conflict_markers_into_a_ci_file_it_leaves_the_kit_version_beside_it(tmp_path):
    plugin = make_plugin_copy(tmp_path)
    proj = adopted_repo(tmp_path, plugin)
    wf = proj / WF
    wf.write_text(wf.read_text().replace("name: secret-scan", "name: our-secret-scan", 1))
    git(proj, "commit", "-q", "-am", "adopter edit")
    advance_kit(plugin, "security", "1.0.1", WF_KIT, lambda t: t.replace("name: secret-scan", "name: kit-renamed-scan", 1))
    rc, out, _ = run(proj, "upgrade", plugin=plugin)
    assert rc == 1 and "GOVERNANCE-UPGRADE-CONFLICTS" in out and f"{WF}.kit-new" in out
    assert "<<<<<<<" not in wf.read_text() and "our-secret-scan" in wf.read_text()        # the adopter file is untouched
    assert "kit-renamed-scan" in (proj / f"{WF}.kit-new").read_text()
    git(proj, "add", "-A")
    git(proj, "commit", "-q", "-m", "conflict state")
    rc, out, _ = run(proj, "upgrade", plugin=plugin)                                      # still reported until resolved
    assert rc == 1 and f"{WF}.kit-new" in out
    (proj / f"{WF}.kit-new").unlink()
    git(proj, "add", "-A")
    git(proj, "commit", "-q", "-m", "resolved")
    assert run(proj, "upgrade", plugin=plugin)[0] == 0


def _brownfield(tmp_path):
    """The adopter already had a workflow with the kit's name; `apply` keeps it."""
    plugin = make_plugin_copy(tmp_path)
    proj = tmp_path / "r"
    proj.mkdir()
    (proj / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.0.1"\ndependencies = ["typer"]\n')
    kit_text = (plugin / WF_KIT).read_text()
    mine = "# adopter header: our own conventions\n" + kit_text + "# adopter trailer\n"
    (proj / WF).parent.mkdir(parents=True)
    (proj / WF).write_text(mine)
    git(proj, "init", "-q", "-b", "main")
    git(proj, "add", "-A")
    git(proj, "commit", "-q", "-m", "init")
    assert run(proj, "select", "--confirm", "cli-tool", plugin=plugin)[0] == 0
    assert run(proj, "declare", "--asvs-level", "2", plugin=plugin)[0] == 0
    rc, out, _ = run(proj, "apply", plugin=plugin)
    assert rc == 0 and "kept" in out and (proj / WF).read_text() == mine
    git(proj, "add", "-A", "-f")
    git(proj, "commit", "-q", "-m", "adopt")
    return plugin, proj, mine


def test_brownfield_kept_file_is_not_a_whole_file_conflict_when_the_kit_is_unchanged(tmp_path):
    plugin, proj, mine = _brownfield(tmp_path)
    advance_kit(plugin, "security", "1.0.1")                       # version bump only: the snippet is unchanged
    rc, out, _ = run(proj, "upgrade", plugin=plugin)
    assert rc == 0 and (proj / WF).read_text() == mine and not (proj / f"{WF}.kit-new").exists()


def test_brownfield_kept_file_gets_a_true_three_way_merge_when_the_kit_changes(tmp_path):
    plugin, proj, mine = _brownfield(tmp_path)
    advance_kit(plugin, "security", "1.0.1", WF_KIT, lambda t: t + "# kit 1.0.1 trailer line\n")
    rc, out, _ = run(proj, "upgrade", plugin=plugin)
    text = (proj / WF).read_text()
    # the adopter's trailer and the kit's new line touch the same end-of-file hunk: that hunk, only, conflicts
    assert rc == 1 and f"{WF}.kit-new" in out and text == mine


def test_brownfield_kit_change_away_from_the_adopters_edits_merges_cleanly(tmp_path):
    plugin, proj, mine = _brownfield(tmp_path)
    advance_kit(plugin, "security", "1.0.1", WF_KIT, lambda t: t.replace("name: secret-scan", "name: secret-scan-v2", 1))
    rc, _, _ = run(proj, "upgrade", plugin=plugin)
    text = (proj / WF).read_text()
    assert rc == 0 and "secret-scan-v2" in text and text.startswith("# adopter header") and text.endswith("# adopter trailer\n")


def test_upgrade_with_an_untouched_file_takes_the_new_kit_version_cleanly(tmp_path):  # control
    plugin = make_plugin_copy(tmp_path)
    proj = adopted_repo(tmp_path, plugin)
    advance_kit(plugin, "security", "1.0.1", WF_KIT, lambda t: t + "# kit 1.0.1 trailer\n")
    rc, _, _ = run(proj, "upgrade", plugin=plugin)
    assert rc == 0 and (proj / WF).read_text().endswith("# kit 1.0.1 trailer\n")


def test_upgrade_leaves_a_file_the_adopter_deleted_deleted(tmp_path):
    plugin = make_plugin_copy(tmp_path)
    proj = adopted_repo(tmp_path, plugin)
    (proj / WF).unlink()
    git(proj, "commit", "-q", "-am", "adopter removes the workflow")
    advance_kit(plugin, "security", "1.0.1", WF_KIT, lambda t: t + "# kit 1.0.1 trailer\n")
    rc, out, _ = run(proj, "upgrade", plugin=plugin)
    assert rc == 0 and not (proj / WF).exists()


def test_upgrade_refuses_a_dirty_tree_and_changes_nothing(tmp_path):
    plugin = make_plugin_copy(tmp_path)
    proj = adopted_repo(tmp_path, plugin)
    advance_kit(plugin, "security", "1.0.1", WF_KIT, lambda t: t + "# kit 1.0.1 trailer\n")
    (proj / "pyproject.toml").write_text((proj / "pyproject.toml").read_text() + "# uncommitted\n")
    before = {p: p.read_bytes() for p in proj.rglob("*") if p.is_file() and ".git" not in p.parts}
    rc, _, err = run(proj, "upgrade", plugin=plugin)
    assert rc == 2 and "dirty-tree" in err
    assert before == {p: p.read_bytes() for p in proj.rglob("*") if p.is_file() and ".git" not in p.parts}


def test_upgrade_is_a_no_op_when_the_lock_matches_the_plugin(tmp_path):
    plugin = make_plugin_copy(tmp_path)
    proj = adopted_repo(tmp_path, plugin)
    rc, out, _ = run(proj, "upgrade", plugin=plugin)
    assert rc == 0 and "already current" in out
    assert git(proj, "status", "--porcelain").strip() == ""


def test_upgrade_without_a_governance_lock_is_refused(tmp_path):
    proj = tmp_path / "r"
    proj.mkdir()
    git(proj, "init", "-q", "-b", "main")
    rc, _, err = run(proj, "upgrade")
    assert rc == 2 and "no-governance-lock" in err


def test_upgrade_applies_a_kit_migration_id_rename_to_the_adopter_layer(tmp_path):
    plugin = make_plugin_copy(tmp_path)
    proj = adopted_repo(tmp_path, plugin)
    rules = yaml.safe_load((plugin / "governance" / "kits" / "security" / "rules.yaml").read_text())["rules"]
    target = next(r["id"] for r in rules if not r["floor"] and r["exception"]["allowed"])
    (proj / "governance" / "local" / "exceptions.yaml").write_text(yaml.safe_dump({"exceptions": [
        {"rule": "FG-SEC-900", "scope": "x/**", "reason": "r", "owner": "o", "expires": "2999-01-01"}]}))
    git(proj, "add", "-A", "-f")
    git(proj, "commit", "-q", "-m", "adopter exception on the old id")
    kit = plugin / "governance" / "kits" / "security" / "kit.yaml"
    doc = yaml.safe_load(kit.read_text())
    doc["version"] = "1.0.1"
    doc["migrations"] = [{"from_id": "FG-SEC-900", "to_id": target, "since": "1.0.1"}]
    kit.write_text(yaml.safe_dump(doc, sort_keys=False))
    rc, out, err = run(proj, "upgrade", plugin=plugin)
    assert rc == 0, (out, err)
    text = (proj / "governance" / "local" / "exceptions.yaml").read_text()
    assert "FG-SEC-900" not in text and target in text


def test_upgrade_opens_a_pull_request_on_a_branch(tmp_path, monkeypatch):
    log = gh_shim(tmp_path, monkeypatch)
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    plugin = make_plugin_copy(tmp_path)
    proj = adopted_repo(tmp_path, plugin)
    git(proj, "remote", "add", "origin", str(bare))
    git(proj, "push", "-q", "origin", "main")
    for k, v in (("NAME", "t"), ("EMAIL", "t@example.invalid")):
        monkeypatch.setenv(f"GIT_AUTHOR_{k}", v)
        monkeypatch.setenv(f"GIT_COMMITTER_{k}", v)
    advance_kit(plugin, "security", "1.0.1", WF_KIT, lambda t: t + "# kit 1.0.1 trailer\n")
    rc, out, err = run(proj, "upgrade", "--pr", "--base", "main", plugin=plugin)
    assert rc == 0, (out, err)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(calls) == 1 and calls[0][:2] == ["pr", "create"]
    assert "governance/upgrade" in git(bare, "branch", "--list")


# ------------------------------------------------------- AC-5: the leak guard ---

def test_leak_findings_refuse_a_private_rule_id_and_pass_an_fg_id():
    bad = gov.leak_findings({"docs/x.md": "see ACME-LCL-001 for the rule\n"})
    assert bad and bad[0].startswith("private-rule-id:docs/x.md:1")
    assert "ACME" not in " ".join(bad)                                  # the matched text is never echoed
    assert gov.leak_findings({"docs/x.md": "see FG-SEC-006 for the rule\n"}) == []   # control


def test_leak_findings_refuse_any_governance_local_path():
    bad = gov.leak_findings({"governance/local/rules.yaml": "rules: []\n"})
    assert bad == ["governance-local-path:governance/local/rules.yaml"]
    assert gov.leak_findings({"governance/kits/security/rules.yaml": "rules: []\n"}) == []  # control


def test_leak_findings_refuse_a_path_the_lock_marks_adopter_owned():
    lock = {"declarations": {"trust_boundaries": ["services/billing/**"]}}
    bad = gov.leak_findings({"services/billing/x.py": "pass\n", ".foundry/governance.lock": "x: 1\n"}, lock=lock)
    assert sorted(b.split(":")[0] for b in bad) == ["adopter-owned-path", "adopter-owned-path"]
    assert gov.leak_findings({"services/other/x.py": "pass\n"}, lock=lock) == []     # control


def test_leak_findings_refuse_product_literals_and_reuse_the_blueprint_deny_list():
    bad = gov.leak_findings({"a.md": "built for Zorblax Corp\n"}, literals=["Zorblax Corp"])
    assert bad and bad[0].startswith("product-literal:a.md") and "Zorblax" not in bad[0]
    bad = gov.leak_findings({"a.md": "tracked as HB"+"K-123\n"})              # foundry_blueprint's default domain-id shape
    assert bad and bad[0].startswith("product-id-pattern:a.md")
    assert gov.leak_findings({"a.md": "built for everyone\n"}, literals=["Zorblax Corp"]) == []


def test_an_unreadable_submission_file_is_a_finding_not_a_pass():
    assert gov.leak_findings({"a.md": None}) == ["unreadable:a.md"]


def test_leak_check_verb_refuses_and_passes(tmp_path):
    proj = tmp_path / "p"
    (proj / "docs").mkdir(parents=True)
    (proj / "docs" / "bad.md").write_text("ACME-LCL-001\n")
    (proj / "docs" / "ok.md").write_text("FG-SEC-006\n")
    assert run(proj, "leak-check", "--files", "docs/bad.md")[0] == 1
    assert run(proj, "leak-check", "--files", "docs/ok.md")[0] == 0
    assert run(proj, "leak-check", "--files", "../escape")[0] == 2


def _scan(tmp_path, *files, extra=()):
    script = ROOT / "scripts" / "foundry-prepublication-leak-scan.py"
    lits = tmp_path.parent / (tmp_path.name + "-literals.txt")
    lits.write_text("Zorblax\n")                                  # a literal source is mandatory: no source, no CLEAN
    return subprocess.run([sys.executable, str(script), "--governance-submission", "--root", str(tmp_path),
                           "--literals", str(lits), "--files", *files, *extra], capture_output=True, text=True)


def test_prepublication_leak_scan_refuses_a_planted_private_id_and_passes_an_fg_rule(tmp_path):
    (tmp_path / "bad.md").write_text("ACME-LCL-001\n")
    (tmp_path / "ok.md").write_text("FG-SEC-006\n")
    r = _scan(tmp_path, "bad.md")
    assert r.returncode == 1 and "GOVERNANCE-SUBMISSION-REFUSED" in r.stdout and "ACME" not in r.stdout
    r = _scan(tmp_path, "ok.md")
    assert r.returncode == 0 and "GOVERNANCE-SUBMISSION-CLEAN" in r.stdout


def test_prepublication_leak_scan_refuses_a_governance_local_path(tmp_path):
    (tmp_path / "governance" / "local").mkdir(parents=True)
    (tmp_path / "governance" / "local" / "rules.yaml").write_text("rules: []\n")
    r = _scan(tmp_path, "governance/local/rules.yaml")
    assert r.returncode == 1 and "governance-local-path" in r.stdout


def test_prepublication_leak_scan_reads_the_lock_for_adopter_owned_paths(tmp_path):
    (tmp_path / ".foundry").mkdir()
    sha = "a" * 64
    (tmp_path / ".foundry" / "governance.lock").write_text(yaml.safe_dump(
        {"blueprint": {"id": "x", "version": "1", "sha256": sha}, "kits": [],
         "declarations": {"asvs_level": 1, "data_classification": [], "trust_boundaries": ["priv/**"], "surfaces": []},
         "facts_sha256": sha, "intake_at": "d", "plugin_version": "1"}))
    (tmp_path / "priv").mkdir()
    (tmp_path / "priv" / "a.py").write_text("pass\n")
    assert _scan(tmp_path, "priv/a.py").returncode == 1


def test_upstream_submit_skill_runs_the_leak_guard_before_anything_leaves():
    text = (ROOT / "skills" / "upstream-submit" / "SKILL.md").read_text()
    assert text.startswith("---\nname: upstream-submit\n")
    for needle in ("leak-check", "--governance-submission", "governance/local/", "FG-", "refuse"):
        assert needle in text, needle


# ------------------------------------------ permission floor, hook, updater classes ---

def _hook():
    spec = importlib.util.spec_from_file_location("allow_hook", ROOT / "hooks" / "foundry-plugin-scripts-allow.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hook_allows_the_read_only_verbs_and_asks_for_the_writing_ones():
    hook = _hook()
    script = ROOT / "scripts" / "foundry_governance.py"
    root = str(ROOT)
    for verb in ("discover", "check", "leak-check", "validate"):
        assert hook.allowed(f"python3 {script} {verb}", root), verb
    for verb in ("select", "declare", "apply", "upgrade", "baseline", "render"):
        assert not hook.allowed(f"python3 {script} {verb}", root), verb


def test_both_permission_floor_files_stay_identical_and_the_governance_row_is_ask():
    a = (ROOT / "docs" / "permission-floor.json").read_bytes()
    assert a == (ROOT / "cli" / "permission-floor.json").read_bytes()
    rows = [e for e in json.loads(a)["entries"] if "foundry_governance.py" in e["rule"]]
    assert len(rows) == 1 and rows[0]["tier"] == "ask"
    for verb in ("discover", "select", "declare", "apply", "upgrade"):
        assert verb in rows[0]["rationale"], verb


def test_updater_never_overwrites_the_lock_or_the_adopter_layer():
    doc = json.loads((ROOT / "cli" / "path-classes.json").read_text())
    kept = doc["classes"]["retained-history"]
    assert ".foundry/governance.lock" in kept and "governance/local/**" in kept
    assert ".foundry/governance.lock" not in doc["classes"]["framework-owned"]


def test_docs_exist_and_are_linked_from_the_how_to_set():
    doc = ROOT / "docs" / "how-to" / "governance-intake-and-upgrade.md"
    text = doc.read_text()
    for needle in ("discover", "select", "declare", "apply", "baseline", "upgrade", "/foundry:post-upgrade",
                   "--confirm", "governance/local", "never"):
        assert needle in text, needle


# ================================================================ review round 1 ===

def test_migrations_do_not_follow_a_symlink_out_of_the_repository(tmp_path):
    plugin = make_plugin_copy(tmp_path)
    proj = adopted_repo(tmp_path, plugin)
    rules = yaml.safe_load((plugin / "governance" / "kits" / "security" / "rules.yaml").read_text())["rules"]
    target = next(r["id"] for r in rules if not r["floor"] and r["exception"]["allowed"])
    outside = tmp_path / "outside.yaml"
    outside.write_text("exceptions:\n- rule: FG-SEC-900\n")
    (proj / "governance" / "local" / "linked.yaml").symlink_to(outside)
    git(proj, "add", "-A", "-f")
    git(proj, "commit", "-q", "-m", "symlink")
    kit = plugin / "governance" / "kits" / "security" / "kit.yaml"
    doc = yaml.safe_load(kit.read_text())
    doc["version"] = "1.0.1"
    doc["migrations"] = [{"from_id": "FG-SEC-900", "to_id": target, "since": "1.0.1"}]
    kit.write_text(yaml.safe_dump(doc, sort_keys=False))
    (proj / "governance" / "local" / "exceptions.yaml").write_text("exceptions:\n- rule: FG-SEC-900\n")
    git(proj, "commit", "-q", "-am", "old id")
    assert run(proj, "upgrade", plugin=plugin)[0] == 0
    assert outside.read_text() == "exceptions:\n- rule: FG-SEC-900\n"                  # untouched through the link
    assert target in (proj / "governance" / "local" / "exceptions.yaml").read_text()   # control: a real file is migrated


def test_a_refused_upgrade_leaves_no_rename_behind(tmp_path):
    plugin = make_plugin_copy(tmp_path)
    proj = adopted_repo(tmp_path, plugin)
    rules = yaml.safe_load((plugin / "governance" / "kits" / "security" / "rules.yaml").read_text())["rules"]
    target = next(r["id"] for r in rules if not r["floor"] and r["exception"]["allowed"])
    kit = plugin / "governance" / "kits" / "security" / "kit.yaml"
    doc = yaml.safe_load(kit.read_text())
    doc["version"] = "1.0.1"
    doc["migrations"] = [{"from_id": "FG-SEC-900", "to_id": target, "since": "1.0.1"}]
    kit.write_text(yaml.safe_dump(doc, sort_keys=False))
    ex = proj / "governance" / "local" / "exceptions.yaml"
    ex.write_text("exceptions:\n- rule: FG-SEC-900\n")
    git(proj, "commit", "-q", "-am", "old id")
    shutil.rmtree(plugin / "governance" / "kits" / "delivery")           # the blueprint now names a kit the plugin lacks
    rc, _, err = run(proj, "upgrade", plugin=plugin)
    assert rc == 2 and "blueprint-kit-missing" in err
    assert ex.read_text() == "exceptions:\n- rule: FG-SEC-900\n"
    assert git(proj, "status", "--porcelain", "--untracked-files=no").strip() == ""


def test_a_plugin_version_bump_alone_is_not_a_change(tmp_path):
    plugin = make_plugin_copy(tmp_path)
    proj = adopted_repo(tmp_path, plugin)
    pj = plugin / ".claude-plugin" / "plugin.json"
    doc = json.loads(pj.read_text())
    doc["version"] = "9.9.9"
    pj.write_text(json.dumps(doc))
    rc, out, _ = run(proj, "upgrade", plugin=plugin)
    assert rc == 0 and "already current" in out and git(proj, "status", "--porcelain").strip() == ""


def test_the_updater_pr_branch_open_is_reported_as_a_skip(tmp_path, monkeypatch):
    log = gh_shim(tmp_path, monkeypatch)
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    plugin = make_plugin_copy(tmp_path)
    proj = adopted_repo(tmp_path, plugin)
    git(proj, "remote", "add", "origin", str(bare))
    git(proj, "push", "-q", "origin", "main")
    for k, v in (("NAME", "t"), ("EMAIL", "t@example.invalid")):
        monkeypatch.setenv(f"GIT_AUTHOR_{k}", v)
        monkeypatch.setenv(f"GIT_COMMITTER_{k}", v)
    advance_kit(plugin, "security", "1.0.1", WF_KIT, lambda t: t + "# kit 1.0.1 trailer\n")
    assert run(proj, "upgrade", "--pr", "--base", "main", plugin=plugin)[0] == 0
    rc, out, _ = run(proj, "upgrade", "--pr", "--base", "main", plugin=plugin)
    assert rc == 0 and "upgrade PR open: governance/upgrade-" in out
    assert len(log.read_text().splitlines()) == 1


# ---- leak guard hardening ----

def test_leak_guard_catches_normalised_embedded_and_cased_private_ids():
    for text in ("see-ACME-SEC-001", "FG-ACME-SEC-001", "acme-sec-001", "\uff21\uff23\uff2d\uff25-SEC-001",
                 "AC\u200bME-SEC-001", "ACME\u2011SEC-001".replace("\u2011", "-")):
        assert gov.leak_findings({"a.md": text + "\n"}), text
    assert gov.leak_findings({"a.md": "FG-SEC-006 and fg-sec-006\n"}) == []            # control: upstream ids pass


def test_leak_guard_compares_governance_local_casefolded():
    assert gov.leak_findings({"Governance/LOCAL/x.yaml": "a: 1\n"})
    assert gov.leak_findings({"governance/loc\u200bal/x.yaml": "a: 1\n"})


def test_the_submission_scan_resolves_the_denylist_and_refuses_without_a_literal_source(tmp_path):
    script = ROOT / "scripts" / "foundry-prepublication-leak-scan.py"
    (tmp_path / "a.md").write_text("shipped for Zorblax customers\n")
    deny = tmp_path / "deny.txt"
    deny.write_text("# private\nZorblax\n")
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    base = [sys.executable, str(script), "--governance-submission", "--root", str(tmp_path), "--files", "a.md"]
    r = subprocess.run(base + ["--denylist", str(deny)], capture_output=True, text=True, env=env)
    assert r.returncode == 1 and "product-literal:a.md" in r.stdout and "Zorblax" not in r.stdout
    r = subprocess.run(base, capture_output=True, text=True, env=env)
    assert r.returncode == 2 and "GOVERNANCE-SUBMISSION-REFUSED" in r.stdout and "GOVERNANCE-SUBMISSION-CLEAN" not in r.stdout
    (tmp_path / "b.md").write_text("fine\n")
    ok = [sys.executable, str(script), "--governance-submission", "--root", str(tmp_path), "--files", "b.md",
          "--denylist", str(deny)]
    assert subprocess.run(ok, capture_output=True, text=True, env=env).returncode == 0


def test_hook_asks_when_discover_names_an_output_file():
    hook = _hook()
    script = ROOT / "scripts" / "foundry_governance.py"
    assert hook.allowed(f"python3 {script} discover --summary", str(ROOT))
    assert not hook.allowed(f"python3 {script} discover --out .foundry/f.json", str(ROOT))
    assert not hook.allowed(f"python3 {script} discover --out=.claude/settings.json", str(ROOT))


def test_the_runtime_gitignore_reincludes_the_governance_lock():
    for rel in ("scripts/foundry-runtime.gitignore", "cli/templates/gitignore.tmpl"):
        assert "!/.foundry/governance.lock" in (ROOT / rel).read_text(), rel


def test_init_step_9a_applies_locally_and_opens_a_pr_only_when_origin_and_gh_exist():
    text = (ROOT / "skills" / "init" / "SKILL.md").read_text()
    block = text[text.index("Step 9a."):text.index("10. **GitHub scope")]
    assert "git remote get-url origin" in block and "command -v gh" in block and "apply --pr" in block
    assert "foundry_governance.py\" apply  " in block
