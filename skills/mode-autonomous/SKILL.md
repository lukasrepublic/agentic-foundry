---
name: mode-autonomous
description: The autonomous implementation driver (/foundry:mode-autonomous). WRAP composing the native /loop (outer session cadence) + the foundry-release-wave Workflow (per-wave fan-out) + the native merge floor (ci.yml + btb-gates). Replaces impl-wizard's impl-progress.yaml wave-state with the Workflow journal + native scheduling. Trigger to drive an authorized release's atoms toward merge (the auto-merge grant is withdrawn until a real server-enforced required-status is live — the operator merges, or the git-discipline hook's checks-green clause governs an agent merge).
---

# /foundry:mode-autonomous

The autonomous driver. Replaces the bespoke `impl-wizard`
wave-state engine (`impl-progress.yaml` — the
silent-halt source) with native primitives:

- **outer cadence** → native **`/loop`** (or `ScheduleWakeup`): one tick per wave; the
  loop survives session boundaries and resumes from the durable state (the release
  manifest + the native-floor verdicts — the `ci.yml` battery + `btb-gates`), not an
  uncommitted YAML counter.
- **per-wave fan-out** → the native **`Workflow`** tool via `workflows/release-wave.js`
  (`implement → verify` pipeline, journaled, `resumeFromRunId`). **SINGLE-REPO only.** For a
  **MULTI-REPO** release (atoms whose authorized contract `target_repo` is a product-repo KEY),
  the `Workflow` sandbox cannot write the per-spawn dispatch manifest, so drive the
  wave as **per-atom `/foundry:dispatch` on the `/loop` cadence** (each writes its manifest
  before the spawn); the `WorktreeCreate` hook redirects the worker into the product repo and
  the native floor (ci.yml + btb-gates) runs against that product repo's own PR. See
  `/foundry:dispatch` → *Multi-repo dispatch*.
  Native `Workflow`/`Agent` fan-out is the unconditional DEFAULT (feat-foundry-dispatch-on-native-
  workflow, AC-DNW-1/-2); the bespoke process-spawn fallback (`foundry-fanout`/
  `foundry-spawn-worker` + the `FOUNDRY_DISPATCH` switch) was REMOVED
  — the driver has exactly one path.
- **merge authority** → **an earlier realignment release: the former `noninteractive`
  auto-merge grant is WITHDRAWN.** The merge-gate PASS verdict it hinged on no
  longer exists — the merge signals are now the native floor (`ci.yml`'s pytest battery +
  graph selftests + doctor, plus `btb-gates`' `spec-link`/`security-path` checks), which is
  server-side required on this repo's `main`. The earlier claim here — that a preflight found
  branch protection unavailable on this plan — was STALE; protection is applied. It is CLASSIC
  protection rather than a ruleset, so `scripts/foundry_tier_preflight.py` reads it as
  `TIER-B (classic-protection)`: unverifiable from a read-only token, NOT advisory. **The exact
  required set is deliberately NOT restated here** — `skills/init/SKILL.md` step 5 is the single
  source, and a live configuration copied into two files is a claim that rots in one of them.
  Server-side checks are still not a grant of unattended self-merge — they bound what CAN merge,
  not who DECIDES to: **the operator merges**, or an agent merge is governed by
  `hooks/foundry-git-discipline.sh`'s deterministic `gh` clause (`gh pr merge --admin` is BLOCKED
  outright; a plain `gh pr merge <n>` is allowed only when `gh pr checks <n>` reports every check
  passing). **The auto-merge grant nonetheless remains WITHDRAWN.** Restoring it is an
  authorization change, not a consequence of this correction, and it is the operator's to
  make — see `docs/merge-floor.md`. Until then the operator merges, or the `gh` clause governs.

## When to trigger

- "drive `<release>` autonomously", "/foundry:mode-autonomous `<release>`", or the operator engages an autonomous loop over an authorized release.
- **Precondition:** every atom in the release is state `AUTHORIZED` (the front-authorization gate). Un-authorized atoms are never driven.

## Procedure

1. **Resolve the release** → its ordered list of AUTHORIZED atom specs (the durable
   work-list; no `impl-progress.yaml`).
2. **Per wave, run the fan-out Workflow:**
   ```
   Workflow({ name: "foundry-release-wave", args: { /* this wave's authorized atom specs */ } })
   ```
   Each atom flows `implement → verify` independently (native concurrency cap + journal).
3. **Native floor + merge authority.** For each atom PR, confirm the native floor is GREEN
   (the `ci.yml` command battery on the candidate branch + the `btb-gates` `spec-link`/
   `security-path` checks — Tier B advisory, honestly labeled, never a blocking required
   status on this repo). **The auto-merge grant is WITHDRAWN** (an earlier realignment release): a green native
   floor is a signal, not a merge authorization. Either the **operator merges**, or an agent's
   `gh pr merge` attempt is itself governed by `hooks/foundry-git-discipline.sh`'s deterministic
   `gh` clause — `--admin` is BLOCKED outright, and a plain merge is allowed only when
   `gh pr checks` reports every check passing. The `sd-review` / `pr-reviewer` pass is
   **advisory** (a mistake-catcher for the operator), never a merge approval — a review finding
   never gates or triggers the merge. The driver never relaxes any floor.
4. **Advance.** State is derived from merged-PR facts + gate verdicts (machine-derived,
   never a hand-written counter) — so a killed/resumed loop re-derives where it is and
   cannot silently halt on a reverted counter.
5. **Closeout** when every atom is merged + walked. Emit the release closeout.

## Inputs / Outputs

- In: an authorized release (atom list + per-atom `acceptance-contract.yaml`).
- Out: merged atom PRs (gate-passed) + the release closeout; no bespoke wave-state file.

## Fork policy — two-way-door auto-answer (AC-AFP-2/-3)

The retrospective measured a single AskUserQuestion stalling an overnight autonomous run for
**10.15 hours**. WHEN the session posture is an autonomous lane AND `fork_policy=two-way-auto`
(resolved via `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_session_mode.py" resolve-fork-policy`),
at every question fork (an AskUserQuestion the driver would otherwise raise) the driver SHALL:

1. **Classify the fork's reversibility** — two-way-door (reversible: cheap to undo, e.g. a default
   config value, a naming choice, a non-destructive scope narrowing) vs one-way-door (irreversible:
   a destructive op, a public release, a scope widen) vs unknown.
