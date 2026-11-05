"""gv-13: the deck report renderer and the pause flag (AC-OM-7, B9, E1, and the message-log findings).

Local files only. No network, no git remote, no gh.
"""
import io
import json
import os
import subprocess
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "foundry_deck_report.py"
sys.path.insert(0, str(ROOT / "scripts"))
import foundry_deck_report as dr  # noqa: E402
import foundry_message_log as ml  # noqa: E402
import foundry_operating_model as om  # noqa: E402

BANNED = "b" + "us"          # the retired word, built from fragments so this file never carries it
BLOCKS = ("Your decisions", "Building now", "Needs you", "Ledger")
DEFAULT_MAX = 8


def tick(**over):
    t = {
        "now": "2026-11-04T10:15:00Z",
        "progress": {"done": 12, "total": 40, "moved": 3},
        "merged": 2, "verified": 1,
        "ready_clusters": 1, "running_builders": 1,
        "decisions": [],
        "building": ["Faster login check"],
        "needs_you": [],
        "ledger": ["Merged the cache fix"],
        "cost": {"agents_started": 1, "longest_agent_calls": 20, "items_closed": 2},
    }
    t.update(over)
    return t


@pytest.fixture()
def proj(tmp_path):
    (tmp_path / ".foundry").mkdir()
    return tmp_path


def policy(proj, **report):
    import yaml
    (proj / ".foundry" / "operating-model.yaml").write_text(yaml.safe_dump({"report": report}))


def run(proj, t, *extra):
    f = proj / "tick.json"
    f.write_text(json.dumps(t))
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = dr.main(["--project-dir", str(proj), "--in", str(f), *extra])
    return rc, out.getvalue(), err.getvalue()


def lines(text):
    return text.rstrip("\n").split("\n")


def is_heading(line, title):
    return line == title or line.startswith(title + ":") or line.startswith(title + " (")


def block(ls, title):
    """The lines under `title` (up to the next heading); [] for an empty block ("Title: none")."""
    heads = [i for i, l in enumerate(ls) if any(is_heading(l, t) for t in BLOCKS)]
    i = next(k for k in heads if is_heading(ls[k], title))
    nxt = [k for k in heads if k > i]
    return ls[i + 1:(nxt[0] if nxt else len(ls))] if ls[i] == title or ls[i].startswith(title + " (") else []


# ---- AC-OM-7 ------------------------------------------------------------------------------------------
def test_valid_tick_renders_exactly_four_blocks_in_order(proj):
    policy(proj, timezone="UTC", max_lines=20)
    rc, out, err = run(proj, tick())
    assert rc == 0 and err == ""
    ls = lines(out)
    heads = [t for l in ls for t in BLOCKS if is_heading(l, t)]
    assert heads == list(BLOCKS)
    assert ls[0] == "2026-11-04 10:15 UTC - 12 of 40 done, +3 moved"
    assert block(ls, "Building now") == ["- Faster login check"] and block(ls, "Ledger") == ["- Merged the cache fix"]


def test_an_empty_block_is_one_line(proj):
    policy(proj, timezone="UTC", max_lines=20)
    _, out, _ = run(proj, tick())
    assert "Your decisions: none" in lines(out) and "Needs you: none" in lines(out)


def test_every_line_counts_toward_max_lines_headings_included(proj):
    policy(proj, timezone="UTC", max_lines=7)
    rc, out, _ = run(proj, tick(building=[], ledger=[]))
    assert rc == 0 and len(lines(out)) == 6        # first line + cost line + four one-line blocks
    rc, out, _ = run(proj, tick(building=[], ledger=["one"]))
    assert rc == 0 and len(lines(out)) == 7        # exactly at the limit
    rc, out, err = run(proj, tick(building=[], ledger=["one", "two"]))
    assert rc == 2 and out == "" and "too-many-lines:8>max_lines=7" in err


def test_the_output_never_exceeds_max_lines(proj):
    for n in (7, 8, 12):
        policy(proj, timezone="UTC", max_lines=n)
        rc, out, _ = run(proj, tick(ledger=[]))
        assert rc == 0 and len(lines(out)) <= n


