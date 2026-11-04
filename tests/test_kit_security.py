"""security + product kits (gv-04): validate, level filter, applicable trigger, licensed-prose grep, planted-name control, floor primitives.

Every check has a failing case and a control.
"""
import copy
import io
import json
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import foundry_governance as gov  # noqa: E402

KITS = ROOT / "governance" / "kits"
SEC, PRD = KITS / "security", KITS / "product"
FIX = ROOT / "tests" / "fixtures" / "governance"
PHRASES = [l.strip() for l in (ROOT / "tests" / "fixtures" / "governance" / "licensed-phrases.txt").read_text().splitlines() if l.strip()]
# built from parts so this file itself carries no private name
NAMES = ["lukas" + "republic", "lsliw" + "ka", "Sli" + "wka", "agentic-" + "workspace", "agentic-" + "handbook", "digital-" + "distillery"]


def rules(kit):
    return yaml.safe_load((kit / "rules.yaml").read_text())["rules"]


def run(plugin, proj, *argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = gov.main(["--plugin-root", str(plugin), "--project-dir", str(proj), *argv])
    return rc, out.getvalue(), err.getvalue()


def plugin_with(tmp_path, kits_rules):
    """A scratch plugin root holding only the given kits ({name: rules})."""
    import shutil
    root = tmp_path / "plugin"
    for name, rs in kits_rules.items():
        d = root / "governance" / "kits" / name
        shutil.copytree(KITS / name, d)
        (d / "rules.yaml").write_text(yaml.safe_dump({"kit": name, "version": "1.0.0", "rules": rs}))
    (tmp_path / "proj").mkdir(exist_ok=True)
    return root, tmp_path / "proj"


# ---- validate: zero findings -------------------------------------------------------------------- #

def test_both_kits_validate_clean(tmp_path):
    root, proj = plugin_with(tmp_path, {"security": rules(SEC), "product": rules(PRD)})
    rc, out, err = run(root, proj, "validate")
    assert rc == 0 and "governance-valid" in out and err == ""


def test_validate_fails_on_a_rule_without_a_check_binding(tmp_path):
    rs = rules(SEC)
    bad = copy.deepcopy(rs)
    del bad[0]["check"]
    root, proj = plugin_with(tmp_path, {"security": bad})
    rc, _, err = run(root, proj, "validate")
    lines = [l for l in err.splitlines() if "FG-SEC-001" in l]
    assert rc == 2 and lines and all(l.startswith("refused: FG-SEC-001: check") for l in lines)


def test_every_rule_binds_a_check_or_states_declared_none(tmp_path):
    for r in rules(SEC) + rules(PRD):
        assert r["check"]["kind"] in ("hook", "ci", "lint", "review-lens", "scorecard", "declared", "none")
        if r["check"]["kind"] not in ("declared", "none"):
            assert r["check"].get("ref"), r["id"]
        assert r["sources"], r["id"]


def test_manifest_lists_exactly_the_rule_ids_and_shipped_files():
    for kit in (SEC, PRD):
        man = yaml.safe_load((kit / "kit.yaml").read_text())
        assert man["rules"] == [r["id"] for r in rules(kit)]
        for rel in man["checks"] + man["templates"]:
            assert (kit / rel).is_file(), rel
    assert {r["id"][:6] for r in rules(SEC)} == {"FG-SEC"} and {r["id"][:6] for r in rules(PRD)} == {"FG-PRD"}
    assert len(rules(SEC)) >= 30 and len(rules(PRD)) == 3


# ---- level: carried by every rule; the resolver does not filter on it yet (gv-15) ----------------- #

def rendered_levels(tmp_path):
    root, proj = plugin_with(tmp_path, {"security": rules(SEC)})
    rc, _, _ = run(root, proj, "render")
    assert rc == 0
    text = (proj / "governance" / "rendered" / "security.md").read_text()
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"## (FG-[A-Z]+-\d{3}) - ", line)
        if m:
            cur = m.group(1)
        m = re.match(r"- level (\d)", line)
        if m and cur:
            out[cur] = int(m.group(1))
    return out


def test_every_rule_carries_a_valid_level_and_render_emits_all_of_them(tmp_path):
    lv = rendered_levels(tmp_path)
    assert set(lv) == {r["id"] for r in rules(SEC)}  # render ignores level today: all rules, every level
    assert all(r["level"] in (1, 2, 3) for r in rules(SEC))
    assert {r["level"] for r in rules(SEC)} == {1, 2, 3}
    assert lv == {r["id"]: r["level"] for r in rules(SEC)}


def test_applicable_ignores_level(tmp_path):
    root, proj = plugin_with(tmp_path, {"security": rules(SEC)})
    _, out, _ = run(root, proj, "applicable", "--json", "--paths", "src/auth/x.py")
    ids = json.loads(out)
    assert "FG-SEC-032" in ids and {next(r for r in rules(SEC) if r["id"] == i)["level"] for i in ids} >= {1}


