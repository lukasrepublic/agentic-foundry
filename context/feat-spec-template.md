# <Atom title>  (feat-<product>-<domain>-<capability>-<behavior>)

> **Human-readable intent.** What this atom does and why — the user-facing behavior, in
> prose a human writes and reads. One atom = one capability-behavior.

## Prior art / industry grounding

<!-- REQUIRED for any non-obvious atom (a new capability, an approach/architecture choice, an
unfamiliar domain). A mechanical/standard atom MAY replace this section with a single line:
"Standard pattern; no research needed." -->
How the best shops solve this — the industry pattern + trade-offs + grounding, per the research-first
discipline. Cite sources with the citation grammar (`context/citation-grammar.md`), e.g.
`[Doc: docs/research/<x>.md]`. This is the proactive complement to the deep spec audit's prior-art lens: it
prevents building what the industry doesn't build.

<!-- normative -->
## Acceptance criteria

<!-- Each criterion is SINGULAR (one capability — ISO/IEC/IEEE 29148 "singular") and MEASURABLE
(an objective pass/fail test exists). Tag each as **Requirement** (triggered) or **Invariant**
(always-true). Use EARS phrasing:
  - Requirement (event-driven): When <trigger>, the <system> SHALL <response>.
  - Requirement (state-driven): While <state>, the <system> SHALL <response>.
  - Requirement (unwanted):     If <condition>, then the <system> SHALL <response>.
  - Requirement (optional):     Where <feature is present>, the <system> SHALL <response>.
  - Invariant  (ubiquitous):    The <system> SHALL <property that always holds>.
Avoid negative "shall not" statements (they are unverifiable); phrase unwanted behaviour as
`If <condition>, then … SHALL …`. AC-IDs are STABLE — never renumbered when the prose changes (the
ID is the delta/trace anchor, per INCOSE A15). -->

- **AC-<TOKEN>-1** (Requirement): When <trigger>, the <system> SHALL <observable, testable response>.
- **AC-<TOKEN>-2** (Invariant): The <system> SHALL <property that always holds>.
<!-- /normative -->

## Scenarios (optional — for disputed / edge-case criteria only)

<!-- Concrete Given-When-Then examples build shared understanding for rules with contested or
boundary behavior (Specification by Example, Adzic). Do NOT use them for invariants or as a blanket
syntax, and keep them DECLARATIVE — imperative, UI-scripted scenarios are a documented anti-pattern
(brittle, implementation-coupled, scenario-explosion). Bind each to the AC-ID it illustrates. -->

#### Scenario: <name>  (illustrates AC-<TOKEN>-1)
- **Given** <context / pre-state>
- **When** <the single event>
- **Then** <the expected observable outcome>

## Out of scope / non-goals

- What this atom deliberately does NOT do — the boundary that keeps it atomic and prevents scope creep.

## Clarifications

<!-- Flag uncertainty rather than guessing (GitHub Spec Kit `[NEEDS CLARIFICATION: <question>]`;
Example Mapping's red Question card). Record resolved forks + their rationale, and leave any open
question as an inline `[NEEDS CLARIFICATION: <question>]` marker so it blocks a false assumption. -->

## UI/UX artifact slots (design/ sibling dir — for a UI-bearing atom only)

<!-- CONSTITUTION §16: UI/UX intent is a first-class spec input, not an afterthought.
Skip this whole section for a non-UI atom. -->

For an atom with a user-facing surface, keep a **`design/` sibling directory** next to this spec
(`specs/features/<product>/<domain>/<capability>/design/`) holding the structured design export,
annotated mockup(s), and target screenshot(s) this atom builds toward. Cite each with the citation
grammar (`context/citation-grammar.md`), e.g. `[Design-asset: design/checkout-flow.fig.json]`,
`[Design-asset: design/annotated-empty-state.png]`.

- `design/<export>.*` — the structured design export (e.g. a Figma/Claude-Design export) this
  atom is grounded on.
- `design/<name>-annotated.png` — an annotated mockup calling out the specific behavior/state
  this atom implements (not just the raw design file).
- `design/<name>-target.png` — a target screenshot of the DONE state, for a faithful implementer
  to compare against.

## Journeys (AC-tagged — becomes the E2E suite verbatim)

<!-- CONSTITUTION §16: a journey is a concrete, ordered user path through the
UI-bearing surface, TAGGED to the AC-IDs it exercises. Journeys are what `release-wave`'s wave
plan surfaces as `journeys: [...]` metadata and what a faithful implementer turns into the E2E
test suite — verbatim, not reinterpreted. Skip for a non-UI / pure-backend atom. -->

- **Journey: `<journey-tag>`** — <name the concrete path, e.g. "guest checkout, no saved card">,
  exercising AC-<TOKEN>-1, AC-<TOKEN>-2. `[Design-asset: design/<name>-target.png]`

## Design / notes

- Cite design assets + related docs with the citation grammar (`context/citation-grammar.md`),
  e.g. `[Design-asset: docs/design/<screen>.html]`, `[Doc: docs/architecture/<x>.md]` — for
  anything that does not fit the structured `design/` sibling-dir slots above (a related
  architecture doc, a non-UI supporting reference).

## Residuals (optional)

- Bounded, logged limitations knowingly accepted for this atom.

## Changelog

- v1.0 Draft.
