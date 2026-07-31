---
name: intake
description: The front door (/foundry:intake, phase 0 of the pipeline). Ingest a fuzzy input (prose, a human-written spec, a PRD, a Figma/Claude-Design export, an MCP connector) → interactive discovery → a deterministic LLM-authored atomic spec ready for the single-pass spec-review + front-authorization. Trigger to turn a fuzzy ask into a spec the factory can build.
---

# /foundry:intake

Phase 0 — the front of the pipeline. Turns fuzzy inputs into deterministic atomic
specs. The pipeline is CLOSED: `intake → spec-review (the single-pass review default) → authorize
→ release-shape → implement → certification run → the merge floor → closeout →
deploy-observe`. (`/foundry:audit`'s multi-pass engine stays dormant-invocable for an
exceptional deep audit — see `skills/audit/SKILL.md` — it is never the default step here.)

## When to trigger

- "intake `<ask>`", "/foundry:intake", "turn this PRD/Figma/prose into a spec".

## Sources (multi-source, pluggable)

Prose · a human-written draft spec · a PRD · a design export (`[Design-asset:]`) · an
MCP intake connector (allowlisted). New source types plug in without changing the
downstream pipeline.

## Procedure

1. **Ingest** the source(s). For design inputs, load the cited design assets first
   (the `design-context-load` discipline) so the spec is design-grounded.
2. **Discovery (interactive)** — walk the decision tree (see the Discovery-interview
   discipline below). Use native `AskUserQuestion` to resolve the load-bearing
   ambiguities (scope, surfaces, acceptance criteria). Do NOT invent requirements; ask.
   (This is the former `clarify-blockers`, now native.)
3. **Research gate (before authoring)** — for a non-trivial approach decision, run the
   research gate (see below) BEFORE authoring; carry its outcome into the spec.
4. **Author the atomic spec** — one atom = one capability-behavior, per the spec
   taxonomy: `specs/features/<product>/<domain>/<capability>/feat-….md`, from
   `context/feat-spec-template.md` (the industry-grounded shape — see "The template shape"
   below). Stable AC IDs (the bijection target for the acceptance-contract) live in a
   delimited normative region (`<!-- normative -->`) so `spec_sha256` excludes cosmetic
   edits. On a **brownfield** atom, ground the spec's data-model / interface section on the
   **schema-aware survey** (see "Schema-aware authoring" below) — `sd-discover`'s fifth
   dimension, composed with `explore-before-ask` and the `data model`
   clarification-taxonomy dimension — BEFORE drafting the section from memory.
5. **Hand off** → `/foundry:spec-review` (the default single-pass review —
   see `skills/spec-review/SKILL.md`) → contract-author → `/foundry:authorize`. Intake
   never authorizes, reviews, or implements; it produces the spec.

## The template shape (what intake emits)

Every authored spec follows `context/feat-spec-template.md` — the industry-grounded shape
`/foundry:spec-review`'s Phase 0 pre-lints and Phase 1 fan-out check against, and the shape
CONSTITUTION.md §III names as the checked standard:

- **EARS-phrased, singular, measurable ACs** (CONSTITUTION §13) — each AC-ID tagged
  **Requirement** (triggered: `When`/`While`/`If…then`) or **Invariant** (always-true,
  ubiquitous `shall`); no vague adjective without a measurable criterion; AC-IDs stable
  (never renumbered).
- **`## Out of scope / non-goals`** (CONSTITUTION §15) — what a reasonable implementer might
  assume is included but isn't. Deferred work is named, never implied.
- **`## Prior art / industry grounding`** — see the research gate below; required for any
  non-obvious atom.
- **UI/UX artifact slots** (CONSTITUTION §16, UI-bearing atoms only) — a `design/` sibling
  directory (`specs/features/<…>/<capability>/design/`) holding the structured design export,
  annotated mockup(s), and target screenshot(s), cited via the citation grammar; a
  **`## Journeys`** section naming each concrete, AC-tagged user path (`journeys: [<tag>,
  …]` is exactly what `foundry-wave-plan.py`/the release manifest carries forward as
  per-atom metadata) — these become the E2E suite **verbatim**, not reinterpreted.
- **Size respects the BINDING ceiling** (CONSTITUTION §12 — **14 ACs / 8,000 words, no
  override**) — if the atom you are drafting is trending oversize, **decompose it into
  smaller atoms now**, at authoring time, rather than authoring one large spec and
  discovering the ceiling at review. `/foundry:spec-review`'s Phase 0 REFUSES an oversize
  spec unconditionally; there is no `--allow-oversize` escape hatch to reach for.

## Discovery-interview discipline

Four discipline points govern the interactive discovery loop (step 2). Folded from the
stood-down `intake-discovery` craft atom — they are distinct, load-bearing, and each is
applied on every discovery turn.

### one-question-at-a-time

Ask exactly ONE question per turn. Never dump a wall of questions; resolve a node, learn
from the answer, then move to the next. A single focused question respects the operator's
attention and keeps the decision tree legible.

### recommend-an-answer