def test_level_control_a_bad_level_is_refused(tmp_path):
    bad = copy.deepcopy(rules(SEC))
    bad[3]["level"] = 4
    root, proj = plugin_with(tmp_path, {"security": bad})
    rc, _, err = run(root, proj, "validate")
    assert rc == 2 and "FG-SEC-004" in err and "level" in err


# ---- applicable: the review trigger ------------------------------------------------------------- #

def applicable(tmp_path, *paths):
    root, proj = plugin_with(tmp_path, {"security": rules(SEC), "product": rules(PRD)})
    rc, out, _ = run(root, proj, "applicable", "--json", "--paths", *paths)
    assert rc == 0
    return json.loads(out)


@pytest.mark.parametrize("p", ["src/auth/login.py", "services/payments/charge.py", ".github/workflows/ci.yml",
                               "lib/secrets/store.py", "package-lock.json"])
def test_boundary_path_returns_the_review_trigger(tmp_path, p):
    assert "FG-SEC-032" in applicable(tmp_path, p)


def test_review_trigger_absent_for_an_unrelated_path(tmp_path):
    assert "FG-SEC-032" not in applicable(tmp_path, "docs/guide.md", "src/render/view.py")


# ---- licensed prose ----------------------------------------------------------------------------- #

def kit_text_files():
    return [p for k in (SEC, PRD) for p in k.rglob("*") if p.is_file()]


def test_no_licensed_standard_phrasing_in_either_kit():
    for p in kit_text_files():
        t = " ".join(p.read_text().split())
        for ph in PHRASES:
            assert ph not in t, f"{p.name}: licensed phrase {ph!r}"


def test_licensed_grep_control_detects_a_planted_phrase(tmp_path):
    planted = tmp_path / "x.md"
    planted.write_text("Intro. The check " + PHRASES[0] + " form.")
    t = " ".join(planted.read_text().split())
    assert any(ph in t for ph in PHRASES)


# ---- planted-name control ----------------------------------------------------------------------- #

def test_kits_carry_no_private_names_and_no_banned_token():
    for p in kit_text_files() + [Path(__file__)]:
        t = p.read_text().lower()
        for n in NAMES:
            assert n.lower() not in t, f"{p.name}: {n}"
        assert ("c" + "tx") not in t, p.name


