# Charter: governance core — rule/kit/lock schemas, the resolver, the brief renderer

**Date:** 2026-11-04 · **Mode:** noninteractive · **Repo:** agentic-foundry · **Base:** release/2.0.0

## Goal
Ship the substrate every governance kit, blueprint and brief stands on. Design: `intake/governance-2-0/design.md` §1, §3, §4, §5.1–5.2 (workspace). Prior art: Spec-Kit constitution + template overrides, Kiro steering, MADR, ASVS/Scorecard ID schemes, Copier answers file, Renovate/ESLint preset layering (`.foundry/research/wf_b9558152-11b-governance-taxonomy.md`).

- `schema/governance-rule.schema.json` — one rule: `id` (`^(FG|[A-Z][A-Z0-9]{1,15})-[A-Z]{2,5}-[0-9]{3}$`), `title`, `statement` (RFC 2119), `category`, `level` (1–3), `floor` (bool), `status` (active | superseded-by:<id> | retired), `applies_to` {paths[], blueprints[]}, `check` {kind: hook|ci|lint|review-lens|scorecard|declared|none, ref}, `exception` {allowed, requires: [reason, owner, expires]}, `sources[]`.
- `schema/governance-kit.schema.json` — `kit`, `version` (semver), `category`, `rules[]`, `checks[]` (shipped snippets by path), `templates[]`, `migrations[]` ({from_id, to_id, since}).
- `schema/governance-lock.schema.json` — `.foundry/governance.lock`: `blueprint` {id, version, sha256}, `kits[]` {id, version, sha256, level}, `declarations` {asvs_level, data_classification[], trust_boundaries[], surfaces[]}, `facts_sha256`, `intake_at`, `plugin_version`. Machine-owned.
- `governance/kits/<category>/{kit.yaml,rules.yaml,README.md,checks/,templates/}` directory convention inside the plugin; this atom ships the `conduct` kit (CND-001..004 from design §2.16) and the `exceptions` kit (EXC-001..004) as the two proofs of the shape; every other kit is its own atom.
- `scripts/foundry_governance.py` — pure core + thin CLI: `load` (plugin kits at the locked versions + `governance/local/`), `validate` (schemas; floor rules present and not disabled; adopter ids namespaced and never `FG-`; no duplicate id; superseded ids resolve), `render` (writes `governance/rendered/<category>.md` — effective rules, short, references live files), `applicable --paths <p>…` (rule ids for a diff, path-scoped), `check` (lock vs installed kit versions, rendered vs sources, expired exceptions → exit 1 listing each), `exceptions` (list/validate `governance/local/exceptions.yaml`), `baseline` (write/refresh `governance/local/baselines/<check>.json` from a check's current findings). No network. Exit 0 ok / 1 findings / 2 refused.
- `scripts/foundry_brief.py render --ticket <json> --paths <p>…` — the shared brief preamble: always-on core (product charter if present, conduct rules), the blueprint's level-1 rules, the ticket's cited ids with statements inlined, path-scoped rules, the E2 style block when `communication.style` ≠ off (read through gv-03's policy loader when present; default on), hygiene + shell-shape rules. Output respects the dispatch inline cap (`foundry_dispatch_lint.py`).
- Paper guard: `.foundry/research/`, `.foundry/decisions/`, `governance/local/` and `governance/rendered/` are admitted as rule-mandated paper (names, not a free pass).

## Acceptance criteria
- WHEN a rule document violates the id pattern, lacks a check binding, or sets `floor: true` with `exception.allowed: true`, THE SYSTEM SHALL refuse validation naming the rule id and the field.
- WHEN an adopter rule under `governance/local/` carries the `FG-` namespace, THE SYSTEM SHALL refuse with `adopter-rule-in-upstream-namespace:<id>`.
- WHEN `render` runs, THE SYSTEM SHALL produce one file per category whose rule set equals kits ⊕ local with local overrides applied only where the upstream rule is not `floor`; a floor override SHALL be refused naming the id.
- WHEN `applicable --paths` is given a path, THE SYSTEM SHALL return exactly the active rules whose `applies_to.paths` glob matches it (control: an unmatched path returns only `**` rules).
- WHEN an exception has passed `expires`, `check` SHALL exit 1 naming it; an unexpired one SHALL be reported as a skip, never counted as a pass.
- WHEN `foundry_brief.py render` runs with `communication.style: off`, the output SHALL contain no E2 style text; with the default it SHALL contain it (AC-OM-11 half).
- THE SYSTEM SHALL make no network call in any verb (stubbed subprocess records zero `gh`/`git fetch`).

## Done when
- test:tests/test_governance_core.py
- test:tests/test_brief_render.py
- file:scripts/foundry_governance.py
- file:scripts/foundry_brief.py
- file:schema/governance-rule.schema.json
- file:governance/kits/conduct/rules.yaml

## Escalate when
- no-consensus-after-research

## Out of scope
- Kit content beyond `conduct` and `exceptions` (gv-04..07); blueprints (gv-08); intake/upgrade (gv-15); review lenses (gv-16).

## Scope (write boundary)
allowed_paths:
  - schema/governance-rule.schema.json
  - schema/governance-kit.schema.json
  - schema/governance-lock.schema.json
  - governance/README.md
  - governance/kits/conduct/
  - governance/kits/exceptions/
  - scripts/foundry_governance.py
  - scripts/foundry_brief.py
  - hooks/foundry-paper-guard.py
  - tests/test_governance_core.py
  - tests/test_brief_render.py
  - tests/fixtures/governance/
  - docs/permission-floor.json
  - cli/permission-floor.json
  - tests/test_permission_floor_map.py
  - tests/fixtures/floor-drift-corpus.json
  - hooks/foundry-plugin-scripts-allow.py
  - README.md
  - docs/VERBS-QUICK-REF.md
  - tests/test_doc_claims.py
denied_paths: []

## Verification
- `python3 -m pytest tests/test_governance_core.py tests/test_brief_render.py -q` green; `python3 -m pytest tests/ -q` no regression; doctor GREEN; permission-floor digest re-pinned; no the retired token substring; no private names (grep census).

## Merge
agent-merges-on-green

## Amendments
| date | what changed | why reality required it | auth_seq |
|---|---|---|---|
