# Charter: dogfood — run governance intake on all three repositories and fix the drift

**Date:** 2026-11-04 · **Mode:** noninteractive · **Repo:** agentic-foundry (plus agentic-handbook and the workspace) · **Base:** release/2.0.0

## Goal
Prove the machinery on itself before the cut.

- agentic-foundry: `discover` → `select --confirm claude-plugin` → `declare` → `apply` → `baseline`; commit the lock and `governance/local/` (upstream's own private layer stays empty by construction — a test asserts it); fix every finding the rendered rules raise against the plugin's own tree or baseline it with a named reason and expiry; `check` clean.
- agentic-handbook: the same with the docs-only blueprint subset (`cli-tool` or a `docs-site` note if none fits — report honestly).
- The workspace (private): intake run by the operator's session; private rules go under `governance/local/` with the `<ORG>-` namespace; the leak gate confirmed by a planted-id control.
- Record the three results in the programme state; CHANGELOG line.

## Acceptance criteria
- WHEN `foundry_governance.py check` runs at the head of each repository, THE SYSTEM SHALL report zero drift and zero expired exceptions.
- WHEN the plugin's own CI runs, the governance-trace job SHALL pass on a PR that cites ids and fail on a fixture PR that does not (control).
- THE SYSTEM SHALL show the plugin's `governance/local/` empty and the workspace's non-empty, with every workspace id namespaced.

## Done when
- file:.foundry/governance.lock
- cli:python3 scripts/foundry_governance.py check
- test:tests/test_dogfood_self.py

## Escalate when
- no-consensus-after-research

## Out of scope
- The release cut, tag, publish (operator).

## Scope (write boundary)
allowed_paths:
  - .foundry/governance.lock
  - governance/local/
  - governance/rendered/
  - tests/test_dogfood_self.py
  - CHANGELOG.md
  - docs/decisions/
denied_paths: []

## Verification
- `check` clean on all three; CI green; the operator's own test pass remains the terminal sign-off.

## Merge
agent-merges-on-green

## Amendments
| date | what changed | why reality required it | auth_seq |
|---|---|---|---|
