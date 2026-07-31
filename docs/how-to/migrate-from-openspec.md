# How to migrate from OpenSpec

OpenSpec's delta-spec model (propose a change as a diff against current behavior) maps
cleanly onto Foundry's atomic-spec model — an OpenSpec change proposal is, in Foundry
terms, an atom waiting for a contract.

## The mapping

| OpenSpec artifact | Foundry equivalent | What changes |
|---|---|---|
| `changes/<name>/proposal.md` | `feat-<capability>.md` | the proposal's "what changes" becomes the normative region; each requirement gets a stable AC-ID |
| `changes/<name>/tasks.md` | the dispatch loop | tasks become the implementation plan of a dispatched atom |
| `specs/` (current behavior) | extracted baseline specs | run `/foundry:extract-spec` per capability as work touches it — same delta philosophy |
| archive-on-merge | git history + the frozen contract | approval is the spec merged to the workspace main; the ledger is git |
| — (no equivalent) | `acceptance-contract.yaml` + authorization | the new artifacts: observable checkpoints, operator-signed freeze |

## Steps

1. **Bring your current-behavior specs lazily.** Don't bulk-convert `specs/` — extract each
   capability with `/foundry:extract-spec` the first time work touches it. This is the same
   brownfield discipline OpenSpec itself teaches, so the muscle transfers directly.
2. **Convert your next change proposal, not your backlog.** `/foundry:intake` + paste the
   proposal; it gains AC-IDs, the normative region, and a drafted acceptance contract.
3. **Add the observable checkpoints** — the one new discipline (see the same step in
   [migrate-from-spec-kit.md](migrate-from-spec-kit.md); it's identical here).
4. **Standard loop from there**: spec-review → authorize → dispatch → certify-local →
   sign-off.

## What you gain over archive-on-merge

OpenSpec's merge-time archive records *that* a change landed. Foundry's freeze records
*what was approved* (hash-bound, operator-signed, before implementation) and certification
records *that the merged thing works against a running instance*. The delta-spec authoring
model you like is untouched — it's the after-the-document seam that changes.
