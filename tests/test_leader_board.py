"""gn-15: /foundry:leader-board. Pure calculator over synthetic inputs; no network, no clock."""
import copy
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import foundry_leader_board as lb  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "gn_leader_board" / "worked_example.json"
CUT = "2026-11-03T12:00:00Z"
P = "example-org/planning#"


def inputs():
    return copy.deepcopy(json.loads(FIX.read_text()))


def run(inp=None, **kw):
    return lb.compute(inp if inp is not None else inputs(), kw.pop("as_of", CUT), kw.pop("filters", None),
                      kw.pop("sort", None))


def w(rep, name="1w"):
    return rep["windows"][name]


# ---- doc 03 worked example ------------------------------------------------------------------------------

def test_worked_example_numbers():
    t = w(run())["team"]
    assert t["tokens"]["total"] == 840_000
    coh = t["outcome_tokens"]["complete_coverage"]
    assert coh["total"] == 300_000 and coh["mean"] == 150_000 and coh["n"] == 2
    assert sorted(coh["ids"]) == [P + "1", P + "2"]
    # outcome C and overhead are visible in period consumption, not charged to A or B
    assert t["tokens"]["by_bucket"] == {"outcome": 740_000, "lead-overhead": 100_000, "unallocated": 0}
    assert t["tokens"]["by_outcome"][P + "3"] == 500_000
    # never period tokens / period completions
    assert coh["mean"] != 840_000 / 2


def test_handoff_keeps_per_operator_usage():
    rep = run()
    a = next(o for o in w(rep)["team"]["outcome_tokens"]["complete_coverage"]["outcomes"] if o["id"] == P + "1")
    assert a["lifetime_tokens"] == 100_000
    assert a["by_operator"] == {"op-alice": 60_000, "op-bob": 40_000}
    item = next(i for i in w(rep)["team"]["throughput"]["items"] if i["id"] == P + "1")
    assert item["handoff"] and item["previous_owners"] == ["alice"] and item["op"] == "op-bob"
    # alice's own in-window consumption does not include the handoff outcome's later usage
    assert w(rep)["operators"]["op-alice"]["tokens"]["total"] == 0
    assert w(rep)["operators"]["op-bob"]["tokens"]["total"] == 40_000 + 200_000
    assert w(rep, "1m")["operators"]["op-alice"]["tokens"]["total"] == 60_000


def test_reaccept_in_later_window_earns_no_throughput():
    rep = run()
    assert P + "4" not in w(rep)["team"]["throughput"]["ids"]
    q = w(rep)["team"]["quality_follow_up"]
    assert q["reopenings"]["ids"] == [P + "4"] and q["reacceptances"]["ids"] == [P + "4"]
    # first acceptance (10-10) is in the 1m window, once
    assert w(rep, "1m")["team"]["throughput"]["ids"].count(P + "4") == 1
    assert P + "4" in w(rep, "1m")["team"]["outcome_tokens"]["no_usage_unavailable"]


def test_missing_ready_is_unavailable_not_zero():
    t = w(run())["team"]
    rl = t["ready_lead_time"]
    assert rl["status"] == "partial" and rl["n"] == 1 and rl["missing_ids"] == [P + "2"]
    inp = inputs()
    inp["issues"] = [i for i in inp["issues"] if i["ref"] != P + "1"]
    rl = w(run(inp))["team"]["ready_lead_time"]
    assert rl["status"] == "unavailable" and rl["median"] is None and "Ready" in rl["reason"]
    assert "n=0" in lb.render_text(run(inp))


def test_promoted_request_pre_blocking_ack_excluded():
    t = w(run())["team"]
    ack = t["blocker_ack_time"]
    assert ack["n"] == 1 and ack["median"] == 4 * 3600          # the 10-28T14:00 ack, 4h after blocking
    assert t["blocker_resolution_time"]["median"] == 24 * 3600
    assert t["blockers_resolved"]["count"] == 1


def test_rescheduled_request_stays_in_original_target_cohort():
    r = w(run())["team"]["scheduled_request_reliability"]
    assert r["due_cohort"] == 2 and r["on_time"] == 1 and r["rate"] == 0.5
    assert P + "11" in r["late_or_open_ids"] and P + "10" in r["rescheduled_ids"]
    # the 11-05 current target is NOT in the window: the cohort follows the original (10-29)
    assert w(run(), "1d")["team"]["scheduled_request_reliability"]["status"] == "unavailable"


def test_queue_panel_includes_items_older_than_the_window():
    pn = run()["queue_panel"]
    assert [x["request"] for x in pn["unacknowledged_blockers"]] == [P + "13"]
    assert pn["oldest_blocker"]["age"] > 30 * 86400
    assert [x["id"] for x in pn["overdue_requests"]] == [P + "13"]
    assert P + "11" in [x["id"] for x in pn["requests_never_responded"]]
    assert [x["id"] for x in pn["active_work"]] == [P + "3"]


