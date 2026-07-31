# How to run author/approver separation with CODEOWNERS

Foundry deliberately ships **no roles system, no approver workflow, no second review UI** —
GitHub already has one, server-enforced and tamper-evident. This guide binds Foundry's
front-authorization to it, so "the person who wrote the spec is not the person who approved
it" becomes a platform guarantee instead of a team norm.

## The idea in one picture

```
  Dana (author)                        Lukas (approver)
      │                                      │
      │  writes spec + contract              │
      │  opens PR to the workspace repo      │
      ▼                                      │
 ┌─────────────────────┐   CODEOWNERS on     │
 │ PR: specs/features/…│──  specs/** ───────▶│  review required from an owner;
 └─────────────────────┘                     │  GitHub refuses Dana's own approval
      │                                      ▼
      │                            approves + merges
      ▼
 the merged spec on main IS the front-authorization
 (git history is the ledger; the freeze binds the hashes)
```

The load-bearing fact: a frozen acceptance contract is a **tracked file** with no effect
until it's merged to the workspace `main` via a PR. GitHub gates the artifact — it doesn't
need to see the `/foundry:authorize` command.

## Setup (once, about five minutes)

1. **CODEOWNERS** in the workspace repo:

   ```
   # .github/CODEOWNERS
   specs/**  @your-org/spec-approvers
   ```

2. **Require review from code owners** on `main` (Settings → Branches → branch protection,
   or a ruleset): require at least one approval *from code owners*, and leave GitHub's built-in
   "author cannot approve their own PR" doing what it does.

3. That's it. No Foundry configuration exists for this, on purpose.

## What this gives you (and what it doesn't)

- ✅ **Server-enforced**: the separation is GitHub's guarantee, not a client-side check an
  agent could edit.
- ✅ **Tamper-evident**: who approved what, when, is the PR record.
- ✅ **Works today** with any team size; scale the owners team as you grow.
- ⚠️ **Requires enforced rulesets** (public repo, or private on a paid plan) — on a
  Tier B repo (private + Free) GitHub stores but does not enforce the requirement. The
  honest fix is Tier A, not a Foundry-side shadow control
  ([merge-floor.md](../merge-floor.md)).

## Why Foundry doesn't ship its own

Any Foundry-side roles/approver mechanism would be a strictly weaker copy of the above —
client-side, editable, and a second system to audit. The design rule
([DESIGN.md](../DESIGN.md)): enforcement the platform already provides is used, never
duplicated. Any future proposal to build roles into Foundry must first state what GitHub
cannot do.