def test_name_control_detects_a_planted_name(tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text("sources: ['see " + NAMES[3] + " notes']")
    assert any(n.lower() in f.read_text().lower() for n in NAMES)


# ---- floor rules name their primitive ----------------------------------------------------------- #

FLOOR_HOOK = "hooks/foundry-secret-read-guard.py"  # shipped by the hook layer (gv-11)


def floor_problems(rs):
    bad = []
    for r in rs:
        if not r["floor"]:
            continue
        ref = r["check"].get("ref", "")
        if r["exception"]["allowed"] or not ref or not (ref == FLOOR_HOOK or (ROOT / ref).is_file()):
            bad.append(r["id"])
    return bad


def test_every_floor_rule_names_its_primitive():
    fl = [r for r in rules(SEC) + rules(PRD) if r["floor"]]
    assert {r["id"] for r in fl} == {"FG-SEC-026", "FG-SEC-032"}
    assert floor_problems(rules(SEC)) == []
    by = {r["id"]: r for r in fl}
    assert by["FG-SEC-026"]["check"] == {"kind": "hook", "ref": FLOOR_HOOK}
    assert by["FG-SEC-032"]["check"]["kind"] == "review-lens"


def test_floor_control_a_floor_rule_without_primitive_is_flagged():
    rs = copy.deepcopy(rules(SEC))
    next(r for r in rs if r["id"] == "FG-SEC-032")["check"] = {"kind": "review-lens", "ref": "agents/missing.md"}
    assert floor_problems(rs) == ["FG-SEC-032"]


# ---- checks name the rules they enforce --------------------------------------------------------- #

def test_each_check_file_names_existing_rule_ids():
    ids = {r["id"] for r in rules(SEC)}
    files = sorted(p for p in (SEC / "checks").rglob("*") if p.is_file())
    assert files
    for f in files:
        named = set(re.findall(r"FG-SEC-\d{3}", f.read_text()))
        assert named and named <= ids, f.name


def test_gitleaks_extends_default_and_scorecard_map_resolves():
    assert re.search(r"(?m)^\[extend\]\s*\nuseDefault = true", (SEC / "checks" / "gitleaks.toml").read_text())
    m = yaml.safe_load((SEC / "checks" / "scorecard-map.yaml").read_text())["mapping"]
    sc_refs = {r["check"]["ref"].split(":")[1]: r["id"] for r in rules(SEC) if r["check"]["kind"] == "scorecard"}
    assert {e["check"]: e["rule"] for e in m if e["check"] in sc_refs} == sc_refs
    assert {e["rule"] for e in m} <= {r["id"] for r in rules(SEC)}


def test_data_classification_enum_is_fixed():
    t = (SEC / "templates" / "THREAT-MODEL.md").read_text()
    assert "public, internal, confidential, restricted" in t


# ---- gv-04 round 1: runnable checks ------------------------------------------------------------- #

import shutil
import subprocess


def test_semgrep_rules_are_a_valid_config_with_metadata():
    cfg = yaml.safe_load((SEC / "checks" / "semgrep-rules.yaml").read_text())
    assert cfg["rules"]
    ids = {r["id"] for r in rules(SEC)}
    for r in cfg["rules"]:
        assert {"id", "message", "severity", "languages"} <= set(r) and any(k in r for k in ("pattern", "patterns"))
        assert r["metadata"]["cwe"] and r["metadata"]["owasp"] and r["metadata"]["rule"] in ids
        assert r["id"].startswith("fg-sec-")


def semgrep_rule_problems(cfg):
    ids = {r["id"] for r in rules(SEC)}
    bad = []
    for r in cfg["rules"]:
        md = r.get("metadata") or {}
        if not (md.get("cwe") and md.get("owasp") and md.get("rule") in ids):
            bad.append(r["id"])
    return bad


def test_semgrep_control_a_mutated_real_rule_fails_the_metadata_check():
    cfg = yaml.safe_load((SEC / "checks" / "semgrep-rules.yaml").read_text())
    assert semgrep_rule_problems(cfg) == []
    mutated = copy.deepcopy(cfg)
    del mutated["rules"][0]["metadata"]["cwe"]
    assert semgrep_rule_problems(mutated) == [cfg["rules"][0]["id"]]


def test_ci_snippets_are_pinned_and_semgrep_is_installed():
    t = (SEC / "checks" / "ci-semgrep.yml.tmpl").read_text()
    assert re.search(r"pip install semgrep==\d+\.\d+\.\d+ # pinned 2026-11-04", t)
    for f in (SEC / "checks").glob("ci-*.yml.tmpl"):
        for m in re.finditer(r"uses: (\S+)", f.read_text()):
            assert re.search(r"@[0-9a-f]{40}$", m.group(1)), (f.name, m.group(1))
    att = (SEC / "checks" / "ci-release-attest.yml.tmpl").read_text()
    assert "anchore/sbom-action@" in att and "actions/attest-build-provenance@" in att
    assert "GITHUB_TOKEN" in (SEC / "checks" / "ci-gitleaks.yml.tmpl").read_text()
    assert "actions/setup-python@" in t and t.index("actions/setup-python@") < t.index("pip install")
    assert "actions/attest-sbom@" in att


def run_lines(text):
    """Every shell line of a workflow snippet: the string values of `run` keys, parsed structurally."""
    out = []
    for job in yaml.safe_load(text)["jobs"].values():
        out += [s["run"] for s in job["steps"] if "run" in s]
    return out


def test_no_expression_reaches_a_shell_in_any_template():
    for f in (SEC / "checks").glob("ci-*.yml.tmpl"):
        for line in run_lines(f.read_text()):
            assert "${{" not in line, (f.name, line)


def test_expression_control_the_release_template_uses_env_indirection():
    t = (SEC / "checks" / "ci-release-attest.yml.tmpl").read_text()
    step = next(s for j in yaml.safe_load(t)["jobs"].values() for s in j["steps"] if s.get("name") == "upload")
    assert step["env"]["TAG"] == "${{ github.event.release.tag_name }}" and '"$TAG"' in step["run"]
    unsafe = t.replace('"$TAG"', '"${{ github.event.release.tag_name }}"')
    assert any("${{" in line for line in run_lines(unsafe))  # control: the mutated template is caught


def scorecard_snippet_facts():
    s = (SEC / "checks" / "ci-scorecard.yml.tmpl").read_text()
    run = next(x for j in yaml.safe_load(s)["jobs"].values() for x in j["steps"] if x.get("name") == "gate")["run"]
    names = json.loads(re.search(r"\[(\"[A-Za-z-]+\"(?:,\"[A-Za-z-]+\")*)\] \| all", run).group(0).rsplit(" | all", 1)[0])
    return s, run, names, int(re.search(r"--argjson t (\d+)", run).group(1))


def test_scorecard_gate_matches_the_map_threshold_names_and_must_rules():
    doc = yaml.safe_load((SEC / "checks" / "scorecard-map.yaml").read_text())
    by = {r["id"]: r for r in rules(SEC)}
    s, run, names, thr = scorecard_snippet_facts()
    assert thr == doc["threshold"]
    assert names == [e["check"] for e in doc["mapping"] if e["gate"]]
    for e in doc["mapping"]:
        if e["gate"]:
            assert " MUST " in by[e["rule"]]["statement"], e["rule"]
    perms = next(iter(yaml.safe_load(s)["jobs"].values()))["permissions"]
    assert perms == {"contents": "read", "actions": "read"}
    assert "repo_token" in s and "SCORECARD_TOKEN" in s


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")
@pytest.mark.parametrize("results,rc", [
    ("all", 0), ("missing", 1), ("inconclusive", 1), ("low", 1)])
def test_scorecard_gate_runs_on_synthetic_results(tmp_path, results, rc):
    _, run, names, thr = scorecard_snippet_facts()
    checks = [{"name": n, "score": 9} for n in names]
    if results == "missing":
        checks = checks[1:]
    elif results == "inconclusive":
        checks[0]["score"] = -1
    elif results == "low":
        checks[0]["score"] = thr - 1
    (tmp_path / "results.json").write_text(json.dumps({"checks": checks}))
    cmd = re.search(r"jq .*results\.json", run).group(0)
    r = subprocess.run(["bash", "-c", cmd], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == rc, r.stdout + r.stderr


def test_asvs_lists_are_ids_only_and_disjoint():
    seen = set()
    for n in (1, 2, 3):
        d = yaml.safe_load((SEC / "checks" / f"asvs-level-{n}.yaml").read_text())
        assert d["level"] == n and d["requirements"]
        for i in d["requirements"]:
            assert re.fullmatch(r"v5\.0\.0-\d+\.\d+\.\d+", i), i
        assert not seen & set(d["requirements"])
        seen |= set(d["requirements"])
    by = {r["id"]: r for r in rules(SEC)}
    for n, rid in ((1, "FG-SEC-003"), (2, "FG-SEC-004"), (3, "FG-SEC-005")):
        assert by[rid]["check"] == {"kind": "declared", "ref": f"governance/kits/security/checks/asvs-level-{n}.yaml"}
    assert by["FG-SEC-002"]["check"]["kind"] == "review-lens"


def test_review_trigger_covers_the_widened_globs(tmp_path):
    for p in (".github/actions/x/action.yml", ".github/CODEOWNERS", "svc/package.json", "svc/pyproject.toml", "svc/requirements-dev.txt",
              "svc/Dockerfile.prod", "api/oauth/cb.py", "api/webhooks/h.py"):
        d = tmp_path / str(abs(hash(p)))
        d.mkdir()
        assert "FG-SEC-032" in applicable(d, p), p


def test_no_empty_comment_only_step_and_no_invented_keys():
    c = yaml.safe_load((SEC / "checks" / "conftest-bundle.yaml").read_text())
    assert set(c) == {"policy_dir", "command", "optional_remote"} and c["optional_remote"] == "PLACEHOLDER"
    by = {r["id"]: r for r in rules(SEC)}
    assert by["FG-SEC-031"]["check"]["ref"].endswith("checks/policy") and (ROOT / by["FG-SEC-031"]["check"]["ref"]).is_dir()


def test_readme_has_the_planned_placeholder_section():
    t = (SEC / "README.md").read_text()
    for needle in ("Planned, placeholder and deviations", "foundry-secret-read-guard.py", ".foundry/governance.lock", "gv-15", "PLACEHOLDER", "Push protection"):
        assert needle in t, needle


REGO_CASES = [("deploy-bad.yaml", 1), ("deploy-good.yaml", 0), ("deploy-empty-sc.yaml", 1), ("deploy-init-bad.yaml", 1),
              ("replicaset-bad.yaml", 1), ("cronjob-bad.yaml", 1), ("pod-eph-bad.yaml", 1),
              ("iam-bad.json", 1), ("iam-good.json", 0), ("iam-object-bad.json", 1), ("iam-object-good.json", 0), ("iam-notaction-bad.json", 1)]


@pytest.mark.skipif(shutil.which("conftest") is None, reason="conftest not installed")
@pytest.mark.parametrize("fix,rc", REGO_CASES)
def test_rego_policy_denies_bad_and_passes_good(fix, rc):
    r = subprocess.run(["conftest", "test", "-p", str(SEC / "checks" / "policy"), str(FIX / fix)], capture_output=True, text=True)
    assert r.returncode == rc, r.stdout + r.stderr
    if rc:
        assert "FG-SEC-031" in r.stdout


def test_every_rego_fixture_exists():
    for fix, _ in REGO_CASES:
        assert (FIX / fix).is_file(), fix


def test_rego_policy_text_denies_the_three_cases():
    t = " ".join(p.read_text() for p in (SEC / "checks" / "policy").glob("*.rego"))
    for needle in ("@sha256:", "runAsNonRoot", "initContainers", "ephemeralContainers", "NotAction", "is_object(input.Statement)", "wildcard(s.Action)"):
        assert needle in t
