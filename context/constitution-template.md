# The <Project> Constitution

**Adopted:** <date> · **Checked by:** the single-pass spec review (`/foundry:spec-review` —
every question in §III below is a review criterion) and the operator's judgment. This is the
distilled, *checkable* form of your project's floors + engineering guardrails — generic and
adopter-facing (this workspace's own instance is `CONSTITUTION.md` at the repo root; keep this
template unedited so `claude plugin update` can deliver improvements to it). It changes only by
deliberate operator edit — copy this file to `CONSTITUTION.md`, fill the `<placeholders>`, and
delete any section your project's mode (§V) doesn't carry.

## I. The floor (never relaxed — principles, not machinery)

1. **Front-authorization** — no build starts from a spec the operator hasn't approved; approval
   is the spec merged to your workspace `main`. Git history is the ledger.
2. **The merge floor** — `main` accepts only PRs with green required checks (tests, a lane
   signal, security routing). An advisory (non-blocking) check must say so honestly wherever it
   reports — never silently promoted to load-bearing.
3. **Security review** — any diff touching auth / secrets / identity / supply-chain surfaces
   gets a fresh-context security-reviewer pass, recorded on the PR before merge.
4. **Typed contracts at boundaries; git discipline** — no force-push to `main`, branch per
   change, PR-then-merge, no admin-override merges.
5. **Operator sign-off is the terminal gate** — every release gets the operator's own test
   pass, last. A practice, deliberately never machine-enforced; it caps what any control below
   is worth building. (See CLAUDE.md / your project's control-plane doc for the fuller framing
   of why this is a practice and not a control.)

## II. Governance guardrails (how controls earn their existence)

6. **No control without a named, observed failure it prevents** — and price it against floor
   #5 before building. "An adversarial agent could…" is not, by itself, a named failure.
7. **Outcome-level over mechanism-level** — prefer checks an implementation choice cannot
   dodge (a test that fails, an E2E journey that breaks, a diff a reviewer reads) over bans
   that must enumerate every evasion. A proposed mechanism-level control is an operator fork.
8. **Tests live in the test suite** — one place (pytest/CI, or your stack's equivalent). A
   check born anywhere else (a standalone CLI, YAML-embedded logic beyond one quoting layer,
   prose-conformance grep) is a finding, not a contribution.
9. **Subtraction ships with every wave** — a wave that only adds lines states why. Deprecated
   means deleted by the next release, not parked.
10. **One planning surface** — the release manifest in git, materialized as the native task
    graph your harness renders. Anything else is a generated view.
11. **Advisory means visible, never load-bearing** — a chronically-red blocking check trains
    bypass; ship it advisory with a named tuning item, or don't ship it.

## III. Spec standards (what the review pass checks)

12. **Atomic and bounded** — one capability per spec; **set a binding size ceiling (words / AC
    count) with no override** — oversize means decompose. Measure your own review-value curve
    and run the pipeline at its knee (this project's own measurement found 1–2 review passes;
    yours may differ — measure, don't assume).
13. **EARS-phrased, singular, measurable ACs** — Requirement (triggered: When/While/If-Then)
    vs Invariant (always-true) tagged correctly; no vague adjectives without a measurable
    criterion; no unresolved TBDs in the normative region.
14. **Prior art before novelty** — every spec grounds its approach in what the industry builds
    (research-first at intake, one prior-art question at review). A category error re-grounds;
    it is never patched in place. Do not build what the industry doesn't build.
15. **Out-of-scope is explicit** — what a reasonable implementer might assume is included but
    isn't. Deferred work is named, not implied.
16. **UI/UX intent is a first-class spec input** — design artifacts (a structured design
    export, annotated mockups, target screenshots) live beside the spec; journeys carry AC tags
    and become the E2E suite verbatim.
17. **Checkpoints are executable and honest** — a verification command a reviewer can run,
    proven against the real tree before freeze; never fabricated evidence, never an assertion
    weakened to pass.

## IV. Engineering standards

18. **Native primitives first** — your harness's own tasks, workflows, subagents, worktrees,
    hooks, and permission modes before any custom machinery; a wrap must be thin and must say
    what it wraps. Novelty is a cost justified only by a real, researched gap.
19. **Every version pinned, every pin deliberate** — a single source-of-truth version matrix;
    exact pins (full-SHA for CI actions), research-timestamped, upgraded as reviewed one-line
    edits. No floating tags, no unpinned installs.
20. **Isolation by default** — agent builds run in worktrees (or your isolation primitive) with
    their own env allocations and a declared write scope; agent identity is attributable
    (trailers or a bot identity).
21. **Honest reporting** — a partial run is distinguishable from a full one; a degraded tier
    says so; silence is never success. Findings are flagged, not silently worked around — the
    flag is the contribution.

## V. The modes (who carries how much of this)

- **`<factory mode name>`** — all of the above, in full: spec → plan (task-graph waves) →
  build → integrate → certify locally → operator acceptance → staging.
- **`<lean/noninteractive mode name>`** — I, II, IV in full; III reduced to a one-page charter
  (goal, EARS ACs, out-of-scope, literal scope, verification, merge policy).
- **`<interactive mode name>`** — I.2/I.4 and IV only. Zero spec process; the operator is
  present.

---

*Fill every `<placeholder>`, delete or rename the mode names in §V to match your own stage-mode
vocabulary, and set your own numbers for the binding ceiling in §III.12. This is a template —
`context/README.md` explains how the plugin ships it; an adopter workspace copies it to its own
`CONSTITUTION.md` rather than editing this file in place.*
