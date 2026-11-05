"""dl-14: the S5 cost census, policy keys, tick section, cost line and the cost-without-progress stall class."""
import importlib.util
import os
import io
import json
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import foundry_blocker_check as bc  # noqa: E402
import foundry_deck_report as dr  # noqa: E402
import foundry_operating_model as om  # noqa: E402

CENSUS = SCRIPTS / "deck-cost-census.py"
spec = importlib.util.spec_from_file_location("deck_cost_census", CENSUS)
census = importlib.util.module_from_spec(spec)
spec.loader.exec_module(census)

TEMPLATE = Path(__file__).resolve().parent.parent / "skills" / "command-deck" / "tick-prompt.template.md"
LINE = "agents started 2 | longest agent 40 calls | items closed 3"


def _agent(path, ts, calls, mtime=None):
    recs = [{"timestamp": ts, "message": {"role": "user", "content": "go"}}]
    for _ in range(calls):
        recs.append({"timestamp": ts, "message": {"role": "assistant",
                                                  "content": [{"type": "text", "text": "x"}, {"type": "tool_use", "name": "Bash"}]}})
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    t = census.parse_ts(mtime or ts).timestamp()
    os.utime(path, (t, t))


@pytest.fixture()
def session(tmp_path):
    d = tmp_path / "subagents"
    d.mkdir()
    _agent(d / "agent-old.jsonl", "2026-11-05T08:00:00Z", 500)   # before the previous tick: not counted
    _agent(d / "agent-a.jsonl", "2026-11-05T10:30:00Z", 40)
    _agent(d / "agent-b.jsonl", "2026-11-05T10:45:00Z", 7)
    prs = tmp_path / "prs.json"
    prs.write_text(json.dumps({"merged": [{"mergedAt": "2026-11-05T10:40:00Z"}, {"mergedAt": "2026-11-05T09:00:00Z"},
                                          {"mergedAt": "2026-11-05T11:00:00Z"}],
                               "authorized": [{"at": "2026-11-05T10:50:00Z"}]}))
    return tmp_path, prs


def test_census_counts_the_fixture(session, capsys):
    root, prs = session
    rc = census.main(["--session-dir", str(root), "--since", "2026-11-05T10:00:00Z", "--prs", str(prs)])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "agents started 2 | longest agent 40 calls | items closed 3"


def test_census_without_since_counts_everything(session, capsys):
    root, prs = session
    assert census.main(["--session-dir", str(root), "--prs", str(prs)]) == 0
    assert capsys.readouterr().out.strip() == "agents started 3 | longest agent 500 calls | items closed 4"


def test_census_append_writes_stamp_and_line(session, tmp_path):
    root, prs = session
    log = tmp_path / "cost-reports.log"
    assert census.main(["--session-dir", str(root), "--since", "2026-11-05T10:00:00Z", "--prs", str(prs),
                        "--append", str(log)]) == 0
    stamp, rest = log.read_text().rstrip("\n").split(" ", 1)
    assert stamp.endswith("Z") and rest == LINE


def test_census_refuses_a_missing_session_dir(tmp_path, capsys):
    assert census.main(["--session-dir", str(tmp_path / "nope")]) == 2


def _log(tmp_path, rows):
    p = tmp_path / "cost-reports.log"
    p.write_text("".join(f"2026-11-05T10:0{i}:00Z {r}\n" for i, r in enumerate(rows)))
    return p


STALLED = "agents started 2 | longest agent 30 calls | items closed 0"
PROGRESS = "agents started 2 | longest agent 30 calls | items closed 1"


def test_stall_three_lines_exit_3(tmp_path, capsys):
    assert census.main(["--stall-check", str(_log(tmp_path, [STALLED] * 3))]) == 3
    assert "cost-without-progress" in capsys.readouterr().out


def test_stall_two_lines_exit_0(tmp_path):
    assert census.main(["--stall-check", str(_log(tmp_path, [STALLED] * 2))]) == 0


def test_progress_breaks_the_run(tmp_path):
    assert census.main(["--stall-check", str(_log(tmp_path, [STALLED, PROGRESS, STALLED, STALLED]))]) == 0


