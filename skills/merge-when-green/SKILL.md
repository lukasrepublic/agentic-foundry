---
name: merge-when-green
description: The one primitive that replaces sleep-then-poll around a merge (/foundry:merge-when-green <pr>). Polls `gh pr checks <pr>` and `gh pr view <pr> --json mergeStateStatus` and merges via the single already-permitted `gh pr merge <pr> --squash` shape the instant every check has concluded pass AND mergeStateStatus is CLEAN; blocks naming a failing check or a behind-main state (remediation `git rebase origin/main`); escalates ONCE with evidence when no check will ever report at all. Trigger instead of a hand-rolled sleep-then-retry loop, whenever a PR's checks need to be waited on before merging — "wait for PR <n> to go green and merge it", "/foundry:merge-when-green <pr>", or a driver about to land an atom whose checks are still pending.
---

# /foundry:merge-when-green

The mechanical half is `scripts/foundry-merge-when-green.py`. It is the primitive
`skills/mode-autonomous/SKILL.md` and `skills/command-deck/tick-prompt.template.md`'s LANDING
guidance now point at, in place of a driver guessing a `wake_seconds` interval and re-checking
`gh pr checks` by hand — measured at 90 sleep-blocks and 83 "pending checks" refusals across a
single release, plus one permanent deadlock where no check ever reported at all.

## When to trigger

- "wait for PR `<n>` to go green and merge it", "/foundry:merge-when-green `<pr>`".
- A driver (`/foundry:mode-autonomous`, a command-deck tick) is about to land an atom whose PR
  has checks still pending, rather than reasoning about polling itself.
- **Never** as a substitute for `hooks/foundry-git-discipline.sh`'s own `gh pr merge` clause —
  this CLI's merge call is the SAME plain, already-permitted shape that clause admits on a
  checks-green query (`gh pr merge <pr> --squash`, never `--admin`). It grants nothing the clause
  did not already allow; it only waits correctly before asking.

## Procedure

