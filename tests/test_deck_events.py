"""gn-04: the deck's event vocabulary, GitHub record mapping, idempotent emit, and as-of reader.
No network: fixtures are GraphQL timelineItems-shaped JSON; the gh adapter is a stub."""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import foundry_deck_events as de  # noqa: E402

FX = ROOT / "tests" / "fixtures" / "gn_deck_events"
ISSUE = "example-org/example-repo#5"
END = "2026-12-31T00:00:00Z"


def fx(name):
    d = json.loads((FX / f"{name}.json").read_text())
    return d["timeline"], d["comments"]


def hist(name, as_of=END):
    tl, cm = fx(name)
    return de.parse(tl, cm, as_of)


def seq(h):
    return [(e.event, e.first) for e in h.events]


class Stub:
    def __init__(self, comments=(), tl=()):
        self.cs = list(comments)
        self.tl = list(tl)
        self.posted = []

    def comments(self, issue):
        return list(self.cs)

    def post_comment(self, issue, body):
        self.posted.append((issue, body))
        self.cs.append({"id": f"IC_new{len(self.posted)}", "createdAt": "2026-11-01T00:00:00Z",
                        "author": {"login": "alice"}, "body": body})

    def timeline(self, issue):
        return list(self.tl)


# ---- vocabulary -------------------------------------------------------------------------------------

def test_vocabulary_is_the_fifteen_and_mapping_covers_each():
    assert len(de.EVENTS) == 15 and len(set(de.EVENTS)) == 15
    assert set(de.MAPPING) == set(de.EVENTS)
    for name, m in de.MAPPING.items():
        assert m["native"] or m["marker"], name
    assert de.MAPPING["blocked"]["native"] == ["BlockedByAddedEvent", "BlockingAddedEvent"]


@pytest.mark.parametrize("name", ["bogus", "ready", "blocked", "reopened", "withdrawn", "cancelled", "duplicate"])
def test_emit_rejects_unknown_and_native_only(name):
    stub = Stub()
    with pytest.raises(ValueError):
        de.emit(ISSUE, name, {"by": "alice"}, adapter=stub)
    assert stub.posted == []


# ---- emit -------------------------------------------------------------------------------------------

def test_emit_posts_one_comment_with_all_eight_fields_and_repeat_is_idempotent():
    stub = Stub()
    p = {"by": "alice", "target": "bob", "evidence": [f"{ISSUE}"]}
    e1 = de.emit(ISSUE, "handoff", p, adapter=stub, clock=lambda: de._dt("2026-11-01T00:00:00Z"))
    assert len(stub.posted) == 1
    body = stub.posted[0][1]
    lines = body.rstrip("\n").split("\n")
    assert len(lines) == 2 and not lines[1].startswith("<!--")
    m = re.fullmatch(r"<!-- foundry-deck: (\{.*\}) -->", lines[0])
    obj = json.loads(m.group(1))
    assert set(obj) >= set(de.MARKER_FIELDS) and obj["v"] == 1
    assert obj["event"] == "handoff" and obj["by"] == "alice" and obj["evidence"] == [ISSUE]
    e2 = de.emit(ISSUE, "handoff", dict(p), adapter=stub)
    assert len(stub.posted) == 1
    assert e2.idempotency_key == e1.idempotency_key == obj["idempotency_key"]
    # a defining-field change is a different event
    de.emit(ISSUE, "handoff", {**p, "target": "carol"}, adapter=stub)
    assert len(stub.posted) == 2


def test_emitted_marker_round_trips_through_parse_and_cannot_close_the_comment():
    stub = Stub()
    de.emit(ISSUE, "ack", {"by": "a-->b", "target": "bob", "note": "x --> y <!-- z"}, adapter=stub)
    body = stub.posted[0][1]
    assert body.count("-->") == 1 and body.count("<!--") == 1
    h = de.parse([], stub.cs, END)
    assert [(e.event, e.by, e.target) for e in h.events] == [("ack", "a-->b", "bob")]


@pytest.mark.parametrize("ev", [
    ["/home/alice/notes.md"], ["./out.txt"], ["notes.txt"], ["file:///etc/passwd"], ["ok-ref-no", "x"], [],
])
@pytest.mark.parametrize("event", ["review_ready", "handoff", "resolved", "accepted"])
def test_bad_or_empty_evidence_rejected_without_posting(event, ev):
    stub = Stub()
    with pytest.raises(ValueError):
        de.emit(ISSUE, event, {"by": "alice", "evidence": ev}, adapter=stub)
    assert stub.posted == [] and stub.cs == []


