"""tests/test_merge_when_green.py — feat-merge-when-green (AC-MWG-1..5, R2 of
autonomy-continuation).

Drives the REAL shipped `scripts/foundry-merge-when-green.py` as a subprocess against the ONE
committed `gh` stub (`tests/fixtures/gh-stub/gh`) — never a bespoke reimplementation of `gh`. Test
names double as the acceptance contract's own scenario names (AC-MWG-5): merged on green, blocked
on a failing check (naming it), escalate-once on the no-checks case, a pending->pass transition
across polls, and `--admin` never appearing in any argv this CLI issues.

`FOUNDRY_MWG_TEST_POLL_FLOOR_SEC=0` is the one documented test-only escape hatch (see the
script's own module docstring): it lets every scenario here run with a near-zero poll interval
instead of the real >=20s production floor, without weakening that floor for anyone who does not
set it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from conftest import REPO_ROOT

SCRIPT = os.path.join(REPO_ROOT, "scripts", "foundry-merge-when-green.py")
GH_STUB_DIR = os.path.join(REPO_ROOT, "tests", "fixtures", "gh-stub")
HOOKS_DIR = os.path.join(REPO_ROOT, "hooks")


def _gh_stub_env(tmp_path, **stub_vars):
    """Mirrors tests/test_hooks_guards.py's own `_gh_stub_env` shape — the SAME real stub binary,
    driven by env vars, never a per-test reimplementation."""
    env = dict(os.environ)
    env["PATH"] = f"{GH_STUB_DIR}{os.pathsep}{env['PATH']}"
    env["GH_STUB_LOG"] = str(tmp_path / "gh-invocations.log")
    env["FOUNDRY_MWG_TEST_POLL_FLOOR_SEC"] = "0"
    for k, v in stub_vars.items():
        if v is not None:
            env[k] = str(v)
    return env


def _run_mwg(tmp_path, pr, *extra_args, **stub_vars):
    env = _gh_stub_env(tmp_path, **stub_vars)
    args = [sys.executable, SCRIPT, str(pr), "--poll-interval-sec", "0", *extra_args]
    return subprocess.run(args, capture_output=True, text=True, timeout=30, env=env)


def _last_json_line(stdout: str) -> dict:
    lines = [l for l in stdout.splitlines() if l.strip()]
    assert lines, f"no output at all:\n{stdout!r}"
    return json.loads(lines[-1])


def _log_text(tmp_path) -> str:
    p = tmp_path / "gh-invocations.log"
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ==================================================================== AC-MWG-1 ==================
# merged on green


def test_merged_on_green(tmp_path):
    p = _run_mwg(
        tmp_path, 42, "--timeout-min", "1",
        GH_STUB_CHECKS_OUTPUT="check-a\tpass\t1s\turl",
        GH_STUB_VIEW_JSON=json.dumps({"mergeStateStatus": "CLEAN", "mergeCommit": {"oid": "deadbeef"}}),
    )
    assert p.returncode == 0, p.stdout + p.stderr
    doc = _last_json_line(p.stdout)
    assert doc["status"] == "merged"
    assert doc["pr"] == 42
    assert doc["merge_commit"] == "deadbeef"
    assert doc["checks"] == [{"name": "check-a", "state": "pass"}]
    assert set(doc.keys()) >= {"status", "pr", "merge_commit", "checks", "reason", "remediation"}


def test_merged_result_only_issues_squash_merge_never_admin(tmp_path):
    p = _run_mwg(
        tmp_path, 42, "--timeout-min", "1",
        GH_STUB_CHECKS_OUTPUT="check-a\tpass\t1s\turl",
        GH_STUB_VIEW_JSON=json.dumps({"mergeStateStatus": "CLEAN"}),
    )
    assert p.returncode == 0, p.stdout + p.stderr
    log = _log_text(tmp_path)
    assert "pr merge 42 --squash" in log, log
    assert "--admin" not in log, log


# blocked on a failing check, naming it


def test_blocked_on_failing_check_names_it(tmp_path):
    p = _run_mwg(
        tmp_path, 7, "--timeout-min", "0.02",
        GH_STUB_CHECKS_OUTPUT="check-a\tfail\t1s\turl",
    )
    assert p.returncode == 3, p.stdout + p.stderr
    doc = _last_json_line(p.stdout)
    assert doc["status"] == "blocked"
    assert "check-a" in doc["reason"]
    assert doc["merge_commit"] is None


def test_blocked_behind_main_carries_rebase_remediation(tmp_path):
    p = _run_mwg(
        tmp_path, 9, "--timeout-min", "0.02",
        GH_STUB_CHECKS_OUTPUT="check-a\tpass\t1s\turl",
        GH_STUB_VIEW_JSON=json.dumps({"mergeStateStatus": "BEHIND"}),
    )
    assert p.returncode == 3, p.stdout + p.stderr
    doc = _last_json_line(p.stdout)
    assert doc["status"] == "blocked"
    assert doc["remediation"] == "git rebase origin/main"


def test_blocked_on_timeout_when_never_settles(tmp_path):
    p = _run_mwg(
        tmp_path, 13, "--timeout-min", "0",
        GH_STUB_CHECKS_OUTPUT="check-a\tpending\t1s\turl",
    )
    assert p.returncode == 3, p.stdout + p.stderr
    doc = _last_json_line(p.stdout)
    assert doc["status"] == "blocked"
    assert "timed out" in doc["reason"]


# ==================================================================== AC-MWG-2 ==================
# escalate-once on the no-checks case


def test_escalate_once_on_no_checks_and_zero_workflows(tmp_path):
    p = _run_mwg(
        tmp_path, 8, "--timeout-min", "1", "--no-checks-grace-min", "0",
        GH_STUB_CHECKS_OUTPUT="",
        GH_STUB_WORKFLOWS_BODY=json.dumps({"workflows": []}),
    )
    assert p.returncode == 4, p.stdout + p.stderr
    doc = _last_json_line(p.stdout)
    assert doc["status"] == "escalate"
    assert doc["why_operator"] == "external-provisioning"
    assert doc["evidence"]["checks"] == []
    assert doc["evidence"]["workflows"] == []
    # exactly one JSON line is printed -- the escalate happens once, not in a loop
    lines = [l for l in p.stdout.splitlines() if l.strip()]
    assert len(lines) == 1, p.stdout


def test_no_checks_does_not_escalate_before_grace_elapses(tmp_path):
    # grace is 5 real minutes by default and the query never reports a check -- the loop must hit
    # the (short, test-only) timeout as "blocked", never escalate, because grace never elapsed.
    p = _run_mwg(
        tmp_path, 14, "--timeout-min", "0.01",
        GH_STUB_CHECKS_OUTPUT="",
        GH_STUB_WORKFLOWS_BODY=json.dumps({"workflows": []}),
    )
    assert p.returncode == 3, p.stdout + p.stderr
    doc = _last_json_line(p.stdout)
    assert doc["status"] == "blocked"


def test_no_checks_with_a_configured_workflow_does_not_escalate(tmp_path):
    # AC-MWG-2's residual scoping (see the script's module docstring): a repo with ANY configured
    # workflow never escalates through this path, even past the grace period -- it times out
    # "blocked" instead, which is the conservative (never-falsely-escalate) side of the residual.
    p = _run_mwg(
        tmp_path, 15, "--timeout-min", "0.01", "--no-checks-grace-min", "0",
        GH_STUB_CHECKS_OUTPUT="",
        GH_STUB_WORKFLOWS_BODY=json.dumps({"workflows": [{"path": ".github/workflows/ci.yml"}]}),
    )
    assert p.returncode == 3, p.stdout + p.stderr
    doc = _last_json_line(p.stdout)
    assert doc["status"] == "blocked"


# ==================================================================== AC-MWG-5 ==================
# a pending -> pass transition across polls


def test_pending_to_pass_transition_across_polls(tmp_path):
    p = _run_mwg(
        tmp_path, 21, "--timeout-min", "1",
        GH_STUB_CHECKS_SEQUENCE="check-a\tpending\t1s\turl\x1echeck-a\tpass\t1s\turl",
        GH_STUB_VIEW_JSON=json.dumps({"mergeStateStatus": "CLEAN", "mergeCommit": {"oid": "cafe"}}),
    )
    assert p.returncode == 0, p.stdout + p.stderr
    doc = _last_json_line(p.stdout)
    assert doc["status"] == "merged"
    assert doc["merge_commit"] == "cafe"
    log = _log_text(tmp_path)
    assert log.count("pr checks 21") == 2, log


def test_watch_mode_prints_one_line_per_state_change_and_exits_on_terminal_state(tmp_path):
    p = _run_mwg(
        tmp_path, 22, "--timeout-min", "1", "--watch",
        GH_STUB_CHECKS_SEQUENCE="check-a\tpending\t1s\turl\x1echeck-a\tpass\t1s\turl",
        GH_STUB_VIEW_JSON=json.dumps({"mergeStateStatus": "CLEAN", "mergeCommit": {"oid": "f00d"}}),
    )
    assert p.returncode == 0, p.stdout + p.stderr
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
    assert len(lines) == 2, p.stdout
    assert lines[0]["status"] == "waiting"
    assert lines[-1]["status"] == "merged"


# `--admin` never appears in any argv this CLI issues, across every scenario above.


def test_admin_never_appears_in_any_scenario(tmp_path):
    scenarios = [
        dict(GH_STUB_CHECKS_OUTPUT="check-a\tpass\t1s\turl",
             GH_STUB_VIEW_JSON=json.dumps({"mergeStateStatus": "CLEAN"})),
        dict(GH_STUB_CHECKS_OUTPUT="check-a\tfail\t1s\turl"),
        dict(GH_STUB_CHECKS_OUTPUT="", GH_STUB_WORKFLOWS_BODY=json.dumps({"workflows": []})),
    ]
    for i, stub_vars in enumerate(scenarios):
        tp = tmp_path / str(i)
        tp.mkdir()
        p = _run_mwg(tp, 100 + i, "--timeout-min", "0.02", "--no-checks-grace-min", "0", **stub_vars)
        assert "--admin" not in p.stdout
        assert "--admin" not in _log_text(tp)


# ==================================================================== AC-MWG-4 ==================
# the merge this CLI issues is admitted by the SAME live-checks query the discipline hook already
# runs -- no new exemption. Drives the real hook, over the SAME stub, for the exact command shape
# this CLI's own `gh_pr_merge` constructs.


def _discipline(cmd, extra_env=None):
    payload = json.dumps({"tool_input": {"command": cmd}})
    env = dict(extra_env or {})
    return subprocess.run(
        [str(os.path.join(HOOKS_DIR, "foundry-git-discipline.sh")), "--protected", "main"],
        input=payload, capture_output=True, text=True, env=env, timeout=60,
    )


def test_the_hooks_own_clause_admits_the_exact_command_this_cli_issues(tmp_path):
    env = _gh_stub_env(tmp_path, GH_STUB_CHECKS_EXIT=0, GH_STUB_CHECKS_OUTPUT="check-a\tpass\t1s\turl")
    p = _discipline("gh pr merge 42 --squash", extra_env=env)
    assert p.returncode == 0, p.stdout + p.stderr