# ---- determinism / cutoff -------------------------------------------------------------------------------

def test_identical_runs_identical_bytes(tmp_path):
    f = tmp_path / "in.json"
    f.write_text(json.dumps(inputs()))
    outs = []
    for _ in range(2):
        buf = io.StringIO()
        assert lb.main(["--inputs", str(f), "--as-of", CUT, "--json"], out=buf) == 0
        outs.append(buf.getvalue())
    assert outs[0] == outs[1]
    texts = []
    for _ in range(2):
        buf = io.StringIO()
        lb.main(["--inputs", str(f), "--as-of", CUT], out=buf)
        texts.append(buf.getvalue())
    assert texts[0] == texts[1] and "collection complete: yes" in texts[0]


def test_cutoff_ignores_later_records():
    rep = run(as_of="2026-10-31T09:00:00Z")
    assert rep["windows"]["1w"]["team"]["throughput"]["ids"] == []      # A accepted 10-31T10:00, after cutoff
    assert rep["windows"]["1w"]["team"]["tokens"]["total"] == 40_000 + 150_000   # u5 sits AT the cutoff: excluded
    # window membership is [start, end): a record exactly at the cutoff is outside every window
    inp = inputs()
    inp["usage"].append(copy.deepcopy(inp["usage"][0]) | {"request_id": "edge", "at": CUT})
    assert w(run(inp))["team"]["tokens"]["total"] == 840_000


def test_duplicate_usage_is_not_double_counted():
    inp = inputs()
    inp["usage"] += copy.deepcopy(inp["usage"])
    assert w(run(inp))["team"]["tokens"]["total"] == 840_000


# ---- statistics -----------------------------------------------------------------------------------------

def test_nearest_rank_and_small_sample():
    assert lb.nearest_rank(list(range(1, 11))) == 9
    assert lb.nearest_rank(list(range(1, 21))) == 18
    assert lb.nearest_rank([5]) == 5
    d = lb.dist([{"id": str(i), "value": i} for i in (1, 2, 3, 4)])
    assert d["label"] == "small sample" and d["n"] == 4
    d = lb.dist([{"id": str(i), "value": i} for i in range(1, 6)])
    assert d["label"] is None and d["p90"] == 5 and d["median"] == 3


def test_team_percentile_is_pooled_not_average_of_medians():
    obs = [{"id": f"a{i}", "value": v} for i, v in enumerate((1, 2, 3))] + \
          [{"id": f"b{i}", "value": v} for i, v in enumerate((100, 200, 300, 400, 500, 600, 700))]
    pooled = lb.dist(obs)["median"]
    assert pooled == 250
    assert (lb.dist(obs[:3])["median"] + lb.dist(obs[3:])["median"]) / 2 != pooled


def test_partial_coverage_cohort_is_separate():
    inp = inputs()
    inp["usage_coverage"]["earliest"] = "2026-10-24T00:00:00Z"      # collection began after #1 started (10-22)
    coh = w(run(inp))["team"]["outcome_tokens"]
    assert coh["partial_coverage"]["ids"] == [P + "1"] and coh["complete_coverage"]["ids"] == [P + "2"]
    assert coh["complete_coverage"]["mean"] == 200_000
    assert "not comparable" in coh["partial_coverage"]["note"]


def test_no_usage_dataset_is_unavailable_but_report_renders():
    inp = inputs()
    inp["usage"] = None
    inp["usage_coverage"] = None
    rep = run(inp)
    assert w(rep)["team"]["tokens"]["status"] == "unavailable"
    assert rep["header"]["collection_complete"] is False
    assert w(rep)["team"]["throughput"]["count"] == 2


# ---- filters / sort / structure -------------------------------------------------------------------------

def test_filters_and_sort():
    rep = run(filters={"operator": "bob"})
    assert rep["header"]["operators_in_scope"] == ["op-bob"]
    assert w(rep)["team"]["throughput"]["count"] == 2
    assert run(filters={"work_type": "fix"})["windows"]["1m"]["team"]["throughput"]["ids"] == [P + "4"]
    assert w(run(filters={"project": "Nope"}))["team"]["throughput"]["count"] == 0
    assert w(run(filters={"work_type": "feature"}))["team"]["tokens"]["total"] == 740_000
    rep = run(sort="tokens@1w")
    assert rep["operator_order"][0] == "op-carol" and rep["sort"]["note"].startswith("descriptive")
    try:
        run(sort="bogus")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_every_operator_listed_with_trend_and_no_score():
    rep = run()
    assert set(rep["header"]["operators_in_scope"]) >= {"op-alice", "op-bob", "op-carol"}
    assert "throughput" in w(rep)["trend"]["op-bob"]
    def keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield k
                yield from keys(v)
        elif isinstance(o, list):
            for v in o:
                yield from keys(v)
    assert [k for k in keys(rep) if "score" in str(k).lower()] == ["no_composite_score"]
    assert rep["header"]["no_composite_score"] is True
    assert all(k in rep["header"] for k in ("cutoff", "timezone", "freshness", "collection_complete"))