@pytest.mark.parametrize("ref", ["https://example.org/pr/1", "example-org/example-repo#12", "0123abc", "a1" * 20])
def test_shared_references_accepted(ref):
    assert de.is_shared_reference(ref)
    stub = Stub()
    de.emit(ISSUE, "review_ready", {"by": "alice", "evidence": [ref]}, adapter=stub)
    assert len(stub.posted) == 1


def test_emit_requires_by_and_issue_shape():
    with pytest.raises(ValueError):
        de.emit(ISSUE, "ack", {}, adapter=Stub())
    with pytest.raises(ValueError):
        de.emit("not-an-issue", "ack", {"by": "alice"}, adapter=Stub())


# ---- parse: ready / start / first -------------------------------------------------------------------

def test_ready_backlog_ready_start_has_one_first_ready_and_first_start():
    h = hist("ready_backlog_ready_start")
    assert seq(h) == [("ready", True), ("ready", False), ("start", True)]
    assert [e.by for e in h.events] == ["alice", "alice", "bob"]
    assert [e.at for e in h.events] == ["2026-10-01T10:00:00Z", "2026-10-03T10:00:00Z", "2026-10-04T10:00:00Z"]


def test_cutoff_excludes_later_records_and_orders_by_time():
    h = hist("ready_backlog_ready_start", "2026-10-02T10:00:00Z")
    assert seq(h) == [("ready", True)]
    assert hist("ready_backlog_ready_start", "2026-09-30T00:00:00Z").events == []


def test_withdrawn_only_after_first_start_and_restart_is_not_first():
    def s(day, status):
        return {"__typename": "ProjectV2ItemStatusChangedEvent", "createdAt": f"2026-10-{day:02d}T00:00:00Z",
                "actor": {"login": "alice"}, "status": status}
    tl = [s(1, "Backlog"), s(2, "In progress"), s(3, "Backlog"), s(4, "In progress")]
    h = de.parse(tl, [], END)
    assert seq(h) == [("start", True), ("withdrawn", False), ("start", False)]


def test_label_status_and_marker_start_dedupe():
    tl = [{"__typename": "LabeledEvent", "createdAt": "2026-10-01T00:00:00Z", "actor": {"login": "alice"},
           "label": {"name": "status:ready"}},
          {"__typename": "ProjectV2ItemStatusChangedEvent", "createdAt": "2026-10-01T00:00:01Z",
           "actor": {"login": "alice"}, "status": "Ready"},
          {"__typename": "ProjectV2ItemStatusChangedEvent", "createdAt": "2026-10-03T00:00:00Z",
           "actor": {"login": "bob"}, "status": "In progress"}]
    stub = Stub()
    de.emit(ISSUE, "start", {"by": "bob", "at": "2026-10-02T00:00:00Z"}, adapter=stub)
    stub.cs[0]["createdAt"] = "2026-10-02T00:00:00Z"
    h = de.parse(tl, stub.cs, END)
    assert seq(h) == [("ready", True), ("start", True)]
    assert h.events[1].source == "marker"   # the earlier of marker / status wins


# ---- parse: blockers --------------------------------------------------------------------------------

def test_blocker_ack_validity_and_ordinary_ack_never_satisfies():
    h = hist("blockers")
    assert [(e.event, e.invalid) for e in h.events] == [
        ("ack", None), ("blocker_ack", "before_blocking"), ("blocked", None), ("blocker_ack", None)]
    assert h.unacknowledged == []
    # the target of the blocked record is the blocked issue's reference
    assert h.events[2].target == "example-org/example-repo#7" and h.events[2].by == "bob"


def test_blocker_acknowledged_only_by_ordinary_ack_stays_unacknowledged():
    h = hist("blockers_unacked")
    assert [e.event for e in h.events] == ["blocked", "ack"]
    assert h.unacknowledged == ["example-org/example-repo#9"]


def test_blocker_ack_with_no_blocked_record_is_invalid():
    tl, cm = fx("blockers")
    h = de.parse([], cm, END)
    assert [e.invalid for e in h.events if e.event == "blocker_ack"] == ["before_blocking", "before_blocking"]


def test_ack_before_cutoff_of_blocking_is_not_visible_as_valid():
    h = hist("blockers", "2026-10-03T12:00:00Z")   # blocked seen, valid ack (day 4) not yet
    assert h.unacknowledged == ["example-org/example-repo#7"]


# ---- parse: assignment ------------------------------------------------------------------------------

