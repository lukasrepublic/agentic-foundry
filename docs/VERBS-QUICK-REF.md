# Verb quick reference

Every shipped `/foundry:<verb>`, one line each, grouped by where it sits in the loop. Outcome
first — what running the verb gets you, not what it internally does. For the six verbs that
matter on day one, see the `## Start here` table in [README.md](../README.md); this page is the
full catalog, kept in lockstep with `skills/` by the build-time contract that shipped it.

## Start and wire

| Verb | What it produces |
|---|---|
| `/foundry:init` | Seeds the operator registry and project config for this repo |
| `/foundry:doctor` | Reports whether every wired guard and skill is present and healthy |
| `/foundry:mode` | Shows which session posture and merge-autonomy mode are active |
| `/foundry:mode-interactive` | Switches to the zero-ceremony lane: plain Claude Code, no pipeline |
| `/foundry:mode-autonomous` | Switches to the noninteractive posture for unattended runs |
| `/foundry:context` | Loads the workspace and product context a session needs to start |
| `/foundry:env-hygiene` | Flags stray environment variables a session should not be carrying |
| `/foundry:work-isolation` | Confirms the session is working inside its own isolated worktree |

## Specify

| Verb | What it produces |
|---|---|
| `/foundry:intake` | Turns a fuzzy ask into an atomic spec plus its sibling acceptance contract |
| `/foundry:spec-review` | Runs fresh-context reviewer questions against a draft spec and records the verdict |
| `/foundry:extract-spec` | Surveys an existing codebase and promotes a capability into a candidate spec |
| `/foundry:research-first` | Runs the research-first discipline at a design fork before anything is built |
| `/foundry:research-capture` | Records prior-art grounding gathered during a research pass |
| `/foundry:grounding-conformance` | Checks a spec's citations still resolve to the sources they claim |
| `/foundry:report-citation-graph` | Renders the citation graph linking specs, code, and grounding sources |
| `/foundry:index` | Lists the live catalog of every shipped verb straight from the tree |
| `/foundry:skill-authoring` | Walks through drafting a new skill to the shipped skill-authoring shape |

## Authorize and build

| Verb | What it produces |
|---|---|
| `/foundry:authorize` | Freezes the spec and contract hashes and records your signed go-ahead |
| `/foundry:dispatch` | Builds the authorized spec in an isolated worktree and opens a PR |
| `/foundry:revert` | Rolls a merged change back out through the same governed loop |
| `/foundry:upgrade` | Walks a dependency or toolchain bump through the same governed loop |
| `/foundry:relock` | Refreshes a pinned lockfile against the standing-versions matrix |

## Certify and release

| Verb | What it produces |
|---|---|
| `/foundry:certify-local` | Deploys a release once locally and runs every atom's tagged journeys against it |
| `/foundry:certify-staging` | Runs the same journey suite against a shared staging deployment |
| `/foundry:verify` | Re-checks a release's evidence trail without re-running the full journey suite |
| `/foundry:audit` | Produces an independent findings pass over a release's governance trail |
| `/foundry:coherence-check` | Confirms a release's specs, contracts, and code still agree with each other |
| `/foundry:authorize-release` | Records the operator go-ahead that gates a multi-atom release from shipping |
| `/foundry:release` | Plans a multi-atom wave, or records your own sign-off on what shipped |
| `/foundry:cut-release` | Bumps the plugin version and writes the dated changelog section adopters see |
| `/foundry:upstream-submit` | Prepares a change for submission back to an upstream project |

## Infra-delivery lane

| Verb | What it produces |
|---|---|
| `/foundry:id-discover` | Surveys existing infra so the delivery lane has real ground truth to start from |
| `/foundry:id-baseline` | Records the current infra state as the baseline a plan is diffed against |
| `/foundry:id-architect` | Drafts the target infra architecture a plan will implement |
| `/foundry:id-plan` | Produces the change plan an infra delivery will apply |
| `/foundry:id-simulate` | Dry-runs a plan against the target so its effect is known before it applies |
| `/foundry:id-impact` | Reports what a plan would change before anyone approves it |
| `/foundry:id-review` | Routes an infra plan through reviewer scrutiny before it applies |
| `/foundry:id-validate` | Checks a plan against policy and schema before it is allowed to apply |
| `/foundry:id-implement` | Writes the infra-as-code that carries out an approved plan |
| `/foundry:id-apply` | Applies an approved infra plan to the target environment |
| `/foundry:infra-sandboxed-apply` | Applies a plan inside a sandboxed target before touching anything real |
| `/foundry:id-verify` | Confirms an applied change matches what the plan said would happen |
| `/foundry:id-test` | Runs the infra-delivery lane's own test suite against a change |
| `/foundry:id-document` | Writes the record of what an infra delivery changed and why |
| `/foundry:id-promote` | Moves a verified infra change from one environment tier to the next |
| `/foundry:id-drift` | Detects where a live environment has drifted from its declared plan |
| `/foundry:drift-sweep` | Sweeps a fleet of environments for the same drift, all at once |
| `/foundry:id-sync` | Reconciles a drifted environment back to its declared plan |
| `/foundry:id-rollback` | Rolls an applied infra change back to its prior known-good state |
| `/foundry:id-import` | Brings existing, unmanaged infra under this lane's management |
| `/foundry:data-tier-cutover` | Walks a data-tier migration through a governed cutover sequence |
| `/foundry:decommission-gate` | Gates the teardown of infra that is no longer in use |
| `/foundry:dashboards-as-code` | Generates monitoring dashboards from the same declared plan as the infra |
| `/foundry:deploy-status` | Reports the live deployment status of an infra-delivered environment |

## Craft lane

| Verb | What it produces |
|---|---|
| `/foundry:sd-discover` | Surveys an existing craft-lane codebase before a debug or build pass starts |
| `/foundry:sd-plan-tests` | Plans the test coverage a craft-lane change will need before it is written |
| `/foundry:sd-debug` | Walks a reproduction-first debugging pass through the craft lane |
| `/foundry:sd-document` | Writes up what a craft-lane change did and why, for the next reader |
| `/foundry:sd-review` | Routes a craft-lane change through reviewer scrutiny before it merges |
| `/foundry:sd-test` | Runs the craft lane's own test suite against a change |
| `/foundry:sd-verify` | Confirms a craft-lane change behaves the way its plan said it would |

## Fleet, learning and upstream

| Verb | What it produces |
|---|---|
| `/foundry:fleet` | Reports doctor status across every repo this operator has wired up |
| `/foundry:learn-capture` | Records a learning surfaced during a session for later distillation |
| `/foundry:learn-distill` | Turns captured learnings into a durable update to the shipped guidance |

Live catalog, always current: run `/foundry:index` in any wired session.