def test_parent_rolls_up_children_without_adding_count():
    inp = inputs()
    inp["issues"][0]["children"] = [P + "2"]
    inp["issues"][1]["parent"] = P + "1"
    t = w(run(inp))["team"]["throughput"]
    assert t["count"] == 1 and t["ids"] == [P + "2"] and t["parent_rollups"] == {P + "1": [P + "2"]}


def test_service_panel_unavailable_without_records_and_computed_with():
    assert run()["service_panel"]["status"] == "unavailable"
    inp = inputs()
    inp["deployments"] = [
        {"id": "d1", "service": "api", "deployed_at": "2026-11-01T10:00:00Z", "commit_at": "2026-11-01T08:00:00Z",
         "status": "success"},
        {"id": "d2", "service": "api", "deployed_at": "2026-11-02T10:00:00Z", "commit_at": "2026-11-02T09:00:00Z",
         "status": "failed", "requires_intervention": True, "recovered_at": "2026-11-02T12:00:00Z"}]
    m = run(inp)["service_panel"]["services"]["api"]["1w"]
    assert m["deployment_frequency"]["deployments"] == 2
    assert m["change_fail_rate"]["numerator"] == 1 and m["change_fail_rate"]["denominator"] == 2
    assert m["failed_deployment_recovery_time"]["median"] == 7200 and m["change_lead_time"]["n"] == 1


def test_inaccessible_source_marks_collection_incomplete():
    inp = inputs()
    inp["inaccessible"] = [{"source": "example-org/other#9", "detail": "HTTP 404"}]
    rep = run(inp)
    assert rep["header"]["collection_complete"] is False
    assert any("example-org/other#9" in r for r in rep["header"]["incomplete_reasons"])


def test_every_aggregate_carries_ids():
    t = w(run())["team"]
    for k in ("cycle_time", "ready_lead_time", "blocker_ack_time", "blocker_resolution_time"):
        assert t[k]["ids"]
    assert t["tokens"]["usage_ids"] == ["u3", "u4", "u5", "u6", "u7"]
    assert t["outcome_tokens"]["complete_coverage"]["usage_ids"]


def test_script_is_read_only_and_adapter_uses_injected_runner():
    src = (ROOT / "scripts" / "foundry_leader_board.py").read_text()
    for bad in ("post_comment(", "gh issue comment", "--method POST", "-X POST", "open(p, \"w\"", "subprocess"):
        assert bad not in src
    assert "datetime.now" in src and src.count("datetime.now") == 3        # only default clocks, never in compute
    compute_src = src[src.index("def compute("):src.index("def _trend(")]
    assert "now(" not in compute_src


def test_fetch_issues_through_runner():
    Res = lb.default_runner.__globals__["Result"]
    calls = []

    def runner(argv, timeout):
        calls.append(argv)
        if argv[1:3] == ["issue", "list"]:
            if "closed" in argv:
                return Res(rc=0, stdout="[]")
            return Res(rc=0, stdout=json.dumps([{"number": 7, "title": "t", "labels": [{"name": "type:fix"}],
                                                 "author": {"login": "alice"}, "createdAt": "2026-11-01T00:00:00Z",
                                                 "projectItems": [{"title": "Delivery"}], "body": "Reverts: example-org/planning#2"}]))
        if argv[1] == "api" and "graphql" in argv:
            return Res(rc=0, stdout=json.dumps({"data": {"repository": {"issue": {"timelineItems": {
                "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}}}}}))
        if argv[1:3] == ["issue", "view"]:
            return Res(rc=0, stdout=json.dumps({"comments": []}))
        if argv[1] == "api":
            return Res(rc=0, stdout="[]")
        return Res(rc=1, stderr="unexpected")
    issues, bad, _ = lb.fetch_issues({"owner": "example-org", "planning_repo": "planning"}, runner, 5,
                                     lb._d(CUT))
    assert bad == [] and issues[0]["ref"] == P + "7" and issues[0]["revert_of"] == P + "2"
    assert issues[0]["project"] == "Delivery"
    assert all(c[0] == "gh" for c in calls)
    assert not any(a in ("POST", "PATCH", "PUT", "DELETE") for c in calls for a in c)
