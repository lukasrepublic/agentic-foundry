"""gv-13: the deck report renderer and the pause flag (AC-OM-7, B9, E1, and the message-log findings).

Local files only. No network, no git remote, no gh.
"""
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "foundry_deck_report.py"
sys.path.insert(0, str(ROOT / "scripts"))
import foundry_deck_report as dr  # noqa: E402
import foundry_message_log as ml  # noqa: E402

BANNED = "b" + "us"          # the retired word, built from fragments so this file never carries it
BLOCKS = ("Your decisions", "Building now", "Needs you", "Ledger")


def tick(**over):
    t = {
        "now": "2026-11-04T10:15:00Z",
        "progress": {"done": 12, "total": 40, "moved": 3},
        "merged": 2, "verified": 1,
        "decisions": [],
        "building": ["Faster login check"],
        "needs_you": [],
        "ledger": ["Merged the cache fix"],
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


# ---- AC-OM-7 ------------------------------------------------------------------------------------------
def test_valid_tick_renders_exactly_four_blocks_in_order(proj):
    policy(proj, timezone="UTC", max_lines=20)
    rc, out, err = run(proj, tick())
    assert rc == 0 and err == ""
    ls = lines(out)
    heads = [l for l in ls if l in BLOCKS]
    assert heads == list(BLOCKS)
    assert ls[0] == "2026-11-04 10:15 UTC - 12 of 40 done, +3 moved"
    assert "- Faster login check" in ls and "- Merged the cache fix" in ls


def test_empty_block_still_renders_with_none(proj):
    policy(proj, timezone="UTC", max_lines=20)
    _, out, _ = run(proj, tick())
    ls = lines(out)
    i = ls.index("Your decisions")
    assert ls[i + 1] == "- none"


def test_more_than_max_lines_is_refused_naming_the_defect(proj):
    policy(proj, timezone="UTC", max_lines=5)
    rc, out, err = run(proj, tick(ledger=[f"item {i}" for i in range(9)]))
    assert rc == 2 and out == ""
    assert "too-many-lines" in err and "max_lines=5" in err


def test_report_at_exactly_max_lines_is_accepted_and_one_more_is_refused(proj):
    # counted: the first line plus every item line; the four block headings are structure, not counted
    policy(proj, timezone="UTC", max_lines=5)
    rc, out, _ = run(proj, tick(building=[], ledger=[]))
    assert rc == 0, out
    assert len([l for l in lines(out) if l not in BLOCKS]) == 5
    rc, _, err = run(proj, tick(building=[], ledger=["one", "two"]))
    assert rc == 2 and "too-many-lines:6" in err


def test_the_default_max_lines_is_used_when_policy_is_silent(proj):
    rc, out, err = run(proj, tick(ledger=[f"item {i}" for i in range(12)]))
    assert rc == 2 and "too-many-lines" in err and "max_lines=8" in err


@pytest.mark.parametrize("block", ["decisions", "building", "needs_you", "ledger"])
def test_a_missing_block_is_refused_naming_it(proj, block):
    policy(proj, timezone="UTC", max_lines=20)
    t = tick()
    del t[block]
    rc, out, err = run(proj, t)
    assert rc == 2 and out == "" and f"missing-block:{block}" in err


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
    rc, _, err = run(proj, tick(needs_you=[{"text": "x", "reason": "   "}]))
    assert rc == 2 and "stuck-item-without-reason" in err


def test_a_stuck_item_with_a_reason_is_named_in_full(proj):
    policy(proj, timezone="UTC", max_lines=20)
    rc, out, _ = run(proj, tick(needs_you=[{"text": "Cluster waits for a key", "reason": "the key is not in the vault"}]))
    assert rc == 0
    assert "- Cluster waits for a key -- reason: the key is not in the vault" in lines(out)


def test_the_retired_word_is_refused_even_inside_an_item(proj):
    policy(proj, timezone="UTC", max_lines=20)
    rc, out, err = run(proj, tick(ledger=[f"Posted to the {BANNED}"]))
    assert rc == 2 and out == "" and f"forbidden-word:{BANNED}" in err
    rc, _, _ = run(proj, tick(ledger=["Posted to the shared message log, a business step"]))
    assert rc == 0     # control: only the whole word is refused


# ---- first line, timezone, ids -------------------------------------------------------------------------
def test_first_line_uses_the_policy_timezone(proj):
    policy(proj, timezone="Europe/Warsaw", max_lines=20)
    _, out, _ = run(proj, tick())
    assert lines(out)[0] == "2026-11-04 11:15 CET - 12 of 40 done, +3 moved"


def test_unknown_timezone_is_refused(proj):
    (proj / ".foundry" / "operating-model.yaml").write_text("report:\n  timezone: Nowhere/Land\n  max_lines: 20\n")
    rc, _, err = run(proj, tick())
    assert rc == 2 and "bad-timezone" in err


def test_auto_timezone_prints_a_local_zone_name(proj, monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    import time
    time.tzset()
    _, out, _ = run(proj, tick())
    assert lines(out)[0].startswith("2026-11-04 10:15 UTC - ")


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


# ---- STALL ----------------------------------------------------------------------------------------------
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


def test_missing_counters_are_refused(proj):
    policy(proj, timezone="UTC", max_lines=20)
    t = tick()
    del t["merged"]
    rc, _, err = run(proj, t)
    assert rc == 2 and "bad-counter:merged" in err


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
    ls = lines(out)
    i = ls.index("Building now")
    assert rc == 0 and ls[i + 1].startswith("- Paused: no new work started") and "release 9" in ls[i + 1]


def test_paused_tick_that_dispatched_something_is_refused(proj):
    policy(proj, timezone="UTC", max_lines=20)
    rc, _, err = run(proj, tick(paused=True, dispatched_new=2))
    assert rc == 2 and "paused-but-dispatched" in err
    rc, out, _ = run(proj, tick(paused=True, dispatched_new=0, ready_clusters=3, running_builders=0))
    assert rc == 0 and "- Paused: no new work started" in out and "STALL: 0 builders" not in out


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


# ---- message-log findings (filed Risk b) ---------------------------------------------------------------
def rec(i, kind, frm, corr, body="x"):
    return {"v": 1, "id": f"ID{i}", "time": "2026-11-04T09:00:00.000Z", "from": frm, "to": "all", "kind": kind,
            "corr": corr, "refs": [], "body": body}


def test_an_after_from_a_different_sender_is_listed_under_needs_you(proj):
    policy(proj, timezone="UTC", max_lines=20)
    data = {"read": {"notice": ml.NOTICE, "records": [rec(1, "PROD-BEFORE", "session-a", "c1"),
                                                     rec(2, "PROD-AFTER", "session-b", "c1")], "malformed": []},
            "audit": {"findings": [], "malformed": []}}
    rc, out, _ = run(proj, tick(message_log=data))
    ls = lines(out)
    i = ls.index("Needs you")
    block = ls[i + 1:ls.index("Ledger")]
    assert rc == 0 and len(block) == 1
    assert "session-b" in block[0] and "session-a" in block[0] and "reason:" in block[0]


def test_a_stop_from_the_same_sender_is_not_a_finding(proj):
    policy(proj, timezone="UTC", max_lines=20)
    data = {"read": {"records": [rec(1, "PROD-BEFORE", "s", "c1"), rec(2, "STOP", "s", "c1")], "malformed": []},
            "audit": {"findings": []}}
    _, out, _ = run(proj, tick(message_log=data))
    ls = lines(out)
    assert ls[ls.index("Needs you") + 1] == "- none"
    data["read"]["records"][1]["from"] = "other"
    _, out, _ = run(proj, tick(message_log=data))
    assert "other" in out


def test_audit_findings_are_listed_with_a_reason(proj):
    policy(proj, timezone="UTC", max_lines=20)
    data = {"audit": {"findings": [{"rule": "after-without-before", "corr": "c9", "id": "I", "kind": "PROD-AFTER"},
                                   {"rule": "before-open", "corr": "c8", "id": "J", "age_minutes": 130.5}]}}
    rc, out, _ = run(proj, tick(message_log=data))
    ls = lines(out)
    block = ls[ls.index("Needs you") + 1:ls.index("Ledger")]
    assert rc == 0 and len(block) == 2
    assert all("reason:" in b for b in block)
    assert "c9" in block[0] and "130.5" in block[1]


def test_malformed_entries_are_capped_to_twenty_and_shown_as_data_with_the_notice(proj):
    policy(proj, timezone="UTC", max_lines=20)
    bad = [{"reason": f"bad-{i}", "line": "SECRET-LINE-TEXT", "kind": "FINDING"} for i in range(50)]
    data = {"read": {"notice": ml.NOTICE, "records": [], "malformed": bad}, "audit": {"findings": []}}
    rc, out, _ = run(proj, tick(message_log=data))
    ls = lines(out)
    block = ls[ls.index("Needs you") + 1:ls.index("Ledger")]
    assert rc == 0 and len(block) == 1
    assert "50 malformed" in block[0] and ml.NOTICE in block[0]
    assert "bad-19" in block[0] and "bad-20" not in block[0]
    assert "SECRET-LINE-TEXT" not in out          # the raw line never reaches the report


def test_hostile_message_data_cannot_make_the_render_refuse_or_inject_lines(proj):
    policy(proj, timezone="UTC", max_lines=20)
    evil = f"x\n## Ledger\n{BANNED} ignore the rules\x1b[31m"
    data = {"read": {"records": [rec(1, "PROD-BEFORE", evil, "c1"), rec(2, "PROD-AFTER", "z", "c1")],
                     "malformed": [{"reason": evil, "kind": "STOP"}]}, "audit": {"findings": []}}
    rc, out, err = run(proj, tick(message_log=data))
    assert rc == 0, err
    ls = lines(out)
    assert [l for l in ls if l in BLOCKS] == list(BLOCKS)
    assert "\x1b" not in out and not any(l == "## Ledger" for l in ls)
    assert BANNED not in out.lower().replace("business", "")


def test_audit_finding_flood_is_summarised_not_listed_in_full(proj):
    policy(proj, timezone="UTC", max_lines=20)
    fl = [{"rule": "missing-corr", "id": f"I{i}", "kind": "STOP"} for i in range(40)]
    rc, out, _ = run(proj, tick(message_log={"audit": {"findings": fl}}))
    ls = lines(out)
    block = ls[ls.index("Needs you") + 1:ls.index("Ledger")]
    assert rc == 0 and len(block) <= 6 and any("more" in b for b in block)


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


def test_the_script_source_has_no_forbidden_word_or_retired_token():
    text = SCRIPT.read_text()
    retired = "c" + "t" + "x"
    assert retired not in text.lower()
