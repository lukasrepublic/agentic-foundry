---
status: accepted
date: 2026-11-05
confirmation:
  check_id: tests/test_operating_model_policy.py::test_keys_removed_in_gv19_are_refused_as_unknown
---
# Key-only policy keys: wire or remove, per key

Date: 2026-11-05. Charter: `gv-19-dogfood`, first amendment. Status: decided.

## Context

The operating-model policy page marks six keys as "key only": the reader validates and shows them, and nothing reads them,
so changing one changes no behavior. A key that does nothing misleads the operator who sets it. Each key was traced to
every reader in the plugin (`grep` over scripts, hooks, skills, agents, templates, docs and tests). Rule used: wire a
consumer only when one is obviously missing; otherwise remove the key from the schema, the loader, the template and the docs.
Floors are untouched: none of the six is a floor, and `decisions.research_first`, `deck.*`, `secrets.*`,
`hygiene.teardown_required` and the other `const` keys are unchanged.

The `v2.0.0` release is not yet cut and the policy file is new in it, so no released adopter file carries these keys. A file
written from a pre-release template that still has one gets `unknown-key:` (exit code `2`) from `validate`; delete the line.

## Decisions

| Key | Readers found | Decision | Why |
|---|---|---|---|
| `decisions.escalation_set` | none. `scripts/foundry_blocker_check.py` lints against its own closed set. | Removed | Wiring it would turn the closed set that guards B4 into an adopter-tunable list, which can only widen the reasons to ask the operator. The lint's fixed set is the control; a tunable copy next to it is a second, weaker source of truth. |
| `report.handoff_dir` | none. The how-to said the agent "reads the policy and puts the file there". | Removed | No program writes to it and no section of the tick prompt renders it. The convention (`~/Documents/<workspace-name>`, outside the repository) now lives on `docs/how-to/operator-handoff-files.md`, which the agent follows. Rule E3 is unchanged. |
| `hygiene.disk_sweep_each_tick` | none. `scripts/foundry_deck_resources.py` measures the disk whatever the value is. | Removed | F2 says to measure the mount where the work writes. A switch that skips the measurement would let the file turn the rule off. The measurement stays unconditional. |
| `messaging.machine_channel` | none. | Removed | The enum has one value (`git-log`). A one-value choice is not a setting. |
| `messaging.people_channel` | none. | Removed | The enum has one value (`issue-per-topic`). Same reason. |
| `messaging.chat_mirror` | none in scripts. The chat-mirror workflow template runs only when the repository variable `MESSAGE_LOG_CHAT_MIRROR` is `on`. | Kept (not one of the listed keys) | It is the declaration of an opt-in that the template's own instructions tell the operator to set, it has an `on`/`off` coercion contract, and removing it changes the template instructions. Still key only; the policy page says so. |

## Consequences

- Schema, loader explanations, the template, the policy page, the shared-message-log page and the hand-off page lose the five
  keys; the tests that pinned them now pin the removal (`unknown-key:`).
- The "Acts through" table in `tests/test_governance_docs.py` lists exactly the keys the schema carries.
- Reverting a decision means adding a consumer first, then the key.
