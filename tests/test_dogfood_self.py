"""gv-19: the plugin governs itself. The lock is committed and valid, `check` is clean at the head, the upstream private layer
(`governance/local/`) is empty of adopter rules by construction, every frozen baseline carries a reason and a future
expiry, and the governance-trace job passes on a PR that cites ids and fails on one that does not (a control).

Assertions are on parsed structure (YAML/JSON fields, parsed findings), never on substrings of joined output.
"""
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

import test_governance_trace as T
from test_governance_trace import gh_path  # noqa: F401  (autouse fixture: a fake `gh` on PATH, nothing real is called)

ROOT = Path(__file__).resolve().parents[1]
GOV = ROOT / "scripts" / "foundry_governance.py"
LOCK = ROOT / ".foundry" / "governance.lock"
LOCAL = ROOT / "governance" / "local"


def _gov(*args):
    return subprocess.run([sys.executable, str(GOV), "--project-dir", str(ROOT), "--plugin-root", str(ROOT), *args],
                          capture_output=True, text=True, timeout=300)


def test_the_lock_exists_and_validates_against_its_schema():
    assert LOCK.is_file()
    doc = yaml.safe_load(LOCK.read_text(encoding="utf-8"))
    schema = json.loads((ROOT / "schema" / "governance-lock.schema.json").read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(doc)) == []
    assert doc["blueprint"]["id"] == "claude-plugin"


def test_check_exits_zero_at_the_head():
    p = _gov("check")
    assert p.returncode == 0, p.stdout + p.stderr
    # zero drift and zero expired exceptions are both reported as findings by `check`; none may be printed
    assert [ln for ln in p.stdout.splitlines() if ln.strip() and ln.strip() != "governance-check-ok"] == []


