---
name: close-out
description: Close out a programme (/foundry:close-out). The single final session after the last wave has merged into the integration branch - it closes the atom issues, runs the programme's Done means as the required check programme/acceptance on the integration head, confirms the milestone open count is zero, runs the Learned distill, creates a DRAFT Release pinned to that SHA and posts the final Project status update. It never tags or publishes. Trigger on "close out the programme", "/foundry:close-out", or when the deck sees every batch of the last wave merged.
---

# /foundry:close-out

The one final session of a **programme**. The operator tests, tags and publishes after it. A draft
Release is the hand-off. Procedure: `docs/how-to/close-out.md`.

The work is done by `${CLAUDE_PLUGIN_ROOT}/scripts/foundry_close_out.py`. This skill only runs it and
reports. Do not re-implement the steps here.

## Run

```
foundry_close_out.py run <programme> --repo <owner/name> --integration <branch> --tag <vX.Y.Z>
    --plan-ref --states <json> --atoms <json> --milestone <number> --project <owner/number>
    [--learned-project <owner/number> | --learned-json <file>]
    [--programme-issue <owner/repo#n> [--trust-author] | --done-means <committed yaml>]
    [--done-means-timeout 1800] [--run-dir .]
    [--target-branch main] [--no-ruleset] [--recheck-in 300] [--dry-run]
```

1. Run with `--dry-run` first. Read the report. It writes nothing.
2. Run without `--dry-run`.
3. Report the exit code and the JSON on stdout.

## Exit codes

- `0` closed out. The draft Release exists and the final status update is posted.
- `2` refused. One `refused:` line says why. No Release is created.
  Refusals: a batch is not merged (the batch is named); another session holds the lease; `programme/acceptance`
  is red or not published; the milestone open count is not zero; the ruleset cannot be created with this token
  (`external-provisioning`: hand the operator the exact ruleset call).
- `3` pending. A check was still pending after 60 seconds. The JSON `pending` list names each check and the
  `recheck_at` time. The lease is dropped. Do not wait. The next tick re-runs close-out at that time.

## Rules

- The Done means come from the programme issue and must pass the trust check (author and every editor). An untrusted
  one refuses with `done-means-untrusted`. A local `--done-means` file must be committed and unmodified. Run from a
  work tree at the integration head.
- The ruleset is created only after acceptance and the milestone pass. The report prints its delete command.
- At most 60 seconds are spent waiting on checks in the whole session. Running the Done means has its own timeout.
- It never tags and never publishes. The Release is a draft pinned to the integration head SHA.
- If a lease is stale, the operator deletes the ref `refs/foundry/claims/close-out/<programme>`.
