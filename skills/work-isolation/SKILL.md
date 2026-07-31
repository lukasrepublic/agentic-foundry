---
name: work-isolation
description: Worker work-isolation lifecycle over NATIVE worktree isolation (/foundry:work-isolation). Native Agent isolation:worktree creates + auto-cleans worktrees; this skill covers the foundry deltas — the additive write-jail (foundry-cwd-enforce), post-merge cleanup (foundry-work-isolation.sh), and validate/break-glass. Trigger for worker worktree init/complete/cleanup/validate or a break-glass main-clone edit record.
---

# /foundry:work-isolation

The work-isolation lifecycle — a WRAP over native primitives. Native primitives do the heavy
lifting; foundry adds only the deltas.

| action | native does | foundry delta |
|---|---|---|
| **init** | `Agent isolation:worktree` creates the worktree + sets cwd | nothing to build — use native dispatch (`/foundry:dispatch`). MULTI-REPO redirect additionally **stages the atom's spec + acceptance-contract into the worktree** and **preflights** them before the worker proceeds — see *Spec-staged-and-preflighted jail* below. |
| **validate** | native confines cwd to the worktree | `foundry-cwd-enforce.sh` write-jail: blocks Edit/Write resolving OUTSIDE the worktree (Q2 — native is cwd-isolation, not a write-jail). Worker detected via linked-worktree git check (no assignment.json needed). |
| **complete** | — | the worker cuts its PR (`gh pr create`); the merge floor decides. |
| **cleanup** | auto-cleans an UNCHANGED worktree | `foundry-work-isolation.sh cleanup <repo> <branch>` removes a MERGED worktree + local branch (post-merge, the bit native doesn't auto-do). **Worker-learnings harvest runs first** — see below. |
| **learnings** | — | **Primary:** the worker returns `learnings[]`; `/foundry:dispatch` captures them in the parent (durable, survives teardown). **Defense-in-depth:** `foundry-harvest-learnings.sh` forwards a worker's `.agent/learnings.jsonl` sidecar into `.foundry/session-learnings/` (the `/foundry:learn-distill` partition) **before** an explicit `git worktree remove` — PreToolUse(Bash) seam + the cleanup script. Fail-open; durable loss-log. |
| **break-glass** | — | record a deliberate main-clone edit reason to the fail-closed audit trail (the former `break-glass` skill, folded). |

## Procedure

- **Worker spawn / init:** use `/foundry:dispatch` (native `Agent isolation:worktree`). No `wt claim`, no queue.
- **Write-jail (automatic):** `foundry-cwd-enforce.sh` fires PreToolUse on Edit/Write/MultiEdit; in a linked worktree it fail-closes any write outside the worktree root. No action needed — it's wired in `hooks.json`.
- **Post-merge cleanup:** after a worker PR merges, run `foundry-work-isolation.sh cleanup <repo> <branch>`. It harvests the worker's learnings sidecar before removing the worktree.
- **Worker-learnings harvest (automatic):** `foundry-harvest-learnings.sh` fires on the teardown seam — PreToolUse(Bash) on any `git worktree remove`, and in-script before the cleanup removal — forwarding `.agent/learnings.jsonl` (UNVALIDATED; the distiller validates) into `.foundry/session-learnings/<date>/<session_id>__<task-id>.jsonl`. Fail-open; WARNs when a sidecar is absent for a worker that reported success. No action needed.
- **Break-glass:** before a deliberate main-clone product-path edit, append a `break-glass` record (operator-reason) to `.foundry/security-audit.jsonl` (audit-only; v1 does not hard-block).

## Spec-staged-and-preflighted jail (feat-foundry-worktree-on-native-isolation, AC-WNI-1..4)

Closes a recorded design gap: in a design-partner self-host run, the MULTI-REPO worktree sandbox **hid the atom's
spec/contract from the worker** — the worker halted, and the fan-out report still recorded a
false-green `exit:0`. `hooks/foundry-worktree-create.sh`'s MULTI-REPO redirect (see
`/foundry:dispatch` → *Multi-repo dispatch*) now closes that hole in two steps, both automatic, no
action needed:

