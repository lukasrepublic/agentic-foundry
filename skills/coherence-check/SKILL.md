---
name: coherence-check
description: Advisory citation-coherence sweep over the direct-edit WORKSPACE corpus (/foundry:coherence-check). Builds the citation graph FRESH every run (no cache trust), then reports broken citations vs out-of-scope citations as deterministic JSON with a tri-state fail-closed exit. ADVISORY, NOT a merge gate — not part of the merge floor (ci.yml + btb-gates), not a branch-protection required status, never reads a PR-merged diff. Trigger to sweep the workspace corpus for dangling/malformed citations.
---

# /foundry:coherence-check

An operator/agent **ADVISORY** sweep that walks the **direct-edit WORKSPACE corpus**
(`docs/`, `foundry/`, `specs/` markdown) and reports citation coherence. It builds the
citation graph **fresh in-memory on every run** — it does NOT read the committed
`.foundry/graph.json` (no cache trust, no `is_stale` branch, single `fresh-walk`
provenance shape).

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-coherence-check.py"
# override the scope (repeatable); default is the canonical docs/ foundry/ specs/:
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-coherence-check.py" --scope specs --scope docs
```

## Output (deterministic JSON, single document on stdout)

```jsonc
{
  "provenance": {"source": "fresh-walk", "scope": ["docs","foundry","specs"],
                 "corpus_root": "/abs/root", "node_count": 42},
  "findings":     [ {"src","target","resolved","reason"} ],   // broken; reason ∈ {malformed, target_missing}
  "out_of_scope": [ {"src","target","resolved"} ],            // resolve to a real .md outside scope — NOT broken
  "counts":       {"broken": N, "out_of_scope": M}
}
```

`findings` are sorted by `(reason, src, target)`; `out_of_scope` by `(src, target)`. On an
operational failure `provenance.source == "error"` and an `error` key carries the cause.

## Tri-state, fail-CLOSED exit (LOAD-BEARING)

- **`0`** — ran, **zero** broken citations.
- **`1`** — ran, **≥1** broken citation. This is **ADVISORY only**.
- **`2`** — **operational failure**: `build_graph` raised, the resolved corpus root is
  missing, OR the corpus walked to **zero nodes**. An empty / mis-resolved corpus is a
  **HARD error**, never a vacuous clean `0` — that is what makes the sweep fail-closed.

**The `1` vs `2` split is load-bearing. Downstream wiring MUST NOT promote exit `1` into a
blocking gate over the workspace corpus.** `1` says "broken citations exist, an operator
should look" — it is not a merge veto. `2` is the only fail-closed operational stop, and it
exists so a mis-resolved/empty corpus can never masquerade as clean.

## Venue boundary — ADVISORY, NOT a merge gate

This sweep is **NOT** part of the merge floor:

- It does **NOT** run as part of the current merge floor (`.github/workflows/ci.yml` +
  `btb-gates`) — the bespoke `foundry-merge-gate.py` this rule originally named was retired; the boundary principle carries over regardless of which script embodies
  the floor at any given time.
- It is **NOT** a branch-protection required status.
- It **never reads a PR-merged diff** — it walks the local working-tree corpus only.

### Why a merge-gate broken-citation check is the WRONG venue

A merge-gate broken-citation check is only sound when *the PR-merged repo IS the corpus*.
Under Foundry **self-hosting**, the corpus is the **workspace** (the direct-edit specs/docs
tree), not the product code repo that a PR merges into — so a citation check run at the
product repo's merge gate is **vacuous**: it inspects a tree that isn't the citation corpus.
This was learned the hard way: an earlier merge-gate broken-citation check was paused after it
kept reporting vacuous results under self-hosting — a recorded incident where a half-updated
corpus paused the program. The coherence sweep therefore lives at the **workspace /
operator venue** as an advisory, where the tree it walks actually IS the corpus.

The authoritative graph-staleness derivation is `foundry_graph.is_stale` (consumed by the graph
tooling, e.g. `foundry-graph-mcp.py`), not this advisory citation sweep. Keep them distinct.

### Governance-propagation advisory mode

An **additive, ADVISORY** mode that treats a governance-doc change as an *event that cascades*
(GitHub Spec Kit `/constitution` prior art): when one of the declared governance source docs
changes, the corpus nodes that **cite / depend on** it are candidates for drift, so the mode
emits a re-validation **worklist** naming them.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-coherence-check.py" --governance-propagation
```

- **Declared sources.** A module-level `GOVERNANCE_SOURCE_DOCS` constant (the in-scope governance
  docs the adopter's own corpus declares, e.g. a research/discipline or constitution-style doc under
  `docs/`) names the cascade triggers.
- **Reuses the existing graph.** It builds the corpus graph via the **existing**
  `foundry_graph.build_graph` over the canonical scope and reads the **materialized
  reverse/backlink index** (the same index that backs the `graph_backlinks` MCP tool) — it does
  **not** re-implement the markdown / citation parser.
- **Deterministic worklist.** For each governance doc **present** in the corpus it lists, in a
  **stable sorted order**, the nodes that cite it (`"these N nodes reference <doc> — review for
  drift"`). A doc with **zero dependents** yields an **empty** worklist for it (not an error); a
  doc absent from the corpus simply yields no worklist entry.

#### Advisory boundary — never a gate, never part of the merge floor

This mode adds **no merge authority**. It is **not** part of the current merge floor
(`.github/workflows/ci.yml` + the `btb-gates` lane signal), is **not** a branch-protection
required status, and **never** reads a PR-merged diff — exactly like the citation sweep's venue
boundary above (the bespoke `foundry-merge-gate.py` this section originally described was retired; the venue-boundary PRINCIPLE — never load-bearing at merge —
carries over to whatever the merge floor is at any given time). Its exit mirrors the tri-state
fail-closed posture: a **populated or empty worklist is advisory** (exit `0` — a worklist is never
a failure; there is no exit `1`), while an **operational failure** (missing root, empty walk, or
`build_graph` raised) is **fail-closed** (exit `2`).

**Anti-dormancy — currently unasserted by machine (named honestly).** The drop-in check that used
to assert this mode + its boundary (a drop-in per-check selftest,
`--coherence-governance-propagation-selftest`) and the doctor's `check_coherence_consumer`
(`--coherence-consumer-selftest` over a throwaway temp corpus) were both removed with the rest of
the drop-in-check registry (in the v0.25.0 test-suite realignment, the doctor-thinning to a
5-check probe — see `skills/doctor/SKILL.md`) and were **not** ported to the `tests/` pytest suite.
`--governance-propagation` still runs (`scripts/foundry-coherence-check.py --governance-propagation`);
nothing currently proves it keeps running if this mode regresses. A future port belongs in `tests/`
(CONSTITUTION.md §8), driven the way that realignment ported the other retired drop-in checks that
stayed live.

## Anti-patterns

- **Promoting exit `1` into a required merge status.** It is advisory by contract.
- **Reading `.foundry/graph.json`.** This sweep always re-walks fresh; it never trusts the cache.
- **Treating an empty walk as clean.** Zero nodes is exit `2` (error), never `0`.
- **Running the consumer over the live corpus inside doctor.** (The retired doctor consumer used a temp corpus only — kept as the rule for any future re-wiring.)
