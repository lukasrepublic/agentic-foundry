---
name: mode-interactive
description: The interactive implementation posture (/foundry:mode-interactive) — the default lean direct loop. The operator drives edit→verify→merge in one accountable context; the operator reviews the diff at the merge button (the Regular-mode segregation). Sibling of mode-autonomous; they differ ONLY at the two ends (who paces, who approves the merge).
---

# /foundry:mode-interactive

The default posture. The lean direct loop: ONE accountable context implements
inline, runs the live-seam walk, and the **operator reviews the merge diff** (Regular
mode) — the un-forgeable segregation on single-host. No worker dispatch, no
queue; the retro shows this beats the dispatch ceremony for most work.

## When to trigger

- Default for a single atom / small change. "implement `<atom>` interactively", "/foundry:mode-interactive".
- Use `mode-autonomous` instead only for scale fan-out over an authorized release.

## Procedure

1. Confirm the atom is AUTHORIZED (`/foundry:authorize` first if not).
2. Implement inline against the frozen `acceptance-contract.yaml` (do not weaken checkpoints).
3. Run the live-seam walk (real boot + Chrome MCP / real seam) against each frozen checkpoint locator.
   - **Fast lane for breadth (page-context fetch + SSE tail).** For *many* cases against the
     same seam, a faster faithful method than one-UI-path-at-a-time: drive the app's API
     **from the browser page context** (real cookies/session → the real model + DB seam, not a
     mocked client) and parse the streamed (SSE) response. Use it to exercise breadth quickly.
   - **Caveat — conversation independence.** Independent single-shot page-context calls behave
     differently from multi-turn/stateful flows; conversation-state independence must be
     **verified, not assumed**.
   - **The UI walk stays authoritative.** The fast lane is for breadth only; the **UI walk
     remains authoritative**, especially for stateful/multi-turn flows. Do not substitute the
     fast lane for the authoritative UI walk on those paths.
4. Cut the PR (`gh pr create`); the worker cuts its PR, the merge floor decides.
5. **Operator reviews the diff + the floor's check results at the merge** — the Regular-mode authority. Merge on PASS.

## The load-bearing insight

`mode-interactive` and `mode-autonomous` differ ONLY at the two ends — **who paces the
loop** (operator vs `/loop`) and **who reviews before the merge** (operator diff-review vs
a fresh-context pr-reviewer pass — advisory, never the merge approval; the floor's green
checks admit either way). The middle (authorized-spec → implement → certification →
the merge floor) is identical and invariant in both.

## Anti-patterns

- **Skipping the live-seam walk** because "it compiled / unit-tests pass" — status ≠ functional.
- **Self-merging in Regular mode** — the operator diff-review IS the segregation; do not bypass it even under noninteractive.
