---
name: spec-review
description: 'The single-pass spec review (/foundry:spec-review) — THE DEFAULT review verb before front-authorization, the multi-pass deep audit is opt-in only (measured: multi-pass runs do not converge, and paying findings land in rounds 1-2). Phase 0 deterministic pre-lints (the CONSTITUTION.md §12 BINDING size ceiling, no override; reference-closure; normative-region presence) — a REFUSE stops here, before any LLM spend. Phase 1 fans out THREE fresh-context questions via the spec-reviewer agent (prior-art / steel-man+adversarial consolidated / per-AC rubric) plus a conditional one-shot security question for security-flagged specs. Phase 2 caps remediation at two rounds — a third requires an operator decision recorded in the spec Clarifications — and downgrades a locator-less Block to Risk. Phase 3, the operators merge of the spec to the workspace main, IS the authorization. workflows/spec-audit.js (the multi-pass engine) stays dormant-invocable for an exceptional deep audit — it is NEVER the default path. Trigger when a spec is drafted/refined and needs review before authorization.'
---

# /foundry:spec-review — the single-pass review (THE default before authorization)

The single-pass review realigns the review step to the industry-consensus shape (Spec-Kit/Kiro).
The prior multi-pass remediate-between-passes engine
(`workflows/spec-audit.js`, driven by `/foundry:audit`) is **retired from the default path** —
the measured record: multi-pass runs do not converge, rounds 3+ regenerate rather than
resolved findings, and every documented paying catch landed in rounds 1–2 (87% ≤2 rounds), which
is why the single-pass review is now the default.
**`/foundry:spec-review` is now the default review verb.** `/foundry:audit` stays
**dormant-invocable** for an exceptional deep audit (see `skills/audit/SKILL.md`) — it is never
the path a normal atom runs.

**CONSTITUTION.md is the checked standard.** Every criterion below cites the CONSTITUTION.md
section it enforces (§12 the size ceiling, §13 EARS/AC quality, §14 prior art, §15 out-of-scope,
§16 UI/UX intent, §17 executable checkpoints). An adopter workspace ships its own copy from
`context/constitution-template.md`.

## When to trigger

- A spec is drafted or refined (via `/foundry:intake` or a direct edit) and needs review before
  `/foundry:authorize` (for a code atom) or before merging to the workspace `main` (front-
  authorization, CONSTITUTION.md §I.1).
- "/foundry:spec-review `<spec>`", "review this spec", "is this spec ready to authorize".
- **Default ON** for any non-`trivial`-tier atom.

### The `trivial` tier (feat-foundry-ceremony-tiering, AC-CTR-13)

When the classified ceremony tier is trivial, the review is skipped ONLY when the operator supplies the verbatim token `skip review; reason: <one-line>` — never framed as optional, numbered, or scope-conditional, and never inferred from the tier alone.

The fallback: absent that token, run the small-tier mask (the consolidated steel-man + adversarial question) instead of skipping — `trivial` is operator-gated, never a silent skip.

## Phase 0 — deterministic pre-lints (BEFORE any LLM dispatch, zero token cost)

