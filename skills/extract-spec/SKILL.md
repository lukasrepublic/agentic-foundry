---
name: extract-spec
description: Extract functional specifications FROM existing code (brownfield → spec). A read-only two-phase playbook — survey a codebase into a behavioral map, then promote a chosen capability into a CANDIDATE atomic spec in the industry-grounded template shape, routed through /foundry:intake (never a self-declared baseline). Trigger when the operator says "extract a spec from this code", "mine specs for <module>", "reverse-engineer a spec", "onboard this brownfield repo to specs", or "/foundry:extract-spec".
---

# /foundry:extract-spec

Turn *existing code* into *functional specifications*. You survey a codebase, mine the behavior it actually
enforces, and produce a **candidate** atomic spec — which then flows through the normal front door
(`/foundry:intake` → `/foundry:spec-review` → `/foundry:authorize`). You are read-heavy and write only a candidate
draft; you never authorize, merge, or baseline.

## The load-bearing discipline (read first)

- **Extracted ≠ authorized, and extracted ≠ desired.** The code may encode bugs; a snapshot of current behavior
  is a *characterization*, not a specification of intent. Therefore the output is an **un-authorized candidate
  draft** that MUST be routed through **`/foundry:intake`** (→ `/foundry:spec-review` → `/foundry:authorize`). Never emit an
  authorized or "baseline truth" spec, and never freeze a contract from extraction. The front-authorization
  floor is preserved: extracted behavior becomes normative only after the operator authorizes it.
- **Never invent behavior.** If the code does not clearly express a contract, record it as a
  `[NEEDS CLARIFICATION: <question>]` marker — do not fabricate a requirement (Example Mapping's Question card).
- **Cross-validate against callers.** The real contract is what callers rely on, not what a docstring claims
  (characterization-test discipline — capture ACTUAL, not intended, behavior; note it can pin existing bugs, so
  surface anything surprising as an uncertainty rather than "correcting" it).

## Prior art / industry grounding

Grounded in prior-art research on the code-to-spec mechanism (how brownfield code is safely mined into a
candidate spec) and on the emit shape (the template a mined capability is promoted into). Sources: Feathers,
*Working Effectively with Legacy Code* (characterization testing); Adzic *Specification by Example* + Wynne's
Example Mapping (flag uncertainty, don't invent); EARS/ISO-29148 (the Requirement/Invariant, measurable shape).
Comparable prior art in other spec-driven tooling (e.g. spec-mining features) is one corroborating datapoint,
not the source.

## Phase 1 — behavioral map (read-only)

Produce a comprehension artifact; change nothing.

1. **Detect the stack.** Find package manifests, framework configs, and entry points (routers, controllers,
   service facades, `main`/`index`/`cmd`). Ignore vendored/generated dirs.
2. **Group into capabilities.** Cluster entry points + their backing modules into cohesive, kebab-case
   **capabilities** by shared service/namespace. A capability is one cohesive set of behaviors.
3. **Operator-gated scope.** Present the capability list and ask which to mine first — do NOT mine an entire
   monorepo at once (specs that outpace usage rot). 
4. **Inventory the enforced behavior** for the chosen capability as flat assertions, each tagged **Requirement**
   (triggered) or **Invariant** (always-true), with the code-enforcement anchor (`file.method`) and the domain
   entities. Record uncertainties inline.

### Sample-and-expand (the token strategy)

Do not read every file. **Sample** the entry surfaces first (routers/controllers/facades ≈ most of the
behavior), then **expand** one level down each behavior's **call chain** to verify it. Stop at an external
boundary (DB/HTTP/queue), after ~3 consecutive files yield nothing new, or at a bounded file cap; **defer** the
unread remainder as an explicit `deferred:` list for a later pass.

## Phase 2 — promote a capability to a candidate spec

For the operator-chosen capability, write ONE candidate `feat-*.md` in the industry-grounded template shape defined by the foundry context kit
(`context/feat-spec-template.md`, shipped in the plugin):

- Acceptance criteria as **singular, measurable** statements in **EARS** phrasing, each tagged **Requirement**
  or **Invariant**, with stable AC-IDs.
- A `## Prior art / industry grounding` section noting this was extracted from `<source>@<commit>` (so the
  candidate's origin + freshness are traceable), an `## Out of scope` boundary, and any
  `[NEEDS CLARIFICATION: …]` markers carried forward.
- Keep it atomic — one capability-behavior per spec; split a multi-capability capability.

## Hand-off (compose with the shipped machinery)

The candidate is a draft, not a decision. Route it:

1. **`spec-reviewer`** (optional but recommended) — the independent shape/quality audit (EARS, singular,
   measurable, Requirement/Invariant, grounding) before intake.
2. **`/foundry:intake`** — the front door; refine the fuzzy candidate into an intake-shaped spec.
3. **`/foundry:spec-review`** → **`/foundry:authorize`** — only here does extracted behavior become normative.

You output a candidate + its behavioral map; the operator and the gates decide what becomes truth.

## Contract seeding — test surfaces belong in `allowed_paths`

- **Seed checkpoint test surfaces into `allowed_paths` at draft time.** When the draft
  contract's checkpoints name a NEW candidate test suite (`surface: test:<path>` for a file the build
  must CREATE), seed that test path into `scope.allowed_paths` in the same draft — a contract whose
  own checkpoints require a file its scope forbids is internally inconsistent, and
  `foundry-authorize` now fails that freeze CLOSED (the surface⊆scope check). Pre-existing test
  files the checkpoints merely RUN need no seeding; suite KEYS (`test:<key>` with no `/`) are not
  paths.
