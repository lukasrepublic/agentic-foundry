"""dl-11: fleet builders - ref-create claims, leases, worker identity, deck slots, capacity, builder mode.

GitHub is always a stub (a FakeGitHub transport that models refs, commits and updateRefs). No network, no
credential. Assertions are on parsed fields and structures, not substrings of joined text.
Part 1 covers the two ticket Risks filed with this atom (claim_unverified on a missing claim, ticket_changed on start).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SCRIPTS))

from test_owned_queue import FakeGh, ft, label_status, mk_issue, run_cli, with_perm, ws  # noqa: E402,F401
import test_ticket_digest as td  # noqa: E402
import test_ticket_first as tf1  # noqa: E402


# ---------------------------------------------------------------------------------------------
# Part 1: the ticket Risks
# ---------------------------------------------------------------------------------------------
def test_a_started_assigned_issue_with_an_emptied_claim_record_stops_dispatch(ws):
    gh = td.claimed(ws)
    ft.store.update_claims(ws, lambda c: c.__setitem__(td.KEY, {}))
    r = td.reconcile(gh)
    assert (r["stop_dispatch"], r["reason"], r["issue"]) == (True, "claim_unverified", td.KEY)


@pytest.mark.xfail(strict=True, reason="BLOCKED: a deleted record is indistinguishable from a never-claimed started "
                   "issue, and tests/test_owned_queue.py (outside this atom's allowed_paths) pins that as authorized")
def test_a_started_assigned_issue_with_a_deleted_claim_record_stops_dispatch(ws):
    gh = td.claimed(ws)
    ft.store.update_claims(ws, lambda c: c.pop(td.KEY))
    r = td.reconcile(gh)
    assert (r["stop_dispatch"], r["reason"], r["issue"]) == (True, "claim_unverified", td.KEY)


def _start_with_ticket_claim(tmp_path, monkeypatch, claim_ticket_digest, live_ticket_digest):
    root = tf1._start_world(tmp_path, monkeypatch, "true", editor="alice", claim_cmd="true")
    st = tf1.foundry_ticket.store
    cf = Path(tf1._git(root, "rev-parse", "--absolute-git-dir")) / "foundry-claims.json"
    cf.write_text(json.dumps({"o/r#7": {"done_digest": st.command_digest("true"),
                                        "ticket_digest": claim_ticket_digest}}))
    digest = st.command_digest("true")
    monkeypatch.setattr(tf1.foundry_ticket, "read_issue", lambda run, repo, n, **k: {
        "done_sha256": digest, "done_digest": digest, "last_edited_by": "alice",
        "ticket_digest": live_ticket_digest})
    return root


def test_start_refuses_when_the_whole_ticket_digest_moved_though_done_means_did_not(tmp_path, monkeypatch):
    root = _start_with_ticket_claim(tmp_path, monkeypatch, "a" * 64, "b" * 64)
    with pytest.raises(SystemExit) as e:
        tf1.foundry_ticket.cmd_start(["7"])
    assert "ticket_changed" in str(e.value) and tf1.foundry_ticket.load(root) is None


def test_start_accepts_an_unchanged_whole_ticket_digest(tmp_path, monkeypatch):
    root = _start_with_ticket_claim(tmp_path, monkeypatch, "a" * 64, "a" * 64)
    assert tf1.foundry_ticket.cmd_start(["7"]) == 0
    assert tf1.foundry_ticket.load(root) is not None


# ---------------------------------------------------------------------------------------------
# Part 2: the fleet (stubbed GitHub)
# ---------------------------------------------------------------------------------------------
import hashlib  # noqa: E402
import re  # noqa: E402

import foundry_fleet as ff  # noqa: E402

REPO = "example-org/app"
T0 = 1_800_000_000.0


class FakeGitHub:
    """Models refs, commits, trees and updateRefs. Records every request so tests can assert on the calls."""

    def __init__(self):
        self.refs, self.commits, self.calls, self.n = {}, {}, [], 0
        self.graphql_vars = []

    def rest(self, method, path, body=None):
        self.calls.append((method, path))
        r = re.escape(REPO)
        if method == "GET" and path == f"repos/{REPO}":
            return 200, {"node_id": "R_node"}
        if method == "POST" and path == f"repos/{REPO}/git/trees":
            return 201, {"sha": ff.EMPTY_TREE}
        if method == "POST" and path == f"repos/{REPO}/git/commits":
            self.n += 1
            sha = hashlib.sha1(f"{self.n}{body['message']}".encode()).hexdigest()
            self.commits[sha] = {"message": body["message"], "parents": body["parents"]}
            return 201, {"sha": sha}
        if method == "POST" and path == f"repos/{REPO}/git/refs":
            if body["ref"] in self.refs:
                return 422, {"message": "Reference already exists"}
            self.refs[body["ref"]] = body["sha"]
            return 201, {}
        m = re.fullmatch(rf"repos/{r}/git/ref/(.+)", path)
        if method == "GET" and m:
            tip = self.refs.get("refs/" + m.group(1))
            return (200, {"object": {"sha": tip}}) if tip else (404, {})
        m = re.fullmatch(rf"repos/{r}/git/commits/([0-9a-f]{{40}})", path)
        if method == "GET" and m:
            c = self.commits.get(m.group(1))
            return (200, {"message": c["message"]}) if c else (404, {})
        m = re.fullmatch(rf"repos/{r}/git/matching-refs/(.+)", path)
        if method == "GET" and m:
            pre = "refs/" + m.group(1)
            return 200, [{"ref": k, "object": {"sha": v}} for k, v in sorted(self.refs.items()) if k.startswith(pre)]
        raise AssertionError(f"unexpected request {method} {path}")

    def graphql(self, query, variables):
        self.calls.append(("GRAPHQL", "updateRefs"))
        self.graphql_vars.append(variables)
        assert "updateRefs" in query
        for u in variables["in"]["refUpdates"]:
            if self.refs.get(u["name"]) != u["beforeOid"]:
                return 200, {"errors": [{"message": "expected beforeOid does not match"}]}
            self.refs[u["name"]] = u["afterOid"]
        return 200, {"data": {"updateRefs": {"clientMutationId": None}}}


def worker(session="sess0001", machine="a" * 12, container="b" * 12, op="op_lukas"):
    return {"id": f"{op}@{machine}/{container}/{session}", "op_id": op, "machine12": machine,
            "container12": container, "session": session, "handle": "lukas"}


def mk_fleet(gh, now=T0, expires=7200):
    box = {"t": now}
    f = ff.Fleet(gh, REPO, expires, clock=lambda: box["t"])
    return f, box


def test_two_workers_create_the_same_claim_ref_the_201_caller_wins_and_the_422_caller_is_lost():
    gh = FakeGitHub()
    f, _ = mk_fleet(gh)
    a, b = worker("sessA0001"), worker("sessB0001", machine="c" * 12)
    ra, rb = f.claim(a, "prog", "w1-x"), f.claim(b, "prog", "w1-x")
    assert (ra["result"], rb["result"]) == ("won", "lost")
    assert rb["holder"] == a["id"]
    assert list(gh.refs) == ["refs/heads/claim/prog/w1-x"]
    posts = [c for c in gh.calls if c[0] == "POST" and c[1].endswith("/git/refs")]
    assert len(posts) == 2


def test_the_same_worker_claiming_again_holds_it_and_makes_no_new_commit_on_the_ref():
    gh = FakeGitHub()
    f, _ = mk_fleet(gh)
    w = worker()
    first = f.claim(w, "prog", "w1-x")
    again = f.claim(w, "prog", "w1-x")
    assert (first["result"], again["result"], again["tip"]) == ("won", "held", first["tip"])


def test_a_heartbeat_moves_the_ref_with_a_before_oid_and_updates_the_lease_time():
    gh = FakeGitHub()
    f, box = mk_fleet(gh)
    w = worker()
    tip0 = f.claim(w, "prog", "w1-x")["tip"]
    box["t"] += 600
    r = f.heartbeat(w, "prog", "w1-x")
    assert r["result"] == "ok" and r["tip"] != tip0
    upd = gh.graphql_vars[-1]["in"]["refUpdates"][0]
    assert upd["beforeOid"] == tip0 and upd["afterOid"] == r["tip"] and upd["name"] == "refs/heads/claim/prog/w1-x"
    rec = json.loads(gh.commits[r["tip"]]["message"].split("\n\n", 1)[1])
    assert rec["heartbeat_at"] == ff.store.iso(T0 + 600) and gh.commits[r["tip"]]["parents"] == [tip0]


def test_a_mismatched_before_oid_rereads_the_ref_and_does_not_overwrite_it():
    gh = FakeGitHub()
    f, box = mk_fleet(gh)
    w = worker()
    f.claim(w, "prog", "w1-x")
    real = gh.graphql

    def racing(query, variables):          # another writer moves the tip between our read and our write
        gh.refs["refs/heads/claim/prog/w1-x"] = "f" * 40
        gh.commits["f" * 40] = {"message": "foundry-claim\n\n" + json.dumps({"worker": w["id"], "state": "building"}),
                                "parents": []}
        return real(query, variables)
    gh.graphql = racing
    r = f.transition(w, "prog", "w1-x", "building")
    assert r["result"] == "conflict" and r["tip"] == "f" * 40
    assert gh.refs["refs/heads/claim/prog/w1-x"] == "f" * 40       # untouched


def test_a_live_claim_refuses_takeover_and_an_expired_one_allows_it():
    gh = FakeGitHub()
    f, box = mk_fleet(gh, expires=7200)
    a, b = worker("sessA0001"), worker("sessB0001", machine="c" * 12)
    f.claim(a, "prog", "w1-x")
    box["t"] = T0 + 7199
    assert f.claim(b, "prog", "w1-x")["result"] == "lost"
    box["t"] = T0 + 7201
    r = f.claim(b, "prog", "w1-x")
    assert (r["result"], r["from"]) == ("taken_over", a["id"])
    rec = json.loads(gh.commits[gh.refs["refs/heads/claim/prog/w1-x"]]["message"].split("\n\n", 1)[1])
    assert rec["worker"] == b["id"] and rec["taken_over_from"]["worker"] == a["id"]
    assert f.heartbeat(a, "prog", "w1-x")["result"] == "not_holder"


def test_the_expiry_comes_from_the_policy_claims_expires_after(tmp_path):
    (tmp_path / ".foundry").mkdir()
    (tmp_path / ".foundry" / "operating-model.yaml").write_text("claims:\n  expires_after: 30m\n")
    assert ff.store.claims_policy(tmp_path)[1] == 1800
    assert ff._expires(tmp_path) == 1800


def test_transitions_record_review_ready_and_a_pr_and_only_the_holder_may_make_them():
    gh = FakeGitHub()
    f, _ = mk_fleet(gh)
    a, b = worker("sessA0001"), worker("sessB0001")
    f.claim(a, "prog", "w1-x")
    pr = "https://github.com/example-org/app/pull/7"
    r = f.transition(a, "prog", "w1-x", "review_ready", pr)
    assert r["result"] == "ok"
    assert f.status("prog", "w1-x") | {"tip": None} == {"result": "claimed", "ref": "refs/heads/claim/prog/w1-x",
                                                        "tip": None, "holder": a["id"], "state": "review_ready",
                                                        "lease": "live"}
    assert f.transition(b, "prog", "w1-x", "building")["result"] == "not_holder"
    with pytest.raises(ff.FleetError) as e:
        f.transition(a, "prog", "w1-x", "merged")
    assert e.value.reason == "bad-state"
    with pytest.raises(ff.FleetError) as e:
        f.transition(a, "prog", "w1-x", "building", "javascript:alert(1)")
    assert e.value.reason == "bad-pr"


@pytest.mark.parametrize("programme,batch,reason", [
    ("../x", "b", "bad-programme"), ("p", "a/b", "bad-batch"), ("p", "..", "bad-batch"), ("p", "x.lock", "bad-batch"),
    ("p q", "b", "bad-programme"), ("", "b", "bad-programme"), ("p", "b\nrefs/heads/main", "bad-batch"),
    ("p", "-rf", "bad-batch"),
])
def test_claim_refs_are_confined_to_validated_segments(programme, batch, reason):
    gh = FakeGitHub()
    f, _ = mk_fleet(gh)
    with pytest.raises(ff.FleetError) as e:
        f.claim(worker(), programme, batch)
    assert e.value.reason == reason and gh.calls == [] and gh.refs == {}


def test_every_ref_the_fleet_writes_is_under_claim_programme_batch():
    gh = FakeGitHub()
    f, _ = mk_fleet(gh)
    f.claim(worker(), "prog", "w1-x")
    f.arm_deck(worker())
    assert all(re.fullmatch(r"refs/heads/claim/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", r) for r in gh.refs)


# --- identity ---------------------------------------------------------------------------------
MID = "0123456789abcdef0123456789abcdef"
CID = "d" * 64


def identity_files(tmp_path, cgroup=f"0::/docker/{CID}\n", mid=MID, handle="lukas"):
    ops = tmp_path / "ops.json"
    ops.write_text(json.dumps({"operators": {"op_lukas": {"name": "L", "github": handle}}}))
    m, c = tmp_path / "machine-id", tmp_path / "cgroup"
    if mid is not None:
        m.write_text(mid + "\n")
    if cgroup is not None:
        c.write_text(cgroup)
    return dict(operators_file=str(ops), machine_id_file=str(m), cgroup_file=str(c))


ENV = {"CLAUDE_CODE_SESSION_ID": "5f0c1b2e-aaaa-bbbb-cccc-0123456789ab", "FOUNDRY_OPERATOR": "op_lukas"}


def test_worker_id_is_op_machine12_container12_session(tmp_path):
    w = ff.worker_identity(env=ENV, **identity_files(tmp_path))
    assert w["id"] == f"op_lukas@{MID[:12]}/{CID[:12]}/{ENV['CLAUDE_CODE_SESSION_ID']}"
    assert w["id"].partition("@")[0] == "op_lukas"
    host, container, session = w["id"].partition("@")[2].split("/")
    assert (len(host), len(container)) == (12, 12) and session == ENV["CLAUDE_CODE_SESSION_ID"]


def test_an_unset_session_id_refuses_with_no_per_clone_fallback(tmp_path, monkeypatch):
    clone = tmp_path / "clone"
    clone.mkdir()
    subprocess.run(["git", "init", "-q", str(clone)], check=True)
    (clone / ".git" / "foundry-lead-session").write_text("per-clone-session\n")     # the removed fallback source
    monkeypatch.chdir(clone)
    env = {k: v for k, v in ENV.items() if k != "CLAUDE_CODE_SESSION_ID"}
    with pytest.raises(ff.FleetError) as e:
        ff.worker_identity(env=env, **identity_files(tmp_path))
    assert e.value.reason == "session-missing"
    gh = FakeGitHub()
    rc = ff.main(["claim", REPO, "prog", "w1-x"], transport=gh, env=env)
    assert rc == 2 and gh.calls == []


@pytest.mark.parametrize("what,kw,env_drop,reason", [
    ("machine id file missing", {"mid": None}, None, "machine-id-unreadable"),
    ("machine id malformed", {"mid": "not-hex"}, None, "machine-id-invalid"),
    ("cgroup missing", {"cgroup": None}, None, "cgroup-unreadable"),
    ("no container in cgroup", {"cgroup": "0::/user.slice/x.scope\n"}, None, "container-id-missing"),
    ("operator unset", {}, "FOUNDRY_OPERATOR", "operator-missing"),
    ("no handle", {"handle": ""}, None, "operator-handle-missing"),
])
def test_a_missing_or_unreadable_part_fails_closed_with_a_named_reason(tmp_path, what, kw, env_drop, reason):
    env = {k: v for k, v in ENV.items() if k != env_drop}
    with pytest.raises(ff.FleetError) as e:
        ff.worker_identity(env=env, **identity_files(tmp_path, **kw))
    assert e.value.reason == reason


@pytest.mark.parametrize("session", ["a b", "x;rm -rf /", "a--b", "-x", "a\nb", "x" * 65, "$(id)"])
def test_a_hostile_session_id_is_refused(tmp_path, session):
    with pytest.raises(ff.FleetError) as e:
        ff.worker_identity(env={**ENV, "CLAUDE_CODE_SESSION_ID": session}, **identity_files(tmp_path))
    assert e.value.reason == "session-invalid"


def test_the_unregistered_operator_is_refused(tmp_path):
    with pytest.raises(ff.FleetError) as e:
        ff.worker_identity(env={**ENV, "FOUNDRY_OPERATOR": "op_other"}, **identity_files(tmp_path))
    assert e.value.reason == "operator-unregistered"


def test_trailers_and_marker_are_built_from_validated_parts_as_data(tmp_path):
    w = ff.worker_identity(env=ENV, **identity_files(tmp_path))
    lines = ff.trailers(w)
    keys = [l.split(": ", 1)[0] for l in lines]
    assert keys == ["Co-authored-by", "Session-Id", "Worker-Host"]
    assert dict(l.split(": ", 1) for l in lines)["Session-Id"] == ENV["CLAUDE_CODE_SESSION_ID"]
    assert dict(l.split(": ", 1) for l in lines)["Worker-Host"] == f"{MID[:12]}/{CID[:12]}"
    marker = ff.pr_marker(w)
    m = re.fullmatch(r"<!-- foundry-worker: (\S+) -->", marker)
    assert m and m.group(1) == w["id"] and "\n" not in marker and marker.count("-->") == 1
    src = (SCRIPTS / "foundry_fleet.py").read_text()
    assert "shell=True" not in src and "os.system" not in src


# --- deck slots, capacity, builder mode ------------------------------------------------------------
def test_a_third_deck_on_the_same_machine_is_deck_slot_full_while_two_are_live():
    gh = FakeGitHub()
    f, _ = mk_fleet(gh)
    ws_ = [worker(f"sess{i}0001") for i in range(3)]
    r = [f.arm_deck(w) for w in ws_]
    assert [x["result"] for x in r] == ["armed", "armed", "deck-slot-full"]
    assert [x["slot"] for x in r[:2]] == [1, 2]
    for ref in gh.refs:
        rec = json.loads(gh.commits[gh.refs[ref]]["message"].split("\n\n", 1)[1])
        assert rec["title"].startswith("deck:") and rec["kind"] == "deck"
    assert f.arm_deck(ws_[0])["result"] == "armed"        # the holder re-arming is not a third deck
    assert len(gh.refs) == 2


def test_another_machine_has_its_own_two_deck_slots_and_an_expired_slot_frees():
    gh = FakeGitHub()
    f, box = mk_fleet(gh)
    mine = [worker(f"sess{i}0001") for i in range(3)]
    f.arm_deck(mine[0]), f.arm_deck(mine[1])
    assert f.arm_deck(worker("sessZ0001", machine="9" * 12))["result"] == "armed"
    box["t"] += 7201
    assert f.arm_deck(mine[2])["result"] == "armed"


@pytest.mark.parametrize("workers,reviewers,plan_max,expect", [
    ([2, 2], 10, None, 4), ([2, 2], 3, None, 3), ([1], 5, None, 1), ([3, 3], 5, 4, 4), ([3, 3], 0, None, 0),
    ([2, 2], 10, 1, 1),
])
def test_capacity_is_the_min_of_worker_sum_and_reviewer_capacity(workers, reviewers, plan_max, expect):
    assert ff.fleet_capacity(workers, reviewers, plan_max) == expect


@pytest.mark.parametrize("workers,reviewers", [([0], 1), ([True], 1), (["2"], 1), ([1], -1), ([1], True), ([], 1)])
def test_capacity_refuses_bad_numbers(workers, reviewers):
    if workers == []:
        assert ff.fleet_capacity(workers, reviewers) == 0
        return
    with pytest.raises(ff.FleetError):
        ff.fleet_capacity(workers, reviewers)


PLAN = {"programme": "prog", "open_wave": 1, "max_concurrent": 3, "held": ["z"], "critical_path": [],
        "generated_from": {"node_ids": [], "corpus_sha256": "0" * 64},
        "waves": [{"n": 1, "batches": [{"id": "w1-a", "atoms": ["a"], "paths": ["a"], "total_float": 0},
                                        {"id": "w1-b", "atoms": ["b"], "paths": ["b"], "total_float": 0},
                                        {"id": "w1-z", "atoms": ["z"], "paths": ["z"], "total_float": 0}]},
                  {"n": 2, "batches": [{"id": "w2-c", "atoms": ["c"], "paths": ["c"], "total_float": 0}]}]}


def test_builder_mode_claims_one_batch_of_the_open_wave_skips_held_and_never_touches_the_plan():
    gh = FakeGitHub()
    f, _ = mk_fleet(gh)
    import copy
    plan = copy.deepcopy(PLAN)
    w1, w2, w3 = worker("sess10001"), worker("sess20001"), worker("sess30001")
    r1, r2, r3 = (ff.build_next(f, w, plan, 2) for w in (w1, w2, w3))
    assert (r1["result"], r1["batch"]) == ("claimed", "w1-a")
    assert (r2["result"], r2["batch"]) == ("claimed", "w1-b")
    assert r3["result"] == "at-capacity"                     # cap 2, two live claims
    assert plan == PLAN                                       # never re-plans or edits the plan
    assert sorted(gh.refs) == ["refs/heads/claim/prog/w1-a", "refs/heads/claim/prog/w1-b"]
    assert ff.build_next(f, w1, plan, 2)["result"] == "held"
    assert ff.build_next(f, w3, {**plan, "open_wave": 9}, 5)["result"] == "none"


def test_builder_mode_cap_comes_from_the_fleet_capacity_on_the_cli(tmp_path, monkeypatch):
    (tmp_path / "plan.json").write_text(json.dumps(PLAN))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    import yaml
    (tmp_path / "plan.yaml").write_text(yaml.safe_dump(PLAN))
    gh = FakeGitHub()
    files = identity_files(tmp_path)
    w = ff.worker_identity(env=ENV, **files)
    rc = ff.main(["build-next", REPO, "prog", "--plan-file", "plan.yaml", "--workers", "1,1", "--reviewers", "1"],
                 transport=gh, env={**ENV, "CLAUDE_PROJECT_DIR": str(tmp_path)}, identity=w)
    assert rc == 0 and list(gh.refs) == ["refs/heads/claim/prog/w1-a"]
    other = dict(w, id=w["id"].replace("/", "/", 1) + "x", session=w["session"] + "x")
    rc = ff.main(["build-next", REPO, "prog", "--plan-file", "plan.yaml", "--workers", "1,1", "--reviewers", "1"],
                 transport=gh, env={**ENV, "CLAUDE_PROJECT_DIR": str(tmp_path)}, identity=other)
    assert rc == 0 and list(gh.refs) == ["refs/heads/claim/prog/w1-a"]      # reviewer capacity 1 => at-capacity


# --- the token ----------------------------------------------------------------------------------
def test_one_installation_token_is_minted_per_worker_session_and_refreshed_before_expiry():
    minted, box = [], {"t": 1000.0}

    def mint():
        minted.append(1)
        return f"ghs_{'A' * 30}{len(minted)}", box["t"] + 3600
    tk = ff.SessionTokens(mint, clock=lambda: box["t"])
    a1, a2 = tk.token_for("sessA"), tk.token_for("sessA")
    b1 = tk.token_for("sessB")
    assert a1 == a2 and b1 != a1 and len(minted) == 2
    box["t"] += 3600 - 30                                     # inside the 60 s margin
    assert tk.token_for("sessA") != a1 and len(minted) == 3


def test_without_a_provisioned_token_the_cli_names_external_provisioning(tmp_path):
    with pytest.raises(ff.FleetError) as e:
        ff.file_token_mint(env={})()
    assert e.value.reason == "external-provisioning"
    p = tmp_path / "tok"
    p.write_text("ghs_" + "B" * 30)
    p.chmod(0o644)
    with pytest.raises(ff.FleetError) as e:
        ff.file_token_mint(env={"FOUNDRY_FLEET_TOKEN_FILE": str(p)})()
    assert e.value.reason == "token-file-permissions"
    p.chmod(0o600)
    tok, exp = ff.file_token_mint(env={"FOUNDRY_FLEET_TOKEN_FILE": str(p)}, clock=lambda: 10.0)()
    assert tok == "ghs_" + "B" * 30 and exp == 3010.0


def test_the_token_reaches_gh_only_in_the_child_environment_and_never_in_output():
    secret = "ghs_" + "C" * 36
    seen = {}

    def runner(args, input=None, capture_output=None, text=None, timeout=None, env=None):
        seen.update(args=args, env=env, input=input)
        return subprocess.CompletedProcess(args, 0, stdout='HTTP/2.0 201 Created\r\n\r\n{"sha":"x"}', stderr="")
    tr = ff.GhTransport(ff.SessionTokens(lambda: (secret, T0 + 3600), clock=lambda: T0), "sess1", runner=runner)
    st, doc = tr.rest("POST", f"repos/{REPO}/git/refs", {"ref": "r"})
    assert st == 201 and seen["env"]["GH_TOKEN"] == secret and "GITHUB_TOKEN" not in seen["env"]
    assert secret not in " ".join(seen["args"]) and secret not in (seen["input"] or "")
    assert secret not in ff.redact(f"boom {secret} and Bearer {'z' * 24}", secret)
    assert "[REDACTED]" in ff.redact(f"x {secret}")


def test_the_fleet_cli_help_and_the_scripts_stay_free_of_a_credential():
    r = subprocess.run([sys.executable, str(SCRIPTS / "foundry_fleet.py"), "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "build-next" in r.stdout
    assert not re.search(r"gh[pousr]_[A-Za-z0-9]{16,}|-----BEGIN", (SCRIPTS / "foundry_fleet.py").read_text())
