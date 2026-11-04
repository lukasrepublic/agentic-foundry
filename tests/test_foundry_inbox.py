"""gn-09: operator inbox. No network, no credential: every subprocess goes through the injectable runner,
replaced by a stateful fake `gh`. Fixtures are synthetic (example-org, alice/bob)."""
import json
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures" / "inbox"
sys.path.insert(0, str(ROOT / "scripts"))
import foundry_inbox as inbox  # noqa: E402
import foundry_deck_events as de  # noqa: E402

R = inbox.default_runner.__globals__["Result"]
T0 = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
REQ = "https://github.com/example-org/planning/issues/1"
REQ2 = "https://github.com/example-org/planning/issues/2"
BLOCKED = "https://github.com/example-org/code/issues/40"
PR = "https://github.com/example-org/code/pull/5"
MENTION = "https://github.com/example-org/code/issues/7"


class FakeGh:
    def __init__(self):
        self.now = T0
        self.search = {"assigned": [], "mentions": [], "review_requests": []}
        self.search_fail = {}                 # name -> Result
        self.notif = (FIX / "notifications_200.txt").read_text()
        self.notif_fail = None
        self.issues = {}                      # url -> {"state", "assignees", "blocking": [urls]}
        self.timeline = {}                    # ref -> nodes
        self.comments = {}                    # ref -> [comment]
        self.calls = []
        self.view_fail = set()                # refs whose blocking read fails
        self.pr_state = {}                    # url -> state for `gh pr view`
        self.notif_pages = None               # optional list of raw responses served page by page
        self.me_fail = False

    def clock(self):
        return self.now

    @staticmethod
    def issue(url, **kw):
        return dict({"state": "OPEN", "assignees": [], "blocking": []}, **kw)

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        j = " ".join(argv)
        if argv[:3] == ["gh", "search", "issues"] or argv[:3] == ["gh", "search", "prs"]:
            name = "review_requests" if argv[2] == "prs" else ("assigned" if "--label" in argv else "mentions")
            if name in self.search_fail:
                return self.search_fail[name]
            return R(stdout=json.dumps(self.search[name]))
        if argv[:3] == ["gh", "api", "user"]:
            if self.me_fail:
                return R(rc=1, stderr="not logged in")
            return R(stdout='{"login":"alice"}')
        if argv[:3] == ["gh", "pr", "view"]:
            return R(stdout=json.dumps({"state": self.pr_state.get(argv[3], "OPEN")}))
        if is_notif(argv):
            if self.notif_pages is not None:
                m = re.search(r"&page=(\d+)", j)
                return R(stdout=self.notif_pages[int(m.group(1)) - 1 if m else 0])
            if self.notif_fail:
                return self.notif_fail
            if "304 Not Modified" in self.notif:
                return R(rc=1, stdout=self.notif, stderr="gh: HTTP 304")
            return R(stdout=self.notif)
        if argv[:3] == ["gh", "api", "graphql"]:
            m = re.search(r"owner=(\S+)", j), re.search(r"repo=(\S+)", j), re.search(r"number=(\d+)", j)
            ref = f"{m[0].group(1)}/{m[1].group(1)}#{m[2].group(1)}"
            nodes = self.timeline.get(ref, [])
            return R(stdout=json.dumps({"data": {"repository": {"issue": {"timelineItems": {
                "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}}}))
        if argv[:3] == ["gh", "issue", "comment"]:
            ref = f"{argv[argv.index('-R') + 1]}#{argv[3]}"
            body = argv[argv.index("--body") + 1]
            lst = self.comments.setdefault(ref, [])
            lst.append({"id": f"c{len(lst) + 1}", "createdAt": self.now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "author": {"login": "alice"}, "authorAssociation": "OWNER", "body": body})
            return R()
        if argv[:3] == ["gh", "issue", "view"]:
            fields = argv[argv.index("--json") + 1]
            if "-R" in argv:
                ref = f"{argv[argv.index('-R') + 1]}#{argv[3]}"
                if fields == "comments":
                    return R(stdout=json.dumps({"comments": self.comments.get(ref, [])}))
                if ref in self.view_fail:
                    return R(rc=1, stderr="boom")
                url = f"https://github.com/{ref.replace('#', '/issues/')}"
                iss = self.issues.get(url, self.issue(url))
                return R(stdout=json.dumps({"state": iss["state"],
                                            "blocking": [{"url": u} for u in iss["blocking"]]}))
            iss = self.issues[argv[3]]
            return R(stdout=json.dumps({"state": iss["state"], "url": argv[3],
                                        "assignees": [{"login": a} for a in iss["assignees"]]}))
        return R(rc=1, stderr=f"unexpected: {j}")

    def posted(self, ref, event=None):
        out = []
        for c in self.comments.get(ref, []):
            out += [e for e in de._markers_in(c) if e.event != "unparsed"]
        return [e for e in out if event is None or e.event == event]

    def add_blocker(self, request_ref, blocked_url, owner="bob", created="2026-11-03T11:00:00Z"):
        self.issues[blocked_url] = self.issue(blocked_url, assignees=[owner])
        req_url = f"https://github.com/{request_ref.replace('#', '/issues/')}"
        self.issues.setdefault(req_url, self.issue(req_url))["blocking"].append(blocked_url)
        m = re.match(r"https://github.com/(.+)/issues/(\d+)", blocked_url)
        self.timeline.setdefault(request_ref, []).append(
            {"__typename": "BlockingAddedEvent", "createdAt": created, "actor": {"login": "alice"},
             "blockedIssue": {"number": int(m.group(2)), "repository": {"nameWithOwner": m.group(1)}}})


def is_notif(argv):
    return any(a.startswith("notifications?") or "api.github.com/notifications" in a for a in argv)


def notif_calls(gh):
    return [c for c in gh.calls if is_notif(c)]


def req_item(url, n, created="2026-11-01T00:00:00Z"):
    return {"number": n, "url": url, "title": f"req {n}", "createdAt": created}


@pytest.fixture
def ws(tmp_path):
    (tmp_path / ".claude").mkdir()
    shutil.copy(FIX / "manifest.json", tmp_path / ".claude" / "foundry-project.json")
    p = tmp_path / ".claude" / "foundry-project.json"   # gn-17: the inbox is a team-mode surface
    d = json.loads(p.read_text())
    d["operating_mode"] = "team"
    p.write_text(json.dumps(d))
    return tmp_path


def set_policy(ws, policy):
    p = ws / ".claude" / "foundry-project.json"
    d = json.loads(p.read_text())
    d["github_scope"]["degraded_policy"] = policy
    p.write_text(json.dumps(d))


def run(ws, gh, **kw):
    return inbox.check(str(ws), runner=gh, clock=gh.clock, **kw)


def state(ws):
    return json.loads((ws / ".foundry" / "runtime" / "inbox.json").read_text())


# ---- AC1: per-source coverage -------------------------------------------------------------------------

def test_every_source_reported_separately_and_full_coverage_advances_last_full(ws):
    gh = FakeGh()
    gh.search["assigned"] = [req_item(REQ, 1)]
    res = run(ws, gh)
    assert set(res["sources"]) == set(inbox.SOURCES)
    for name, row in res["sources"].items():
        assert row["status"] == "ok" and row["scope"], name
    assert res["sources"]["assigned"]["scope"] == "example-org"
    assert res["coverage"] == "full"
    assert res["last_full_check_at"] == "2026-11-03T12:00:00Z"


def test_an_error_source_is_partial_not_empty_and_does_not_advance_last_full(ws):
    gh = FakeGh()
    run(ws, gh)
    gh.now = T0 + timedelta(minutes=5)
    gh.search_fail["mentions"] = R(rc=1, stderr="HTTP 502 bad gateway")
    res = run(ws, gh)
    assert res["sources"]["mentions"]["status"] == "error"
    assert res["sources"]["assigned"]["status"] == "ok"
    assert res["coverage"] == "partial"
    assert res["last_full_check_at"] == "2026-11-03T12:00:00Z"   # unchanged
    assert state(ws)["last_full_check_at"] == "2026-11-03T12:00:00Z"


def test_a_scope_refusal_is_uncovered(ws):
    gh = FakeGh()
    gh.search_fail["review_requests"] = R(rc=1, stderr="HTTP 403 forbidden")
    res = run(ws, gh)
    assert res["sources"]["review_requests"]["status"] == "uncovered"
    assert res["coverage"] == "partial"


def test_notifications_sends_if_modified_since_treats_304_as_no_change_and_honors_poll_interval(ws):
    gh = FakeGh()
    run(ws, gh)
    first = notif_calls(gh)
    assert len(first) == 1 and not any("If-Modified-Since" in x for x in first[0])
    assert "notifications?all=true&per_page=50" in first[0]            # first poll: all=true
    st = state(ws)["notifications"]
    assert st["last_modified"] == "Tue, 03 Nov 2026 10:00:00 GMT" and st["poll_interval"] == 60

    gh.now = T0 + timedelta(seconds=30)           # inside the interval: no call at all
    res = run(ws, gh)
    assert len(notif_calls(gh)) == 1
    assert res["sources"]["approvals"]["status"] == "ok" and res["sources"]["approvals"]["cached"] is True
    assert res["sources"]["approvals"]["checked_at"] == "2026-11-03T12:00:00Z"   # original, not re-stamped

    gh.now = T0 + timedelta(seconds=61)           # elapsed: conditional request, answered 304
    gh.notif = (FIX / "notifications_304.txt").read_text()
    res = run(ws, gh)
    calls = notif_calls(gh)
    assert len(calls) == 2 and "notifications?per_page=50" in calls[1]    # later polls: conditional, no all=true
    i = calls[1].index("If-Modified-Since: Tue, 03 Nov 2026 10:00:00 GMT")
    assert calls[1][i - 1] == "-H"
    assert res["sources"]["approvals"]["status"] == "ok"
    assert res["coverage"] == "full"
    assert state(ws)["notifications"]["next_poll_at"] == "2026-11-03T12:02:01Z"


def test_notification_reasons_are_approvals_and_other_reasons_are_ignored(ws):
    gh = FakeGh()
    res = run(ws, gh)
    approvals = [p for p in res["pending"] if p["kind"] == "approval"]
    assert [p["url"] for p in approvals] == [PR]
    assert [s["reason"] for s in res["notifications"]["signals"]] == ["review_requested"]


# ---- AC2: persistence ---------------------------------------------------------------------------------

def test_already_read_notification_request_stays_pending(ws):
    gh = FakeGh()
    run(ws, gh)                                    # fixture notification is unread:false
    gh.now = T0 + timedelta(minutes=10)
    gh.notif = "HTTP/2.0 200 OK\nX-Poll-Interval: 60\n\n[]"   # server now lists nothing
    res = run(ws, gh)
    assert PR in [p["url"] for p in res["pending"]]


def test_pending_survives_restart_and_errored_source_marks_items_stale(ws):
    gh = FakeGh()
    gh.search["assigned"] = [req_item(REQ, 1)]
    run(ws, gh)
    assert (ws / ".foundry" / "runtime" / ".gitignore").read_text() == "*\n"
    pend = inbox.main(["pending", "--json", "--project-dir", str(ws)], runner=gh)   # fresh process view
    assert pend == 0
    gh.now = T0 + timedelta(minutes=10)
    gh.search_fail["assigned"] = R(rc=1, stderr="HTTP 500")
    res = run(ws, gh)
    item = {p["url"]: p for p in res["pending"]}[REQ]
    assert item["stale"] is True
    assert {p["url"]: p for p in res["pending"]}[PR]["stale"] is False
    assert REQ in state(ws)["pending"]


def test_closed_request_leaves_the_pending_set(ws):
    gh = FakeGh()
    gh.search["assigned"] = [req_item(REQ, 1)]
    run(ws, gh)
    gh.search["assigned"] = []
    res = run(ws, gh)
    assert REQ not in [p["url"] for p in res["pending"]]


# ---- AC3: blockers ------------------------------------------------------------------------------------

def test_blocker_gets_blocker_ack_after_relationship_and_one_escalate(ws):
    gh = FakeGh()
    gh.search["assigned"] = [req_item(REQ, 1)]
    gh.add_blocker("example-org/planning#1", BLOCKED, owner="bob", created="2026-11-03T11:30:00Z")
    res = run(ws, gh)
    acks = gh.posted("example-org/planning#1", "blocker_ack")
    assert len(acks) == 1 and acks[0].target == "example-org/code#40"
    assert de._dt(acks[0].at) >= de._dt("2026-11-03T11:30:00Z")
    assert gh.posted("example-org/planning#1", "scheduled") == []
    assert len(res["escalate"]) == 1
    esc = res["escalate"][0]
    assert esc["type"] == "escalate" and esc["action"]
    assert esc["affected"] == {"url": BLOCKED, "owner": "bob"}
    assert esc["evidence"] == [REQ, BLOCKED] and esc["event_emitted"] is True


def test_blocked_issue_owned_by_me_or_closed_is_not_a_blocker(ws):
    gh = FakeGh()
    gh.search["assigned"] = [req_item(REQ, 1), req_item(REQ2, 2)]
    gh.add_blocker("example-org/planning#1", BLOCKED, owner="alice")
    other = "https://github.com/example-org/code/issues/41"
    gh.add_blocker("example-org/planning#2", other, owner="bob")
    gh.issues[other]["state"] = "CLOSED"
    res = run(ws, gh)
    assert res["escalate"] == []
    assert len(gh.posted("example-org/planning#1", "scheduled")) == 1
    assert len(gh.posted("example-org/planning#2", "scheduled")) == 1


def test_scheduled_request_promoted_to_blocker_gets_a_second_distinct_blocker_ack(ws):
    gh = FakeGh()
    gh.search["assigned"] = [req_item(REQ, 1)]
    run(ws, gh)
    ref = "example-org/planning#1"
    assert len(gh.posted(ref, "scheduled")) == 1 and gh.posted(ref, "blocker_ack") == []
    gh.now = T0 + timedelta(hours=1)
    gh.add_blocker(ref, BLOCKED, created="2026-11-03T12:30:00Z")
    res = run(ws, gh)
    assert len(res["escalate"]) == 1
    acks = gh.posted(ref, "blocker_ack")
    assert len(acks) == 1 and de._dt(acks[0].at) >= de._dt("2026-11-03T12:30:00Z")
    # a second blocker is its own blocker_ack + escalate, with a distinct key
    gh.now = T0 + timedelta(hours=2)
    gh.add_blocker(ref, "https://github.com/example-org/code/issues/42", created="2026-11-03T13:30:00Z")
    res = run(ws, gh)
    assert len(res["escalate"]) == 2                                   # the first one persists (R2)
    assert sorted(e["event_emitted"] for e in res["escalate"]) == [False, True]
    keys = {e.idempotency_key for e in gh.posted(ref, "blocker_ack")}
    assert len(keys) == 2


# ---- AC4: scheduling ----------------------------------------------------------------------------------

def test_non_blocker_is_scheduled_with_target_and_queue_position(ws):
    gh = FakeGh()
    gh.search["assigned"] = [req_item(REQ2, 2, "2026-11-02T00:00:00Z"), req_item(REQ, 1, "2026-11-01T00:00:00Z")]
    res = run(ws, gh)
    ev = gh.posted("example-org/planning#1", "scheduled")
    assert len(ev) == 1 and ev[0].when == "next-iteration"
    items = {p["url"]: p for p in res["pending"]}
    assert items[REQ]["scheduled"]["position"] == 1 and items[REQ2]["scheduled"]["position"] == 2
    assert items[REQ]["scheduled"]["target"] == "next-iteration"
    assert "queue position 1" in gh.comments["example-org/planning#1"][0]["body"]


def test_schedule_change_emits_rescheduled_carrying_the_original_target(ws):
    gh = FakeGh()
    gh.search["assigned"] = [req_item(REQ, 1)]
    run(ws, gh)
    out = inbox.schedule(str(ws), REQ, "2026-11-10", runner=gh, by="alice", clock=gh.clock)
    assert out["event"] == "rescheduled" and out["when"] == "2026-11-10"
    assert out["original_target"] == "next-iteration"
    gh.now = T0 + timedelta(minutes=1)
    out2 = inbox.schedule(str(ws), REQ, "2026-11-20", runner=gh, by="alice", clock=gh.clock)
    assert out2["original_target"] == "next-iteration"                  # first target unchanged
    assert [e.event for e in gh.posted("example-org/planning#1")] == ["scheduled", "rescheduled", "rescheduled"]
    assert inbox.schedule(str(ws), REQ, "2026-11-20", runner=gh, by="alice", clock=gh.clock)["unchanged"]
    assert state(ws)["pending"][REQ]["scheduled"]["target"] == "2026-11-20"
    res = run(ws, gh)                                                   # check keeps the changed target
    assert {p["url"]: p for p in res["pending"]}[REQ]["scheduled"] == {
        "target": "2026-11-20", "original_target": "next-iteration", "position": 1}


# ---- AC5: unchanged state is silent, also after runtime loss --------------------------------------------

def test_unchanged_state_emits_nothing_and_survives_runtime_loss(ws):
    gh = FakeGh()
    gh.search["assigned"] = [req_item(REQ, 1), req_item(REQ2, 2)]
    gh.add_blocker("example-org/planning#2", BLOCKED)
    first = run(ws, gh)
    assert len(first["events"]) == 3 and len(first["escalate"]) == 1
    n = {r: len(c) for r, c in gh.comments.items()}
    gh.now = T0 + timedelta(minutes=5)
    again = run(ws, gh)
    assert again["events"] == []                                        # nothing re-emitted ...
    assert [e["event_emitted"] for e in again["escalate"]] == [False]   # ... but still surfaced (R2)
    shutil.rmtree(ws / ".foundry")                                      # runtime file lost
    gh.now = T0 + timedelta(minutes=10)
    lost = run(ws, gh)
    assert lost["events"] == [] and len(lost["escalate"]) == 1
    assert {r: len(c) for r, c in gh.comments.items()} == n


# ---- AC6: resolve / ack leave state ---------------------------------------------------------------------

def test_resolve_without_https_evidence_exits_nonzero_and_records_nothing(ws, capsys):
    gh = FakeGh()
    gh.search["assigned"] = [req_item(REQ, 1)]
    run(ws, gh)
    before = {r: len(c) for r, c in gh.comments.items()}
    for ev in ([], ["http://insecure.example/x"], ["owner/repo#3"], ["/etc/passwd"], ["https://github.com"],
               ["https://github.com/"], ["https://example.org/ok", "https://github.com"]):
        args = ["resolve", REQ, "--project-dir", str(ws)] + [x for e in ev for x in ("--evidence", e)]
        assert inbox.main(args, runner=gh, clock=gh.clock) != 0
    assert {r: len(c) for r, c in gh.comments.items()} == before
    assert REQ in state(ws)["pending"]


def test_ack_and_scheduled_leave_unresolved_and_resolve_closes_it(ws):
    gh = FakeGh()
    gh.search["assigned"] = [req_item(REQ, 1)]
    gh.add_blocker("example-org/planning#1", BLOCKED)
    run(ws, gh)
    inbox.ack(str(ws), REQ, runner=gh, by="alice", clock=gh.clock)
    pend = run(ws, gh)["pending"]
    item = {p["url"]: p for p in pend}[REQ]
    assert item["acked"] is True                                         # still pending after ack+blocker_ack
    assert inbox.main(["resolve", REQ, "--evidence", "https://github.com/example-org/code/pull/99",
                       "--project-dir", str(ws)], runner=gh, clock=gh.clock) == 0
    assert gh.posted("example-org/planning#1", "resolved")[0].evidence == [
        "https://github.com/example-org/code/pull/99"]
    assert REQ not in state(ws)["pending"]
    shutil.rmtree(ws / ".foundry")
    assert REQ not in [p["url"] for p in run(ws, gh)["pending"]]         # resolved marker read back


def test_mentions_are_surfaced_keyed_by_source_url_and_not_scheduled(ws):
    gh = FakeGh()
    gh.search["mentions"] = [req_item(MENTION, 7)]
    res = run(ws, gh)
    m = {p["url"]: p for p in res["pending"]}[MENTION]
    assert m["kind"] == "mention" and "scheduled" not in m
    assert gh.posted("example-org/code#7") == []


# ---- AC7: dispatch_allowed ------------------------------------------------------------------------------

def test_full_coverage_allows_dispatch(ws):
    res = run(ws, FakeGh())
    assert res["dispatch_allowed"] is True and res["decision_needed"] is None


def _partial_after_full(ws, name="mentions"):
    gh = FakeGh()
    run(ws, gh)
    gh.now = T0 + timedelta(minutes=10)
    gh.search_fail[name] = R(rc=1, stderr="HTTP 502")
    return gh


def test_partial_without_policy_blocks_dispatch_with_decision_text(ws):
    gh = _partial_after_full(ws)
    res = run(ws, gh)
    assert res["dispatch_allowed"] is False and "mentions" in res["decision_needed"]


def test_policy_covering_the_failed_source_scope_and_age_allows_dispatch(ws):
    set_policy(ws, {"sources": ["mentions"], "max_age_min": 30, "scope": "example-org"})
    res = run(ws, _partial_after_full(ws))
    assert res["dispatch_allowed"] is True


def test_policy_that_does_not_cover_the_failed_source_denies(ws):
    set_policy(ws, {"sources": ["review_requests"], "max_age_min": 30, "scope": "example-org"})
    res = run(ws, _partial_after_full(ws))
    assert res["dispatch_allowed"] is False and res["decision_needed"]


def test_policy_for_another_scope_denies(ws):
    set_policy(ws, {"sources": ["mentions"], "max_age_min": 30, "scope": "other-org"})
    assert run(ws, _partial_after_full(ws))["dispatch_allowed"] is False


def test_policy_with_invalid_max_age_denies(ws):
    set_policy(ws, {"sources": ["mentions"], "max_age_min": -1, "scope": "example-org"})
    assert run(ws, _partial_after_full(ws))["dispatch_allowed"] is False
    set_policy(ws, {"sources": ["mentions"], "max_age_min": "soon", "scope": "example-org"})
    assert run(ws, _partial_after_full(ws))["dispatch_allowed"] is False


def test_permanently_uncovered_named_source_never_denies_forever(ws):
    """R8: anchored to the check where every UNNAMED source was ok, so a named, permanently uncovered
    `approvals` (fine-grained token) keeps dispatch allowed however long since a FULL check."""
    set_policy(ws, {"sources": ["approvals"], "max_age_min": 5, "scope": "example-org"})
    gh = FakeGh()
    gh.notif_fail = R(rc=1, stderr="HTTP 403 forbidden")
    for i in range(4):
        gh.now = T0 + timedelta(hours=6 * i)
        res = run(ws, gh)
        assert res["sources"]["approvals"]["status"] == "uncovered" and res["coverage"] == "partial"
        assert res["last_full_check_at"] is None
        assert res["dispatch_allowed"] is True, i


def test_failure_outside_the_named_set_always_denies(ws):
    set_policy(ws, {"sources": ["approvals"], "max_age_min": 5, "scope": "example-org"})
    gh = FakeGh()
    gh.notif_fail = R(rc=1, stderr="HTTP 403 forbidden")
    gh.search_fail["mentions"] = R(rc=1, stderr="HTTP 502")
    assert run(ws, gh)["dispatch_allowed"] is False


def test_policy_without_any_prior_full_check_allows_when_only_named_sources_fail(ws):
    set_policy(ws, {"sources": ["mentions"], "max_age_min": 30, "scope": "example-org"})
    gh = FakeGh()
    gh.search_fail["mentions"] = R(rc=1, stderr="HTTP 502")
    assert run(ws, gh)["dispatch_allowed"] is True


@pytest.mark.parametrize("qs", ["stale", "error"])
def test_stale_or_unreadable_queue_denies_regardless_of_policy_and_coverage(ws, qs):
    set_policy(ws, {"sources": list(inbox.SOURCES), "max_age_min": 999, "scope": "example-org"})
    full = run(ws, FakeGh(), queue_status=qs)
    assert full["coverage"] == "full" and full["dispatch_allowed"] is False and qs in full["decision_needed"]
    part = run(ws, _partial_after_full(ws), queue_status=qs)
    assert part["dispatch_allowed"] is False


def test_queue_status_cli_flag_and_bad_value(ws, capsys):
    gh = FakeGh()
    assert inbox.main(["check", "--json", "--queue-status", "stale", "--project-dir", str(ws)],
                      runner=gh, clock=gh.clock) == 0
    assert json.loads(capsys.readouterr().out)["dispatch_allowed"] is False
    with pytest.raises(SystemExit):
        inbox.main(["check", "--queue-status", "bogus", "--project-dir", str(ws)], runner=gh)


def test_no_github_scope_is_a_clean_error(tmp_path, capsys):
    assert inbox.main(["check", "--project-dir", str(tmp_path)], runner=FakeGh()) == 2


def test_failed_blocking_read_is_an_error_source_not_an_empty_one(ws):
    gh = FakeGh()
    gh.search["assigned"] = [req_item(REQ, 1)]
    gh.view_fail.add("example-org/planning#1")
    res = run(ws, gh)
    assert res["sources"]["assigned"]["status"] == "error" and res["coverage"] == "partial"
    assert {p["url"]: p for p in res["pending"]}[REQ]["stale"] is True


def test_the_only_subprocess_seam_is_the_injected_runner():
    """N1: AST-based - no subprocess/os.system/popen import or call anywhere, however it is spelled."""
    import ast
    tree = ast.parse((ROOT / "scripts" / "foundry_inbox.py").read_text())
    banned_mods = {"subprocess", "pty", "multiprocessing"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not {a.name.split(".")[0] for a in node.names} & banned_mods
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in banned_mods
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os":
            assert node.attr not in {"system", "popen", "execv", "execvp", "spawnv", "fork"} and \
                not node.attr.startswith(("exec", "spawn", "popen"))
        elif isinstance(node, ast.Name):
            assert node.id not in {"__import__", "eval", "exec"}


# ---- gn-09 review round 1 ---------------------------------------------------------------------------------

def test_end_to_end_check_then_parse_validates_ack_and_request_is_not_unacknowledged(ws):
    """B1: a real `check` against the blocker (request) issue, then gn-04's reader over that issue."""
    gh = FakeGh()
    gh.search["assigned"] = [req_item(REQ, 1)]
    gh.add_blocker("example-org/planning#1", BLOCKED, owner="bob", created="2026-11-03T11:30:00Z")
    run(ws, gh)
    ref = "example-org/planning#1"
    h = de.parse(gh.timeline[ref], gh.comments[ref], "2026-12-31T00:00:00Z")
    assert [(e.event, e.relation if e.event == "blocked" else None, e.invalid) for e in h.events] == [
        ("blocked", "blocking", None), ("blocker_ack", None, None)]
    assert h.unacknowledged == [] and de.is_blocked(h) is False


def test_scan_is_read_only_never_comments_and_check_is_the_only_writer(ws):
    gh = FakeGh()
    gh.search["assigned"] = [req_item(REQ, 1), req_item(REQ2, 2)]
    gh.add_blocker("example-org/planning#2", BLOCKED)
    res = inbox.scan(str(ws), runner=gh, clock=gh.clock)
    assert not [c for c in gh.calls if c[:3] == ["gh", "issue", "comment"]]
    assert gh.comments == {} and res["events"] == []
    assert {(p["event"], p["issue"]) for p in res["planned_events"]} == {
        ("scheduled", "example-org/planning#1"), ("scheduled", "example-org/code#5"),
        ("blocker_ack", "example-org/planning#2")}
    assert len(res["escalate"]) == 1 and res["escalate"][0]["event_emitted"] is False
    assert (ws / ".foundry" / "runtime" / "inbox.json").exists()
    assert inbox.main(["scan", "--json", "--project-dir", str(ws)], runner=gh, clock=gh.clock) == 0
    assert not [c for c in gh.calls if c[:3] == ["gh", "issue", "comment"]]
    run(ws, gh)
    assert [c for c in gh.calls if c[:3] == ["gh", "issue", "comment"]]


def test_escalations_persist_until_resolve_and_pending_shows_them(ws, capsys):
    gh = FakeGh()
    gh.search["assigned"] = [req_item(REQ, 1)]
    gh.add_blocker("example-org/planning#1", BLOCKED)
    run(ws, gh)
    for i in range(3):
        gh.now = T0 + timedelta(minutes=5 * (i + 1))
        res = run(ws, gh)
        assert res["events"] == [] and len(res["escalate"]) == 1
    assert inbox.main(["pending", "--json", "--project-dir", str(ws)], runner=gh) == 0
    assert len(json.loads(capsys.readouterr().out)["escalate"]) == 1
    inbox.resolve(str(ws), REQ, ["https://github.com/example-org/code/pull/99"], runner=gh, clock=gh.clock)
    assert run(ws, gh)["escalate"] == []


def test_failed_readback_keeps_the_persisted_escalation(ws):
    gh = FakeGh()
    gh.search["assigned"] = [req_item(REQ, 1)]
    gh.add_blocker("example-org/planning#1", BLOCKED)
    run(ws, gh)
    gh.view_fail.add("example-org/planning#1")
    res = run(ws, gh)
    assert res["sources"]["assigned"]["status"] == "error" and len(res["escalate"]) == 1


@pytest.mark.parametrize("activity", ["blocked", "reopened", "comment"])
def test_resolved_suppresses_only_until_new_activity(ws, activity):
    gh = FakeGh()
    gh.search["assigned"] = [req_item(REQ, 1)]
    run(ws, gh)
    gh.now = T0 + timedelta(hours=1)
    inbox.resolve(str(ws), REQ, ["https://github.com/example-org/code/pull/99"], runner=gh, clock=gh.clock)
    gh.now = T0 + timedelta(hours=2)
    assert REQ not in [p["url"] for p in run(ws, gh)["pending"]]          # still suppressed
    later = (T0 + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ref = "example-org/planning#1"
    if activity == "blocked":
        gh.add_blocker(ref, BLOCKED, created=later)
    elif activity == "reopened":
        gh.timeline.setdefault(ref, []).append({"__typename": "ReopenedEvent", "createdAt": later,
                                                "actor": {"login": "bob"}})
    else:
        gh.comments[ref].append({"id": "h1", "createdAt": later, "author": {"login": "bob"}, "body": "still needed"})
    gh.now = T0 + timedelta(hours=4)
    assert REQ in [p["url"] for p in run(ws, gh)["pending"]]


def test_a_bookkeeping_marker_after_resolve_does_not_unsuppress(ws):
    gh = FakeGh()
    gh.search["assigned"] = [req_item(REQ, 1)]
    run(ws, gh)
    inbox.resolve(str(ws), REQ, ["https://github.com/example-org/code/pull/99"], runner=gh, clock=gh.clock)
    gh.now = T0 + timedelta(hours=1)
    inbox.ack(str(ws), REQ, runner=gh, by="alice", clock=gh.clock)
    gh.now = T0 + timedelta(hours=2)
    assert REQ not in [p["url"] for p in run(ws, gh)["pending"]]


def test_resolve_failing_to_identify_the_operator_emits_nothing(ws):
    gh = FakeGh()
    gh.search["assigned"] = [req_item(REQ, 1)]
    run(ws, gh)
    gh.me_fail = True
    n = {r: len(c) for r, c in gh.comments.items()}
    with pytest.raises(RuntimeError, match="operator's login"):
        inbox.resolve(str(ws), REQ, ["https://github.com/example-org/code/pull/99"], runner=gh, clock=gh.clock)
    assert inbox.main(["check", "--project-dir", str(ws)], runner=gh, clock=gh.clock) == 2
    assert {r: len(c) for r, c in gh.comments.items()} == n


def _notif(i, url, reason="review_requested"):
    return {"id": str(i), "reason": reason, "unread": True, "updated_at": "2026-11-03T09:00:00Z",
            "subject": {"title": f"n{i}", "url": url, "type": "PullRequest"}}


def _resp(items, link=None, lm="Tue, 03 Nov 2026 10:00:00 GMT"):
    head = f"HTTP/2.0 200 OK\nLast-Modified: {lm}\nX-Poll-Interval: 60\n"
    if link:
        head += f'Link: <{link}>; rel="next"\n'
    return head + "\n" + json.dumps(items)


def test_unparseable_approval_has_null_ref_and_is_pruned_when_its_notification_is_gone(ws):
    gh = FakeGh()
    gh.notif = _resp([_notif(21, "https://api.github.com/repos/example-org/code/discussions/3")])
    res = run(ws, gh)
    item = {p["url"]: p for p in res["pending"]}["notification:21"]
    assert item["ref"] is None and item["kind"] == "approval"
    gh.now = T0 + timedelta(minutes=5)
    gh.notif = _resp([])
    assert "notification:21" not in [p["url"] for p in run(ws, gh)["pending"]]


def test_unparseable_approval_survives_a_304_and_a_cached_return(ws):
    gh = FakeGh()
    gh.notif = _resp([_notif(21, "https://api.github.com/repos/example-org/code/discussions/3")])
    run(ws, gh)
    gh.now = T0 + timedelta(seconds=61)
    gh.notif = (FIX / "notifications_304.txt").read_text()
    assert "notification:21" in [p["url"] for p in run(ws, gh)["pending"]]


def test_approval_on_a_merged_or_closed_pr_is_pruned(ws):
    gh = FakeGh()
    run(ws, gh)
    assert PR in [p["url"] for p in run(ws, gh)["pending"]]
    gh.pr_state[PR] = "MERGED"
    gh.now = T0 + timedelta(minutes=5)
    assert PR not in [p["url"] for p in run(ws, gh)["pending"]]


def test_notifications_paginate_with_all_true_and_a_bounded_page_count(ws):
    gh = FakeGh()
    base = "https://api.github.com/notifications?all=true&per_page=50&page="
    gh.notif_pages = [_resp([_notif(i, f"https://api.github.com/repos/example-org/code/pulls/{i}")],
                            link=base + str(i + 1)) for i in range(1, 9)]
    res = run(ws, gh)
    assert len(notif_calls(gh)) == inbox.MAX_PAGES == 5
    assert res["sources"]["approvals"]["truncated"] is True
    assert len([p for p in res["pending"] if p["kind"] == "approval"]) == 5
    assert "notifications?all=true&per_page=50" in notif_calls(gh)[0]
    assert not any("If-Modified-Since" in x for x in notif_calls(gh)[1])   # only page 1 is conditional


def test_notifications_follow_link_until_no_next(ws):
    gh = FakeGh()
    gh.notif_pages = [_resp([_notif(1, "https://api.github.com/repos/example-org/code/pulls/1")],
                            link="https://api.github.com/notifications?all=true&per_page=50&page=2"),
                      _resp([_notif(2, "https://api.github.com/repos/example-org/code/pulls/2")])]
    res = run(ws, gh)
    assert len(notif_calls(gh)) == 2 and "truncated" not in res["sources"]["approvals"]


def test_rescheduled_carries_a_structured_original_target_marker_field(ws):
    gh = FakeGh()
    gh.search["assigned"] = [req_item(REQ, 1)]
    run(ws, gh)
    inbox.schedule(str(ws), REQ, "2026-11-10", runner=gh, by="alice", clock=gh.clock)
    body = gh.comments["example-org/planning#1"][-1]["body"]
    assert json.loads(re.search(r"foundry-deck: (\{.*?\}) -->", body).group(1))["original_target"] == "next-iteration"
    assert gh.posted("example-org/planning#1", "rescheduled")[0].original_target == "next-iteration"
    # recovery from the first `scheduled` marker is the fallback when the structured field is absent
    gh.comments["example-org/planning#1"][-1]["body"] = body.replace(
        ',"original_target":"next-iteration"', "").replace("\\u003e", "\\u003e")
    view = inbox._schedule_view(inbox._markers(inbox.RunnerAdapter(gh), "example-org/planning#1"),
                                "example-org/planning#1")
    assert view["original_target"] == "next-iteration" and view["target"] == "2026-11-10"


# ---- R8 follow-up: max_age bounds the staleness of the covering check ----------------------------------

def test_unnamed_failure_denies_regardless_of_a_recent_clean_anchor(ws):
    set_policy(ws, {"sources": ["approvals"], "max_age_min": 10, "scope": "example-org"})
    gh = FakeGh()
    run(ws, gh)                                            # clean check
    gh.now = T0 + timedelta(minutes=3)
    gh.notif_fail = R(rc=1, stderr="HTTP 403 forbidden")
    gh.search_fail["assigned"] = R(rc=1, stderr="HTTP 502")
    res = run(ws, gh)
    assert res["sources"]["approvals"]["status"] == "uncovered"
    assert res["sources"]["assigned"]["status"] == "error"
    assert res["dispatch_allowed"] is False                 # `assigned` is not named: subset rule fails


def test_a_fresh_covered_check_allows_and_the_same_state_read_11_min_later_is_stale(ws, capsys):
    gh = FakeGh()
    assert run(ws, gh)["dispatch_allowed"] is True
    assert state(ws)["last_covered_check_at"] == "2026-11-03T12:00:00Z"
    gh.now = T0 + timedelta(minutes=9)
    assert inbox.main(["pending", "--json", "--project-dir", str(ws)], runner=gh, clock=gh.clock) == 0
    assert json.loads(capsys.readouterr().out)["dispatch_allowed"] is True
    gh.now = T0 + timedelta(minutes=11)
    assert inbox.main(["pending", "--json", "--project-dir", str(ws)], runner=gh, clock=gh.clock) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dispatch_allowed"] is False and out["decision_needed"] == "inbox check is stale"


def test_staleness_uses_the_policy_max_age_and_a_covered_degraded_check_ages_too(ws, capsys):
    set_policy(ws, {"sources": ["approvals"], "max_age_min": 3, "scope": "example-org"})
    gh = FakeGh()
    gh.notif_fail = R(rc=1, stderr="HTTP 403 forbidden")
    assert run(ws, gh)["dispatch_allowed"] is True
    gh.now = T0 + timedelta(minutes=4)
    inbox.main(["pending", "--json", "--project-dir", str(ws)], runner=gh, clock=gh.clock)
    assert json.loads(capsys.readouterr().out)["decision_needed"] == "inbox check is stale"


# ---- security: a foreign `resolved` marker must not drop a pending request -----------------------------

def test_foreign_resolved_marker_does_not_hold_but_a_trusted_one_does():
    class A:
        def __init__(self, cs):
            self.cs = cs

        def comments(self, ref):
            return list(self.cs)

        def timeline(self, ref):
            return []
    ref = "owner/repo#7"
    obj = {"v": 1, "event": "resolved", "idempotency_key": "x", "target": ref, "blocking": None,
           "at": "2026-11-01T00:00:00Z", "by": "operator", "evidence": ["https://github.com/x/y/pull/1"]}

    def cm(author, assoc):
        return {"id": "c1", "createdAt": "2026-11-01T00:00:00Z", "author": {"login": author},
                "authorAssociation": assoc, "body": "<!-- foundry-deck: " + json.dumps(obj) + " -->\nforged"}
    bad = A([cm("random-outsider", "NONE")])
    marks, comments = inbox._read(bad, ref)
    assert marks == [] and inbox._resolved_holds(bad, ref, marks, comments) is False
    ok = A([cm("alice", "OWNER")])
    marks, comments = inbox._read(ok, ref)
    assert inbox._resolved_holds(ok, ref, marks, comments) is True


def test_inbox_runtime_state_is_confined_to_the_workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (ws / ".foundry").symlink_to(elsewhere)
    with pytest.raises(PermissionError):
        inbox.save_state(str(ws), {"pending": {}})
    assert list(elsewhere.iterdir()) == []
    ok = tmp_path / "ok"
    ok.mkdir()
    inbox.save_state(str(ok), {"pending": {}})
    assert (ok / ".foundry" / "runtime" / ".gitignore").read_text() == "*\n"