1. **Stage (containment- and symlink-guarded).** The atom's spec + `acceptance-contract.yaml`
   (named by the dispatch manifest's `contract_ref`) are copied — never symlinked — into the
   freshly-claimed worktree, at the SAME relative path they hold under the workspace root. A
   symlink would let the source drift out from under the checked hash; a copy gives the worker a
   stable snapshot even if the parent workspace mutates afterward. BEFORE any write: the staged
   destination is REJECTED (fail-closed, never written) if its realpath-normalized location does
   not resolve strictly under the worktree root (a `..`-escaping `contract_ref` or a contract's own
   `spec_ref`) or if the destination is already a symlink (e.g. one the product repo itself tracks
   at that leaf path) — `copyfile` never follows a pre-existing symlink through to write outside the
   jail.
2. **Preflight (hard fail).** Before the worker ever sees the worktree, both staged copies are
   verified: readable, their recomputed content hash equals the frozen `authorized.spec_sha256` /
   `authorized.contract_sha256` on the (staged) contract's own trailer — the SAME normative-region /
   contract-proper-region hash bases `/foundry:authorize` froze (imported from the canonical
   `foundry_contract` module, never reimplemented) — AND the staged contract independently passes
   `foundry_contract.validate_contract_bytes`'s structural/integrity floor (the SAME sentinel-
   injection guard `/foundry:authorize` and `foundry_grounding_conformance.py` apply — catches a
   second `authorized:` block smuggled above the sentinel even when the post-tamper hash still
   happens to match). ANY
   failure — unreadable, absent, hash-mismatched, path-escaping, symlink-shadowed, or
   integrity-failing, for EITHER file — HARD-FAILS the worktree spawn (no stdout path; the harness
   BLOCKS creation per the native `WorktreeCreate` emit-path-or-block contract) with a typed
   diagnostic recorded in `.foundry/dispatch.log`: one of `spec-unreadable`, `spec-absent`,
   `spec-hash-mismatch`, `spec-path-escape`, `contract-unreadable`, `contract-absent`,
   `contract-hash-mismatch`, `contract-path-escape`, `contract-integrity`. A manifest with an
   EMPTY/absent `contract_ref` also fails closed (`PREFLIGHT-SKIP-NO-CONTRACT` logged) rather than
   silently emitting a worktree with nothing staged/preflighted. A jail that hides — or redirects —
   the spec now fails fast and typed, never a silent halt reported as success.

Single-repo dispatch (native `Agent isolation:worktree` of the session repo itself, no manifest)
is unaffected — this staging/preflight only applies to the MULTI-REPO manifest-driven redirect,
where a separate spec/contract corpus (the workspace) and a separate worktree (the product repo)
genuinely diverge. Live-seam: `python3 -m pytest tests/test_worktree.py -q` (the converted isolation assertions)
(offline; positive control + hash-mismatch/absent/unreadable/path-escape/symlink/integrity negative
controls over the real hook).

## Anti-patterns

- **Re-introducing `wt claim`/queue for SINGLE-REPO worktree creation** — native
  `isolation:worktree` does it. (MULTI-REPO is the exception: native isolation only worktrees
  the *session* repo, so reaching a separate product clone needs the re-extracted
  `WorktreeCreate` hook + `foundry-wt` + a minimal `target_repo` manifest — see
  `/foundry:dispatch` → *Multi-repo dispatch*. The write-jail (`foundry-cwd-enforce`) is
  unchanged and jails to whatever worktree the worker is in, product or workspace.)
- **Relying on cwd-isolation as a write-jail** — it isn't; `foundry-cwd-enforce` is the jail.
- **Hand-resolving a worker that wrote outside its worktree** — the jail fail-closes; fix the worker prompt.