def test_a_ci_pending_line_is_skipped_not_reset(tmp_path):
    rows = [STALLED, STALLED + " ci-pending", STALLED]
    assert census.main(["--stall-check", str(_log(tmp_path, rows))]) == 0            # only 2 counted lines
    assert census.main(["--stall-check", str(_log(tmp_path, rows + [STALLED]))]) == 3  # 3 counted, the pending one skipped
    assert census.main(["--stall-check", str(_log(tmp_path, [STALLED, STALLED, PROGRESS + " ci-pending", STALLED]))]) == 3


def test_zero_agents_is_not_a_stall(tmp_path):
    assert census.main(["--stall-check", str(_log(tmp_path, ["agents started 0 | longest agent 0 calls | items closed 0"] * 3))]) == 0


def test_stall_after_ticks_is_honoured(tmp_path):
    assert census.main(["--stall-check", str(_log(tmp_path, [STALLED] * 2)), "--stall-after", "2"]) == 3


def test_cli_stall_exit_code(tmp_path):
    r = subprocess.run([sys.executable, str(CENSUS), "--stall-check", str(_log(tmp_path, [STALLED] * 3))],
                       capture_output=True, text=True)
    assert r.returncode == 3 and "cost-without-progress" in r.stdout


# --- policy keys and the rendered section ---------------------------------------------------------
def _policy(tmp_path, text=""):
    (tmp_path / ".foundry").mkdir(parents=True, exist_ok=True)
    if text:
        (tmp_path / ".foundry" / "operating-model.yaml").write_text(text)
    return om.load(str(tmp_path))


def test_policy_defaults(tmp_path):
    p = _policy(tmp_path)
    assert [p.get(k) for k in ("deck.max_agent_calls", "deck.max_wait_s", "deck.note_max_tokens",
                               "deck.stall_after_ticks")] == [150, 60, 2000, 3]


def test_s5_renders_the_policy_numbers(tmp_path):
    p = _policy(tmp_path, "deck:\n  max_agent_calls: 90\n  max_wait_s: 30\n  note_max_tokens: 1500\n  stall_after_ticks: 4\n")
    text = om.render_tick_section(p, "S5")
    assert "after 90 tool calls" in text and "at most 1,500\n   tokens" in text
    assert "more than 30 seconds" in text and text.count("Four consecutive reports") == 1
    d = om.render_tick_section(_policy(tmp_path / "d"), "S5")
    assert "150 tool calls" in d and "2,000" in d and "60 seconds" in d and "Three consecutive" in d
    assert "agents started N | longest agent C calls | items closed K" in d and "§5 relaxes no floor" in d


def test_template_carries_s5_after_the_fan_out(tmp_path):
    t = TEMPLATE.read_text()
    assert "{{SECTION:S5}}" in t and "items closed" not in t.split("{{SECTION:S5}}")[0]
    out = om.render_tick_template(_policy(tmp_path), t, "/w", "op")
    assert "items closed K" in out and "{{" not in out


# --- the cost line in the report ------------------------------------------------------------------
def _tick(**over):
    t = {"now": "2026-11-05T10:15:00Z", "progress": {"done": 1, "total": 2, "moved": 1}, "merged": 1, "verified": 1,
         "ready_clusters": 1, "running_builders": 1, "decisions": [], "building": [], "needs_you": [], "ledger": [],
         "cost": {"agents_started": 2, "longest_agent_calls": 40, "items_closed": 3}}
    t.update(over)
    return t


def _run(tmp_path, tick, *extra):
    (tmp_path / ".foundry").mkdir(exist_ok=True)
    f = tmp_path / "t.json"
    f.write_text(json.dumps(tick))
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = dr.main(["--project-dir", str(tmp_path), "--in", str(f), *extra])
    return rc, out.getvalue(), err.getvalue()


def test_report_without_the_cost_line_is_refused(tmp_path):
    t = _tick()
    del t["cost"]
    rc, out, err = _run(tmp_path, t)
    assert rc == 2 and out == "" and "refused: cost-line-missing" in err


@pytest.mark.parametrize("bad", [{"agents_started": -1, "longest_agent_calls": 1, "items_closed": 0},
                                 {"agents_started": 1}, "x"])
def test_a_malformed_cost_is_refused(tmp_path, bad):
    rc, _, err = _run(tmp_path, _tick(cost=bad))
    assert rc == 2 and "cost-line-missing" in err