def test_assignment_intervals_at_two_cutoffs():
    early = hist("reassignment", "2026-10-03T00:00:00Z").intervals
    assert early == [{"assignee": "alice", "start": "2026-10-01T10:00:00Z", "end": None}]
    late = hist("reassignment").intervals
    assert late == [{"assignee": "alice", "start": "2026-10-01T10:00:00Z", "end": "2026-10-05T10:00:00Z"},
                    {"assignee": "bob", "start": "2026-10-05T10:00:00Z", "end": None}]


# ---- parse: accept / reopen / close -----------------------------------------------------------------

def test_accept_reopen_reaccept_is_not_first():
    h = hist("accept_reopen")
    assert seq(h) == [("start", True), ("accepted", True), ("reopened", False), ("accepted", False)]
    assert h.events[1].evidence == ["example-org/example-repo#12"]


def test_close_not_planned_is_cancelled():
    h = hist("close_not_planned")
    assert [e.event for e in h.events] == ["cancelled"] and h.closures == []


def test_close_duplicate_yields_single_duplicate_event():
    h = hist("close_duplicate")
    assert [e.event for e in h.events] == ["duplicate"]


def test_completed_close_is_a_closure_not_an_acceptance():
    h = hist("close_completed")
    assert [e.event for e in h.events] == ["start"]
    assert [(c.state_reason, c.by, c.at) for c in h.closures] == [("completed", "bob", "2026-10-03T10:00:00Z")]
    assert not any(e.event == "accepted" for e in h.events)


# ---- parse: unparsed --------------------------------------------------------------------------------

def test_unknown_version_and_malformed_markers_are_returned_unparsed_with_comment_id():
    h = hist("bad_markers")
    assert [(e.event, e.detail, e.comment_id) for e in h.events] == [
        ("unparsed", "unknown_version", "IC_1"), ("unparsed", "malformed_json", "IC_2"), ("ack", None, "IC_3")]
    assert [e.comment_id for e in h.unparsed] == ["IC_1", "IC_2"]


def test_marker_for_native_only_event_or_missing_field_is_unparsed():
    def c(i, obj):
        return {"id": i, "createdAt": "2026-10-01T00:00:00Z", "author": {"login": "x"},
                "body": f"<!-- foundry-deck: {json.dumps(obj)} -->\nline\n"}
    base = {"v": 1, "event": "ready", "idempotency_key": "k", "target": None, "blocking": None,
            "at": "x", "by": "x", "evidence": []}
    nof = {k: v for k, v in base.items() if k != "by"}
    h = de.parse([], [c("a", base), c("b", nof), c("d", {**base, "event": "ack", "v": True})], END)
    assert [(e.event, e.detail) for e in h.events] == [
        ("unparsed", "event_not_markable"), ("unparsed", "missing_fields"), ("unparsed", "unknown_version")]


# ---- purity / adapter confinement -------------------------------------------------------------------

def test_parse_is_pure_no_subprocess_no_clock(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("subprocess")))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("subprocess")))
    tl, cm = fx("blockers")
    a = de.parse(tl, cm, END)
    assert [e.as_dict() for e in a.events] == [e.as_dict() for e in de.parse(tl, cm, END).events]


def test_subprocess_only_inside_gh_adapter():
    import ast
    tree = ast.parse((ROOT / "scripts" / "foundry_deck_events.py").read_text())
    owners = set()
    for cls in [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))]:
        for n in ast.walk(cls):
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "subprocess" \
                    and n.attr in ("run", "Popen", "call", "check_output", "check_call"):
                owners.add(cls.name)
    assert owners == {"GhAdapter"}


def test_naive_as_of_rejected():
    with pytest.raises(ValueError):
        de.parse([], [], "2026-10-01T00:00:00")


