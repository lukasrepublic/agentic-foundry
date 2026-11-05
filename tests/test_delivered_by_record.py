"""gv-16 / rule G2: the merge step writes the delivered-by record (PR, merger login, head sha) in the same action as the merge.

  * `foundry_queue_land.py`: after the platform reports the merge, the record is posted; a record that cannot be posted
    never turns a merged PR into a refusal (it says `recorded: false`);
  * `foundry-merge-when-green.py`: the plain squash merge is followed, in the same run, by the same record.
Authorship alone never proves attestation: the record names who MERGED. `gh` is a stub; the argv logs prove nothing real ran.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO_ROOT
from test_queue_land import Env, terminal

MWG = os.path.join(REPO_ROOT, "scripts", "foundry-merge-when-green.py")
GH_STUB_DIR = os.path.join(REPO_ROOT, "tests", "fixtures", "gh-stub")


# ---------------------------------------------------------------------------------------- queue-land
def test_queue_land_records_pr_merger_login_and_head_sha(tmp_path):
    env = Env(tmp_path)
    env.arm()
    p, lines = env.run()
    assert p.returncode == 0, p.stdout + p.stderr
    done = terminal(lines)
    assert done["status"] == "merged" and done["merged_by"] == "queue-bot" and done["head"] == env.head()
    assert done["recorded"] is True and done["pr"] == "42"
    rec = env.bodies("delivered-by:")
    assert len(rec) == 1
    first = rec[0].splitlines()[0]
    fields = [x.strip() for x in first.split("|")]
    assert fields[0] == "delivered-by: PR #42" and fields[1] == "merged by queue-bot" and fields[2] == "head " + env.head()[:12]
    merge_idx = next(i for i, c in enumerate(env.calls()) if c[:2] == ["pr", "merge"])
    rec_idx = next(i for i, c in enumerate(env.calls()) if c[:2] == ["pr", "comment"] and "delivered-by:" in c[-1])
    assert rec_idx > merge_idx


def test_a_record_that_cannot_be_posted_does_not_turn_a_merge_into_a_refusal(tmp_path):
    env = Env(tmp_path)
    env.arm()
    sc = json.loads(env.scenario.read_text())
    comment = next(r for r in sc["responses"] if r["match"] == ["pr", "comment"])
    comment["rcs"] = [0, 1]                              # the pre-enqueue record posts; the delivered-by post fails
    comment["stderr"] = "HTTP 502"
    env.scenario.write_text(json.dumps(sc))
    p, lines = env.run()
    done = terminal(lines)
    assert done["status"] == "merged" and p.returncode == 0, p.stdout + p.stderr
    assert done["recorded"] is False and "record_error" in done and "delivered-by record could not be posted" in done["record_error"]


# ---------------------------------------------------------------------------------------- merge-when-green
def _mwg(tmp_path, **stub):
    e = dict(os.environ)
    e["PATH"] = f"{GH_STUB_DIR}{os.pathsep}{e['PATH']}"
    e["GH_STUB_LOG"] = str(tmp_path / "gh.log")
    e["FOUNDRY_MWG_TEST_POLL_FLOOR_SEC"] = "0"
    e.update({k: str(v) for k, v in stub.items()})
    p = subprocess.run([sys.executable, MWG, "42", "--poll-interval-sec", "0", "--timeout-min", "1"], capture_output=True,
                       text=True, timeout=30, env=e)
    log = (tmp_path / "gh.log").read_text() if (tmp_path / "gh.log").exists() else ""
    return p, json.loads([l for l in p.stdout.splitlines() if l.strip()][-1]), log


VIEW = json.dumps({"mergeStateStatus": "CLEAN", "mergeCommit": {"oid": "d" * 40}, "mergedBy": {"login": "merger-1"},
                   "headRefOid": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"})


def test_merge_when_green_posts_the_record_with_merger_and_head(tmp_path):
    p, doc, log = _mwg(tmp_path, GH_STUB_CHECKS_OUTPUT="check-a\tpass\t1s\turl", GH_STUB_VIEW_JSON=VIEW)
    assert p.returncode == 0, p.stdout + p.stderr
    assert doc["status"] == "merged" and doc["merge_commit"] == "d" * 40
    d = doc["delivered_by"]
    assert d == {"pr": 42, "merged_by": "merger-1", "head": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
                 "merge_commit": "d" * 40, "recorded": True}
    assert "pr comment 42 --body delivered-by: PR #42 | merged by merger-1 | head a1b2c3d4e5f6" in log
    assert log.index("pr merge 42 --squash") < log.index("pr comment 42")
    assert "--admin" not in log


def test_merge_when_green_record_failure_never_undoes_the_merge(tmp_path):
    p, doc, log = _mwg(tmp_path, GH_STUB_CHECKS_OUTPUT="check-a\tpass\t1s\turl", GH_STUB_VIEW_JSON=VIEW, GH_STUB_COMMENT_EXIT=1)
    assert p.returncode == 0 and doc["status"] == "merged"
    assert doc["delivered_by"]["recorded"] is False and "record_error" in doc["delivered_by"]


def test_no_record_is_posted_when_nothing_merged(tmp_path):
    p, doc, log = _mwg(tmp_path, GH_STUB_CHECKS_OUTPUT="check-a\tfail\t1s\turl")
    assert p.returncode == 3 and doc["status"] == "blocked" and "delivered_by" not in doc
    assert "pr comment" not in log
