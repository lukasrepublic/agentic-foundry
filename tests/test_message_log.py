"""gv-09: the shared message log -- AC-BUS-1..5, 7, 8 (AC-BUS-6 is the deck's, gv-13).

Real local bare-remote git fixtures for every git path (post / read / audit / concurrency / the rejection
observation); `gh` is ALWAYS a stub (`import`). Nothing here reaches the network or spawns a real `gh`.
"""
import datetime
import json
import os
import re
import subprocess
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import foundry_message_kind as mk  # noqa: E402
import foundry_message_log as ml  # noqa: E402
from test_operating_model_policy import PRIVATE, PRODUCT  # noqa: E402  (the one name census, reused)

LOG_CLI = os.path.join(SCRIPTS, "foundry_message_log.py")
KIND_CLI = os.path.join(SCRIPTS, "foundry_message_kind.py")
NOSLEEP = lambda s: None  # noqa: E731


@pytest.fixture(autouse=True)
def _isolated_git(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL",
              "CLAUDE_PROJECT_DIR", "FOUNDRY_MESSAGE_REMOTE"):
        monkeypatch.delenv(k, raising=False)


def git(cwd, *args, check=True):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if check:
        assert r.returncode == 0, f"git {args}: {r.stderr}"
    return r


def make_remote(tmp_path, name="remote.git", config=None):
    path = tmp_path / name
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(path)], check=True)
    for k, v in (config or {}).items():
        git(path, "config", k, v)
    return path


def make_clone(tmp_path, remote, name):
    dest = tmp_path / name
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True, capture_output=True)
    return dest


def tip_records(clone):
    tip = ml.fetch_tip(ml.Adapter(), clone, "origin")
    return tip, (ml.replay(ml.Adapter(), clone, tip)[0] if tip else [])


def remote_lines(remote):
    r = git(remote, "ls-tree", "-r", "--name-only", ml.BRANCH, check=False)
    out = []
    for p in r.stdout.split():
        if p.endswith(".jsonl"):
            out += git(remote, "show", f"{ml.BRANCH}:{p}").stdout.splitlines()
    return out


