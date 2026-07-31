---
name: drift-sweep
description: CURRENTLY DORMANT (named honestly — the scripts/foundry_drift_sweep.py engine this skill drives was retired and does not ship; this file is design intent for a re-implementation, not a live procedure). Advisory batch drift-suspect sweep — given a base->head system-state snapshot pair (from the system-state snapshot tool), lists every AUTHORIZED contract whose frozen system_grounding block (or, for atoms predating it, its spec body) still references a schema/module identifier the migration DROPPED or RENAMED. ADVISORY, NOT a merge gate — not part of the merge floor (ci.yml + btb-gates), is not a branch-protection required status, opens no grounding source itself. Trigger to sweep the AUTHORIZED-contract corpus for stale schema references after a migration lands.
---

# /foundry:drift-sweep

An operator/agent **ADVISORY** sweep that names the **whole batch** of frozen atoms a schema/module
migration just invalidated, instead of letting each one be rediscovered one at a time, weeks apart,
at its own build. It is the **batch-level, schema-coherence analogue** of
`/foundry:coherence-check` (which covers citations): same advisory posture, same tri-state
fail-closed exit, same fresh-walk-every-run discipline — the missing dimension is schema
identifiers instead of citations.

> **DORMANT:** the engine below (`scripts/foundry_drift_sweep.py`) does **not** ship — the
> command is design intent for a re-implementation, not a runnable verb today.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_drift_sweep.py" \
  --base  /path/to/base-snapshot.json \
  --head  /path/to/head-snapshot.json \
  --corpus-root "$CLAUDE_PROJECT_DIR"      # default: $CLAUDE_PROJECT_DIR or cwd
```

## Input — a base->head snapshot PAIR (host-side, HOW obtained is a wiring concern)

The sweep takes **two already-built** snapshots from the system-state snapshot tool
(`foundry_system_snapshot.build_system_snapshot()` results — `{schema_version, schema_grounded,
module_grounded, grounding_configured, entities, modules, signature}`) as JSON files. It opens
**no grounding source itself** and does **no git plumbing** — how the caller obtains the base
(pre-migration) and head (post-migration) snapshots (e.g. re-resolving the same `grounding` block
at two different migration-history points) is an **operational wiring concern**, not this sweep's.

## What counts as a candidate (schema-delta vocabulary)

A **dropped/renamed candidate identifier** is one present in the **base** snapshot and **absent**
from the **head** snapshot, over the grounded dimensions:

- **table** — a base `entities` key absent from head.
- **column** — a base `<entity>::<column>` absent from head.
- **module** — a base `modules` member absent from head.

A **RENAME** is observed as a **drop of the old identifier** — the new name appears as an
*addition*, which is **never** a candidate (only disappearances are). An identifier present in
both, or added only in head, is never a candidate.

## The back-reference scan

For each candidate identifier, the sweep scans **every AUTHORIZED contract** in the corpus
(AUTHORIZED per `foundry_authz`) for that identifier:

1. **Primary — the `system_grounding` block** (written by the system_grounding validators):
   a **whole-string** match against each artifact's `identifier`.
2. **Fallback — the spec body**, when an AUTHORIZED atom carries **no** `system_grounding` block
   (it predates the block): a **whole-identifier-token** grep — the candidate must be bounded by a
   non-identifier character (or start/end of text) on **both** sides over the identifier class
   `[A-Za-z0-9_.:]`. Candidate `vehicle` matches the token `vehicle` but **never** `vehicles`
   (suffix continuation), `vehicle_parts` (suffix continuation), or `dealer_vehicle` (prefix
   continuation) — a partial overlap is **not** a hit.

**DRAFT (non-AUTHORIZED) atoms are always excluded**, even when they reference the identifier.

## Output — a worklist naming EVERY affected atom (deterministic JSON on stdout)

```jsonc
{
  "provenance":  {"corpus_root": "/abs/root", "source": "fresh-walk", "error": null},
  "candidates":  ["public.vehicles", "public.vehicles::vin", ...],   // sorted
  "drift_suspects": [
    {"spec_ref": "specs/features/.../feat-x.md", "identifier": "public.vehicles",
     "hit_via": "system_grounding"}
  ],
  "counts": {"candidates": N, "drift_suspects": M}
}
```

`drift_suspects` entries are the **pinned field set** `{spec_ref, identifier, hit_via}` —
`hit_via ∈ {"system_grounding", "spec_body"}` — sorted **ascending** by `(spec_ref, identifier)`. A
single sweep **lists every affected atom**, not one at a time.

## Tri-state, fail-CLOSED exit (LOAD-BEARING)

- **`0`** — ran, **zero** drift-suspects.
- **`1`** — ran, **≥1** drift-suspect. This is **ADVISORY only** — a worklist / re-audit nudge,
  never a veto.
- **`2`** — **operational failure**: a malformed/unreadable snapshot input, a missing/mis-resolved
  corpus root, or the corpus walk **raising** — including an **unreadable/corrupt AUTHORIZED
  contract encountered mid-walk**. A scan error is a **HARD stop**, never a silent skip to a
  partial `0`/`1` worklist.

## Unconfigured no-op

When **either** snapshot has `grounding_configured: false` (no schema surface to diff) the sweep
emits **zero** drift-suspects and exits `0` — never a false signal for an adopter with no schema
source configured. This holds for **both** asymmetric permutations (base configured / head not,
and vice-versa).

## Venue boundary — ADVISORY, NOT a merge gate

This sweep is **NOT** part of the merge floor:

- It does **NOT** run as part of the merge floor (`.github/workflows/ci.yml` + `btb-gates` —
  the bespoke `foundry-merge-gate.py` this line originally named was retired; the boundary principle carries over to whatever embodies the floor).
- It is **NOT** a branch-protection required status.
- It emits a signal / re-audit nudge — it does **NOT** rewrite frozen specs or re-authorize them
  (that disposition is the operator's, via re-audit).

The enforcement floors live elsewhere in the reality-grounding gate: the review-time
grounding-reconciliation check catches the divergence at each atom's own review; the
merge-time grounding-reconciliation check, `foundry_reconcile.py`) was designed to reconcile
mechanical drift at each atom's own merge, but its original call site — the deleted bespoke merge
gate — no longer exists, and it is not currently wired into `ci.yml`/`btb-gates` (a sweep finding,
flagged honestly here, not fixed in this pass). This atom is the **batch early warning at
migration-merge time** regardless: a single sweep names the whole affected batch instead of waiting
weeks for each to be rediscovered independently.

## Anti-patterns

- **Promoting exit `1` into a required merge status.** It is advisory by contract; do not wire this
  sweep into the merge floor (ci.yml + btb-gates) or a branch-protection required check.
- **Treating a mid-walk read/parse error as a partial `0`/`1`.** An unreadable/corrupt AUTHORIZED
  contract hit during the walk is a hard stop (exit `2`), never a silent skip.
- **Substring matching in the spec-body fallback.** The match is a whole-identifier token
  (`\b`-style, over `[A-Za-z0-9_.:]`) — a partial overlap (`vehicles` for `vehicle`) is never a hit.
- **Opening a grounding source or doing git plumbing inside the engine.** The sweep is host-side,
  snapshot-pair-in; obtaining the base/head pair is the caller's wiring concern.