def test_the_default_max_lines_is_used_when_policy_is_silent(proj):
    rc, out, err = run(proj, tick(ledger=[f"item {i}" for i in range(12)]))
    assert rc == 2 and "too-many-lines" in err and f"max_lines={DEFAULT_MAX}" in err
    rc, out, _ = run(proj, tick())
    assert rc == 0 and len(lines(out)) <= DEFAULT_MAX


def test_schema_minimum_for_max_lines_is_six():
    assert om.validate_text("report:\n  max_lines: 5\n") != []
    assert om.validate_text("report:\n  max_lines: 6\n") == []


@pytest.mark.parametrize("block_key", ["decisions", "building", "needs_you", "ledger"])
def test_a_missing_block_is_refused_naming_it(proj, block_key):
    policy(proj, timezone="UTC", max_lines=20)
    t = tick()
    del t[block_key]
    rc, out, err = run(proj, t)
    assert rc == 2 and out == "" and f"missing-block:{block_key}" in err


def test_a_block_that_is_not_a_list_is_refused(proj):
    policy(proj, timezone="UTC", max_lines=20)
    rc, _, err = run(proj, tick(ledger="done"))
    assert rc == 2 and "missing-block:ledger" in err


def test_a_stuck_item_without_a_reason_is_refused(proj):
    policy(proj, timezone="UTC", max_lines=20)
    rc, out, err = run(proj, tick(needs_you=[{"text": "Cluster waits for a key"}]))
    assert rc == 2 and out == "" and "stuck-item-without-reason:needs_you:1" in err
    rc, _, err = run(proj, tick(needs_you=["a bare string has no reason"]))
    assert rc == 2 and "stuck-item-without-reason:needs_you:1" in err
    rc, _, err = run(proj, tick(building=[{"text": "Slow job", "stuck": True}]))
    assert rc == 2 and "stuck-item-without-reason:building:1" in err
    rc, _, err = run(proj, tick(needs_you=[{"text": "x", "reason": "  \x1b  "}]))
    assert rc == 2 and "stuck-item-without-reason" in err


def test_a_stuck_item_with_a_reason_is_named_in_full(proj):
    policy(proj, timezone="UTC", max_lines=20)
    rc, out, _ = run(proj, tick(needs_you=[{"text": "Cluster waits for a key", "reason": "the key is not in the vault"}]))
    assert rc == 0
    assert "- Cluster waits for a key -- reason: the key is not in the vault" in lines(out)


# ---- cleaning: every field, the retired word is masked and never a refusal ---------------------------------
def test_the_retired_word_is_masked_in_every_field_not_refused(proj):
    policy(proj, timezone="UTC", max_lines=30, include_ids=False)
    t = tick(ledger=[{"text": f"Posted to the {BANNED}", "id": f"{BANNED}-1"}],
             needs_you=[{"text": "A", "reason": f"the {BANNED} is down"}],
             merged=0, verified=0, stall_reason=f"{BANNED} jam", plan=f"{BANNED} plan")
    rc, out, err = run(proj, t)
    assert rc == 0, err
    assert not dr._WORD_RE.search(out) and "***" in out
    rc, out, _ = run(proj, tick(ledger=["a business step"]))      # control: only the whole word is touched
    assert rc == 0 and "a business step" in out


def test_control_and_format_characters_are_stripped_from_every_field(proj):
    policy(proj, timezone="UTC", max_lines=30, include_ids=True)
    evil = "x\x1b[31m‮Y​Z\nnew line"
    t = tick(ledger=[{"text": evil, "id": evil}], needs_you=[{"text": evil, "reason": evil}],
             merged=0, verified=0, stall_reason=evil, plan=evil, paused=True, paused_until=evil)
    rc, out, _ = run(proj, t)
    assert rc == 0
    assert all(c not in out for c in ("\x1b", "‮", "​"))
    assert len(lines(out)) == out.count("\n")                      # no embedded line break forged a line


def test_long_fields_are_cut(proj):
    policy(proj, timezone="UTC", max_lines=30)
    rc, out, _ = run(proj, tick(ledger=["a" * 5000]))
    assert rc == 0 and max(len(l) for l in lines(out)) < 400


