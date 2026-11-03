"""gn-08: the owner-aware queue — read / next / claim / reconcile and the local lead lease.

Every `gh` call goes through the one injectable runner; here a stub (FakeGh) answers from synthetic
fixtures (tests/fixtures/gn_owned_queue/), so nothing touches the network. Assertions are on parsed
structures, never on substrings of joined text."""
import copy
import importlib.util
import io
import json
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIX = json.loads((ROOT / "tests" / "fixtures" / "gn_owned_queue" / "queue.json").read_text())
sys.path.insert(0, str(SCRIPTS))

_spec = importlib.util.spec_from_file_location("foundry_ticket_oq", SCRIPTS / "foundry-ticket.py")
ft = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ft)

NOW = 1_800_000_000.0   # fixed clock
ACC = "## Acceptance criteria\n- the widget renders\n"


class FakeGh:
    def __init__(self, issues=None, login="alice", project=None, priority_options=None):
        self.issues = copy.deepcopy(issues if issues is not None else FIX["issues"])
        self.login = login
        self.project = project
        self.priority_options = priority_options
        self.comments = []        # posted/served issue comments (all issues; key = "repo#n" in c["_issue"])
        self.timelines = {}       # "repo#n" -> nodes
        self.fail_list_page = None   # fail on the Nth list call (0-based)
        self.fail_all = False
        self.calls = []
        self._list_calls = 0
        self.page_size = 50

    def node(self, n):
        return next(i for i in self.issues if i["number"] == n)

    def __call__(self, args, stdin=None):
        self.calls.append(list(args))
        if self.fail_all:
            raise ft.GhError("gh failed: network down")
        if args[:2] == ["api", "user"]:
            return json.dumps({"login": self.login})
        if args[:2] == ["api", "graphql"]:
            q = next(a for a in args if a.startswith("query="))[6:]
            v = {}
            for k, val in zip(args, args[1:]):
                if k in ("-f", "-F") and "=" in val and not val.startswith("query="):
                    a, b = val.split("=", 1)
                    v[a] = b
            if "timelineItems" in q:
                key = f"{v['owner']}/{v['repo']}#{v['number']}"
                return json.dumps({"data": {"repository": {"issue": {"timelineItems": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": self.timelines.get(key, [])}}}}})
            if "repositoryOwner" in q:
                if self.priority_options is None:
                    return json.dumps({"errors": [{"message": "project unreachable"}], "data": None})
                return json.dumps({"data": {"repositoryOwner": {"projectV2": {"field": {
                    "options": [{"name": n} for n in self.priority_options]}}}}})
            if "issues(first:50" in q:
                n = self._list_calls
                self._list_calls += 1
                if self.fail_list_page == n:
                    raise ft.GhError("gh api failed: HTTP 502")
                after = int(v.get("after") or 0)
                open_ = [i for i in self.issues if i["state"] == "OPEN"]
                chunk = open_[after: after + self.page_size]
                more = after + self.page_size < len(open_)
                return json.dumps({"data": {"repository": {"issues": {
                    "pageInfo": {"hasNextPage": more, "endCursor": str(after + self.page_size)}, "nodes": chunk}}}})
            if "issue(number:" in q:
                try:
                    node = self.node(int(v["number"]))
                except StopIteration:
                    node = None
                return json.dumps({"data": {"repository": {"issue": node}}})
        if args[:2] == ["issue", "view"] and "comments" in args:
            key = f"{args[args.index('-R') + 1]}#{args[2]}"
            return json.dumps({"comments": [c for c in self.comments if c["_issue"] == key]})
        if args[:2] == ["issue", "comment"]:
            key = f"{args[args.index('-R') + 1]}#{args[2]}"
            self.comments.append({"_issue": key, "id": f"c{len(self.comments)}", "body": stdin,
                                  "createdAt": "2026-11-03T00:00:00Z", "author": {"login": self.login}})
            return ""
        raise AssertionError(f"unexpected gh call: {args}")

    def markers(self):
        out = []
        for c in self.comments:
            out += [e for e in ft.deck._markers_in(c) if e.event != "unparsed"]
        return out


def label_status(node, status):
    node["labels"]["nodes"] = [l for l in node["labels"]["nodes"] if not l["name"].startswith("status:")]
    node["labels"]["nodes"].append({"name": f"status:{status}"})


def mk_issue(n, assignees=("alice",), labels=("status:ready", "repo:app"), body=ACC, state="OPEN", reason=None, blocked=()):
    return {"number": n, "title": f"Task {n}", "body": body, "url": f"https://github.com/example-org/app/issues/{n}",
            "state": state, "stateReason": reason, "author": {"login": "carol"},
            "assignees": {"totalCount": len(assignees), "nodes": [{"login": a} for a in assignees]},
            "labels": {"totalCount": len(labels), "nodes": [{"name": l} for l in labels]},
            "blockedBy": {"totalCount": len(blocked), "nodes": [
                {"number": b, "state": "OPEN", "repository": {"nameWithOwner": "example-org/app"}} for b in blocked]},
            "blocking": {"totalCount": 0, "nodes": []}, "parent": None,
            "closedByPullRequestsReferences": {"nodes": []}, "projectItems": {"totalCount": 0, "nodes": []}}


@pytest.fixture
def ws(tmp_path, monkeypatch):
    r = tmp_path / "ws"
    r.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    (r / ".claude").mkdir()
    (r / ".claude" / "foundry-project.json").write_text(json.dumps(FIX["config"]))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(r))
    return r


def set_project(ws, number=7):
    cfg = copy.deepcopy(FIX["config"])
    cfg["github_scope"]["project_number"] = number
    (ws / ".claude" / "foundry-project.json").write_text(json.dumps(cfg))


def run_cli(argv, gh, now=NOW):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = ft.main(argv, run=gh, now=now)
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 2
    return rc, out.getvalue(), err.getvalue()


def nxt(ws, gh):
    rc, out, _ = run_cli(["next", "--json"], gh)
    return rc, json.loads(out)


# ---- AC1: the owner-aware read ------------------------------------------------------------------------

def test_read_issue_returns_owner_fields_and_keeps_the_old_ones():
    gh = FakeGh()
    n = gh.node(1)
    n["blockedBy"] = {"totalCount": 1, "nodes": [{"number": 9, "state": "OPEN", "repository": {"nameWithOwner": "example-org/app"}}]}
    n["parent"] = {"number": 3, "repository": {"nameWithOwner": "example-org/app"}}
    n["closedByPullRequestsReferences"] = {"nodes": [{"number": 40, "state": "OPEN", "url": "https://github.com/example-org/app/pull/40"}]}
    n["body"] = ACC + "\n## Done means\n```\nAPI_TOKEN=abcd1234 python3 -m pytest -q\n```\n"
    n["author"] = {"login": "alice"}   # the authenticated operator: trusted
    rec = ft.read_issue(gh, "example-org/app", 1)
    for k in ("number", "title", "body", "url", "author", "state"):
        assert k in rec
    assert rec["assignees"] == ["alice"]
    assert "status:ready" in rec["labels"] and rec["status"] == "ready" and rec["status_source"] == "label"
    assert rec["blocked_by"] == [{"number": 9, "repo": "example-org/app", "state": "OPEN"}]
    assert rec["blocking"] == [] and rec["parent"] == {"number": 3, "repo": "example-org/app"}
    assert rec["linked_prs"][0]["number"] == 40
    # trust + digest + redaction as before
    assert rec["done_trusted"] is True
    assert rec["done"] == "API_TOKEN=<redacted> python3 -m pytest -q"
    assert rec["done_sha256"] == ft.store.command_digest("API_TOKEN=abcd1234 python3 -m pytest -q")


def test_read_issue_withholds_done_from_an_untrusted_author():
    gh = FakeGh()
    gh.node(1)["body"] = "## Done means\n```\nrm -rf x\n```\n"
    orig = gh.__call__

    def perm(args, stdin=None):
        if args[:1] == ["api"] and "collaborators" in args[1]:
            return json.dumps({"permission": "read"})
        return orig(args, stdin)
    rec = ft.read_issue(perm, "example-org/app", 1)
    assert rec["done"] == "" and rec["done_trusted"] is False and "read" in rec["done_reason"]


def test_status_comes_from_the_project_when_configured():
    gh = FakeGh()
    n = gh.node(1)
    n["projectItems"] = {"totalCount": 1, "nodes": [{"project": {"number": 7}, "status": {"name": "In progress"},
                                                     "priority": {"name": "P1"}}]}
    rec = ft.read_issue(gh, "example-org/app", 1, project_number=7)
    assert (rec["status"], rec["status_source"], rec["priority"]) == ("in progress", "project", "P1")


# ---- AC2/AC3: next -------------------------------------------------------------------------------------

def test_next_picks_highest_priority_ready_and_lists_every_skip_with_reason(ws):
    rc, res = nxt(ws, FakeGh())
    assert rc == 0 and res["queue"] == "available" and res["mode"] == "ready"
    assert res["selected"]["number"] == 2 and res["selected"]["repo_key"] == "app"   # priority:p0 beats p1
    assert {s["number"]: s["reason"] for s in res["skipped"]} == {
        3: "unassigned", 4: "other-owner", 5: "backlog", 6: "blocked", 7: "no-acceptance", 8: "no-repo-key"}
    assert {s["reason"] for s in res["skipped"]} <= set(ft.SKIP_REASONS)


def test_resume_of_in_progress_beats_a_new_ready(ws):
    gh = FakeGh()
    label_status(gh.node(1), "in progress")
    rc, res = nxt(ws, gh)
    assert (res["mode"], res["selected"]["number"]) == ("resume", 1)
    gh2 = FakeGh()
    label_status(gh2.node(1), "review")
    assert nxt(ws, gh2)[1]["selected"]["number"] == 1


def test_resume_is_dropped_when_reconcile_says_stop(ws):
    gh = FakeGh()
    label_status(gh.node(1), "in progress")
    ft.save_claim(ws, "example-org/app#1", {"issue": "example-org/app#1", "number": 1, "repo": "example-org/app",
                                              "body_sha256": "0" * 64})
    rc, res = nxt(ws, gh)
    assert res["selected"]["number"] == 2     # the Ready one, since #1's acceptance changed
    assert {"number": 1, "reason": "stop-dispatch:acceptance_changed"}.items() <= next(
        s for s in res["skipped"] if s["number"] == 1).items()


def test_label_fallback_and_project_mode_priority(ws):
    set_project(ws)
    gh = FakeGh(priority_options=["P0", "P1", "P2"])
    for n, (st, pr) in {1: ("Ready", "P2"), 2: ("Ready", "P1"), 5: ("Backlog", "P0")}.items():
        gh.node(n)["projectItems"] = {"totalCount": 1, "nodes": [{"project": {"number": 7}, "status": {"name": st},
                                                                  "priority": {"name": pr}}]}
    rc, res = nxt(ws, gh)
    assert res["selected"]["number"] == 2
    assert next(s for s in res["skipped"] if s["number"] == 5)["reason"] == "backlog"
    # project mode ignores status:* labels: #8 etc have no project item -> backlog
    assert next(s for s in res["skipped"] if s["number"] == 7)["reason"] == "backlog"


def test_ties_go_to_oldest_ready_then_lowest_number(ws):
    gh = FakeGh(issues=[mk_issue(10, labels=("status:ready", "priority:p1", "repo:app")),
                        mk_issue(11, labels=("status:ready", "priority:p1", "repo:app"))])
    gh.timelines = {
        "example-org/app#10": [{"__typename": "LabeledEvent", "createdAt": "2026-11-02T10:00:00Z", "actor": {"login": "carol"},
                                "label": {"name": "status:ready"}}],
        "example-org/app#11": [{"__typename": "LabeledEvent", "createdAt": "2026-11-01T10:00:00Z", "actor": {"login": "carol"},
                                "label": {"name": "status:ready"}}]}
    assert nxt(ws, gh)[1]["selected"]["number"] == 11      # Ready earlier
    gh.timelines["example-org/app#11"][0]["createdAt"] = "2026-11-02T10:00:00Z"
    assert nxt(ws, gh)[1]["selected"]["number"] == 10      # same instant: lowest number


@pytest.mark.parametrize("how", ["page", "all", "project"])
def test_unreadable_queue_selects_nothing_and_exits_nonzero(ws, how):
    if how == "page":
        gh = FakeGh(issues=[mk_issue(n) for n in range(1, 6)])
        gh.page_size, gh.fail_list_page = 2, 1
    elif how == "all":
        gh = FakeGh()
        gh.fail_all = True
    else:
        set_project(ws)
        gh = FakeGh(priority_options=None)
    claims = {"example-org/app#2": {"issue": "x", "number": 2, "repo": "example-org/app", "body_sha256": "x"}}
    ft.save_claim(ws, "example-org/app#2", claims["example-org/app#2"])   # a cache must never rescue the read
    rc, res = nxt(ws, gh)
    assert rc != 0 and res["queue"] == "unavailable" and res["selected"] is None and res["cause"]


def test_next_cli_help_and_text_mode(ws):
    p = subprocess.run([sys.executable, str(SCRIPTS / "foundry-ticket.py"), "next", "--help"], capture_output=True, text=True)
    assert p.returncode == 0 and "--json" in p.stdout
    rc, out, _ = run_cli(["next"], FakeGh())
    assert rc == 0 and "example-org/app#2" in out


# ---- AC4: claim ----------------------------------------------------------------------------------------

def test_claim_records_start_and_the_local_record_with_the_body_hash(ws):
    gh = FakeGh()
    rc, out, err = run_cli(["claim", "2"], gh)
    assert rc == 0, err
    evs = [e for e in gh.markers() if e.event == "start"]
    assert len(evs) == 1 and evs[0].by == "alice"
    rec = ft.load_claims(ws)["example-org/app#2"]
    assert rec["body_sha256"] == ft.store.command_digest(gh.node(2)["body"]) and rec["exception"] is None
    # idempotent: a second claim posts no second marker
    assert run_cli(["claim", "2"], gh)[0] == 0
    assert len([e for e in gh.markers() if e.event == "start"]) == 1


@pytest.mark.parametrize("n", [3, 4])
def test_claim_refused_for_unassigned_or_other_owner_and_writes_nothing(ws, n):
    gh = FakeGh()
    rc, _, err = run_cli(["claim", str(n)], gh)
    assert rc != 0 and gh.comments == [] and ft.load_claims(ws) == {}
    assert err


def test_claim_at_wip_limit_counts_review_and_blocked_then_allows_a_recorded_exception(ws):
    gh = FakeGh()
    label_status(gh.node(5), "review")                       # Review counts
    gh.node(6)["labels"]["nodes"] = [{"name": "status:in progress"}, {"name": "repo:app"}]   # started + blocked counts
    assert ft.wip_count(ft.read_queue(gh, ft.load_config(ws)), "alice", {}) == 2
    rc, _, err = run_cli(["claim", "2"], gh)
    assert rc != 0 and gh.comments == [] and ft.load_claims(ws) == {}
    rc, _, err = run_cli(["claim", "2", "--allow-exception", "customer outage"], gh)
    assert rc == 0, err
    assert ft.load_claims(ws)["example-org/app#2"]["exception"] == "customer outage"
    start = next(c for c in gh.comments if "foundry-deck" in c["body"])["body"]
    assert "customer outage" in start.splitlines()[1]        # the reason rides the human line of the start record
    assert [e.event for e in gh.markers()] == ["start"]


def test_claim_counts_a_blocked_started_issue_by_its_claim_record(ws):
    gh = FakeGh()
    label_status(gh.node(5), "review")
    ft.save_claim(ws, "example-org/app#6", {"issue": "example-org/app#6", "number": 6, "repo": "example-org/app"})
    issues = ft.read_queue(gh, ft.load_config(ws))     # #6: Ready but blocked, and claimed before
    assert ft.wip_count(issues, "alice", ft.load_claims(ws)) == 2


# ---- AC5/AC6: reconcile --------------------------------------------------------------------------------

def claimed(ws, gh, n=1):
    label_status(gh.node(n), "in progress")
    ft.save_claim(ws, f"example-org/app#{n}", {"issue": f"example-org/app#{n}", "number": n, "repo": "example-org/app",
                                                  "body_sha256": ft.store.command_digest(gh.node(n)["body"])})


def rec_one(ws, gh, n=1):
    rc, out, _ = run_cli(["reconcile", str(n), "--json"], gh)
    return rc, json.loads(out)["results"][0]


def test_reconcile_authorized_only_from_current_github_state(ws):
    gh = FakeGh()
    claimed(ws, gh)
    rc, r = rec_one(ws, gh)
    assert (rc, r["stop_dispatch"], r["reason"]) == (0, False, "authorized")


def test_reconcile_reassigned(ws):
    gh = FakeGh()
    claimed(ws, gh)
    gh.node(1)["assignees"] = {"totalCount": 1, "nodes": [{"login": "bob"}]}
    assert rec_one(ws, gh)[1]["reason"] == "reassigned"
    gh.node(1)["assignees"] = {"totalCount": 0, "nodes": []}
    assert rec_one(ws, gh)[1]["reason"] == "reassigned"


@pytest.mark.parametrize("reason,expected", [("NOT_PLANNED", "cancelled"), ("DUPLICATE", "duplicate")])
def test_reconcile_closed_reasons(ws, reason, expected):
    gh = FakeGh()
    claimed(ws, gh)
    gh.node(1)["state"], gh.node(1)["stateReason"] = "CLOSED", reason
    rc, r = rec_one(ws, gh)
    assert (r["stop_dispatch"], r["reason"]) == (True, expected)


def test_reconcile_withdrawn_records_the_actor(ws):
    gh = FakeGh()
    claimed(ws, gh)
    label_status(gh.node(1), "backlog")
    gh.timelines["example-org/app#1"] = [
        {"__typename": "LabeledEvent", "createdAt": "2026-11-01T10:00:00Z", "actor": {"login": "alice"}, "label": {"name": "status:in progress"}},
        {"__typename": "LabeledEvent", "createdAt": "2026-11-02T10:00:00Z", "actor": {"login": "carol"}, "label": {"name": "status:backlog"}}]
    rc, r = rec_one(ws, gh)
    assert (r["stop_dispatch"], r["reason"], r["actor"]) == (True, "withdrawn", "carol")


def test_reconcile_acceptance_changed(ws):
    gh = FakeGh()
    claimed(ws, gh)
    gh.node(1)["body"] += "- a new criterion\n"
    assert rec_one(ws, gh)[1]["reason"] == "acceptance_changed"


def test_reconcile_unreadable_github_is_unverified_and_a_record_never_authorizes(ws):
    gh = FakeGh()
    claimed(ws, gh)
    gh.fail_all = True
    rc, r = rec_one(ws, gh)
    assert (r["stop_dispatch"], r["reason"]) == (True, "unverified") and rc != 0
    # replaying a record onto a fresh clone with a broken network still never yields `authorized`
    rc, out, _ = run_cli(["reconcile", "--json"], gh)
    res = json.loads(out)
    assert res["results"] and all(x["reason"] == "unverified" and x["stop_dispatch"] for x in res["results"])


def test_reconcile_without_arg_covers_records_and_the_operators_started_issues(ws):
    gh = FakeGh()
    claimed(ws, gh, 1)
    label_status(gh.node(2), "review")             # started on GitHub, no local record
    rc, out, _ = run_cli(["reconcile", "--json"], gh)
    got = {r["number"]: (r["stop_dispatch"], r["reason"]) for r in json.loads(out)["results"]}
    assert got == {1: (False, "authorized"), 2: (False, "authorized")}


# ---- AC7: the lead lease -------------------------------------------------------------------------------

def lead(ws, action, sid, now=NOW, extra=()):
    return run_cli(["lead", action, "--session-id", sid, "--op-id", "op_example", *extra], None, now=now)


def test_lead_lease_lifecycle(ws):
    path = ws / ".foundry" / "runtime" / "lead.json"
    rc, _, _ = lead(ws, "acquire", "s1")
    assert rc == 0
    doc = json.loads(path.read_text())
    assert set(doc) == {"op_id", "hostname", "pid", "session_id", "acquired_at", "heartbeat_at"} and doc["session_id"] == "s1"
    assert not [p for p in path.parent.iterdir() if p.name != "lead.json"]      # temp + rename left nothing behind
    # a second session is refused with the local-only message naming the cross-machine signals
    rc, _, err = lead(ws, "acquire", "s2", now=NOW + 60)
    assert rc != 0 and "local to this machine" in err
    for signal in ("assignee", "draft PR", "`start` record"):
        assert signal in err
    assert json.loads(path.read_text())["session_id"] == "s1"
    # check reports without writing
    before = path.read_text()
    rc, out, _ = lead(ws, "check", "s2", now=NOW + 60)
    assert rc != 0 and json.loads(out)["lease"] == "held" and path.read_text() == before
    rc, out, _ = lead(ws, "check", "s1", now=NOW + 60)
    assert rc == 0 and json.loads(out)["lease"] == "mine"
    # renew refreshes heartbeat only for the owner
    assert lead(ws, "renew", "s2", now=NOW + 61)[0] != 0
    assert lead(ws, "renew", "s1", now=NOW + 120)[0] == 0
    assert json.loads(path.read_text())["heartbeat_at"] != doc["heartbeat_at"]
    # a release by a non-owner removes nothing; the owner's removes it
    assert lead(ws, "release", "s2", now=NOW + 121)[0] != 0 and path.exists()
    assert lead(ws, "release", "s1", now=NOW + 121)[0] == 0 and not path.exists()


def test_lead_lease_is_accepted_once_stale(ws):
    assert lead(ws, "acquire", "s1")[0] == 0
    assert lead(ws, "acquire", "s2", now=NOW + 599)[0] != 0          # default 10 min
    rc, _, _ = lead(ws, "acquire", "s2", now=NOW + 601)
    assert rc == 0
    assert json.loads((ws / ".foundry" / "runtime" / "lead.json").read_text())["session_id"] == "s2"
    # --stale-after is honoured
    assert lead(ws, "acquire", "s3", now=NOW + 601 + 30, extra=("--stale-after", "10s"))[0] == 0


def test_lead_unreadable_lease_counts_as_stale_and_check_on_nothing_is_clean(ws):
    assert json.loads(lead(ws, "check", "s1")[1])["lease"] == "none"
    p = ws / ".foundry" / "runtime" / "lead.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json")
    assert lead(ws, "acquire", "s1")[0] == 0


# ---- guards: the config + existing verbs --------------------------------------------------------------

def test_existing_verbs_are_still_dispatched(ws):
    rc, out, _ = run_cli(["status"], None)
    assert rc == 1 and "no active ticket" in out
    with pytest.raises(SystemExit):
        ft.main(["bogus"])
