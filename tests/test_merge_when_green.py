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

import pytest

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


@pytest.mark.parametrize("state", ["skipped", "neutral", "cancelled"])
def test_blocked_immediately_on_terminal_nonpass_conclusion(tmp_path, state):
    # PR #180 round 2: a skipped/neutral/cancelled conclusion is TERMINAL, never "still waiting"
    # -- none of the three ever becomes `pass` on its own. Sequenced pending -> <state> (via the
    # stub's GH_STUB_CHECKS_SEQUENCE) and asserted to resolve on the SECOND poll rather than
    # riding out the full --timeout-min as an undifferentiated "timed out".
    p = _run_mwg(
        tmp_path, 30, "--timeout-min", "5",
        GH_STUB_CHECKS_SEQUENCE=f"check-a\tpending\t1s\turl\x1echeck-a\t{state}\t1s\turl",
    )
    assert p.returncode == 3, p.stdout + p.stderr
    doc = _last_json_line(p.stdout)
    assert doc["status"] == "blocked"
    assert f"check-a ({state})" in doc["reason"]
    assert doc["remediation"] == "re-run the workflow or make it a required context"
    log = _log_text(tmp_path)
    assert log.count("pr checks 30") == 2, log  # resolved on poll 2, not the full timeout


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


# ------------------------------------------------------------------------------------------ #
# ER #186 -- gh's exit codes are verdicts: 8 = pending, 1 = some failed, 0 = all passed.
# The shared stub cannot derive them from the rows (git-discipline's tests own its defaults), so
# each scenario sets GH_STUB_CHECKS_EXIT to what real gh returns for those rows.
# ------------------------------------------------------------------------------------------ #

def test_pending_exit_8_keeps_polling_and_merges_once_rows_pass(tmp_path):
    p = _run_mwg(
        tmp_path, 42, "--timeout-min", "1",
        GH_STUB_CHECKS_SEQUENCE="check-a\tpending\t1s\turl\x1echeck-a\tpass\t1s\turl",
        GH_STUB_CHECKS_EXIT=8,
        GH_STUB_VIEW_JSON=json.dumps({"mergeStateStatus": "CLEAN", "mergeCommit": {"oid": "er186"}}),
    )
    assert p.returncode == 0, p.stdout + p.stderr
    doc = _last_json_line(p.stdout)
    assert doc["status"] == "merged"
    assert "checks query failed" not in p.stdout
    assert _log_text(tmp_path).count("pr checks") >= 2


def test_failed_exit_1_blocks_naming_the_check_not_the_query(tmp_path):
    p = _run_mwg(tmp_path, 42, GH_STUB_CHECKS_OUTPUT="check-a\tfail\t1s\turl", GH_STUB_CHECKS_EXIT=1)
    assert p.returncode == 3, p.stdout + p.stderr
    doc = _last_json_line(p.stdout)
    assert doc["status"] == "blocked"
    assert "failing check(s): check-a" in doc["reason"]
    assert "checks query failed" not in doc["reason"]


def test_other_exit_or_no_rows_is_still_a_query_failure(tmp_path):
    p = _run_mwg(tmp_path, 42, GH_STUB_CHECKS_OUTPUT="check-a\tpass\t1s\turl", GH_STUB_CHECKS_EXIT=4)
    assert p.returncode == 3
    assert "checks query failed" in _last_json_line(p.stdout)["reason"]
    p = _run_mwg(tmp_path, 42, GH_STUB_CHECKS_OUTPUT="", GH_STUB_CHECKS_EXIT=8)
    assert p.returncode == 3
    assert "checks query failed" in _last_json_line(p.stdout)["reason"]


# ==================================================================== AC-BWD-5 ==================
# branch-and-worktree-discipline (v1.16.0): the base-branch refusal when the release manifest
# names an `integration_branch`.


