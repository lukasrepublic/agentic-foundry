"""gv-07: cost, observability, data, compliance and ux kits."""
import datetime
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import jsonschema
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import foundry_governance as gov  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "governance"
TOK = "c" + "tx"  # retired token, built from fragments so it never appears literally
PLANNED_REFS = {"scripts/foundry_blocker_check.py"}  # lands with gv-14
KITS = {"cost": "COST", "observability": "OBS", "data": "DATA", "compliance": "LIC", "ux": "UX"}
LOCK_SCHEMA = json.loads((ROOT / "schema" / "governance-lock.schema.json").read_text())


def kit_rules(name):
    return yaml.safe_load((ROOT / "governance" / "kits" / name / "rules.yaml").read_text())["rules"]


def run(*argv, project=None):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = gov.main(["--plugin-root", str(ROOT), "--project-dir", str(project or ROOT / "nonexistent"), *argv])
    return rc, out.getvalue(), err.getvalue()


def test_five_kits_validate_with_zero_findings():
    rc, out, _ = run("validate")
    assert rc == 0 and "governance-valid" in out
    kits = {k["dir"]: k for k in gov.load_kits(ROOT)}
    for name, prefix in KITS.items():
        assert name in kits
        assert gov.validate_all([kits[name]], {"rules": [], "overrides": [], "exceptions": []}) == []
        for r in kit_rules(name):
            assert r["id"].startswith(f"FG-{prefix}-") and r["category"] == name
            assert r["sources"] and all("2026-11-04" in s for s in r["sources"])
            assert r["check"]["ref"]


def test_kit_declared_files_exist():
    for name in KITS:
        d = ROOT / "governance" / "kits" / name
        k = yaml.safe_load((d / "kit.yaml").read_text())
        assert (d / "README.md").is_file()
        for rel in k["checks"] + k["templates"]:
            assert (d / rel).is_file(), f"{name}: {rel}"
        for r in kit_rules(name):
            ref = r["check"]["ref"]
            if ref in PLANNED_REFS:
                continue
            assert (ROOT / ref).exists(), f"{r['id']}: {ref}"


def test_cost_001_is_a_floor_bound_to_priced_ask_and_scale_to_zero_template_taints():
    c1 = {r["id"]: r for r in kit_rules("cost")}["FG-COST-001"]
    assert c1["floor"] is True and c1["exception"]["allowed"] is False
    assert c1["check"]["ref"] == "scripts/foundry_blocker_check.py"


def test_data_classification_enum_is_fixed():
    d = yaml.safe_load((ROOT / "governance/kits/data/templates/data-classification.yaml").read_text())
    assert d["classes"] == ["public", "internal", "confidential", "restricted"]
    assert set(d["handling"]) == set(d["classes"])
    for c in d["classes"]:
        assert set(d["handling"][c]) == {"storage", "transport", "access", "retention_max_days"}, c
        assert all(isinstance(d["handling"][c][k], str) and d["handling"][c][k] for k in ("storage", "transport", "access"))
    assert d["handling"]["confidential"]["access"] == "named roles, audited"
    assert d["handling"]["public"]["retention_max_days"] is None
    assert d["handling"]["restricted"]["retention_max_days"] == 365


def _kit_files(root=None):
    base = root or ROOT / "governance" / "kits"
    for name in KITS:
        for f in (base / name).rglob("*"):
            if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
                yield f


def _denylist():
    base = os.environ.get("CLAUDE_PROJECT_DIR")
    f = Path(base) / ".claude" / "foundry-leak-denylist.txt" if base else None
    if f and f.is_file():
        return [x.strip().lower() for x in f.read_text().splitlines() if x.strip() and not x.startswith("#")]
    return []


def scan(files, terms):
    """Return [(file name, term index)] for every hit. Never returns the term text itself."""
    hits = []
    for f in files:
        low = f.read_text().lower()
        for i, term in enumerate(terms):
            if term in low:
                hits.append((f.name, i))
        if re.search(r"\b" + TOK, low):
            hits.append((f.name, TOK))
    return hits


