---
name: mode
description: Set the session operating posture (/foundry:mode <name>) — set mode <name> — a closed set of exactly {factory, noninteractive, interactive}, default factory (the heavy, spec-first, fully-gated lane); noninteractive and interactive are the light lane. Trigger on "switch to noninteractive mode", "go interactive", "back to factory", "factory mode", "set mode noninteractive", "what mode am I in". Persists for the rest of the session (survives /compact) and is surfaced on the statusline (⚙️ factory / ⚡ noninteractive / ⏸ interactive). Posture-only — does NOT relax any gate, change authorization, or enable self-merge. ALSO sets the session's fork policy (/foundry:mode --fork-policy <value>) — set fork policy <value> — a second closed set of exactly {park, two-way-auto}, default park; two-way-auto lets the session auto-answer a reversible question fork instead of stopping (security/authorization-adjacent/irreversible forks always park). Trigger on "set fork policy two-way-auto", "set fork policy park", "what is my fork policy". Surfaced on the statusline appended to the mode glyph, e.g. ⚡ noninteractive+auto.
---

# /foundry:mode — the session posture selector

A first-class **session posture** — exactly three modes,
**`factory`** (the full lane: front-authorization → build → the merge floor → certification),
**`noninteractive`**, and **`interactive`** (the light lane; the who-merges differentiation between
the latter two is a downstream atom — M1 stores and surfaces the posture only).

## When to trigger

- Explicit: `/foundry:mode <name>` (e.g. `/foundry:mode noninteractive`).
- Natural language: "switch to noninteractive mode", "go interactive", "back to factory",
  "factory mode", "set mode noninteractive", "what mode is this session in".

## Procedure

1. Determine `<name>` from the request (`factory`, `noninteractive`, or `interactive`).
2. Call the store:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_session_mode.py" set <name>
   ```
   - On success, the script prints the resulting active mode on stdout (exit 0) — echo it back to
     the operator, e.g. "Session mode set to `noninteractive`."
   - On an unknown `<name>` (outside the closed set), the script rejects (exit 1, `REJECTED: ...`
     on stderr naming the three valid modes) and the active session mode is **left unchanged**.
     Report the rejection and the valid set to the operator; do not retry with a guessed value.
3. Re-issuing the same `<name>` is a no-op on state and still echoes the mode (idempotent).

## What this does NOT do

- **Relax any gate, change authorization, or enable self-merge.** The posture is stored + surfaced
  only; downstream mode-aware behaviors read it to differentiate the lane's merge authority.
- **Drive an implementation loop.** Use `/foundry:dispatch` and the native `/loop` for that.

## Anti-patterns

- **Inventing a fourth mode value** or accepting a near-miss spelling — the closed set is exactly
  `factory` / `noninteractive` / `interactive`; anything else is rejected, never coerced.
- **Silently falling back** on a rejected set-mode request — always surface the rejection + the
  valid set to the operator.

## What each posture means in practice (the three-mode model)

- **`factory`** — the default posture: a ticket with a runnable `Done means` is the unit of work
  (`context/operating-model.md`); branch protection + CI is the gate; the operator merges.
- **`noninteractive`** — one ticket built in an isolated worktree → PR (CI green + fresh-context
  review) → operator merges (or `/foundry:merge-when-green` on an explicit instruction). A
  `security: true` change, or one whose scope names auth, secrets, custody, a production
  mutation, or a cross-repo pin, takes the opt-in spec lane instead (`/foundry:intake`,
  `/foundry:authorize`) — see `skills/intake/SKILL.md` "Lane routing".
- **`interactive`** — zero-process vibe/debug session. No ticket needed for exploration; work
  lands by ordinary commit/PR at the operator's discretion. Git discipline (protected `main`, no
  destructive ops) still applies — it is floor #4, not ceremony.

## Native permission-mode pairing (recommendation, not enforcement)

The posture does NOT drive Claude Code's native permission mode — it cannot: hooks receive
`permission_mode` as read-only input, and no framework surface can set it (verified against the
Claude Code docs, 2026-07-27; this retired the planned "posture as thin display over native
permissionMode" atom as unimplementable). The two are **paired by convention** instead:

| posture          | recommended native mode   |
|------------------|---------------------------|
| `factory`        | `plan` → `default`        |
| `noninteractive` | `acceptEdits` (or `auto`) |
| `interactive`    | `acceptEdits`             |

Suggest the pairing when setting a posture (one line, e.g. "consider Shift+Tab → accept-edits
for this posture"); a workspace can pin its default via `permissions.defaultMode` in
`.claude/settings.json`.

## Fork policy (two-way-door auto-answer) — AC-AFP-1/-5

A second, orthogonal closed-set field on the **same** session-mode store: `fork_policy` ∈
`{park, two-way-auto}`, default `park`. Governs whether the session auto-answers a
reversible ("two-way-door") question fork instead of stopping to ask (security-flagged,
authorization-adjacent, or irreversible forks always park, fail-closed on ambiguous
classification).

### When to trigger

- Explicit: `/foundry:mode --fork-policy <value>` (e.g. `/foundry:mode --fork-policy two-way-auto`).
- Natural language: "set fork policy two-way-auto", "set fork policy park", "what is my fork
  policy".

### Procedure

1. Determine `<value>` (`park` or `two-way-auto`).
2. Call the store:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_session_mode.py" set-fork-policy <value>
   ```
   - On success, prints the resulting fork policy on stdout (exit 0) — echo it back, e.g.
     "Fork policy set to `two-way-auto`."
   - On an unknown `<value>` (outside `{park, two-way-auto}`), the script rejects (exit 1,
     `REJECTED: ...` on stderr naming the two valid fork policies) and the stored fork policy is
     **left unchanged**. Report the rejection to the operator; do not retry with a guessed value.
3. Read back:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_session_mode.py" resolve-fork-policy
   ```
4. Persists with the posture (survives turns and `/compact` — same on-disk store, same record).
5. Surfaced on the statusline appended to the mode glyph when active, e.g. `⚡ noninteractive+auto`
   — the operator always sees when a session may auto-answer.

**Posture is advisory, not authority.** `two-way-auto` only changes which reversible question
forks the session auto-answers; it grants no authorization and does not touch the merge
floor or the self-authorization classifier.

**Interim surface for auto-answers.** Every auto-answer a session makes is appended, append-only,
to `.foundry/auto-answers.jsonl` — inspect that file directly.
