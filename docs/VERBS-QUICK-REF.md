# Verb quick reference

Every shipped `/foundry:<verb>`, one line each, grouped by where it sits in the loop. Outcome
first — what running the verb gets you, not what it internally does. The default path is
three verbs (`init`, `dispatch`, `merge-when-green`); see the `## Start here` table in
[README.md](../README.md) and the one-page model in
[context/operating-model.md](../context/operating-model.md).

> **What is machine-checked here, and what is not.** `tests/test_docs_claims.py` asserts the
> **roster** matches `skills/` in both directions — no shipped verb missing, no verb listed that
> does not exist. The **descriptions are prose and are not machine-checked**; when a verb's
> behavior changes, its line here has to be changed by hand. If a line below disagrees with the
> verb's own `skills/<verb>/SKILL.md`, the SKILL.md is authoritative — and the disagreement is a
> bug worth reporting.

## Start and wire

| Verb | What it produces |
|---|---|
| `/foundry:init` | Seeds the operator registry and project config for this repo |
| `/foundry:doctor` | Runs seven structural checks on the wiring — python deps, plugin manifest, hooks, skill frontmatter, stack-profile lock, operator registry, control-plane. Not a merge gate, and green here is not proof the merge floor is sound |
| `/foundry:mode` | Shows (or sets) which session posture is active |
| `/foundry:context` | Snapshots and resumes the arc-state a long session needs to hand to its successor |
| `/foundry:env-hygiene` | Flags stray environment variables and owned dev/test resources a session should not leave behind |
| `/foundry:work-isolation` | Manages the worktree write-jail and post-merge cleanup around an isolated worker — the repo-wide sweep is `scripts/foundry-worktree-gc.py --dry-run`/`--apply` (`docs/how-to/branching-and-cleanup.md`) |
| `/foundry:repos` | Repo verbs over the repos{} registry — sync (clone/fetch), status, foreach, validate |

## The ticket loop

| Verb | What it produces |
|---|---|
| `/foundry:dispatch` | Builds a ticket in an isolated worktree, runs the local-green step, and opens one PR |
| `/foundry:merge-when-green` | On your explicit instruction, waits for a PR's checks and mergeStateStatus, then merges the instant both are green — the one primitive that replaces a hand-rolled sleep-then-poll loop |
| `/foundry:verify` | Runs your stack's static validation and tests — the format/lint/typecheck/build and test recipes the active stack profile declares. Skips when no stack profile is locked |
| `/foundry:research-first` | Runs the research-first discipline at a design fork before anything is built |

## The opt-in spec lane (security: true, or an operator-chosen spec)

| Verb | What it produces |
|---|---|
| `/foundry:intake` | Turns a fuzzy ask into a ticket — or, on the spec lane, an atomic spec plus its sibling acceptance contract |
| `/foundry:authorize` | Freezes the spec and contract hashes and records your signed go-ahead |

## Release and upgrade

| Verb | What it produces |
|---|---|
| `/foundry:cut-release` | Verifies the release preconditions you staged (both manifests bumped, the changelog section written), refuses until the acceptance gate is green, then emits a publish plan **for you to run** — it never tags or pushes. Refuses a tag less than a month after the previous one unless `--hotfix` |
| `/foundry:upgrade` | After a `claude plugin update`, reports whether your adopter config has drifted from the current shape or gone malformed |
| `/foundry:post-upgrade` | After `npx update-agentic-workspace`, reads its report and walks the judgement half of the upgrade — grants into policy, `requires_capabilities` on unfrozen contracts, a truth pass over your own prose, branch gc, one PR |
| `/foundry:relock` | Re-locks your **already-locked** stack profiles after a trusted profile-version advance, refusing a downgrade or an incompatible profile |
| `/foundry:deploy-status` | Reports the live deployment status of an infra-delivered environment |

## Infra-delivery lane

| Verb | What it produces |
|---|---|
| `/foundry:id-discover` | Surveys existing infra so the delivery lane has real ground truth to start from |
| `/foundry:id-baseline` | Records the current infra state as the baseline a plan is diffed against |
| `/foundry:id-import` | Brings existing, unmanaged infra under this lane's management |
| `/foundry:id-implement` | Writes the infra-as-code that carries out the intended change |
| `/foundry:id-validate` | Checks a change statically (`tofu validate`, `tofu fmt`) before it goes further |
| `/foundry:id-test` | Runs the stack profile's policy-as-code tests against a change |
| `/foundry:id-simulate` | Dry-runs a change offline so its effect is known before the live plan |
| `/foundry:id-plan` | Produces the change plan an infra delivery will apply |
| `/foundry:id-apply` | Applies an approved infra plan to the target environment |
| `/foundry:id-sync` | Reconciles the GitOps controller toward the merged state and records what it sees |
| `/foundry:id-verify` | Confirms an applied change matches what the plan said would happen |
| `/foundry:id-promote` | Moves a verified infra change from one environment tier to the next |
| `/foundry:id-drift` | Detects where a live environment has drifted from its declared plan |
| `/foundry:id-rollback` | Rolls an applied infra change back to its prior known-good state |

Live catalog, always current: `ls skills/` in the plugin, or `/help` in any wired session.