def _write_release_manifest(project_dir, rel_id, *, integration_branch=None, state="backlog"):
    rel_dir = os.path.join(project_dir, ".foundry", "releases", rel_id)
    os.makedirs(rel_dir, exist_ok=True)
    doc = {
        "id": rel_id, "description": "d", "state": state,
        "atoms": [{"id": "a1", "spec_ref": "specs/a1.md", "contract_ref": "specs/a1.yaml",
                  "depends_on": []}],
    }
    if integration_branch is not None:
        doc["integration_branch"] = integration_branch
    import yaml
    with open(os.path.join(rel_dir, "release.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, sort_keys=False)


def test_refuses_when_pr_base_is_main_and_release_names_integration_branch(tmp_path):
    _write_release_manifest(str(tmp_path), "r-bwd1", integration_branch="release/1.16.0")
    p = _run_mwg(
        tmp_path, 55, "--release", "r-bwd1", "--timeout-min", "1",
        GH_STUB_VIEW_JSON=json.dumps({"baseRefName": "main"}),
        CLAUDE_PROJECT_DIR=str(tmp_path),
    )
    assert p.returncode == 3, p.stdout + p.stderr
    doc = _last_json_line(p.stdout)
    assert doc["status"] == "blocked"
    assert "release/1.16.0" in doc["reason"]
    assert doc["remediation"] == "gh pr edit 55 --base release/1.16.0"
    # refused BEFORE any checks poll -- the log never even queries `gh pr checks`.
    assert "pr checks" not in _log_text(tmp_path)


def test_proceeds_normally_when_pr_base_already_matches_integration_branch(tmp_path):
    _write_release_manifest(str(tmp_path), "r-bwd2", integration_branch="release/1.16.0")
    p = _run_mwg(
        tmp_path, 56, "--release", "r-bwd2", "--timeout-min", "1",
        GH_STUB_VIEW_JSON=json.dumps({
            "baseRefName": "release/1.16.0", "mergeStateStatus": "CLEAN", "mergeCommit": {"oid": "x"},
        }),
        GH_STUB_CHECKS_OUTPUT="check-a\tpass\t1s\turl",
        CLAUDE_PROJECT_DIR=str(tmp_path),
    )
    assert p.returncode == 0, p.stdout + p.stderr
    assert _last_json_line(p.stdout)["status"] == "merged"


def test_proceeds_normally_when_release_names_no_integration_branch(tmp_path):
    _write_release_manifest(str(tmp_path), "r-bwd3")  # no integration_branch at all
    p = _run_mwg(
        tmp_path, 57, "--release", "r-bwd3", "--timeout-min", "1",
        GH_STUB_VIEW_JSON=json.dumps({
            "baseRefName": "main", "mergeStateStatus": "CLEAN", "mergeCommit": {"oid": "y"},
        }),
        GH_STUB_CHECKS_OUTPUT="check-a\tpass\t1s\turl",
        CLAUDE_PROJECT_DIR=str(tmp_path),
    )
    assert p.returncode == 0, p.stdout + p.stderr
    assert _last_json_line(p.stdout)["status"] == "merged"


def test_hotfix_base_is_never_refused_even_with_an_integration_branch(tmp_path):
    _write_release_manifest(str(tmp_path), "r-bwd4", integration_branch="release/1.16.0")
    p = _run_mwg(
        tmp_path, 58, "--release", "r-bwd4", "--timeout-min", "1",
        GH_STUB_VIEW_JSON=json.dumps({
            "baseRefName": "hotfix/urgent", "mergeStateStatus": "CLEAN", "mergeCommit": {"oid": "z"},
        }),
        GH_STUB_CHECKS_OUTPUT="check-a\tpass\t1s\turl",
        CLAUDE_PROJECT_DIR=str(tmp_path),
    )
    assert p.returncode == 0, p.stdout + p.stderr
    assert _last_json_line(p.stdout)["status"] == "merged"


def test_no_release_flag_and_no_active_release_candidate_never_refuses(tmp_path):
    # a NON-active (backlog) release carrying an integration_branch is not a derivation candidate
    # (round-2 review finding 4's own scoping: `state == "active"` only) -- omitting --release here
    # still merges normally, though the derivation code path DOES run (see the auto-derivation
    # tests below for the case where it actually fires).
    _write_release_manifest(str(tmp_path), "r-bwd5", integration_branch="release/1.16.0")
    p = _run_mwg(
        tmp_path, 59, "--timeout-min", "1",
        GH_STUB_VIEW_JSON=json.dumps({
            "baseRefName": "main", "mergeStateStatus": "CLEAN", "mergeCommit": {"oid": "w"},
        }),
        GH_STUB_CHECKS_OUTPUT="check-a\tpass\t1s\turl",
        CLAUDE_PROJECT_DIR=str(tmp_path),
    )
    assert p.returncode == 0, p.stdout + p.stderr
    assert _last_json_line(p.stdout)["status"] == "merged"


def test_unresolvable_release_id_degrades_to_no_refusal_never_a_new_hard_failure(tmp_path):
    p = _run_mwg(
        tmp_path, 60, "--release", "does-not-exist-xyz", "--timeout-min", "1",
        GH_STUB_VIEW_JSON=json.dumps({
            "baseRefName": "main", "mergeStateStatus": "CLEAN", "mergeCommit": {"oid": "v"},
        }),
        GH_STUB_CHECKS_OUTPUT="check-a\tpass\t1s\turl",
        CLAUDE_PROJECT_DIR=str(tmp_path),
    )
    assert p.returncode == 0, p.stdout + p.stderr
    assert _last_json_line(p.stdout)["status"] == "merged"


# -------------------------------------------------------------------------------------- finding 4
# round-2 review: --release auto-derived when omitted, from the SINGLE active release manifest
# carrying an integration_branch -- so the autonomous callers (no --release in their examples)
# still get the AC-BWD-5 refusal.


def test_release_flag_omitted_auto_derives_the_single_active_release_and_refuses(tmp_path):
    _write_release_manifest(str(tmp_path), "r-active", integration_branch="release/1.16.0",
                            state="active")
    p = _run_mwg(
        tmp_path, 61, "--timeout-min", "1",
        GH_STUB_VIEW_JSON=json.dumps({"baseRefName": "main"}),
        CLAUDE_PROJECT_DIR=str(tmp_path),
    )
    assert p.returncode == 3, p.stdout + p.stderr
    doc = _last_json_line(p.stdout)
    assert doc["status"] == "blocked"
    assert "r-active" in doc["reason"]
    assert doc["remediation"] == "gh pr edit 61 --base release/1.16.0"


def test_ambiguous_active_releases_skip_derivation_no_refusal(tmp_path):
    _write_release_manifest(str(tmp_path), "r-active-a", integration_branch="release/1.16.0",
                            state="active")
    _write_release_manifest(str(tmp_path), "r-active-b", integration_branch="release/2.0.0",
                            state="active")
    p = _run_mwg(
        tmp_path, 62, "--timeout-min", "1",
        GH_STUB_VIEW_JSON=json.dumps({
            "baseRefName": "main", "mergeStateStatus": "CLEAN", "mergeCommit": {"oid": "amb"},
        }),
        GH_STUB_CHECKS_OUTPUT="check-a\tpass\t1s\turl",
        CLAUDE_PROJECT_DIR=str(tmp_path),
    )
    assert p.returncode == 0, p.stdout + p.stderr
    assert _last_json_line(p.stdout)["status"] == "merged"
    assert "ambiguous" in p.stderr


def test_explicit_release_flag_overrides_the_derivation(tmp_path):
    # two active releases would be ambiguous for derivation, but an EXPLICIT --release always wins
    # and is never subject to the ambiguity check.
    _write_release_manifest(str(tmp_path), "r-active-a", integration_branch="release/1.16.0",
                            state="active")
    _write_release_manifest(str(tmp_path), "r-active-b", integration_branch="release/2.0.0",
                            state="active")
    p = _run_mwg(
        tmp_path, 63, "--release", "r-active-a", "--timeout-min", "1",
        GH_STUB_VIEW_JSON=json.dumps({"baseRefName": "main"}),
        CLAUDE_PROJECT_DIR=str(tmp_path),
    )
    assert p.returncode == 3, p.stdout + p.stderr
    doc = _last_json_line(p.stdout)
    assert doc["status"] == "blocked"
    assert doc["remediation"] == "gh pr edit 63 --base release/1.16.0"


def test_auto_derived_release_never_refuses_a_hotfix_pr(tmp_path):
    _write_release_manifest(str(tmp_path), "r-active", integration_branch="release/1.16.0",
                            state="active")
    p = _run_mwg(
        tmp_path, 64, "--timeout-min", "1",
        GH_STUB_VIEW_JSON=json.dumps({
            "baseRefName": "hotfix/urgent", "mergeStateStatus": "CLEAN", "mergeCommit": {"oid": "h"},
        }),
        GH_STUB_CHECKS_OUTPUT="check-a\tpass\t1s\turl",
        CLAUDE_PROJECT_DIR=str(tmp_path),
    )
    assert p.returncode == 0, p.stdout + p.stderr
    assert _last_json_line(p.stdout)["status"] == "merged"
