# Foundry Governance

Governance turns a human spec into rules an implementing agent can follow without reinventing your architecture or
security principles. The rules ship as read-only kits in the plugin. You add private rules in your own layer. Nothing you
add reaches the plugin repository.

This page is a map. Each section points at the live file that holds the detail; it does not copy rule text. Rule text
lives in `governance/kits/<category>/rules.yaml` and in the rendered copy in your workspace.

## Vocabulary

| Term | Meaning | Where it lives |
|---|---|---|
| Rule | One statement with an id, a category, an adoption level, a check binding, an exception policy and its sources. | `governance/kits/<category>/rules.yaml` |
| Kit | A versioned, read-only bundle of the rules for one category, with its checks and templates. | `governance/kits/<category>/` in the plugin |
| Blueprint | A project type: a stack profile extended with the kits and levels it selects, the file signals that detect it and the surfaces it exposes. | `packs/stack-profiles/<id>/` |
| Adopter layer | Your private rules, overrides, exceptions and baselines. | `governance/local/` in your workspace |
| Lock | The answers file: blueprint, kit versions, levels, your declarations. Machine-owned. | `.foundry/governance.lock` |
| Rendered rules | The effective rules (kits plus your layer), generated for people and agents. Do not edit. | `governance/rendered/` |

Rule ids are `FG-<CATEGORY>-<NNN>` upstream and `<ORG>-<CATEGORY>-<NNN>` in your layer. An upstream id is never
reused or renumbered. A rule is retired by marking it superseded. The schemas are in `schema/governance-rule.schema.json`,
`schema/governance-kit.schema.json` and `schema/governance-lock.schema.json`.

## The sixteen categories and their kits

Fifteen categories are kits. The sixteenth, `factory`, is how the factory itself runs, and its surface is the
operating-model policy file (see [the operating-model policy](operating-model-policy.md)).

| Category | Kit | What it governs |
|---|---|---|
| product | `governance/kits/product/` | purpose, users, goals and non-goals |
| architecture | `governance/kits/architecture/` | layers, cycles, external dependencies, decisions, fitness checks |
| stack | `governance/kits/stack/` | approved technology, pinned versions, update presets |
| security | `governance/kits/security/` | ASVS level, threat model, supply chain, platform, secrets, agent permissions, review |
| contracts | `governance/kits/contracts/` | API style, breaking changes, event schemas, data contracts, consumer contracts |
| quality | `governance/kits/quality/` | review, style, tests, flaky-test quarantine, definition of done |
| delivery | `governance/kits/delivery/` | branches, commits, merge discipline, release, ownership, the governance trace |
| operations | `governance/kits/operations/` | pillars, service-level objectives, change announcement, readiness, rollout |
| cost | `governance/kits/cost/` | cost as an input, tags, cost diff, scale to zero |
| observability | `governance/kits/observability/` | telemetry conventions, alert hygiene, usage collection |
| data | `governance/kits/data/` | classification, retention, privacy |
| compliance | `governance/kits/compliance/` | licenses, per-file headers, bills of materials |
| ux | `governance/kits/ux/` | accessibility, front-end performance report, internationalization |
| exceptions | `governance/kits/exceptions/` | exception records, baselines, rollout of new rules, amendments |
| factory | the policy file | the operating rules of the deck and the merge flow |
| conduct | `governance/kits/conduct/` | how instruction files layer, scope and are enforced |

Each kit has a `README.md` with its own short summary. The kit index is `governance/README.md`.

## Floor and tunable

A rule is a **floor** rule when its `floor` field is true. A floor rule cannot be turned off by configuration, cannot
take an exception, cannot be overridden from your layer, and ships as a hook, a CI check or a review lens. Every other rule is **tunable**: a blueprint selects it at an
adoption level, and you may waive it with a recorded exception.

The floor rules, and the primitive that enforces each:

| Rule | Enforced by |
|---|---|
| `FG-COST-001` | `scripts/foundry_blocker_check.py` |
| `FG-DLV-001` | `hooks/foundry-git-discipline.sh` |
| `FG-DLV-004` | `scripts/foundry_queue_land.py` |
| `FG-DLV-010` | `hooks/foundry-paper-guard.py` |
| `FG-DLV-011` | `scripts/foundry_queue_land.py` |
| `FG-DLV-012` | `scripts/foundry_queue_land.py` |
| `FG-DLV-013` | `scripts/foundry_queue_land.py` |
| `FG-DLV-014` | `hooks/foundry-git-discipline.sh` |
| `FG-DLV-015` | `scripts/foundry_queue_land.py` |
| `FG-DLV-016` | `scripts/foundry_queue_land.py` |
| `FG-DLV-017` | `scripts/foundry_queue_land.py` |
| `FG-DLV-018` | `scripts/foundry_queue_land.py` |
| `FG-DLV-019` | `scripts/foundry_queue_land.py` |
| `FG-EXC-001` | `scripts/foundry_governance.py` |
| `FG-OPS-004` | `scripts/foundry_prod_step.py` |
| `FG-OPS-005` | `scripts/foundry_message_log.py` |
| `FG-QA-001` | `agents/pr-reviewer.md` |
| `FG-QA-006` | `scripts/foundry-ticket.py` |
| `FG-SEC-026` | `hooks/foundry-secret-read-guard.py` |
| `FG-SEC-032` | `agents/security-reviewer.md` |

