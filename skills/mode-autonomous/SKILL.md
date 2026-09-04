---
name: mode-autonomous
description: The autonomous implementation driver (/foundry:mode-autonomous). WRAP composing the native /loop (outer session cadence) + the foundry-release-wave Workflow (per-wave fan-out) + the native merge floor (ci.yml + btb-gates). Replaces impl-wizard's impl-progress.yaml wave-state with the Workflow journal + native scheduling. Trigger to drive an authorized release's atoms toward merge (the auto-merge grant was RESTORED 2026-08-13 by operator decision, bounded by the git-discipline hook's checks-green clause — --admin stays blocked outright and a plain merge needs every check passing). NOT /foundry:command-deck, which is the PROGRAMME-level clock — a recurring watcher armed over one release that re-measures, dispatches and reports once per tick; reach for this one when you are driving an already-authorized release's atoms through implementation right now, and for the command deck when you want a programme watched unattended.
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
  auto-merge grant was WITHDRAWN (see the 2026-08-13 restoration below).** The merge-gate PASS verdict it hinged on no
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
  passing). **The auto-merge grant remained WITHDRAWN until 2026-08-13 — see the restoration immediately below.** Restoring it was an
  authorization change, not a consequence of this correction, and it is the operator's to
  make — see `docs/merge-floor.md`.

  **RESTORED 2026-08-13 (operator decision, recorded in the command-deck-watcher authorization).**
  The auto-merge grant is **RESTORED**, bounded by the `gh` clause above rather than by anything new:
  `--admin` stays blocked outright, and a plain `gh pr merge <n>` is allowed only when
  `gh pr checks <n>` reports every check passing. The driver may therefore land its own atoms on an
  **affirmative** success conclusion from the forge for the head commit — and on nothing else. An
  absent, empty, pending, `neutral` or `skipped` conclusion is NOT evidence, and neither is any result
  the worker that produced the commit reports about itself (`feat-foundry-fleet-command-deck-watcher`,
  AC-CDW-10).

  **AC-CDW-10 IS STRICTER THAN THE HOOK, AND THAT GAP IS THE DRIVER'S TO CLOSE.** The hook blocks on a
  non-zero `gh pr checks` exit or any `fail`/`pending` row — but `gh pr checks` **exits 0 when every
  check is `skipped` or `neutral`**, and neither word matches its convicting pattern. So a PR whose
  required contexts were all skipped (a `paths-ignore` filter, say) passes the hook having run nothing.
  The hook is the floor, not the ceiling: **before merging, call
  `foundry_command_deck.may_land(<conclusion>)` and land only on `success`.** The empty-check-set case
  is the one the hook does cover — a repo with no CI makes `gh pr checks` exit non-zero, the clause
  refuses, and the merge is correctly the operator's.

## The tick contract (feat-foundry-fleet-command-deck-watcher)

The driver above says WHAT to compose. This says **when to wake, what a tick does, and when there is
nothing to do** — the part whose absence made it run once in thirty days while 37 of 52 resumes were
the agent stopping silently after finishing work.

**Resolve the programme first.** `/foundry:command-deck <programme>` — or this skill invoked with a
programme name. Resolution goes through `scripts/foundry_command_deck.py`, which delegates to
`foundry_release.load_release` (slug-only, containment-checked). **Given no programme that resolves to
exactly one release manifest, list the in-flight programmes and drive nothing** — never guess, because
`ready_set` auto-starts what resolution returns and a near-miss name would start a different
programme's atoms unattended.

**Each tick, in order:**

1. **Census.** Re-derive the ready-set: `foundry_command_deck.ready_set(...)`. It is recomputed every
   tick from the manifest and the Task graph, so an atom that becomes ready on the hundredth tick is in
   the hundredth tick's set. It excludes any atom whose contract does not **re-derive as authorized**,
   and it respects the wave barrier, so file-overlapping atoms never start concurrently.
2. **Act on completion in the same tick.** A finished worker is verified, landed, or followed up in the
   tick that observes it — never noted for later.
3. **Reconcile the native Task graph.** It is the run state, together with the release manifest it is
   regenerable from. Regenerate it only when it is **absent**: a present graph's transitions ARE the
   started-ness, and regenerating a present graph destroys them. Build no second tracker.
4. **Start what is ready**, up to the wave barrier.
5. **Emit one executive status**: what was accomplished, what is next, what is blocking. Terse,
   imperative, once.
6. **Schedule the next wake.** `foundry_command_deck.wake_seconds(...)`. Harness-tracked work
   re-invokes the session on completion, so polling for it is waste — use the long fallback heartbeat
   (>= 1200s) to survive work that hangs or never notifies. Use a matched, shorter interval only for
   state the harness cannot observe (a CI run, a deploy, an external queue). When both are awaited, the
   shorter wins.
