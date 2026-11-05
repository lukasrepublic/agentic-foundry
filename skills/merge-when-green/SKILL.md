---
name: merge-when-green
description: The one primitive that replaces sleep-then-poll around a merge (/foundry:merge-when-green <pr>). Polls `gh pr checks <pr>` and `gh pr view <pr> --json mergeStateStatus` and merges via the single already-permitted `gh pr merge <pr> --squash` shape the instant every check has concluded pass AND mergeStateStatus is CLEAN; blocks naming a failing check or a behind-main state (remediation `git rebase origin/main`); escalates ONCE with evidence when no check will ever report at all. Trigger instead of a hand-rolled sleep-then-retry loop, whenever a PR's checks need to be waited on before merging — "wait for PR <n> to go green and merge it", "/foundry:merge-when-green <pr>", or a session about to land a PR whose checks are still pending.
---

# /foundry:merge-when-green

**This skill is the front for landing a PR. Pick the path from the base branch's live ruleset** (gv-10,
operating-model rule A2). Read it; do not assume it:

```bash
gh api repos/{owner}/{repo}/rules/branches/{base}
```

- **The ruleset has a `merge_queue` rule and at least one required status check** — land with the queue. Run it
  from a checkout of the PR's own branch (`--dry-run` stops after the record):
  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_queue_land.py" <pr>
  ```
  It merges the base into the branch (a merge commit, never a rebase or a force), runs the tests that name a
  changed shared catalogue, posts the one-line record `PR | head | base merged at | catalogue tests | branch full
  suite | queued`, enqueues with `--auto`, reads the queue entry twice and waits. If a record column cannot be filled
  honestly it refuses and names the column. A rejection is classified from the failed log: **own files** → fix
  them; **outside the diff** → evidence posted, re-queue **once**; the **same failure again** → a quarantine
  request for the test's owner, **never a third roll**. A skipped check is not a pass. It enqueues, so it asks first. **It runs the PR branch's code locally** (the catalogue tests, the full suite, the local-green runner), so use it for the operator's own PRs, **never for a fork's**; a PR already in the queue is refused (`dequeue-first`) before any push.
- **No merge queue** — use the CLI documented below, which waits for green and merges.
- **Never** `--admin`, a force flag, or a push to a PR that is in the queue (dequeue first). The git-discipline hook
  refuses all three, and refuses `--auto` unless the base names a required check and the head is green.
- **Never edit CI to make a PR pass.** Workflows, CI scripts, test configs and quarantine lists are refused by the
  paper guard unless the ticket names them (`foundry-ticket.py ci-allow <path>`, the operator's call).

The rest of this page documents the no-queue path.

The mechanical half is `scripts/foundry-merge-when-green.py`. It is the primitive a session uses in
place of guessing a wait interval and re-checking `gh pr checks` by hand - measured at 90
sleep-blocks and 83 "pending checks" refusals across a single release, plus one permanent deadlock
where no check ever reported at all.

## When to trigger

- "wait for PR `<n>` to go green and merge it", "/foundry:merge-when-green `<pr>`".
- A session is about to land a PR that has checks still pending, rather than reasoning about
  polling itself.
- **Never** as a substitute for `hooks/foundry-git-discipline.sh`'s own `gh pr merge` clause —
  this CLI's merge call is the SAME plain, already-permitted shape that clause admits on a
  checks-green query (`gh pr merge <pr> --squash`, never `--admin`). It grants nothing the clause
  did not already allow; it only waits correctly before asking.

## When this may run (v2.0.0, the delivery rebase)

- **Only on an explicit instruction** — the operator's, or a ticket whose body says `merge: auto`.
  Never from a loop or "the PR looks done". Measured: automatic landing produced
  15 merges a day on one adopter repo, each a staging deploy.
- **The PR must be non-draft with local-green recorded for its head** (the git-discipline hook's
  clause (j) already refused the push otherwise).
- **Monitor re-arm.** A native `Monitor` watch now expires after at most 30 minutes (10 in `-p`).
  A deadline event is **not** a result: re-arm the watch on the still-running process until the
  CLI prints its terminal line (`merged` / `blocked` / `escalate`). Never treat a deadline as a
  stall or a failure.
- **Security lane.** Run the `security-reviewer` agent first when the ticket is `security: true` or
  touches auth/secrets/custody paths; CI's `security-path-base` check needs the `security-reviewed`
  label before this CLI's merge can go green.

## Review rounds before the merge (decision 5)

The merge floor is unchanged. Review before it follows one exit rule:

1. **One full review round** per PR. At most one review agent per PR runs at a time.
2. **No Block in the round** means no delta review runs.
3. **Blocks in the round** mean one fresh-context delta review. Its brief names only the fix commits and those Blocks.
   The delta review covers every line of every fix commit, including new files, flags and floor rows outside the Blocks' locators. A new defect there is graded on its own severity and can be a Block. A filed Risk that a fix commit makes worse is a new finding.
   A new Block from the delta review gets ONE narrow follow-up by the builder, scoped to that Block. The deck session verifies the follow-up diff and runs the tests itself, with no second reviewer agent, and records that in the PR's state note. If the follow-up does not close it, or a further new Block appears, park the PR with the Block as its reason for the operator.
4. **Risks** are recorded as filed items and left out of the delta brief. They are not re-litigated.
5. **Security label.** On a security-touching PR, apply `security-reviewed:<head12>-<base8>` as follows. With no Block in the full round, label the head once the full round is clean on that head. With Blocks, label only after the delta review is clean on the current head.
   A new head after the last review (a merge of the base, a rebase, a conflict resolution) gets a delta review of the commits since the reviewed head. It does not count against the one-delta budget, and the label moves only after that review is clean. A new push invalidates the label.
6. **No reviewer waits on CI.** A reviewer ends and records what to check and when. This skill runs in the deck session.

## Procedure

1. **Run the CLI directly for a short wait of at most 60 seconds**, or **arm a native `Monitor` in `--watch` mode** for
   anything that might outlast a single turn:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-merge-when-green.py" <pr> --watch
   ```
   `--watch` prints one JSON line per observed state change (`status: "waiting"` for every
   non-terminal poll) and exits on the terminal line (`merged`/`blocked`/`escalate`) — arm a
   `Monitor` on the running process rather than sleeping and re-invoking it; each stdout line is
   a notification, so the session sees a state change the moment it happens instead of guessing
   an interval.
