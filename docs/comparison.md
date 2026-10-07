# How Foundry compares — honestly

Every tool below is good at what it actually does. This page states what each does, what
Foundry does differently, and — first, because it's the rarer courtesy — **when you should
not use Foundry.**

## When NOT to use Foundry

- **Exploratory prototyping / vibe-coding a throwaway.** Ceremony would be pure tax. Use
  plain Claude Code — our `/foundry:mode interactive` *is* plain Claude Code, on purpose.
- **You're not on Claude Code.** The verbs are Claude Code skills. The *artifacts* (tickets, specs,
  contracts — plain issues/YAML/markdown/git) survive without the tool, but the
  workflow is Claude-Code-native. No GitLab support yet, stated plainly.
- **You want an agent that merges without you.** We built the opposite, deliberately: the
  operator's sign-off is the terminal step and there is no unattended-merge mode.
- **A tiny team that just wants better PR review comments.** A review bot (CodeRabbit,
  BugBot, native `/review`) is cheaper to adopt and may be all you need.

## vs the SDD tools (Spec Kit, OpenSpec, Kiro, BMAD, GSD)

They generate excellent spec/plan/task documents; several have far larger communities than
we do. **They all stop at the documents.** No enforcement at the merge seam, no local-green gate
before the push — with Kiro's honorable exception (property-based tests from specs; its approvals
stay IDE-local and its specs stay mutable). Foundry's default path is deliberately lighter than
theirs: one artifact per change, a ticket whose `Done means` is a command, with the platform as the
gate. Measured over 60 days on two adopter workspaces, spec-and-contract paper outweighed shipped
code 1.5:1 to 8:1, so Foundry made the spec an **opt-in lane** (frozen, hash-bound contracts and an
operator authorization with no skip, for `security: true` work). If you already use Spec Kit-shaped
artifacts, they map onto that lane naturally — the pipelines are complementary, not rivals.

## vs review bots (CodeRabbit, BugBot, Copilot Review, Qodo)

They review whatever PR shows up, advisorily, with one lens. Foundry sends one fresh-context
reviewer (and a security reviewer on auth / secrets / custody paths) at a ticket whose `Done
means` is already green locally, and sits on a floor that can actually block. Run both happily: a
review bot's comments and Foundry's governance answer different questions.

## vs agent platforms (Devin, Codex, Cursor agents, OpenHands)

They govern **execution risk** — sandboxes, permission matrices, isolation. Nobody there
governs **delivery**: what was assigned, whether the session may stop while its check fails, whether
the push was green locally, and who signs off. Foundry gates work *any* agent produced; it doesn't
compete for the generation.

## vs bare Claude Code

The substrate keeps absorbing mechanics (Agent Teams, Dynamic Workflows, multi-agent
review, `/loop`, `/schedule`) — good; we rebuilt on those primitives when it happened and deleted
our own versions. What stays non-native is the delivery layer: what a ticket's "done" is, the
paper guard, local-green before push, and how a release earns a human signature. That layer is
this plugin.

## vs capability packs / harness kits (skill, agent, and command catalogs)

This category equips a coding agent's session with more capability — a catalog of skills,
agents, or slash commands installed into the harness so it knows how to do more things. Its job
ends at the session boundary: it does not keep the session working while its check fails, does not
gate a push behind local-green, and does not gate a merge on the platform's required checks.
Foundry's job starts exactly where that job ends. The relationship is complementary, not
competing: a capability pack equips the session, Foundry governs the delivery, and you can run
both.

## The one-table version

| Capability | SDD tools | Review bots | Agent platforms | Bare Claude Code | **Foundry** |
|---|---|---|---|---|---|
| Unit of work | spec/plan artifacts | — | ephemeral plans | CLAUDE.md conventions | ✅ a ticket with a runnable `Done means`; a spec is opt-in |
| Keeps going while the check fails | — | — | varies | — | ✅ Stop hook (three refusals, then it may end) |
| Local-green before push | — | — | — | hooks (DIY) | ✅ push and non-draft PR refused until recorded for HEAD |
| Merge-seam enforcement | prompt packs | advisory | — | hooks (DIY) | ✅ tiered floor, honestly labeled |
| Human authority | varies | n/a | sandbox-level | you | ✅ terminal sign-off, by design |

*Claims about other tools reflect mid-2026 public documentation; corrections welcome —
file an issue and we'll fix this page.*
