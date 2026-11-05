# Charter: the command deck — policy-rendered tick template, pause flag, report renderer, prod-step

**Date:** 2026-11-04 · **Mode:** noninteractive · **Repo:** agentic-foundry · **Base:** release/2.0.0

## Goal
Rules A1, A5, A6, B1, B8, B9, B10, C1, D2, E1, E2 of the port prompt onto the gn-10 seven-step deck (port plan §2 "Skills"). Grounding: executive-summary formats, STE-style plain language, SRE change records (`wf_7ca7ea73-8bd`).

- `skills/command-deck/tick-prompt.template.md` becomes a RENDERED template: `foundry_operating_model.py render-tick-section` fills sections A1 (merge plan line via `foundry_merge_plan.py`), A5 (largest cluster first; whole-backlog coverage check; owner or dated recheck hold per item), A6 (verify-after-merge: re-run Done means on merged main, batch re-checks into one record), B1 (every ready cluster dispatched up to `max_builders_effective`; fewer running than ready = STALL line), B2 (resources verdict first via gv-12), B8 (smallest size wins), B9 (pause flag checked first), C1 (cost section), D2 (prod-step lines), E1 (four-block report), E2 (style block when on). No `cd`: absolute paths + `git -C` (B6). Steps 1–7 of gn-10 stay in order; the new sections slot around them.
- `skills/command-deck/SKILL.md`: `pause [--until <ref>]` writes `.foundry/runtime/deck-pause.json`; a paused deck starts no new fan-out and finishes or parks what runs (B9); B10 rule text (fix the next brief, resume not duplicate); the report vocabulary ("the shared message log", never "bus").
- `scripts/foundry_deck_report.py --in <tick.json>`: renders exactly four blocks — Your decisions · Building now · Needs you · Ledger — first line = local date-time (policy `report.timezone`) + "X of N done, +moved"; enforces `report.max_lines`; every stuck item carries a reason or the render is refused; a tick that merged nothing and verified nothing prints `STALL` + why; refuses the word "bus"; `include_ids: false` moves ids to a trailing line.
- `scripts/foundry_prod_step.py -- <command>`: posts PROD-BEFORE (with a fresh `corr`) through gv-09's writer, runs the command, posts PROD-AFTER with the outcome (or STOP on failure after cleanup); a missing BEFORE is reported as a process miss by `audit`; subagent briefs inherit the rule via the brief renderer.
- Tick step 1 adds `foundry_message_log.py read --to <me>` and `audit`; violations are listed under Needs you.

## Acceptance criteria
- AC-OM-3 WHEN the template is rendered with the default policy, THE SYSTEM SHALL contain sections A1, A5, A6, B1, B8, B9, C1, D2 and E1 with values from the policy; with `communication.style: off` the E2 block SHALL be absent (AC-OM-11 third).
- AC-OM-7 WHEN the renderer receives more than `max_lines`, a missing block, or a stuck item without a reason, THE SYSTEM SHALL refuse naming the defect; a valid tick SHALL render four blocks.
- AC-BUS-6 WHEN the rendered template is grepped, it SHALL call `read` and `audit` and SHALL NOT contain the word "bus".
- WHEN the pause flag is set, a tick SHALL dispatch nothing new and SHALL say so in Building now.
- WHEN `prod-step` wraps a failing command, THE SYSTEM SHALL post STOP with the reason after cleanup and exit non-zero.

## Done when
- test:tests/test_command_deck_render.py
- test:tests/test_deck_report.py
- test:tests/test_prod_step.py
- file:scripts/foundry_deck_report.py
- file:scripts/foundry_prod_step.py

## Escalate when
- no-consensus-after-research

## Out of scope
- Resource measurement (gv-12), merge plan logic (gv-10), blocker lint (gv-14) — called by name.

## Scope (write boundary)
allowed_paths:
  - skills/command-deck/
  - scripts/foundry_deck_report.py
  - scripts/foundry_prod_step.py
  - scripts/foundry_operating_model.py
  - tests/test_command_deck_render.py
  - tests/test_deck_report.py
  - tests/test_prod_step.py
  - tests/test_command_deck_slim.py
  - tests/fixtures/deck/
  - docs/permission-floor.json
  - cli/permission-floor.json
  - tests/test_permission_floor_map.py
  - tests/fixtures/floor-drift-corpus.json
  - hooks/foundry-plugin-scripts-allow.py
denied_paths: []

## Verification
- pytest green; the rendered template passes gv-01's brief lint for size; no `cd` in the template (grep).

## Merge
agent-merges-on-green

## Amendments
| date | what changed | why reality required it | auth_seq |
|---|---|---|---|
| 2026-11-05 | Scope adds `schema/operating-model.schema.json` (a tunable `models: {builder, reviewer, security_reviewer}` object); goal adds port-prompt rule B7 (token discipline): the rendered template carries a model-hints block from policy with defaults (builder/reviewer = the smaller model, security_reviewer = the strongest), states that reviewers receive a diff file path never shell access, and that tick turns stay short. New AC: WHEN the template is rendered, THE SYSTEM SHALL include the model-hints block; absent `models` the defaults SHALL render. | gv-16's AC-OM-10 traceability run over the real charters found B7 mapped to no AC. | 1 |