def test_no_framework_text_no_retired_token_no_denylisted_names():
    hits = scan(_kit_files(), _denylist())  # computed outside the assert: a failure prints indices only
    assert hits == [], f"denylist/retired-token hits (file, term index): {hits}"
    for f in _kit_files():
        low = f.read_text().lower()
        for banned in ("soc 2", "soc2", "iso 27001", "iso27001", "pci-dss", "hipaa"):
            assert banned not in low, f"{f.name}: {banned}"


def test_detector_fires_on_a_planted_name(tmp_path):  # planted-name control
    copy = tmp_path / "kits"
    shutil.copytree(ROOT / "governance" / "kits", copy, ignore=shutil.ignore_patterns("__pycache__"))
    assert scan(_kit_files(copy), ["planted-secret-name"]) == []
    target = copy / "cost" / "README.md"
    target.write_text(target.read_text() + "\nplanted-secret-name\n")
    assert scan(_kit_files(copy), ["planted-secret-name"]) == [("README.md", 0)]
    target.write_text(f"a {TOK} directory\n")
    assert scan(_kit_files(copy), []) == [("README.md", TOK)]


def test_no_private_design_labels():
    for f in _kit_files():
        s = f.read_text()
        assert "operating model" not in s.lower(), f.name
        assert not re.search(r"\(C[123]\)", s), f.name


def test_sources_are_public_prior_art():
    for name in KITS:
        for r in kit_rules(name):
            assert len(r["sources"]) == len(set(r["sources"]))
            assert not any("Foundry governance design" in s for s in r["sources"])


def test_retention_rule_is_declared_not_ci():
    r = {x["id"]: x for x in kit_rules("data")}["FG-DATA-002"]
    assert r["check"]["kind"] == "declared"


def test_scale_to_zero_templates():
    docs = [d for d in yaml.safe_load_all((ROOT / "governance/kits/cost/templates/scale-to-zero-nodeclass.yaml").read_text())]
    assert all(docs)  # no trailing empty document
    kinds = [(d["kind"], d["metadata"]["name"]) for d in docs]
    assert ("EC2NodeClass", "privileged-job") in kinds
    pools = {d["metadata"]["name"]: d for d in docs if d["kind"] == "NodePool"}
    assert {"privileged-job", "non-prod"} <= set(pools)
    assert pools["privileged-job"]["spec"]["template"]["spec"]["taints"] != pools["non-prod"]["spec"]["template"]["spec"]["taints"]


def test_kyverno_policy_and_cli_cases():
    d = ROOT / "governance/kits/cost/templates"
    pol = yaml.safe_load((d / "restrict-privileged-toleration.kyverno.yaml").read_text())
    assert pol["kind"] == "ClusterPolicy" and "validationFailureAction" not in pol["spec"]
    rules = {r["name"]: r for r in pol["spec"]["rules"]}
    assert set(rules) == {"only-in-signer-namespace", "only-the-signer-service-account",
                          "no-node-name-outside-signer-namespace"}
    assert rules["no-node-name-outside-signer-namespace"]["match"]["any"][0]["resources"]["operations"] == ["CREATE"]
    pol_text = (d / "restrict-privileged-toleration.kyverno.yaml").read_text()
    assert "fails OPEN" in pol_text and "(key || '')" in pol_text and "!(key || '')" in pol_text
    assert "nodeSelectorTerms || `[]`" in pol_text
    assert all(r["validate"]["failureAction"] == "Enforce" for r in rules.values())
    text = json.dumps(rules)
    # both bypass forms, nodeName and the selector/affinity paths are covered by the deny conditions
    assert "operator==" in text and "Exists" in text and "!(key || '')" in text
    assert "spec.nodeName" in text and "nodeSelector" in text and "nodeAffinity" in text
    assert "tolerations || `[]`" in text
    cases = yaml.safe_load((d / "kyverno-tests/kyverno-test.yaml").read_text())
    res = {r["resources"][0]: (r["rule"], r["result"]) for r in cases["results"]}
    pods = {x["metadata"]["name"]: x for x in yaml.safe_load_all((d / "kyverno-tests/resources.yaml").read_text())}
    assert set(res) == set(pods)
    for bypass in ("bypass-key-exists", "bypass-no-key-exists", "bypass-node-name", "bypass-affinity"):
        assert res[bypass][1] == "fail"
    assert res["signer-equal-form"] == ("only-the-signer-service-account", "pass")
    assert pods["bypass-no-key-exists"]["spec"]["tolerations"] == [{"operator": "Exists"}]
    assert pods["signer-equal-form"]["spec"]["tolerations"][0]["operator"] == "Equal"
    assert "NoExecute" in (d / "scale-to-zero-pattern.md").read_text()


