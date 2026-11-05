# Charter: hardening — close the must-fix residuals the wave-2/3 security reviews filed

**Date:** 2026-11-05 · **Mode:** noninteractive · **Repo:** agentic-foundry · **Base:** release/2.0.0 · **Security lane:** yes (hooks, ticket store, guards)

## Goal
This atom closes review-filed residuals; see the programme state.

## Acceptance criteria
- WHEN `GITHUB_ACTIONS=true` and no `--agent-login` is given, `author_trusted` SHALL return untrusted naming "identity lookup failed"; with `--agent-login <actor>` a registered-operator author SHALL pass and the actor-authored ticket SHALL be refused.
- WHEN a wrapped command's AFTER push lands but read-back is interrupted, the retry SHALL post no second record for that `corr` (audit shows one pair).
- WHEN the paper guard sees `cd .github/workflows && sed -i … ci.yml` or `cp -r x .github/`, it SHALL refuse; `cp -r x docs/` SHALL be admitted.
- WHEN the shape hook sees `</dev/null rm -rf /x` or `$'rm' -rf /x`, it SHALL refuse; `rm -rf <scratch>/x 2>/dev/null` SHALL be admitted.
- WHEN a kit snippet installed by `apply` is edited by hand, `check` SHALL report `snippet-tampered:<path>`.
- WHEN a planted `FG‑SEC‑001` with U+2011 hyphens or a Cyrillic letter appears in a submission, the leak guard SHALL refuse; a plain `FG-SEC-001` SHALL pass.
- WHEN a record earlier than the cursor is rewritten on the log branch, `audit` SHALL flag `history-rewritten`.

## Done when
- test:tests/test_hardening_gv20.py
- file:docs/how-to/governance-hardening-notes.md

## Escalate when
- security-widening
- no-consensus-after-research

## Out of scope
- New verbs or kits; the dogfood run (gv-19).

## Scope (write boundary)
allowed_paths:
  - scripts/foundry-ticket.py
  - scripts/foundry_ticket_store.py
  - scripts/foundry_prod_step.py
  - scripts/foundry_message_log.py
  - scripts/foundry_governance.py
  - scripts/foundry_shell_scan.py
  - scripts/foundry_research_note.py
  - hooks/foundry-paper-guard.py
  - hooks/foundry-shell-shape.py
  - hooks/foundry-secret-read-guard.py
  - hooks/foundry-git-discipline.sh
  - hooks/foundry-plugin-scripts-allow.py
  - governance/kits/delivery/checks/
  - schema/operating-model.schema.json
  - schema/governance-lock.schema.json
  - scripts/foundry_operating_model.py
  - cli/templates/operating-model.yaml.tmpl
  - tests/test_hardening_gv20.py
  - tests/test_gv16_round*.py
  - tests/test_shell_shape_hook.py
  - tests/test_secret_read_guard.py
  - tests/test_message_log.py
  - tests/test_prod_step.py
  - tests/test_governance_intake.py
  - tests/test_governance_upgrade.py
  - tests/test_paper_guard*.py
  - tests/test_claims_expiry.py
  - tests/test_research_note.py
  - tests/fixtures/release/cut-release-reconcile-sweep.yaml
  - docs/permission-floor.json
  - cli/permission-floor.json
  - tests/test_permission_floor_map.py
  - tests/fixtures/floor-drift-corpus.json
  - docs/how-to/governance-hardening-notes.md
  - docs/README.md
denied_paths:
  - .github/workflows/

## Verification
- pytest green; doctor GREEN; hook digests re-pinned; fresh-context security-reviewer pass; `security-reviewed:<head12>-924f6d00` label.

## Merge
agent-merges-on-green

## Amendments
| date | what changed | why reality required it | auth_seq |
|---|---|---|---|
