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
                                      [--no-checks-grace-min N] [--watch] [--release <id>]

`--release <id>` (branch-and-worktree-discipline, AC-BWD-5, v1.16.0): before polling, refuses (exit
3, `blocked`) when release `<id>`'s manifest names an `integration_branch` and this PR's real base
(`gh pr view <pr> --json baseRefName`) is literally `main` instead of it -- the atom-PR-targets-
the-release-branch rule (`context/branch-discipline.md`, rule 3). `remediation` is `gh pr edit <pr>
--base <integration_branch>`. A hotfix PR (base anything other than `main`) is never refused here;
nor is a release with no `integration_branch` at all.

**`--release` is derived automatically when omitted** (round-2 PR-#205 review finding 4: the
autonomous callers -- `skills/mode-autonomous/SKILL.md`, `skills/command-deck/tick-prompt.template.
md` -- invoke this CLI with no `--release` at all, which would otherwise make AC-BWD-5 dead code
for exactly the drivers it exists to cover): `derive_active_release_id` finds the SINGLE
`.foundry/releases/*/release.yaml` manifest whose `state == "active"` AND whose
`integration_branch` is set. Zero or more than one candidate is NOT a refusal -- it just means no
`--release` was derived, and the reason is printed to STDERR only (this CLI's one-JSON-object-on-
stdout contract is never touched by the derivation itself). Pass `--release <id>` explicitly to
override the derivation or to disambiguate.

Exit codes: 0 merged, 3 blocked (a failing check, a `skipped`/`neutral`/`cancelled` check
conclusion -- each TERMINAL, never treated as "still waiting" since none becomes `pass` on its
own, named in `reason` with remediation "re-run the workflow or make it a required context" -- or
a non-CLEAN mergeStateStatus named in `reason`; a behind-main state carries `remediation: git
rebase origin/main`), 4 escalate (AC-MWG-2 -- no check has reported for `--no-checks-grace-min`
minutes, default 5, and no workflow is configured for this repo at all, so waiting is provably
pointless).

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