def test_local_layer_holds_scaffold_files_only():
    """upstream's private layer is empty by construction: nothing but the scaffold (and the machine-owned .kit-base)."""
    assert LOCAL.is_dir()
    allowed_exact = {"README.md", "exceptions.yaml", "baselines/.gitkeep", "adr/.gitkeep"}
    found = []
    for f in sorted(LOCAL.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(LOCAL)
        if rel.parts[0] == ".kit-base":
            continue
        found.append(rel.as_posix())
        ok = rel.as_posix() in allowed_exact or (rel.parts[0] == "baselines" and len(rel.parts) == 2 and rel.suffix == ".json")
        assert ok, f"unexpected file in governance/local/: {rel}"
    assert {"README.md", "exceptions.yaml"} <= set(found)
    assert yaml.safe_load((LOCAL / "exceptions.yaml").read_text(encoding="utf-8")) == {"exceptions": []}


def test_every_baseline_has_a_reason_and_a_future_expiry():
    today = datetime.date.today()
    files = sorted((LOCAL / "baselines").glob("*.json"))
    assert files, "the dogfood run baselined its known findings"
    for f in files:
        doc = json.loads(f.read_text(encoding="utf-8"))
        assert doc["check"] == f.stem
        assert doc["findings"]
        for item in doc["findings"]:
            assert isinstance(item, dict), f"{f.name}: a baseline entry must carry its reason, not a bare id"
            assert isinstance(item.get("reason"), str) and len(item["reason"].split()) >= 6, f"{f.name}: reason"
            expires = datetime.date.fromisoformat(item["expires"])
            assert expires > today, f"{f.name}: expired {expires}"


# ---- governance-trace as a control, run against the real tree (its real lock and rendered rules) ----

REAL_DESIGN = """## Design
- Components: hooks/foundry-example.py
- Reuses: the shared brief renderer
- Cites: FG-SEC-006, FG-QA-001

## Security
- FG-SEC-006

## Surfaces
- cli: n/a: a hook has no command line
"""


def _trace(tmp_path, **kw):
    p, doc = T.run(ROOT, tmp_path, **kw)
    return p, doc


def test_trace_passes_on_a_pr_that_cites_ids_against_the_real_tree(tmp_path):
    p, doc = _trace(tmp_path, ticket_body=REAL_DESIGN, author="carol", extra=("--agent-login", "agent-bot"))
    assert p.returncode == 0 and doc["ok"] is True, p.stdout + p.stderr
    findings = {f["check"]: f for f in doc["findings"]}
    assert findings["cited-ids-exist"]["ok"] is True, doc
    assert findings["design-present"]["ok"] is True, doc
    assert findings["rendered-fresh"]["ok"] is True, doc
    assert findings["exceptions-live"]["ok"] is True, doc


def test_trace_fails_on_a_fixture_pr_that_cites_no_design_or_an_unknown_id(tmp_path):
    p, doc = _trace(tmp_path, ticket_body="## Done means\n```\ntrue\n```\n", extra=("--agent-login", "agent-bot"))
    assert p.returncode == 1, p.stdout + p.stderr
    assert doc["ok"] is False
    assert {f["check"] for f in doc["findings"] if not f["ok"]} >= {"design-present"}

    p2, doc2 = _trace(tmp_path, ticket_body=REAL_DESIGN.replace("FG-QA-001", "FG-NOPE-999"), extra=("--agent-login", "agent-bot"))
    assert p2.returncode == 1
    bad = [f for f in doc2["findings"] if f["check"] == "cited-ids-exist" and not f["ok"]]
    assert bad and "FG-NOPE-999" in bad[0]["detail"]

    p3, doc3 = _trace(tmp_path, pr_body="no ticket here\n", ticket_body=REAL_DESIGN, extra=("--agent-login", "agent-bot"))
    assert p3.returncode == 1
    assert [f for f in doc3["findings"] if f["check"] == "ticket-named" and not f["ok"]]

# ===========================================================================================================
# dl-13: the delivery-2-0 programme proves itself offline. The atom corpus (ids, depends_on, paths) is embedded
# below, copied from the programme manifest. The planner places it, the fleet identity function separates two
# containers, and close-out runs on a fake programme against a stubbed GitHub. Deterministic and offline.
# ===========================================================================================================
sys.path.insert(0, str(ROOT / "scripts"))
import foundry_close_out as co  # noqa: E402
import foundry_execution_plan as ep  # noqa: E402
import foundry_fleet as fleet  # noqa: E402
import foundry_programme_issue as pi  # noqa: E402
import foundry_wave_plan as wp  # noqa: E402

# (id, depends_on, paths) for every atom of the delivery-2-0 manifest.
DELIVERY_2_0 = [
    ('dl-01-acceptance-section', [], ['scripts/foundry-ticket.py', 'scripts/foundry_brief.py', 'agents/pr-reviewer.md', 'tests/test_acceptance_section.py', 'docs/governance.md']),
    ('dl-02-ticket-reviewer', ['dl-01-acceptance-section'], ['agents/ticket-reviewer.md', 'skills/dispatch/SKILL.md', 'tests/test_ticket_reviewer.py']),
    ('dl-03-whole-ticket-digest', ['dl-01-acceptance-section'], ['scripts/foundry-ticket.py', 'scripts/foundry_ticket_store.py', 'tests/test_ticket_digest.py']),
    ('dl-04-execution-plan', [], ['scripts/foundry_execution_plan.py', 'scripts/foundry_wave_plan.py', 'skills/execution-plan/SKILL.md', 'schema/execution-plan.schema.json', 'tests/test_execution_plan.py', 'docs/how-to/execution-plan.md']),
    ('dl-05-wave-dispatch', ['dl-04-execution-plan', 'dl-02-ticket-reviewer'], ['workflows/wave-dispatch.js', 'skills/dispatch/SKILL.md', 'skills/command-deck/SKILL.md', 'skills/command-deck/tick-prompt.template.md', 'scripts/foundry_operating_model.py', 'tests/test_wave_dispatch.py']),
    ('dl-06-programme-issue', ['dl-04-execution-plan'], ['scripts/foundry_programme_issue.py', 'scripts/foundry_deck_report.py', 'skills/command-deck/SKILL.md', 'tests/test_programme_issue.py']),
    ('dl-07-provenance-and-learned', [], ['governance/kits/delivery/checks/governance-trace.py', 'governance/kits/delivery/templates/governance-trace.yml.tmpl', 'governance/kits/delivery/rules.yaml', 'skills/dispatch/SKILL.md', 'tests/test_governance_trace.py']),
    ('dl-08-review-delta-pass', ['dl-01-acceptance-section'], ['skills/merge-when-green/SKILL.md', 'skills/dispatch/SKILL.md', 'agents/pr-reviewer.md', 'agents/security-reviewer.md', 'docs/how-to/recover-from-a-failed-gate.md', 'tests/test_review_delta_pass.py']),
    ('dl-09-cut-release-cadence', [], ['scripts/foundry-cut-release.py', 'skills/cut-release/SKILL.md', 'tests/test_cut_release.py', 'docs/how-to/cut-a-release.md']),
    ('dl-10-learned-distill', ['dl-07-provenance-and-learned'], ['scripts/foundry_learned_distill.py', 'skills/upstream-submit/SKILL.md', 'tests/test_learned_distill.py']),
    ('dl-11-fleet-builders', ['dl-04-execution-plan', 'dl-05-wave-dispatch', 'dl-03-whole-ticket-digest'], ['scripts/foundry_fleet.py', 'scripts/foundry-ticket.py', 'scripts/foundry_ticket_store.py', 'skills/command-deck/SKILL.md', 'docs/how-to/fleet-builders.md', 'tests/test_fleet.py']),
    ('dl-12-close-out', ['dl-04-execution-plan', 'dl-06-programme-issue', 'dl-10-learned-distill'], ['skills/close-out/SKILL.md', 'scripts/foundry_close_out.py', 'tests/test_close_out.py', 'docs/how-to/close-out.md']),
    ('dl-13-dogfood', ['dl-01-acceptance-section', 'dl-02-ticket-reviewer', 'dl-03-whole-ticket-digest', 'dl-04-execution-plan', 'dl-05-wave-dispatch', 'dl-06-programme-issue', 'dl-07-provenance-and-learned', 'dl-08-review-delta-pass', 'dl-09-cut-release-cadence', 'dl-10-learned-distill', 'dl-11-fleet-builders', 'dl-12-close-out', 'dl-14-token-cost'], ['CHANGELOG.md', 'docs/governance.md', 'docs/how-to/', 'tests/test_dogfood_self.py']),
    ('dl-14-token-cost', [], ['scripts/deck-cost-census.py', 'scripts/foundry_operating_model.py', 'schema/operating-model.schema.json', 'cli/templates/operating-model.yaml.tmpl', 'scripts/foundry_deck_report.py', 'scripts/foundry_blocker_check.py', 'skills/command-deck/tick-prompt.template.md', 'tests/test_deck_cost.py', 'docs/operating-model-policy.md']),
]


def corpus(rows=DELIVERY_2_0):
    return {"project_id": "P", "atoms": [
        {"id": i, "node_id": f"N_{i}", "item_id": f"I_{i}", "blocked_by": list(deps), "paths": list(paths),
         "labels": [], "state": "open"} for i, deps, paths in rows]}


def wave_of(plan):
    return {a: w["n"] for w in plan["waves"] for b in w["batches"] for a in b["atoms"]}


def batch_wave_of(plan):
    return {b["id"]: w["n"] for w in plan["waves"] for b in w["batches"]}


def check_plan(plan, rows):
    """Return the list of violations: an atom not strictly after a blocker, or overlapping atoms in one wave."""
    wv = wave_of(plan)
    bad = []
    for i, deps, _ in rows:
        for d in deps:
            if not wv[i] > wv[d]:
                bad.append(("order", i, d))
    paths = {i: p for i, _, p in rows}
    ids = sorted(paths)
    for x in ids:
        for y in ids:
            if x < y and wv[x] == wv[y] and any(wp.paths_overlap(p, q) for p in paths[x] for q in paths[y]):
                bad.append(("overlap", x, y))
    return bad


# --- the planner plans the programme that built it ------------------------------------------------------------------

def test_planner_places_every_atom_after_its_blockers_and_apart_from_overlaps():
    plan = ep.build_plan("delivery-2-0", corpus())
    wv = wave_of(plan)
    assert sorted(wv) == sorted(i for i, _, _ in DELIVERY_2_0)          # every atom is placed, once
    assert check_plan(plan, DELIVERY_2_0) == []
    # the render round-trips and still validates against the schema
    again = yaml.safe_load(ep.render(plan))
    ep.validate(again)
    assert wave_of(again) == wv
    # dl-13 blocks on every other atom, so it sits in the last wave alone with respect to its blockers
    assert wv["dl-13-dogfood"] == max(wv.values())
    assert plan["critical_path"] and plan["critical_path"][-1] in batch_wave_of(plan)


def test_a_broken_fixture_ordering_fails_the_wave_assertion():
    """RED control: a plan placed from the right corpus, then read against an edge the planner did not see."""
    plan = ep.build_plan("delivery-2-0", corpus())
    # dl-13 is placed after dl-01; add the reverse edge (dl-01 after dl-13) and the order check must fire
    rows = [(i, list(d) + (["dl-13-dogfood"] if i == "dl-01-acceptance-section" else []), p) for i, d, p in DELIVERY_2_0]
    bad = check_plan(plan, rows)
    assert ("order", "dl-01-acceptance-section", "dl-13-dogfood") in bad


def test_a_cycle_in_the_fixture_is_refused():
    rows = [(i, list(d) + (["dl-13-dogfood"] if i == "dl-01-acceptance-section" else []), p) for i, d, p in DELIVERY_2_0]
    with pytest.raises(Exception) as ei:
        ep.build_plan("delivery-2-0", corpus(rows))
    assert "dl-01-acceptance-section" in str(ei.value) and "dl-13-dogfood" in str(ei.value)


def test_path_overlap_is_detected_when_a_plan_is_forced_into_one_wave():
    """RED control for the overlap half: dl-02 and dl-05 both write skills/dispatch/SKILL.md."""
    plan = ep.build_plan("delivery-2-0", corpus())
    wv = wave_of(plan)
    wv_forced = dict(wv, **{"dl-08-review-delta-pass": wv["dl-02-ticket-reviewer"]})
    forced = {"waves": [{"n": n, "batches": [{"id": f"b{n}", "atoms": sorted(a for a, w in wv_forced.items() if w == n)}]}
                        for n in sorted(set(wv_forced.values()))]}
    bad = check_plan(forced, DELIVERY_2_0)
    assert ("overlap", "dl-02-ticket-reviewer", "dl-08-review-delta-pass") in bad


# --- a second container is a different worker -----------------------------------------------------------------------

def _worker(tmp_path, container, session):
    mid = tmp_path / "machine-id"
    mid.write_text("0123456789abcdef" * 2 + "\n")
    cg = tmp_path / "cgroup"
    cg.write_text("0::/\n")                                   # no container id in the cgroup: the env value is used
    ops = tmp_path / "operators.json"
    ops.write_text(json.dumps({"operators": {"op_lukas": {"github": "lukasrepublic"}}}))
    env = {"CLAUDE_CODE_SESSION_ID": session, "FOUNDRY_OPERATOR": "op_lukas", "FOUNDRY_CONTAINER_ID": container}
    return fleet.worker_identity(env=env, operators_file=str(ops), machine_id_file=str(mid), cgroup_file=str(cg))


def test_two_containers_on_one_machine_give_two_well_formed_worker_ids(tmp_path):
    lead = _worker(tmp_path, "a" * 12 + "1" * 20, "sess-lead")
    second = _worker(tmp_path, "b" * 12 + "2" * 20, "sess-second")
    assert lead["machine12"] == second["machine12"] and lead["op_id"] == second["op_id"]
    assert lead["container12"] != second["container12"]
    assert lead["id"] != second["id"]
    for w, c in ((lead, "a" * 12), (second, "b" * 12)):
        op, rest = w["id"].split("@")
        machine, container, session = rest.split("/")
        assert (op, machine, container) == (w["op_id"], w["machine12"], c)
        assert len(machine) == 12 and len(container) == 12 and session == w["session"]
        assert fleet.trailers(w)[2] == f"Worker-Host: {machine}/{container}"
        assert fleet.pr_marker(w) == f"<!-- foundry-worker: {w['id']} -->"


def test_a_missing_container_part_is_refused_not_defaulted(tmp_path):
    mid = tmp_path / "machine-id"
    mid.write_text("0123456789abcdef" * 2)
    cg = tmp_path / "cgroup"
    cg.write_text("0::/\n")
    ops = tmp_path / "operators.json"
    ops.write_text(json.dumps({"operators": {"op_lukas": {"github": "lukasrepublic"}}}))
    env = {"CLAUDE_CODE_SESSION_ID": "sess-x", "FOUNDRY_OPERATOR": "op_lukas"}
    with pytest.raises(fleet.FleetError) as ei:
        fleet.worker_identity(env=env, operators_file=str(ops), machine_id_file=str(mid), cgroup_file=str(cg))
    assert ei.value.reason == "container-id-missing"


# --- close-out leaves a draft Release pinned to the integration head ------------------------------------------------

SHA = "c" * 40
REPO = "acme/code"


class StubGH:
    def __init__(self):
        self.writes = []
        self.releases = []
        self.lease = False

    def __call__(self, args, payload=None):
        method = "GET"
        if args[:1] == ["-X"]:
            method, args = args[1], args[2:]
        path = args[0]
        if args == ["graphql"]:
            method = "POST"
        if method != "GET":
            self.writes.append((method, path, payload))
        if path == f"repos/{REPO}/git/ref/heads/release/2.0.0":
            return {"object": {"sha": SHA}}
        if path == f"repos/{REPO}/git/refs" and method == "POST":
            self.lease = True
            return {}
        if path.startswith(f"repos/{REPO}/git/refs/foundry/claims/") and method == "DELETE":
            self.lease = False
            return {}
        if path.startswith(f"repos/{REPO}/issues/"):
            return {"state": "open"}
        if path.startswith(f"repos/{REPO}/rulesets"):
            return {"id": 11} if method == "POST" else []
        if path.startswith(f"repos/{REPO}/statuses/") and method == "POST":
            return {}
        if "/statuses?" in path:
            return [{"context": co.CHECK, "state": "success"}]
        if path == f"repos/{REPO}/milestones/7":
            return {"open_issues": 0}
        if path.startswith(f"repos/{REPO}/releases") and method == "GET":
            return list(self.releases)
        if path == f"repos/{REPO}/releases" and method == "POST":
            self.releases.append(dict(payload, id=99))
            return {}
        if args == ["graphql"]:
            if "createProjectV2StatusUpdate" in payload["query"]:
                return {"data": {"createProjectV2StatusUpdate": {"statusUpdate": {"id": "SU"}}}}
            return {"data": {"repositoryOwner": {"projectV2": {"id": "PVT"}}}}
        raise AssertionError(f"unstubbed gh call: {method} {path}")


class FixedClock:
    def left(self):
        return 60.0

    def sleep(self, s):
        pass

    def utcnow(self):
        import datetime
        return datetime.datetime(2026, 11, 5, 12, 0, 0, tzinfo=datetime.timezone.utc)


def test_close_out_leaves_a_draft_release_pinned_to_the_integration_head(tmp_path, monkeypatch, capsys):
    stub = StubGH()
    monkeypatch.setattr(pi, "gh_api", stub)
    rows = [("a-one", [], ["x/"]), ("a-two", ["a-one"], ["y/"])]
    plan = ep.build_plan("prog", corpus(rows))
    (tmp_path / "plan.yaml").write_text(ep.render(plan))
    (tmp_path / "states.json").write_text(json.dumps({"states": {"a-one": "merged", "a-two": "merged"}}))
    (tmp_path / "atoms.json").write_text(json.dumps({"atoms": [{"id": "a-one", "issue": 1}, {"id": "a-two", "issue": 2}]}))
    (tmp_path / "learned.json").write_text(json.dumps({"atoms": []}))
    rc = co.main(["run", "prog", "--repo", REPO, "--integration", "release/2.0.0", "--tag", "v2.0.0",
                  "--plan", str(tmp_path / "plan.yaml"), "--states", str(tmp_path / "states.json"),
                  "--atoms", str(tmp_path / "atoms.json"), "--milestone", "7", "--project", "acme/3",
                  "--learned-json", str(tmp_path / "learned.json")], FixedClock(), lambda a, t, c=None: 0)
    out, err = capsys.readouterr()
    assert rc == 0, err
    rep = json.loads(out)
    assert rep["sha"] == SHA and rep["acceptance"] == "success" and rep["release"] == "created"
    releases = [w for w in stub.writes if w[1] == f"repos/{REPO}/releases"]
    assert len(releases) == 1
    method, _, body = releases[0]
    assert method == "POST"
    assert body["draft"] is True and body["target_commitish"] == SHA and body["tag_name"] == "v2.0.0"
    # never publishes, never tags, drops the lease
    assert not any(isinstance(w[2], dict) and w[2].get("draft") is False for w in stub.writes)
    assert not any("git/tags" in w[1] or w[1].endswith("/tags") for w in stub.writes)
    assert stub.lease is False
