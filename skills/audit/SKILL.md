---
name: audit
description: 'DORMANT-INVOCABLE, non-default deep audit (/foundry:audit) via the native Workflow tool — a PHASED remediate-between-passes engine. The single-pass /foundry:spec-review (see skills/spec-review/SKILL.md) replaced this as the default review verb; this engine is kept for an EXCEPTIONAL deep audit only (operator-invoked, never automatic). Runs an ORDERED sequential sweep prior-art -> requirement-quality -> steel-man -> adversarial -> red-team (workflows/spec-audit.js, itself unmodified/dormant); after each phase a SEPARATE fresh-context reviser self-remediates the spec IN the engine, guarded by a deterministic control-plane. Per an internal assessment establishing that 0 of 17 real runs converged and every paying finding landed in rounds 1-2, this is no longer the audit-before-merge gate and is never invoked automatically.'
---

# /foundry:audit — the dormant-invocable deep audit (NOT the default review)

> **Binding disposition:** this multi-pass engine is **no longer the default review
> path.** `/foundry:spec-review` (`skills/spec-review/SKILL.md`) is now the default review verb
> before front-authorization — a single-pass Phase-0-lints + three-question fan-out + one
> remediation round. This engine's own empirical record is why: **0 of 17 real v2 runs CONVERGED**, every documented paying
> finding landed in **rounds 1–2** (87% within 2 rounds), and rounds 3+ demonstrably *regenerated*
> findings rather than resolving them. It is kept **dormant-invocable** — the operator may still run
> `/foundry:audit <spec>` directly for an **exceptional deep audit** (a genuinely contested atom
> where the operator wants the full five-phase sweep) — but it is **never** triggered automatically,
> never the mandatory gate before authorization, and never the assumed step in a normal atom's
> pipeline. `workflows/spec-audit.js` itself is unmodified (denied path — it stays byte-identical,
> available for this dormant invocation).

The native-Workflow realization of the deep spec audit engine, as originally built. The
orchestration — an ordered phased sweep + remediate-between-passes + a deterministic guard
control-plane — is the `Workflow` tool; the phase prompts are the foundry layer. **This engine is
NOT read-only and remediation is NOT a separate step**: it runs the audit as a **PHASED
remediate-between-passes loop** whose **default mode is GUARDED-AUTO** (a background run that
reports the remediated spec + the diff the operator reviews at `/foundry:authorize`).
**Operator-steer is an OPT-IN sibling mode** — a per-phase REVIEW of the engine's remediations via
native `AskUserQuestion` (`approve` / `reject` / `stop` + "Other"-as-edit) that grants **NO
elevated authority** and **SURFACES-AND-HALTS** every high-blast outcome to the real channels; see
**Operator-steer mode** below.

## The five phases (ORDERED, sequential)

A **PASS** is ONE ordered, sequential sweep through five phases (`workflows/spec-audit.js` `PHASES`), each
phase = **AUDIT → REMEDIATE → next phase**. The order is **fixed and canonical** — prior-art FIRST (a
category-error halts before downstream effort), red-team LAST (on the most-hardened artifact):

1. **PRIOR-ART** (research-first) — the *proactive* lens the three adversarial lenses structurally miss:
   **is the target's approach the one the best agentic-engineering / platform / domain shops actually use,
   or are we building what the industry does not build?** It embeds the `research-first` prior-art criteria
   inline; if the approach is novel, is it research-justified, or a **category error** (a bespoke
   re-invention of a solved problem)?
2. **REQUIREMENT-QUALITY** (advisory) — grades the *prose quality* of each requirement, never the code
   (see the dedicated section below).
3. **STEEL-MAN** — assume the design is right; find where it is under-specified.
4. **ADVERSARIAL** — assume an adversary; find vacuous-pass holes, fail-open defaults.
5. **RED-TEAM** — attack the security/trust floor.

### The requirement-quality lens (advisory — grade the requirement, not the code)

