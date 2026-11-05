# Charter: guard hooks — shell shape, secret-read guard, claims with expiry and the claim-required push

**Date:** 2026-11-04 · **Mode:** noninteractive · **Repo:** agentic-foundry · **Base:** release/2.0.0 · **Security lane:** yes (new PreToolUse hooks)

## Goal
Rules B5, B6, D1 of the port prompt; port plan §1.3 rulings 2–4. Grounding: Claude Code docs (Bash deny rules are not a security boundary; hooks exit 2 deny), gitleaks/push protection, agent credential boundaries, lease patterns with heartbeat and takeover (security + message-log sweeps).

- `hooks/foundry-shell-shape.py` (PreToolUse Bash): refuse `rm` whose target is outside the session scratch directory (`$CLAUDE_SCRATCHPAD` or the harness-provided path; inside it admitted); refuse packed `bash -c` / `sh -c` / `eval` / `python3 -` scripts; refuse a heredoc `foundry_shell_scan` classifies `code`, admit a `data` heredoc whose sink is a file ("write the script to a file and run it by name"); refuse `cd` as a command (use `git -C` and absolute paths); one simple command per call is advisory text in the brief, not a refusal. Exit 2 with the reason and the allowed shape. Floor (no policy off-switch); a `--strict` env for CI preflight.
- `hooks/foundry-secret-read-guard.py` (PreToolUse Bash): default forbidden verb list (cloud secret-manager reads, `kubectl get secret -o yaml|json`, vault kv get, keyvault secret show, `op read`, `gh auth token`, printing a plan body that resolves secrets) + no-print roots; `.foundry/operating-model.yaml` `secrets.forbidden_verbs[]`/`no_print_roots[]` may only ADD; `value_reads_forbidden` is const true. The docs page states that a command-text hook is not a boundary and only an IAM-level boundary prevents the read.
- Claims (gn-08 store): `expires_after` (policy `claims.expires_after`, default 2h), heartbeat on `lead renew` and `reconcile`, takeover allowed only on an expired lease and recorded, successor claim for a long chunk; `hooks/foundry-git-discipline.sh` clause (m): a `git push` of a work branch or a non-draft `gh pr create` without a live claim for the bound issue is refused (reads the claims store; fail closed on unreadable).
- `hooks/hooks.json`: wires the two new hooks and gv-12's SubagentStop reap script (this atom is the only editor of hooks.json in wave 2). Doctor `check_hooks` passes.

## Acceptance criteria
- AC-OM-5 WHEN `rm` outside scratch, a packed `bash -c` script, or a code heredoc is attempted, the shape hook SHALL refuse; a script run by name and an `rm` inside scratch SHALL be admitted (controls). (rule B6)
- WHEN a forbidden secret-read verb is attempted, the secret guard SHALL refuse naming the verb; WHEN the policy file tries to remove a default verb, `validate` SHALL refuse (`floor-rule-disabled:secrets.value_reads_forbidden` or `secrets.forbidden_verbs.removal`). (rule D1)
- WHEN a claim's heartbeat is older than `expires_after`, another session's takeover SHALL succeed and be recorded; before expiry it SHALL be refused. (rule B5)
- WHEN a work-branch push has no live claim, the push SHALL be refused with `claim-required`; with a live claim it SHALL be admitted.
- THE SYSTEM SHALL keep doctor GREEN with the new wiring and the plugin-scripts allow hook consistent.

## Done when
- test:tests/test_shell_shape_hook.py
- test:tests/test_secret_read_guard.py
- test:tests/test_claims_expiry.py
- file:hooks/foundry-shell-shape.py
- file:hooks/foundry-secret-read-guard.py

## Escalate when
- security-widening
- no-consensus-after-research

## Out of scope
- Moving the claims store onto a git branch (a later ticket once gv-09 proves the writer).

## Scope (write boundary)
allowed_paths:
  - hooks/foundry-shell-shape.py
  - hooks/foundry-secret-read-guard.py
  - hooks/foundry-git-discipline.sh
  - hooks/hooks.json
  - scripts/foundry_ticket_store.py
  - scripts/foundry-ticket.py
  - scripts/foundry_github_capabilities.py
  - scripts/foundry_leader_board.py
  - tests/test_github_scope_gate.py
  - scripts/foundry_shell_scan.py
  - schema/operating-model.schema.json
  - scripts/foundry_operating_model.py
  - tests/test_shell_shape_hook.py
  - tests/test_secret_read_guard.py
  - tests/test_claims_expiry.py
  - tests/test_owned_queue.py
  - tests/fixtures/guards/
  - docs/how-to/secrets-never-read.md
  - scripts/foundry-doctor.py
  - docs/permission-floor.json
  - cli/permission-floor.json
  - tests/test_permission_floor_map.py
  - tests/fixtures/floor-drift-corpus.json
  - hooks/foundry-plugin-scripts-allow.py
denied_paths: []

## Verification
- pytest green; doctor GREEN; fresh-context security-reviewer pass; `security-reviewed:<head12>-924f6d00` label; the plugin's own test suite still runs under the shape hook (CI uses scripts by name).

## Merge
agent-merges-on-green

## Amendments
| date | what changed | why reality required it | auth_seq |
|---|---|---|---|
| 2026-11-04 (b) | Scope adds `hooks/foundry-env-reap.sh` (a `--subagent-stop` early-exit guard so the SubagentStop entry is safe in any merge order; gv-12's label-scoped sweep replaces it), `hooks/foundry-plugin-scripts-allow.py` (METACHARS gains `{}*?[]`; lifecycle hooks `foundry-env-reap.sh`/`foundry-worktree-remove.sh` never silently allowed), `scripts/foundry_shell_scan.py` (quoted-delimiter tracking: an unquoted-delimiter heredoc body with `$(`, backtick or `${` is `code`), `CHANGELOG.md`, `docs/how-to/`, `cli/templates/operating-model.yaml.tmpl` (new schema leaves), `tests/fixtures/release/cut-release-reconcile-sweep.yaml` (hook digest). Clause (m) fails closed on a malformed project file; a session whose lease was taken over may not push. | the code + security reviews of #300 and gv-12's final review (brace-expansion smuggling past the token check; env-reap reachable as a silent `.sh`; the SubagentStop ordering hazard). | 1 |
| 2026-11-04 | Scope adds `scripts/foundry_github_capabilities.py` and `scripts/foundry_leader_board.py`; goal adds: validate `github_scope.planning_repo` and `github_scope.repos[]` in `load_scope` (`[A-Za-z0-9_.-]+`, no leading `-`/`.`, not `..`) with gate tests; run the worktree-create hook's inline python with `-I`; a non-OSError/ValueError exception in `load_scope` still yields the typed exit 2. | gv-02's security re-review (head 6811056) filed these as Risks; they belong with the guard hooks. | 1 |
