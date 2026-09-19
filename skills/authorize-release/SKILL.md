---
name: authorize-release
description: Release-level authorization DASHBOARD (/foundry:authorize-release) — displays the release's atoms with their routed lane (charter/factory) and readiness, then routes ONLY the next unblocked atom (all depends_on satisfied) to a single just-in-time /foundry:authorize invocation. The bulk-signing loop is RETIRED — there is no bulk script and no "authorize the release" shortcut; foundry-authorize.py takes exactly one --spec/--contract pair per call. Trigger after release-DRAFT-shaping, before implement.
---

# /foundry:authorize-release

A **display + routing** surface over a release's atoms — not a bulk-authorization loop.
Piiq's measured history (462 atoms authorized, 24 built — 438 authorized-never-built from
bulk pre-authorization of a backlog) is why the bulk-sign loop that used to live in this
skill's prose is **retired**: authorization is now **just-in-time**, one atom at a time, at
the moment that atom is next.

## When to trigger

- "authorize release `<slug>`", "/foundry:authorize-release `<slug>`" — after the
  release is DRAFT-shaped (atoms enumerated) and BEFORE implementation dispatch
  (steel-man-1 ordering).

## Procedure

1. **Resolve** the release → its ordered atom list. Across the list, resolve each atom's
   routed lane (charter lane by default; factory lane for the security set — see
   `skills/intake/SKILL.md` "Lane routing") and readiness (`depends_on` satisfied? spec or
   charter committed? contract drafted, for a factory-lane atom?).
2. **Display the dashboard.** Show the release's atoms with: name, lane (charter/factory),
   readiness, and current state. This is a read-only status view — it authorizes nothing by
   itself.
3. **Identify the next atom** — the first atom (in dependency order) whose `depends_on` are
   all satisfied and that is not yet authorized/built. There is exactly one "next" atom at a
   time; the rest of the release stays displayed but untouched.
4. **Route the next atom just-in-time.**
   - **Charter lane** — no `/foundry:authorize` call at all: the charter is committed and the
     isolated-worktree build starts directly (per `skills/mode/SKILL.md`'s noninteractive
     paragraph).
   - **Factory lane** — invoke `/foundry:authorize` for that ONE atom only:
     ```bash
     python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-authorize.py" \
       --spec <atom-spec> --contract <atom-contract> --operator <op> --mode <regular|lean> --yes
     ```
     `foundry-authorize.py` accepts exactly one `--spec`/`--contract` pair per invocation —
     there is no `--all`, and this skill never loops the call across the release's atoms.
5. **Hand off** the one just-authorized (or charter-committed) atom to
   `/foundry:mode-autonomous` (or `/foundry:dispatch`). Re-run this skill to pick the next
   atom once it lands; do not pre-authorize ahead of readiness.

## Anti-patterns

- **A bulk-sign loop over the release's atoms** — retired. This skill never authorizes more
  than one atom per operator confirmation; there is no procedure here that iterates
  `/foundry:authorize` across the release.
- **Pre-authorizing atoms whose `depends_on` are not yet satisfied** — just-in-time means the
  next atom, not the whole backlog.
- **Authorizing an atom whose spec skipped review** without the explicit operator token.
- **Self-confirming authorization** — the operator's confirmation on the *single* next atom is
  the authority; the dashboard display is not a confirmation.
- **On a harness denial** during the single next-atom authorization, see `docs/harness-denial-fallback.md` and STOP: hand back the exact denied invocation; never retry it — resolve it through settings, then resume with the next atom.
