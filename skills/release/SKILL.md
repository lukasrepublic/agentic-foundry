---
name: release
description: The release lifecycle operator surface (/foundry:release, release-manager). Shape a release (release.yaml manifest + dependency graph), drive its backlog→planned→active→completed state machine, and CLOSE it through the evidence-derived closure gate (refused on assertion; re-derived per atom from authorized (recompute-match) + merged-on-main, fail-closed). Trigger to create/inspect a release, advance its state, or close it. Complements /foundry:authorize-release (gate-in) and release-wave/mode-autonomous (wave).
---

# /foundry:release

The release shaping (RA) + closure (RD) surface over `scripts/foundry_release.py`. A **release is foundry's
default operating unit** (atoms are driven as a release; a single feature is a release of N=1). Foundry ships
the middle — **RB** gate-in (`/foundry:authorize-release`) + **RC** the wave (`release-wave` /
`/foundry:mode-autonomous`); `release-manager` adds the **ends**: shaping + an evidence-derived closure gate.
This skill never re-implements the schema/loader/state-machine/closure — it drives `foundry_release.py`.

**Closure is re-derived, never asserted.** `active → completed` is **refused on operator assertion / no
`--force`**: it re-derives a per-atom CLOSED verdict from the SAME evidence a landed PR requires
(authorized recompute-match + merged-on-main — which subsumes the full per-atom floor, since nothing
reaches `main` without clearing the merge floor's checks) and closes only if EVERY
atom is CLOSED (else fail-closed naming
the unclosed atoms). This is the gate-enforcement-point principle — it holds for the trusted operator (it
prevents an honest "I think it's done" mistake from closing a release that isn't).

## When to trigger

- "shape a release", "/foundry:release shape `<id>`", "release status", "plan/activate/close the release".

## Verbs

The manifest lives at `$CLAUDE_PROJECT_DIR/.foundry/releases/<id>/release.yaml` (per-project operator state).

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_release.py" ...   # (driven via the loader API)
```

- **`shape <id>`** — **scaffold** a `release.yaml` template if none exists (the operator then authors the
  atom set + `depends_on` graph), else load + **validate** + show the existing manifest. Fail-closed on a
  malformed manifest (missing field, bad `state`, empty atoms, duplicate atom id, dangling `depends_on`, or a
  **dependency cycle**). Shows the topological atom order.
- **`status <id>`** — show the release state + per-atom evidence (`authorized` / `merged-on-main`) so the
  operator sees exactly what closure would re-derive.
- **`plan <id>`** — `backlog → planned`: re-derives that the graph is acyclic + every atom's spec + contract
  exist.
- **`activate <id>`** — `planned → active`: re-derives that **every atom is AUTHORIZED** (the frozen
  `authorized:` block's hashes recompute-match), fail-closed otherwise.
  Hand off to `/foundry:authorize-release` (gate-in) → `/foundry:mode-autonomous` (wave) for the atoms.
- **`close <id>`** — `active → completed`: the **evidence-derived closure gate** (above). No assertion path.
- **`run-state <id> [--summary] [--strict]`** — a per-atom, **machine-derived run ledger** (advisory;
  replaces hand-carried run-notes). Emits one YAML object per manifest atom —
  `{id, state, authorized, superseded, dispatched, pr, merged_on_main, walk_verdict, runnable,
  blocked_reason, probe_error}` — every field re-derived at call time from the SAME primitives
  `close` already uses (authorization recompute-match, merged-on-main contract-sha history match),
  plus supersession-as-state, the process-spawn dispatch-claim surface, and the dependency-gated
  `runnable` flag. `state` is exactly one of `{superseded, merged, dispatched, unauthorized,
  runnable, blocked, UNKNOWN}` — an unresolvable derivation input (missing target repo, unreadable
  contract, a git failure) reports `UNKNOWN` with the failing probe in `probe_error`, dominating
  every other bucket. `--summary` emits a compact <=2KB YAML digest (release pointer + all seven
  per-state counts + the next runnable atoms in manifest-DAG order); `--strict` exits non-zero if
  any atom is `UNKNOWN`. Read-only over the floor — it never writes specs/contracts/authorization/
  provenance/the manifest, and it never gates anything itself (the closure gate and
  `activate`/`close` above are untouched consumers of the same primitives).
- **`accept <id> --operator <op> --verdict {accepted|rejected} [--note "…"]`** — append a
  one-line PRACTICE acceptance record `{date, operator, verdict, note}` to the manifest's
  optional `acceptance:` list (the v0.25.0 certification realignment — see the Certification tail section below). A
  **PRACTICE record, never a gate**: it does not participate in `close`/`derive_closure`/any
  floor primitive above. Idempotent per `(operator, date, verdict)`; refuses an unknown release
  and a non-terminal verdict (the closed 2-value enum `accepted`/`rejected`).

Illegal transitions (skip a state / go backward) are fail-closed.

## Wave planning + the native Task graph

The manifest (`release.yaml`) is **the one planning surface** (CONSTITUTION.md §10) — every other
view of a release's plan (the wave grouping, the operator-visible Task graph) is a **generated
view REGENERATED from it**, never a second source of truth. Drive it in three steps, after
`activate` (every atom AUTHORIZED):

1. **Compute the wave plan.** Run the deterministic, pure planner over the manifest:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-wave-plan.py" \
     "$CLAUDE_PROJECT_DIR/.foundry/releases/<id>/release.yaml"
   ```
   Emits `{release_id, waves: [[atom-id, ...], ...], atoms: {<id>: {wave, spec_ref, contract_ref,
   depends_on, paths, journeys}}}` — atoms grouped by dependency closure **and** declared-path
   overlap (a same-wave sibling whose `paths` overlap is pushed to a later wave; see
   `skills/intake/SKILL.md`'s Journeys section for where `paths`/`journeys` come from on the
   manifest atom). `--check` validates without emitting (a pre-flight in CI or before dispatch). A
   dependency cycle is a **hard error naming the cycle** — fix the manifest, re-run; there is no
   partial/best-effort plan.

2. **Materialize the native Task graph FROM the wave plan.** For each atom in the plan's `atoms`
   map, `TaskCreate` one task: `blockedBy` = the atom's OWN Task ids for each name in its
   `depends_on` (the wave plan's DAG edges, carried over verbatim — never re-derived by hand), and
   `metadata = {spec: atom.spec_ref, wave: atom.wave, journeys: atom.journeys}`. The harness renders
   the graph; **`TaskUpdate` transitions ARE the run state** — there is no separate hand-carried
   ledger to keep in sync (the machine-derived `run-state` verb above already covers the
   authorization/merge evidence view; the Task graph covers the in-flight dispatch view). Because
   the manifest is the durable source, **re-running step 1 and re-materializing the graph is always
   safe** — a stale/abandoned Task graph is never a second ground truth to reconcile, it is thrown
   away and regenerated.

3. **Dispatch via `release-wave.js`, wave-barriered.** `release-wave.js`'s impl/verify prompts
   need each wave element to be an **atom SPEC PATH** (the same shape its original flat-array
   arg always required — `"Implement the AUTHORIZED atom ${atom} against its frozen
   acceptance-contract.yaml"` only makes sense given a path). The wave plan's `waves` are **atom
   IDS**, not paths — **map every id through `plan.atoms[id].spec_ref` before passing them as
   `args`**; do NOT pass `plan.waves` straight through (a bare id is not a valid dispatch target
   and `release-wave.js` now REJECTS it fail-closed — see below):
   ```js
   const argWaves = plan.waves.map(wave => wave.map(id => plan.atoms[id].spec_ref))
   Workflow({ scriptPath: "workflows/release-wave.js", args: { waves: argWaves } })
   ```
   `release-wave.js` now accepts this explicit wave-list shape (an array of arrays
   of atom **spec paths**) alongside its original flat atom-spec-path-array shape: every atom in
   wave *N* completes (implement → self-gate → verify) before wave *N+1* is dispatched — the ONE
   property a flat fan-out never had (a later wave's worker can never race a same-path
   earlier-wave worker still landing its PR). Within a wave, atoms still fan out via the same
   native `pipeline()` fan-out the flat shape always used — no new fan-out mechanism, just a
   sequential barrier between wave-scoped `pipeline()` calls. `release-wave.js` validates every
   wave element fail-closed (a path-shaped string — `^[A-Za-z0-9._/-]+$`, no `..` segment,
   contains `/`, ends `.md`) and REJECTS (logs the offending element, drops it from that wave's
   dispatch) anything that isn't — a bare atom id, an injected shell fragment, or any other
   non-path string is refused, never silently dispatched. A flat array (no `waves` key) still
   runs exactly as before (unchanged, byte-for-byte) for a caller that has no wave plan to hand.

## Certification tail — certify-local → operator acceptance → certify-staging

Once every atom in the release is merged (`close` re-derives CLOSED), drive the factory-mode tail
CONSTITUTION.md §V names in full: **"…build → integrate → certify locally → operator acceptance
→ staging."** Three steps, thin procedure + templates over native primitives — no verdict engine,
no evidence ledger this section re-invents:

1. **`/foundry:certify-local <id>`** (`skills/certify-local/SKILL.md`) — deploy the release ONCE
   locally via the active stack profile's boot recipe, run the FULL tagged journey suite (plain
   `npx playwright test --grep` over every atom's `journeys[]`) against that one instance, get
   per-atom pass/fail with the runner's own output as evidence. Refuses (never a vacuous pass)
   naming the missing prerequisite when there is no journey suite or no boot recipe.
2. **`/foundry:release accept <id> --operator <op> --verdict accepted --note "…"`** — once the
   operator has run their OWN test pass (the constitution's "operator sign-off is the terminal gate" principle — context/constitution-template.md §I.5, "Delivery sign-off — operator-held, the
   terminal step" — the certify-local evidence feeds this judgement, it does not substitute for
   it) and is satisfied, record it: a one-line PRACTICE acceptance note appended to the manifest
   (see the `accept` verb above). Never a gate — nothing above re-checks it, nothing downstream
   requires it to exist.
3. **`/foundry:certify-staging <id>`** (`skills/certify-staging/SKILL.md`) — emit the staging
   certification checklist (`context/staging-checklist-template.md`), observe deploy state via
   the kept `/foundry:deploy-status` surface, and STOP. Promotion beyond staging stays CD-owned
   and operator-gated — this tail never triggers it.

## Pre-cut acceptance gate (HARD-STOP)

Before cutting a release (the local `claude plugin tag --push` / marketplace re-pin), run the **release
acceptance-gate** over the to-be-released artifact and **refuse to cut on a `fail` verdict** — a **pre-cut
HARD-STOP**, the binding enforcement that sits between "cut" and "all clients update" (the CI `release-acceptance`
job is the earlier advisory signal). The gate validates the artifact the way a fresh client receives it, using
the tool's OWN first-party validators (NOT a re-implementation):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-release-acceptance.py" --tree <release-checkout>
```

It runs `claude plugin validate --strict` + `claude plugin tag --dry-run` (manifest + marketplace +
plugin↔marketplace version agreement + strict skill-frontmatter), asserts every `hooks.json` script is
executable (`os.X_OK`; it does NOT execute the hooks — they are fail-closed, side-effecting gates), and
requires the candidate tree's own `/foundry:doctor` to be `DOCTOR-GREEN`. A non-`pass` verdict ⇒ **do not cut**;
fix the surfaced defect first. (Recent releases shipped broken to all clients precisely because nothing
validated the artifact with the tool's own validators before the cut.)

## Gate-wiring pin & manifest — RETIRED (historical note)

The version-bound wiring pin (`.foundry/wiring-hash.pin`), the wiring manifest
(`.claude-plugin/wiring-manifest.json`), `foundry-wiring-hash.py`, its drop-in doctor check, and
the sibling auto-heal were all retired — none of that machinery ships,
and the current thin doctor carries no wiring check. There is NO wiring step at cut time anymore:
the cut ordering is simply land the wave → bump `plugin.json` + `marketplace.json` (version +
`source.ref`) → tag → re-pin `source.sha` to the tag commit (see `skills/cut-release/SKILL.md`).
This heading is kept as a tombstone so older references land on the honest disposition instead of
a dangling anchor.

**Closure.** The cross-file reconcile that `skills/cut-release/SKILL.md` and `skills/init/SKILL.md`
once pointed at this tombstone for is now **complete**: both surfaces carry a settled, conformant
disclosure of their own naming this same retirement, and no surface in the release family defers to
a follow-up any longer.

## Inputs / Outputs

- In: a `release.yaml` manifest under `$CLAUDE_PROJECT_DIR/.foundry/releases/<id>/`; per-atom evidence
  (each atom's `acceptance-contract.yaml` authorization + its merged-on-main status — the marker reachable on
  its target repo's `main`, resolved from the contract's `target_repo` via `.claude/foundry-project.json`).
- Out: a validated, state-advanced manifest; the closure verdict (CLOSED per atom, or the fail-closed reason).

## Anti-patterns

- **Asserting closure / forcing `completed`** — there is none; closure is re-derived from evidence, fail-closed.
- **A release "authorize-once" shortcut** — per-atom authorization + per-atom merge re-check is invariant
  (the `/foundry:authorize-release` precedent); `activate` re-derives each atom's authorization.
- **Re-implementing the loader/state-machine in the skill** — always drive `foundry_release.py`.
- **Declaring a floor gate in the manifest** — there is no floor-gate field; the floor is core-owned and
  re-derives per atom regardless of the release.