The second phase is **ADVISORY** — prose-side only, never runtime-side. It scores **each acceptance
criterion in the spec's `<!-- normative -->` region** against a small fixed rubric — **completeness /
clarity / measurability / consistency** — flagging vague adjectives that lack a measurable criterion,
unresolved placeholders, and untestable phrasing. Its load-bearing boundary is **"evaluate the requirement,
not the code"**: it is forbidden from grading implementation behavior, runtime output, or whether the system
"displays/works correctly" — runtime proof belongs to the merge floor's CI checks + certification, and crossing the boundary would duplicate the
gate. Findings ride a distinct **`requirement-quality`** `class` whose `location` names the AC-ID it concerns
(traceability). The class is **advisory — it NEVER gates the convergence predicate and adds no merge
authority**; it sharpens the spec pre-authorize. (Contrast the `category-error` class, which DOES dominate.)

## Risk tiers (T0–T3) — proportionate ceremony, hard caps (feat-foundry-audit-tier-caps)

The binder (`scripts/foundry-audit-prepare.py`) **machine-derives a risk tier** per subject — codifying
the proportionate-ceremony directive INTO the machinery, not left to discipline — and the engine enforces
a **hard round cap** per tier. The risk tier (`riskTier`, T0–T3) is a **DISTINCT field from `auditModel`**
(the opus/sonnet/haiku/fable critic/reviser model resolved above); do not conflate them.

| Tier | Subject | Round cap | Phase order | Requirement-quality |
|------|---------|-----------|-------------|----------------------|
| **T3** | security-flagged / blast-radius / a spec missing the research-first "no operator fork" consensus marker (an unresolved novel-architecture fork) — **or any ambiguity** | the existing `MAX_PASS(10)` ceiling | full separated prior-art → requirement-quality → steel-man → adversarial → red-team | advisory |
| **T2** | any v1.0 draft feature atom (with or without a runtime surface — a pure-doc/agent feature atom is graded T2 too, since the audit grades the spec, not the walk) | **≤ 2 rounds** | prior-art → requirement-quality → **ONE consolidated adversarial-family pass** (steel-man + adversarial + red-team merged into a single critic dispatch) | **non-gating advisory** |
| **T1** | a normative delta to an **already-audited** spec (a findings-carry ledger exists for the atom) | **≤ 1 round** (delta-scoped to the changed normative sections when derivable; else the whole normative region under the same 1-round cap) | full order | advisory |
| **T0** | non-feature doc/scaffold/taxonomy atoms only (zero normative AC-IDs) | **0 LLM rounds** — deterministic lint only (the system-grounding phase; no `agent()` dispatch) | — | — |

