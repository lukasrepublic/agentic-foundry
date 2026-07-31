# Design rationale & trust model

The *why* behind what ships — including, precisely, what Foundry protects you from and what
it deliberately does not. If you evaluate governance tools by their honesty about limits,
start here.

## The problem

LLM code factories fail predictably: an agent produces something that compiles, passes
shape-tests, and is marked "done" — but is broken at runtime (**status ≠ functional**).
More prompting doesn't fix this; a delivery discipline does. The industry data made it
mainstream: individual output up, review time up sharply, trust in AI-written code low —
the constraint is verification, not generation.

## The trust model

Four floors, none ever relaxed; each names exactly what it enforces:

1. **Front-authorization.** Specs are authored atomically (stable AC-IDs, a hard 14-AC/8k-word
   ceiling), single-pass reviewed (three fresh-context lenses), then **authorized by the
   operator**: the spec + acceptance-contract hashes are frozen and signed. In the factory
   flow an unauthorized spec cannot reach `main` — approval is the spec merged to your
   workspace's main; **git history is the ledger**.
2. **The merge floor.** Your platform's own enforcement, tiered honestly (see
   [merge-floor.md](merge-floor.md)): required status checks where your plan supports
   rulesets (Tier A); always-reporting checks + a fail-closed client-side git-discipline
   hook where it doesn't (Tier B — *labeled* advisory, never silently overclaimed).
3. **Security review** on any diff touching auth, secrets, or supply-chain surfaces —
   a separate-context reviewer persona, routed by path.
4. **Typed contracts + git discipline.** Acceptance contracts are schema-validated YAML;
   no force-push to protected branches; PR-then-merge, always.

Then the tail: **certification** (deploy the release once, run every atom's real Playwright
journeys against that instance — refuse, never vacuously pass, when journeys are missing)
and the **operator's terminal sign-off**, a recorded practice note that is deliberately
**not** machine-enforced.

```
        what the machine enforces                what stays human
 ┌────────────────────────────────────┐   ┌──────────────────────────────┐
 │ 1. front-authorization  (no skip)  │   │  the AUTHORIZE decision      │
 │ 2. the merge floor      (tiered)   │   │  the sign-off after testing  │
 │ 3. security review      (routed)   │   │  every scope/risk judgment   │
 │ 4. typed contracts + git discipline│   │                              │
 │    certification        (refuses   │   │  the automation makes these  │
 │                     when vacuous)  │   │  INFORMED — never replaced   │
 └────────────────────────────────────┘   └──────────────────────────────┘
```

## Gates make problems visible, not impossible

The doctrine that shapes every design decision: prefer outcome-level controls that cannot
be dodged by implementation choice over mechanism-level bans that must enumerate every
bypass. A control whose only justification is "an adversarial agent could…" gets priced
against the human review it supposedly replaces — and usually loses. The operator tests
and signs off last; the automation's job is to make that judgment *informed*, not to
replace it. This bounds how much anti-gaming machinery is ever worth building.

**What this deliberately does not guarantee:** on a single host the agent is co-resident
with the operator, so any client-side mechanism is, in principle, gameable by the agent.
Tier B names this instead of hiding it. If you need adversarial-grade enforcement, use
Tier A (server-side required checks) — the platform's enforcement, not ours, is the part
an agent cannot edit.

## Every gate earns its keep

The design method, stated as the rule it is: **every gate must name the observed failure it
prevents, or it does not ship.** Governance here is treated as a cost to be justified by
evidence, not a virtue to be accumulated:

- A review pipeline runs the number of passes the data says pays for itself — not more.
- Enforcement the platform already provides (branch protection, required checks) is *used*,
  never duplicated — a bespoke copy of a server-side control is strictly weaker than the
  original, minus the platform's tamper resistance.
- Ceremony is tiered to blast radius: a small change earns a small process, with the
  zero-ceremony interactive mode as a first-class, documented lane — not a workaround.

The result is a deliberately small enforcement core with a large *optional* catalog around
it. What ships is what earned its place.

## The layer model

```
BRAND       "Agentic Foundry"           ← metaphor lives ONLY here
NAMESPACE   foundry                      ← the plugin namespace
PRIMITIVES  [foundry]  the governance semantics: contracts, authorization,
                       wave planning, certification, the floor discipline
            [native]   Anthropic primitives — Agent · Workflow · Skill · hook · MCP
            [external] git · gh · your CI · your branch protection
```

**Native-primitive-first.** Dispatch is the `Agent` tool; fan-out is the `Workflow` tool;
review personas are subagents; retrieval is MCP. `[foundry]` exists only where the platform
has no opinion: what a spec is, who may authorize it, what "certified" means, and how a
release earns a human signature.

## Further reading

- [merge-floor.md](merge-floor.md) — the tier model and the exact hook semantics.
- [comparison.md](comparison.md) — honest positioning vs the alternatives, including when
  not to use this.
- [architecture.md](architecture.md) — the workspace ⟷ factory split.
- [glossary.md](glossary.md) — the vocabulary (atom, contract, floor, journey…).
