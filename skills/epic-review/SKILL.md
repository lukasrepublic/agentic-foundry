---
name: epic-review
description: Run the Epic review (/foundry:epic-review). The single final session after the last increment has merged into the integration branch - it closes the Story issues, runs the Epic's Definition of Done as the required check epic/acceptance on the integration head, confirms the milestone open count is zero, runs the Retrospective distill, creates a DRAFT Release pinned to that SHA and posts the final Project status update. It never tags or publishes. Trigger on "close out the Epic", "/foundry:epic-review", or when the deck sees every batch of the last increment merged.
---

# /foundry:epic-review

The one final session of an **Epic**. The operator tests, tags and publishes after it. A draft
Release is the hand-off. Procedure: `docs/how-to/epic-review.md`.

The work is done by `${CLAUDE_PLUGIN_ROOT}/scripts/foundry_epic_review.py`. This skill only runs it and
reports. Do not re-implement the steps here.

## Run

```
foundry_epic_review.py run <epic> --repo <owner/name> --integration <branch> --tag <vX.Y.Z>
    --plan-ref --states <json> --atoms <json> --milestone <number> --project <owner/number>
    [--learned-project <owner/number> | --learned-json <file>]
    [--epic-issue <owner/repo#n> [--trust-author] | --done-means <committed yaml>]
    [--done-means-timeout 1800] [--run-dir .]
    [--target-branch main] [--no-ruleset] [--recheck-in 300] [--dry-run]
```

1. Run with `--dry-run` first. Read the report. It writes nothing.
2. Run without `--dry-run`.
3. Report the exit code and the JSON on stdout.

## Exit codes

- `0` closed out. The draft Release exists and the final status update is posted.
- `2` refused. One `refused:` line says why. No Release is created.
  Refusals: a batch is not merged (the batch is named); another session holds the lease; `epic/acceptance`
  is red or not published; the milestone open count is not zero; the ruleset cannot be created with this token
  (`external-provisioning`: hand the operator the exact ruleset call).
- `3` pending. A check was still pending after 60 seconds. The JSON `pending` list names each check and the
  `recheck_at` time. The lease is dropped. Do not wait. The next tick re-runs Epic review at that time.

## Rules

- The Definition of Done comes from the Epic issue and must pass the trust check (author and every editor). An untrusted
  one refuses with `done-means-untrusted`. A local `--done-means` file must be committed and unmodified. Run from a
  work tree at the integration head.
- The ruleset is created only after acceptance and the milestone pass. The report prints its delete command.
- At most 60 seconds are spent waiting on checks in the whole session. Running the Definition of Done has its own timeout.
- It never tags and never publishes. The Release is a draft pinned to the integration head SHA.
- If a lease is stale, the operator deletes the ref `refs/foundry/claims/epic-review/<epic>`.
