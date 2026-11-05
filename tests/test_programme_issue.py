"""dl-06: scripts/foundry_programme_issue.py and the deck report's --programme-issue emitter (offline, stubbed gh)."""
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import foundry_deck_report as dr  # noqa: E402
import foundry_execution_plan as ep  # noqa: E402
import foundry_programme_issue as pi  # noqa: E402

COST = "agents started 2 | longest agent 40 calls | items closed 1"
REPORT = "Your decisions: none\nBuilding now\n- a-two\nLedger: none\n" + COST + "\n"


def atom(i, blocked_by=(), paths=(), **kw):
    d = {"id": i, "node_id": f"N_{i}", "item_id": f"I_{i}", "blocked_by": list(blocked_by),
         "paths": list(paths), "labels": [], "state": "open"}
    d.update(kw)
    return d


@pytest.fixture
def plan():
    corpus = {"project_id": "P", "atoms": [atom("a-one", paths=["x/"]), atom("a-two", ["a-one"], ["y/"]),
                                           atom("a-three", ["a-two"], ["z/"], labels=["hold"])]}
    return ep.build_plan("prog", corpus)


@pytest.fixture
def plan_file(tmp_path, plan):
    f = tmp_path / "execution-plan.yaml"
    f.write_text(ep.render(plan))
    return f


@pytest.fixture
def gh(monkeypatch):
    calls = []
    state = {"body": "", "unreachable": False}

    def fake(args, payload=None):
        if state["unreachable"]:
            raise pi.Unreachable("down")
        calls.append((list(args), payload))
        if args[0].startswith("repos/") and payload is None:
            return {"body": state["body"]}
        if args[:2] == ["-X", "PATCH"]:
            state["body"] = payload["body"]
            return {}
        if args == ["graphql"] and "createProjectV2StatusUpdate" in payload["query"]:
            return {"data": {}}
        return {"data": {"repositoryOwner": {"projectV2": {"id": "PVT_1"}}}}
    monkeypatch.setattr(pi, "gh_api", fake)
    return calls, state


def test_render_has_header_waves_critical_path_parked_and_override_block(plan):
    body = pi.render_body(plan, {"states": {"a-one": "merged", "a-two": "building"},
                                 "parked": [{"item": "idea-x", "reason": "no owner"}]})
    lines = body.splitlines()
    assert lines[0] == "**prog: 1/3 done, open wave 2**"
    assert "- batch `w1-a-one` (float 0)" in lines and "  - `a-one`: merged" in lines
    assert "  - `a-two`: building" in lines and "  - `a-three`: held" in lines
    assert "- idea-x -- no owner" in lines
    i = lines.index("## Critical path")
    assert lines[i + 2].startswith("`w1-a-one` -> ")
    assert "- [ ] <!-- hold:a-one --> Hold `a-one`" in lines
    assert "- [ ] <!-- release-hold:a-three --> Release the hold on `a-three`" in lines
    assert "- [ ] <!-- pause-wave:2 --> Pause wave 2" in lines


def test_checked_boxes_survive_a_regenerate_and_are_applied(plan):
    first = pi.render_body(plan)
    edited = first.replace("- [ ] <!-- hold:a-one -->", "- [x] <!-- hold:a-one -->") \
                  .replace("- [ ] <!-- pause-wave:1 -->", "- [X] <!-- pause-wave:1 -->") \
                  .replace("- [ ] <!-- release-hold:a-three -->", "- [x] <!-- release-hold:a-three -->")
    again = pi.render_body(plan, None, edited)
    assert "- [x] <!-- hold:a-one -->" in again and "- [x] <!-- pause-wave:1 -->" in again
    assert "### Wave 1 (paused)" in again
    assert "  - `a-one`: held" in again                  # the override applies
    assert "  - `a-three`: blocked" in again             # a released hold leaves "held"
    checked, _ = pi.read_overrides(again)
    assert checked == {"hold": ["a-one"], "release-hold": ["a-three"], "pause-wave": ["1"]}
    assert pi.render_body(plan, None, again) == again   # a fixed point


def test_a_checked_marker_that_left_the_plan_stays():
    body = "- [x] <!-- hold:gone-atom -->\n"
    p = ep.build_plan("prog", {"project_id": "P", "atoms": [atom("a-one")]})
    assert "- [x] <!-- hold:gone-atom -->" in pi.render_body(p, None, body)


def test_publish_is_idempotent(plan, gh):
    calls, state = gh
    ref = ("o", "r", 7)
    body = pi.render_body(plan)
    assert pi.publish(ref, body) == "edited"
    assert pi.publish(ref, body) == "unchanged"
    assert [c for c in calls if c[0][:2] == ["-X", "PATCH"]] == [(["-X", "PATCH", "repos/o/r/issues/7"], {"body": body})]


def test_publish_dry_run_sends_nothing(plan, gh):
    calls, _ = gh
    assert pi.publish(("o", "r", 7), pi.render_body(plan), dry_run=True) == "dry-run"
    assert not [c for c in calls if c[1] is not None]


def _st(plan, **s):
    return pi.atom_states(plan, {"states": s})


def test_status_mapping(plan):
    allm = _st(plan, **{"a-one": "merged", "a-two": "merged", "a-three": "merged"})
    assert pi.status_for(plan, allm, stalled=True) == "COMPLETE"
    assert pi.status_for(plan, _st(plan), stalled=True) == "OFF_TRACK"
    assert pi.status_for(plan, _st(plan, **{"a-one": "building"})) == "ON_TRACK"
    # open wave has nothing ready or running and the critical path is unstarted
    assert pi.status_for(plan, _st(plan, **{"a-one": "blocked"})) == "AT_RISK"
    assert pi.status_for(plan, _st(plan)) == "ON_TRACK"


