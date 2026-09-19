#!/usr/bin/env python3
"""foundry-merge-when-green — the one primitive that replaces sleep-then-poll
(feat-merge-when-green, AC-MWG-1..2, R2 of autonomy-continuation).

Measured: 90 sleep-blocks x 83 "pending checks" refusals across the driver's own tick loop, and
one permanent deadlock where no check ever reported. `hooks/foundry-git-discipline.sh`'s `gh pr
merge` clause is right to refuse a merge while checks are pending -- what was missing is the
primitive that WAITS correctly: this CLI polls the same two live queries the clause already
trusts, merges through the one already-permitted path the instant every check is green AND
`mergeStateStatus` is `CLEAN`, and escalates ONCE with evidence when no check will ever report at
all -- rather than handing an agent a bare `gh pr merge` to retry by hand, sleeping an arbitrary
guess each time.

    foundry-merge-when-green.py <pr> [--timeout-min N] [--squash] [--poll-interval-sec N]
                                      [--no-checks-grace-min N] [--watch]

Exit codes: 0 merged, 3 blocked (a failing check, or a non-CLEAN mergeStateStatus -- named in
`reason`; a behind-main state carries `remediation: git rebase origin/main`), 4 escalate (AC-MWG-2
-- no check has reported for `--no-checks-grace-min` minutes, default 5, and no workflow is
configured for this repo at all, so waiting is provably pointless).

Prints one JSON object on stdout: `{"status": "merged"|"blocked"|"escalate"|"waiting", "pr",
"merge_commit", "checks": [...], "reason", "remediation"}`. In `--watch` mode, one such object is
printed per observed state change (`status: "waiting"` for every non-terminal poll, flushed
immediately so a native `Monitor` sees it as a fresh line), and the process exits after printing
the terminal (`merged`/`blocked`/`escalate`) object -- exactly one per invocation, never a line
that repeats an unchanged state.

`--admin` is never constructed here, in any form, on any path (AC-MWG-4) -- this CLI's own merge
call is the SAME plain `gh pr merge <pr> --squash` the git-discipline hook's clause already
admits on a checks-green query; nothing here asks that hook for a new exemption.

RESIDUAL (recorded here rather than silently narrowed -- see the shipping PR): AC-MWG-2 reads "no
workflow whose paths match the PR's changed files". Deciding that fully requires fetching and
parsing every configured workflow's own `on:`-trigger `paths`/`paths-ignore` filters against the
PR's changed-file list -- a materially larger feature the frozen AC-MWG-5 test list does not
itself exercise beyond the single "no-checks case". This round implements the unconditionally
correct special case of that condition: the workflow list for the repo is EMPTY (there is
provably no CI configured at all, so no workflow's paths -- there being none -- can ever match).
A repo with one or more configured workflows never escalates through this path, even if none of
them happen to trigger on the PR's specific changed files; that finer partition is deferred.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone

_GH_TIMEOUT_SEC = 30            # per-invocation cap on any single `gh` call, mirrors the guard's own.
_DEFAULT_POLL_INTERVAL_SEC = 20
_DEFAULT_TIMEOUT_MIN = 60
_DEFAULT_NO_CHECKS_GRACE_MIN = 5

# Never advertised to an operator: the floor below is REAL production behaviour. This is the one
# escape hatch that lets the pytest suite drive many fast poll iterations without a real 20s+
# sleep per iteration, without weakening the shipped default for anyone who does not set it.
_POLL_FLOOR_TEST_ENV = "FOUNDRY_MWG_TEST_POLL_FLOOR_SEC"

# mergeStateStatus values GitHub can report. CLEAN is the only mergeable one; BEHIND/BLOCKED are
# both treated as "needs a rebase onto the base branch" (BLOCKED is GitHub's own overloaded value
# for "requires the branch be up to date and it is not", the common real-world cause pairing with
# BEHIND in the charter's own "BLOCKED-behind-main" naming) -- every other non-CLEAN value is
# reported verbatim with a generic remediation rather than guessed at.
_REBASE_STATES = frozenset({"BEHIND", "BLOCKED"})


class GhUnavailable(Exception):
    """`gh` could not be run at all (not on PATH, or the query itself errored/timed out)."""


def _run_gh(args: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["gh", *args], capture_output=True, text=True, timeout=_GH_TIMEOUT_SEC,
        )
    except FileNotFoundError as e:
        raise GhUnavailable(f"`gh` is not on PATH: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise GhUnavailable(f"gh {' '.join(args)} timed out after {_GH_TIMEOUT_SEC}s") from e


def _poll_floor_sec() -> float:
    raw = os.environ.get(_POLL_FLOOR_TEST_ENV)
    if raw is None:
        return float(_DEFAULT_POLL_INTERVAL_SEC)
    try:
        return float(raw)
    except ValueError:
        return float(_DEFAULT_POLL_INTERVAL_SEC)


def parse_checks(raw_stdout: str) -> list[dict]:
    """Parse `gh pr checks`'s plain tab-separated rows (name, state, elapsed, url) into
    structured dicts. Blank lines are skipped; a row with fewer columns than expected still
    yields its available fields rather than being dropped, so a malformed row is visible in the
    JSON result instead of silently vanishing."""
    rows = []
    for line in raw_stdout.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        name = cols[0].strip() if len(cols) > 0 else line.strip()
        state = cols[1].strip().lower() if len(cols) > 1 else ""
        rows.append({"name": name, "state": state})
    return rows


def gh_pr_checks(pr: int) -> tuple[list[dict], int, str]:
    """Runs `gh pr checks <pr>` -- the SAME query `hooks/foundry-git-discipline.sh`'s `gh pr
    merge` clause already trusts (AC-MWG-4: no new exemption, no different query). Returns
    (rows, returncode, combined_output)."""
    proc = _run_gh(["pr", "checks", str(pr)])
    out = (proc.stdout or "") + (proc.stderr or "")
    return parse_checks(proc.stdout or ""), proc.returncode, out


def gh_pr_view(pr: int, json_fields: str) -> dict:
    proc = _run_gh(["pr", "view", str(pr), "--json", json_fields])
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return {}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def gh_pr_merge(pr: int) -> subprocess.CompletedProcess:
    """The ONE merge command this CLI ever issues. Always `--squash`, NEVER `--admin` -- the same
    plain shape `hooks/foundry-git-discipline.sh`'s clause already admits on a checks-green
    query (AC-MWG-4)."""
    return _run_gh(["pr", "merge", str(pr), "--squash"])


def gh_workflows() -> list | None:
    """`gh api repos/{owner}/{repo}/actions/workflows` -- the placeholder form `gh` itself
    resolves against the current repo context. Returns None (never []) when the query could not
    be answered at all, so a transport failure is never mistaken for "zero workflows configured"
    (AC-MWG-2 fails CLOSED toward NOT escalating when this is unknown)."""
    try:
        proc = _run_gh(["api", "repos/{owner}/{repo}/actions/workflows"])
    except GhUnavailable:
        return None
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return None
    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    workflows = doc.get("workflows")
    return workflows if isinstance(workflows, list) else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _result(status: str, pr: int, checks: list[dict], *, merge_commit=None, reason=None,
            remediation=None, **extra) -> dict:
    doc = {
        "status": status, "pr": pr, "merge_commit": merge_commit, "checks": checks,
        "reason": reason, "remediation": remediation,
    }
    doc.update(extra)
    return doc


def _emit(doc: dict) -> None:
    print(json.dumps(doc), flush=True)


def run(pr: int, *, timeout_min: float, poll_interval_sec: float, no_checks_grace_min: float,
        watch: bool, sleep=time.sleep, clock=_now) -> tuple[dict, int]:
    """The poll loop. Factored as a function of injectable `sleep`/`clock` so the test suite
    drives many iterations with no real wall-clock wait. Returns (result_doc, exit_code); the
    caller is responsible for printing intermediate `--watch` lines this function already
    yields via `_emit` and for printing the final line."""
    poll_interval = max(poll_interval_sec, _poll_floor_sec())
    start = clock()
    deadline = start.timestamp() + timeout_min * 60
    first_empty_checks_at = None
    last_watch_line = None

    while True:
        now = clock()
        try:
            rows, rc, raw = gh_pr_checks(pr)
        except GhUnavailable as e:
            doc = _result("blocked", pr, [], reason=f"gh pr checks could not run: {e}",
                          remediation="Fix gh's availability on PATH, then retry.")
            return doc, 3
        if rc != 0:
            doc = _result("blocked", pr, rows,
                          reason=f"gh pr checks exited {rc} (checks query failed): {raw.strip()}",
                          remediation="Retry once the checks query itself succeeds.")
            return doc, 3

        failing = [r for r in rows if r["state"] == "fail"]
        if failing:
            names = ", ".join(r["name"] for r in failing)
            doc = _result("blocked", pr, rows, reason=f"failing check(s): {names}",
                          remediation=f"Fix {names} and push a new commit; re-run "
                                      f"foundry-merge-when-green.py {pr} once it is green.")
            return doc, 3

        if rows:
            # Checks are reporting at all -- the AC-MWG-2 grace clock is about a query that comes
            # back with NO rows whatsoever, not a still-pending one, so any non-empty result
            # (pending or passing) resets it.
            first_empty_checks_at = None

        if rows and all(r["state"] == "pass" for r in rows):
            view = gh_pr_view(pr, "mergeStateStatus")
            merge_state = view.get("mergeStateStatus")
            if merge_state == "CLEAN":
                merge_proc = gh_pr_merge(pr)
                if merge_proc.returncode != 0:
                    doc = _result("blocked", pr, rows,
                                  reason=f"gh pr merge exited {merge_proc.returncode}: "
                                        f"{(merge_proc.stderr or merge_proc.stdout or '').strip()}",
                                  remediation="Inspect the merge failure and retry.")
                    return doc, 3
                commit_view = gh_pr_view(pr, "mergeCommit")
                merge_commit = (commit_view.get("mergeCommit") or {}).get("oid")
                doc = _result("merged", pr, rows, merge_commit=merge_commit)
                return doc, 0
            if merge_state in _REBASE_STATES:
                doc = _result("blocked", pr, rows,
                              reason=f"mergeStateStatus is {merge_state!r} (behind the base branch)",
                              remediation="git rebase origin/main")
                return doc, 3
            if merge_state and merge_state != "CLEAN":
                doc = _result("blocked", pr, rows,
                              reason=f"mergeStateStatus is {merge_state!r}, not CLEAN",
                              remediation="Resolve the PR's mergeability directly, then retry.")
                return doc, 3
            # merge_state unknown/unreadable -- keep polling rather than guessing.
            watch_line = _result("waiting", pr, rows, reason="checks green; waiting on mergeStateStatus")
        elif not rows:
            # AC-MWG-2 -- no check has reported at all (a SUCCESSFUL query, zero rows; distinct
            # from a query failure, handled above).
            if first_empty_checks_at is None:
                first_empty_checks_at = now
            elapsed_min = (now.timestamp() - first_empty_checks_at.timestamp()) / 60.0
            if elapsed_min >= no_checks_grace_min:
                workflows = gh_workflows()
                if workflows is not None and len(workflows) == 0:
                    doc = _result(
                        "escalate", pr, [], why_operator="external-provisioning",
                        reason=(f"no check has reported in {elapsed_min:.1f} min "
                                "(>= --no-checks-grace-min) and this repo has zero workflows "
                                "configured -- no check can ever report"),
                        remediation="An operator must wire up CI for this repo (or the PR's base "
                                    "branch) before this PR can be merged.",
                        evidence={"checks": [], "workflows": []},
                    )
                    return doc, 4
                # workflows exist (or are unknown) -- CI may yet be provisioning; keep waiting.
            watch_line = _result("waiting", pr, [], reason="no check has reported yet")
        else:
            # a genuine mix that is neither all-pass nor empty nor failing -- still pending.
            watch_line = _result("waiting", pr, rows, reason="one or more checks still pending")

        if watch:
            if watch_line != last_watch_line:
                _emit(watch_line)
                last_watch_line = watch_line

        if now.timestamp() >= deadline:
            doc = _result("blocked", pr, rows,
                          reason=f"timed out after {timeout_min} minute(s) waiting for checks",
                          remediation=f"Re-run foundry-merge-when-green.py {pr}, or raise "
                                      "--timeout-min.")
            return doc, 3

        sleep(poll_interval)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Poll a PR's checks + mergeStateStatus and merge it the instant both are "
                    "green, or refuse/escalate with structured evidence.",
    )
    ap.add_argument("pr", type=int, help="the PR number -- a LITERAL integer, never a variable")
    ap.add_argument("--timeout-min", type=float, default=_DEFAULT_TIMEOUT_MIN)
    ap.add_argument("--squash", action="store_true", default=True,
                    help="always on -- squash is the only merge strategy this primitive issues; "
                         "the flag is accepted for explicitness and forward compatibility")
    ap.add_argument("--poll-interval-sec", type=float, default=_DEFAULT_POLL_INTERVAL_SEC,
                    help=f"floored at {_DEFAULT_POLL_INTERVAL_SEC}s in production")
    ap.add_argument("--no-checks-grace-min", type=float, default=_DEFAULT_NO_CHECKS_GRACE_MIN)
    ap.add_argument("--watch", action="store_true",
                    help="print one line per observed state change; arm a native Monitor on it")
    args = ap.parse_args(argv)

    doc, exit_code = run(
        args.pr, timeout_min=args.timeout_min, poll_interval_sec=args.poll_interval_sec,
        no_checks_grace_min=args.no_checks_grace_min, watch=args.watch,
    )
    _emit(doc)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
