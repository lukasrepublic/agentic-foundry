# Charter: governance kits — `cost`, `observability`, `data`, `compliance`, `ux`

**Date:** 2026-11-04 · **Mode:** noninteractive · **Repo:** agentic-foundry · **Base:** release/2.0.0

## Goal
Author five kits per design §2.9–§2.13 on gv-01's schemas. Grounding: FinOps (tag policies, Infracost as comment, scale-to-zero), OTel semantic conventions + Weaver registry, pint, data classification schemes (declared, fixed enum), retention as code, ORT/SPDX license policy, REUSE, SBOM, WCAG + axe (~30–40 % coverage stated as a practice), Lighthouse budgets, i18n lint (operational sweep).

- `cost`: FG-COST- — cost in every deck decision + priced ask (`floor`, binds gv-14's `priced-ask` kind), required cost tags in IaC plans, cost diff on infra PRs as comment, scale-to-zero pattern for non-prod and rare privileged jobs (C3 pattern doc + template under `templates/`).
- `observability`: FG-OBS- — OTel conventions + owner-prefixed custom names with a registry check, alert-rule hygiene, the factory's usage collection (gn-05) declared.
- `data`: FG-DATA- — classification scheme + handling matrix (declared), retention per store matched to class, privacy declarations where personal data exists.
- `compliance` (licensing and provenance only, never compliance-framework content): FG-LIC- — allowlist by category, per-package exceptions, deny unknown, SPDX headers where required, SBOM per release.
- `ux` (UI blueprints only): FG-UX- — WCAG target + axe in e2e, performance budget as a report (C2: budgets are tools, not gates), no literal strings.

## Acceptance criteria
- WHEN validated, each kit SHALL pass with zero findings.
- WHEN a blueprint without a UI surface selects kits, `render` SHALL omit `ux` entirely; with `ui` in surfaces it SHALL include it.
- WHEN a license fixture lists an unknown license, the shipped policy template SHALL deny it (control: MIT passes).
- THE SYSTEM SHALL state in the ux README that automated accessibility checks cover a minority of WCAG and the rest is a practice.

## Done when
- test:tests/test_kit_cost_obs_data_ux.py
- file:governance/kits/cost/rules.yaml
- file:governance/kits/observability/rules.yaml
- file:governance/kits/data/rules.yaml
- file:governance/kits/compliance/rules.yaml
- file:governance/kits/ux/rules.yaml

## Escalate when
- no-consensus-after-research

## Out of scope
- Any compliance-framework (SOC 2, ISO) control text — out of scope by the brief.

## Scope (write boundary)
allowed_paths:
  - governance/kits/cost/
  - governance/kits/observability/
  - governance/kits/data/
  - governance/kits/compliance/
  - governance/kits/ux/
  - tests/test_kit_cost_obs_data_ux.py
  - tests/fixtures/governance/
denied_paths: []

## Verification
- pytest green; validate clean; no private names.

## Merge
agent-merges-on-green

## Amendments
| date | what changed | why reality required it | auth_seq |
|---|---|---|---|
| 2026-11-04 | allowed_paths adds `governance/kits/contracts/` (rename of the data-contract rule `FG-DATA-001` → `FG-DCON-001`) and `tests/test_kits_validate_together.py` (every shipped kit validates together; no duplicate ids across kits); the ux surface tag `["ui"]` in `applies_to.blueprints` is a surface tag consumed by gv-15's resolver filter. | design §2.5 and §2.11 both assigned the `FG-DATA-` family; once the contracts kit (gv-05) and the data kit were on one tree the validator refused `FG-DATA-001: id: duplicate` and every brief test failed on the merge ref. Rule: a kit's ids use the kit's own category token; sub-families are a title convention. | 1 |
