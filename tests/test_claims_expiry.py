"""tests/test_claims_expiry.py - gv-11: claims are leases (operating-model rule B5).

  * `claim` records session + heartbeat + expiry; `reconcile` and `lead renew` heartbeat the claims THIS
    session holds (never another session's);
  * a lease older than `claims.expires_after` (default 2h; policy) can be TAKEN OVER by another session, and
    the takeover is recorded (`taken_over_from`, `at`); before expiry the takeover is refused;
  * `claim --successor-of` continues a long chunk under the SAME session and is not a takeover;
  * clause (m) of hooks/foundry-git-discipline.sh: a work-branch push / non-draft `gh pr create` without a
    live claim for the bound issue is refused `claim-required`; with a live claim it is admitted; the claims
    store is read fail-closed.
No real gh/git network: `gh` is the FakeGh stub from test_owned_queue (an argv log), the hook runs with no remote.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

import foundry_ticket_store as store
from test_owned_queue import FakeGh, FIX, NOW, run_cli, ws  # noqa: F401  (ws is a fixture)

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "foundry-git-discipline.sh"
H = 3600


def claim(ws, n, sid, gh=None, now=NOW, *extra):
    return run_cli(["claim", str(n), "--session-id", sid, *extra], gh or FakeGh(), now=now)


def rec(ws, n=2):
    return store.read_claims(ws)[0].get(f"example-org/app#{n}")


# ---- the lease record -------------------------------------------------------------------------------------
def test_claim_records_the_session_heartbeat_and_expiry(ws):
    rc, out, err = claim(ws, 2, "A")
    assert rc == 0, err
    r = rec(ws)
    assert r["session_id"] == "A" and r["heartbeat_at"] == store.iso(NOW) and r["expires_after_s"] == 2 * H
    assert "taken_over_from" not in r and "successor_of" not in r


def test_same_session_reclaim_refreshes_the_heartbeat_and_is_not_a_takeover(ws):
    claim(ws, 2, "A")
    rc, out, err = claim(ws, 2, "A", now=NOW + 3 * H)          # expired, but it is MY lease
    assert rc == 0, err
    r = rec(ws)
    assert r["session_id"] == "A" and r["heartbeat_at"] == store.iso(NOW + 3 * H) and "taken_over_from" not in r


# ---- takeover: only an expired lease; recorded --------------------------------------------------------------
def test_takeover_before_expiry_is_refused_and_changes_nothing(ws):
    claim(ws, 2, "A")
    before = rec(ws)
    gh = FakeGh()
    rc, out, err = claim(ws, 2, "B", gh, now=NOW + 2 * H - 1)    # one second short of expiry
    assert rc == 2 and "lease-held" in err and "session A" in err and "only an expired lease" in err
    assert rec(ws) == before
    assert not [c for c in gh.calls if c[:2] == ["issue", "comment"]]      # no `start` marker was posted


def test_takeover_after_expiry_succeeds_and_is_recorded(ws):
    claim(ws, 2, "A")
    t = NOW + 2 * H                                              # exactly expired: age == expires_after
    rc, out, err = claim(ws, 2, "B", now=t)
    assert rc == 0, err
    assert "took over from session A" in out
    r = rec(ws)
    assert r["session_id"] == "B" and r["heartbeat_at"] == store.iso(t) and r["claimed_at"] == store.iso(t)
    assert r["taken_over_from"] == {"session_id": "A", "operator": "alice", "last_heartbeat_at": store.iso(NOW),
                                    "at": store.iso(t)}


def test_policy_expires_after_sets_the_lease_length(ws):
    (ws / ".foundry").mkdir()
    (ws / ".foundry" / "operating-model.yaml").write_text("claims:\n  expires_after: 30m\n")
    claim(ws, 2, "A")
    assert rec(ws)["expires_after_s"] == 1800
    assert claim(ws, 2, "B", now=NOW + 1799)[0] == 2
    rc, out, err = claim(ws, 2, "B", now=NOW + 1800)
    assert rc == 0 and "took over" in out and rec(ws)["taken_over_from"]["session_id"] == "A"


def test_a_record_without_a_session_is_not_a_lease(ws):
    claim(ws, 2, "A")

    def strip(cl):
        for k in ("session_id", "heartbeat_at", "expires_after_s"):
            cl["example-org/app#2"].pop(k)
    store.update_claims(ws, strip)
    rc, out, err = claim(ws, 2, "B", now=NOW + 60)              # a pre-lease record: re-claim as before
    assert rc == 0, err
    assert "taken_over_from" not in rec(ws)


# ---- heartbeats --------------------------------------------------------------------------------------------
def test_reconcile_is_a_heartbeat_for_my_claims_only(ws):
    claim(ws, 2, "A")
    claim(ws, 1, "B")
    t = NOW + 90 * 60
    rc, out, err = run_cli(["reconcile", "--session-id", "A", "--json"], FakeGh(), now=t)
    assert rc in (0, 3), err
    assert rec(ws, 2)["heartbeat_at"] == store.iso(t)
    assert rec(ws, 1)["heartbeat_at"] == store.iso(NOW)          # B's claim was NOT kept alive by A
    # A's lease now outlives the original expiry; B's does not
    assert claim(ws, 2, "C", now=NOW + 2 * H + 1)[0] == 2
    rc, out, err = claim(ws, 1, "C", now=NOW + 2 * H + 1)
    assert rc == 0 and rec(ws, 1)["taken_over_from"]["session_id"] == "B"
    assert claim(ws, 2, "C", now=t + 2 * H)[0] == 0              # and it too expires 2h after A's last heartbeat


def test_lead_renew_heartbeats_the_claims_this_session_holds(ws):
    claim(ws, 2, "A")
    claim(ws, 1, "B")
    assert run_cli(["lead", "acquire", "--session-id", "A"], FakeGh(), now=NOW)[0] == 0
    t = NOW + 100 * 60
    rc, out, err = run_cli(["lead", "renew", "--session-id", "A"], FakeGh(), now=t)
    assert rc == 0, err
    assert rec(ws, 2)["heartbeat_at"] == store.iso(t) and rec(ws, 1)["heartbeat_at"] == store.iso(NOW)
    assert "heartbeat on 1 claim" in out


def test_heartbeat_never_writes_without_a_claims_file_and_refuses_an_unreadable_one(ws):
    assert store.heartbeat_claims(ws, "A", NOW) == []
    assert store.claims_file(ws) is None or not store.claims_file(ws).exists()
    claim(ws, 2, "A")
    store.claims_file(ws).write_text("{not json")
    with pytest.raises(store.ClaimsUnreadable):
        store.heartbeat_claims(ws, "A", NOW)
    assert store.claims_file(ws).read_text() == "{not json"      # never overwritten


# ---- successor: continuing a long chunk is not a takeover -----------------------------------------------------
def test_successor_claim_continues_my_chunk_without_a_takeover(ws):
    claim(ws, 2, "A")
    rc, out, err = claim(ws, 1, "A", None, NOW + 3600, "--successor-of", "2")
    assert rc == 0, err
    succ, pred = rec(ws, 1), rec(ws, 2)
    assert succ["successor_of"] == "example-org/app#2" and "taken_over_from" not in succ and succ["session_id"] == "A"
    assert pred["superseded_by"] == "example-org/app#1" and pred["session_id"] == "A"
    assert "successor of example-org/app#2" in out


def test_successor_of_a_claim_i_do_not_hold_is_refused_and_is_not_a_takeover_path(ws):
    claim(ws, 2, "A")
    rc, out, err = claim(ws, 1, "B", None, NOW + 3 * H, "--successor-of", "2")     # A's lease is even expired
    assert rc == 2 and "successor-refused" in err and rec(ws, 1) is None
    assert rec(ws, 2)["session_id"] == "A" and "superseded_by" not in rec(ws, 2)
    assert claim(ws, 1, "A", None, NOW, "--successor-of", "9")[0] == 2           # unknown predecessor
    assert claim(ws, 2, "A", None, NOW, "--successor-of", "2")[0] == 2           # itself


# ---- clause (m): the claim-required push ---------------------------------------------------------------------
@pytest.fixture
def team(tmp_path):
    root = tmp_path / "team"
    (root / ".claude").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "feature-x", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@e.invalid",
                    "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    (root / ".claude" / "foundry-project.json").write_text(json.dumps(
        {"github_scope": {"owner": "example-org", "planning_repo": "planning"}}))
    return root


def set_ticket(root, issue=2, repo="example-org/app"):
    p = store.ticket_file(root)
    p.write_text(json.dumps({"issue": issue, "repo": repo, "status": "open"}))


def set_claim(root, age, number=2, **extra):
    stamp = store.iso(time.time() - age)
    r = {"issue": f"example-org/app#{number}", "number": number, "repo": "example-org/app", "operator": "alice",
         "claimed_at": stamp, "heartbeat_at": stamp, "session_id": "A", "expires_after_s": 2 * H, **extra}
    store.update_claims(root, lambda cl: cl.__setitem__(f"example-org/app#{number}", r))


def gd(root, command, *args):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    return subprocess.run([str(HOOK), "--local-green=off", *args], capture_output=True, text=True, env=env,
                          input=json.dumps({"tool_input": {"command": command}, "cwd": str(root)}))


def refused_claim(r):
    return r.returncode == 2 and "claim-required" in r.stdout + r.stderr


PUSH = "git push -u origin feature-x"
PR = "gh pr create --title t --body b"


def test_push_without_a_claim_is_refused_claim_required(team):
    set_ticket(team)
    r = gd(team, PUSH)
    assert refused_claim(r), (r.returncode, r.stdout, r.stderr)
    assert "foundry-ticket.py claim" in r.stdout + r.stderr          # the way forward is named


def test_push_with_a_live_claim_is_admitted(team):
    set_ticket(team)
    set_claim(team, age=H)
    r = gd(team, PUSH)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert gd(team, "git push origin feature-x:feature-x").returncode == 0
    assert gd(team, "git push").returncode == 0


def test_push_with_an_expired_claim_is_refused_and_a_heartbeat_revives_it(team):
    set_ticket(team)
    set_claim(team, age=2 * H + 60)
    r = gd(team, PUSH)
    assert refused_claim(r) and "expired" in r.stdout + r.stderr
    store.heartbeat_claims(team, "A", time.time())
    assert gd(team, PUSH).returncode == 0


def test_push_fails_closed_on_an_unreadable_claims_store_or_ticket(team):
    set_ticket(team)
    set_claim(team, age=60)
    store.claims_file(team).write_text("{not json")
    r = gd(team, PUSH)
    assert refused_claim(r) and "unreadable" in r.stdout + r.stderr
    store.claims_file(team).write_text(json.dumps({"example-org/app#2": {"heartbeat_at": store.iso(time.time())}}))
    assert gd(team, PUSH).returncode == 0                              # control: readable again, live
    store.ticket_file(team).write_text("{not json")
    r = gd(team, PUSH)
    assert refused_claim(r) and "ticket record is unreadable" in r.stdout + r.stderr


def test_a_claim_for_another_issue_does_not_admit_the_push(team):
    set_ticket(team, issue=2)
    set_claim(team, age=60, number=1)
    assert refused_claim(gd(team, PUSH))


def test_maintenance_pushes_need_no_claim(team):
    set_ticket(team)
    for ok in ("git push --delete origin feature-x", "git push origin :feature-x", "git push --dry-run origin feature-x",
               "git push origin --tags"):
        assert gd(team, ok).returncode == 0, ok
    assert gd(team, "git status").returncode == 0 and gd(team, "git log -1").returncode == 0


def test_gh_pr_create_needs_a_claim_unless_draft(team):
    set_ticket(team)
    assert refused_claim(gd(team, PR))
    assert refused_claim(gd(team, "gh pr create -R example-org/app --title t"))
    assert gd(team, "gh pr create --draft --title t").returncode == 0
    assert gd(team, "gh pr create -d --title t").returncode == 0
    assert gd(team, "gh pr list").returncode == 0
    set_claim(team, age=60)
    assert gd(team, PR).returncode == 0


def test_the_gate_applies_only_where_claims_are_in_use(team):
    # no ticket bound to an issue
    assert gd(team, PUSH).returncode == 0
    set_ticket(team)
    assert refused_claim(gd(team, PUSH))                               # control: now it applies
    # claims disabled by policy
    (team / ".foundry").mkdir()
    (team / ".foundry" / "operating-model.yaml").write_text("claims:\n  enabled: false\n")
    assert gd(team, PUSH).returncode == 0
    (team / ".foundry" / "operating-model.yaml").unlink()
    assert refused_claim(gd(team, PUSH))
    # a broken policy file never turns the lease off
    (team / ".foundry" / "operating-model.yaml").write_text("claims: [oops\n")
    assert refused_claim(gd(team, PUSH))
    # a workspace with no github_scope (single-operator) has no claims and is never gated
    (team / ".claude" / "foundry-project.json").write_text(json.dumps({"schema_version": 1}))
    assert gd(team, PUSH).returncode == 0


def test_claim_gate_unit_reasons(team):
    set_ticket(team)
    ok, why = store.claim_gate(str(team))
    assert not ok and why.startswith("claim-required: no claim for example-org/app#2")
    set_claim(team, age=10)
    assert store.claim_gate(str(team)) == (True, "live claim on example-org/app#2")
    ok, why = store.claim_gate(str(team), now=time.time() + 2 * H + 30)
    assert not ok and why.startswith("claim-required:") and "expired" in why
    # a future heartbeat is not a live lease
    set_claim(team, age=-10 * H)
    assert not store.claim_gate(str(team))[0]


# ---- fix round 1 ---------------------------------------------------------------------------------------------
def test_fix1_a_malformed_project_file_fails_closed(team):
    set_ticket(team)
    set_claim(team, age=60)
    assert gd(team, PUSH).returncode == 0                                   # control: well-formed and live
    for bad in ("{not json", "[1]", json.dumps({"github_scope": "x"}), json.dumps({"github_scope": {"owner": "o"}})):
        (team / ".claude" / "foundry-project.json").write_text(bad)
        r = gd(team, PUSH)
        assert refused_claim(r) and "claim-store-unreadable" in r.stdout + r.stderr, bad
    (team / ".claude" / "foundry-project.json").unlink()                    # no project file at all: claims not in use
    assert gd(team, PUSH).returncode == 0


def test_fix1_gh_hostname_does_not_hide_pr_create(team):
    set_ticket(team)
    assert refused_claim(gd(team, "gh --hostname github.example.invalid pr create --title t"))
    assert refused_claim(gd(team, "gh -R example-org/app pr create --title t"))
    assert gd(team, "gh --hostname h pr create --draft --title t").returncode == 0


def test_fix1_git_dash_C_picks_the_repository_the_push_is_aimed_at(team, tmp_path):
    other = tmp_path / "other-ws"
    (other / ".claude").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "feature-x", str(other)], check=True)
    (other / ".claude" / "foundry-project.json").write_text(json.dumps(
        {"github_scope": {"owner": "example-org", "planning_repo": "planning"}}))
    set_ticket(other)
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    def run(cmd, cwd):
        return subprocess.run([str(HOOK), "--local-green=off"], capture_output=True, text=True, env=env,
                              input=json.dumps({"tool_input": {"command": cmd}, "cwd": str(cwd)}))
    assert refused_claim(run(f"git -C {other} push origin feature-x", team))     # judged in `other`, which has no claim
    set_claim(other, age=30)
    assert run(f"git -C {other} push origin feature-x", team).returncode == 0
    assert run("git push origin feature-x", team).returncode == 0               # `team` has no ticket: not gated


def test_fix1_claims_disabled_means_the_claim_verb_does_not_enforce_the_lease(ws):
    (ws / ".foundry").mkdir()
    (ws / ".foundry" / "operating-model.yaml").write_text("claims:\n  enabled: false\n")
    claim(ws, 2, "A")
    rc, out, err = claim(ws, 2, "B", now=NOW + 60)
    assert rc == 0, err
    assert rec(ws)["session_id"] == "B" and "taken_over_from" not in rec(ws)


def test_fix1_the_lease_is_rechecked_inside_the_claims_lock(ws, monkeypatch):
    """A contender that takes the lease between the early read and the locked write makes the later claim LOSE."""
    claim(ws, 2, "A")
    real = store.update_claims

    def sneaky(root, fn):          # B's claim lands first, inside the window A's early check cannot see
        def both(cl):
            cl["example-org/app#2"] = {**cl["example-org/app#2"], "session_id": "B", "heartbeat_at": store.iso(NOW + 2 * H + 5)}
            fn(cl)
        return real(root, both)
    monkeypatch.setattr(store, "update_claims", sneaky)
    rc, out, err = claim(ws, 2, "C", now=NOW + 2 * H + 10)       # C saw A's claim as expired; B renewed it meanwhile
    assert rc == 2 and "lease-held" in err
    assert rec(ws)["session_id"] != "C" and "taken_over_from" not in rec(ws)


def test_fix1_the_record_length_is_bounded_by_policy_and_the_smaller_wins():
    r = {"heartbeat_at": store.iso(NOW), "expires_after_s": 99999}
    assert store.lease_state(r, NOW + 2 * H, 2 * H)["state"] == "expired"        # policy 2h wins over a stored 99999
    r["expires_after_s"] = 600
    assert store.lease_state(r, NOW + 601, 2 * H)["state"] == "expired"          # a shorter record wins
    assert store.lease_state(r, NOW + 599, 2 * H)["state"] == "live"


def test_fix1_a_session_whose_lease_was_taken_over_may_not_push(team):
    set_ticket(team)
    set_claim(team, age=30, session_id="B", taken_over_from={"session_id": "A", "at": store.iso(time.time() - 60)})
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(team))
    def push(session):
        return subprocess.run([str(HOOK), "--local-green=off"], capture_output=True, text=True, env=env,
                              input=json.dumps({"tool_input": {"command": PUSH}, "cwd": str(team), "session_id": session}))
    r = push("A")
    assert refused_claim(r) and "taken over" in r.stdout + r.stderr
    assert push("B").returncode == 0                  # the new holder
    assert push("some-subagent").returncode == 0      # a worker dispatched by the holder is not the displaced session