def test_report_carries_the_cost_line_and_appends_it(tmp_path):
    log = tmp_path / "cost-reports.log"
    rc, out, _ = _run(tmp_path, _tick(), "--append-cost", str(log))
    assert rc == 0 and LINE in out.splitlines()
    stamp, rest = log.read_text().rstrip("\n").split(" ", 1)
    assert stamp.endswith("Z") and rest == LINE


def test_a_refused_report_appends_nothing(tmp_path):
    log = tmp_path / "cost-reports.log"
    t = _tick()
    del t["cost"]
    assert _run(tmp_path, t, "--append-cost", str(log))[0] == 2
    assert not log.exists()


def test_cost_line_string_form(tmp_path):
    t = _tick(cost_line=LINE)
    del t["cost"]
    assert LINE in _run(tmp_path, t)[1].splitlines()


# --- the stall class in the blocker lint ----------------------------------------------------------
def _stall(rows, **over):
    c = {"stall_class": "cost-without-progress", "evidence": [f"2026-11-05T10:0{i}:00Z {r}" for i, r in enumerate(rows)]}
    c.update(over)
    return c


def test_cost_without_progress_is_accepted_with_three_log_lines():
    out = bc.partition([_stall([STALLED] * 3)])
    assert len(out["blockers"]) == 1 and out["next_tasks"] == []


@pytest.mark.parametrize("cand", [_stall([STALLED] * 2), _stall([STALLED, STALLED, PROGRESS]),
                                  _stall([STALLED, STALLED, STALLED + " ci-pending"]),
                                  _stall([STALLED] * 3, stall_class="other-stall"), _stall([STALLED] * 3, extra=1)])
def test_a_bad_stall_candidate_is_demoted(cand):
    out = bc.partition([cand])
    assert out["blockers"] == [] and len(out["next_tasks"]) == 1


# --- round 2 ---------------------------------------------------------------------------------------
def test_stall_after_must_be_at_least_one(tmp_path, capsys):
    for bad in ("0", "-2"):
        assert census.main(["--stall-check", str(_log(tmp_path, [STALLED] * 3)), "--stall-after", bad]) == 2


def test_stall_after_defaults_to_the_policy(tmp_path, monkeypatch):
    proj = tmp_path / "p"
    (proj / ".foundry").mkdir(parents=True)
    (proj / ".foundry" / "operating-model.yaml").write_text("deck:\n  stall_after_ticks: 2\n")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    assert census.main(["--stall-check", str(_log(tmp_path, [STALLED] * 2))]) == 3
    assert census.main(["--stall-check", str(_log(tmp_path, [STALLED] * 1))]) == 0


def test_the_log_regex_rejects_a_trailing_newline():
    assert dr.parse_log_line("2026-11-05T10:00:00Z " + STALLED) is not None
    assert dr.parse_log_line("2026-11-05T10:00:00Z " + STALLED + "\n") is None
    assert dr.COST_LINE_RE.fullmatch(LINE + "\n") is None


def test_one_implementation_of_the_log_logic():
    assert not hasattr(census, "stall") and not hasattr(census, "_LOG_RE")
    assert not hasattr(bc, "_COST_LOG_LINE")


def test_census_refuses_a_session_dir_without_agent_dirs(tmp_path, capsys):
    (tmp_path / "other.jsonl").write_text("{}\n")
    assert census.main(["--session-dir", str(tmp_path)]) == 2


def test_census_skips_symlinks(session, tmp_path):
    root, prs = session
    (tmp_path / "elsewhere.jsonl").write_text("{}\n")
    _agent(tmp_path / "real.jsonl", "2026-11-05T10:31:00Z", 999)
    os.symlink(tmp_path / "real.jsonl", root / "subagents" / "agent-link.jsonl")
    n, c = census.count_agents(str(root), census.parse_ts("2026-11-05T10:00:00Z"))
    assert (n, c) == (2, 40)


def test_a_long_agent_spanning_ticks_shows_in_c(tmp_path):
    d = tmp_path / "subagents"
    d.mkdir()
    _agent(d / "agent-long.jsonl", "2026-11-05T08:00:00Z", 300, mtime="2026-11-05T10:20:00Z")   # started before, active since
    _agent(d / "agent-new.jsonl", "2026-11-05T10:30:00Z", 5)
    assert census.count_agents(str(tmp_path), census.parse_ts("2026-11-05T10:00:00Z")) == (1, 300)


