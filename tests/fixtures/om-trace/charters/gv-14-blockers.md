# Charter: blockers — schema with attempted[] and control, the lint, priced-ask, research save-and-rule

**Date:** 2026-11-04 · **Mode:** noninteractive · **Repo:** agentic-foundry · **Base:** release/2.0.0

## Goal
Rules B3, B4, C1 of the port prompt. Re-extract `schema/blocker.schema.json` and `scripts/foundry_blocker_check.py` from plugin 1.18.5 and extend them.

- Schema: `claim`, `evidence[]` (≥1), `attempted[]` (≥1, each with the command issued and its outcome), `control` (the call that would have failed differently), `why_operator` ∈ {external-provisioning, credential-step, no-consensus-after-research, priced-ask}, `handoff` {cwd, command, why, expect} for credential/provisioning/priced-ask, `priced_ask` {purpose, cost_breakdown[]} required when kind is priced-ask; the three questions stated: capability / authority / judgement.
- Lint: partitions candidates into `blockers` and `next_tasks`; demotes a "needs the operator" claim with no attempt or no control (AC-OM-8); demotes a credential blocker that did not first try the credential a running job already holds (an `attempted` entry naming it); refuses malformed input (exit 2).
- `skills/research-first/SKILL.md`: the sweep is saved to `.foundry/research/<date>-<slug>.md` with provenance markers (sources, retrieval dates, negative findings, gaps) and a dated ruling to `.foundry/decisions/<date>-<slug>.md` when sources converge; only no-consensus reaches the operator, with options, evidence and a recommendation, asking for one word. `scripts/foundry_research_note.py` writes both files from a structured input (the shape the research workflows return).
- `scripts/foundry_inbox.py`: escalations pass through the lint before they reach `decision_needed`.

## Acceptance criteria
- AC-OM-8 WHEN a candidate has no `attempted` or no `control`, the lint SHALL place it in `next_tasks` with the reason; a complete candidate SHALL be a blocker. (rule B4)
- WHEN `why_operator` is `priced-ask` and `priced_ask.cost_breakdown` is empty, the lint SHALL refuse the candidate. (rule E3)
- WHEN `foundry_research_note.py` is given a sweep with converging sources, THE SYSTEM SHALL write the note and the ruling; with `no_consensus` non-empty it SHALL write the note and a blocker candidate instead of a ruling. (rule B3)
- THE SYSTEM SHALL make no network call in any verb.

## Done when
- test:tests/test_blocker_check.py
- test:tests/test_research_note.py
- file:schema/blocker.schema.json
- file:scripts/foundry_blocker_check.py
- file:scripts/foundry_research_note.py

## Escalate when
- no-consensus-after-research

## Out of scope
- The deck's §5c call (gv-13 names the script).

## Scope (write boundary)
allowed_paths:
  - schema/blocker.schema.json
  - scripts/foundry_blocker_check.py
  - scripts/foundry_research_note.py
  - scripts/foundry_inbox.py
  - skills/research-first/SKILL.md
  - tests/test_blocker_check.py
  - tests/test_research_note.py
  - tests/test_foundry_inbox.py
  - tests/fixtures/blockers/
  - docs/permission-floor.json
  - cli/permission-floor.json
  - tests/test_permission_floor_map.py
  - tests/fixtures/floor-drift-corpus.json
  - hooks/foundry-plugin-scripts-allow.py
denied_paths: []

## Verification
- pytest green; digest re-pinned; no private names.

## Merge
agent-merges-on-green

## Amendments
| date | what changed | why reality required it | auth_seq |
|---|---|---|---|
| 2026-11-04 | `why_operator` narrows from the 1.18.5 six to four (external-provisioning, credential-step, no-consensus-after-research, priced-ask) per the port prompt; `schema/acceptance-contract.schema.json` and `foundry_contract.py` still carry the old six (follow-up for gv-16). `questions` {capability, authority, judgement} is REQUIRED on every candidate (B4 is a floor); `options`/`recommendation`/`one_word_ask` required for no-consensus. Scope adds `tests/fixtures/subtraction/deleted-module-sweep.yaml`, `tests/test_subtraction_absence.py` (the reintroduced module leaves the deleted-module set) and `tests/test_plugin_scripts_allow.py` (writer/reader rows). | the review found the optional `questions` made a floor rule optional; the subtraction guard went red once the module returned. | 1 |
