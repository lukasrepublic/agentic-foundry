# How Foundry compares — honestly

Every tool below is good at what it actually does. This page states what each does, what
Foundry does differently, and — first, because it's the rarer courtesy — **when you should
not use Foundry.**

## When NOT to use Foundry

- **Exploratory prototyping / vibe-coding a throwaway.** Ceremony would be pure tax. Use
  plain Claude Code — our `/foundry:mode-interactive` *is* plain Claude Code, on purpose.
- **You're not on Claude Code.** The verbs are Claude Code skills. The *artifacts* (specs,
  contracts, journeys — plain YAML/markdown/git) survive without the tool, but the
  workflow is Claude-Code-native. No GitLab support yet, stated plainly.
- **You want an agent that merges without you.** We built the opposite, deliberately: the
  operator's sign-off is the terminal step and there is no unattended-merge mode.
- **A tiny team that just wants better PR review comments.** A review bot (CodeRabbit,
  BugBot, native `/review`) is cheaper to adopt and may be all you need.

## vs the SDD tools (Spec Kit, OpenSpec, Kiro, BMAD, GSD)

They generate excellent spec/plan/task documents; several have far larger communities than
we do. **They all stop at the documents.** No signed authorization, no enforcement at the
merge seam, no certification against the running app — with Kiro's honorable exception
(property-based tests from specs; its approvals stay IDE-local and its specs stay mutable).
Foundry picks up exactly where they stop: frozen, hash-bound acceptance contracts; an
operator authorization step with no skip; the tiered merge floor; deploy-once
certification. If you already use Spec Kit-shaped artifacts, they map onto our intake
naturally — the pipelines are complementary, not rivals.

## vs review bots (CodeRabbit, BugBot, Copilot Review, Qodo)

They review whatever PR shows up, advisorily, with one lens. Foundry reviews **against an
authorized contract** (the diff has a frozen definition of done to be judged by), routes
**persona-diverse reviewers** (general + security + spec-shape — the pattern that
empirically catches what single-lens review misses), and sits on a floor that can actually
block. Run both happily: a review bot's comments and Foundry's governance answer different
questions.

## vs agent platforms (Devin, Codex, Cursor agents, OpenHands)

They govern **execution risk** — sandboxes, permission matrices, isolation. Nobody there
governs **delivery authorization**: what was approved to be built, by whom, against which
acceptance criteria, and whether the merged thing was certified against the running
release. Foundry gates work *any* agent produced; it doesn't compete for the generation.

## vs bare Claude Code

The substrate keeps absorbing mechanics (Agent Teams, Dynamic Workflows, multi-agent
review) — good; we rebuilt on those primitives when it happened and deleted our own
versions. What stays non-native is the semantic layer: what a spec is, what "authorized"
means, what a merge floor requires, what "certified" means, and how a release earns a
human signature. That layer is this plugin.

## vs capability packs / harness kits (skill, agent, and command catalogs)

This category equips a coding agent's session with more capability — a catalog of skills,
agents, or slash commands installed into the harness so it knows how to do more things. Its job
ends at the session boundary: it does not author a frozen, hash-bound acceptance contract, does
not gate a merge behind a signed operator authorization, and does not certify a release against
the real running app. Foundry's job starts exactly where that job ends. The relationship is
complementary, not competing: a capability pack equips the session, Foundry governs the
delivery, and you can run both — install whichever catalog you like, and let Foundry sit at the
seam where a spec becomes an authorized, floor-gated, certified change.

## The one-table version

| Capability | SDD tools | Review bots | Agent platforms | Bare Claude Code | **Foundry** |
|---|---|---|---|---|---|
| Spec/plan artifacts | ✅ | — | ephemeral plans | CLAUDE.md conventions | ✅ + frozen hash-bound contracts |
| Authorization before build | IDE approvals at best | — | — | — | ✅ operator-signed, no skip |
| Merge-seam enforcement | prompt packs | advisory | — | hooks (DIY) | ✅ tiered floor, honestly labeled |
| Certification vs the running app | Kiro: property tests | — | — | — | ✅ deploy-once + real journeys |
| Provenance (spec → merged change) | — | — | line-attribution (Cursor) | git history | ✅ contract-pinned convention + shipped reader (writer is adopter-side today) |
| Human authority | varies | n/a | sandbox-level | you | ✅ terminal sign-off, by design |

*Claims about other tools reflect mid-2026 public documentation; corrections welcome —
file an issue and we'll fix this page.*