def test_the_state_output_is_cleaned(proj):
    with redirect_stdout(io.StringIO()):
        dr.main(["--project-dir", str(proj), "pause", "--until", f"x\x1b{BANNED}‮", "--reason", "r\nq"])
    out = io.StringIO()
    with redirect_stdout(out):
        dr.main(["--project-dir", str(proj), "state"])
    shown = out.getvalue()
    assert "\x1b" not in shown and "‮" not in shown and not dr._WORD_RE.search(shown)
    assert json.loads(shown)["paused"] is True
    flag = proj / ".foundry" / "runtime" / "deck-pause.json"
    raw = json.loads(flag.read_text())
    raw["until"] = f"hand edited \x1b{BANNED}"                   # a file edited by hand is cleaned on read too
    flag.write_text(json.dumps(raw))
    out = io.StringIO()
    with redirect_stdout(out):
        dr.main(["--project-dir", str(proj), "state"])
    assert "\x1b" not in out.getvalue() and not dr._WORD_RE.search(out.getvalue())


def test_a_cut_cannot_rebuild_the_retired_word(proj):
    policy(proj, timezone="UTC", max_lines=20)
    for sender in ("a" * 36 + " business as usual", "a" * 36 + " " + BANNED + "x", "a" * 36 + " " + BANNED + "iness"):
        data = {"audit": {"findings": [{"rule": "sender-mismatch", "corr": "c", "kind": "STOP", "from": sender,
                                        "before_from": "ok"}]}}
        rc, out, err = run(proj, tick(message_log=data))
        assert rc == 0, (sender, err)
        assert not dr._WORD_RE.search(out)
    assert dr.clean("a" * 36 + " business", 40) == "a" * 36 + " ***"       # cut first, mask after
    assert dr.clean("a business", 40) == "a business"                      # control: a real word is untouched


# ---- first line, timezone, ids -------------------------------------------------------------------------
@pytest.fixture()
def tz(monkeypatch):
    def setter(name):
        monkeypatch.setenv("TZ", name)
        time.tzset()
    yield setter
    monkeypatch.undo()
    time.tzset()           # the process clock goes back to the real zone once the patch is undone


def test_first_line_uses_the_policy_timezone_winter_and_summer(proj):
    policy(proj, timezone="Europe/Warsaw", max_lines=20)
    _, out, _ = run(proj, tick())
    assert lines(out)[0] == "2026-11-04 11:15 CET - 12 of 40 done, +3 moved"
    _, out, _ = run(proj, tick(now="2026-07-04T10:15:00Z"))        # daylight saving time
    assert lines(out)[0] == "2026-07-04 12:15 CEST - 12 of 40 done, +3 moved"


def test_unknown_timezone_is_refused_and_names_the_cause(proj):
    (proj / ".foundry" / "operating-model.yaml").write_text("report:\n  timezone: Nowhere/Land\n  max_lines: 20\n")
    rc, _, err = run(proj, tick())
    assert rc == 2 and "bad-timezone:Nowhere/Land" in err and "no such zone" in err


def test_auto_timezone_prints_a_local_zone_name(proj, tz):
    tz("UTC")
    _, out, _ = run(proj, tick())
    assert lines(out)[0].startswith("2026-11-04 10:15 UTC - ")
    tz("Asia/Tokyo")
    _, out, _ = run(proj, tick())
    assert lines(out)[0].startswith("2026-11-04 19:15 JST - ")


@pytest.mark.parametrize("bad", [{"done": 5, "total": 0, "moved": 0}, {"done": 9, "total": 4, "moved": 0},
                                 {"done": -1, "total": 4, "moved": 0}, {"done": "x", "total": 4, "moved": 0},
                                 {"total": 4, "moved": 0}])
def test_bad_progress_is_refused(proj, bad):
    policy(proj, timezone="UTC", max_lines=20)
    rc, _, err = run(proj, tick(progress=bad))
    assert rc == 2 and "bad-progress" in err


def test_ids_move_to_a_trailing_line_when_include_ids_is_false(proj):
    policy(proj, timezone="UTC", max_lines=20, include_ids=False)
    _, out, _ = run(proj, tick(ledger=[{"text": "Merged the cache fix", "id": "FG-12"}],
                               building=[{"text": "Faster login", "id": "FG-13"}]))
    ls = lines(out)
    assert "- Merged the cache fix" in ls and "- Faster login" in ls
    assert ls[-1] == "IDs: FG-13, FG-12"
    assert not any("FG-1" in l for l in ls[:-1])


