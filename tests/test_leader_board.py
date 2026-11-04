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
    (tmp_path / ".claude").mkdir()   # gn-17: the leader-board is a team-mode verb
    (tmp_path / ".claude" / "foundry-project.json").write_text(json.dumps({
        "operating_mode": "team", "github_scope": {"owner": "example-org", "planning_repo": "planning"}}))
    pd = str(tmp_path)
    outs = []
    for _ in range(2):
        buf = io.StringIO()
        assert lb.main(["--inputs", str(f), "--as-of", CUT, "--json", "--project-dir", pd], out=buf) == 0
        outs.append(buf.getvalue())
    assert outs[0] == outs[1]
    texts = []
    for _ in range(2):
        buf = io.StringIO()
        lb.main(["--inputs", str(f), "--as-of", CUT, "--project-dir", pd], out=buf)
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
    assert run(filters={"work_type": "operations"})["windows"]["1m"]["team"]["throughput"]["ids"] == [P + "4"]
    assert w(run(filters={"project": "Nope"}))["team"]["throughput"]["count"] == 0
    assert w(run(filters={"work_type": "code"}))["team"]["tokens"]["total"] == 740_000
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


def test_compute_reads_no_clock():
    src = (ROOT / "scripts" / "foundry_leader_board.py").read_text()
    assert "datetime.now" in src        # default clocks only (fetch_inputs, main), never in compute
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
            return Res(rc=0, stdout=json.dumps([{"number": 7, "title": "t", "labels": [{"name": "work:operations"}],
                                                 "author": {"login": "alice"}, "authorAssociation": "MEMBER", "createdAt": "2026-11-01T00:00:00Z",
                                                 "projectItems": [{"title": "Delivery"}], "body": "Reverts: example-org/planning#2"}]))
        if argv[1] == "api" and "graphql" in argv:
            return Res(rc=0, stdout=json.dumps({"data": {"repository": {"issue": {"timelineItems": {
                "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}}}}}))
        if argv[1:3] == ["issue", "view"]:
            return Res(rc=0, stdout=json.dumps({"comments": []}))
        if argv[1] == "api":
            return Res(rc=0, stdout="[]")
        return Res(rc=1, stderr="unexpected")
    issues, bad = lb.fetch_issues({"owner": "example-org", "planning_repo": "planning"}, runner, 5,
                                     lb._d(CUT))
    assert bad == [] and issues[0]["ref"] == P + "7" and issues[0]["revert_of"] == P + "2"
    assert issues[0]["project"] == "Delivery"
    assert all(c[0] == "gh" for c in calls)
    assert not any(a in ("POST", "PATCH", "PUT", "DELETE") for c in calls for a in c)


# ---- review round 1: synthetic histories ----------------------------------------------------------------

def _marker(i, event, at, by, target=None, blocking=None, **extra):
    body = {"v": 1, "event": event, "idempotency_key": f"{event}-{i}-{at}-{target}", "target": target,
            "blocking": blocking, "at": at, "by": by, "evidence": ["https://example.com/e"], **extra}
    return {"author": {"login": by if by in ("alice", "bob", "carol") else "alice"}, "authorAssociation": "MEMBER", "id": f"{event}-{at}-{target}", "createdAt": at,
            "body": "<!-- foundry-deck: " + json.dumps(body) + " -->\nrecorded"}


def _assign(login, at):
    return {"__typename": "AssignedEvent", "actor": {"login": "lead"}, "assignee": {"login": login}, "createdAt": at}


def _unassign(login, at):
    return {"__typename": "UnassignedEvent", "actor": {"login": "lead"}, "assignee": {"login": login}, "createdAt": at}


def _blocking(n, at):
    return {"__typename": "BlockingAddedEvent", "actor": {"login": "lead"}, "createdAt": at,
            "blockedIssue": {"number": n, "repository": {"nameWithOwner": "example-org/planning"}}}


def _status(st, at):
    return {"__typename": "ProjectV2ItemStatusChangedEvent", "actor": {"login": "lead"}, "createdAt": at, "status": st}


def _issue(n, created, timeline=(), comments=(), labels=("request", "request:review"), **kw):
    return {"ref": P + str(n), "title": f"t{n}", "created_at": created, "author": "lead", "labels": list(labels),
            "project": "Delivery", "timeline": list(timeline), "comments": list(comments), **kw}


def _base(issues, **kw):
    return {"scope": {"operators": {"alice": "op-alice", "bob": "op-bob", "carol": "op-carol"},
                      "default_repo": "example-org/planning"},
            "issues": issues, "usage": [], "usage_coverage": {"complete": True, "earliest": "2026-01-01T00:00:00Z",
                                                              "operators_without_records": []},
            "inaccessible": [], "deployments": None, "freshness": {}, **kw}


def _outcome(n, owner, accepted_at, labels=("work:code",), created="2026-10-01T08:00:00Z", **kw):
    return _issue(n, created, [_status("Ready", created), _assign(owner, created),
                               _status("In progress", "2026-10-02T08:00:00Z")],
                  [_marker(n, "accepted", accepted_at, owner, None)], labels=labels, **kw)


def test_turnaround_attributed_to_the_responder_and_handoff_is_unattributed():
    same = _issue(1, "2026-11-02T00:00:00Z", [_assign("alice", "2026-11-02T00:00:00Z")],
                  [_marker(1, "resolved", "2026-11-02T10:00:00Z", "alice", P + "1")])
    moved = _issue(2, "2026-11-02T00:00:00Z", [_assign("alice", "2026-11-02T00:00:00Z"),
                                               _unassign("alice", "2026-11-02T05:00:00Z"),
                                               _assign("bob", "2026-11-02T05:00:00Z")],
                   [_marker(2, "resolved", "2026-11-02T10:00:00Z", "bob", P + "2")])
    rep = run(_base([same, moved]))
    ops = rep["windows"]["1w"]["operators"]
    assert ops["op-alice"]["turnaround_by_request_type"]["review"]["ids"] == [P + "1"]
    assert ops["op-alice"]["turnaround_by_request_type"]["review"]["median"] == 10 * 3600
    # reassignment between request and response: the response is nobody's, not the new assignee's
    assert ops["(unattributed)"]["turnaround_by_request_type"]["review"]["ids"] == [P + "2"]
    assert "op-bob" not in ops or ops["op-bob"]["turnaround_by_request_type"].get("review") is None


def test_blocker_clock_starts_at_the_later_of_blocking_and_assignment():
    blocker = _issue(5, "2026-11-01T00:00:00Z",
                     [_blocking(1, "2026-11-01T00:00:00Z"), _assign("carol", "2026-11-01T03:00:00Z")],
                     [_marker(5, "blocker_ack", "2026-11-01T04:00:00Z", "carol", P + "1", P + "1")])
    blocked = _outcome(1, "alice", "2026-11-02T00:00:00Z")
    ack = w(run(_base([blocker, blocked])))["team"]["blocker_ack_time"]
    assert ack["n"] == 1 and ack["median"] == 3600          # 04:00 - 03:00, not 04:00 - 00:00


def test_blocker_ack_before_targeting_is_invalid_not_clamped():
    blocker = _issue(5, "2026-11-01T00:00:00Z",
                     [_blocking(1, "2026-11-01T00:00:00Z"), _assign("carol", "2026-11-01T03:00:00Z")],
                     [_marker(5, "blocker_ack", "2026-11-01T01:00:00Z", "carol", P + "1", P + "1")])
    rep = run(_base([blocker, _outcome(1, "alice", "2026-11-02T00:00:00Z")]))
    ack = w(rep)["team"]["blocker_ack_time"]
    assert ack["n"] == 0 and ack["status"] == "unavailable" and len(ack["invalid_acks"]) == 1
    assert [x["acknowledged"] for x in rep["queue_panel"]["open_blockers"]] == [False]
    assert "invalid acknowledgements" in lb.render_text(rep)


def test_resolved_matches_the_episode_target():
    def blocker(resolved_target):
        return _issue(5, "2026-11-01T00:00:00Z", [_assign("carol", "2026-11-01T00:00:00Z"), _blocking(1, "2026-11-01T01:00:00Z")],
                      [_marker(5, "resolved", "2026-11-01T05:00:00Z", "carol", resolved_target)])
    blocked = _outcome(1, "alice", "2026-11-02T00:00:00Z")
    other = w(run(_base([blocker(P + "9"), blocked])))["team"]
    assert other["blockers_resolved"]["count"] == 0
    mine = w(run(_base([blocker(P + "1"), blocked])))["team"]
    assert mine["blockers_resolved"]["count"] == 1 and mine["blocker_resolution_time"]["median"] == 4 * 3600
    itself = w(run(_base([blocker(P + "5"), blocked])))["team"]
    assert itself["blockers_resolved"]["count"] == 1


def test_cross_engineer_compares_blocked_issue_owner_and_unattributed_is_excluded():
    def blocker(n, tgt, owner):
        return _issue(n, "2026-11-01T00:00:00Z", [_assign(owner, "2026-11-01T00:00:00Z"), _blocking(tgt, "2026-11-01T01:00:00Z")],
                      [_marker(n, "resolved", "2026-11-01T05:00:00Z", owner, P + str(n))])
    mine = _outcome(1, "alice", "2026-11-02T00:00:00Z")
    unowned = _issue(2, "2026-10-01T00:00:00Z", [], [], labels=("work:code",))
    team = w(run(_base([mine, unowned, blocker(5, 1, "alice"), blocker(6, 1, "bob"), blocker(7, 2, "bob"),
                        blocker(8, 99, "bob")])))["team"]
    b = team["blockers_resolved"]
    assert b["ids"] == [f"{P}6->{P}1@2026-11-01T01:00:00Z"]
    assert [i.split("->")[0] for i in b["excluded_not_cross_engineer"]] == [P + "5"]
    assert sorted(i.split("->")[0] for i in b["unattributed"]) == [P + "7", P + "8"]
    # unattributed episodes are in neither the count nor the pooled distribution
    assert team["blocker_resolution_time"]["n"] == 2
    assert sorted(i.split("->")[0] for i in team["blocker_resolution_time"]["unattributed_ids"]) == [P + "7", P + "8"]


def test_unscheduled_and_unparseable_requests():
    open_req = _issue(1, "2026-11-01T00:00:00Z", [_assign("alice", "2026-11-01T00:00:00Z")])
    bad = _issue(2, "2026-11-01T00:00:00Z", [_assign("alice", "2026-11-01T00:00:00Z")],
                 [_marker(2, "scheduled", "2026-11-01T01:00:00Z", "alice", P + "2", when="soonish")])
    rep = run(_base([open_req, bad]))
    assert [x["id"] for x in rep["queue_panel"]["unscheduled_requests"]] == [P + "1"]
    assert rep["header"]["collection_complete"] is False
    assert any("unparseable schedule target" in r and P + "2" in r for r in rep["header"]["incomplete_reasons"])
    assert w(rep)["team"]["scheduled_request_reliability"]["status"] == "unavailable"


def test_date_only_deadline_is_end_of_that_utc_day():
    assert lb.parse_deadline("2026-10-30") == lb._d("2026-10-31T00:00:00Z")
    assert lb.parse_deadline("2026-10-30T09:00:00Z") == lb._d("2026-10-30T09:00:00Z")
    assert lb.parse_deadline("") is None and lb.parse_deadline("soonish") is None

    def req(done):
        return _issue(1, "2026-10-29T00:00:00Z", [_assign("alice", "2026-10-29T00:00:00Z")],
                      [_marker(1, "scheduled", "2026-10-29T01:00:00Z", "alice", P + "1", when="2026-10-30"),
                       _marker(1, "resolved", done, "alice", P + "1")])
    on = w(run(_base([req("2026-10-30T23:00:00Z")]), as_of="2026-11-03T12:00:00Z"), "1m")["team"]
    assert on["scheduled_request_reliability"]["on_time"] == 1
    late = w(run(_base([req("2026-10-31T00:00:01Z")])), "1m")["team"]
    assert late["scheduled_request_reliability"]["on_time"] == 0


def test_rescheduled_only_history_has_unavailable_original_target():
    r = _issue(1, "2026-10-01T00:00:00Z", [_assign("alice", "2026-10-01T00:00:00Z")],
               [_marker(1, "rescheduled", "2026-10-02T00:00:00Z", "alice", P + "1", when="2026-10-10")])
    rep = run(_base([r]))
    assert rep["header"]["original_target_unavailable"] == [P + "1"]
    assert rep["queue_panel"]["overdue_requests"][0]["original_target"] == "unavailable"
    assert w(rep, "1m")["team"]["scheduled_request_reliability"]["status"] == "unavailable"
    assert rep["header"]["collection_complete"] is False


def test_cancelled_request_is_excluded_from_cohort_and_surfaced():
    r = _issue(1, "2026-10-29T00:00:00Z",
               [_assign("alice", "2026-10-29T00:00:00Z"),
                {"__typename": "ClosedEvent", "actor": {"login": "lead"}, "createdAt": "2026-10-30T00:00:00Z",
                 "stateReason": "NOT_PLANNED"}],
               [_marker(1, "scheduled", "2026-10-29T01:00:00Z", "alice", P + "1", when="2026-10-31")])
    rep = run(_base([r]))
    rel = w(rep, "1m")["team"]["scheduled_request_reliability"]
    assert rel["excluded_cancelled_from_cohort"] == [P + "1"]
    assert "cancelled/duplicate requests excluded from the cohort: " + P + "1" in lb.render_text(rep)


def test_preceding_comparison_window_contents():
    cur = _outcome(1, "alice", "2026-11-03T06:00:00Z")           # inside the 1d window
    prev = _outcome(2, "alice", "2026-11-02T06:00:00Z")          # inside the preceding 1d window only
    old = _outcome(3, "alice", "2026-10-20T06:00:00Z")           # before every 1d/1w window
    rep = run(_base([cur, prev, old]))
    d = w(rep, "1d")
    assert d["team"]["throughput"]["ids"] == [P + "1"]
    assert d["team_previous"]["throughput"]["ids"] == [P + "2"]
    assert (d["previous_start"], d["previous_end"]) == ("2026-11-01T12:00:00Z", "2026-11-02T12:00:00Z")
    assert d["trend"]["op-alice"]["throughput"] == {"current": 1, "previous": 1, "change": 0}
    wk = w(rep, "1w")
    assert wk["team"]["throughput"]["ids"] == [P + "1", P + "2"]
    assert wk["team_previous"]["throughput"]["ids"] == []        # [10-20T12, 10-27T12) excludes 10-20T06
    assert w(rep, "1m")["team_previous"]["throughput"]["ids"] == []


def test_json_and_text_cover_every_section():
    inp = inputs()
    inp["deployments"] = [{"id": "d1", "service": "api", "deployed_at": "2026-11-01T10:00:00Z",
                           "commit_at": "2026-11-01T08:00:00Z", "status": "success"}]
    rep = run(inp, sort="tokens@1w")
    text = lb.render_text(rep)
    assert {"header", "windows", "queue_panel", "bottlenecks", "service_panel", "operator_order",
            "sort"} <= set(rep)
    for phrase in ("cutoff:", "timezone:", "source freshness:", "collection complete:", "filters:",
                   "## Team bottlenecks", "## Queue panel", "## Window 1d", "## Window 1w", "## Window 1m",
                   "## Service panel (DORA)", "## Coverage", "operator tables ordered by tokens@1w"):
        assert phrase in text
    for o in rep["operator_order"]:
        assert f"### Operator {o}" in text
    assert "api 1w: deployments 1" in text
    # every per-scope JSON key has a rendered counterpart; a new metric key fails this until it is rendered
    rendered = {"throughput": "accepted throughput:", "cycle_time": "cycle time:", "ready_lead_time": "ready lead time:",
                "blockers_resolved": "blockers resolved:", "blocker_ack_time": "blocker acknowledgement time:",
                "blocker_resolution_time": "blocker resolution time:",
                "turnaround_by_request_type": "turnaround [", "scheduled_request_reliability": "scheduled-request reliability",
                "routine_requests_completed": "routine requests completed", "quality_follow_up": "quality follow-up:",
                "tokens": "tokens consumed in window:", "outcome_tokens": "lifetime tokens per accepted outcome"}
    team = rep["windows"]["1w"]["team"]
    assert set(team) == set(rendered)
    for k, phrase in rendered.items():
        assert phrase in text, k
    # values agree, not just labels
    assert f"accepted throughput: {team['throughput']['count']} ids: {', '.join(team['throughput']['ids'])}" in text
    assert f"tokens consumed in window: {team['tokens']['total']:,}" in text
    for kind in (b["kind"] for b in rep["bottlenecks"]):
        assert f"- {kind}:" in text
    pn = rep["queue_panel"]
    assert f"open blockers: {len(pn['open_blockers'])}; unacknowledged: {len(pn['unacknowledged_blockers'])}" in text
    for key in ("overdue_requests", "unscheduled_requests", "outstanding_reviews"):
        assert f"{len(pn[key])}" in text
    for x in pn["active_work"]:
        assert x["id"] in text
    # the JSON output of main() is the same report
    assert json.loads(json.dumps(rep, sort_keys=True))["header"]["cutoff"] == CUT


def test_cohort_with_no_usage_is_unavailable_not_zero():
    inp = _base([_outcome(1, "alice", "2026-11-02T06:00:00Z")])
    rep = run(inp)
    oc = w(rep)["team"]["outcome_tokens"]
    assert oc["no_usage_unavailable"] == [P + "1"]
    assert oc["complete_coverage"]["status"] == "unavailable" and oc["complete_coverage"]["n"] == 0
    assert w(rep)["team"]["tokens"]["total"] == 0 and "unavailable" in lb.render_text(rep)


def test_mapped_and_unmapped_logins():
    mapped = _outcome(1, "alice", "2026-11-02T06:00:00Z")
    unmapped = _outcome(2, "zed", "2026-11-02T07:00:00Z")
    rep = run(_base([mapped, unmapped]))
    ops = rep["header"]["operators_in_scope"]
    assert "op-alice" in ops and "zed" in ops and "alice" not in ops
    assert any("no operator mapping: zed" in r for r in rep["header"]["incomplete_reasons"])
    assert w(rep)["operators"]["zed"]["throughput"]["ids"] == [P + "2"]


def test_usage_without_request_id_is_unplaced_not_deduped_away():
    inp = inputs()
    base = copy.deepcopy(inp["usage"][0])
    inp["usage"] += [base | {"request_id": None, "at": "2026-11-01T00:00:00Z"},
                     base | {"request_id": None, "at": "2026-11-01T01:00:00Z", "tokens": {"input": 5}}]
    rep = run(inp)
    assert rep["header"]["usage_unplaced"]["count"] == 2
    assert w(rep)["team"]["tokens"]["total"] == 840_000             # excluded from totals
    assert any("2 records with no request id" in r for r in rep["header"]["incomplete_reasons"])
    assert "usage records unplaced" in lb.render_text(rep) and "no request_id" in lb.render_text(rep)


def test_lifetime_coverage_uses_earliest_of_ready_created_start():
    inp = inputs()
    one = next(i for i in inp["issues"] if i["ref"] == P + "1")        # created 10-20, ready 10-21, start 10-22
    inp["usage_coverage"]["earliest"] = "2026-10-20T12:00:00Z"        # after created, before ready/start
    coh = w(run(inp))["team"]["outcome_tokens"]
    assert P + "1" in coh["partial_coverage"]["ids"]
    assert one["created_at"] == "2026-10-20T08:00:00Z"
    inp["usage_coverage"]["earliest"] = "2026-10-20T08:00:00Z"
    assert P + "1" in w(run(inp))["team"]["outcome_tokens"]["complete_coverage"]["ids"]


def test_work_type_reads_work_label_then_project_field():
    a = _outcome(1, "alice", "2026-11-02T06:00:00Z", labels=("work:security",))
    b = _outcome(2, "alice", "2026-11-02T07:00:00Z", labels=(), work_type="architecture")
    c = _outcome(3, "alice", "2026-11-02T08:00:00Z", labels=("type:fix",))
    d = _outcome(4, "alice", "2026-11-02T09:00:00Z", labels=("work:document",), work_type={"name": "code"})
    by = w(run(_base([a, b, c, d])))["team"]["throughput"]["by_work_type"]
    assert {k: v["ids"] for k, v in by.items()} == {"security": [P + "1"], "architecture": [P + "2"],
                                                    "unclassified": [P + "3"], "document": [P + "4"]}
    assert w(run(_base([a, b, c, d]), filters={"work_type": "architecture"}))["team"]["throughput"]["ids"] == [P + "2"]


def test_kind_request_alias_is_not_a_request():
    r = _issue(1, "2026-11-02T00:00:00Z", [_assign("alice", "2026-11-02T00:00:00Z")], labels=("kind:request",))
    rep = run(_base([r]))
    assert rep["queue_panel"]["unscheduled_requests"] == []


# ---- the adapter through an injected runner --------------------------------------------------------------

def _stub(calls, list_rows=None, sub=None, sub_rc=0):
    Res = lb.default_runner.__globals__["Result"]

    def runner(argv, timeout):
        calls.append(list(argv))
        if argv[1:3] == ["issue", "list"]:
            return Res(rc=0, stdout=json.dumps([] if "closed" in argv else (list_rows or [])))
        if argv[1] == "api" and "graphql" in argv:
            return Res(rc=0, stdout=json.dumps({"data": {"repository": {"issue": {"timelineItems": {
                "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}}}}}))
        if argv[1:3] == ["issue", "view"]:
            return Res(rc=0, stdout=json.dumps({"comments": []}))
        if argv[1] == "api" and any("sub_issues" in a for a in argv):
            n = next(a for a in argv if "sub_issues" in a).split("/issues/")[1].split("/")[0]
            return Res(rc=sub_rc, stdout=(sub or {}).get(n, "[]"), stderr="boom")
        return Res(rc=1, stderr="unexpected")
    return runner


def _row(n, labels=()):
    return {"number": n, "title": f"t{n}", "labels": [{"name": x} for x in labels], "author": {"login": "alice"},
            "createdAt": "2026-11-01T00:00:00Z", "projectItems": [], "body": ""}


CFG = {"owner": "example-org", "planning_repo": "planning"}


def test_fetch_sets_parent_on_children_so_rollups_populate_through_the_adapter():
    calls = []
    kids = json.dumps([{"number": 2, "repository_url": "https://api.github.com/repos/example-org/planning"}])
    runner = _stub(calls, [_row(1), _row(2)], {"1": kids})
    issues, bad = lb.fetch_issues(CFG, runner, 5, lb._d(CUT))
    by = {i["ref"]: i for i in issues}
    assert bad == [] and by[P + "1"]["children"] == [P + "2"] and by[P + "2"]["parent"] == P + "1"
    subs = [c for c in calls if any("sub_issues" in a for a in c)]
    assert subs and all("--paginate" in c and any(a.endswith("sub_issues?per_page=100") for a in c) for c in subs)
    done = [_outcome(1, "alice", "2026-11-02T06:00:00Z", children=[P + "2"]),
            _outcome(2, "alice", "2026-11-02T07:00:00Z", parent=P + "1")]
    assert w(run(_base(done)))["team"]["throughput"]["parent_rollups"] == {P + "1": [P + "2"]}


def test_fetch_handles_paginated_sub_issue_pages_and_failure():
    page = lambda n: [{"number": n, "repository_url": "https://api.github.com/repos/example-org/planning"}]  # noqa: E731
    two_pages = json.dumps(page(2)) + json.dumps(page(3))             # `gh api --paginate` prints pages back to back
    issues, bad = lb.fetch_issues(CFG, _stub([], [_row(1), _row(2), _row(3)], {"1": two_pages}), 5, lb._d(CUT))
    assert [i for i in issues if i["ref"] == P + "1"][0]["children"] == [P + "2", P + "3"]
    _, bad = lb.fetch_issues(CFG, _stub([], [_row(1)], sub_rc=1), 5, lb._d(CUT))
    assert bad and "roll-up unavailable" in bad[0]["detail"]


def test_fetch_truncation_is_reported_as_incomplete():
    rows = [_row(n) for n in range(1, lb.LIST_LIMIT + 1)]
    old = lb.LIST_LIMIT
    try:
        lb.LIST_LIMIT = 3
        issues, bad = lb.fetch_issues(CFG, _stub([], rows[:3]), 5, lb._d(CUT))
    finally:
        lb.LIST_LIMIT = old
    assert any("truncated" in b["detail"] for b in bad)
    inp = _base(issues, inaccessible=bad)
    assert run(inp)["header"]["collection_complete"] is False


def test_fetch_reads_project_work_type_field():
    row = _row(1) | {"projectItems": [{"title": "Delivery", "Work type": {"name": "Security"}}]}
    issues, _ = lb.fetch_issues(CFG, _stub([], [row]), 5, lb._d(CUT))
    assert issues[0]["work_type"] == "Security"
    assert lb._facts(issues[0] | {"labels": []}, lb._d(CUT)).work_type == "security"


def test_runner_argv_allowlist_is_read_only(tmp_path):
    """Every gh call the adapter makes is a GET, a search listing, or a GraphQL query (never a mutation)."""
    calls = []
    kids = json.dumps([{"number": 2, "repository_url": "https://api.github.com/repos/example-org/planning"}])
    runner = _stub(calls, [_row(1), _row(2)], {"1": kids})
    lb.fetch_issues(CFG, runner, 5, lb._d(CUT))
    assert calls
    for argv in calls:
        assert argv[0] == "gh"
        if argv[1:3] == ["issue", "list"] or argv[1:3] == ["issue", "view"]:
            assert not any(a in ("comment", "edit", "close", "create") for a in argv[1:3])
        elif argv[1] == "api" and "graphql" in argv:
            q = next(a for a in argv if a.startswith("query="))
            assert q.lstrip("query=").lstrip().startswith("query") and "mutation" not in q.lower()
        elif argv[1] == "api":
            method = [argv[i + 1] for i, a in enumerate(argv) if a in ("-X", "--method")]
            assert method in ([], ["GET"])
            assert not any(a in ("-f", "-F", "--field", "--raw-field", "--input") for a in argv)
        else:
            raise AssertionError(f"argv outside the allowlist: {argv}")


def test_foreign_accepted_marker_is_not_an_acceptance():
    iss = _outcome(1, "alice", "2026-11-02T06:00:00Z")
    iss["comments"][0]["author"] = {"login": "random-outsider"}
    iss["comments"][0]["authorAssociation"] = "NONE"
    rep = run(_base([iss]))
    assert w(rep)["operators"].get("op-alice", {}).get("throughput", {}).get("ids", []) == []
    good = run(_base([_outcome(1, "alice", "2026-11-02T06:00:00Z")]))
    assert w(good)["operators"]["op-alice"]["throughput"]["ids"] == [P + "1"]
