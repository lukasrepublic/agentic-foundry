# Charter: merge primitives — queue-land, bypass and push-to-queued refusals, CI-file guard, merge plan

**Date:** 2026-11-04 · **Mode:** noninteractive · **Repo:** agentic-foundry · **Base:** release/2.0.0 · **Security lane:** yes (edits the floor hook)

## Goal
Rules A1–A4 of `intake/governance-2-0/operating-model-port-prompt.txt`; port plan §1.3 ruling 1. Grounding: GitHub merge queue + rulesets, flaky quarantine practice, PR-size research (operational + message-log sweeps).

- `hooks/foundry-git-discipline.sh`: new clause (k) `gh pr merge --auto` admitted ONLY when a live query shows the base branch's ruleset names ≥ 1 required status check AND `gh pr checks` is all green at the current head (else refused; `--admin` stays refused outright; force flags stay refused); clause (l) a `git push` to a branch whose open PR reports `isInMergeQueue` is refused ("dequeue first"); both fail closed on query error.
- `hooks/foundry-paper-guard.py`: a `ci` class — `.github/workflows/**`, CI scripts named in the repo's CI, test configs, quarantine lists — refused unless the active ticket's `paper_allowed`/`ci_allowed` names the path (A2 "never edit CI to make a PR pass").
- `scripts/foundry_queue_land.py <pr>`: merge base into the PR branch first; run the catalogue tests when a shared catalogue changed (`merge.full_suite_on_branch`); post the one-line record `PR | head | base merged at | catalogue tests | branch full suite | queued` (refuse to enqueue if any column cannot be filled honestly); enqueue via the admitted shape; read the queue entry twice; wait; classify a rejection (own-files → fix; outside-diff → post evidence, re-queue once within `requeue_budget_outside_diff`; second time → quarantine request to the owner, never a third); a skipped check is not a pass (reads the owning workflow). `skills/merge-when-green` becomes its front.
- `scripts/foundry_merge_plan.py --ready <json> --paths <json>`: clusters ready items by overlapping files/components across tickets; adds work for an area to that area's open un-queued PR; orders clusters so a shared catalogue changes once per round; one report line.

## Acceptance criteria
- AC-OM-4 WHEN `gh pr merge --admin`, a force flag, or `gh pr merge --auto` without a required check on the base is attempted, the hook SHALL refuse; a normal push to an un-queued PR SHALL be admitted (control); a push to a queued PR SHALL be refused.
- WHEN a queue rejection's failing log names a file outside the diff, `queue-land` SHALL re-queue exactly once and record the evidence; a second identical failure SHALL emit a quarantine request and SHALL NOT re-queue.
- WHEN any record-line column is unknown, `queue-land` SHALL refuse to enqueue naming the column.
- WHEN `.github/workflows/ci.yml` is edited without `ci_allowed`, the paper guard SHALL refuse; with it, admit.
- WHEN `merge-plan` is given two items touching one catalogue file, THE SYSTEM SHALL put them in one cluster and state it in one line.

## Done when
- test:tests/test_git_discipline_queue.py
- test:tests/test_queue_land.py
- test:tests/test_merge_plan.py
- file:scripts/foundry_queue_land.py
- file:scripts/foundry_merge_plan.py

## Escalate when
- security-widening
- no-consensus-after-research

## Out of scope
- The deck's call sites (gv-13); release-wave (does not exist at 2.0).

## Scope (write boundary)
allowed_paths:
  - hooks/foundry-git-discipline.sh
  - hooks/foundry-paper-guard.py
  - scripts/foundry_queue_land.py
  - scripts/foundry_merge_plan.py
  - scripts/foundry-merge-when-green.py
  - skills/merge-when-green/SKILL.md
  - tests/test_git_discipline_queue.py
  - tests/test_queue_land.py
  - tests/test_merge_plan.py
  - tests/test_git_discipline*.py
  - tests/test_paper_guard*.py
  - tests/fixtures/queue/
  - docs/merge-floor.md
  - docs/permission-floor.json
  - cli/permission-floor.json
  - tests/test_permission_floor_map.py
  - tests/fixtures/floor-drift-corpus.json
  - hooks/foundry-plugin-scripts-allow.py
denied_paths:
  - hooks/hooks.json   # gv-11 owns it

## Verification
- pytest green with stubbed `gh` (argv log proves no real call); every existing git-discipline test still green; fresh-context security-reviewer pass on the head; `security-reviewed:<head12>-924f6d00` label.

## Merge
agent-merges-on-green

## Amendments
| date | what changed | why reality required it | auth_seq |
|---|---|---|---|
| 2026-11-04 | The CI-edit lift is a NEW verb `foundry-ticket.py ci-allow <path>` (own ask row) instead of `allow --ci`; `allow <ci-path>` is refused; the paper guard honours only `ci_allowed` and the issue author's own `## Paper allowed` list for CI paths. Scope adds `scripts/foundry-ticket.py`, `tests/test_hooks_guards.py`, `tests/test_verb_path_resolution.py`, `tests/fixtures/release/cut-release-reconcile-sweep.yaml`. Clause (l) refuses an unresolvable non-local remote host (SSH alias) — only a local path or file:// skips the query. Clause (k) also reads classic branch protection. | the permission floor's prefix-subsumption test makes an `allow --ci:*` ask row impossible under the existing `allow:*` allow row; the security review found a plain `allow` lifted the CI guard silently; the base hook's `--auto` test asserted the exact behaviour this atom withdraws. | 1 |