def test_stall_comes_from_the_cost_log(tmp_path, plan_file, gh, capsys):
    log = tmp_path / "cost.log"
    log.write_text("".join(f"2026-11-05T0{i}:00:00Z agents started 2 | longest agent 9 calls | items closed 0\n"
                           for i in range(3)))
    rep = tmp_path / "r.md"
    rep.write_text(REPORT)
    assert pi.main(["status-update", "prog", "--plan", str(plan_file), "--project", "o/1", "--report", str(rep),
                    "--cost-log", str(log)]) == 0
    assert capsys.readouterr().out.startswith("OFF_TRACK: posted")


def test_status_update_posts_exactly_one_update_with_the_report(tmp_path, plan_file, gh, capsys):
    calls, _ = gh
    rep = tmp_path / "r.md"
    rep.write_text(REPORT)
    assert pi.main(["status-update", "prog", "--plan", str(plan_file), "--project", "o/1", "--report", str(rep)]) == 0
    sent = [c for c in calls if c[1] and "createProjectV2StatusUpdate" in c[1]["query"]]
    assert len(sent) == 1
    assert sent[0][1]["variables"]["input"] == {"projectId": "PVT_1", "status": "ON_TRACK", "body": REPORT.strip()}


def test_report_without_cost_line_is_refused(tmp_path, plan_file, gh, capsys):
    rep = tmp_path / "r.md"
    rep.write_text("Your decisions: none\nLedger: none\n")
    for argv in (["render", "prog", "--plan", str(plan_file), "--report", str(rep)],
                 ["status-update", "prog", "--plan", str(plan_file), "--project", "o/1", "--report", str(rep)]):
        assert pi.main(argv) == 2
    err = capsys.readouterr().err
    assert err.count("no cost line") == 2
    assert not [c for c in gh[0] if c[1]]


def test_outage_renders_from_the_cached_plan_and_sends_nothing(tmp_path, plan_file, gh, capsys):
    calls, state = gh
    state["unreachable"] = True
    rep = tmp_path / "r.md"
    rep.write_text(REPORT)
    assert pi.main(["publish", "prog", "--plan", str(plan_file), "--issue", "o/r#7", "--report", str(rep)]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("**prog: 0/3 done")
    assert out.rstrip().endswith(pi.OUTAGE_LINE)
    assert pi.main(["status-update", "prog", "--plan", str(plan_file), "--project", "o/1", "--report", str(rep)]) == 0
    assert pi.OUTAGE_LINE in capsys.readouterr().out
    assert not calls


def test_overrides_verb_prints_checked_boxes(plan, gh, capsys):
    _, state = gh
    state["body"] = pi.render_body(plan).replace("- [ ] <!-- hold:a-two -->", "- [x] <!-- hold:a-two -->")
    assert pi.main(["overrides", "prog", "--issue", "o/r#7"]) == 0
    assert json.loads(capsys.readouterr().out) == {"hold": ["a-two"], "release-hold": [], "pause-wave": []}


def test_publish_keeps_a_box_checked_on_github(plan, plan_file, gh, capsys):
    _, state = gh
    state["body"] = pi.render_body(plan).replace("- [ ] <!-- hold:a-two -->", "- [x] <!-- hold:a-two -->")
    assert pi.main(["publish", "prog", "--plan", str(plan_file), "--issue", "o/r#7"]) == 0
    assert "- [x] <!-- hold:a-two -->" in state["body"]


def test_wrong_programme_and_missing_plan_are_refused(plan_file, capsys):
    assert pi.main(["render", "other", "--plan", str(plan_file)]) == 2
    assert pi.main(["render", "prog"]) == 2
    assert "refused" in capsys.readouterr().err


def test_deck_report_programme_issue_emitter_writes_the_body_file(tmp_path):
    out = tmp_path / "body.md"
    tick = {"progress": {"done": 1, "total": 3, "moved": 1}, "merged": 1, "verified": 1,
            "decisions": [], "building": [], "needs_you": [], "ledger": [], "ready_clusters": 0,
            "running_builders": 0, "cost": {"agents_started": 2, "longest_agent_calls": 40, "items_closed": 1}}
    tf = tmp_path / "tick.json"
    tf.write_text(json.dumps(tick))
    (tmp_path / ".foundry").mkdir()
    assert dr.main(["--project-dir", str(tmp_path), "--in", str(tf), "--programme-issue", str(out)]) == 0
    text = out.read_text()
    assert text.rstrip().splitlines()[-1] == COST
    assert pi.check_report(text).endswith(COST)


def test_deck_report_refuses_a_symlinked_body_file(tmp_path):
    (tmp_path / ".foundry").mkdir()
    target = tmp_path / "t.md"
    target.write_text("keep")
    link = tmp_path / "l.md"
    link.symlink_to(target)
    tf = tmp_path / "tick.json"
    tf.write_text(json.dumps({"progress": {"done": 0, "total": 1, "moved": 0}, "merged": 0, "verified": 0,
                              "decisions": [], "building": [], "needs_you": [], "ledger": [],
                              "ready_clusters": 0, "running_builders": 0, "stall_reason": "x",
                              "cost": {"agents_started": 0, "longest_agent_calls": 0, "items_closed": 0}}))
    assert dr.main(["--project-dir", str(tmp_path), "--in", str(tf), "--programme-issue", str(link)]) == 2
    assert target.read_text() == "keep"