@pytest.mark.skipif(not shutil.which("kyverno"), reason="kyverno CLI not on PATH: policy evaluation not run")
def test_kyverno_cli_evaluates_the_cases():
    r = subprocess.run(["kyverno", "test", str(ROOT / "governance/kits/cost/templates/kyverno-tests/")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]


def test_infracost_script_shape():
    s = (ROOT / "governance/kits/cost/checks/infracost-comment.sh").read_text()
    assert "pull_request_target" in s and "NEVER" in s and "Fork PRs" in s and s.count("--project-name cost-diff") == 2
    assert "git worktree add" in s and "mktemp -d" in s and "/tmp/infracost" not in s
    assert "always exit 0" not in s
    assert "--path . --format json" not in s
    if shutil.which("bash"):
        assert subprocess.run(["bash", "-n", str(ROOT / "governance/kits/cost/checks/infracost-comment.sh")]).returncode == 0


# ---- rego ------------------------------------------------------------------------------------- #

OPA = shutil.which("opa")


def opa_deny(rego_dir, query, tmp_path, doc):
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps(doc))
    r = subprocess.run([OPA, "eval", "-f", "json", "-d", str(rego_dir), "-i", str(inp), query],
                       capture_output=True, text=True, check=True)
    res = json.loads(r.stdout).get("result", [])
    return sorted(res[0]["expressions"][0]["value"]) if res else []


def opa_deny_raw(rego_dir, query, tmp_path, doc):
    inp = tmp_path / "in2.json"
    inp.write_text(json.dumps(doc))
    r = subprocess.run([OPA, "eval", "-f", "json", "-d", str(rego_dir), "-i", str(inp), query],
                       capture_output=True, text=True, check=True)
    res = json.loads(r.stdout).get("result", [])
    return res[0]["expressions"][0]["value"] if res else []


def rc(addr, typ, after, unknown=None, mode="managed", actions=("create",)):
    return {"address": addr, "type": typ, "mode": mode,
            "change": {"actions": list(actions), "after": after, "after_unknown": unknown or {}}}


FULL = {"owner": "a", "environment": "dev", "cost-center": "1"}


@pytest.mark.skipif(not OPA, reason="opa not installed")
def test_required_tags_rego(tmp_path):
    d = ROOT / "governance/kits/cost/checks"
    plan = {"resource_changes": [
        rc("aws_s3_bucket.untagged", "aws_s3_bucket", {"bucket": "b", "tags": {}}),
        rc("aws_s3_bucket.nulltags", "aws_s3_bucket", {"bucket": "b", "tags": None}),
        rc("aws_s3_bucket.partial", "aws_s3_bucket", {"tags": {"owner": "a"}}),
        rc("aws_s3_bucket.computed", "aws_s3_bucket", {"bucket": "b"}, unknown={"tags_all": True}),
        rc("aws_route.r", "aws_route", {"route_table_id": "x"}),
        rc("aws_s3_bucket.tagged", "aws_s3_bucket", {"tags": FULL}),
        rc("aws_s3_bucket.default_tags", "aws_s3_bucket", {"tags": {}, "tags_all": FULL}),
        rc("data.aws_s3_bucket.d", "aws_s3_bucket", {"tags": {}}, mode="data"),
        rc("aws_s3_bucket.emptyval", "aws_s3_bucket", {"tags": {"owner": "", "environment": "dev", "cost-center": "1"}}),
        rc("aws_s3_bucket.listtags", "aws_s3_bucket", {"tags": ["owner"]}),
        rc("aws_s3_bucket.stringtags", "aws_s3_bucket", {"tags": "owner"}),
        rc("aws_s3_bucket.gone", "aws_s3_bucket", None, actions=("delete",)),
    ]}
    msgs = opa_deny(d, "data.main.deny", tmp_path, plan)
    flagged = {m.split()[1] for m in msgs}
    assert flagged == {"aws_s3_bucket.untagged", "aws_s3_bucket.nulltags", "aws_s3_bucket.partial", "aws_s3_bucket.computed",
                       "aws_s3_bucket.emptyval", "aws_s3_bucket.listtags", "aws_s3_bucket.stringtags"}