ALWAYS propose a recommended answer alongside the question — never an open-ended prompt.
For a **best-practice decision**, the recommendation is SOURCED VIA `research-first`: you research the convergent industry practice
and recommend adopting it, rather than asking the operator to supply the standard from
memory. This is where the research gate plugs into the interview — `research-first` is the
authority behind every best-practice recommendation, so the operator confirms or forks a
*researched* proposal, not a blank.

### explore-before-ask

Read the codebase to answer a question BEFORE asking the operator. Only ask what the code
cannot answer — convention, existing wiring, prior atoms, and naming are discoverable from
the repo. Asking the operator for something the code already states wastes a turn.

### depth-first

Traverse the decision tree DEPTH-FIRST: resolve a node's dependencies before moving to a
sibling. A child decision often constrains or eliminates its siblings, so resolving deep
before wide avoids re-asking and keeps the interview convergent.

## The research gate

For a **non-trivial approach / architecture decision**, intake invokes `research-first` BEFORE authoring the spec, and records the
outcome as a **`## Prior art / industry grounding`** section in the spec — the adopted
industry consensus plus its cited sources, or the operator-fork record on a genuine
no-consensus. The `research-first` invocation is ordered strictly *before* the author step;
intake LOADS the primitive, it does not re-implement the procedure.

**Composition.** The discovery loop walks the decision tree depth-first; at each
**best-practice node** the research gate / `research-first` resolves it (adopt the
convergent practice, or escalate only a genuine fork). This replaces "ask the operator
about everything" with "research everything, ask only on the genuine forks."

### Prior art / industry grounding

The research-gate outcome is recorded in the authored spec under a section with this exact
heading — `## Prior art / industry grounding` — carrying the adopted industry consensus plus
its cited sources, or the operator-fork record on a genuine no-consensus. Its presence (or
the fork record) is what discharges the novel-machinery defect rule below.

### Threshold (approach-ambiguity, not mechanical)

The gate triggers on **approach / architecture ambiguity** — a new capability, an
unfamiliar domain, a design fork ("how should we do X"). It does **NOT** trigger on
mechanical work: a typo, a rename, or a well-trodden CRUD atom where the standard is
obvious / established. The discipline is proportionate proactive prevention, not ceremony
on every atom.

### Novel-machinery-without-grounding defect

A spec that proposes **novel / non-standard machinery WITHOUT** either the
`## Prior art / industry grounding` section (consensus-adopted) or the operator-fork record
is an **intake defect**. This is the *prevent* half of closing the phantom-atom hole; the
deep spec audit engine's prior-art lens is the *catch* half downstream.

## Clarification taxonomy

Discovery is not "done" by vibe. Before exiting clarification, scan a **fixed clarification
taxonomy** — a closed set of named coverage dimensions — and score **each dimension Clear /
Partial / Missing** against the in-progress spec. The taxonomy is a **coverage floor, not a
question ceiling**: it guarantees no whole dimension is skipped silently, and the operator may
always go deeper. It **composes with** the research gate (the `## Prior art / industry
grounding` section above) — the taxonomy bounds *coverage*, the research gate grounds the
*best-practice* nodes; neither replaces the other.

The fixed coverage dimensions (pruned to what Foundry atoms actually need):

- **functional scope** — what the atom does, and explicitly does NOT do.
- **data model** — the entities, fields, and record contracts the atom touches.
- **non-functional constraints** — performance, security, limits, failure modes.
- **integration** — the seams, callers, and wiring the atom plugs into.
- **edge cases** — the boundary / error / empty-state behaviors.
- **terminology** — the load-bearing terms, named and disambiguated.
- **completion signals** — the acceptance criteria + the done/exit predicate.

### dimension scoring (Clear / Partial / Missing)

Score every dimension above as **Clear** (fully specified), **Partial** (named but
under-specified), or **Missing** (absent entirely). A Missing dimension, or a material
Partial, is a coverage hole the question queue must target — this is what turns "clarified
enough" from a judgment call into a checklist.

### bounded prioritized question queue

From the Missing / material-Partial dimensions, generate a **bounded queue of at most N
high-impact questions** (a hard cap — default N=5), ordered by **materiality**
(scope > security > data > UX > terminology), and **excluding low-value trivia**. This
**refines** the one-question-at-a-time discipline (it does not replace it): ask from the
queue, highest-materiality first, one question per turn. A small, ordered, high-impact queue
beats an unbounded ad-hoc stream.

## Atomic answer persistence

Integrate **each accepted answer atomically, before asking the next question**, so an
interrupted session (compaction, the operator stepping away) resumes from the last persisted
clarification instead of restarting cold.

### per-answer save (append to ## Clarifications, then write the spec to disk)

After EACH accepted answer, in this order, *before* asking the next question:

1. **Append** the question and its resolved answer to a **`## Clarifications`** session log in
   the spec.
2. **Apply** the answer to the most-appropriate spec section.
3. **Save the spec file to disk immediately** — never batch the writes to the end.

The load-bearing rule is **save after each integration** — minimizing the risk of context
loss. A batch-at-the-end integration forfeits resumability and is the anti-pattern this
section exists to forbid.

## Discovery exit condition (taxonomy-keyed)

