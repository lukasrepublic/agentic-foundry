---
name: context
description: Context lifecycle as thin seams over native primitives (/foundry:context <snapshot|resume|list|status>). WRAP of the ctx-* skills — additive over native /compact, --resume, /rewind, and the context sensor; never reinvents transcript replay. Trigger to snapshot/resume a session's distilled arc-state or check context budget.
---

# /foundry:context

The context lifecycle, an additive WRAP layer over native primitives. The four ctx-* skills fold into one
`/foundry:context <action>` — each action is a THIN seam over a native primitive,
realizing (extend, don't compete).

| action | native primitive it wraps | foundry delta |
|---|---|---|
| `snapshot` | `/compact` (lossy in-session) + `--resume` (verbatim replay) | writes a DURABLE, hand-editable distilled arc-state file (§0–§6 schema) that survives session end + 30-day replay retention. Additive to `--resume`, not a replacement. |
| `resume` | `--resume <id>` (verbatim) | re-grounds a FRESH session from the distilled snapshot's §6 authoritative-artifact pointers (cheaper than verbatim replay; survives expiry). |
| `list` | — | lists snapshots (the `summary:` line per file). |
| `status` | the context sensor | reports context budget / tier. |

## The snapshot schema (§0–§6)

The distilled arc-state file has seven short sections, numbered so resumers can cite them:
§0 header (session id, date, mandate) · §1 the mandate/goal · §2 decisions taken (with why) ·
§3 current state (what is done/verified) · §4 open threads + blockers · §5 next steps ·
§6 authoritative artifact pointers (paths/commits/PRs a successor MUST re-read before trusting
the prose — the load-bearing section).

## Procedure

- `snapshot` — distill the session arc into the §0–§6 schema file; §6 (authoritative artifact pointers) is load-bearing (a snapshot whose §6 is empty is a defect).
- `resume <id|path>` — read the snapshot; re-read its §6 artifacts BEFORE trusting the distilled prose.
- `list` / `status` — read-only.

## Relationship to native (do NOT reinvent)

`/compact` (in-session lossy cost lever), `--resume` (verbatim replay), `/rewind`
(within-session rollback) remain authoritative. This skill is the DURABLE,
distilled, cross-session cold-start seam on top of them — additive only.

## Mandate handoff

(feat-foundry-session-per-mandate, AC-SPM-3.) Session-per-mandate is the enforced advisory norm: one
session per mandate, ended and handed off rather than compacted through. Prior operating-cost
retrospective grounding showed this is the largest single cost lever — sessions that snapshot-and-respawn
instead of compacting run compaction-free, at a fraction of the cache-read spend of a multi-mandate
mega-session.

The statusline context segment (`scripts/foundry-statusline.sh`) makes the budget a visible choice: past
the configured threshold (default 65 used-%, `.foundry/context-threshold` to override) it escalates to a
distinct over-budget state naming the exact move — `/foundry:context snapshot`. This is ADVISORY ONLY;
nothing blocks or auto-terminates (AC-SPM-4) — the operator (or an autonomous driver) chooses when to
hand off.

The handoff procedure:

1. **`snapshot`** the arc-state — `/foundry:context snapshot`, distilling the §0–§6 schema (§6
   authoritative-artifact pointers load-bearing).
2. **End the session** — at mandate completion, or on the over-budget nudge, whichever comes first. Do
   not compact through it; a compaction pays the summarization-loss + cache-read tax the snapshot avoids.
3. **Boot the successor** from the snapshot plus the release run-state summary
   (`/foundry:release run-state <id> --summary`) — the snapshot
   supplies the distilled arc-state, the run-state summary supplies the machine-derived per-atom ledger
   (`authorized` / `dispatched` / `merged_on_main` / `runnable` / `blocked`), so the successor cold-starts
   grounded in both "what happened in this arc" and "what the release actually needs next" without
   re-deriving either from a live transcript.

### Autonomous-driver variant

A driver operating without a human at the wheel (factory/noninteractive mode, a supervisor loop
dispatching successive mandates) follows the same procedure as a scheduled step, not an exception: on
reaching the over-budget threshold — or at each mandate boundary, whichever comes first — the driver
`snapshot`s the arc-state, ends the session, and respawns a fresh session from the snapshot + the release
run-state summary rather than compacting through and carrying a stacked, multi-mandate context forward.
The nudge is the same advisory signal a human reads off the statusline; the driver's loop treats it as a
scheduled handoff point rather than a threshold to negotiate past.

## Anti-patterns

- **Reinventing transcript replay** — that's `--resume`; this is the distilled durable layer.
- **Empty/prose-only §6** in a snapshot — §6 must cite resolvable artifacts.
- **Labeling the snapshot "lossless"** — it is high-signal / sufficient-to-reconstruct.
- **Compacting through the over-budget nudge** — stacking mandates in one live context instead of
  snapshot-and-respawn is exactly the mega-session pattern this atom exists to counter.
