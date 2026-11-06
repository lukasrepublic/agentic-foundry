"""dl-03: the claim digest covers the whole ticket (Acceptance, Design, Security, Surfaces, Done means).

`claim` records a signed `ticket_digest`; `reconcile` stops dispatch with `ticket_changed` when any of the five
sections moves (`acceptance_changed` when only Acceptance did), and `claim_unverified` when the HMAC fails.
`gh` is never called: the owned-queue FakeGh answers. Assertions are on parsed fields, not joined text.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_owned_queue import FakeGh, ft, label_status, mk_issue, run_cli, with_perm, ws  # noqa: E402,F401  (ws is a fixture)

KEY = "example-org/app#2"
BODY = ("## Acceptance\n- AC-1: WHEN a user logs in, THE SYSTEM SHALL show the widget.\nGrounding: EARS.\n\n"
        "## Design\n- Components: scripts/a.py\n- Cites: FG-SEC-006\n\n"
        "## Security\n- FG-SEC-006\n\n"
        "## Surfaces\n- cli: reachable\n\n"
        "## Done means\n```\npython3 -m pytest -q\n```\n\n"
        "## Notes\nfree text outside the five\n")
SECTIONS = ("acceptance", "design", "security", "surfaces", "done_means")
EDITS = {
    "acceptance": ("SHALL show the widget", "SHALL show the gadget"),
    "design": ("scripts/a.py", "scripts/b.py"),
    "security": ("- FG-SEC-006\n\n## Surfaces", "- FG-SEC-007\n\n## Surfaces"),
    "surfaces": ("cli: reachable", "cli: unreachable"),
    "done_means": ("python3 -m pytest -q", "python3 -m pytest -x"),
}


def digest(body):
    return ft.ticket_digest(body)[0]


def claimed(ws, body=BODY, extra=()):
    gh = FakeGh(issues=[mk_issue(2, body=body)])
    rc, _, err = run_cli(["claim", "2", "--trust-author", *extra], gh)
    assert rc == 0, err
    label_status(gh.node(2), "in progress")
    return gh


def reconcile(gh):
    rc, out, _ = run_cli(["reconcile", "2", "--json"], with_perm(gh, "write"))
    return json.loads(out)["results"][0]


def test_same_body_same_digest_and_five_sections():
    d, parts = ft.ticket_digest(BODY)
    assert d == digest(BODY) and sorted(parts) == sorted(SECTIONS)
    assert len(d) == 64


@pytest.mark.parametrize("section", SECTIONS)
def test_each_section_change_flips_the_digest(section):
    old, new = EDITS[section]
    assert old in BODY
    d0, p0 = ft.ticket_digest(BODY)
    d1, p1 = ft.ticket_digest(BODY.replace(old, new))
    assert d1 != d0
    assert [k for k in SECTIONS if p0[k] != p1[k]] == [section]


def test_whitespace_only_change_does_not_flip():
    ws_body = BODY.replace("- AC-1:", "-   AC-1:").replace("\n\n", "\n\n\n").replace("```\npython", "```\n  python")
    assert ws_body != BODY and digest(ws_body) == digest(BODY)
    assert digest(BODY.replace("\n", "\r\n")) == digest(BODY)


def test_section_outside_the_five_does_not_flip():
    assert digest(BODY.replace("free text outside the five", "other text")) == digest(BODY)


def test_claim_records_a_signed_ticket_digest(ws):
    claimed(ws)
    rec = ft.load_claims(ws)[KEY]
    assert rec["ticket_digest"] == digest(BODY) and rec["done_digest"]
    assert sorted(rec["ticket_sections"]) == sorted(SECTIONS)
    assert ft.store.verify_claim(rec, ws) is True


def test_read_issue_json_exposes_ticket_digest(ws):
    gh = FakeGh(issues=[mk_issue(2, body=BODY)])
    rc, out, _ = run_cli(["read-issue", "2", "--json"], with_perm(gh, "write"))
    assert rc == 0 and json.loads(out)["ticket_digest"] == digest(BODY)


def test_unchanged_ticket_is_authorized(ws):
    r = reconcile(claimed(ws))
    assert (r["stop_dispatch"], r["reason"]) == (False, "authorized")


@pytest.mark.parametrize("section", ["design", "security", "surfaces", "done_means"])
def test_reconcile_ticket_changed_per_section(ws, section):
    gh = claimed(ws)
    old, new = EDITS[section]
    gh.node(2)["body"] = BODY.replace(old, new)
    r = reconcile(gh)
    assert (r["stop_dispatch"], r["reason"], r["changed_sections"]) == (True, "ticket_changed", [{"done_means": "definition_of_done"}.get(section, section)])


def test_acceptance_only_change_keeps_the_sub_reason(ws):
    gh = claimed(ws)
    gh.node(2)["body"] = BODY.replace(*EDITS["acceptance"])
    r = reconcile(gh)
    assert (r["stop_dispatch"], r["reason"]) == (True, "acceptance_changed")


def test_two_sections_changed_is_ticket_changed(ws):
    gh = claimed(ws)
    gh.node(2)["body"] = BODY.replace(*EDITS["acceptance"]).replace(*EDITS["design"])
    r = reconcile(gh)
    assert r["reason"] == "ticket_changed" and r["changed_sections"] == ["acceptance_criteria", "design"]


def test_next_skips_a_ticket_changed_atom(ws):
    gh = claimed(ws)
    gh.node(2)["body"] = BODY.replace(*EDITS["design"])
    res = json.loads(run_cli(["next", "--json"], with_perm(gh, "write"))[1])
    assert {"number": 2, "reason": "stop-dispatch:ticket_changed"}.items() <= next(
        s for s in res["skipped"] if s["number"] == 2).items()


def test_section_outside_the_five_keeps_dispatch_authorized(ws):
    gh = claimed(ws)
    gh.node(2)["body"] = BODY.replace("free text outside the five", "other text") + "\n## Links\n- x\n"
    r = reconcile(gh)
    assert (r["stop_dispatch"], r["reason"]) == (False, "authorized")


def test_dispatch_stays_stopped_until_the_operator_reclaims_with_the_new_digest(ws):
    gh = claimed(ws)
    gh.node(2)["body"] = BODY.replace(*EDITS["design"])
    assert reconcile(gh)["reason"] == "ticket_changed"
    # a plain re-claim keeps the original baseline (same rule as the body baseline): still stopped
    assert run_cli(["claim", "2", "--trust-author"], gh)[0] == 0
    assert reconcile(gh)["reason"] == "ticket_changed"
    # the explicit re-claim records the new digest and re-signs
    assert run_cli(["claim", "2", "--trust-author", "--rebase-acceptance"], gh)[0] == 0
    rec = ft.load_claims(ws)[KEY]
    assert rec["ticket_digest"] == digest(gh.node(2)["body"]) and ft.store.verify_claim(rec, ws)
    assert reconcile(gh)["stop_dispatch"] is False


@pytest.mark.parametrize("field,value", [("ticket_digest", "0" * 64), ("done_digest", "1" * 64),
                                          ("ticket_sections", {k: "2" * 64 for k in SECTIONS})])
def test_tampered_record_is_refused_and_names_the_atom(ws, field, value):
    gh = claimed(ws)
    cl = ft.load_claims(ws)
    cl[KEY][field] = value
    ft.store.update_claims(ws, lambda c: c.__setitem__(KEY, cl[KEY]))
    r = reconcile(gh)
    assert (r["stop_dispatch"], r["reason"], r["issue"]) == (True, "claim_unverified", KEY)
    assert KEY in r["cause"]


def test_hand_edit_that_matches_the_new_ticket_is_still_refused(ws):
    """The attack: edit the ticket, then edit the local digest to match. The signature does not move."""
    gh = claimed(ws)
    gh.node(2)["body"] = BODY.replace(*EDITS["design"])
    new_d, new_s = ft.ticket_digest(gh.node(2)["body"])
    ft.store.update_claims(ws, lambda c: c[KEY].update(ticket_digest=new_d, ticket_sections=new_s))
    assert reconcile(gh)["reason"] == "claim_unverified"


def test_stripped_signature_is_refused(ws):
    gh = claimed(ws)
    ft.store.update_claims(ws, lambda c: c[KEY].pop("claim_sig"))
    assert reconcile(gh)["reason"] == "claim_unverified"


def test_plain_reclaim_over_a_tampered_baseline_is_refused(ws):
    """Block B: a plain claim never adopts the live digest over an unverifiable baseline."""
    gh = claimed(ws)
    gh.node(2)["body"] = BODY.replace(*EDITS["design"])
    new_d, new_s = ft.ticket_digest(gh.node(2)["body"])
    ft.store.update_claims(ws, lambda c: c[KEY].update(ticket_digest=new_d, ticket_sections=new_s))
    before = ft.load_claims(ws)[KEY]
    rc, _, err = run_cli(["claim", "2", "--trust-author"], gh)
    assert rc == 2 and "baseline-unverified" in err and "--rebase-acceptance" in err
    assert ft.load_claims(ws)[KEY] == before
    assert run_cli(["claim", "2", "--trust-author", "--rebase-acceptance"], gh)[0] == 0
    rec = ft.load_claims(ws)[KEY]
    assert ft.store.verify_claim(rec, ws) and rec["ticket_digest"] == new_d


def test_downgrade_by_deleting_digest_and_signature_is_refused(ws):
    """Block A: remove ticket_digest + claim_sig, set body/done digests to the live values: not authorized."""
    gh = claimed(ws)
    live = gh.node(2)["body"]
    def strip(c):
        for f in ("ticket_digest", "ticket_sections", "claim_sig"):
            c[KEY].pop(f, None)
        c[KEY]["body_sha256"] = ft.store.command_digest(live)
        c[KEY]["done_digest"] = ft.store.command_digest("python3 -m pytest -q")
    ft.store.update_claims(ws, strip)
    r = reconcile(gh)
    assert (r["stop_dispatch"], r["reason"]) == (True, "claim_unverified")
    assert "--rebase-acceptance" in r["cause"] and KEY in r["cause"]


def test_prior_with_edited_done_fields_is_refused_not_resigned(ws):
    """Block C: a hand-edited done_digest/done_trusted in the prior record is not copied and re-signed."""
    gh = claimed(ws)
    ft.store.update_claims(ws, lambda c: c[KEY].update(done_digest="3" * 64, done_trusted=True))
    rc, _, err = run_cli(["claim", "2", "--trust-author"], gh)
    assert rc == 2 and "baseline-unverified" in err
    rec = ft.load_claims(ws)[KEY]
    assert rec["done_digest"] == "3" * 64 and not ft.store.verify_claim(rec, ws)


def test_key_is_shared_across_worktrees(tmp_path):
    """Block D: a claim signed under one worktree verifies from another worktree and the main clone."""
    import subprocess
    main = tmp_path / "main"
    main.mkdir()
    g = lambda *a, cwd=main: subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True)  # noqa: E731
    g("init", "-q", "-b", "main")
    g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i")
    w1, w2 = tmp_path / "w1", tmp_path / "w2"
    g("worktree", "add", "-q", str(w1), "-b", "b1")
    g("worktree", "add", "-q", str(w2), "-b", "b2")
    rec = {"issue": "o/r#1", "ticket_digest": "a" * 64, "ticket_sections": {"acceptance": "b" * 64}, "done_digest": "c" * 64}
    assert ft.store.sign_claim(rec, w1, create=True)
    assert ft.store.verify_claim(rec, w2) and ft.store.verify_claim(rec, main)
    rec["done_digest"] = "d" * 64
    assert not ft.store.verify_claim(rec, w2)
