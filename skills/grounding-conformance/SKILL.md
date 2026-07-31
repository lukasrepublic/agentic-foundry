---
name: grounding-conformance
description: Advisory grounding-conformance backfill sweep — classifies every frozen acceptance-contract.yaml in the corpus as GROUNDED / UNGROUNDED / STALE against the current system-state snapshot (from the system-state snapshot tool), reusing the system_grounding contract block's structural AND consistency validators (the system_grounding validators), and emits a deterministic backfill worklist naming every non-conformant atom. ADVISORY, NOT a merge gate — not part of the merge floor (ci.yml + btb-gates), is not a branch-protection required status, never rewrites a frozen contract. Trigger to measure how much of the AUTHORIZED corpus is reality-grounded and to surface the atoms still needing a system_grounding block backfilled or re-audited.
---

# /foundry:grounding-conformance

An operator/agent **ADVISORY** sweep that answers **"how much of the frozen corpus is reality-grounded,
and which atoms are not?"** — the **coverage** analogue of `/foundry:drift-sweep` (the batch sweep, which answers
a *delta* question: "did a schema change just invalidate frozen specs?"). This atom closes the
reality-grounding gate's **retroactive gap**: atoms authorized **before** the gate existed (the
system_grounding validators) carry **no** `system_grounding` block at all, so the corpus is only partially covered — this
sweep measures the gap and lists exactly the atoms that still need attention.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_grounding_conformance.py" \
  --corpus-root "$CLAUDE_PROJECT_DIR"     # default: $CLAUDE_PROJECT_DIR or cwd
  # --snapshot /path/to/snapshot.json     # optional: inject a pre-built system-state snapshot;
                                           # default: build a fresh one host-side against --corpus-root
```

## Input — the current system-state snapshot (host-side, HOW obtained is a wiring concern)

The engine entrypoint, `foundry_grounding_conformance.classify_corpus(corpus_root, snapshot)`, takes
an **already-built** snapshot from the system-state snapshot tool (`foundry_system_snapshot.build_system_snapshot()` — `{schema_
version, schema_grounded, module_grounded, grounding_configured, entities, modules, signature}`) as a
parameter. It opens **no grounding source itself**. The standalone CLI above builds one fresh by
default (or accepts `--snapshot` for a pre-built/pinned one) — how the snapshot is obtained is the
**caller's** wiring concern, not the engine's.

## The three-way classification (per atom, per the AC-GCB-1 normative rule)

For every **frozen** contract (carries an `authorized:` trailer — `foundry_authz._authorized_block`),
against the current snapshot:

- **GROUNDED** — carries a `system_grounding` block that passes **structural** validation
  (`_system_grounding_structural_errors`, zero errors) AND **consistency** validation
  (`system_grounding_errors`, zero errors vs. the snapshot). A well-formed **empty**
  `artifacts: []` block is GROUNDED (conformant — nothing to ground).
- **UNGROUNDED** — carries **no** `system_grounding` block at all (a pre-gate atom).
- **STALE** — carries a block that is present but non-conformant: either **structurally malformed**
  (≥1 structural error — checked **first**, fail-safe) **or** contradicts the current snapshot
  (≥1 consistency error). A structurally malformed block is **never** silently GROUNDED — the
  consistency validator alone would skip it silently (it assumes the structural floor already ran),
  so both validators run, structural first.

**Non-conformant = UNGROUNDED ∪ STALE.** A **DRAFT** (never-frozen) contract is never classified or
listed — only frozen contracts are in scope.

## Output — a backfill worklist + a conformance summary (deterministic JSON on stdout)

```jsonc
{
  "provenance": {"corpus_root": "/abs/root", "source": "fresh-walk", "error": null},
  "worklist": [
    {"spec_ref": "specs/features/.../feat-x.md", "classification": "UNGROUNDED", "errors": []},
    {"spec_ref": "specs/features/.../feat-y.md", "classification": "STALE",
     "errors": ["system_grounding: artifact kind='table' identifier='public.ghost' declared "
                "'exists' but is absent from the live snapshot (AC-SGC-4)"]}
  ],
  "summary": {"grounded": 41, "ungrounded": 12, "stale": 2, "total": 55}
}
```

`worklist` entries are the **pinned field set** `{spec_ref, classification, errors}` —
`classification ∈ {"UNGROUNDED", "STALE"}`, `errors` the specific failing structural/consistency
message(s) (empty for UNGROUNDED; never empty for a malformed-block STALE entry) — sorted
**ascending by `spec_ref`**. `summary` is internally consistent with the worklist:
`ungrounded + stale == len(worklist)` and `total == grounded + ungrounded + stale`.

## Tri-state, fail-CLOSED exit (LOAD-BEARING)

- **`0`** — ran, **zero** non-conformant atoms.
- **`1`** — ran, **≥1** non-conformant atom. This is **ADVISORY only** — a backfill worklist /
  re-audit nudge, never a veto.
- **`2`** — **operational failure**: a malformed/unreadable snapshot input, a missing/mis-resolved
  corpus root, or the corpus walk **raising** — including an **unreadable/corrupt frozen contract
  encountered mid-walk**. A scan error is a **HARD stop**, never a silent skip to a partial `0`/`1`
  worklist.

## Unconfigured no-op

When the snapshot's `grounding_configured` is **false** (no schema/module surface to conform to), the
sweep classifies **every** atom **GROUNDED**, emits an **empty** worklist, and exits `0` — no adopter
without a schema source configured is ever told their corpus is non-conformant.

## Venue boundary — ADVISORY, NOT a merge gate

This sweep is **NOT** part of the merge floor:

- It does **NOT** run as part of the merge floor (`.github/workflows/ci.yml` + `btb-gates` —
  the bespoke `foundry-merge-gate.py` this line originally named was retired; the boundary principle carries over to whatever embodies the floor).
- It is **NOT** a branch-protection required status.
- It emits a coverage report / backfill worklist — it does **NOT** rewrite frozen contracts or
  auto-add `system_grounding` blocks.

**No unattended backfill.** The worklist names the gap; closing it flows through the **normal
authoring loop** — a re-audit and re-authorization of each named atom (or, going forward,
`/foundry:intake`'s schema-grounding survey) — never an automated, unattended rewrite of a frozen
contract. Retroactively hard-requiring a block on every pre-gate atom would wedge the corpus; the
measured-baseline-then-burn-down pattern (the same one `betterer`/`mypy --strict` baselines or
OPA/Conftest policy-conformance scans use for a newly-adopted rule) is the industry answer: surface the
gap, drain it through the normal loop.

## Anti-patterns

- **Promoting exit `1` into a required merge status.** It is advisory by contract; do not wire this
  sweep into the merge floor (ci.yml + btb-gates) or a branch-protection required check.
- **Auto-writing a `system_grounding` block from the worklist.** The sweep surfaces the gap; a human
  operator (or the authoring loop) backfills it via re-audit + re-authorization — never a silent
  mutation of a frozen artifact.
- **Running only the consistency validator.** A structurally malformed block would then be silently
  skipped into a false GROUNDED — always run the structural validator first (this atom's own
  `classify_corpus` does this for you; do not hand-roll a shortcut around it).
- **Treating a mid-walk read/parse error as a partial `0`/`1`.** An unreadable/corrupt frozen contract
  hit during the walk is a hard stop (exit `2`), never a silent skip.