**Fail-closed:** the binder defaults to **T3** on any undecidable/ambiguous input (an unresolvable
`scope.allowed_paths`, a blast-radius keyword hit in the spec text, or the absence of the research-first
"no operator fork" consensus marker in the spec's "Prior art / industry grounding" section). The engine
ITSELF also fail-closes independently: an unrecognized/absent `_args.riskTier` resolves to `T3`. **Caps
bound COST, not COVERAGE** — reaching a tier's cap with a material (High+) finding still open terminates
`NEEDS-OPERATOR`, never a silent pass and never an uncapped continuation.

**Per-critic watchdog (AC-ATC-4).** Every critic dispatch is bounded by a wall-time threshold (default
**10 minutes**) and an output-token threshold (default **200,000 tokens**, wired + configurable for when
a real per-call token count is available to the adapter — the wall-time bound is fully live today), both
overridable via `--watchdog-ms-limit <n>` / `--watchdog-token-limit <n>` on the binder. A critic that
exceeds either threshold, or otherwise dies (`max_output_tokens` truncation, a session/usage-limit
exhaustion, or an API error), is recorded `killed`, `kill_reason ∈ {watchdog, limit, error}` (the
ledger-taxonomy flat-enum shape) — the round is **voided, never a vacuous pass**: the engine retries the
round **ONCE** if the tier's cap is not yet exhausted; a second death, or a death with the cap already
exhausted, terminates the run at the new **`KILLED`** terminus (see Termini below).

**Findings-carry (AC-ATC-6).** A re-audit of the SAME atom auto-loads its prior run's findings ledger
(`.foundry/audit-findings/<atom-hash>.json`) and seeds it into the engine — the blind critic
verifies-or-extends against it (via the existing blind-view hint) rather than re-deriving the full set
fresh-context; a findings-carry ledger present is exactly what derives the **T1** tier above. The engine
returns `findingsDelta: {new, resolved, open}` (mirrors the ledger row's own `findings` shape) instead of
a bare re-derived total. See Procedure step 8 below for how to persist it after a run.

## The category-error DOMINANCE + re-ground rule

A **`category-error`** finding (emitted by the PRIOR-ART phase — a first-class `class` in the findings
schema) **DOMINATES**: it is a **deterministic HALT** that terminates the run at **`NEEDS-REGROUND`**
(severity-independent — even an `Info` category-error halts; auto cannot re-ground). It is resolved by
**RE-GROUNDING on the industry standard — run `research-first` → adopt the standard pattern → re-spec — NOT
by implementation-patching** the bespoke approach. (The blast-engine regression: a non-standard cross-plane
engine that survived four rounds as "fixed" implementation findings would have been a round-1 category-error
re-grounded on policy-as-code.)

## Remediation happens IN the engine (the guarded control-plane)

Remediation **now happens in the engine** (guarded-auto), not solely an external step. After each phase's
findings, a **SEPARATE remediation agent with FRESH context** (critic ≠ reviser) self-remediates **only the
target spec**, dispatched under the `foundry-cwd-enforce.sh` write-jail. Every guard is **deterministic
orchestrator logic** (NOT model judgment — an unguarded auto loop is reward-hackable).

**The remediation MODEL is propose-diff → blind verify → anchored apply**
(`feat-foundry-audit-remediation-model`) — it replaced the old remediate-between-passes-by-append shape,
which was the corpus's documented self-defeat ("each pass is auditing my own fresh edits from the previous
pass"; 13–17 stacked amendment blocks per spec judged unreadable and hand-rewritten). For each open finding
the reviser emits a **structured diff `proposal`** against the round's anchored base text — named findings
addressed/deferred + **bounded in-place span edits** (never a full-document rewrite, never an appended
amendment/reground block) — budgeted at the **initial proposal plus one post-rejection revision** (at most
ONE proposal is ever *applied* per round). A **fresh-context BLIND verifier** (no access to the reviser's
reasoning — only the base text + the diff + the named finding's bare identity) checks that every named
finding is resolved or explicitly deferred and that the diff introduces **no normative delta outside its
declared span**; a proposal failing either is **rejected and recorded, never applied**. Only a **verified**
proposal is **applied in place**, with a **deterministic one-line changelog entry** naming the round + the
finding resolved — the applied result becomes the **next round's anchored base**; the engine never audits
un-anchored (rejected/un-applied) reviser output.

- **Verify-after-correct on the NEXT sweep** — an applied fix is `pending-fix`, never `fixed` on the
  reviser's say-so; promoted to `fixed-verified` only after the NEXT full ordered sweep's INDEPENDENT,
  **BLIND** critic re-audits the ANCHORED result and does not re-raise it (the critic's input suppresses
  only `fixed-verified` + `accepted-residual`; it is never told a finding was "fixed").
- **Span-scoped normative integrity** (defense-in-depth, never delegated to the blind verifier's say-so) —
  every AC-block OUTSIDE a proposal's actually-touched span must be byte-identical to the anchored base, the
  AC-ID set must not shrink/duplicate, and the `<!-- normative -->` delimiters must stay intact; any breach
  is **rolled back from a whole-tree pre-edit snapshot** and **escalates `NEEDS-OPERATOR`**. (A legacy
  `{ newText }` reviser reply — never emitted by the production adapter — still runs the prior append-only
  floor byte-for-byte, kept for backward-compat with earlier test harnesses only.)
- **Budget exhaustion — `NEEDS-OPERATOR`** — when no verified proposal is produced within the round's budget
  (reviser failure, or the blind verifier rejects both the initial proposal and its one revision), the round
  halts `NEEDS-OPERATOR` with the open findings + the last rejected proposal attached; remediation **never
  force-applies and never silently drops a finding**.
- **Confined reviser** (allowlist = the target spec only); **critic-owned + pinned severity** (no
  severity-laundering); **no auto self-accept** of a Crit/High/Med (only Low/Info may be `accepted-residual`);
  **reopen-on-re-raise** (including an `accepted-residual` that recurs higher).
- **Fail-safe blast-radius escalation** over BOTH critic findings AND the reviser diff (ambiguous ⇒
  escalate) → a **security/auth/secrets/supply-chain** finding or edit halts at **`NEEDS-OPERATOR`**.
- **Draft-only precondition** — the engine reads the **sibling `acceptance-contract.yaml`** trailer and
  REFUSES to auto-remediate an authorized/frozen atom (front-authorization floor #1 preserved).

Ledger status ∈ `{open, pending-fix, fixed-verified, accepted-residual}`, threaded across phases + passes.

## Termini (layered — supersedes PLATEAU-WITH-BOUNDED-RESIDUALS for this engine)

The loop ends at exactly one of four **deterministic** termini:

- **`CONVERGED`** — ONLY on a **clean verification sweep**: a full ordered pass that raised **zero
  fresh-or-reopened** findings, leaves **no `open`/`pending-fix` Critical/High/Medium**, AND has
  `category-error == 0`. There is **no "no-change ⇒ CONVERGED" path** — a stalled sweep with an open
  blocker is `MAX_PASS`, never `CONVERGED`.
- **`MAX_PASS(10)-REACHED`** — the hard 10-pass cap with a blocker still open.
- **`NEEDS-REGROUND`** — a category-error halt (auto cannot re-ground; re-spec on the standard).
- **`NEEDS-OPERATOR`** — a blast-radius escalation (security/auth/secrets/supply-chain in a finding or the
  reviser diff), a foreign write, an existing-normative-text change (operator authority — steer mode), or
  a T0/T1/T2 tier's round cap reached with a material (High+) finding still open (AC-ATC-2).
- **`KILLED`** (feat-foundry-audit-tier-caps, AC-ATC-4/-5) — a critic died a SECOND time, or once with the
  tier's round cap already exhausted; `kill_reason ∈ {watchdog, limit, error}` names the cause. A round in
  which any critic died is NEVER eligible to produce `CONVERGED` — the engine retries once (within the
  tier's cap) before landing here.

## When to trigger — dormant, operator-invoked ONLY, never automatic

- **Never automatic.** `/foundry:spec-review` is the default before any front-authorization — see
  `skills/spec-review/SKILL.md`. This engine runs **only** on an explicit operator invocation:
  "audit `<spec>`", "/foundry:audit `<spec-or-doc>`", "run the full deep audit on `<spec>`".
- **Reach for it when** a single-pass review's findings are contested, an atom is genuinely
  high-stakes enough to warrant the full five-phase sweep, or the operator wants the
  remediate-between-passes behavior for a specific reason — never as the assumed default step.

## Pre-engine preconditions (G-2/G-3/G-4 — deterministic, run BEFORE any LLM round)

`scripts/foundry-audit-prepare.py` runs a **deterministic fail-closed gate**
(`scripts/foundry_audit_preconditions.py`, feat-foundry-audit-preconditions) at the very start of
`prepare()` — before the size gate (G-1) and before binding — that can only **SKIP** or **REFUSE**
a run, never weaken one. Evaluation order is **G-3 -> G-4 -> G-2** (REFUSE dominates SKIP: a
subject audited before it went dead must still refuse on re-invocation, never silently dedupe-skip
past the liveness fact — `spec_sha256` hashes only the normative region, so `status: superseded`
never changes it). This closes the retrospective's three deterministic-cost gaps: 7 specs
re-audited on byte-identical content, a superseded spec carried through 2 audit rounds by luck of
model judgment, and cross-spec references to dead siblings that no gate resolved.

- **G-2 dedupe (AC-APC-1).** An identical `spec_sha256` already has a recorded terminus row in
  `.foundry/audit-ledger.jsonl` -> the binder **SKIPS** (zero LLM rounds), prints the deterministic
  reason line, records a NEW `dedupe-skip` ledger event, and **exits 0 with NO `scriptPath`**.
  When the skill sees this — no `scriptPath` on stdout, exit 0, a line starting
  `AUDIT-PRECONDITION-SKIP` — **stop here**: the audit is already covered by the prior terminus;
  do not invoke `Workflow`. `--force` bypasses G-2 **only**.
- **G-3 liveness (AC-APC-2).** `status: superseded` -> the binder **REFUSES** (fail-closed,
  non-zero exit, no `scriptPath`) always. "Release-dropped" (absent from an **explicitly invoked**
  `--release <id>`'s manifest) also refuses, but **only** in that explicit context — pass
  `--release <id>` when auditing inside a release wave; omit it and release-dropped never fires
  (vacuously live). A `completed` release's manifest never kills a subject even if absent from it.
  **No bypass** — fix the spec (drop `status: superseded`, or restore release membership).
- **G-4 reference closure (AC-APC-3).** Every `[[feat-slug]]` / `its authorizing spec` / bare
  (non-bold, non-self) `AC-<PREFIX>-<N>` reference in the subject's `<!-- normative -->` region
  must resolve to a live, authorized corpus atom -> else the binder **REFUSES**, naming every
  dangling reference (nonexistent / superseded / unauthorized-and-required). **No bypass** — fix
  the referenced atom or the reference itself.
- **Fail-closed toward AUDITING (AC-APC-4), never toward suppression.** An error reading a gate's
  OWN inputs (an unreadable ledger, an unparseable release manifest, an unreadable spec/corpus)
  disables SKIP/REFUSE for **that heuristic alone** and lets the audit **proceed** — the gate may
  only ever save cost, never silently skip a needed audit. `--allow-oversize`-style overrides do
  not apply here; there is nothing to override on an error passthrough.

**Skill reaction table:**

| Binder outcome | stdout/stderr shape | Skill action |
|---|---|---|
| PROCEED | `scriptPath` printed, exit 0 | Continue to step 3 (`Workflow`) below |
| SKIP (G-2) | `AUDIT-PRECONDITION-SKIP …` printed, exit 0, no `scriptPath` | Stop; report the prior terminus (already covered) |
| REFUSE (G-3/G-4) | `foundry-audit-prepare: FAIL-CLOSED — AUDIT-PRECONDITION-REFUSE …` on stderr, exit non-zero | Stop; surface the named reason (superseded / release-dropped / dangling reference) to the operator — fix the subject, do not retry blindly |

## Procedure

1. **Bind the target by PATH via the deterministic host-side binder (NEVER hand-emit the spec bytes).** The
   native Workflow runtime forbids filesystem / Node access, but the skill main-loop **does NOT read the spec
   into an `args` value** — a large spec cannot be transcribed faithfully by the model (a real-world
   ~39K-token spec previously drove a placeholder substitution + a garbage run). Instead run the **deterministic binder**
   (a non-LLM Python step) — which now runs the G-2/G-3/G-4 preconditions above BEFORE anything else — and
   pass only a **path**:
   ```
   scripts/foundry-audit-prepare.py <target>   [--max-pass <≤10>] [--reality-sampling] [--audit-model <alias>]
                                                [--risk-tier <T0|T1|T2|T3>]
                                                [--watchdog-token-limit <n>] [--watchdog-ms-limit <n>]
                                                [--release <id>] [--force]
   ```
   The risk tier is **machine-derived automatically** (see Risk tiers above) — `--risk-tier` is an explicit
   operator override, not the normal path. The binder prints `§8 risk tier = <T0..T3> — <reason>` to
   stderr alongside the model decision.
   The binder reads the on-disk `<target>` spec + its sibling `acceptance-contract.yaml` **host-side (no
   LLM)**, byte-binds them (JSON-escaped) into a **per-run copy of the shipped engine** — byte-identical
   except a single injected `_args` binding that pre-populates exactly the `_args.specText` /
   `_args.contractText` the entrypoint already consumes — **round-trip-verifies** the written artifact against
   the on-disk bytes, and on success **prints a `scriptPath`** (a path to the per-run script in a system-temp
   location) to stdout; it **fails closed** (non-zero exit, NO `scriptPath`) on any mismatch, truncation, or
   unreadable target. The full spec/contract text **never transits an LLM-emitted `args` value nor an agent
   return**, at any spec size (uniform — no size threshold).
2. **Pass-0 reality sampling (conditional).** If the target declares a parser/extractor over a corpus,
   pass `--reality-sampling` to the binder — sample real inputs before auditing claims; cannot be silently
   skipped. `--max-pass` is a soft target capped at **10** (the engine never exceeds it); the lean/scale dial
   only lowers the soft target, never the hard cap.

   **Audit-model selection (auto-escalation; feat-foundry-audit-model-escalation).** The binder resolves the
   model the `critic`/`reviser` fan-out runs on and injects it as `_args.auditModel`. **Precedence:** an
   explicit **`--audit-model <alias>`** (`opus|sonnet|haiku|fable`) wins; else the atom is **auto-escalated to
   `opus` when it is security-flagged** (an auth/secrets/supply-chain path in its `scope.allowed_paths`, via
   the shipped `derive_security_flag`), else the **`sonnet`** cost floor; an underivable scope fail-safes to
   `opus`. The **decision is surfaced natively**: the binder prints `§8 audit model = <alias> — <reason>` to
   **stderr** at bind time, and the running engine emits a `log()` narrator line `§8 audit tier: critic +
   reviser on '<alias>'`. Escalation is only ever STRONGER than the floor.
3. **Run the audit Workflow by PATH (guarded-auto):** invoke the per-run script the binder printed:
   ```
   Workflow({ scriptPath: "<the scriptPath the binder printed>", args: {} })
   ```
   The bound script runs the ORDERED phased sweep (prior-art → requirement-quality → steel-man → adversarial
   → red-team) over an **in-memory string buffer** built from the injected `_args.specText`/`_args.contractText`
   (no file handle is opened inside the workflow), self-remediates between phases under the deterministic
   control-plane (T2 consolidates the adversarial-family into one pass; T0/T1 run the tier-capped shapes
   above), and loops to one of the five termini above — returning
   `{ target, terminus, passes, ledger, events, finalSpecText, riskTier, findingsDelta }`.
   **`finalSpecText`** is the in-memory buffer at the terminus (accepted appends included, rolled-back
   edits excluded; equals the bound spec text when nothing was accepted). **`findingsDelta`**
   (`{new, resolved, open}`, AC-ATC-6) is the findings-carry delta against any seeded prior-run ledger.
   Pass `args: {}` — the injected binding overrides any inline args, so nothing needs to be hand-emitted.
4. **Apply `finalSpecText` under the write-jail (defense-in-depth draft-only guard).** After the workflow
   returns, **the skill** performs the ONLY file write (the workflow wrote nothing): write `finalSpecText`
   back to `<target>` — the exact path the binder read from — **only if** (a) `finalSpecText` differs from the
   **freshly RE-READ on-disk `<target>`** (skip a no-op write — since the skill no longer holds the spec text
   in memory, it re-reads the on-disk target for this comparison; a host-side re-read, NOT an `args` transit,
   so it does not reopen the fidelity wall) **and** (b) you **re-read the on-disk sibling
   `acceptance-contract.yaml` and confirm it carries NO authorized trailer** (`FOUNDRY-AUTHORIZED-TRAILER`).
   The file-capable actor that owns the write independently enforces the draft-only floor at the write
   boundary, so a frozen atom's spec can never be overwritten.
   Then **delete the per-run `scriptPath`** (the binder's system-temp bound script) — each run's script embeds
   the full spec+contract text, so removing it after the workflow returns avoids unbounded accumulation in
   `$TMPDIR` (the OS temp reaper is only the fallback, not the cleanup path).
5. **Judge by terminus, not by a zero-finding count.** `CONVERGED` requires a clean verification sweep;
   the other four are deterministic halts the operator dispositions (re-ground, review at authorize, or —
   for `KILLED` — re-run once the underlying cause, e.g. a usage-limit reset, is resolved). Never grind
   for a fake green zero.
6. **Review the auto-remediation diff at `/foundry:authorize`.** Guarded-auto bypasses nothing: the
   operator reviews the full remediated-spec diff at front-authorization (the human root of trust). A
   `NEEDS-OPERATOR` / `NEEDS-REGROUND` / `KILLED` terminus is handed to the operator (or the operator-steer
   sibling).
7. **Record the audit (ENFORCED at authorize).** Run
   ```
   scripts/foundry-audit-record.py --spec <spec> --rounds <N> --operator <id> --tier <riskTier>
       [--verdict converged|needs-operator|needs-reground|killed]
       [--kill-reason watchdog|limit|error]   # required iff --verdict killed
       [--findings-new <findingsDelta.new>] [--findings-resolved <findingsDelta.resolved>]
         [--findings-open <findingsDelta.open>]
   ```
   to write an evidence row bound to the spec's content hash into `.foundry/audit-ledger.jsonl`. Pass the
   engine's returned **`riskTier`** (T0–T3) as `--tier` — the ledger row's generic `tier` string field
   records the RISK tier here (distinct from the `auditModel` alias surfaced above). A `KILLED` terminus
   maps to `--verdict killed --kill-reason <the engine's kill_reason>`. `/foundry:authorize` **fail-closes**
   on a spec with no matching row (or the operator's logged `--skip-audit-reason`), so this step is the
   gate, not optional bookkeeping. Also note the terminus + tier in the spec's Changelog section for humans.
   (Threat model: the row binds to the signed `spec_sha256` and makes the audit auditable; it is the
   proportionate, non-crypto enforcement. See `scripts/foundry_audit_ledger.py`.)
8. **Persist findings-carry for the NEXT re-audit (AC-ATC-6).** After recording, persist the run's FINAL
   ledger so a later re-audit of the SAME atom loads it (and derives **T1** instead of T2/T3, AC-ATC-1):
   ```
   echo '<JSON array of Object.values(ledger)>' | scripts/foundry-audit-prepare.py <target> --save-findings
   ```
   Skip this step for a `T0` run (no ledger was populated) or when the atom is about to be authorized and
   frozen (no further re-audit is expected).

<!-- steer-mode:start -->
## Operator-steer mode (OPT-IN — auto stays the default)

**AUTO is the default.** A plain `/foundry:audit <spec>` runs the authorized guarded-auto engine to a
terminus and is **never blocked waiting on the operator**. **Operator-steer engages ONLY on an explicit
steer request** — a `--steer` flag, or the phrasing "with operator steering" / "interactive audit". The
skill **MUST NOT silently default to steer**; absent an explicit steer request, auto runs. (AC-AOS-1)

**Steer REUSES the authorized engine — it does not redefine it (AC-AOS-2).** When steer is engaged the
skill orchestrates, in the **skill MAIN-LOOP** (the only place `AskUserQuestion` is callable — the
background `Workflow` cannot solicit input mid-run), the **same** ordered phases
**prior-art -> requirement-quality -> steel-man -> adversarial -> red-team**, the same **ledger** + status
semantics, the same convergence predicate, and the same four **termini** (`CONVERGED` /
`MAX_PASS(10)-REACHED` / `NEEDS-REGROUND` / `NEEDS-OPERATOR`) and deterministic guards as Atom 1's engine.
Gating is **per-phase**: the operator reviews a phase's proposed remediation **before the next phase runs**,
preserving the engine's remediate-**between-phases** invariant (each phase still audits the spec the prior
phase improved) — this is **NOT per-pass batch** (which would let phases 2–5 audit an un-remediated spec).
The **`critic != reviser` separation is MECHANIZED**: the main-loop dispatches the **SAME fresh-context,
write-jail-confined reviser sub-agent** the auto engine uses and only orchestrates phases + relays the
operator's decision — **the main-loop does not self-remediate**.

### The per-phase review gate (native `AskUserQuestion`)

For each phase's proposed remediation the confined reviser **applies its edit in place under the
write-jail**, and the resulting **diff IS the preview** — **preview = apply-then-snapshot** (it reuses the
engine's pre-edit snapshot; `reject` / `edit` / `stop` roll back from that snapshot — there is no
propose-without-apply mode). The operator decides via the native **`AskUserQuestion`** tool.

STEER OPTIONS (load-bearing): the native `AskUserQuestion` offers EXACTLY THREE explicit options
{approve, reject, stop} + the native "Other" free-text = edit. No other explicit option exists.

Effects are **defined** (AC-AOS-3):

- **approve** -> keep the applied edit; status `pending-fix` (still subject to the engine's NEXT-sweep
  **verify-after-correct** — approval is not self-attested resolution).
- **edit ("Other")** -> roll back the reviser's edit, then apply the operator's revision **through the SAME
  deterministic guards** (write-jail target-spec-only + blast-radius + append-only). There is **no
  exemption**: an operator edit that would change existing-normative-text or touch a security region does
  NOT get applied — it **surfaces-and-halts** per AC-AOS-4.
- **reject** -> roll back from the snapshot; the finding **stays open for ALL severities** — a Low/Info
  `open` finding is non-blocking and a Crit/High/Med stays `open` (never dismissed to convergence). There is
  **no `accepted-residual` via the click**: residual acceptance is not granted through the unauthenticated
  click.
- **stop** -> roll back the **in-flight applied preview** from the snapshot (matching `reject`, leaving a
  clean decided tree) and terminate with verdict **`abandoned`** (open blockers are never laundered to
  `CONVERGED`; downstream `/foundry:authorize` fails closed on a non-plateau row).

**No remediation is kept without an operator decision.**

### Surface-and-halt — steer grants NO elevated authority (AC-AOS-4)

Steer grants the operator **NO elevated authority** the auto engine lacks. Every high-blast outcome — a
**`NEEDS-OPERATOR`** halt (security/auth/secrets blast-radius or an **existing-normative-text** change), a
**`NEEDS-REGROUND`** category-error halt, or a **Crit/High/Med** finding the operator will not approve a fix
for — **SURFACES-AND-HALTS** (surface-and-halt: the finding + its explanation is presented and the loop
TERMINATES with a **`needs-human`** ≡ **`abandoned`** verdict), directing the operator to the **REAL
channels: manual spec revision + re-audit + `/foundry:authorize`**. These outcomes are **NOT dispositioned
through the `AskUserQuestion` click** — accepting a Crit/High/Med residual, approving an
existing-normative-text change, or clearing a security halt are authenticated, `op_id`-bound authorizations
only `/foundry:authorize` performs. The **front-authorization floor is NOT relaxed** and no
privilege-elevating transition rides the unauthenticated click. Steer is **re-run, not resume**: invoked on
a spec a prior auto run left at a halt, it re-runs the audit in steer mode (the deterministic engine
re-derives the halt) — there is **no persisted ledger** (an on-disk ledger would be a forgeable-evidence
path).
<!-- steer-mode:end -->

## Inputs

- `<target>` — spec or design doc path. `max_pass` (mode-derived soft target, hard cap 10),
  `reality_sampling` (conditional).

## Outputs

- The structured engine result (`terminus` + `passes` + `ledger` + `events`) + the auto-remediated spec
  diff (reviewed at authorize) + the spec's Changelog audit-trail entry.

## Anti-patterns

- **Chasing a zero-finding `CONVERGED`.** Convergence is a clean verification sweep, not a zero count;
  a `NEEDS-REGROUND` / `NEEDS-OPERATOR` halt is the correct outcome, not a failure to grind harder.
- **Adding a mechanism layer to close a meta-regress finding** ("who verifies the verifier") — bound it at
  the human root-of-trust (the operator review at authorize); the control-plane caps the blast radius.
- **Invoking this engine as the default review step for a normal atom.** It is dormant-invocable
  for an exceptional deep audit ONLY (a deliberate disposition since the v0.25.0 review-pipeline
  realignment) — `/foundry:spec-review` is the default;
  reaching for this engine automatically, or presenting it as "the" review step, is the anti-pattern
  this reframe exists to correct.
- **Re-implementing the loop imperatively** — the orchestration is the `Workflow` tool.
