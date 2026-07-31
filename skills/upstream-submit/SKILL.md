---
name: upstream-submit
description: 'Submit a triaged learning UPSTREAM to the framework as a GitHub enhancement-request issue (/foundry:upstream-submit). The adopter-side half of the adopter→framework loop: generify to the mechanism → fail-closed leak scan → provenance + dedup key → gh ISSUE. The ER is a REQUEST (a new /foundry:intake source), never a contribution to foundry main, so front-authorization is preserved. Trigger when a distilled learning would benefit EVERY adopter, not just this product.'
---

# /foundry:upstream-submit

The cross-repo promotion stage of the self-improvement loop. The within-repo loop
(`learn-capture → learn-distill`) distills session learnings into *local* HBK/memory/skill
candidates. Most learnings stop there — they are adopter-local. This verb handles the few that
belong **upstream in the framework**, carrying them across the adopter→framework **trust
boundary** without leaking proprietary context and without bypassing the both-modes floor.

## When to trigger

- "submit this upstream", "/foundry:upstream-submit", "file a framework enhancement request".
- After `learn-distill` surfaces a candidate that passes the **triage filters** below.
- NEVER for adopter-local specifics (seed data, infra naming, version locks, cost figures).

## Triage first — does this belong upstream? (apply filters in order)

| Filter (in order) | If it resolves here → |
|---|---|
| **(a) Generalizability** — would a *different* product on a *different* stack hit this? | **No → adopter-local.** Stays in your memory. This is the default; most learnings are local. |
| **(b) Native-first** — does a native Claude Code primitive already do this? | **Yes → don't request a build.** Adopt the primitive; at most a thin seam. |
| **(c) Build-on-substrate** — does it touch an L1 substrate (memory, sessions, model tiers)? | **Yes → request template *guidance*, not a new primitive.** |
| **(d) Shipped-surface** — does it fix a false-green / silent-failure / inert check in a SHIPPED foundry gate/verb/script/hook? | **Yes → `core-plugin`, HIGH.** The highest-value request. |
| **(e) Otherwise** — a generalizable capability/convention every adopter would want. | **`core-plugin`** (verb/script/hook/skill) or **`handbook-template`** (workspace-shape convention). |

Only `core-plugin` / `handbook-template` candidates get an ER. `adopter-local` never does.

## Procedure

1. **Generify.** Write the candidate as a markdown file with these sections (the verb enforces
   them): `# <title>`, `## Generalized problem` (the *mechanism*, not your incident),
   `## Evidence (sanitized)`, `## Proposed mechanism`, `## Triage bucket` (`core-plugin` |
   `handbook-template`). Restate everything as the framework concern — this is also the dedup key.
2. **Declare your redactions.** Put your product/customer/internal names in
   `.foundry/upstream-redaction.txt` (one literal/regex per line). The verb already enforces a
   built-in floor (structural Foundry-ecosystem ref shapes); your file adds your specifics.
3. **Dry-run (default).** `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-upstream-submit.py"
   --candidate <er.md>`. It scans **fail-closed** (refuses + names any leak, no network call, no
   label-ensure either), renders the ER with a provenance footer (adopter · foundry-version ·
   dedup-key), and prints the exact `gh issue create` command + target repo. **Read the rendered
   ER.** (eyes-on layer.)
