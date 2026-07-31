---
name: learn-capture
description: The direct/lean interactive-session learnings PRODUCER (/foundry:learn-capture). Normally automatic — an enforced once-per-session Stop hook injects a reflection turn that distills the session and emits records via the capture CLI into the .foundry/session-learnings partition /foundry:learn-distill consumes. This skill documents that mechanism + the manual capture escape hatch. Trigger to capture the current session's learnings on demand, or to understand the Stop-reflection producer.
---

# /foundry:learn-capture

The third learnings PRODUCER (feat-foundry-session-learnings-capture) — for the plain operator-driven
direct/lean session that returns no value, spawns no worker, and leaves no worktree. Siblings: the
worker structured-return + worktree-sidecar producers (`foundry-harvest-learnings.sh`). Consumer:
`/foundry:learn-distill` (unchanged; the schema authority).

## How it works (normally automatic)

- An **enforced `Stop` hook** (`hooks/foundry-session-learnings.sh stop`, wired in `hooks.json`) fires
  when the session goes idle and — **once per session**, re-entrancy-guarded by `stop_hook_active` +
  a per-session marker, **interactive sessions only**, and only for a **substantive** session (see the
  substance gate below) — injects a single **reflection turn**.
- **What you see, and why it says "error".** The reflection arrives as a **blocked `Stop` hook**, and
  Claude Code captions *every* blocked `Stop` hook as an **error** — including this one, which is
  working exactly as designed. That caption is upstream's
  ([anthropics/claude-code#12667](https://github.com/anthropics/claude-code/issues/12667),
  [#34600](https://github.com/anthropics/claude-code/issues/34600),
  [#62139](https://github.com/anthropics/claude-code/issues/62139)) and Foundry cannot change it. What
  Foundry *does* control is the contents: the operator-facing `reason` is **two short lines** that say
  so plainly, while the model-facing runbook rides in `hookSpecificOutput.additionalContext` where it
  costs you no screen space (AC-RUX-1). **Nothing is wrong when you see it.**
- The model distills the session into learning records (JSONL) and emits them via the **bare PATH
  command** `foundry-learn-capture` (added to the Bash tool PATH while the plugin is enabled — no
  version in the path, no `$CLAUDE_PLUGIN_ROOT`), with an absolute-path fallback for the post-update
  window:
  ```
  if command -v foundry-learn-capture >/dev/null 2>&1; then \
    cat <records.jsonl> | foundry-learn-capture --final --session-id <id> --project-dir "$CLAUDE_PROJECT_DIR"; \
  else \
    cat <records.jsonl> | "${CLAUDE_PLUGIN_ROOT}"/hooks/foundry-session-learnings.sh capture --session-id <id>; \
  fi
  ```
  (records on **stdin**, never on argv). Zero records is fine for a low-value session.
- **CLI-only write path (AC-LBC-3).** Emit records ONLY through this capture CLI (which validates each
  line and partitions the write) — **never write a file directly under .foundry/session-learnings/**. A
  hand-written file the distiller cannot read (wrong shape, or in the buffer root instead of a
  `YYYY-MM-DD/` partition) is **silently lost** (the doctor `buffer-drift` signal this line originally named was retired with the drop-in registry — check the partition by hand); it counts as any
  residual. A non-object/unparseable line piped to the CLI is **rejected + counted** (content-free), not
  written.
- The shared writer lands them content-addressed + idempotent in
  `.foundry/session-learnings/<date>/<sid>__session__<hash>.jsonl` (channel `session`), through a
  **mechanical secret-scrub** (best-effort defense-in-depth — see AC-SLEARN-7), fail-open,
  with a reconciliation row in `.harvest-log.jsonl` (the per-channel doctor surfacing this line originally named was retired with the drop-in registry).

## Cadence + the off switch (`FOUNDRY_SESSION_LEARNINGS`)

**The substance gate (AC-RUX-2).** A session that provably did nothing worth reflecting on never
reflects. The hook reads the harness-supplied transcript and treats the session as **substantive** if
**any** of: a mutation tool call (`Edit` / `Write` / `MultiEdit` / `NotebookEdit`); a delegation
(`Agent` / `Task` / `Workflow`); or at least **`FOUNDRY_SESSION_LEARNINGS_MIN_TURNS`** genuine user
turns (default **3**). A one-shot read-only probe — `/foundry:doctor` and friends — is therefore
silent. `Bash` is deliberately *not* a substantiveness signal (classifying it would mean parsing
arbitrary shell); a real `Bash` session qualifies on turn count instead.

The gate is conservative in exactly one direction: an **absent, unreadable, or unparseable** transcript
counts as **substantive**. It can only ever *suppress* on positive proof of insubstantiality — a read
failure can never silently switch capture off.

**The knob (AC-RUX-3).** Claude Code has **no per-hook disable** — only `disableAllHooks` (which would
also disable the git-discipline and cwd-enforce hooks) or uninstalling the plugin. So Foundry ships its
own; **reach for this, not `disableAllHooks`**:

| `FOUNDRY_SESSION_LEARNINGS` | Behavior |
|---|---|
| `off` | Never reflect. Silent no-op; no marker written, so re-enabling mid-session loses nothing. |
| `full` | Always reflect when the classic guards pass — bypasses the substance gate (pre-v0.27 cadence). |
| unset / `gated` / anything else | **Default.** The substance gate applies. An unrecognized value degrades to this, never to an error. |

Set it per-session in your shell, or persistently via the `env` block in `.claude/settings.json`.

## Public programmatic entrypoint — `foundry-learn-capture`

A **consumer skill body** (model-invoked Bash, NOT a hook) that wants to append **one** learning
record the moment an insight is filed uses the PATH-installed public command
**`foundry-learn-capture`** (`bin/foundry-learn-capture`, auto-added to the Bash tool PATH while the
plugin is enabled). It reads records as JSONL **on STDIN only** (never argv) and self-resolves its
shared library relative to its own location (no `$CLAUDE_PLUGIN_ROOT`, no version path).

**Consumer-binding contract (REQUIRED).** Because a skill-body Bash call does NOT reliably export
`CLAUDE_SESSION_ID` / `CLAUDE_PROJECT_DIR`, a consumer skill body **MUST pass `--session-id` and
`--project-dir`** (supplied via skill/command-file inline `${…}` substitution, not the Bash env):
```
printf '%s\n' '{"lesson":"…","context":"…"}' \
  | foundry-learn-capture --session-id "<sid>" --project-dir "<workspace-root>"
```
- Default mode is **`append`** — it writes records and does **not** touch the once-per-session
  reflection marker (so a mid-session append can never suppress the session's own `Stop` reflection).
- `--final` is the explicit closer (the `Stop` reflection turn) — it writes records then CASes the
  marker `requested`→`done` (and refuses to manufacture a `done` from an absent marker).
- An **unresolved** `--project-dir` (and no `$CLAUDE_PROJECT_DIR`) **fails closed** (a content-free
  stderr loss signal, nothing written) rather than writing into the wrong repo; an unresolved
  `--session-id` fails closed in `--final` (records still written) and warns in `append`.
- `--strict` makes a reject/loss/fail-closed-root exit non-zero (default is fail-open exit 0).

## Manual capture (escape hatch)

Run the capture CLI yourself to record a learning on demand (same partition/format):
```
printf '%s\n' '{"lesson":"…","context":"…"}' \
  | "${CLAUDE_PLUGIN_ROOT}"/hooks/foundry-session-learnings.sh capture --session-id "$CLAUDE_SESSION_ID"
```

## Notes / anti-patterns

- **Do not put secrets in records.** The producer scrub is best-effort; the authoritative control is
  operator review at the public boundary (`/foundry:upstream-submit`). Distill *lessons*, not raw spans.
- **Not per-turn, but re-armed per manual `/compact`.** Reflection fires at the first qualifying idle and
  is then **re-armed at each MANUAL compaction boundary** by the `PreCompact` hook
  (`foundry-session-learnings.sh precompact`), which resets the per-session marker to `rearmed` so the
  next idle re-injects — once per compaction SEGMENT rather than once per whole session (the operator's
  real episode boundary). Auto-compaction does NOT re-arm (no system-triggered amplification). See
  `its authorizing spec`.
- **A session that dies before its first idle (or before its next idle after a `/compact`) loses that
  segment's learnings** — the accepted bounded residual of the inline-only design (the post-hoc fallback
  was dropped after the deep spec audit; matches inline-only systems), now strictly smaller thanks to the
  per-compaction re-arm.
- Self-test the producer: `hooks/foundry-session-learnings.sh --selftest`.