A test compares this table with the kits (`tests/test_governance_docs.py`), so it cannot drift silently. The
operating-model policy file has its own floor keys, which can only be written as their enforced value.

Two of these are review lenses, not machine checks. A reviewer applies them and a person judges the result. Do not read
the table as "every floor rule is mechanical".

## One ticket carries the rules to the agent

The ticket is the unit of work. Besides `## Definition of Done` and `## Referenced Artifacts`, a ticket has three sections that
connect it to governance, and a fourth that states what done looks like (parsed by `scripts/foundry-ticket.py`):

- `## Design`: the components the change touches, the pattern it reuses, and the rule ids it cites.
- `## Security`: the security rule ids that apply, or `none:` with the reason.
- `## Surfaces`: one line for each surface the blueprint declares, or `n/a:` with the reason.
- `## Acceptance`: EARS bullets (`WHEN <condition>, THE SYSTEM SHALL <response>` or `THE SYSTEM SHALL <response>`),
  each with a stable id (`- AC-1: ...`), and one `Grounding:` line that names the research-gate source. A bullet
  with no id, a repeated id, or a bullet without `SHALL` is refused and named; ids are `AC-<n>`; the script caps the id length, the bullet count and the bullet length (see `AC_MAX_BULLETS` and `AC_MAX_BULLET_CHARS`). `## Acceptance criteria` names the same section. Below the design-required class a malformed section is a checklist line, not a refusal. `read-issue --json` exposes the
  parsed bullets and the grounding line. The brief renderer inlines every bullet under its id, and `pr-reviewer`
  reports a pass or a Block per id. Definition of Done stays the machine check; the bullets are the review target.

A ticket in the **design-required class** is refused with `design-required` until its Design cites at least one rule id
that exists, and with `acceptance-required` until it has an `## Acceptance` section with at least one EARS bullet and
a `Grounding:` line. The class is: a path in the policy key `governance.design_required_paths` or the blueprint's list, a
declared trust-boundary path, or a size label at or above the policy key `governance.size_tag_required`. Below the class
the sections are a checklist (a malformed `## Acceptance` is still refused).

`scripts/foundry_brief.py` builds the brief for a builder or a reviewer. It inlines the statement of every cited rule, so
the agent reads the rule, not a pointer to it.

The CI job `governance-trace` (template: `governance/kits/delivery/templates/governance-trace.yml.tmpl`) checks the
pull request against its ticket: the ticket is named, its author is trusted, a Design is present where required, the
cited ids exist, every changed path is inside the Design's components, the rendered rules are fresh and no exception has
expired. It reads policy, boundaries and the operator list from the base commit, so a pull request cannot loosen the
class that grades it.

In a product repo that a workspace governs, the job also runs `built-from`. The repository (or organisation) variable
`FOUNDRY_WORKSPACE_REPO` (`OWNER/NAME` of the workspace) arms it; the base ref's `.claude/foundry-project.json` binding is a
second source. A variable that is set but empty or malformed, or a binding that cannot be read, fails the rule; it never skips.
With neither source the rule skips and names the reason (single-repo). When armed, the pull request body or its head commit
(read from the head sha, not from the body) must carry `Built-from: <40-character workspace sha>`. Every trailer must agree, and
a trailer inside a code fence or a `>` quote is ignored. The sha must exist in the workspace repo and be an ancestor of (or equal
to) the workspace default branch.

What the rule proves: the pull request names a real commit that is on the workspace's default branch. It does not prove that
the Story was built from that commit, or that the commit is the one the ticket was planned against; the author states that, and
the operator's own test pass is the practice that judges it. The lookup uses the secret `FOUNDRY_WORKSPACE_READ_TOKEN` (a
fine-grained or App token with `contents: read` on the workspace only, ideally in a protected environment; the operator provisions
it, and its value is never written to a file). If the secret is unset and the default job token cannot read the workspace repo, the
lookup fails and the rule fails closed. The workflow file itself is editable by a same-repo pull request, so protect
`.github/workflows/**` with CODEOWNERS plus required review, or a ruleset-required workflow.

Two honest notes on that job:

- It vets the ticket author with `gh`. A step that runs without `GH_TOKEN` cannot vet an author who is not a registered
  operator, and the `ticket-author` check then fails closed. Give the step the token the template shows.