@pytest.mark.skipif(not OPA, reason="opa not installed")
def test_owner_prefix_policy(tmp_path):
    d = ROOT / "governance/kits/observability/templates/telemetry-policies"
    bad = {"groups": [{"id": "g", "attributes": [{"id": "other.x", "type": "string"}, {"ref": "http.request.method"}]}]}
    ok = {"groups": [{"id": "g", "attributes": [{"id": "example.x", "type": "string"}, {"ref": "http.request.method"}]}]}
    got = opa_deny_raw(d, "data.before_resolution.deny", tmp_path, bad)
    assert len(got) == 1 and got[0]["group"] == "g" and got[0]["attr"] == "other.x"
    assert opa_deny(d, "data.before_resolution.deny", tmp_path, ok) == []  # control


def test_weaver_script_points_at_the_policy():
    s = (ROOT / "governance/kits/observability/checks/weaver-registry-check.sh").read_text()
    assert "telemetry-policies" in s
    assert (ROOT / "governance/kits/observability/templates/telemetry-policies/owner-prefix.rego").is_file()


def test_no_reference_to_missing_standing_versions_doc():
    for f in _kit_files():
        assert "docs/standing-versions.md" not in f.read_text(), f


# ---- license check ---------------------------------------------------------------------------- #

def _lic():
    spec = importlib.util.spec_from_file_location("lic", ROOT / "governance/kits/compliance/checks/license_policy_check.py")
    sys.dont_write_bytecode = True
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


POLICY = ROOT / "governance/kits/compliance/templates/license-policy.yaml"


def test_license_check_denies_unknown_license():
    lic = _lic()
    assert lic.main([str(POLICY), str(FIX / "packages-unknown.json")]) == 1
    bad = lic.evaluate(yaml.safe_load(POLICY.read_text()), json.loads((FIX / "packages-unknown.json").read_text()))
    assert len(bad) == 1 and "mystery-lib" in bad[0]


def test_license_check_passes_mit_and_dual_license_control():  # control
    assert _lic().main([str(POLICY), str(FIX / "packages-pass.json")]) == 0


def test_license_check_denies_empty_denied_and_honours_unexpired_exception():
    lic, pol = _lic(), yaml.safe_load(POLICY.read_text())
    assert lic.evaluate(pol, [{"name": "a", "license": ""}, {"name": "b", "license": "AGPL-3.0-only"}])
    pol["exceptions"] = [{"package": "b", "license": "AGPL-3.0-only", "reason": "r", "owner": "o", "expires": "2027-01-01"}]
    assert lic.evaluate(pol, [{"name": "b", "license": "AGPL-3.0-only"}], datetime.date(2026, 11, 4)) == []
    assert lic.evaluate(pol, [{"name": "b", "license": "AGPL-3.0-only"}], datetime.date(2027, 2, 1))