@pytest.mark.parametrize("body", ['"x"', '{"merged": "nope"}', '{"merged": [1]}', '[1, 2]', '{"authorized": {"a": 1}}', 'not json'])
def test_malformed_prs_exit_2_without_a_traceback(tmp_path, body):
    (tmp_path / "subagents").mkdir()
    f = tmp_path / "p.json"
    f.write_text(body)
    r = subprocess.run([sys.executable, str(CENSUS), "--session-dir", str(tmp_path), "--prs", str(f)],
                       capture_output=True, text=True)
    assert r.returncode == 2 and "Traceback" not in r.stderr


def test_append_to_an_unwritable_log_exits_2(session, tmp_path):
    root, _ = session
    assert census.main(["--session-dir", str(root), "--append", str(tmp_path / "no" / "such" / "log")]) == 2


def test_repo_needs_a_branch_prefix(session):
    root, _ = session
    assert census.main(["--session-dir", str(root), "--repo", "o/r"]) == 2


def test_repo_counts_only_the_branch_prefix_and_authorized_comes_from_prs(session, monkeypatch, capsys):
    root, prs = session
    rows = [{"mergedAt": "2026-11-05T10:40:00Z", "headRefName": "dl/dl-1"},
            {"mergedAt": "2026-11-05T10:41:00Z", "headRefName": "other/x"}]
    seen = {}

    def fake(repo, prefix, since=None):
        seen.update(repo=repo, prefix=prefix)
        return [r for r in rows if r["headRefName"].startswith(prefix)]
    monkeypatch.setattr(census, "gh_merged", fake)
    assert census.main(["--session-dir", str(root), "--since", "2026-11-05T10:00:00Z", "--repo", "o/r",
                        "--branch-prefix", "dl/", "--prs", str(prs)]) == 0
    assert capsys.readouterr().out.strip().endswith("items closed 2")     # 1 gh PR + 1 authorized atom from --prs
    assert seen == {"repo": "o/r", "prefix": "dl/"}


def test_report_ci_pending_tag_and_skip(tmp_path):
    log = tmp_path / "cost-reports.log"
    assert _run(tmp_path, _tick(cost={"agents_started": 1, "longest_agent_calls": 1, "items_closed": 0}),
                "--append-cost", str(log), "--ci-pending")[0] == 0
    assert log.read_text().rstrip("\n").endswith(" ci-pending")


def test_the_cost_line_is_a_trailer_outside_max_lines(tmp_path):
    (tmp_path / ".foundry").mkdir(exist_ok=True)
    import yaml
    (tmp_path / ".foundry" / "operating-model.yaml").write_text(yaml.safe_dump({"report": {"max_lines": 6, "timezone": "UTC"}}))
    rc, out, err = _run(tmp_path, _tick(ledger=["one"]))
    assert rc == 0, err
    ls = out.rstrip("\n").split("\n")
    assert ls[-1] == LINE and len(ls) == 6 + 1


def test_blocker_evidence_must_match_the_policy_count_and_order(tmp_path, monkeypatch):
    proj = tmp_path / "p"
    (proj / ".foundry").mkdir(parents=True)
    (proj / ".foundry" / "operating-model.yaml").write_text("deck:\n  stall_after_ticks: 4\n")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    assert bc.partition([_stall([STALLED] * 3)])["blockers"] == []
    assert len(bc.partition([_stall([STALLED] * 4)])["blockers"]) == 1
    rev = _stall([STALLED] * 3)
    rev["evidence"] = list(reversed(rev["evidence"]))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR")
    assert bc.partition([rev])["blockers"] == []
    assert bc.partition([_stall([STALLED] * 3, evidence=_stall([STALLED] * 3)["evidence"][:2] + [_stall([STALLED] * 3)["evidence"][2] + "\n"])])["blockers"] == []


def test_a_non_regular_cost_log_is_refused(tmp_path, capsys):
    assert census.main(["--stall-check", "/dev/null"]) == 2
    with pytest.raises(dr.Refused):
        dr.read_cost_log("/dev/null")
    assert dr.read_cost_log(str(tmp_path / "missing.log")) == []


def test_repo_with_a_list_shaped_prs_is_refused(session, capsys):
    root, _ = session
    f = root / "list.json"
    f.write_text('["2026-11-05T10:40:00Z"]')
    assert census.main(["--session-dir", str(root), "--repo", "o/r", "--branch-prefix", "dl/", "--prs", str(f)]) == 2
    assert "list-shaped" in capsys.readouterr().err