- While your declarations are still `pending` in the lock, the declared trust-boundary globs do not yet extend the
  paths that need a security review (`FG-SEC-032`). `foundry_governance.py check` reports `declarations-pending`; it is
  the only signal. Run `declare` first.

## Review lenses

`agents/pr-reviewer.md` carries the governance lenses. They are the review checks the kits bind to:

- Conformance: the diff against each cited rule, and a governed path with no Design.
- Flag cleanup: a feature flag without an owner and a removal date.
- Rollout: a production deploy without a progressive plan and an abort condition.

`agents/security-reviewer.md` is the separate security lens. It triggers on the security-tagged paths: the kit's static
globs, and your declared trust boundaries once `declare` has run.

## Delivery of an Epic

A **Epic** is the whole body of work. An **Story** is one ticket with a runnable Definition of Done. A **batch** is the Stories
that land as one pull request. The rules of the kits reach the agent through the ticket; the delivery verbs below move the
tickets through the platform.

1. **Release plan.** `/foundry:release-plan <epic>` reads the Stories and their `blocked_by` edges from the GitHub
   Project and writes `release-plan.yaml`: **increments** of batches, the **critical path** and the **total float** of each
   batch. A Story sits in an increment after every Story that blocks it. Atoms that share a path never share an increment. See
   [plan an Epic](how-to/release-plan.md).
2. **Increment dispatch.** The command deck builds the batches of the open increment in one Workflow, within the
   WIP limit: the smaller of the policy key `deck.max_builders` and the `wip_limit` of the plan. A **Epic issue** and a Project status update show the
   state each tick.
3. **Fleet builders.** A builder on another machine or container takes a batch by a **claim** (a ref create) under a
   **lease**. Its worker id has the form `<operator>@<machine>/<container>/<session>` and differs from the lead's in the machine or container part. See
   [run builders across machines](how-to/fleet-builders.md).
4. **Review.** One full review round runs for each pull request. If it raised Blocks, one fresh-context delta review of the
   fix commits follows, and only then the head-bound security label. See
   [recover from a failed gate](how-to/recover-from-a-failed-gate.md).
5. **Retrospective.** Each Story ends with a `## Retrospective` comment. The Retrospective distill turns the comments into proposed rule
   edits: a draft pull request for your private layer, a kit-fix ticket for upstream. It proposes and never merges.
6. **Epic review.** `/foundry:epic-review <epic>` is the single final session. It leaves a draft Release pinned to the
   integration head. The operator tests, tags and publishes. See [run the Epic review](how-to/epic-review.md).

The deck token cost is bounded by the keys `deck.max_agent_calls`, `deck.max_wait_s`, `deck.note_max_tokens` and
`deck.stall_after_ticks`; see [the operating-model policy](operating-model-policy.md). The operator's own test pass is a
practice, not a control. No script asserts that it ran.

## Exceptions and baselines

An exception is a record in `governance/local/exceptions.yaml` with the rule, the scope, the reason, the owner and an
expiry date. It is reported as a skip, never as a pass, and when it expires the finding fires again. A floor rule takes no
exception. List and validate them with:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_governance.py" exceptions
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_governance.py" check
```

A baseline freezes the violations that exist today, per check, so only new ones fail. It lives in
`governance/local/baselines/`. Freeze one only after the first fully green run of that check; see
[the evidence and baseline rule](how-to/evidence-baseline-rule.md).

## Intake and upgrade are one step

`/foundry:init` and `/foundry:intake existing` run discover, select, declare, apply and baseline. The updater runs
`upgrade` after every plugin refresh: three-way merge of the kit snippets against your edits, renamed ids migrated, one
pull request when you ask for it. The steps, the exit codes and the file-ownership table are in
[governance intake and upgrade](how-to/governance-intake-and-upgrade.md).

The older `.foundry/stack-profile.lock` is absorbed into the governance lock the first time `apply` or `upgrade` runs.
Detection is a signature table plus your one-word confirmation, never an inference.

## Nothing private reaches the plugin

`foundry_governance.py leak-check` and the upstream leak guard refuse any file under `governance/local/`, any
`<ORG>-` id and any path the lock marks as yours. See `skills/upstream-submit/SKILL.md`.

## How-to pages

- [Scale-to-zero for occasional privileged jobs](how-to/scale-to-zero-privileged-jobs.md)
- [CI budgets are tools, not gates](how-to/ci-budgets-are-tools.md)
- [The evidence and baseline rule](how-to/evidence-baseline-rule.md)
- [Operator hand-off files](how-to/operator-handoff-files.md)
- [Never read a secret value](how-to/secrets-never-read.md)
- [Use the shared message log](how-to/shared-message-log.md)
- [Plan an Epic](how-to/release-plan.md)
- [Run builders across machines](how-to/fleet-builders.md)
- [Run the Epic review](how-to/epic-review.md)