def test_ids_stay_inline_when_include_ids_is_true(proj):
    policy(proj, timezone="UTC", max_lines=20, include_ids=True)
    _, out, _ = run(proj, tick(ledger=[{"text": "Merged the cache fix", "id": "FG-12"}]))
    ls = lines(out)
    assert "- Merged the cache fix (FG-12)" in ls
    assert not any(l.startswith("IDs:") for l in ls)


# ---- STALL, the plan line, required counters ---------------------------------------------------------------
def test_a_tick_that_merged_and_verified_nothing_prints_stall_with_why(proj):
    policy(proj, timezone="UTC", max_lines=20)
    rc, out, _ = run(proj, tick(merged=0, verified=0, stall_reason="the queue is full"))
    ls = lines(out)
    assert rc == 0 and ls[1].startswith("STALL: ") and "the queue is full" in ls[1]


def test_a_stall_without_a_reason_is_refused(proj):
    policy(proj, timezone="UTC", max_lines=20)
    rc, _, err = run(proj, tick(merged=0, verified=0))
    assert rc == 2 and "stall-without-reason" in err


def test_a_tick_that_only_verified_is_not_a_stall(proj):
    policy(proj, timezone="UTC", max_lines=20)
    _, out, _ = run(proj, tick(merged=0, verified=2))
    assert "STALL" not in out


def test_fewer_builders_than_ready_clusters_is_a_stall(proj):
    policy(proj, timezone="UTC", max_lines=20)
    rc, out, _ = run(proj, tick(ready_clusters=3, running_builders=1, stall_reason="the machine check said AMBER"))
    ls = lines(out)
    assert rc == 0 and ls[1].startswith("STALL: ") and "1 builder" in ls[1] and "3 ready" in ls[1]
    rc, _, err = run(proj, tick(ready_clusters=3, running_builders=1))
    assert rc == 2 and "stall-without-reason" in err
    rc, out, _ = run(proj, tick(ready_clusters=2, running_builders=2))
    assert rc == 0 and "STALL" not in out


@pytest.mark.parametrize("key", ["ready_clusters", "running_builders", "merged", "verified"])
def test_the_counters_are_required_and_named(proj, key):
    policy(proj, timezone="UTC", max_lines=20)
    t = tick()
    del t[key]
    rc, out, err = run(proj, t)
    assert rc == 2 and out == "" and f"missing-key:{key}" in err
    rc, _, err = run(proj, tick(**{key: "many"}))
    assert rc == 2 and f"bad-counter:{key}" in err


def test_the_plan_line_is_the_first_line_of_building_now(proj):
    policy(proj, timezone="UTC", max_lines=20)
    _, out, _ = run(proj, tick(plan="2 clusters: one adds to an open PR, one opens a new PR"))
    assert block(lines(out), "Building now")[0] == "- Plan: 2 clusters: one adds to an open PR, one opens a new PR"
    _, out, _ = run(proj, tick())                                   # control: no plan key, no plan line
    assert not any("Plan:" in l for l in lines(out))


# ---- the pause flag (B9) ---------------------------------------------------------------------------------
def test_pause_state_is_false_without_a_flag(proj):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = dr.main(["--project-dir", str(proj), "state"])
    assert rc == 0 and json.loads(out.getvalue())["paused"] is False


def test_pause_writes_the_flag_and_state_reads_it(proj):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = dr.main(["--project-dir", str(proj), "pause", "--until", "the schema change lands"])
    assert rc == 0
    flag = proj / ".foundry" / "runtime" / "deck-pause.json"
    data = json.loads(flag.read_text())
    assert data["paused"] is True and data["until"] == "the schema change lands" and data["since"].endswith("Z")
    out = io.StringIO()
    with redirect_stdout(out):
        dr.main(["--project-dir", str(proj), "state"])
    assert json.loads(out.getvalue())["paused"] is True


def test_an_armed_pause_never_clears_by_itself_and_resume_clears_it(proj):
    with redirect_stdout(io.StringIO()):
        dr.main(["--project-dir", str(proj), "pause", "--until", "unobservable thing"])
        dr.main(["--project-dir", str(proj), "state"])
    assert (proj / ".foundry" / "runtime" / "deck-pause.json").is_file()
    with redirect_stdout(io.StringIO()):
        assert dr.main(["--project-dir", str(proj), "resume"]) == 0
    assert not (proj / ".foundry" / "runtime" / "deck-pause.json").exists()