7. **Idle honestly.** A tick is idle **iff the ready-set is empty AND no worker is running** —
   `foundry_command_deck.is_idle(...)`, a predicate, not a judgement. On an idle tick say so in one
   line and stop. **Create, dispatch and record nothing.** This is load-bearing, not politeness: a loop
   with nothing to do invents work to look busy, and a fabricated task in a governance programme is
   worse than an idle tick.

### Landing, and what counts as evidence

Land on the **forge's own affirmative success conclusion for the head commit** and on nothing else —
`foundry_command_deck.may_land(<conclusion>)`, which returns true **only** for `success`. The
git-discipline hook is the floor, not the ceiling: it admits a PR whose checks were all `skipped` or
`neutral`, because `gh pr checks` exits 0 for those. Closing that gap is the driver's obligation, not
the hook's. A task notification, a tool result, or your own prior message is **never** evidence that
checks passed, and never operator consent.

**Merged is not applied.** An atom with a live surface is not complete while the deploy observation for
its merged commit reports the artifact stale or not rolled (`/foundry:deploy-status`, which already
cross-checks deployed-artifact identity against the expected merged commit). Report the two states
distinctly; an SCP was once coded, reviewed, gated and merged and was still not in effect days later.

**Walk preconditions to ground before promising a completion.** The declared dependency list is not the
precondition list — an apply reported "one command away" was not, because the state backend it needed
sat in a different, still-open, red PR.

### Handing a command to the operator

Any command that may be refused is issued **alone**. Preconditions run as separate, independently
verified steps. Hand over **exactly one self-contained command**, only after verifying every
precondition is actually in place, and state **which guard refused it and why**. The failure this comes
from: a precondition was chained into the same invocation as a mutating command, the classifier denied
the whole thing so the precondition never ran, and the operator was handed a command that assumed it
had — it reported success and did the wrong thing.

Afterwards, **verify the outcome against the world**, not against the report of success: a terminal
once said "successfully initialized" while the state had not migrated, and only listing the bucket
revealed it was empty. **If verifying needs the same capability that forced the handover, say so and
stop** — never report an unverified outcome as verified.

### Escalate on a closed set

Surface a fork **only** for (a) external provisioning you cannot perform, or (b) a fork the session's
fork policy parks. Resolve everything else by prior-art research (`research-first`) and record it.

This is the atom's reason for existing, and it is measured rather than stylistic: dispatched workers
cost 0.22-0.54 operator interventions per 100 turns; **decks cost 10.66-12.07** — a 20-50x
concentration, recorded as *"the deck became the inbox."* Of the three standard ambient-agent
human-in-the-loop patterns, this driver keeps **notify** (the executive status, which asks for nothing)
and deliberately does not offer **question** or **review** as standing surfaces.

**Recommend against interest.** When the evidence does not support what was asked for, say so and give
the strongest case for the rejected option. A recommendation that never contradicts the operator is not
a recommendation.

**Correct the record where it lives.** When a later finding falsifies an earlier one, amend it at its
source with the superseded text struck and marked — never leave a corrected claim standing in one
document and fixed in another.

**Relax no floor; grant yourself no authority.** Never widen an authorized surface to close a finding:
park it, with its tradeoff and what would unpark it.


## When to trigger

- "drive `<release>` autonomously", "/foundry:mode-autonomous `<release>`", "/foundry:command-deck `<programme>`", "watch this programme", or the operator engages an autonomous loop over an authorized release.
- **Given no programme, or one that does not resolve to exactly one manifest:** list the in-flight programmes and drive nothing (AC-CDW-1).
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
   `security-path` checks — server-side REQUIRED on this repo's `main`; see `skills/init/SKILL.md`
   step 5 for the enumerated set. The earlier "Tier B advisory, never a blocking required status"
   wording here was stale). **The auto-merge grant was RESTORED 2026-08-13** (operator decision; see the header): a green native
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
  bypass) on an advisory-only native-floor signal, or on a conclusion that is not an affirmative success.** The restored grant is bounded
  by `foundry-git-discipline.sh`'s `gh` clause **plus** the affirmative-success rule that clause does
  not carry: land on `success`, never on a `skipped`/`neutral` conclusion the hook happens to admit.
- **Hand-rolling the per-wave iteration** — it's the `Workflow` tool.
- **Auto-answering a security-flagged, authorization-adjacent, or ambiguous fork.** The carve-out
  is closed and fail-closed — never widen it, never treat an unclassifiable fork as reversible.
- **Treating the auto-answer as authorization.** It is advisory only; the operator ceremony +
  the self-authorization classifier are the only invariant carriers.
