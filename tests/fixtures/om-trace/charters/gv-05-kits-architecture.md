# Charter: governance kits — `architecture`, `stack`, `contracts`

**Date:** 2026-11-04 · **Mode:** noninteractive · **Repo:** agentic-foundry · **Base:** release/2.0.0

## Goal
Author three kits per design §2.2, §2.3, §2.5 on gv-01's schemas. Grounding: ArchUnit/dependency-cruiser/import-linter/Nx boundaries + frozen baselines, MADR with a `confirmation.check_id` binding (our addition), C4/Structurizr, Zalando/AIP numbered API rules + Spectral, buf/oasdiff breaking gates, AsyncAPI/schema registry, ODCS, Pact can-i-deploy; Renovate presets, Scorecard pinning (architecture sweep).

- `architecture`: FG-ARCH-001.. — layers + allowed direction (binds to the stack profile's `architecture.allowed_dependencies`), cycle freedom, external allow/deny, model-as-code, ADR format + lifecycle + `confirmation.check_id`, fitness functions in CI with a baseline file. `checks/`: per-ecosystem bindings table (python → import-linter; ts/js → dependency-cruiser; java → ArchUnit; monorepo → Nx) with config templates and the baseline file shape; "no check for this ecosystem" is a reported state, never a silent pass. `templates/docs/decisions/0000-template.md`.
- `stack`: FG-STK-001.. — approved stack from the profile, every version pinned (the workspace's standing-versions discipline generalised: matrix + lock + research timestamp), dependency-update preset by reference, "adding a dependency names the rejected alternative". `checks/`: Scorecard Pinned-Dependencies mapping, a conftest rego for SHA-pinned actions and digest-pinned images (port the workspace's `standing-versions.pinning.rego` with the product literals removed), Renovate preset template.
- `contracts`: FG-API-/FG-EVT-/FG-DATA- — style guide by reference + Spectral ruleset, breaking-change gate against main, AsyncAPI + compatibility mode, ODCS contract, consumer-driven contract gate where declared. `checks/`: Spectral ruleset template, oasdiff/buf CI snippets.

## Acceptance criteria
- WHEN validated, each kit SHALL pass with zero findings and every rule SHALL bind a check or declare `review-lens`/`none`.
- WHEN a blueprint names an ecosystem the architecture kit has no binding for, `render` SHALL print `no-check:<ecosystem>` for each tool-bound ARCH rule (control: python renders import-linter).
- WHEN an ADR fixture lacks `status` or reuses a number, the ADR lint shipped in `checks/` SHALL fail naming the file.
- THE SYSTEM SHALL carry no product or workspace literal in the ported rego (planted-literal control).

## Done when
- test:tests/test_kit_architecture.py
- file:governance/kits/architecture/rules.yaml
- file:governance/kits/stack/rules.yaml
- file:governance/kits/contracts/rules.yaml

## Escalate when
- no-consensus-after-research

## Out of scope
- Blueprint `architecture:` blocks (gv-08); intake detection (gv-15).

## Scope (write boundary)
allowed_paths:
  - governance/kits/architecture/
  - governance/kits/stack/
  - governance/kits/contracts/
  - tests/test_kit_architecture.py
  - tests/fixtures/governance/
denied_paths: []

## Verification
- pytest green; validate clean; rego evaluated with conftest in the test if available, else the test skips with a named reason (never a vacuous pass).

## Merge
agent-merges-on-green

## Amendments
| date | what changed | why reality required it | auth_seq |
|---|---|---|---|
