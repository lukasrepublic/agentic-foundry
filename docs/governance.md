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

The ticket is the unit of work. Besides `## Done means` and `## Paper allowed`, a ticket has three sections that
connect it to governance (parsed by `scripts/foundry-ticket.py`):

- `## Design`: the components the change touches, the pattern it reuses, and the rule ids it cites.
- `## Security`: the security rule ids that apply, or `none:` with the reason.
- `## Surfaces`: one line for each surface the blueprint declares, or `n/a:` with the reason.

A ticket in the **design-required class** is refused with `design-required` until its Design cites at least one rule id
that exists. The class is: a path in the policy key `governance.design_required_paths` or the blueprint's list, a
declared trust-boundary path, or a size label at or above the policy key `governance.size_tag_required`. Below the class
the three sections are a checklist.

`scripts/foundry_brief.py` builds the brief for a builder or a reviewer. It inlines the statement of every cited rule, so
the agent reads the rule, not a pointer to it.

The CI job `governance-trace` (template: `governance/kits/delivery/templates/governance-trace.yml.tmpl`) checks the
pull request against its ticket: the ticket is named, its author is trusted, a Design is present where required, the
cited ids exist, every changed path is inside the Design's components, the rendered rules are fresh and no exception has
expired. It reads policy, boundaries and the operator list from the base commit, so a pull request cannot loosen the
class that grades it.

In a product repo that a workspace governs, the job also runs `built-from`: the pull request body or head commit must carry
`Built-from: <40-character workspace sha>`, and that sha must exist in the workspace repo. Single-repo adopters skip the rule
with a named reason. The lookup uses the secret `FOUNDRY_WORKSPACE_READ_TOKEN` (a read-only token for the workspace repo,
exposed to the job as `WORKSPACE_GH_TOKEN`; the operator provisions it, and its value is never written to a file). If the
secret is unset and the default job token cannot read the workspace repo, the lookup fails and the rule fails closed.

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
