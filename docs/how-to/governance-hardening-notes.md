# Governance hardening notes

What the hardening pass changed and, for each control, whether it is a boundary or a visibility control. Every change
has a test in `tests/test_hardening_gv20.py`. The operator's own test pass stays the terminal step of a delivery
(CLAUDE.md, "Delivery sign-off"); coverage drawn from it below is a practice, not a control.

| Area | Change | Kind |
|---|---|---|
| Ticket trust | `author_trusted` never skips the self-identity rule because `GITHUB_ACTIONS=true`. Under CI with no `--agent-login` it refuses ("identity lookup failed"). `governance-trace` passes the `github_scope.agent_login` declared on the base ref plus the workflow actor, never the actor alone. A login must match `[A-Za-z0-9][A-Za-z0-9-]*`. | control (fail closed) |
| `foundry_prod_step.py` | The AFTER/STOP retry looks for the same `corr` and kind first and posts nothing twice. SIGHUP and SIGQUIT end the command like SIGTERM. Signals are masked from before the command starts until it is bound, so none lands in the gap. Cleanup is bounded by `messaging.cleanup_timeout_s` (default two minutes); past it STOP says "cleanup timed out". The command runs without a controlling terminal: **non-interactive commands only** (a `sudo` or `ssh` prompt fails). | control |
| Paper guard (Bash) | Reads `cd X && ...`, `-t` / `--target-directory`, `cp -r` / `rsync -a` into a directory that is or holds CI paths, `rm -rf tests` / `*` / `./`, a shell write to `package.json`, and `.worktrees/<x>/.github/**` plus nested clones' workflows. | **visibility control**: it reads command text, never a shell. Still unseen: `env -i`, `sudo -u`, `git apply`, `patch`, `python -c open().write`, process substitution, paths built by expansion. |
| Shell shape | Leading redirections and ANSI-C quoting no longer hide a verb; a trailing `2>/dev/null` is not a delete target; `bash -c "$CMD"`, `cat s.py \| python3` and `perl`/`ruby`/`node -e` are refused as packed scripts; `git clean`, `rsync --delete`, `truncate` join the delete verbs; the scratch fallback admits only this session's scratchpad (the payload `session_id`). | **visibility control** (a lint over command text) |
| Secret-read guard | An option value may sit between a verb's sub-words; `dd if=`; whole-home recursive reads (`grep -r X ~`, `tar czf x ~`); `/proc/*/environ` in every spelling. | **visibility control**; the boundary is IAM (no read permission on the identity) |
| Claims | `gh pr new`, `gh pr ready` and `gh api repos/<o>/<r>/pulls` (POST, explicit or implied) need a live claim like a non-draft `gh pr create`. | control for the routes it names; other routes (aliases, GraphQL) are not modelled |
| Governance lock | The lock carries a sha256 per installed snippet; `check` reports `snippet-tampered:<path>`. `discover --out` writes only `.foundry/facts.json`. The leak guard folds dashes, Cyrillic/Greek look-alikes, fullwidth forms, combining marks and non-ASCII digits and reports `confusable-rule-id`; `governance//local/` is `governance/local/`. `--pr` snapshots earlier managed files before switching branch (an undo restores a missing one) and lists the managed files in the PR body. | control for the lock; the leak guard is a visibility control |
| Message log | Reads and pushes run with the stock `uploadpack`/`receivepack` (command-line option, because git keeps the first config value) and no credential helper; the porcelain diff has `--no-ext-diff --no-textconv`. The allow hook asks when its own environment carries `FOUNDRY_MESSAGE_REMOTE`. `malformed[]` is capped at the source and filtered by recipient. `audit` compares every commit it has seen with the tip and flags `history-rewritten` when an earlier line was removed or the history is no longer an ancestor. | control |
| Research note | The `ruled` path checks the ownership marker (first line) before it writes a decision file, like the `escalate` path. | control |
| Chat mirror | `.from`, `.corr` and `.body` are entity-escaped and `@` becomes `(at)` before they reach the webhook. | control |

## Known limits

- The history check sees only commits this clone has already audited or read; a rewrite made before the first audit is not seen.
- `ls-remote` failing is still read as "branch absent" by `--pr`.
- The hooks run without `-I`; the harness starts them, so the shebang line is not changed here.
