# Charter: blueprint kits — stack profiles extended with governance, detection and surfaces

**Date:** 2026-11-04 · **Mode:** noninteractive · **Repo:** agentic-foundry · **Base:** release/2.0.0

## Goal
A blueprint kit IS a stack profile (`packs/stack-profiles/<id>/`, `schema/stack-profile.schema.json`, `.foundry/stack-profile.lock`, `/foundry:relock`) extended (operator yes 2026-11-04). Grounding: Backstage templates + Soundcheck tracks, Copier answers file, buildpacks ordered detect groups, Renovate manager file patterns, Nx createNodes (kits sweep).

- Schema additions: `governance: {kits: {<category>: {level: 1|2|3}}, design_required_paths: []}`, `surfaces: [cli|api|ui|workflows|mcp|goal-agents]` (D3), `detect: {signals: [{kind: manifest|lockfile|ci|iac|container|dependency|path, pattern, weight}], confidence_threshold}` (grown from `matches`; `matches` stays as an alias), `readiness_checklist: path`, `test_strategy: path`.
- The four existing profiles gain those blocks; four new profiles: `service-event-driven`, `cli-tool`, `claude-plugin` (so the foundry governs itself), `data-pipeline` — each with `conventions.md`, `architecture.allowed_dependencies`, `security_checklist` replaced by kit selection, `detect`, `surfaces`, standing-versions manifest with research timestamps.
- `foundry_blueprint.py`/`foundry-stack-profile.py`: load + validate the new blocks; `blueprints_sha256` digest covers them; `profile-version-ledger.json` bumped; `relock` unchanged in behaviour.
- `.foundry/governance.lock` (gv-01 schema) records `blueprint` by id/version/sha alongside the stack-profile lock; a later atom (gv-15) merges the two locks — this atom keeps both valid.

## Acceptance criteria
- WHEN a profile lacks `governance`, `surfaces` or `detect`, THE SYSTEM SHALL fail validation naming the profile and block (control: the eight shipped profiles validate).
- WHEN `detect` is evaluated over a fixture tree for each of the eight profiles, THE SYSTEM SHALL rank the matching profile first with confidence ≥ threshold, and SHALL rank nothing above threshold on an empty tree.
- WHEN `claude-plugin` is evaluated over this repository's own tree, THE SYSTEM SHALL select it (dogfood control).
- THE SYSTEM SHALL keep every existing stack-profile test green and the lock digest reproducible.

## Done when
- test:tests/test_blueprints.py
- file:packs/stack-profiles/claude-plugin/stack-profile.yaml
- file:packs/stack-profiles/cli-tool/stack-profile.yaml
- file:packs/stack-profiles/service-event-driven/stack-profile.yaml
- file:packs/stack-profiles/data-pipeline/stack-profile.yaml

## Escalate when
- no-consensus-after-research

## Out of scope
- The selection UX (gv-15); kit content (gv-04..07).

## Scope (write boundary)
allowed_paths:
  - packs/
  - schema/stack-profile.schema.json
  - schema/app-stack-blueprint.schema.json
  - scripts/foundry_blueprint.py
  - scripts/foundry-stack-profile.py
  - tests/test_blueprints.py
  - tests/test_stack_profile*.py
  - tests/fixtures/blueprints/
  - docs/standing-versions.md
denied_paths: []

## Verification
- pytest green incl. existing stack-profile suites; `foundry-stack-profile.py validate` over all eight; standing-versions pins carry research timestamps.

## Merge
agent-merges-on-green

## Amendments
| date | what changed | why reality required it | auth_seq |
|---|---|---|---|
| 2026-11-04 | allowed_paths adds `tests/test_release.py`, `tests/test_python_stack_profile.py`, `tests/test_stack_profile_lock_create.py` (synthetic fixture profiles gained the now-required blocks); `docs/standing-versions.md` dropped (does not exist in this repo — per-pack `standing-versions/RESEARCH.md` carries the research trail instead); the loader refuses duplicate top-level keys. | the schema made `governance`/`surfaces`/`detect` required, so every synthetic profile fixture had to carry them; the review found the four retrofitted profiles carried the new blocks twice and PyYAML keeps the last silently. | 1 |
