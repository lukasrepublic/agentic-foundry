---
name: learn-distill
description: Cluster session-learning records into HBK/memory/skill candidates (/foundry:learn-distill), on a native-scheduled cadence. The distill consumer (token-overlap clustering, deterministic) is CUSTOM; the CADENCE is a WRAP over native ScheduleWakeup/CronCreate. Trigger on the scheduled tick or when the operator asks for a cluster report.
---

# /foundry:learn-distill

The self-improvement consumer — a native-scheduling WRAP for the cadence. Two halves:

- **CUSTOM (no native equivalent):** the deterministic clustering + report logic, implemented in
  **`scripts/foundry-distill.py`** — read the dated
  partitions under `.foundry/session-learnings/<YYYY-MM-DD>/*.jsonl` (the reconciliation
  `.harvest-log.jsonl` + dotfiles excluded), token-overlap cluster (≥3 shared normalized tokens drawn
  from every string scalar field + `tags`; no embeddings; fully-pinned canonicalization + ordering →
  **byte-reproducible**), threshold-promote (≥3-record components) to HBK/memory/skill/unclassified
  candidates over the real `type`/`kind` vocabulary, write
  `.foundry/learnings/DISTILL_REPORT-<UTC-date>.md` (an **untracked** runtime artifact — NOT the
  governed `specs/` tree), then prune buffer partitions whose **directory-NAME date** is >90 days old
  (never by mtime). Run it directly: `scripts/foundry-distill.py [--date YYYY-MM-DD] [--dry-run]`;
  self-test `scripts/foundry-distill.py --selftest`. (The doctor drop-in check this line
  originally named was retired with the drop-in registry in the v0.25.0 realignment — an open
  loop is now visible only by running the distiller itself.)
- **WRAP over native:** the CADENCE. Instead of a bespoke scheduler, the distill tick
  is scheduled via native **`ScheduleWakeup`** (session-local) or **`CronCreate`**
  (persistent) — e.g. a daily tick that runs the consumer. Producers feed the buffer
  via `/foundry:learn-capture` (the in-session path) + the worker-sidecar harvest.

## Procedure

1. **Scheduled tick** (native `CronCreate` daily, or `ScheduleWakeup`): invoke the consumer —
   `scripts/foundry-distill.py`.
2. **Consume** the buffer (Read → Cluster → Threshold → Write → Retention, in that strict order —
   retention prunes only partitions whose NAME-date is >90 days old, never a within-window partition).
3. **Emit** `.foundry/learnings/DISTILL_REPORT-YYYY-MM-DD.md` with HBK/memory/skill/unclassified
   candidates (operator-reviewed; no auto-dispatch).

## Anti-patterns

- **A bespoke scheduler** — use native `ScheduleWakeup`/`CronCreate` for the cadence.
- **Embeddings / semantic clustering** — clustering is deterministic token-overlap (reproducible across runs).
- **Running retention before the read phase** — order is non-negotiable.
- **Auto-dispatching candidates** — the report is a recommendation; the operator drives follow-ups.
