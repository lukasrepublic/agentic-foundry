# Charter: the operating-model policy file — schema, validator, defaults, seed, doctor row, skill

**Date:** 2026-11-04 · **Mode:** noninteractive · **Repo:** agentic-foundry · **Base:** release/2.0.0

## Goal
`.foundry/operating-model.yaml` is the `factory` governance kit's policy surface: the 25 ported rules' tunables and floors (`intake/governance-2-0/operating-model-port-prompt.txt` §2–§3; port plan §2). A workspace with no file gets every default; a floor cannot be turned off by configuration (P1).

- `schema/operating-model.schema.json` — keys exactly as the prompt §2 plus `messaging:` (gv-09) and `governance:` {design_required_paths[], size_tag_required: "L"}; every FLOOR key is a `const` (e.g. `merge.record_line_before_enqueue: true`, `deck.resource_check: true`, `cost.priced_ask_for_always_on: true`, `hygiene.teardown_required: true`, `secrets.value_reads_forbidden: true`, `merge.requeue_budget_outside_diff` max 1 min 1); tunables carry defaults in the schema.
- `scripts/foundry_operating_model.py` — `load(project_dir) -> Policy` (absent file = defaults; malformed = refuse naming the key, never silently default), `validate` (named error `floor-rule-disabled:<key>`), `status` (effective values, floor vs tunable per key, source: default | file), `explain <key>` (the rule, why, the primitive that enforces it), `render-tick-section <name>` (the policy-driven text blocks gv-13's template includes). Every other primitive reads policy only through `load`. `deck.workflow_size_source: smallest` resolves min(session guideline, tick prompt, `max_builders`).
- Seed: `cli/src/scaffold.mjs` writes the default file on create; `cli/path-classes.json` classes it framework-owned-managed-block (adopter edits inside the managed interior survive reconcile); `skills/init` step writes it; `skills/post-upgrade` validates it.
- `foundry-doctor.py` advisory row `operating-model: ok | defaults (no file) | invalid:<key>`.
- `skills/operating-model/SKILL.md` — `/foundry:operating-model status|validate|explain <key>`.

## Acceptance criteria
- WHEN no policy file exists, `load` SHALL return every default and `status` SHALL mark each key `source: default` (AC-OM-1).
- WHEN a file sets a floor key to its disabled value, `validate` SHALL exit 2 with `floor-rule-disabled:<key>` and `load` SHALL refuse (AC-OM-2); a tunable set within its enum SHALL load.
- WHEN `communication.style` is `off`, `status` SHALL say so and gv-01's brief renderer SHALL omit the E2 block (AC-OM-11 half).
- WHEN `npx create-agentic-workspace` scaffolds a workspace, the default file SHALL exist and validate; WHEN the updater reconciles an existing workspace, an adopter's tunable edit SHALL survive byte-for-byte.
- THE SYSTEM SHALL contain no workspace, repository, cloud account, product or person name in defaults or examples (AC-OM-9 for this atom; planted-name control test).

## Done when
- test:tests/test_operating_model_policy.py
- test:cli/test/operating-model-seed.test.mjs
- file:schema/operating-model.schema.json
- file:scripts/foundry_operating_model.py
- file:skills/operating-model/SKILL.md

## Escalate when
- no-consensus-after-research

## Out of scope
- The rule texts themselves (they render in gv-13's template, gv-01's brief); hooks (gv-10/11); the deck (gv-13).

## Scope (write boundary)
allowed_paths:
  - schema/operating-model.schema.json
  - scripts/foundry_operating_model.py
  - skills/operating-model/SKILL.md
  - cli/src/scaffold.mjs
  - cli/src/reconcile.mjs
  - cli/path-classes.json
  - cli/templates/operating-model.yaml.tmpl
  - cli/test/operating-model-seed.test.mjs
  - skills/init/SKILL.md
  - skills/post-upgrade/SKILL.md
  - scripts/foundry-doctor.py
  - tests/test_operating_model_policy.py
  - tests/fixtures/operating-model/
  - docs/permission-floor.json
  - cli/permission-floor.json
  - tests/test_permission_floor_map.py
  - tests/fixtures/floor-drift-corpus.json
  - hooks/foundry-plugin-scripts-allow.py
  - README.md
  - docs/VERBS-QUICK-REF.md
  - tests/test_doc_claims.py
denied_paths: []

## Verification
- python + cli suites green; doctor GREEN; digest re-pinned; a cli preview run on a fixture workspace shows the policy file as framework-owned.

## Merge
agent-merges-on-green

## Amendments
| date | what changed | why reality required it | auth_seq |
|---|---|---|---|
