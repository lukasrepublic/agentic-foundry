# create-agentic-workspace

The pre-session bootstrap wizard for an [Agentic Foundry](https://github.com/lukasrepublic/agentic-foundry)
workspace. `/foundry:init` can never scaffold its own permission floor — a model editing its own
confinement is a shape the platform's own classifier denies — so the floor is written **before a
session exists**, in the operator's own terminal.

```bash
npx create-agentic-workspace --dir my-workspace
```

The CLI walks you through the target directory, greenfield-vs-existing, git/GitHub identity, and
stage mode, **previews every file it will write and every capability it will declare**, writes
the workspace, and stops. `create-agentic-workspace` never runs `claude`, never accepts the
workspace trust dialog, and never pre-grants anything — it *declares*, the platform's trust dialog
is the consent ceremony.

**Already have a workspace and want it current instead?** That is a different command, on purpose
— see the sibling package [`update-agentic-workspace`](https://github.com/lukasrepublic/agentic-foundry/tree/main/cli-update#readme),
which refreshes the marketplace, updates the plugin, and re-runs this reconcile. Unlike this one,
it does invoke `claude` (bounded by a closed allowlist — see its own README).

## What it does

- Emits the plugin's reviewed three-tier permission map verbatim into the new workspace's
  committed `.claude/settings.json`, alongside `extraKnownMarketplaces` and `enabledPlugins`.
  As of `feat-foundry-installer-unpinning`, the marketplace registration names no ref at all
  (`autoUpdate: false` still stops it from floating on its own — no floating grant); the ARTIFACT
  fetched is what stays pinned, via the untouched `plugins[].source.sha` in the marketplace
  manifest, which `sha` outranks `ref` at install time regardless of which index ref is named.
- Absorbs `foundry-bootstrap.sh`'s out-of-session `git` commit-identity isolation (`--gh-account`),
  proved differentially equal to the shipped script.
- Scaffolds a seven-file, schema-valid workspace seed.
- Re-running is a **reconcile with a drift report** — an edited managed file is reported
  `drifted` and left byte-identical, never overwritten. Never-clobber is unconditional.
- The permission-floor reconcile is additive with ONE narrow exception: a row shaped exactly like
  the floor's own root-glob rows (`Bash(<plugin-root-glob>/scripts/<name>[ <sub>]:*)`), whose
  `(name, sub)` PAIR — not the script name alone — the shipped floor no longer declares, is retired
  from `allow`/`ask` and printed `[retired] <row>`. The pair, not just the name, matters: retiring
  one subcommand of a script that still ships other subcommands (say `<name> --a:*` is dropped
  while `<name> --b:*` remains) removes only the stale `(name, sub)` row, never the whole family —
  and a script deleted outright does not leave a standing grant behind for a future script with the
  same name to inherit unreviewed. Any row of another shape (an adopter-authored rule naming a
  plugin script through a different prefix, or a `deny` row) is never touched. The summary line
  reports all three outcomes together:
  `permission-floor reconcile: … — N added, M retired, K unchanged`.
- `.gitignore` gets its own narrower reconcile on top of that: the file also carries a
  `FOUNDRY-RUNTIME-GITIGNORE-BEGIN`/`-END` managed block, converged independently of the
  whole-file compare above (so an adopter's own surrounding lines never block it from catching
  up) — reported `[converged]`, `[unchanged]`, or, for a malformed sentinel state or a symlinked
  `.gitignore`, `[refused]` and left untouched.
- The Amendments backfill: every `specs/**/feat-*.md` with a normative region and no
  `## Amendments` section after it gets the empty section `/foundry:amend` requires, reported as
  one row (`[amendments] backfilled N of M specs (K already present, J skipped: no normative
  region)`). Outside the hashed region, so no `spec_sha256` moves; already-present, marker-less and
  symlinked specs are never written; `--dry-run` prints the same row.

## Flags

Run `npx create-agentic-workspace --help` for the full, single-sourced flag list (every flag has
an interactive-prompt twin, and `--yes` never prompts).

## No telemetry

This CLI collects and transmits nothing — **no telemetry** of any kind, and no opt-out to offer
because there is nothing to opt out of. No credential is read, derived, or written; the one
optional `gh api user` identity probe reads whatever authentication your own `gh` already holds
without ever persisting, logging, or printing it beyond the name/email you confirm.

## Supply-chain posture

Zero third-party dependencies, `scripts` closed to `{test}` (no lifecycle hook of any kind), and
every `import` a `node:` built-in or a relative path. See the plugin's own
[Security posture](https://github.com/lukasrepublic/agentic-foundry/blob/main/specs/features/foundry/onboarding/bootstrap-cli/feat-foundry-bootstrap-cli.md)
for the honest limits of what a build-time check over the source tree can and cannot attest about
a published tarball.