def test_cli_parse_uses_adapter(monkeypatch, capsys):
    tl, cm = fx("close_not_planned")

    class A(Stub):
        def timeline(self, issue):
            return tl
    monkeypatch.setattr(de, "GhAdapter", lambda: A(cm))
    assert de.main(["parse", ISSUE, "--as-of", END]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [e["event"] for e in out["events"]] == ["cancelled"]
    assert de.main(["emit", ISSUE, "ready", "--payload", "{}"]) == 2


# ---- review round 1 ---------------------------------------------------------------------------------

def _status(day, status, hour=0):
    return {"__typename": "ProjectV2ItemStatusChangedEvent", "createdAt": f"2026-10-{day:02d}T{hour:02d}:00:00Z",
            "actor": {"login": "alice"}, "status": status}


def _clock(day):
    return lambda: de._dt(f"2026-11-{day:02d}T00:00:00Z")


def test_restart_after_withdraw_posts_a_second_start_marker():
    tl = [_status(1, "In progress")]
    stub = Stub(tl=tl)
    de.emit(ISSUE, "start", {"by": "bob"}, adapter=stub, clock=_clock(1))
    de.emit(ISSUE, "start", {"by": "bob"}, adapter=stub, clock=_clock(1))
    assert len(stub.posted) == 1
    stub.tl += [_status(2, "Backlog")]
    de.emit(ISSUE, "start", {"by": "bob"}, adapter=stub, clock=_clock(2))
    assert len(stub.posted) == 2


def test_reaccept_after_reopen_posts():
    stub = Stub(tl=[])
    p = {"by": "bob", "target": "x", "evidence": [ISSUE]}
    de.emit(ISSUE, "accepted", p, adapter=stub, clock=_clock(1))
    de.emit(ISSUE, "accepted", p, adapter=stub, clock=_clock(1))
    assert len(stub.posted) == 1
    stub.tl.append({"__typename": "ReopenedEvent", "createdAt": "2026-10-05T00:00:00Z", "actor": {"login": "a"}})
    de.emit(ISSUE, "accepted", p, adapter=stub, clock=_clock(2))
    assert len(stub.posted) == 2


OTHER = "example-org/example-repo#7"


def _blocked(day, hour=0):
    return {"__typename": "BlockedByAddedEvent", "createdAt": f"2026-10-{day:02d}T{hour:02d}:00:00Z",
            "actor": {"login": "bob"}, "blockingIssue": {"number": 7, "repository": {"nameWithOwner": "example-org/example-repo"}}}


def test_reblock_then_ack_posts_a_second_blocker_ack():
    stub = Stub(tl=[_blocked(1)])
    p = {"by": "alice", "target": OTHER}
    de.emit(ISSUE, "blocker_ack", p, adapter=stub, clock=_clock(1))
    de.emit(ISSUE, "blocker_ack", p, adapter=stub, clock=_clock(1))
    assert len(stub.posted) == 1
    stub.tl.append(_blocked(3))
    de.emit(ISSUE, "blocker_ack", p, adapter=stub, clock=_clock(3))
    assert len(stub.posted) == 2


def _ack_marker(i, day, key):
    obj = {"v": 1, "event": "blocker_ack", "idempotency_key": key, "target": OTHER, "blocking": OTHER,
           "at": "x", "by": "alice", "evidence": []}
    return {"id": i, "createdAt": f"2026-10-{day:02d}T12:00:00Z", "author": {"login": "alice"},
            "body": f"<!-- foundry-deck: {json.dumps(obj)} -->\nack\n"}


def test_blocked_ack_reblocked_is_unacknowledged_and_flag_agrees():
    tl = [_blocked(1), _blocked(3)]
    h = de.parse(tl, [_ack_marker("c1", 2, "k1")], END)
    assert [(e.event, e.invalid) for e in h.events] == [("blocked", None), ("blocker_ack", None), ("blocked", None)]
    assert h.unacknowledged == [OTHER]
    h2 = de.parse(tl, [_ack_marker("c1", 2, "k1"), _ack_marker("c2", 4, "k2")], END)
    assert h2.unacknowledged == []


def _blocking(day, typ="BlockingAddedEvent"):
    return {"__typename": typ, "createdAt": f"2026-10-{day:02d}T00:00:00Z", "actor": {"login": "a"},
            "blockedIssue": {"number": 7, "repository": {"nameWithOwner": "example-org/example-repo"}}}


def test_blocking_added_event_records_blocking_relation_not_is_blocked():
    h = de.parse([_blocking(1)], [], END)
    assert [(e.event, e.relation, e.target, e.blocking) for e in h.events] == [
        ("blocked", "blocking", OTHER, OTHER)]
    assert h.unacknowledged == [OTHER]
    assert de.is_blocked(h) is False
    assert len(de.EVENTS) == 15


def test_blocked_by_event_keeps_default_relation_and_is_blocked():
    h = de.parse([_blocked(1)], [], END)
    assert h.events[0].relation == "blocked_by" and de.is_blocked(h) is True


def test_inbox_shaped_blocker_ack_on_blocking_episode_is_valid():
    h = de.parse([_blocking(1)], [_ack_marker("c1", 2, "k1")], END)
    assert [(e.event, e.invalid) for e in h.events] == [("blocked", None), ("blocker_ack", None)]
    assert h.unacknowledged == []


def test_blocking_removed_event_closes_the_episode():
    h = de.parse([_blocking(1), _blocking(3, "BlockingRemovedEvent")], [], END)
    assert h.unacknowledged == [] and h.open_blocks == {}


def test_parse_dedupes_same_key_first_by_time_wins():
    a, b = _ack_marker("c1", 2, "same"), _ack_marker("c2", 3, "same")
    h = de.parse([_blocked(1)], [b, a], END)
    assert [e.comment_id for e in h.events if e.event == "blocker_ack"] == ["c1"]


def test_quoted_marker_is_not_live():
    c = _ack_marker("c1", 2, "k")
    c["body"] = "\n".join("> " + ln for ln in c["body"].split("\n"))
    assert de.parse([], [c], END).events == []
    stub = Stub([c])
    de.emit(ISSUE, "ack", {"by": "alice", "target": "bob", "key": "k"}, adapter=stub)
    assert len(stub.posted) == 1


def test_same_key_different_event_is_not_a_dedupe_hit():
    stub = Stub([_ack_marker("c1", 2, "k")])
    de.emit(ISSUE, "ack", {"by": "alice", "target": "bob", "key": "k"}, adapter=stub)
    assert len(stub.posted) == 1


def test_hand_edited_marker_with_same_key_counts_as_present():
    c = _ack_marker("c1", 2, "k")
    c["body"] = c["body"].replace('"v": 1', '"v": 9')
    stub = Stub([c])
    ex = de.emit(ISSUE, "blocker_ack", {"by": "alice", "target": OTHER, "key": "k"}, adapter=stub)
    assert stub.posted == [] and ex.event == "unparsed" and ex.idempotency_key == "k"
    c2 = _ack_marker("c2", 2, "k2")
    c2["body"] = c2["body"].replace("}", "", 1)   # malformed JSON
    stub = Stub([c2])
    de.emit(ISSUE, "blocker_ack", {"by": "alice", "target": OTHER, "key": "k2"}, adapter=stub)
    assert stub.posted == []


def _gql(nodes, nxt=None):
    return json.dumps({"data": {"repository": {"issue": {"timelineItems": {
        "pageInfo": {"hasNextPage": nxt is not None, "endCursor": nxt}, "nodes": nodes}}}}})


class _R:
    def __init__(self, out, rc=0):
        self.stdout, self.stderr, self.returncode = out, "", rc


def test_gh_adapter_paginates_and_raises_on_errors_and_null_issue(monkeypatch):
    import subprocess
    calls = []
    pages = [_gql([{"__typename": "ReopenedEvent"}], "CUR1"), _gql([{"__typename": "ClosedEvent"}])]

    def fake(args, **kw):
        calls.append(args)
        return _R(pages[len(calls) - 1])
    monkeypatch.setattr(subprocess, "run", fake)
    nodes = de.GhAdapter().timeline(ISSUE)
    assert [n["__typename"] for n in nodes] == ["ReopenedEvent", "ClosedEvent"]
    assert "after=CUR1" in calls[1] and not any(a.startswith("after=") for a in calls[0])
    monkeypatch.setattr(subprocess, "run", lambda a, **k: _R(json.dumps({"errors": [{"message": "boom"}]})))
    with pytest.raises(RuntimeError):
        de.GhAdapter().timeline(ISSUE)
    monkeypatch.setattr(subprocess, "run", lambda a, **k: _R(json.dumps({"data": {"repository": {"issue": None}}})))
    with pytest.raises(RuntimeError):
        de.GhAdapter().timeline(ISSUE)


@pytest.mark.parametrize("bad", ["abcdefabcdef", "1234567", "a1b2c3", "a1" * 33, "0123abc\n"])
def test_sha_needs_digit_and_letter_and_bounded_length(bad):
    assert not de.is_shared_reference(bad)
    assert de.is_shared_reference("a1b2c3d") and de.is_shared_reference("a1" * 32)


@pytest.mark.parametrize("bad", ["../x#1", "a/../#1", "a/b/c#1", "a/b#1\n", "./b#1"])
def test_issue_ref_rejects_path_shaped(bad):
    assert not de.is_shared_reference(bad)
    with pytest.raises(ValueError):
        de.emit(bad, "ack", {"by": "a"}, adapter=Stub())


def test_marker_escapes_lt():
    ev = de.Event(event="ack", at="2026-10-01T00:00:00Z", by="<!--x", idempotency_key="k")
    body = de.render_marker(ev, "l")
    assert body.count("<!--") == 1 and "\\u003c" in body
