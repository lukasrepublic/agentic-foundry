# Charter: governance kits — `security` (and the `product` charter kit)

**Date:** 2026-11-04 · **Mode:** noninteractive · **Repo:** agentic-foundry · **Base:** release/2.0.0

## Goal
Author the `security` kit per design §2.4 and the small `product` kit per §2.1, on gv-01's schemas. Grounding: ASVS 5.0 ids, NIST SSDF, SLSA, OpenSSF Scorecard/Baseline, gitleaks `[extend]`, Semgrep metadata, Kyverno/conftest exceptions, Claude Code permission precedence (`.foundry/research/wf_b9558152-11b-governance-taxonomy.md`, security sweep).

- `governance/kits/security/rules.yaml`: FG-SEC-001..~030 covering level (declared), requirement subset per level (cites ASVS `v5.0.0-c.s.r`), threat model / trust boundaries / data classification (declared, fixed enum), supply chain (SLSA level, SBOM, pinned inputs, signed releases), platform hardening (rulesets, token least privilege, dangerous workflows), secrets (scanner with inherited defaults, push protection, agents never read a value — `floor: true`, check ref gv-11's hook), agent execution boundary (deny-first, sandbox, org precedence), infra policy-as-code bundle, security-review trigger paths (`floor: true`, check `review-lens`).
- `checks/`: gitleaks base config (`[extend] useDefault = true`), a Scorecard/Baseline mapping table, a Semgrep ruleset reference pinned by tag, a conftest bundle reference, CI snippet templates; each check names the rule ids it enforces.
- `templates/`: `THREAT-MODEL.md` (boundaries + classification tables), `SECURITY.md` skeleton.
- `product` kit: FG-PRD-001..003 (charter present, goals/non-goals per change, surfaces named) with `templates/PRODUCT.md`.
- Every rule carries `sources[]`; no licensed standard text is copied (ids + own summaries only).

## Acceptance criteria
- WHEN the kit is validated by `foundry_governance.py validate`, THE SYSTEM SHALL pass with zero findings; every rule SHALL bind a check or state `declared`/`review-lens`/`none` explicitly.
- WHEN a blueprint selects `security` at level 1, `render` SHALL emit only level-1 rules; at level 3, all.
- WHEN a path under a declared trust boundary is passed to `applicable`, THE SYSTEM SHALL return the security-review trigger rule.
- THE SYSTEM SHALL ship no ASVS/NIST prose beyond identifiers and one-line own summaries (a grep for licensed phrasing is part of the test).

## Done when
- test:tests/test_kit_security.py
- file:governance/kits/security/rules.yaml
- file:governance/kits/product/rules.yaml

## Escalate when
- no-consensus-after-research

## Out of scope
- The hooks the rules bind to (gv-11); the reviewer lens (gv-16); blueprint selection (gv-08).

## Scope (write boundary)
allowed_paths:
  - governance/kits/security/
  - governance/kits/product/
  - tests/test_kit_security.py
  - tests/fixtures/governance/
denied_paths: []

## Verification
- `python3 -m pytest tests/test_kit_security.py -q`; `foundry_governance.py validate` clean; no private names.

## Merge
agent-merges-on-green

## Amendments
| date | what changed | why reality required it | auth_seq |
|---|---|---|---|
