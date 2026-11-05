# How to close out a programme (`/foundry:close-out`)

Close-out is the single final session of a **programme**. It runs once, after every **batch** of the last
**wave** has merged into the integration branch. It follows PMBOK Close Project ("obtain final acceptance of
the deliverables"). The operator's own test pass, the tag and the publish come after it and stay with the
operator. A draft Release is the hand-off.

## What it does, in order

1. It refuses unless every batch of the execution plan is merged. The refusal names the unmerged batch.
2. It takes the lease by creating the ref `refs/foundry/claims/close-out/<programme>` on the integration head.
   A second session finds the ref and is refused. The lease is dropped when the session ends.
3. It closes the atom issues. Closing keywords fire only on default-branch pull requests, so close-out closes them itself.
4. It makes sure a ruleset requires the status `programme/acceptance` on the target branch (default `main`).
   If the token cannot create it, close-out stops with `external-provisioning`.
5. It runs the programme's Done means (`--done-means`, a YAML list of `{name, argv}`) and publishes the result as
   the commit status `programme/acceptance` on the integration head. Without `--done-means` it reads the status
   that is already there.
6. It confirms the milestone open count is zero.
7. It runs the Learned distill (`foundry_learned_distill.py propose`, proposals only).
8. It creates a DRAFT Release pinned to the integration head SHA, then posts the final Project status update (COMPLETE).

If `programme/acceptance` is red, or the milestone is not empty, no Release is created and the reason is printed.

## The one-minute rule

Close-out waits at most one minute on checks in the whole session. If a check is still pending, it prints the
check and the time to re-check (`pending`, `recheck_at`), drops the lease and exits with the pending code. The next deck
tick or a one-shot wake-up re-runs it. Nothing waits for CI, a merge queue or an observation window.

## Inputs

| flag | meaning |
|---|---|
| `--plan-ref` or `--plan` | the execution plan (planner ref `plan/<programme>`, or a file) |
| `--states` | JSON `{"states": {atom: merged\|...}}`, the tick's view of the atoms |
| `--atoms` | JSON `{"atoms": [{"id", "issue"}]}`, the issue number of each atom |
| `--milestone` | the milestone number of the programme |
| `--project` | `<owner>/<number>`, the programme Project that gets the status update |
| `--tag` | the tag the draft Release will carry when the operator publishes it |
| `--dry-run` | read GitHub, write nothing |

## Exit codes

Zero: closed out. Two: refused (one line on stderr). Three: pending (re-check at the recorded time).

## What stays with the operator

Tag, publish and the terminal test pass. Release notes come from the cut-release step. The repository setting
"immutable releases" makes the published Release immutable; close-out cannot set it per Release.
The ruleset stays after close-out. The operator removes `programme-acceptance-<programme>` when the programme ends,
or it blocks every pull request to the target branch that lacks the status.
