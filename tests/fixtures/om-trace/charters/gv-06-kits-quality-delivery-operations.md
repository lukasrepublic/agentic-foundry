# Charter: governance kits — `quality`, `delivery`, `operations`

**Date:** 2026-11-04 · **Mode:** noninteractive · **Repo:** agentic-foundry · **Base:** release/2.0.0

## Goal
Author three kits per design §2.6–§2.8 on gv-01's schemas. Grounding: Google review practices, flaky quarantine (GitLab/Datadog: not skip, owner + issue + expiry, retry budget 1), conventional commits + SemVer automation, CODEOWNERS, Well-Architected pillar ids, OpenSLO, error-budget policy, announce-before/report-after with a correlation id (`wf_7ca7ea73-8bd`), pint alert hygiene, Piranha-style flag cleanup (operational sweep).

- `quality`: FG-QA- — one separate-context review per PR with the conformance lens (`floor`), style delegated to linters, test strategy per blueprint with a coverage floor number, flaky quarantine record shape + retry budget, Done-means re-run on merged main (`floor`, binds gv-13's verify-after-merge). `checks/`: quarantine record schema, commitlint-style review checklist, coverage CI snippet.
- `delivery`: FG-DLV- — branch discipline (`floor`, binds git-discipline), conventional commits + bump derivation, merge-queue discipline (`floor`, binds gv-10), coarse PRs + merge plan, full suite only when required, release cadence floor, feature-flag cleanup expectation, CODEOWNERS for governed paths. `checks/`: commitlint config, release-please config template, CODEOWNERS template.
- `operations`: FG-OPS- — pillar checklist mapped to repo facts (scorecard, report not gate), SLO/SLI as code + error-budget policy document, change announce/report with correlation id (`floor`, binds gv-09 kinds + gv-13 prod-step), readiness checklist per blueprint, runbook linked from every alert, progressive rollout where the blueprint deploys. `checks/`: OpenSLO validate snippet, pint snippet, readiness checklist template.

## Acceptance criteria
- WHEN validated, each kit SHALL pass with zero findings; floor rules SHALL name the shipped primitive in `check.ref`.
- WHEN a quarantine record lacks owner, issue or expiry, the shipped schema check SHALL fail naming the field; an expired record SHALL be reported as expired.
- WHEN `applicable` is asked for `.github/workflows/**`, THE SYSTEM SHALL return the delivery rule that forbids editing CI to pass (binds gv-10's paper-guard class).

## Done when
- test:tests/test_kit_quality_delivery_operations.py
- file:governance/kits/quality/rules.yaml
- file:governance/kits/delivery/rules.yaml
- file:governance/kits/operations/rules.yaml

## Escalate when
- no-consensus-after-research

## Out of scope
- The primitives the floors bind to (gv-09, gv-10, gv-13).

## Scope (write boundary)
allowed_paths:
  - governance/kits/quality/
  - governance/kits/delivery/
  - governance/kits/operations/
  - tests/test_kit_quality_delivery_operations.py
  - tests/fixtures/governance/
denied_paths: []

## Verification
- pytest green; validate clean; no private names.

## Merge
agent-merges-on-green

## Amendments
| date | what changed | why reality required it | auth_seq |
|---|---|---|---|
