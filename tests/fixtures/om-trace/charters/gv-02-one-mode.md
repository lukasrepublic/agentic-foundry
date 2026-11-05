# Charter: one operating mode — remove the single-operator/team switch

**Date:** 2026-11-04 · **Mode:** noninteractive · **Repo:** agentic-foundry · **Base:** release/2.0.0

## Goal
Operator 2026-11-04: "I do not want to have a difference between team or single mode; too much complexity; assume team and even for one developer track as if this was team of one." Remove gn-17's switch and every gate; a solo developer is a team of one.

- Delete `scripts/foundry_operating_mode.py`, `skills/team-mode/`, `tests/test_operating_mode.py`; remove `operating_mode` from `schema/foundry-project.schema.json`; remove every `require_team` / `resolve()` call and every "team mode only" sentence (39 files at f6fd8a0: `grep -rlE 'require_team|operating_mode|single-operator|enable-team|team-mode'`).
- `github_scope` {owner, planning_repo, operators[]} becomes a required key the `init` skill and `foundry-doctor.py` check (doctor: advisory line `github-scope: missing — run /foundry:init` until set; never RED on a fresh clone). A workspace with no scope still runs the base ticket loop; the GitHub-backed verbs refuse with one typed line `github-scope-missing` naming `/foundry:init` (replaces the old `single-operator-mode` refusal).
- `hooks/foundry-worktree-create.sh`: the issue-assignment lane is always available; the `single-operator-mode` reason disappears.
- Fixtures: every test that set `operating_mode: team` drops it; `tests/fixtures/floor-drift-corpus.json` and the permission-floor map drop the deleted script's rows (digest re-pinned).
- CHANGELOG 2.0.0 section: the "operating modes" paragraph is replaced by one sentence.

## Acceptance criteria
- WHEN the repository is searched for `operating_mode`, `single-operator`, `enable-team`, `team-mode` or `require_team` (excluding CHANGELOG history lines), THE SYSTEM SHALL return zero matches (control: a planted token in a fixture is found by the same grep).
- WHEN `.claude/foundry-project.json` lacks `github_scope`, THE SYSTEM SHALL let `foundry-ticket.py start|done|status` run unchanged and SHALL refuse `next|claim|reconcile|lead`, `foundry_inbox.py`, `foundry_leader_board.py` with the typed line `github-scope-missing` (exit 2) before any network call.
- WHEN `github_scope` is present and well-formed, THE SYSTEM SHALL run every GitHub-backed verb exactly as team mode did at f6fd8a0 (the gn-03/04/05/08/09/10/11/15 suites pass with the `operating_mode` fixture lines removed).
- THE SYSTEM SHALL keep doctor GREEN with and without `github_scope`.

## Done when
- cli:test -z "$(git grep -lE 'require_team|operating_mode\b|single-operator|enable-team|team-mode' -- . ':!CHANGELOG.md')"
- test:tests/test_owned_queue.py
- test:tests/test_foundry_inbox.py
- test:tests/test_dispatch_binding.py

## Escalate when
- no-consensus-after-research

## Out of scope
- The handbook rewrite (gv-18); the cli upgrade path's mode note (gv-15 handles `.claude/foundry-project.json` reconcile).

## Scope (write boundary)
allowed_paths:
  - scripts/foundry_operating_mode.py
  - skills/team-mode/
  - tests/test_operating_mode.py
  - schema/foundry-project.schema.json
  - scripts/foundry-ticket.py
  - scripts/foundry_inbox.py
  - scripts/foundry_leader_board.py
  - scripts/foundry_github_capabilities.py
  - scripts/foundry_deck_events.py
  - scripts/foundry_usage.py
  - scripts/foundry_migrate.py
  - scripts/foundry-doctor.py
  - hooks/foundry-usage-bind.py
  - hooks/foundry-worktree-create.sh
  - hooks/foundry-plugin-scripts-allow.py
  - skills/command-deck/SKILL.md
  - skills/dispatch/SKILL.md
  - skills/doctor/SKILL.md
  - skills/leader-board/SKILL.md
  - skills/init/SKILL.md
  - tests/
  - docs/permission-floor.json
  - cli/permission-floor.json
  - docs/how-to/post-upgrade.md
  - docs/VERBS-QUICK-REF.md
  - README.md
  - CHANGELOG.md
denied_paths:
  - skills/command-deck/tick-prompt.template.md   # gv-13 owns it

## Verification
- `python3 -m pytest tests/ -q` green; doctor GREEN with and without `github_scope`; the Done-when grep is empty; permission-floor digest re-pinned.

## Merge
agent-merges-on-green

## Amendments
| date | what changed | why reality required it | auth_seq |
|---|---|---|---|
| 2026-11-04 | Done-when grep uses `operating_mode\b`; `foundry_github_capabilities.py` keeps reporting `n/a (no github_scope configured)` with exit 0 (read-only probe, the doctor consumes it) — the "every GitHub-backed verb refuses" criterion applies to verbs that write or call the network; `tests/test_init_slimming.py` digest may be re-pinned to insert the GitHub-scope step before the doctor step. | gv-03 introduces `foundry_operating_model` which contains `operating_mode` as a prefix; the review found the probe's n/a report equivalent to the base; the init section was unreachable as an unnumbered heading. | 1 |
