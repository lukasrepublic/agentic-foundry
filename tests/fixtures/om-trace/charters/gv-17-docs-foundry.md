# Charter: foundry docs — governance guide, operating-model guide, how-tos, name census, CHANGELOG

**Date:** 2026-11-04 · **Mode:** noninteractive · **Repo:** agentic-foundry · **Base:** release/2.0.0

## Goal
Port prompt PR-8 plus the governance pages. Short pages that reference live files (consensus: governance text short, live references, no copies).

- `docs/governance.md`: vocabulary, the sixteen categories with their kits, floor vs tunable table, the per-ticket sections, the review lenses, exceptions and baselines, upgrade.
- `docs/operating-model-policy.md`: every policy key, floor/tunable, the primitive that enforces it, plus the generated traceability table (`foundry_om_trace.py --markdown`).
- `docs/how-to/`: `scale-to-zero-privileged-jobs.md` (C3 pattern + template), `ci-budgets-are-tools.md` (C2), `evidence-baseline-rule.md` (G1), `operator-handoff-files.md` (E3: `report.handoff_dir`, durable instructions stay in the repo as Markdown, generated PDFs beside the hand-off files; no PDF skill in 2.0 — stated).
- `context/operating-model.md` one-pager: add "The map" paragraph (governance) beside "The floor"; the "deliberately absent" list updated honestly.
- CI `selftests`: AC-OM-9 name census over the change set with a planted-name control (extends the leak gate).
- CHANGELOG 2.0.0: governance, one mode, operating-model policy, shared message log, hooks; every removed or changed behaviour names its replacement.

## Acceptance criteria
- WHEN `tests/test_doc_claims.py` runs, every command and file the new pages name SHALL exist at the head.
- AC-OM-9 WHEN the census runs over the programme's change set, THE SYSTEM SHALL find no workspace, cloud account, repository or person name; a planted name in a fixture SHALL be caught.
- THE SYSTEM SHALL regenerate the traceability table in CI and fail when it differs from the committed page.

## Done when
- file:docs/governance.md
- file:docs/operating-model-policy.md
- file:docs/how-to/scale-to-zero-privileged-jobs.md
- test:tests/test_doc_claims.py

## Escalate when
- no-consensus-after-research

## Out of scope
- Handbook (gv-18).

## Scope (write boundary)
allowed_paths:
  - docs/
  - context/operating-model.md
  - README.md
  - CHANGELOG.md
  - .github/workflows/ci.yml
  - tests/test_doc_claims.py
  - tests/test_name_census.py
  - tests/fixtures/census/
denied_paths: []

## Verification
- doc-claims test green; census green with control; CI SHA pins unchanged or re-pinned per standing-versions.

## Merge
agent-merges-on-green

## Amendments
| date | what changed | why reality required it | auth_seq |
|---|---|---|---|
| 2026-11-05 | Scope adds `scripts/foundry_om_trace.py` (a `--markdown` renderer for the traceability table, if absent) and the state.yaml doc notes routed here (prod-step tty note, never-persist-allow for prod-step, message-log tamper-evidence wording, queue-land's orphan branch, governance-trace without a token, declarations-pending scope, tzdata, the `--subagent-stop` CHANGELOG line). | the gv-13/gv-15/gv-16/gv-09 reviews filed these as documentation residuals. | 1 |