2. **Carve-out — always park for the operator regardless of `fork_policy`** when the fork is:
   - **security-flagged** (touches auth / secrets / supply-chain),
   - **authorization-adjacent** (authorize / re-authorize / merge authority / scope widen), or
   - **classified irreversible** (one-way-door), or
   - **classified UNKNOWN** — fail-closed on ambiguous classification: unknown reversibility ⇒
     park, never guessed as reversible.

   This parking rule is **instruction-level defense-in-depth, not the invariant carrier**: the
   actual invariant is held by the deterministic downstream floors — an auto-answered
   authorization-adjacent fork CANNOT effect authorization or a merge, because the authorize path
   requires the operator ceremony and the self-authorization classifier blocks agent self-authorization
   regardless of what a driver answers. This section grants no authority; it only decides which
   *advisory* forks proceed without stopping the loop.
3. **For a genuine two-way-door fork** (none of the carve-outs above apply): choose the
   recommended option (else the most-reversible option), record the auto-answer (AC-AFP-4):
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_session_mode.py" record-auto-answer \
     --question "<the fork's question>" --options '["opt-a","opt-b"]' \
     --chosen "<the chosen option>" --rationale "<why it's two-way-door + why this option>"
   ```
   and **continue the loop without stopping** — a genuinely reversible, non-carve-out fork under
   `two-way-auto` never surfaces as a stopped AskUserQuestion turn.
4. WHEN `fork_policy=park` (the default) or the session posture is not an autonomous lane, every
   fork parks for the operator exactly as today — this section only activates under the explicit
   opt-in.

Every auto-answer is appended, append-only, to `.foundry/auto-answers.jsonl` (interim surface for
the next operator touchpoint, pending the run-state summary consumer — the operator can inspect it
directly; see `skills/mode/SKILL.md`).

## Anti-patterns

- **Re-introducing `impl-progress.yaml` / a hand-written wave counter.** State is
  derived from merged-PR + verdict facts; the Workflow journal + `/loop` carry resume.
- **Driving an un-authorized atom**, or **self-merging (including a `gh pr merge --admin`
  bypass) on an advisory-only native-floor signal.** The auto-merge grant is withdrawn
  pending an operator decision (server-side protection being live does not itself restore it);
  the operator merges, or `foundry-git-discipline.sh`'s `gh` clause governs it.
- **Hand-rolling the per-wave iteration** — it's the `Workflow` tool.
- **Auto-answering a security-flagged, authorization-adjacent, or ambiguous fork.** The carve-out
  is closed and fail-closed — never widen it, never treat an unclassifiable fork as reversible.
- **Treating the auto-answer as authorization.** It is advisory only; the operator ceremony +
  the self-authorization classifier are the only invariant carriers.
