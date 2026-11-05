# Charter: machine primitives — deck-resources, the heavy-slot lock, hygiene

**Date:** 2026-11-04 · **Mode:** noninteractive · **Repo:** agentic-foundry · **Base:** release/2.0.0

## Goal
Rules B2, F1–F4 of the port prompt. Grounding: runner admission control, Ryuk-style reapers (existing `foundry-env-hygiene.py`), `foundry_env_isolation.py` (ports, conductor flock).

- `scripts/foundry_deck_resources.py`: measure CPU load, memory, swap, root disk AND the mount the work tree writes to (`--path`); return GREEN/AMBER/RED with thresholds from policy (`deck.heavy_slots: auto` → derived from measured CPU and memory); fixture-injectable readings (`--from-json`) for tests; prints `heavy_slots` and `max_builders_effective = min(policy, verdict)`; reports an OOM-kill signal from the kernel log when readable and resets a self-caused baseline instead of stalling.
- `scripts/foundry-deck-heavy -- <command>`: a flock slot wrapper sized by `heavy_slots` (builds on `foundry_env_isolation`'s lock helpers); heavy commands (tests, type checks, builds, installs, container stacks, infra plans) run through it; waits with a bounded timeout and reports the wait.
- Hygiene: `scripts/foundry-env-hygiene.py` gains worktree + scratch-output scope under the project label; `hooks/foundry-env-reap.sh` gains a SubagentStop entry point (wired by gv-11) that sweeps orphans; F3 "push every head" and F4 "no shared stash, diffs to files" are brief text gv-01's renderer carries (this atom supplies the text file `context/brief-hygiene.md`).

## Acceptance criteria
- AC-OM-6 WHEN readings are injected from a low-memory fixture, THE SYSTEM SHALL return RED; a healthy fixture SHALL return GREEN; AMBER SHALL reduce `max_builders_effective` by the policy step.
- WHEN `heavy_slots` is 2 and three wrapped commands start, the third SHALL wait and the wait SHALL be reported.
- WHEN a SubagentStop event names a project label, the reaper SHALL remove only resources carrying that label (control: a foreign label survives).

## Done when
- test:tests/test_deck_resources.py
- test:tests/test_deck_heavy.py
- file:scripts/foundry_deck_resources.py
- file:scripts/foundry-deck-heavy
- file:context/brief-hygiene.md

## Escalate when
- no-consensus-after-research

## Out of scope
- The tick template's use of the verdict (gv-13); hooks.json wiring (gv-11).

## Scope (write boundary)
allowed_paths:
  - scripts/foundry_deck_resources.py
  - scripts/foundry-deck-heavy
  - scripts/foundry_env_isolation.py
  - scripts/foundry-env-hygiene.py
  - hooks/foundry-env-reap.sh
  - context/brief-hygiene.md
  - tests/test_deck_resources.py
  - tests/test_deck_heavy.py
  - tests/test_env_isolation.py
  - tests/fixtures/resources/
  - docs/permission-floor.json
  - cli/permission-floor.json
  - tests/test_permission_floor_map.py
  - tests/fixtures/floor-drift-corpus.json
  - hooks/foundry-plugin-scripts-allow.py
denied_paths:
  - hooks/hooks.json

## Verification
- pytest green; no real resource mutated in tests (fixture-driven); digest re-pinned.

## Merge
agent-merges-on-green

## Amendments
| date | what changed | why reality required it | auth_seq |
|---|---|---|---|
