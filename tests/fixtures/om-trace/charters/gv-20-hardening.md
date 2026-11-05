# Charter: hardening — close the must-fix residuals the wave-2/3 security reviews filed

**Date:** 2026-11-05 · **Mode:** noninteractive · **Repo:** agentic-foundry · **Base:** release/2.0.0 · **Security lane:** yes (hooks, ticket store, guards)

## Goal
Every item below was filed in `.foundry/releases/governance-2-0/state.yaml` `open_risks` by a fresh-context security review whose PR had spent its round budget. None is hypothetical; each names a file:line in the merged tree. This atom closes them before the cut, each with a failing test and a control, in the order listed.

1. **Trust carve-out (gv-16, must-fix first).** `scripts/foundry-ticket.py` `author_trusted`: the self-identity rule is skipped whenever `GITHUB_ACTIONS=true` and no `agent_login` is given, for every caller (`done_trust` included). Fix: under CI with no login, REFUSE ("identity lookup failed"); the carve-out exists only through governance-trace's explicit `--agent-login`; validate the login against `[A-Za-z0-9][A-Za-z0-9-]*`; the trace compares against a declared agent account from the base ref (`github_scope.agent_login` if present) with `github.actor` as an addition, never alone.
2. **prod-step (gv-13).** `scripts/foundry_prod_step.py`: idempotent AFTER/STOP retry by `corr` (never a second record when the first push landed); SIGHUP/SIGQUIT handled like SIGTERM; bounded cleanup wait (policy `messaging.cleanup_timeout_s`, default 120) then STOP with "cleanup timed out"; the Popen/`proc.wait` signal gap closed (signals masked until `proc` is bound).
3. **Paper guard (gv-16).** `hooks/foundry-paper-guard.py`: `cd … &&` tracking (resolve later targets against the `cd` operand), `-t`/`--target-directory`, `cp -r dir <ci-dir>/`, `rsync`, `rm -rf tests`/`*`/`./`, `echo … > package.json`; classify `.worktrees/<x>/.github/**` and nested product clones' `.github/workflows/` as CI when under CLAUDE_PROJECT_DIR; keep the docstring's "visibility control" statement.
4. **Shell shape + secret guard (gv-11).** `scripts/foundry_shell_scan.py` splits redirections and models ANSI-C quoting; a trailing redirect is not a delete target; `bash -c "$CMD"`, `cat s.py | python3`, `perl/ruby/node -e` are refused as packed scripts; `DELETE_VERBS` gains `git clean`, `rsync --delete`, `truncate`, `shred`, `unlink`; the scratch fallback admits only THIS session's scratchpad. `hooks/foundry-secret-read-guard.py`: option-tolerant subsequence match, `dd if=…`, whole-home reads (`grep -r … ~`, `tar … ~`), `/proc/*/environ`.
5. **Claims (gv-11).** `scripts/foundry-ticket.py`/`foundry_ticket_store.py`: `gh pr create --draft` + `gh pr ready`, `gh pr new`, and the REST pulls POST route are gated by clause (m) as a non-draft create.
6. **Governance lock + leak guard (gv-15).** `scripts/foundry_governance.py`: per-file sha256 of every installed snippet in the lock, `check` reports tampering; `discover --out` limited to `.foundry/facts.json`; leak guard catches confusables (NFKC + Cf strip + confusable-hyphen fold + Cyrillic/fullwidth detection) and `governance//local/`; the `--pr` journal snapshots pre-existing managed files before the branch switch and lists managed files in the PR body.
7. **Shared message log (gv-09).** `scripts/foundry_message_log.py`: `-c remote.<name>.uploadpack=git-upload-pack -c remote.<name>.receivepack=git-receive-pack -c credential.helper=` and `--no-ext-diff --no-textconv` on the porcelain diff; the allow hook asks when `os.environ` carries `FOUNDRY_MESSAGE_REMOTE`; `malformed[]` capped at source and filtered by recipient; `_USERINFO_RE` stops at the first `@` after `//`; `audit` flags a cursor..tip rewrite of earlier lines (append-only check).
8. **gv-14 ruled path.** `scripts/foundry_research_note.py` `ruled` carries the same ownership marker check as `escalate`.

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
