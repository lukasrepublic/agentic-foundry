# Charter: the shared message log — machine channel on a protected git branch, people channel as issues

**Date:** 2026-11-04 · **Mode:** noninteractive · **Repo:** agentic-foundry · **Base:** release/2.0.0

## Goal
Brief: `intake/governance-2-0/message-log-brief.txt`. Design: `intake/governance-2-0/operating-model-port-plan.md` §5. Grounding: `.foundry/research/wf_7ca7ea73-8bd-message-log.md` (git-bug / git-annex / git-appraise / Flux write-back; every rejected option confirmed).

- `scripts/foundry_message_log.py` — `post --to <id|all> --kind <K> [--corr <id>] [--refs …] --body-file <f>` (kind lint via `scripts/foundry_message_kind.py`, re-extracted from 1.18.5 and extended with PROD-BEFORE, PROD-AFTER, STOP; ULID id; commit on orphan branch `foundry/messages`, file `messages/YYYY-MM.jsonl`, `.gitattributes` `merge=union`; push; on non-fast-forward: reset to the fetched tip, re-append, retry 5× with 2 s base, doubling, 50 % jitter, ~2 min cap; read-back verifies the id on the pushed tip; never force, never swallowed; exhaustion = exit 1 with the reason), `read --since <commit> --to <me> [--kind]` (single-branch fetch, `cursor..tip`, dedupe by id, skip + report malformed lines, cursor stored in the git dir), `audit [--open-minutes N]` (replay: AFTER/STOP without BEFORE; BEFORE with no terminal after N from `messaging.open_change_minutes`, default 120), `import <issue-url>` (one-time, paginated, kind inferred from `TO:`/`BEFORE`/`AFTER` lines else FINDING, idempotent by comment id), `ruleset-template` (prints the branch ruleset JSON: block force-push, restrict deletion).
- Policy keys in gv-03's schema: `messaging: {machine_channel: git-log, people_channel: issue-per-topic, chat_mirror: off, open_change_minutes: 120}`.
- People channel: documented as one issue per production change / request / decision; the gn-09 inbox already reads request issues. Chat mirror: an opt-in workflow template, PROD-* only, off by default.
- Operator text never says "bus": the skill and docs say "the shared message log".
- Permission floor: `post`/`import` tiered `ask`; `read`/`audit` `allow`.

## Acceptance criteria
- AC-BUS-1 WHEN `post` is given a kind outside the closed set, THE SYSTEM SHALL refuse and write nothing; a valid kind SHALL append exactly one valid JSON line.
- AC-BUS-2 WHEN `read --to X --since C` runs, THE SYSTEM SHALL return only records addressed to X or `all` after C, deduped by id.
- AC-BUS-3 WHEN three writers post concurrently against one real git remote fixture, THE SYSTEM SHALL land all three records (no loss, no force) and each writer SHALL verify its id by read-back.
- AC-BUS-4 WHEN `audit` sees a PROD-AFTER whose `corr` has no PROD-BEFORE, THE SYSTEM SHALL flag it; a correct pair SHALL pass; a BEFORE older than N minutes with no AFTER/STOP SHALL be flagged.
- AC-BUS-5 WHEN the ruleset template is applied and a force-push is attempted, the push SHALL be rejected (attempt + a normal push control); on a plan where rulesets are created-not-enforced the test SHALL report `unverifiable:tier-b` rather than pass.
- AC-BUS-7 WHEN `import` runs twice on the same issue, THE SYSTEM SHALL write the comments once.
- AC-BUS-8 THE SYSTEM SHALL contain no workspace-specific name (grep census with a planted control).

## Done when
- test:tests/test_message_log.py
- file:scripts/foundry_message_log.py
- file:scripts/foundry_message_kind.py
- file:docs/how-to/shared-message-log.md

## Escalate when
- external-provisioning
- no-consensus-after-research

## Out of scope
- The deck's use of `read`/`audit` and the prod-step wrapper (gv-13); moving claims onto a branch (gv-11).

## Scope (write boundary)
allowed_paths:
  - scripts/foundry_message_log.py
  - scripts/foundry_message_kind.py
  - schema/operating-model.schema.json
  - scripts/foundry_operating_model.py
  - tests/test_message_log.py
  - tests/fixtures/message-log/
  - docs/how-to/shared-message-log.md
  - cli/templates/workflows/message-log-chat-mirror.yml
  - docs/permission-floor.json
  - cli/permission-floor.json
  - tests/test_permission_floor_map.py
  - tests/fixtures/floor-drift-corpus.json
  - hooks/foundry-plugin-scripts-allow.py
denied_paths: []

## Verification
- pytest green (real-git fixture for AC-BUS-3, stubbed gh elsewhere); two experiments recorded in the PR body: ten simultaneous pushes to one branch; whether a commit on a non-default branch creates an issue reference event.

## Merge
agent-merges-on-green

## Amendments
| date | what changed | why reality required it | auth_seq |
|---|---|---|---|
| 2026-11-05 | Scope adds `tests/fixtures/subtraction/deleted-module-sweep.yaml` and `tests/test_subtraction_absence.py` (only the `foundry_message_kind` token leaves the frozen deleted-module set — the charter revives that exact file) and `docs/README.md` (one index line; `test_every_how_to_is_linked_from_the_docs_index` requires it). Ratified by the deck under the standing authorization. | the subtraction guard went red once the module returned; the docs-index test is a base invariant. | 1 |
