# Charter: intake and upgrade — discover, select, declare, apply, baseline, upgrade, no upstream leak

**Date:** 2026-11-04 · **Mode:** noninteractive · **Repo:** agentic-foundry · **Base:** release/2.0.0

## Goal
Design §5.1, §5.5, §5.6. Grounding: Linguist/buildpacks/Renovate ordered file-signal detection, Copier/cruft three-way update with an answers file, never-overwrite sets, brownfield onboarding by PR, catalog analyze-location (kits sweep). Negative finding honoured: facts → kit selection has no prior art, so selection is a declared signature table + one-word confirmation, never an inference.

- `foundry_governance.py` gains: `discover` (ordered detectors over manifests, lockfiles, CI, IaC, containers, dependency names incl. auth-library heuristics → `facts.json`, every detector reports found/not-found/could-not-look), `select` (facts × every blueprint's `detect` → ranked candidates with evidence; an LLM proposal is a labelled suggestion only; refuses to proceed without a one-word confirmation argument `--confirm <id>`), `declare` (ASVS level, data classification, trust boundaries, surfaces; written to the lock), `apply` (writes `.foundry/governance.lock`, scaffolds `governance/local/` with `README`, `exceptions.yaml`, `baselines/`, `adr/`, installs the selected kits' CI/hook snippets from templates, renders, opens a PR — brownfield by PR, never regeneration), `baseline` (per check), `upgrade` (regenerate the old and the new kit versions, re-apply the adopter diff three-way, inline conflict markers, refuse on a dirty tree, apply `migrations/` id renames, bump the lock, PR), `check` extended with lock-vs-plugin drift.
- `skills/init/SKILL.md` and `skills/intake/SKILL.md` (`intake existing`) run the five steps; `cli/src/update.mjs` runs `governance upgrade` after the plugin refresh and names `/foundry:post-upgrade`, whose skill reviews the PR; `.foundry/governance.lock` and `governance/local/**` are in the updater's never-overwrite set (`cli/path-classes.json`); the old `stack-profile.lock` is absorbed by the governance lock with a one-time migration.
- Leak guard: `skills/upstream-submit` and `scripts/foundry-prepublication-leak-scan.py` refuse any file under `governance/local/`, any `<ORG>-` id, any path the lock marks adopter-owned, and any product literal (generalising `foundry_blueprint.py`'s HOW/WHAT guard).

## Acceptance criteria
- WHEN `discover` runs over each of the eight blueprint fixture trees, `select` SHALL rank the matching blueprint first with evidence; over an empty tree it SHALL rank none above threshold and say so.
- WHEN `select` is run without `--confirm`, THE SYSTEM SHALL print candidates and exit 3 (decision needed), writing nothing.
- WHEN `apply` runs on a fixture repo, THE SYSTEM SHALL write the lock and the adopter layer, install the snippets, and produce a reviewable diff; running it twice SHALL be idempotent. (rule G1)
- WHEN `upgrade` runs with an adopter edit inside a kit-templated file, THE SYSTEM SHALL preserve the edit or surface a conflict marker, never clobber; with a dirty tree it SHALL refuse.
- WHEN a planted `<ORG>-` id or a `governance/local/` path is included in an upstream submission, the leak scan SHALL refuse (control: an `FG-` rule passes).
- AC-OM-1 WHEN a fresh workspace has no lock and no policy file, THE SYSTEM SHALL enforce every floor and default every tunable (end-to-end fixture).

## Done when
- test:tests/test_governance_intake.py
- test:tests/test_governance_upgrade.py
- test:cli/test/governance-upgrade.test.mjs
- file:docs/how-to/governance-intake-and-upgrade.md

## Escalate when
- no-consensus-after-research

## Out of scope
- New blueprints; the handbook guides (gv-18).

## Scope (write boundary)
allowed_paths:
  - scripts/foundry_governance.py
  - skills/init/SKILL.md
  - skills/intake/SKILL.md
  - skills/post-upgrade/SKILL.md
  - skills/upgrade/SKILL.md
  - skills/relock/SKILL.md
  - skills/upstream-submit/
  - scripts/foundry-prepublication-leak-scan.py
  - scripts/foundry-stack-profile.py
  - cli/src/update.mjs
  - cli/src/reconcile.mjs
  - cli/src/governanceUpgrade.mjs
  - cli/path-classes.json
  - cli/test/governance-upgrade.test.mjs
  - tests/test_governance_intake.py
  - tests/test_governance_upgrade.py
  - tests/fixtures/governance/
  - docs/how-to/governance-intake-and-upgrade.md
  - docs/permission-floor.json
  - cli/permission-floor.json
  - tests/test_permission_floor_map.py
  - tests/fixtures/floor-drift-corpus.json
  - hooks/foundry-plugin-scripts-allow.py
denied_paths: []

## Verification
- python + cli suites green; a dogfood dry run of `discover`+`select` over this plugin's own tree selects `claude-plugin`.

## Merge
agent-merges-on-green

## Amendments
| date | what changed | why reality required it | auth_seq |
|---|---|---|---|
| 2026-11-04 | Goal adds: `render` and `applicable` gain `--level N` and `--surfaces <list>` filters (from the lock's per-kit level and `declarations.surfaces`); `applies_to.blueprints` entries are blueprint ids OR surface tags (`ui`, `api`, `cli`, `workflows`, `mcp`, `goal-agents`), stated in the rule schema description; `applicable` extends FG-SEC-032's globs with the lock's `declarations.trust_boundaries[]`. | gv-04/gv-07 reviews: the level-1⊂level-3 and ux-omission acceptance criteria could only be asserted on data shape because gv-01's resolver has no such filters. | 1 |
| 2026-11-05 | Scope adds `docs/README.md` (index link), `docs/VERBS-QUICK-REF.md` (one row), the root `README.md` skill-count claim, `tests/test_governance_core.py` (admit `subprocess` in foundry_governance.py: the intake verbs run local git and one gh pr create), `tests/test_bootstrap_cli.py` (new spawn site), `tests/test_init_slimming.py` (digest), `tests/test_doc_claims.py` (skill-count token). Parked for gv-19: `scripts/foundry-runtime.gitignore` + `cli/templates/gitignore.tmpl` un-ignore `.foundry/governance.lock` (apply --pr works around it with `git add -f`); `schema/governance-rule.schema.json` surface-tag wording; `check` kit sha drift at unchanged version; stack-profile.lock retirement from verify/doctor. | base tests require the index/quick-ref/count edits whenever a skill and how-to ship; the remaining items are outside this atom's paths. | 1 |
| 2026-11-05 (b) | Scope adds `scripts/foundry-runtime.gitignore`, `cli/templates/gitignore.tmpl` (un-ignore the governance lock), `schema/governance-lock.schema.json` (blueprint.id pattern). The updater opens no PR without an explicit opt-in; conflicted CI/hook files are never committed with markers. | the reviews of #303: silent-write and consent findings. | 1 |