Run these checks, in order, over the target `<spec>` (and its sibling `acceptance-contract.yaml`
if one exists). **Any REFUSE stops here** — fix the spec, re-run Phase 0 before spending any LLM
budget on Phase 1 (the "cheap, deterministic, zero-token, run first" lesson,
generalized from the retired engine's G-2/G-3/G-4 preconditions).

1. **Size ceiling + reference-closure — by COMMAND, not by describing an import.** Run:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-spec-lint.py" <spec> [--project-dir <dir>]
   ```
   `foundry-spec-lint.py` is a small, dedicated CLI wrapping the two
   REUSED deterministic checks below — never re-implementing them, and never inlining an ad-hoc
   Python import into this skill:
   - **Size ceiling — CONSTITUTION.md §12, BINDING, NO OVERRIDE.** `spec_size_metrics(spec_text)`
     (`scripts/foundry-audit-prepare.py` — distinct normative AC-IDs + total word count) against a
     ceiling **HARDCODED at 14 ACs / 8,000 words** — deliberately NOT the adopter-tunable
     `.claude/foundry-project.json` `gates.audit_size` resolution the dormant deep spec audit
     engine reads (that knob soft-tunes the retired engine's own warn/hard thresholds; the ceiling
     THIS lint enforces is a constitution-bound floor, not an adopter dial). **Oversize FAILS
     unconditionally** — `foundry-spec-lint.py` exposes no override flag, full stop (the size
     ceiling is the one control the data fully validates — it is made binding, not advisory). The
     remedy is **decomposition into smaller atoms**, never a bypass.
   - **Reference-closure — CONSTITUTION.md §14 (grounding integrity).**
     `gate_reference_closure(spec_path, project_dir=...)` (`scripts/foundry_audit_preconditions.py`,
     the retired engine's G-4 gate, kept as a cheap deterministic lint).
     Every `[[feat-slug]]` / `its authorizing spec` / bare `AC-<PREFIX>-<N>` reference in the spec's
     normative region must resolve to a live, non-superseded corpus atom (authorized if the
     reference is load-bearing) — a dangling reference FAILS, naming it.

   `foundry-spec-lint.py` exits 0 (`OK`, both checks clean) or 1 (prints each finding to stderr,
   prefixed `OVERSIZE:`/`DANGLING-REFERENCE:`) — treat a non-zero exit as a Phase-0 REFUSE.
2. **Normative-region presence.** The spec carries a delimited `<!-- normative -->` …
   `<!-- /normative -->` region with **at least one** stable AC-ID (reuse `_normative_region`/the
   AC-ID pattern from either preconditions module — not wrapped by `foundry-spec-lint.py`, since an
   absent normative region is a structural defect the size/reference checks above can't even reach).
   An absent or empty normative region REFUSES — there is nothing to review.
3. **Release corpus coherence — only when `<spec>` belongs to a release (charter
   `coherence-and-two-rounds`, AC-CTR-1/-2).** A spec "belongs to a release" when its atom id is
   named in a `.foundry/releases/<release-id>/release.yaml`'s `atoms:` list, or it lives under that
   release's `.foundry/releases/<release-id>/charters/` directory. When it does, run the EXISTING
   advisory sweep (never re-implement its detection logic — `scripts/foundry-coherence-check.py`
   is out of scope for this skill to change):
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-coherence-check.py" --scope specs
   ```
   Do this **before Phase 1 dispatches for any atom of that release** — the sweep is cheap
   (stdlib-only, always a fresh walk), and running it every time a release atom reaches Phase 0 is
   how "before any of them is authorized" (AC-CTR-1) holds without extra bookkeeping of which atom
   went first. Parse the single JSON document on stdout:
   - `counts.broken == 0` (exit `0`) → continue to Phase 1 as normal — the release corpus is
     coherent as far as this sweep can tell.
   - `counts.broken >= 1` (exit `1`, `findings` non-empty) → **this is not the sweep's own
     advisory-only boundary** (that boundary is for the standalone `/foundry:coherence-check`
     verb — see `skills/coherence-check/SKILL.md`; it never gates a merge). **Inside
     `/foundry:spec-review`, a nonzero `findings` list across the release's atoms is a coherence
     Block.** For each `findings[]` entry, derive the **owning atom of each side** — the path
     segment immediately after `specs/` (or, for a charter-lane atom, the charter filename's
     slug) for `src`, and the same segment parsed out of the raw `target` string, **even when
     `target` did not resolve** (`resolved: null` — the broken citation is itself the trace of the
     drift; do not require it to resolve to name the other side). Emit **one Block per finding,
     naming BOTH owning atoms' spec paths** (never only the citing side) — this is "a contradiction
     between two atoms Blocks authorization naming both owners," not a one-sided broken-link note.
     **Authorization of EITHER named atom stays refused until that Block is cleared** (a corrected
     citation, or a reconciled claim recorded in both specs) — refuse Phase 0 for both until then.
   - `provenance.source == "error"` (exit `2`) is an **operational failure** (missing or
     mis-resolved corpus root, or the walk found zero nodes) — REFUSE Phase 0 the same as any
     other pre-lint failure; do not treat it as the advisory `1`.

## Phase 1 — the ceremony-tiered question fan-out (fresh context each, via `spec-reviewer`)

**Phase-1 question mask (feat-foundry-ceremony-tiering).** Before dispatching, read the tier
`/foundry:intake` printed (`scripts/foundry_ceremony_tier.py`'s one-line classification, carried
forward from discovery exit) and dispatch **exactly the questions that tier's mask names** —
never all three unconditionally:

- **`trivial`** — no Phase-1 questions dispatched; see "the `trivial` tier" below (the review is
  skipped, token-gated).
- **`small`** — the consolidated **steel-man + adversarial** question ONLY (question 2 below);
  prior-art and the per-AC rubric are skipped for this tier.
- **`standard`** — all three questions below.
- **`large`** — all three questions below, plus the mandatory decomposition check (see below).

The conditional one-shot security question is bound to the sensitivity trigger, not the tier —
dispatch it whenever the classifier's mask marked `security_question` REQUIRED, independent of
which tier fired the mask above (see "Conditional — one-shot security question" below).

Dispatch the **`spec-reviewer` agent** (`agents/spec-reviewer.md`) as **separate, fresh-context
invocations**, each scoped to exactly ONE question — never one combined mega-prompt (a single
dispatch answering multiple questions loses the fresh-context independence that makes each lens
honest; this is the SAME "critic ≠ reviser, separate fresh context" principle the retired engine
enforced, applied to the surviving high-yield lenses instead of a five-phase loop). Every
dispatched question reads the **same** spec text (there is no between-question remediation in
Phase 1 — that is Phase 2's job):

1. **Prior-art** (CONSTITUTION.md §14) — *"Is this the approach the best agentic-engineering /
   platform / domain shops actually build, or a category error — a bespoke re-invention of a
   solved problem?"* The proactive complement to `research-first` at intake (which should already
   have produced the spec's `## Prior art / industry grounding` section); this question checks
   whether that grounding actually holds up, not whether the section merely exists. The
   highest-value lens in the retrospective (the `gitops-surfaces` category error, the phantom-atom
   postmortem) — kept as a one-shot question (it never needed a loop; it terminates clean from the
   first pass).
2. **Steel-man + adversarial, consolidated** (CONSTITUTION.md §13/§17) — *"Assume the design is
   right: where is it under-specified? Assume an adversary: where does it fail open, what's a
   vacuous-pass hole?"* ONE consolidated dispatch — the retired engine's own data showed these two
   lenses catalogued mostly the same finds (taxonomy, not yield); merge them permanently.
3. **Per-AC rubric** (CONSTITUTION.md §13, advisory) — score **each acceptance criterion once**
   against completeness / clarity / measurability / consistency. Grade the **requirement's prose**,
   never the code or runtime behavior — that boundary belongs to the merge floor's CI checks +
   certification. This is
   **advisory**: it sharpens the spec, it never blocks by itself (the retired engine's own
   `requirement-quality` class was correctly non-gating; this replicates that, scored once instead
   of on a treadmill).

**Conditional — one-shot security question.** A **security-flagged** spec (an auth / secrets /
supply-chain path in `scope.allowed_paths`, or a blast-radius keyword in the spec text) adds **ONE
additional** one-shot security-focused dispatch (*"attack the security/trust floor of this
design"*) — never a loop, never repeated across rounds. The real, enforcing security floor stays
the **`security-reviewer`** subagent at PR-diff time (CONSTITUTION.md §I.3) — this spec-time
question is a proactive front-load, not a substitute for it.

## Phase 2 — the remediation rounds (capped at two; a third is the operator's fork)

Consolidate every finding from Phase 1 (dedupe overlapping Block/Risk findings across the three
questions) and revise the spec, addressing each — or explicitly deferring a non-blocking one,
naming it in the spec's `## Clarifications` or `## Residuals` section (CONSTITUTION.md §15).

**Round budget (charter `coherence-and-two-rounds`, AC-CTR-3).** Phase 2's first pass is **round
1**. If findings remain unresolved after round 1, **exactly one further remediation round (round
2)** is allowed — dispatch it the same way, addressing what round 1 left open. **There is no
automatic third round.** A third round requires an **operator decision recorded in the spec's
`## Clarifications` section** (naming what rounds 1 and 2 left open and why a third is warranted)
before it runs — absent that recorded decision, a spec still contested after round 2 is either
**decomposed** (it was probably oversize/multi-capability to begin with) or gets an
**operator-directed disposition** (ship with the residual named, or park) — never a silent
automatic loop. This is the same "no re-litigating" discipline the retired engine's own
value-density curve motivated (87% of real findings resolved in ≤2 rounds, convergence never
actually reached beyond that; a prior run reached 4 rounds / 26 Blocks and never converged) —
codified here as a hard cap rather than a norm.

**A finding unchanged across two rounds is reported once, as "unresolved" — never re-raised
verbatim a third time** (AC-CTR-5). If round 2 leaves a finding exactly where round 1 left it,
record it once in the review evidence as unresolved and let the operator-fork decision above
(or decomposition/park) dispose of it — do not spend a round restating an already-recorded
finding.

**Block requires a locator, or it downgrades to Risk (AC-CTR-4/-5).** When consolidating
findings, any finding categorised **Block** that cites **neither** a checkpoint id
(`AC-<TOKEN>-<n>`) **nor** a `file:line` locator is **downgraded to Risk** — record why in the
review evidence (e.g. "downgraded: no AC-ID/file:line locator given"). `agents/spec-reviewer.md`
states this rule directly to the reviewer persona so it is applied at the source; Phase 2
re-applies it defensively when consolidating, in case a Block without a locator slipped through.

**Dedup before you re-litigate a finding twice** (feat-foundry-review-fanout-hardening,
AC-RFH-12/-13 — the human/agent-run mirror of `workflows/release-wave.js`'s
`consolidateFindings`/`assembleReviewResult` for the code-side `release-wave` fan-out). Two
questions quoting the same defect pay for the same fix twice if you triage them separately:

- **The dedup key is the whitespace-normalized evidence snippet, file path, and category —
  not the finding title or line number.** Two findings merge only when all three match (a
  divergent category or a divergent file never merges — same text in two files, or two
  categories at one site, are two distinct findings, not one). When findings merge, keep the
  **strictest** severity and note every location the finding was raised at; never let a merge
  soften a finding or silently drop a distinct one.
- **A Block that could not be verified remains a Block, tagged `could not be verified` —
  never silently demoted to a non-blocking note.** A reviewer/verifier that fails, times out, or
  cannot reach a verdict is an absence of evidence, not evidence of absence; only an explicit,
  well-formed refutation (a concrete reason the finding does not hold) demotes it, and only that
  one finding.

## Phase 3 — record the review evidence, then operator merge = authorization

**Record the review, content-bound to the spec (before authorize, not instead of it).** A
single-pass review is now the NORMAL case, not an exception — so it earns its own evidence row,
the same way the retired engine's audits did, rather than routing through the operator-exception
skip token every time. Run:

```bash
CLAUDE_PROJECT_DIR=<workspace> python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-audit-record.py" \
  --spec <spec> --rounds 1 --operator <op_id> --tier single-pass-review --verdict plateau-clean
```

This writes a row to `.foundry/audit-ledger.jsonl` bound to the spec's CURRENT `spec_sha256` (the
same evidence file the retired engine wrote to — `scripts/foundry_audit_ledger.py`).
`--tier` is a free-form string (no enum constraint on the CLI) so `single-pass-review` names this
disposition honestly rather than overloading one of the retired engine's own tier aliases (T0–T3)
or model aliases; `--verdict plateau-clean` is a real `V2_VERDICTS` member (a clean terminus, no
open Critical/High/Medium) — the correct verdict for a review that completed its one remediation
round with nothing left open (a review that halted with a real blocker unresolved should NOT
record `plateau-clean`; escalate to the operator instead, per the Anti-patterns below).
**This row is exactly what `/foundry:authorize`'s existing audit-ledger precondition
(`foundry_audit_ledger.find_audit`) reads** — `find_audit` matches on `spec_sha256` + `rounds >= 1`
+ a non-`{fail,rejected,abandoned}` verdict, which a `plateau-clean` row satisfies with no code
change and no special-casing. `/foundry:authorize` step 2 (`skills/authorize/SKILL.md`) finds this
row the same way it always found a deep-spec-audit row; the `--skip-audit-reason` escape hatch is reserved
for the genuine exception (an atom that skipped review outright, an operator override) — see that
skill for when it actually applies.

**Then the operator merge.** The operator reviews the consolidated findings + the revised spec
diff, and **merging the spec to the workspace `main` IS the front-authorization** (CONSTITUTION.md
§I.1 — "approval is the spec merged to the workspace main; git history is the ledger"). For a code
atom that also carries a sibling `acceptance-contract.yaml`, `/foundry:authorize` still performs
the mechanical freeze (spec + contract hash binding), now finding the row recorded above.

## Anti-patterns

- **Re-introducing a remediate-between-passes loop.** At most two remediation rounds, by
  default — that is the measured knee of value, not a corner cut. A third round without a
  recorded operator decision in `## Clarifications` is the loop this default exists to prevent.
- **Re-raising a finding round 2 already restated verbatim.** Record it once as "unresolved" and
  dispose of it via decomposition, park, or the operator fork — do not spend a round on it twice.
- **Letting a locator-less Block through as a Block.** No AC-ID and no `file:line` → downgrade to
  Risk and say why (AC-CTR-4/-5) — never wave it through un-downgraded because it "sounds severe."
- **Combining the three Phase-1 questions into one dispatch.** Fresh context per question is what
  makes each lens honest; a combined prompt reintroduces the same-context bias the retired engine's
  critic/reviser split existed to avoid.
- **Passing `allow_oversize`/an override to the size ceiling.** There is none, by design — decompose.
- **Reaching for `/foundry:audit`/`workflows/spec-audit.js` by default.** It is dormant-invocable for
  an exceptional deep audit only (see `skills/audit/SKILL.md`) — never the default path a normal
  atom runs.
- **Treating the per-AC rubric as gating.** It is advisory (CONSTITUTION.md §III), same as the
  retired engine's `requirement-quality` phase.
- **Recording `--verdict plateau-clean` over a genuinely unresolved Block/Risk finding.** The
  ledger row is evidence the review actually happened AND concluded clean — a review that halts
  with a real blocker needs the operator's judgment (escalate, don't launder the row).
- **Reaching for `--skip-audit-reason` as the routine path.** That flag is the operator-only
  exception (an atom that skipped review outright); the normal path is the evidence row this
  Phase records, which `find_audit` reads exactly like a deep-spec-audit row always satisfied it.

## Inputs / Outputs

- In: `<spec>` (+ its sibling `acceptance-contract.yaml` if present).
- Out: a Phase-0 pass/REFUSE, the three (or four, security-flagged) Phase-1 findings sets
  (categorized Block/Risk/Nit per `spec-reviewer`'s convention), the Phase-2 revised spec, and the
  Phase-3 authorization (the merge itself).