1. **Run the CLI directly for a short wait**, or **arm a native `Monitor` in `--watch` mode** for
   anything that might outlast a single turn:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-merge-when-green.py" <pr> --release <id> --watch
   ```
   `--release <id>` is auto-derived (the single active release's own manifest) when omitted, but
   name it explicitly when you already know it.
   `--watch` prints one JSON line per observed state change (`status: "waiting"` for every
   non-terminal poll) and exits on the terminal line (`merged`/`blocked`/`escalate`) — arm a
   `Monitor` on the running process rather than sleeping and re-invoking it; each stdout line is
   a notification, so the session sees a state change the moment it happens instead of guessing
   an interval.
2. **Hand the terminal JSON line to the tick report, unmodified.** Its `status` is exactly one
   of:
   - `"merged"` — done; `merge_commit` carries the squash commit's `oid`.
   - `"blocked"` (exit 3) — `reason` names the failing check, or a non-`CLEAN`
     `mergeStateStatus`; a behind-main state carries `remediation: "git rebase origin/main"`.
     Report as Next Task work (fix the check / rebase), never a blocker — this is the driver's
     own queue, not an operator escalation.
   - `"escalate"` (exit 4) — no check has reported for `--no-checks-grace-min` minutes (default
     5) and the repo has no workflow configured at all, so waiting is provably pointless. This
     carries `why_operator: "external-provisioning"` and an `evidence` object (the empty checks
     list + the workflow list) — feed it to `foundry_blocker_check.py` as a candidate blocker
     (`skills/command-deck/tick-prompt.template.md` §5c), never re-run in a loop hoping for a
     different answer.
3. **Never construct `--admin` around this primitive.** If a PR is genuinely stuck behind a
   required-review or admin-bypass need, that is an operator decision (`gh pr merge --admin` by
   the operator's own hand, or a re-authorization), not something this CLI or its caller works
   around.

## Inputs

- `<pr>` — a **literal** PR number, never a shell variable or the current branch's PR (mirrors
  the discipline hook's own refusal of an ambient PR selector).
- `--release <id>` (branch-and-worktree-discipline, AC-BWD-5, v1.16.0; optional) — enforces the
  release manifest's `integration_branch` (`context/branch-discipline.md`, rule 3): if release
  `<id>`'s manifest names one AND this PR's real base (`gh pr view <pr> --json baseRefName`) is
  literally `main` instead of it, refuses BEFORE polling — `blocked` (exit 3), `remediation: "gh pr
  edit <pr> --base <integration_branch>"`. **Auto-derived when omitted** (round-2 review finding
  4): the SINGLE `.foundry/releases/*/release.yaml` manifest whose `state == "active"` AND whose
  `integration_branch` is set — so the autonomous callers above, which never pass `--release`
  explicitly in their own LANDING guidance, still get the refusal. Zero or more than one active
  candidate is not a refusal, just a derivation miss (one line to stderr saying why, never onto
  the one-JSON-line-on-stdout contract). Pass `--release` explicitly to override the derivation or
  resolve an ambiguity. Never refuses a hotfix PR (any base other than `main`), and never refuses
  when the named/derived release carries no `integration_branch`. **Retargeting re-pins the
  `btb-gates` security-review label** (recorded here, not a new AC): `security-reviewed:<head12>-
  <base8>`'s `base8` is derived from the base ref name itself
  (`.github/workflows/btb-gates-base.yml`), so a PR moved from `main` onto `release/<version>`
  needs its label re-applied against the release branch's own hash — see
  `docs/how-to/branching-and-cleanup.md` for the mechanism and the branch-protection-tier
  trade-off (an unprotected release branch reads merge floor tier B/advisory; checks still gate
  this CLI's own merge either way).
- `--timeout-min` (default 60) — the whole run's wall-clock budget; on expiry without a terminal
  state, exits `blocked` with `reason: "timed out..."`.
- `--poll-interval-sec` (default, and production floor, 20) — how often the two live queries run.
- `--no-checks-grace-min` (default 5) — how long a genuinely empty checks list is tolerated
  before the escalate evidence-gathering fires.
- `--watch` — prints one line per state change instead of one terminal line; pair with a native
  `Monitor`.

## Residual (recorded, not silently narrowed)

AC-MWG-2's "no workflow whose paths match the PR's changed files" is implemented as "the repo has
zero workflows configured at all" — the unconditionally correct special case of that condition
that does not require fetching and parsing every workflow's own trigger `paths`/`paths-ignore`
filters (a materially larger feature). A repo with one or more configured workflows never
escalates through this path even when none of them would ever trigger on the PR's specific
changed files; that finer partition is deferred to a follow-up atom. See
`scripts/foundry-merge-when-green.py`'s own module docstring.

A zero-checks poll never merges even when `mergeStateStatus` already reads `CLEAN` — left as-is,
fail-closed by design: no CI reporting at all means there is no merge floor to have gone green.

## Anti-patterns

- **Sleeping an arbitrary interval and re-running `gh pr checks` by hand.** That is exactly the
  measured failure this primitive replaces — use the CLI's own poll loop (or `--watch` +
  `Monitor`), never a bespoke wait.
- **Retrying past an `escalate` verdict.** It fired once, with evidence, because no check will
  ever report; re-running the same command in a loop cannot change that answer.
- **Treating `blocked` as a blocker.** A failing check or a behind-main state is this driver's
  own Next Task, not an operator escalation — only `escalate` (`why_operator:
  external-provisioning`) is.

## See also

- `hooks/foundry-git-discipline.sh` — the `gh pr merge` clause this CLI's own merge call is
  admitted by; never a new exemption.
- `skills/mode-autonomous/SKILL.md` — the driver whose LANDING guidance now points here instead
  of a hand-rolled poll.
- `skills/command-deck/tick-prompt.template.md` — the tick's own LANDING section, same pointer.