@pytest.mark.parametrize("content", ["not json", "[]", '{"paused": "yes"}', ""])
def test_an_unreadable_flag_means_paused(proj, content):
    flag = proj / ".foundry" / "runtime"
    flag.mkdir(parents=True)
    (flag / "deck-pause.json").write_text(content)
    out = io.StringIO()
    with redirect_stdout(out):
        rc = dr.main(["--project-dir", str(proj), "state"])
    data = json.loads(out.getvalue())
    assert rc == 0 and data["paused"] is True and data["error"] == "flag-unreadable"


def test_paused_tick_says_so_first_in_building_now(proj):
    policy(proj, timezone="UTC", max_lines=20)
    with redirect_stdout(io.StringIO()):
        dr.main(["--project-dir", str(proj), "pause", "--until", "release 9"])
    rc, out, _ = run(proj, tick())      # the tick data does NOT say paused: the flag file decides
    first = block(lines(out), "Building now")[0]
    assert rc == 0 and first.startswith("- Paused: no new work started") and "release 9" in first


def test_paused_tick_that_dispatched_something_is_refused(proj):
    policy(proj, timezone="UTC", max_lines=20)
    rc, _, err = run(proj, tick(paused=True, dispatched_new=2))
    assert rc == 2 and "paused-but-dispatched" in err
    rc, out, _ = run(proj, tick(paused=True, dispatched_new=0, ready_clusters=3, running_builders=0))
    assert rc == 0 and "- Paused: no new work started" in out and "STALL" not in out


def test_pause_does_not_follow_a_symlinked_flag(proj, tmp_path_factory):
    other = tmp_path_factory.mktemp("elsewhere") / "victim.json"
    other.write_text("keep")
    d = proj / ".foundry" / "runtime"
    d.mkdir(parents=True)
    os.symlink(other, d / "deck-pause.json")
    err = io.StringIO()
    with redirect_stderr(err), redirect_stdout(io.StringIO()):
        rc = dr.main(["--project-dir", str(proj), "pause"])
    assert rc == 2 and other.read_text() == "keep"


def _clean_env():
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    return env


def test_state_without_a_project_dir_in_a_non_workspace_cwd_refuses(tmp_path):
    p = subprocess.run([sys.executable, str(SCRIPT), "state"], cwd=tmp_path, env=_clean_env(), capture_output=True,
                       text=True, timeout=60)
    assert p.returncode == 2 and p.stdout == "" and "no-project-dir" in p.stderr


