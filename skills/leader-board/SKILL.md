---
name: leader-board
description: Read-only operator and outcome statistics (/foundry:leader-board) over GitHub history and native usage - three rolling windows (1d, 1w, 1m = trailing 30 days) with one cutoff, flow and collaboration measures, lifetime tokens per accepted outcome, the as-of queue panel, a DORA service panel, and a coverage header. Descriptive, deterministic, never a score. Trigger on "/foundry:leader-board", "team stats", "who is blocked", "tokens per outcome".
---

# /foundry:leader-board

A read-only report. It writes nothing to GitHub or to the workspace. It combines GitHub work history
(gn-04 events over issue timelines and comments) with the native usage dataset (gn-05) and the
operator mapping from `github_scope.operators` (`{login: op_id}`). Definitions are intake doc 03,
verbatim.

## Run

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_leader_board.py" [--as-of ISO] [--operator X] \
  [--project P] [--work-type T] [--service S] [--sort METRIC[@1d|@1w|@1m]] [--json]
```

- `--as-of` is the only clock: identical inputs and cutoff give byte-identical output. Without it the
  cutoff is "now".
- `--sort` takes `name, throughput, cycle_median, ready_lead_median, blockers_resolved,
  blocker_ack_median, blocker_resolution_median, tokens`. It orders a table; it is not a ranking.
- `--inputs FILE` replays already-fetched inputs with no network. `--deployments FILE` supplies the
  adopter's deployment records for the service panel (also `leader_board.deployments` in the manifest).
- `--json` lists every contributing issue, request and usage id; the text view truncates usage ids.

## The three windows

`1d` = preceding 24 hours, `1w` = preceding 7 days, `1m` = preceding 30 days (a trailing window, not a
calendar month). All three, and their immediately preceding comparison windows, share one cutoff;
membership is `[start, end)`. The views overlap and are not additive. Each operator row carries its own
trend versus the preceding window.

## How to read it

- **Throughput** counts distinct outcomes whose first acceptance (`first: true`) is in the window,
  credited to the accountable owner at that moment (historical assignment intervals). A reopen and
  reacceptance appears only under quality follow-up, never as new throughput. Parents roll up children
  without adding a count. Routine action requests are reported separately.
- **Distributions** print `n` beside median and p90 (nearest rank, rank = ceil(0.9 n)); `n < 5` is
  labelled `small sample`. Team percentiles are pooled observations, never an average of operator medians.
- **Tokens**: "consumed in window" belongs to the consuming operator. "Lifetime tokens per accepted
  outcome" uses each outcome's whole attributed usage up to the cutoff (before the window, other
  operators, later repairs) for outcomes first accepted in the window: mean, median, p90. Period tokens
  are never divided by period completions. A handoff keeps each operator's own consumption.
  Lead overhead and unallocated usage stay visible beside attributed usage; nothing is allocated.
- **Coverage**: the header states cutoff, timezone, source freshness and `collection complete: yes|no`
  with the reasons. Outcomes whose early usage was not collected are a separate "partial coverage" cohort
  and are never mixed into the complete-coverage comparison. `unavailable` (with the missing source
  named) and `partial` are never zero.
- **Queue panel** (as of the cutoff, includes items older than any window): open and unacknowledged
  blockers, oldest blocker age, overdue requests, requests never responded to, unscheduled requests,
  active work, outstanding reviews.
- **Scheduled-request reliability** keeps the original target after rescheduling. A date-only target is
  due at the end of that UTC day.
- **Service panel**: DORA measures per service, only from supplied deployment records; otherwise
  `unavailable`.

There is no composite productivity score. Raw throughput or token ordering is not individual
performance; read it with work type, coverage and sample size.
