---
name: fleet
description: The single-pane session roster (/foundry:fleet) — every active Claude Code session joined to its foundry work-context (epic/atom/governance), over the NATIVE session list. Read-only/advisory. Trigger to see all your parallel sessions at a glance with their foundry context, or when supervising many sessions and asking "what is each one doing / which need a decision".
---

# /foundry:fleet

The operator's single-pane view for supervising many parallel sessions (the cognitive-load surface).
A **thin overlay** over native Claude Code session machinery — it does NOT rebuild what the harness
ships (`claude agents` already enumerates sessions + their needs-input state + a summary). Foundry adds
the one thing native cannot know: each session's **work-context** — which release/feature/atom it
serves, its governance state, and any operator-reported why/pending-decision/blocker.

## What it shows

`scripts/foundry-fleet-roster.py` renders one row per native session (via the session-registry):
- **native base** (harness-authoritative): session id, repo (cwd), branch, name, state, a best-effort
  liveness value (native `updated_at`, "—" when absent — no fabricated "live", no dead-PID stale flag);
- **foundry overlay**: epic/atom, governance state, a `◀` **pending-decision** marker;
- **headline machinery scan columns** (joined from the `session-machinery` overlay by `session_id`):
  `ISO` (isolation), `GATE` (gate/merge-readiness), `WF·step` (workflow+stage). These are **DEFAULT-DENY**:
  a value renders clear **only** when it is in that field's explicit known-safe set (e.g. `GATE` clear only
  for a derived `authorized`/`gate_pass`/`merged`; `ISO` clear only for `worktree`); every other value —
  `unknown`, `gate_block`, `direct_main`, `worktree_on_main`, a null/un-derived risk value, **or any
  novel/out-of-vocabulary token** — renders `⚠` (attention), never blank-as-green. A row whose machinery
  block is entirely missing renders `⚠ machinery unavailable`. `WF·step` is honestly **sparse** — the
  workflow pointer is the invoking session's own, so it shows for that row and "—" for others. The fuller
  machinery (mode, security-flag, infra blast radius, target-repo) is **drilldown** in `--json`;
- a one-line summary = the native Haiku summary suffixed with the foundry atom/step.

It is **read-only** (renders, never acts; runs in **any** session — operator or worker — with no dispatch
queue, worktree, or special mode, and never trips worker-cwd-enforcement), **secret-scrubbed + sanitized
on EVERY rendered string** (the native summary/name/branch are session-content-derived → the
security-review floor; C0 **and** C1 control ranges + ANSI), **fail-closed** (if the native list can't be
read it shows a visible banner, never an empty "all clear"), and `--json` for scripting.

## Relationship to native (do NOT reinvent)

`claude agents` (and `claude agents --json`) is the authoritative session enumerator + the
needs-input/`N awaiting` grouping. `/foundry:fleet` **augments** it with foundry context; it never
re-hosts the native count or re-arbitrates liveness.

## Exercise

- `cli:foundry-fleet-roster` — `scripts/foundry-fleet-roster.py --selftest` (AC-ROST-1..7, hermetic).
- `scripts/foundry-fleet-roster.py [--json]` — the live roster (runs in any session).