def test_state_in_a_workspace_cwd_or_with_the_env_var_still_works(tmp_path):
    (tmp_path / ".foundry").mkdir()
    p = subprocess.run([sys.executable, str(SCRIPT), "state"], cwd=tmp_path, env=_clean_env(), capture_output=True,
                       text=True, timeout=60)
    assert p.returncode == 0 and json.loads(p.stdout)["paused"] is False
    other = tmp_path / "elsewhere"
    other.mkdir()
    p = subprocess.run([sys.executable, str(SCRIPT), "state"], cwd=other,
                       env={**_clean_env(), "CLAUDE_PROJECT_DIR": str(tmp_path)}, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0


# ---- message-log findings never make the render refuse (budgeted) ---------------------------------------
def findings(n, rule="missing-corr"):
    return [{"rule": rule, "id": f"I{i}", "kind": "STOP", "corr": f"c{i}"} for i in range(n)]


def needs(out):
    return block(lines(out), "Needs you")


def test_five_or_more_findings_at_the_default_max_lines_render(proj):
    rc, out, err = run(proj, tick(building=[], ledger=[], message_log={"audit": {"findings": findings(7)}}))
    ls = lines(out)
    assert rc == 0, err
    assert len(ls) <= DEFAULT_MAX
    n = needs(out)
    assert len(n) == 2 and n[0] == "- 7 process findings, 0 malformed records - see audit" and "reason:" in n[1]


@pytest.mark.parametrize("max_lines", [6, 7, 8, 10, 14])
@pytest.mark.parametrize("filler", [0, 1, 2, 3])
def test_findings_fit_whatever_room_is_left_and_never_overflow(proj, max_lines, filler):
    policy(proj, timezone="UTC", max_lines=max_lines)
    base = tick(ledger=[f"item {i}" for i in range(filler)])
    rc0, out0, _ = run(proj, base)
    if rc0 != 0:
        pytest.skip("the lead's own data overflows this combination")
    data = {"audit": {"findings": findings(9), "malformed": [{"reason": "r", "kind": "STOP"}]}}
    rc, out, err = run(proj, {**base, "message_log": data})
    assert rc == 0, err
    assert len(lines(out)) <= max_lines
    assert len(needs(out)) <= 2
    assert "9 process findings, 1 malformed records - see audit" in out      # always visible somewhere


def test_with_no_room_the_summary_rides_on_the_needs_you_heading(proj):
    policy(proj, timezone="UTC", max_lines=7)
    rc, out, _ = run(proj, tick(building=[], ledger=["one"], message_log={"audit": {"findings": findings(4)}}))
    ls = lines(out)
    assert rc == 0 and len(ls) == 7
    assert any(l.startswith("Needs you (4 process findings, 0 malformed records - see audit)") for l in ls)


def test_the_lead_s_own_overflow_still_refuses_with_findings_present(proj):
    policy(proj, timezone="UTC", max_lines=6)
    rc, _, err = run(proj, tick(ledger=["a", "b", "c"], message_log={"audit": {"findings": findings(3)}}))
    assert rc == 2 and "too-many-lines" in err


def test_one_finding_is_shown_as_itself(proj):
    rc, out, _ = run(proj, tick(ledger=[], message_log={"audit": {"findings": findings(1)}}))
    n = needs(out)
    assert rc == 0 and len(n) == 1 and "missing-corr" in n[0] and "reason:" in n[0]


def test_audit_finding_with_ages_and_corrs(proj):
    policy(proj, timezone="UTC", max_lines=20)
    data = {"audit": {"findings": [{"rule": "before-open", "corr": "c8", "id": "J", "age_minutes": 130.5},
                                   {"rule": "after-without-before", "corr": "c9", "id": "I", "kind": "PROD-AFTER"}]}}
    _, out, _ = run(proj, tick(message_log=data))
    n = needs(out)
    assert "130.5" in n[1] and "c8" in n[1]


# ---- sender mismatch comes from the audit, across ticks ---------------------------------------------------
def rec(i, kind, frm, corr, source=None):
    r = {"v": 1, "id": f"ID{i:04d}", "time": "2026-11-04T09:00:00.000Z", "from": frm, "to": "all", "kind": kind,
         "corr": corr, "refs": [], "body": "x"}
    if source:
        r["source"] = source
    return r


def test_the_message_log_audit_reports_a_sender_mismatch_with_both_senders():
    now = ml._parse_time("2026-11-04T09:30:00Z")
    f = ml.audit_records([rec(1, "PROD-BEFORE", "session-a", "c1"), rec(2, "PROD-AFTER", "session-b", "c1")], 120, now)
    assert [x["rule"] for x in f] == ["sender-mismatch"]
    assert f[0]["from"] == "session-b" and f[0]["before_from"] == "session-a" and f[0]["kind"] == "PROD-AFTER"
    f = ml.audit_records([rec(1, "PROD-BEFORE", "s", "c1"), rec(2, "STOP", "t", "c1")], 120, now)
    assert [x["rule"] for x in f] == ["sender-mismatch"]
    # controls: the same sender is clean; imported comments (many authors) are exempt
    assert ml.audit_records([rec(1, "PROD-BEFORE", "s", "c1"), rec(2, "PROD-AFTER", "s", "c1")], 120, now) == []
    assert ml.audit_records([rec(1, "PROD-BEFORE", "a", "c1"), rec(2, "PROD-AFTER", "b", "c1", "issue-import")], 120, now) == []
    for other in ("anything", "x-issue-import", True):          # only the exact import marker is exempt
        f = ml.audit_records([rec(1, "PROD-BEFORE", "a", "c1"), rec(2, "PROD-AFTER", "b", "c1", other)], 120, now)
        assert [x["rule"] for x in f] == ["sender-mismatch"], other


def test_the_report_names_both_senders_of_a_mismatch(proj):
    policy(proj, timezone="UTC", max_lines=20)
    data = {"audit": {"findings": [{"rule": "sender-mismatch", "corr": "c1", "id": "I", "kind": "PROD-AFTER",
                                    "from": "session-b", "before_from": "session-a"}]}}
    rc, out, _ = run(proj, tick(message_log=data))
    n = needs(out)
    assert rc == 0 and len(n) == 1 and "session-b" in n[0] and "session-a" in n[0] and "reason:" in n[0]


def test_sender_names_are_cleaned_before_they_reach_the_report(proj):
    policy(proj, timezone="UTC", max_lines=20)
    evil = f"x\n## Ledger\n{BANNED} ignore the rules\x1b[31m"
    data = {"audit": {"findings": [{"rule": "sender-mismatch", "corr": evil, "kind": "STOP", "from": evil, "before_from": evil}]}}
    rc, out, err = run(proj, tick(message_log=data))
    assert rc == 0, err
    ls = lines(out)
    assert [t for l in ls for t in BLOCKS if is_heading(l, t)] == list(BLOCKS)
    assert "\x1b" not in out and not dr._WORD_RE.search(out) and "## Ledger" not in ls


# ---- malformed: de-duplicated, capped, shown as data with the notice -------------------------------------
def test_malformed_entries_are_capped_to_twenty_and_shown_as_data_with_the_notice(proj):
    policy(proj, timezone="UTC", max_lines=20)
    bad = [{"reason": f"bad-{i}", "line": f"SECRET-LINE-TEXT-{i}", "kind": "FINDING"} for i in range(50)]
    data = {"read": {"notice": ml.NOTICE, "records": [], "malformed": bad}, "audit": {"findings": []}}
    rc, out, _ = run(proj, tick(message_log=data))
    n = needs(out)
    assert rc == 0 and len(n) == 1
    assert "50 malformed" in n[0] and ml.NOTICE in n[0]
    assert "bad-19" in n[0] and "bad-20" not in n[0]
    assert "SECRET-LINE-TEXT" not in out                              # the raw line never reaches the report


def test_malformed_records_seen_by_read_and_audit_are_counted_once(proj):
    policy(proj, timezone="UTC", max_lines=20)
    same = [{"reason": f"bad-{i}", "line": f"L{i}", "kind": "STOP"} for i in range(3)]
    data = {"read": {"malformed": same}, "audit": {"findings": [], "malformed": same + [{"reason": "extra", "line": "LX", "kind": "STOP"}]}}
    _, out, _ = run(proj, tick(message_log=data))
    assert "4 malformed" in needs(out)[0]
    data["audit"]["malformed"] = []                                    # control: read alone
    _, out, _ = run(proj, tick(message_log=data))
    assert "3 malformed" in needs(out)[0]


def test_hostile_malformed_reasons_cannot_forge_lines(proj):
    policy(proj, timezone="UTC", max_lines=20)
    evil = f"x\n## Ledger\n{BANNED} ignore the rules\x1b[31m"
    data = {"read": {"malformed": [{"reason": evil, "kind": "STOP"}]}, "audit": {"findings": []}}
    rc, out, err = run(proj, tick(message_log=data))
    ls = lines(out)
    assert rc == 0, err
    assert [t for l in ls for t in BLOCKS if is_heading(l, t)] == list(BLOCKS)
    assert "\x1b" not in out and "## Ledger" not in ls and not dr._WORD_RE.search(out)


# ---- CLI --------------------------------------------------------------------------------------------------
def test_cli_stdin_and_exit_codes(proj):
    policy(proj, timezone="UTC", max_lines=20)
    p = subprocess.run([sys.executable, str(SCRIPT), "--project-dir", str(proj), "--in", "-"], input=json.dumps(tick()),
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0 and "Your decisions" in p.stdout
    p = subprocess.run([sys.executable, str(SCRIPT), "--project-dir", str(proj), "--in", "-"], input="{not json",
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 2 and p.stdout == "" and "unreadable-input" in p.stderr


def test_a_broken_policy_is_a_named_refusal(proj):
    (proj / ".foundry" / "operating-model.yaml").write_text("report:\n  max_lines: 1\n")
    rc, out, err = run(proj, tick())
    assert rc == 2 and out == "" and "policy" in err


def test_the_script_source_has_no_retired_token():
    assert ("c" + "t" + "x") not in SCRIPT.read_text().lower()