Left as-is, by design (coordinator review, PR #180 round 2): a zero-checks poll never attempts a
merge even when `mergeStateStatus` already reads `CLEAN` -- `gh pr view` is never even queried
while `rows` is empty, so an empty checks list plus a coincidentally-CLEAN merge state still falls
through to the AC-MWG-2 grace/escalate path, not a merge. Fail-closed on purpose: no CI reporting
at all means there is no merge floor to have gone green in the first place.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
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

# `gh pr checks` conclusions that will NEVER become `pass` on their own -- terminal non-pass,
# blocked immediately rather than treated as "still pending" (coordinator review, PR #180 round
# 2). `fail` is handled separately above (a different reason/remediation); this is every other
# conclusion that is done reporting without having passed.
_TERMINAL_NONPASS_STATES = frozenset({"skipped", "neutral", "cancelled"})


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


_GH_CHECKS_VERDICT_EXITS = (0, 1, 8)


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


def gh_pr_base_ref(pr: int) -> str | None:
    """`gh pr view <pr> --json baseRefName` -- the PR's REAL base branch (branch-and-worktree-
    discipline, AC-BWD-5). Returns None (never raises) when the query fails/is unreadable, same
    discipline as `gh_pr_view` above."""
    view = gh_pr_view(pr, "baseRefName")
    val = view.get("baseRefName")
    return val if isinstance(val, str) else None


def integration_branch_base_refusal(pr: int, release_id: str, *, project_dir=None) -> dict | None:
    """AC-BWD-5: refuses (returns a `blocked` result dict) when the release manifest named by
    `release_id` carries an `integration_branch` AND the PR's real base is `main` instead of it --
    the structured `blocked` shape, reusing `_result` so the caller's own printing/exit-code
    plumbing is unchanged. Returns None (no refusal -- proceed to the normal poll loop) when
    `release_id` is falsy, the manifest carries no `integration_branch`, or the PR's base is
    already that integration branch (or anything other than literal `main` -- a `hotfix/<id>` ->
    `main` PR, the ONE stated exception in `context/branch-discipline.md`, is never refused here).
    Any release-loader error (a malformed/missing manifest) is swallowed -- this is an ADDITIVE
    guard, never a new way for merge-when-green to hard-fail on an unrelated release problem."""
    if not release_id:
        return None
    try:
        foundry_release = _import_foundry_release()
        release = foundry_release.load_release(release_id, project_dir=project_dir)
    except Exception:
        return None
    integration_branch = getattr(release, "integration_branch", None)
    if not integration_branch:
        return None
    base_ref = gh_pr_base_ref(pr)
    if base_ref != "main":
        return None
    return _result(
        "blocked", pr, [],
        reason=(f"PR #{pr}'s base is 'main' but release {release_id!r} names "
                f"integration_branch {integration_branch!r} -- atom PRs target the release "
                "branch, not main (context/branch-discipline.md, rule 3)"),
        remediation=f"gh pr edit {pr} --base {integration_branch}",
    )


def _import_foundry_release():
    """Sibling import that survives PYTHONSAFEPATH (the cut-release preflight runs the suite with it set,
    so the script's own directory is NOT on sys.path[0]) — the same explicit insert the other scripts use."""
    import importlib
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    return importlib.import_module("foundry_release")


def derive_active_release_id(project_dir=None) -> tuple[str | None, str | None]:
    """Round-2 review finding 4: `--release` never fires from the autonomous callers (the
    mode-autonomous/tick-prompt LANDING guidance call this CLI with no `--release` at all), so the
    base-branch refusal (AC-BWD-5) silently never runs for them. Derives the release id ITSELF
    when `--release` is omitted: the SINGLE `.foundry/releases/*/release.yaml` manifest whose
    `state == "active"` AND whose `integration_branch` is set. Returns `(release_id, None)` on
    exactly one candidate; `(None, reason)` on zero or more than one -- ambiguity is NOT a refusal,
    it just means no `--release` could be derived, and `reason` says why (printed to stderr only,
    never onto the one-JSON-line-on-stdout contract). Any per-manifest load error is skipped, not
    raised -- a malformed sibling release must never break this derivation for a healthy one."""
    pd = project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    releases_dir = os.path.join(pd, ".foundry", "releases")
    if not os.path.isdir(releases_dir):
        return None, f"no {releases_dir} directory"
    try:
        foundry_release = _import_foundry_release()
    except Exception as e:
        return None, f"foundry_release unavailable: {type(e).__name__}: {e}"
    candidates = []
    for name in sorted(os.listdir(releases_dir)):
        manifest = os.path.join(releases_dir, name, "release.yaml")
        if not os.path.isfile(manifest):
            continue
        try:
            release = foundry_release.load_release(name, project_dir=pd)
        except Exception:
            continue  # a malformed sibling manifest is skipped, never raised
        if release.state == "active" and getattr(release, "integration_branch", None):
            candidates.append(release.id)
    if len(candidates) == 1:
        return candidates[0], None
    if not candidates:
        return None, "no active release manifest carries an integration_branch"
    return None, (f"ambiguous: {len(candidates)} active releases carry an integration_branch "
                  f"({', '.join(candidates)})")


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
        # `gh pr checks` exits 0 only when EVERY check passed, 8 while any check is still pending
        # and 1 when any check failed (gh's documented exit codes) -- all three are verdicts the
        # rows already carry, not a failed query. Only another code, or a non-zero code with no
        # parseable rows at all, means the query itself failed (ER #186: the first R3 merge was
        # refused on its first poll because a pending check made gh exit 8).
        if rc not in _GH_CHECKS_VERDICT_EXITS or (rc != 0 and not rows):
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

        # A `skipped`/`neutral`/`cancelled` conclusion is TERMINAL, never "still waiting": none of
        # the three will ever become `pass` on their own (coordinator review, PR #180 round 2) --
        # the git-discipline hook's own clause misses exactly this gap (`gh pr checks` exits 0 for
        # them), so this CLI must close it rather than spin until --timeout-min on an undifferentiated
        # "timed out".
        terminal_nonpass = [r for r in rows if r["state"] in _TERMINAL_NONPASS_STATES]
        if terminal_nonpass:
            names = ", ".join(f"{r['name']} ({r['state']})" for r in terminal_nonpass)
            doc = _result("blocked", pr, rows,
                          reason=f"check(s) concluded without passing: {names}",
                          remediation="re-run the workflow or make it a required context")
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
                    help=f"floored at {_DEFAULT_POLL_INTERVAL_SEC}s in production; the ONLY way "
                         f"under that floor is the test-only {_POLL_FLOOR_TEST_ENV} env escape "
                         "(see the module docstring) -- never advertised to an operator")
    ap.add_argument("--no-checks-grace-min", type=float, default=_DEFAULT_NO_CHECKS_GRACE_MIN)
    ap.add_argument("--watch", action="store_true",
                    help="print one line per observed state change; arm a native Monitor on it")
    ap.add_argument("--release", default=None,
                    help="(AC-BWD-5) a release id whose manifest's optional `integration_branch` "
                         "is enforced: refuses BEFORE polling if this PR's base is `main` instead "
                         "of it. Omitted by default: the SINGLE active release manifest carrying "
                         "an integration_branch is derived automatically (round-2 review finding "
                         "4) -- pass this explicitly only to override that derivation or when "
                         "more than one active release makes it ambiguous.")
    args = ap.parse_args(argv)

    release_id = args.release
    if not release_id:
        release_id, derivation_note = derive_active_release_id()
        if not release_id and derivation_note:
            # Never onto stdout -- exactly one JSON object per invocation is this CLI's own
            # contract, and a derivation miss is not a failure, just an explanation.
            print(f"merge-when-green: --release not given, no base-branch refusal derived "
                  f"({derivation_note})", file=sys.stderr)
    if release_id:
        refusal = integration_branch_base_refusal(args.pr, release_id)
        if refusal is not None:
            _emit(refusal)
            return 3

    doc, exit_code = run(
        args.pr, timeout_min=args.timeout_min, poll_interval_sec=args.poll_interval_sec,
        no_checks_grace_min=args.no_checks_grace_min, watch=args.watch,
    )
    _emit(doc)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