def body(tmp_path, text, name="body.txt"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


# ============================================================================================ AC-BUS-1
def test_ac_bus_1_kind_outside_the_closed_set_is_refused_and_nothing_is_written(tmp_path):
    remote = make_remote(tmp_path)
    clone = make_clone(tmp_path, remote, "w")
    rc = ml.main(["--repo", str(clone), "post", "--to", "all", "--kind", "GOSSIP", "--body-file",
                  body(tmp_path, "hello")], sleep=NOSLEEP)
    assert rc == 3
    assert git(remote, "rev-parse", "--verify", "-q", ml.BRANCH, check=False).returncode != 0, "branch created"
    assert git(clone, "for-each-ref", "refs/foundry").stdout == ""


def test_ac_bus_1_control_valid_kind_appends_exactly_one_valid_json_line(tmp_path, capsys):
    remote = make_remote(tmp_path)
    clone = make_clone(tmp_path, remote, "w")
    rc = ml.main(["--repo", str(clone), "post", "--from", "sess-a", "--to", "sess-b", "--kind", "NEEDS-INTERFACE",
                  "--refs", "o/r#1,o/r#2", "--body-file", body(tmp_path, "need a stable API")], sleep=NOSLEEP)
    assert rc == 0
    lines = remote_lines(remote)
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert list(rec) == ["v", "id", "time", "from", "to", "kind", "refs", "body"]
    assert rec["v"] == 1 and rec["from"] == "sess-a" and rec["to"] == "sess-b" and rec["kind"] == "NEEDS-INTERFACE"
    assert rec["refs"] == ["o/r#1", "o/r#2"] and re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", rec["id"])
    assert rec["time"].endswith("Z") and mk.lint(rec) == []
    assert json.loads(capsys.readouterr().out)["id"] == rec["id"]
    # the orphan branch carries the union attribute and the per-month file
    assert git(remote, "show", f"{ml.BRANCH}:.gitattributes").stdout == "*.jsonl merge=union\n"
    assert git(remote, "rev-list", "--max-parents=0", ml.BRANCH).stdout.strip() == git(remote, "rev-parse", ml.BRANCH).stdout.strip()
    assert re.fullmatch(r"messages/\d{4}-\d{2}\.jsonl", git(remote, "ls-tree", "-r", "--name-only", ml.BRANCH).stdout.split()[1])
    # the writer's own work tree and index were never touched
    assert git(clone, "status", "--porcelain").stdout == ""


def test_ac_bus_1_body_rules_refuse_and_the_work_tree_branch_is_untouched(tmp_path):
    remote = make_remote(tmp_path)
    clone = make_clone(tmp_path, remote, "w")
    cases = [("FINDING", None, "no evidence, just a claim"), ("PROD-AFTER", None, "done"),
             ("PROD-BEFORE", "c1", "   "), ("TICK", None, "not a tick"), ("HANDOFF", None, "no block")]
    for kind, corr, text in cases:
        args = ["--repo", str(clone), "post", "--to", "all", "--kind", kind, "--body-file", body(tmp_path, text)]
        if corr:
            args += ["--corr", corr]
        assert ml.main(args, sleep=NOSLEEP) == 3, kind
    assert git(remote, "rev-parse", "-q", "--verify", ml.BRANCH, check=False).returncode != 0, "a refusal wrote"
    ok = [("FINDING", None, "see docs/x.md:3"), ("TICK", None, "prog-1 2026-01-01T00:00:00Z"),
          ("PROD-BEFORE", "c1", "touching it"), ("STOP", "c1", "reason: lane stopped"),
          ("HANDOFF", None, 'x\n```json\n{"cwd":"/tmp","command":"ls -l","why":"w","expect":"e"}\n```\n')]
    for kind, corr, text in ok:
        args = ["--repo", str(clone), "post", "--to", "all", "--kind", kind, "--body-file", body(tmp_path, text)]
        if corr:
            args += ["--corr", corr]
        assert ml.main(args, sleep=NOSLEEP) == 0, kind
    assert len(remote_lines(remote)) == len(ok)


def test_message_kind_cli_exit_codes(tmp_path):
    good = ml.make_record("a", "all", "STOP", "reason", corr="c")
    f = tmp_path / "r.json"
    f.write_text(json.dumps(good))
    r = subprocess.run([sys.executable, KIND_CLI, "--in", str(f)], capture_output=True, text=True)
    assert r.returncode == 0 and json.loads(r.stdout)["valid"] is True
    good["kind"] = "BUS"
    f.write_text(json.dumps(good))
    r = subprocess.run([sys.executable, KIND_CLI, "--in", str(f)], capture_output=True, text=True)
    assert r.returncode == 3 and "closed set" in json.loads(r.stdout)["errors"][0]
    r = subprocess.run([sys.executable, KIND_CLI, "--in", str(tmp_path / "missing")], capture_output=True, text=True)
    assert r.returncode == 2 and r.stdout == ""
    assert set(mk.KINDS) == {"FINDING", "NEEDS-INTERFACE", "CHALLENGE", "HANDOFF", "TICK", "PROD-BEFORE",
                             "PROD-AFTER", "STOP"}


# ============================================================================================ AC-BUS-2
def _post(clone, to, kind="NEEDS-INTERFACE", text="x", corr=None, sender="s"):
    rec, errs = ml.post(ml.Adapter(), clone, "origin", sender, to, kind, text, corr, [], NOSLEEP)
    assert errs == []
    return rec


def _plant_raw(clone, raw_line):
    ad = ml.Adapter()
    tip = ml.fetch_tip(ad, clone, "origin")
    path = [p for p in ml._log_files(ad, clone, tip)][-1]
    new = ml._blob_text(ad, clone, tip, path) + raw_line + "\n"
    env = {"GIT_INDEX_FILE": str(clone / ".git" / "plant-index"), **ml._identity_env(ad, clone)}
    ad.git(["read-tree", tip], clone, env=env)
    sha = ad.git(["hash-object", "-w", "--stdin"], clone, env=env, input_text=new).stdout.strip()
    ad.git(["update-index", "--cacheinfo", f"100644,{sha},{path}"], clone, env=env)
    tree = ad.git(["write-tree"], clone, env=env).stdout.strip()
    c = ad.git(["commit-tree", tree, "-p", tip, "-m", "plant"], clone, env=env).stdout.strip()
    assert ad.git(["push", "origin", f"{c}:refs/heads/{ml.BRANCH}"], clone).returncode == 0


def test_ac_bus_2_read_returns_only_my_and_all_records_after_the_cursor_deduped(tmp_path):
    remote = make_remote(tmp_path)
    w = make_clone(tmp_path, remote, "w")
    r = make_clone(tmp_path, remote, "r")
    old = _post(w, "x", text="before the cursor")
    cursor, _ = tip_records(w)
    mine = _post(w, "x", text="for x")
    _post(w, "someone-else", text="not mine")
    everyone = _post(w, "all", kind="STOP", text="stopped", corr="c1")
    dup = ml.make_record("s", "x", "NEEDS-INTERFACE", "dup", None, [])
    for _ in range(2):  # the same id twice (a union-merge artefact)
        _plant_raw(w, ml._line(dup))
    _plant_raw(w, "this is not json")
    res = ml.read(ml.Adapter(), r, "origin", "x", since=cursor)
    ids = [x["id"] for x in res["records"]]
    assert ids == [mine["id"], everyone["id"], dup["id"]]
    assert old["id"] not in ids
    assert [m["reason"].split(":")[0] for m in res["malformed"]] == ["not-json"]
    # kind filter
    assert [x["id"] for x in ml.read(ml.Adapter(), r, "origin", "x", since=cursor, kinds={"STOP"})["records"]] == [everyone["id"]]
    # control: reading as another recipient sees its own + all, never x's
    other = ml.read(ml.Adapter(), r, "origin", "someone-else", since=cursor)["records"]
    assert {x["to"] for x in other} == {"someone-else", "all"}


def test_ac_bus_2_cursor_lives_in_the_git_dir_and_advances(tmp_path):
    remote = make_remote(tmp_path)
    w = make_clone(tmp_path, remote, "w")
    r = make_clone(tmp_path, remote, "r")
    a = _post(w, "x", text="one")
    first = ml.read(ml.Adapter(), r, "origin", "x")
    assert [x["id"] for x in first["records"]] == [a["id"]]
    cur = git(r, "rev-parse", "--git-path", ml.CURSOR_BASENAME).stdout.strip()
    cur = cur if os.path.isabs(cur) else os.path.join(str(r), cur)
    assert open(cur).read().strip() == first["tip"]
    assert git(r, "status", "--porcelain", "--ignored").stdout == "", "cursor leaked into the work tree"
    assert ml.read(ml.Adapter(), r, "origin", "x")["records"] == []
    b = _post(w, "x", text="two")
    assert [x["id"] for x in ml.read(ml.Adapter(), r, "origin", "x")["records"]] == [b["id"]]
    # an explicit --since never moves the stored cursor; --no-advance neither
    before = open(cur).read()
    _post(w, "x", text="three")
    ml.read(ml.Adapter(), r, "origin", "x", since=first["tip"])
    ml.read(ml.Adapter(), r, "origin", "x", advance=False)
    assert open(cur).read() == before
    # a cursor that is not in the tip's history is a named hard error, not an empty answer
    with pytest.raises(ml.LogError, match="not an ancestor"):
        ml.read(ml.Adapter(), r, "origin", "x", since="0" * 40)


# ============================================================================================ AC-BUS-3
def test_ac_bus_3_three_concurrent_writers_all_land_with_no_force(tmp_path):
    remote = make_remote(tmp_path, config={"receive.denyNonFastForwards": "true"})  # a force would be refused
    clones = [make_clone(tmp_path, remote, f"w{i}") for i in range(3)]
    barrier, out, errors = threading.Barrier(3), {}, []

    def writer(i):
        try:
            barrier.wait()
            rec, errs = ml.post(ml.Adapter(), clones[i], "origin", f"w{i}", "all", "NEEDS-INTERFACE", f"msg {i}",
                                None, [], lambda s: time.sleep(0.02))
            out[i] = rec
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    ts = [threading.Thread(target=writer, args=(i,)) for i in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert errors == []
    ids = {json.loads(l)["id"] for l in remote_lines(remote)}
    assert ids == {out[i]["id"] for i in range(3)}, "a record was lost"
    assert len(remote_lines(remote)) == 3
    # linear history: every commit has at most one parent, one orphan root, 3 appends
    assert git(remote, "rev-list", "--min-parents=2", ml.BRANCH).stdout == ""
    assert len(git(remote, "rev-list", ml.BRANCH).stdout.split()) == 3
    # each writer verified by read-back from ITS OWN clone (post would have raised otherwise)
    for i in range(3):
        assert out[i]["id"] in {r["id"] for r in tip_records(clones[i])[1]}


class RaceOnce(ml.Adapter):
    """A real competing writer lands between this writer's fetch and push -- `times` times."""

    def __init__(self, rival_clone, times):
        self.rival, self.times, self.n = rival_clone, times, 0

    def git(self, args, cwd, env=None, input_text=None):
        if args[0] == "push" and self.n < self.times:
            self.n += 1
            ml.post(ml.Adapter(), self.rival, "origin", "rival", "all", "NEEDS-INTERFACE", f"rival {self.n}", None, [], NOSLEEP)
        return super().git(args, cwd, env, input_text)


def test_ac_bus_3_a_lost_race_is_retried_by_refetch_and_reappend_never_forced(tmp_path):
    remote = make_remote(tmp_path, config={"receive.denyNonFastForwards": "true"})
    w = make_clone(tmp_path, remote, "w")
    rival = make_clone(tmp_path, remote, "rival")
    sleeps = []
    ad = RaceOnce(rival, 2)
    rec, errs = ml.post(ad, w, "origin", "w", "all", "NEEDS-INTERFACE", "mine", None, [], sleeps.append, rng=lambda: 0.5)
    assert errs == [] and ad.n == 2
    assert sleeps == [2.0, 4.0], "2 s base, doubling (jitter pinned at x1.0)"
    texts = sorted(json.loads(l)["body"] for l in remote_lines(remote))
    assert texts == ["mine", "rival 1", "rival 2"]


def test_ac_bus_3_control_exhaustion_is_a_hard_failure_with_the_reason(tmp_path):
    remote = make_remote(tmp_path)
    w = make_clone(tmp_path, remote, "w")
    rival = make_clone(tmp_path, remote, "rival")
    sleeps = []
    with pytest.raises(ml.LogError, match=r"(?s)retries exhausted.*(rejected|fetch first|non-fast-forward)"):
        ml.post(RaceOnce(rival, 99), w, "origin", "w", "all", "NEEDS-INTERFACE", "mine", None, [], sleeps.append)
    assert len(sleeps) == ml.MAX_RETRIES == 5
    assert sum(sleeps) <= ml.BACKOFF_TOTAL_CAP_S
    assert "mine" not in " ".join(remote_lines(remote)), "an exhausted post must not have landed"
    # and via the CLI: exit 1, the reason on stderr
    rc = ml.main(["--repo", str(w), "post", "--to", "all", "--kind", "STOP", "--corr", "c", "--body-file",
                  body(tmp_path, "x")], adapter=RaceOnce(rival, 99), sleep=NOSLEEP)
    assert rc == 1


def test_backoff_envelope_two_second_base_doubling_fifty_percent_jitter():
    lo, hi = ml.backoff_delays(lambda: 0.0), ml.backoff_delays(lambda: 1.0)
    assert lo == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert hi[:4] == [3.0, 6.0, 12.0, 24.0] and sum(hi) <= ml.BACKOFF_TOTAL_CAP_S


def test_ac_bus_3_a_refusal_that_is_not_a_race_is_not_retried_and_not_swallowed(tmp_path):
    remote = make_remote(tmp_path)
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'policy says no' >&2\nexit 1\n")
    hook.chmod(0o755)
    w = make_clone(tmp_path, remote, "w")
    sleeps = []
    with pytest.raises(ml.LogError, match="not a race"):
        ml.post(ml.Adapter(), w, "origin", "w", "all", "NEEDS-INTERFACE", "x", None, [], sleeps.append)
    assert sleeps == []


def test_ac_bus_3_read_back_failure_is_reported(tmp_path):
    remote = make_remote(tmp_path)
    w = make_clone(tmp_path, remote, "w")

    class Lossy(ml.Adapter):  # the push "succeeds" but the tip does not carry the record
        def git(self, args, cwd, env=None, input_text=None):
            if args[0] == "push":
                return subprocess.CompletedProcess(args, 0, "", "")
            return super().git(args, cwd, env, input_text)

    with pytest.raises(ml.LogError, match="read-back"):
        ml.post(Lossy(), w, "origin", "w", "all", "NEEDS-INTERFACE", "x", None, [], NOSLEEP)


# ============================================================================================ AC-BUS-4
def _rec(kind, corr, when, i):
    r = ml.make_record("s", "all", kind, "body", corr, [], when)
    r["id"] = ml.new_ulid(when)
    return r


def test_ac_bus_4_audit_flags_after_without_before_and_passes_a_correct_pair(tmp_path):
    remote = make_remote(tmp_path)
    w = make_clone(tmp_path, remote, "w")
    _post(w, "all", "PROD-BEFORE", "about to", corr="ok-1")
    _post(w, "all", "PROD-AFTER", "done", corr="ok-1")
    clean = ml.audit(ml.Adapter(), w, "origin", open_minutes=120)
    assert clean["findings"] == [] and clean["records"] == 2
    _post(w, "all", "PROD-AFTER", "done but never announced", corr="orphan-1")
    _post(w, "all", "STOP", "stopped", corr="orphan-2")
    res = ml.audit(ml.Adapter(), w, "origin", open_minutes=120)
    assert [(f["rule"], f["corr"]) for f in res["findings"]] == [("after-without-before", "orphan-1"),
                                                                 ("stop-without-before", "orphan-2")]
    rc = ml.main(["--repo", str(w), "audit", "--open-minutes", "120"])
    assert rc == 3


def test_ac_bus_4_a_before_with_no_terminal_after_n_minutes_is_flagged_and_stop_closes_it(tmp_path):
    remote = make_remote(tmp_path)
    w = make_clone(tmp_path, remote, "w")
    _post(w, "all", "PROD-BEFORE", "a", corr="open-1")
    _post(w, "all", "PROD-BEFORE", "b", corr="stopped-1")
    _post(w, "all", "STOP", "gave up", corr="stopped-1")
    _post(w, "all", "PROD-BEFORE", "c", corr="paired-1")
    _post(w, "all", "PROD-AFTER", "c done", corr="paired-1")
    later = ml._now() + datetime.timedelta(minutes=121)
    res = ml.audit(ml.Adapter(), w, "origin", open_minutes=120, now=later)
    assert [(f["rule"], f["corr"]) for f in res["findings"]] == [("before-open", "open-1")]
    soon = ml.audit(ml.Adapter(), w, "origin", open_minutes=120, now=ml._now() + datetime.timedelta(minutes=5))
    assert soon["findings"] == [], "control: a young BEFORE is not an open change yet"


def test_ac_bus_4_n_comes_from_the_policy_with_default_120(tmp_path):
    remote = make_remote(tmp_path)
    w = make_clone(tmp_path, remote, "w")
    _post(w, "all", "PROD-BEFORE", "a", corr="open-1")
    assert ml.audit(ml.Adapter(), w, "origin")["open_minutes"] == 120
    (w / ".foundry").mkdir()
    (w / ".foundry" / "operating-model.yaml").write_text("schema_version: 1\nmessaging:\n  open_change_minutes: 10\n")
    res = ml.audit(ml.Adapter(), w, "origin", now=ml._now() + datetime.timedelta(minutes=11))
    assert res["open_minutes"] == 10 and [f["rule"] for f in res["findings"]] == ["before-open"]


def test_ac_bus_4_audit_replay_flags_an_after_that_precedes_its_before():
    now = ml._now()
    recs = [_rec("PROD-AFTER", "c", now, 0), _rec("PROD-BEFORE", "c", now, 1)]
    assert [f["rule"] for f in ml.audit_records(recs, 120, now)] == ["after-without-before"]
    assert ml.audit_records(recs[::-1], 120, now) == []


# ============================================================================================ AC-BUS-5
def test_ruleset_template_prints_force_push_and_deletion_blocks_for_the_branch(capsys):
    assert ml.main(["ruleset-template"]) == 0
    t = json.loads(capsys.readouterr().out)
    assert {r["type"] for r in t["rules"]} == {"non_fast_forward", "deletion"}
    assert t["conditions"]["ref_name"]["include"] == ["refs/heads/foundry/messages"]
    assert t["enforcement"] == "active" and t["target"] == "branch" and t["bypass_actors"] == []


def _observe(tmp_path, cfg, name):
    remote = make_remote(tmp_path, name + ".git", cfg)
    scratch = make_clone(tmp_path, remote, name)
    return ml.observe_ruleset_enforcement(ml.Adapter(), scratch, "origin", "scratch-branch"), remote


def test_ac_bus_5_force_push_is_rejected_where_the_template_rules_are_enforced(tmp_path):
    cfg = ml.git_equivalent_of(ml.ruleset_template())
    assert cfg == {"receive.denyNonFastForwards": "true", "receive.denyDeletes": "true"}
    verdict, remote = _observe(tmp_path, cfg, "enforced")
    assert verdict == "enforced"
    # the observation was real: the control push landed and the force-push did not move the ref
    assert git(remote, "log", "--format=%s", "scratch-branch").stdout.split("\n")[0] == "observe: control"
    # deletion is refused too
    scratch = tmp_path / "enforced"
    assert git(scratch, "push", "origin", ":scratch-branch", check=False).returncode != 0


def test_ac_bus_5_control_an_unenforced_remote_reports_unverifiable_tier_b_never_a_pass(tmp_path):
    verdict, remote = _observe(tmp_path, {}, "created-not-enforced")  # rules "created", nothing enforces them
    assert verdict == "unverifiable:tier-b"
    assert verdict != "enforced"
    assert git(remote, "log", "--format=%s", "scratch-branch").stdout.split("\n")[0] == "observe: diverging"


def test_ac_bus_5_a_refused_control_push_concludes_nothing(tmp_path):
    remote = make_remote(tmp_path, config={"receive.denyCurrentBranch": "refuse"})
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    scratch = make_clone(tmp_path, remote, "s")
    assert ml.observe_ruleset_enforcement(ml.Adapter(), scratch, "origin", "b") == "unverifiable:control-failed"


# ============================================================================================ AC-BUS-7
class StubGh(ml.Adapter):
    def __init__(self, comments):
        self.comments, self.pages, self.calls = comments, [], 0

    def gh(self, args, stdin=None):
        self.calls += 1
        m = re.fullmatch(r"repos/([^/]+)/([^/]+)/issues/(\d+)/comments\?per_page=(\d+)&page=(\d+)", args[1])
        assert args[0] == "api" and m, args
        per, page = int(m.group(4)), int(m.group(5))
        self.pages.append(page)
        return json.dumps(self.comments[(page - 1) * per: page * per])


def _comments(n, start=1):
    out = []
    for i in range(start, start + n):
        text = f"plain note {i}"
        if i % 50 == 1:
            text = f"TO: peer-{i}\nBEFORE: apply change {i}"
        elif i % 50 == 2:
            text = f"AFTER: applied change {i - 1}"
        out.append({"id": 9000 + i, "user": {"login": "someone"}, "created_at": f"2026-03-01T00:{i // 60:02d}:{i % 60:02d}Z",
                    "body": text})
    return out


ISSUE = "https://github.com/example-org/example-repo/issues/7"


def test_ac_bus_7_import_twice_writes_the_comments_once_paginated(tmp_path):
    remote = make_remote(tmp_path)
    w = make_clone(tmp_path, remote, "w")
    gh = StubGh(_comments(230))
    first = ml.import_issue(gh, w, "origin", ISSUE, NOSLEEP)
    assert first == {"imported": 230, "skipped_existing": 0, "skipped_empty": 0}
    assert gh.pages[:3] == [1, 2, 3], "paginated 100 per page"
    n = len(remote_lines(remote))
    again = ml.import_issue(StubGh(_comments(230)), w, "origin", ISSUE, NOSLEEP)
    assert again == {"imported": 0, "skipped_existing": 230, "skipped_empty": 0}
    assert len(remote_lines(remote)) == n == 230
    # control: a comment added later is imported -- once
    more = ml.import_issue(StubGh(_comments(231)), w, "origin", ISSUE, NOSLEEP)
    assert more["imported"] == 1 and len(remote_lines(remote)) == 231
    refs = [json.loads(l)["refs"][0] for l in remote_lines(remote)]
    assert len(set(refs)) == 231 and refs[0] == f"{ISSUE}#issuecomment-9001"


def test_ac_bus_7_kind_is_inferred_from_to_before_after_lines_else_finding(tmp_path):
    remote = make_remote(tmp_path)
    w = make_clone(tmp_path, remote, "w")
    ml.import_issue(StubGh(_comments(3)), w, "origin", ISSUE, NOSLEEP)
    recs = [json.loads(l) for l in remote_lines(remote)]
    assert [r["kind"] for r in recs] == ["PROD-BEFORE", "PROD-AFTER", "FINDING"]
    assert recs[0]["to"] == "peer-1" and recs[2]["to"] == "all"
    assert recs[0]["corr"] == recs[1]["corr"], "the AFTER pairs with the BEFORE before it"
    assert ml.audit(ml.Adapter(), w, "origin", open_minutes=120)["findings"] == []
    assert ml.infer("BEFORE") == ("all", "PROD-BEFORE") and ml.infer("we talked before the AFTER") == ("all", "FINDING")


def test_ac_bus_7_empty_comments_and_bad_urls_and_the_row_ceiling(tmp_path, monkeypatch):
    remote = make_remote(tmp_path)
    w = make_clone(tmp_path, remote, "w")
    cs = _comments(2)
    cs[1]["body"] = "   "
    assert ml.import_issue(StubGh(cs), w, "origin", ISSUE, NOSLEEP)["skipped_empty"] == 1
    with pytest.raises(ml.LogError, match="not an issue URL"):
        ml.import_issue(StubGh([]), w, "origin", "https://example.com/x/y/issues/1", NOSLEEP)
    monkeypatch.setattr(ml, "IMPORT_ROW_CEILING", 200)
    with pytest.raises(ml.LogError, match="ceiling"):
        ml.import_issue(StubGh(_comments(250)), w, "origin", ISSUE, NOSLEEP)
    assert len(remote_lines(remote)) == 1, "the ceiling refusal wrote nothing"


def test_ac_bus_7_gh_failure_is_a_named_hard_failure(tmp_path):
    class Down(ml.Adapter):
        def gh(self, args, stdin=None):
            raise RuntimeError("gh api failed: boom")

    w = make_clone(tmp_path, make_remote(tmp_path), "w")
    rc = ml.main(["--repo", str(w), "import", ISSUE], adapter=Down(), sleep=NOSLEEP)
    assert rc == 1


# ============================================================================================ AC-BUS-8
def _p(*rel):
    return os.path.join(ROOT, *rel)


CENSUS_FILES = [_p("scripts", "foundry_message_log.py"), _p("scripts", "foundry_message_kind.py"),
                _p("docs", "how-to", "shared-message-log.md"),
                _p("cli", "templates", "workflows", "message-log-chat-mirror.yml"),
                _p("docs", "permission-floor.json")]
NO_BUS = [r"\bbus\b"]
NO_TOKEN = ["c" + "tx"]


def _hits(paths, patterns):
    return [(os.path.basename(p), pat) for p in paths for pat in patterns
            if re.search(pat, open(p, encoding="utf-8").read(), re.IGNORECASE)]


def test_ac_bus_8_no_workspace_specific_name_in_the_change_set():
    assert _hits(CENSUS_FILES, PRIVATE + PRODUCT + NO_TOKEN) == []
    assert _hits(CENSUS_FILES[:4], NO_BUS) == [], "operator text says 'the shared message log', never the other word"


def test_ac_bus_8_planted_name_control_is_caught(tmp_path):
    for real in CENSUS_FILES[:4]:
        planted = tmp_path / os.path.basename(real)
        planted.write_text(open(real, encoding="utf-8").read() + "\n# see agentic-workspace and the bus\n")
        got = _hits([str(planted)], PRODUCT + NO_BUS)
        assert len(got) == 2, real
    assert _hits([_planted(tmp_path, "owner: lsliwka")], PRIVATE)


def _planted(tmp_path, text):
    p = tmp_path / "planted.txt"
    p.write_text(text)
    return str(p)


# ============================================================================================ floor / hook / mirror
def _floor():
    return json.load(open(_p("docs", "permission-floor.json")))["entries"]


def test_permission_floor_tiers_the_two_scripts_and_is_byte_mirrored():
    assert open(_p("docs", "permission-floor.json"), "rb").read() == open(_p("cli", "permission-floor.json"), "rb").read()
    tiers = {e["rule"].split("/scripts/", 1)[1].rstrip(")"): e["tier"] for e in _floor() if "foundry_message_" in e["rule"]}
    assert tiers == {"foundry_message_log.py post:*": "ask", "foundry_message_log.py import:*": "ask",
                     "foundry_message_log.py read:*": "allow", "foundry_message_log.py audit:*": "allow",
                     "foundry_message_log.py ruleset-template:*": "allow", "foundry_message_kind.py:*": "allow"}


def test_allow_hook_does_not_silently_allow_post_or_import_but_allows_the_reads():
    import importlib.util
    spec = importlib.util.spec_from_file_location("allowhook", _p("hooks", "foundry-plugin-scripts-allow.py"))
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    root = os.path.realpath(ROOT)
    script = os.path.join(root, "scripts", "foundry_message_log.py")
    for verb in ("post --to all --kind STOP --body-file /tmp/x", "import https://github.com/o/r/issues/1"):
        assert hook.allowed(f"python3 {script} {verb}", root) is False, verb
    for verb in ("read --to me", "audit", "ruleset-template"):
        assert hook.allowed(f"python3 {script} {verb}", root) is True, verb
    assert hook.allowed(f"python3 {os.path.join(root, 'scripts', 'foundry_message_kind.py')} --in /tmp/x", root) is True


def test_chat_mirror_template_is_opt_in_prod_only_sha_pinned_least_privilege():
    import yaml
    text = open(_p("cli", "templates", "workflows", "message-log-chat-mirror.yml"), encoding="utf-8").read()
    doc = yaml.safe_load(text)
    on = doc.get(True, doc.get("on"))
    assert set(on) == {"schedule", "workflow_dispatch"}, "no push/dispatch-per-message trigger"
    assert doc["permissions"] == {"contents": "read"}
    steps = [s for j in doc["jobs"].values() for s in j["steps"]]
    for s in steps:
        if "uses" in s:
            assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", s["uses"]), s["uses"]
    assert "PROD-BEFORE" in text and "PROD-AFTER" in text
    assert "vars.MESSAGE_LOG_CHAT_MIRROR" in text and "== 'on'" in text, "off unless explicitly turned on"
    for other in ("FINDING", "TICK", "NEEDS-INTERFACE", "CHALLENGE"):
        assert other not in text


def test_policy_keys_exist_with_the_documented_defaults(tmp_path):
    import foundry_operating_model as om
    p = om.load(tmp_path)
    assert (p.get("messaging.machine_channel"), p.get("messaging.people_channel"), p.get("messaging.chat_mirror"),
            p.get("messaging.open_change_minutes")) == ("git-log", "issue-per-topic", "off", 120)