def test_license_precedence_and_parentheses_refused(tmp_path):
    lic, pol = _lic(), yaml.safe_load(POLICY.read_text())
    # AND binds tighter than OR
    assert lic.evaluate(pol, [{"name": "p", "license": "MIT OR GPL-3.0-only AND Apache-2.0"}]) == []
    assert lic.evaluate(pol, [{"name": "p", "license": "GPL-3.0-only AND MIT OR GPL-2.0-only"}])
    pk = tmp_path / "p.json"
    pk.write_text(json.dumps([{"name": "p", "license": "GPL-3.0-only AND (MIT OR Apache-2.0)"}]))
    assert lic.main([str(POLICY), str(pk)]) == 2  # refused, never silently allowed or denied by guess
    with pytest.raises(lic.Refused, match="unparsed-expression"):
        lic.evaluate(pol, [{"name": "p", "license": "GPL-3.0-only AND (MIT OR Apache-2.0)"}])


def test_license_with_exception_must_be_listed():
    lic, pol = _lic(), yaml.safe_load(POLICY.read_text())
    e = "GPL-2.0-only WITH Classpath-exception-2.0"
    assert lic.evaluate(pol, [{"name": "p", "license": e}])
    pol["allow"]["permissive"].append(e)
    assert lic.evaluate(pol, [{"name": "p", "license": e}]) == []


def test_license_cli_input_errors_exit_2(tmp_path):
    lic = _lic()
    empty, notlist, nullpol = tmp_path / "e.json", tmp_path / "n.json", tmp_path / "n.yaml"
    empty.write_text("[]")
    notlist.write_text('{"a": 1}')
    nullpol.write_text("")
    assert lic.main([str(POLICY), str(empty)]) == 2
    assert lic.main([str(POLICY), str(empty), "--allow-empty"]) == 0
    assert lic.main([str(POLICY), str(notlist)]) == 2
    assert lic.main([str(nullpol), str(FIX / "packages-pass.json")]) == 2
    assert lic.main([str(POLICY), str(FIX / "packages-pass.json"), "--today", "not-a-date"]) == 2
    assert lic.main([str(POLICY)]) == 2
    for bad in ('deny: "AGPL-3.0-only"\nallow: {a: [MIT]}\n', 'deny: []\nallow: {a: MIT}\n', 'deny: []\nallow: [MIT]\n'):
        bp = tmp_path / "bad.yaml"
        bp.write_text(bad)
        assert lic.main([str(bp), str(FIX / "packages-pass.json")]) == 2
    assert lic.main([str(POLICY), str(tmp_path / "missing.json")]) == 2


# ---- ux --------------------------------------------------------------------------------------- #

def test_ux_rules_carry_the_ui_surface_tag_data_shape():
    """Data shape only: render does not filter by surface yet (gv-15), so this does not test omission."""
    for r in kit_rules("ux"):
        assert r["applies_to"]["blueprints"] == ["ui"]
    for name in set(KITS) - {"ux"}:
        assert all(r["applies_to"]["blueprints"] == ["*"] for r in kit_rules(name))
    for lock in ("lock-ui.yaml", "lock-noui.yaml"):  # fixtures conform to the lock schema
        jsonschema.validate(yaml.safe_load((FIX / lock).read_text()), LOCK_SCHEMA)
    assert "SURFACE tag" in (ROOT / "governance/kits/ux/README.md").read_text()
    assert "SURFACE tag" in (ROOT / "governance/kits/ux/kit.yaml").read_text()


def test_ux_check_files():
    d = ROOT / "governance/kits/ux/checks"
    assert (d / "eslint-i18next.config.mjs").is_file() and not (d / "eslint-i18next.config.js").exists()
    axe = (d / "axe-e2e.spec.ts").read_text()
    for tag in ("wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"):
        assert f"'{tag}'" in axe
    assert "baseURL" in axe
    assert "startServerCommand" in json.loads((d / "lighthouserc.json").read_text())["ci"]["collect"]


def test_render_emits_ux_category_from_shipped_kits(tmp_path):
    rc, _, _ = run("render", project=tmp_path)
    assert rc == 0 and (tmp_path / "governance/rendered/ux.md").is_file()


def test_ux_readme_states_wcag_coverage_is_a_minority_and_a_practice():
    text = " ".join((ROOT / "governance/kits/ux/README.md").read_text().split())
    assert "Automated accessibility checks cover a minority of WCAG" in text
    assert "is a practice, not a control" in text
