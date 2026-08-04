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
the workspace, and stops. It never runs `claude`, never accepts the workspace trust dialog, and
never pre-grants anything — it *declares*, the platform's trust dialog is the consent ceremony.

## What it does

- Emits the plugin's reviewed three-tier permission map verbatim into the new workspace's
  committed `.claude/settings.json`, alongside `extraKnownMarketplaces` and `enabledPlugins`
  pinned to an exact marketplace ref (`autoUpdate: false` — no floating grant).
- Absorbs `foundry-bootstrap.sh`'s out-of-session `git` commit-identity isolation (`--gh-account`),
  proved differentially equal to the shipped script.
- Scaffolds a seven-file, schema-valid workspace seed.
- Re-running is a **reconcile with a drift report** — an edited managed file is reported
  `drifted` and left byte-identical, never overwritten. Never-clobber is unconditional.

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