2. **Report the terminal JSON line unmodified.** Its `status` is exactly one
   of:
   - `"merged"` — done; `merge_commit` carries the squash commit's `oid`.
   - `"blocked"` (exit 3) — `reason` names the failing check, or a non-`CLEAN`
     `mergeStateStatus`; a behind-main state carries `remediation: "git rebase origin/main"`.
     Fix the check / rebase and re-run; it is the session's own work, not an operator escalation.
   - `"escalate"` (exit 4) — no check has reported for `--no-checks-grace-min` minutes (default
     5) and the repo has no workflow configured at all, so waiting is provably pointless. This
     carries `why_operator: "external-provisioning"` and an `evidence` object (the empty checks
     list + the workflow list) — tell the operator, with that evidence; never re-run in a loop hoping
     for a different answer.
3. **Never construct `--admin` around this primitive.** If a PR is genuinely stuck behind a
   required-review or admin-bypass need, that is an operator decision (`gh pr merge --admin` by
   the operator's own hand, or a re-authorization), not something this CLI or its caller works
   around.

## Inputs

- `<pr>` — a **literal** PR number, never a shell variable or the current branch's PR (mirrors
  the discipline hook's own refusal of an ambient PR selector).
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
- **Treating `blocked` as a blocker.** A failing check or a behind-main state is the session's
  own work, not an operator escalation — only `escalate` (`why_operator:
  external-provisioning`) is.

## See also

- `hooks/foundry-git-discipline.sh` — the `gh pr merge` clause this CLI's own merge call is
  admitted by; never a new exemption.
- `context/branch-discipline.md` — the rule set for branches (ticket PRs target the release branch; the platform's branch protection is the merge gate). Retargeting re-pins the `security-reviewed` label (`docs/how-to/branching-and-cleanup.md`).