Discovery may **exit clarification only when no dimension is Missing and every material
Partial is resolved**. "Clarified enough" is therefore a **checkable predicate over the
taxonomy**, not a judgment call: while any dimension is Missing, stay in the bounded-queue
question loop. The exit condition is keyed to the taxonomy score, so it is auditable and
resumable — the same coverage floor that drove the questions also gates the exit.

## Ceremony-tier classification (feat-foundry-ceremony-tiering, AC-CTR-8)

At the end of the discovery interview, once the exit condition above is satisfied, classify the
atom's review depth by INVOKING the deterministic classifier CLI as a command with its signal
arguments — never by describing the classification in prose. Estimate `--files` (files touched)
and `--ambiguity` (`none`/`low`/`medium`/`high`) from the interview, pass `--new-contract-surface`
when the atom adds a new public contract, and pass every declared scope path plus the draft spec
text so the security trigger can fire:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_ceremony_tier.py" --files <n> --ambiguity <level> [--new-contract-surface] [--scope-path <p> ...] --spec <draft-spec-path> [--contract <sibling-contract-path>]
```

The CLI prints exactly one line (`tier: <tier> — <n> files, <…>, <…> ambiguity`, extended with
` (security override: <trigger>)` when the sensitivity trigger fired) — show that line to the
operator verbatim and carry the resolved tier forward to `/foundry:spec-review`, which reads it
to select the masked Phase-1 questions.

**Operator override, either direction.** The operator may override the classified tier by
re-running the CLI with `--override <tier> --override-reason <one-line>`. A security-triggered
atom refuses an override below `standard`, and an empty/whitespace-only reason is refused — the
CLI exits non-zero naming the ground of refusal, and the classified tier stands. When an override
IS accepted, append this line verbatim to the spec's `## Clarifications` section:

```
ceremony-tier override: classified <tier> → adopted <tier> (reason: <one-line>)
```

## Schema-aware authoring (data-model grounding)

The `data model` clarification-taxonomy dimension above is **hardened from advisory to
machine-checked**: it is not scored by model judgment alone. Before drafting the spec's data-model /
interface section (Procedure step 4), ground on the recovered live schema — composed with
`explore-before-ask` — by running `sd-discover`'s **fifth data-model / persisted-schema dimension**
(`[Skill: sd-discover]`), which recovers the existing entities/tables/columns with their live
identifiers from the system-state snapshot tool's deterministic snapshot (`build_system_snapshot`). This is an
**authoring-time** consumer of that dimension — it runs BEFORE the spec is drafted, in addition to
(not instead of) the existing implement-time consumer.

Then **machine-check** the author's declared data-model artifacts against that recovered reality —
never trust model judgment alone for the net-new-vs-live call. Invoke the seam directly:

```
python3 scripts/foundry_intake_grounding.py --project-dir <code-repo-root> \
  --json '[{"kind": "table", "identifier": "public.vehicles", "classification": "net-new"}, ...]'
```

(equivalently, import `foundry_intake_grounding.intake_schema_defects(declared_artifacts,
project_dir)` directly). Each declared artifact is `{kind, classification, identifier}` — the exact
shape the authorize-time `system_grounding` block later freezes, so the intake *prevent* verdict and
the authorize *freeze* verdict are the same function of the same snapshot.

**A returned defect BLOCKS the hand-off to `/foundry:spec-review`.** Treat any non-empty
`intake_schema_defects(...)` result as a blocking finding: do not proceed to step 5 (hand off to
`/foundry:spec-review`) until the author resolves every returned defect (re-ground the declaration to
`alter`/`exists`, or correct the identifier). This is the *prevent* half of the reality-grounding
gate — the review pipeline's `reality-divergence` HALT and the authorize-time freeze remain the load-bearing
downstream floors; this check is not itself a new enforcement floor, it just makes the author less
likely to hand them a stale spec.

### Mutation-delta authoring rule

Express the authored spec's data-model / interface section as **deltas** — `alter` / extend /
integrate — over the surveyed live schema. Use `net-new` / `CREATE` **only** for an artifact the
fifth-dimension survey proves absent. A from-scratch, greenfield-in-a-vacuum redescription of an
artifact the snapshot proves already live (declaring it `net-new` when it exists) is the
schema-grounding intake defect above — the rule's enforcement IS the deterministic machine-check
just described, applied to every declared artifact; the spec makes no unverifiable claim over an
authoring path the check does not see.

**Unconfigured no-op.** When grounding is unconfigured (no schema source wired for the code repo),
this whole section is a no-op: the survey records "no schema grounding configured" and
`intake_schema_defects(...)` returns zero defects for any declaration — never blocking a project with
no schema source wired.

## Inputs / Outputs

- In: fuzzy source(s) + (for design) the cited assets. Out: a DRAFT atomic spec + its declared AC IDs, ready for review.

## Anti-patterns

- **Inventing requirements** instead of asking via `AskUserQuestion`.
- **Authoring a non-atomic spec** (multiple capabilities in one) — split it.
- **Skipping design-asset load** for a UI spec (builds blind → off-design,).
- **Authorizing/implementing in intake** — that's the downstream gates.
