# How to close out an epic (`/foundry:epic-review`)

Epic review is the single final session of a **epic**. It runs once, after every **batch** of the last
**increment** has merged into the integration branch. It follows PMBOK Close Project ("obtain final acceptance of
the deliverables"). The operator's own test pass, the tag and the publish come after it and stay with the
operator. A draft Release is the hand-off.

## What it does, in order

1. It refuses unless every batch of the release plan is merged. The refusal names the unmerged batch.
2. It takes the lease by creating the ref `refs/foundry/claims/epic-review/<epic>` on the integration head.
   A second session finds the ref and is refused. The lease is dropped when the session ends.
3. It closes the atom issues. Closing keywords fire only on default-branch pull requests, so epic review closes them itself.
4. It runs the epic's Done means and publishes the result as the commit status `epic/acceptance` on the
   integration head. The Done means come from the epic issue (`--epic-issue <owner>/<repo>#<n>`), through
   the same trust check as a ticket: the issue author and every editor of its body must be trusted. If not, epic review
   refuses with `done-means-untrusted` and runs nothing. The operator reads the command and may pass `--trust-author`.
   A local file (`--done-means`) is accepted only when it is a committed, unmodified file of the repo. The commands run
   as argument lists, never through a shell, in a work tree at the integration head (`--run-dir`; epic review refuses if
   `git rev-parse HEAD` differs). Each command has its own timeout (`--done-means-timeout`, default thirty minutes). With
   neither source, epic review reads the status that is already there. `--dry-run` prints the exact argument lists.
5. It confirms the milestone open count is zero.
6. Only now does it make sure a ruleset requires `epic/acceptance` on the target branch (default `main`). It
   compares enforcement, conditions and rules, not only the name, and updates a ruleset that differs. If the token
   cannot write it, epic review stops with `external-provisioning`. The report prints the delete command.
7. It runs the Learned distill (`foundry_retrospective.py propose`, proposals only).
8. It creates a DRAFT Release pinned to the integration head SHA, then posts the final Project status update (COMPLETE).

If `epic/acceptance` is red, or the milestone is not empty, no Release is created and the reason is printed.

## The one-minute rule

Epic review waits at most one minute on checks in the whole session. Running the Done means is not waiting, and has its own timeout. If a check is still pending, it prints the
check and the time to re-check (`pending`, `recheck_at`), drops the lease and exits with the pending code. The next deck
tick or a one-shot wake-up re-runs it. Nothing waits for CI, a merge queue or an observation window.

## Inputs

| flag | meaning |
|---|---|
| `--plan-ref` or `--plan` | the release plan (planner ref `plan/<epic>`, or a file) |
| `--states` | JSON `{"states": {atom: merged\|...}}`, the tick's view of the atoms |
| `--atoms` | JSON `{"atoms": [{"id", "issue"}]}`, the issue number of each atom |
| `--milestone` | the milestone number of the epic |
| `--project` | `<owner>/<number>`, the epic Project that gets the status update |
| `--tag` | the tag the draft Release will carry when the operator publishes it |
| `--epic-issue` | the epic issue whose trusted Done means is run |
| `--dry-run` | read GitHub, write nothing, list the commands |

## Exit codes

Zero: closed out. Two: refused (one line on stderr). Three: pending (re-check at the recorded time).

## What stays with the operator

Tag, publish and the terminal test pass. Release notes come from the cut-release step. The repository setting
"immutable releases" makes the published Release immutable; epic review cannot set it per Release.
The ruleset stays after epic review. The operator removes `epic-acceptance-<epic>` when the epic ends,
or it blocks every pull request to the target branch that lacks the status.