4. **File.** Re-run with `--create` (uses your own `gh` identity — the per-project jail),
   or paste the printed `gh` command. Before filing, the verb idempotently **ensures** the
   `enhancement-request` label exists on the upstream repo via **probe-then-create**: it first
   reads whether the label already exists (a read-only GET), and only creates it when the probe
   reports it absent. **This is a pure create-or-noop**: when the label already exists, the ensure
   succeeds by reading only — it makes no write call and touches nothing on the target repo, so a
   second (or hundredth) consecutive run is exactly as safe as the first. This is deliberately
   *not* `gh label create --force` (the vendor's create-or-**update** affordance), which would
   PATCH the existing label's colour/description on every privileged run even when nothing needed
   to change. **If the label can't be ensured** — most commonly because you hold issue-create but
   not label-write permission on a repo you don't own, which is the expected common case, not an
   error — the verb does **not** abort: it degrades to filing an **unlabelled** issue plus a
   stderr warning (carrying the stable token `UPSTREAM-SUBMIT-LABEL-DEGRADED`) instead of failing,
   and still exits `0`. The maintainer labels an unlabelled issue on arrival. On success, the verb
   also prints the created issue's URL (`gh`'s own stdout).

   **Which GitHub identity it runs as.** If this project has declared a GitHub identity (a handle
   in `.claude/gh-identity`, the same jail the default workspace template's (`lukasrepublic/agentic-handbook`) `gh-account-guard.sh` PreToolUse hook
   enforces — this plugin ships no guard of its own; see `docs/identity-isolation.md`), every `gh` call the verb itself makes — and the command it prints — is composed with
   that identity: the child process runs with `GH_CONFIG_DIR=$HOME/.config/gh-<handle>` and with
   `GH_TOKEN` / `GITHUB_TOKEN` / `GH_ENTERPRISE_TOKEN` / `GITHUB_ENTERPRISE_TOKEN` / `GH_HOST`
   removed (those outrank config-dir auth), and the printed command carries a matching, shell-quoted
   `GH_CONFIG_DIR=...` prefix. **With no `.claude/gh-identity` declared** (the common single-account
   case, and the state in which the template's guard is itself dormant by design), nothing changes:
   the command prints bare (`gh issue create ...`, no prefix) and runs under whatever `gh` identity
   your session already has. Either way every element is `shlex.quote`d, so the printed text is
   always safe to paste into a shell as-is. **Note on the prefix's real job:** pasting the printed
   command into a plain terminal — no PreToolUse hook, no `direnv` guaranteed active — is where the
   prefix earns its keep, by pinning the paste to the right account. It does **not** change whether
   a PreToolUse guard admits the verb's *own* `gh` call — no guard parses an env-assignment prefix
   out of a command string; a guard's verdict depends only on the environment the invoking process
   already inherited.
   **If the declared identity is malformed** — the file exists and is non-empty but its contents
   don't match GitHub's handle grammar (alphanumerics and single hyphens, no leading/trailing
   hyphen, max 39 chars) — the verb makes **zero** `gh` calls and exits `4` (see below) rather than
   silently falling back to your ambient identity. This composition applies to **every** `gh`
   invocation the verb makes, including the read-only label probe above — it is a `gh` call like
   any other, and it always runs AFTER the leak scan, never before.
5. **That's it on the adopter side.** The maintainer triages the issue and, if accepted, runs it
   through `/foundry:intake → audit → authorize → build` — the ER is just a new intake source.
   You'll see it land in a foundry version bump (the CHANGELOG entry cites its `ER #<n>`
   marker); check `claude plugin list` against the marketplace tag, then
   `claude plugin update <plugin>@<marketplace>` (the id must be marketplace-qualified — a bare plugin
   name fails with `Plugin "<name>" not found`; `claude plugin list` prints the qualified form).

## Exit statuses

| Exit | Meaning |
|---|---|
| `0` | ok — dry-run rendered, or the issue was filed (labelled, or unlabelled-and-degraded — see step 4). |
| `1` | selftest red, or a candidate input error (e.g. a missing required section). |
| `2` | **REFUSED** — the fail-closed leak scan found a hit. No issue filed, no network call, no label-ensure. |
| `3` | **A `gh` invocation failed** — `gh issue create` exited non-zero, or `gh` is absent/not executable. A diagnostic naming the invoked command, `gh`'s captured stderr, and a remedy is written to stderr; no Python traceback ever reaches the output. |
| `4` | **Declared identity is unusable** — `.claude/gh-identity` exists, is readable, and is non-empty, but does not match GitHub's handle grammar. Zero `gh` invocations are made; the offending value is never rendered anywhere. Fix the handle or remove the file (which runs the verb under your ambient `gh` identity instead). |

## Anti-patterns

- **Submitting un-generified text** — an incident-specific report leaks context and won't dedup.
  Restate as the mechanism first (filter (a)).
- **Promoting adopter-local specifics** — seed data, infra naming, version locks, cost figures
  are not framework concerns.
- **Treating the leak scan as sufficient** — it is a floor (known shapes/tokens), not a ceiling.
  Generify + eyes-on are the other two layers; a novel proprietary noun you never declared can
  still slip through a bare scan.
- **Trying to PR into foundry `main`** — the channel is an *issue* by design. A direct
  contribution-to-`main` would bypass front-authorization; acceptance is a maintainer-gated
  factory run.
- **Reinventing a native primitive (b) or an L1 substrate (c)** instead of requesting guidance.

## Relationship to existing primitives

- `learn-distill` — the within-repo loop this extends with the cross-repo promotion stage.
- `/foundry:intake` — the maintainer's front door; an ER is a pluggable intake source.
- generalized from an internal maintainer process note (not shipped in this repo)
  across the adopter trust boundary.
