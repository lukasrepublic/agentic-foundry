# update-agentic-workspace

One command to bring an already-installed [Agentic Foundry](https://github.com/lukasrepublic/agentic-foundry)
workspace current: refresh the plugin marketplace, migrate a pre-v1.7.0 tag-pinned registration if
one is found, update the plugin in every scope that enables it, and re-run the workspace and
permission-floor reconcile.

```bash
npx update-agentic-workspace
```

Run it **from inside** the workspace directory you scaffolded with `npx create-agentic-workspace`
(the sibling entry point). This package is a thin wrapper: every shared module is resolved from
`create-agentic-workspace` at an exact, pinned version — nothing here is a second copy.

## Unlike the sibling entry point, this one runs `claude`

`create-agentic-workspace` never runs `claude`, never accepts the workspace trust dialog, and never
pre-grants anything — it only declares. The update entry point necessarily does invoke `claude`, to
refresh the marketplace and update the plugin. That posture change is bounded, not open-ended:
every invocation this command makes is drawn from **one frozen, closed allowlist** of six
non-interactive `plugin` subcommand pairs (`plugin marketplace update`, `plugin marketplace add`,
`plugin marketplace remove`, `plugin update`, `plugin install`, `plugin list`) — none of which can
start a session or reach the workspace trust dialog. This command still never accepts the trust
dialog itself and never grants anything beyond what that dialog decides — put plainly, it never
grants a capability of its own: it declares and refreshes, and the platform's own trust dialog
remains the only consent ceremony there is.

**One side effect worth stating plainly, because "grants nothing of its own" could be read to rule
it out.** The migration heals a scope whose registration still carries a pinned `ref`, and healing
it ends — by design — in `claude plugin install <plugin>@<marketplace> --scope <scope>`, so that
removing the registration cannot leave the plugin orphaned. If that scope had the plugin *disabled*,
this re-enables it there. That is the specified behaviour, not an accident: the pinned-`ref` trigger
is deliberately not conditioned on enablement, because a stale pin is a broken registration whether
or not the plugin is currently switched on. If you have deliberately disabled the plugin in a scope
and want it to stay that way, disable it again after the update, or migrate that scope by hand.

## What it does

1. **Marketplace refresh** — `claude plugin marketplace update <marketplace>`, first migrating a
   registration that is not yet in the tagless steady state (a leftover from before v1.7.0): removed,
   re-added tagless, and the plugin re-installed, once per affected scope, with that scope's other
   settings preserved exactly.
2. **Plugin update** — `claude plugin update <plugin>@<marketplace>` once for every scope whose
   settings enable it, verified by reading back the refreshed cache manifest rather than trusting
   the invoked CLI's own success line.
3. **Cleanup, opt-in (`--cleanup`)** — prune superseded plugin-cache versions and remove a stale or
   duplicate marketplace registration. **This is destructive** and off by default: without
   `--cleanup`, every candidate path and registration is still previewed, but nothing is removed and
   no `claude` invocation runs for this phase at all. Pass `--cleanup` to actually delete what was
   previewed.
4. **Reinitialization** — the same never-clobber managed-file reconcile and additive permission-floor
   reconcile `create-agentic-workspace --existing` already implements: an operator-edited file is
   reported drifted and left byte-identical, never overwritten.

Every run previews every `claude` invocation and every path it will touch **before** the first one
happens, and ends with a per-phase summary (`changed` / `already current` / `skipped: <reason>`).

## Flags

- `--cleanup` — also perform the destructive cache-prune and stale-registration removal previewed
  above. Off by default; a flagless run removes nothing and prunes nothing.
- `--help` — print usage and exit.

## Exit codes

Same convention as the sibling package, and worth reading before you wire this into anything:

| code | meaning |
|------|---------|
| `0`  | the run completed and no managed workspace file was found drifted |
| `2`  | the run completed and at least one managed workspace file was drifted |
| `1`  | the run refused, or an invocation failed |

**`2` is a success, not an error.** It reports one specific thing: a managed file in your workspace
has diverged from what the current template would write. That is a normal finding on a workspace
that predates a template change, and it is the expected result of a first run against a pre-v1.7.0
workspace.

Read the codes precisely, because `2` is narrower than "something happened": it is computed *only*
from the managed-file drift check, so a run that migrates a tag-pinned registration and updates the
plugin in every scope — real, visible changes — still exits `0` if no managed file drifted. Use the
printed phase summary, not the exit code, to see what the run actually did.

Only `1` means something went wrong. A `set -e` script or a CI step that treats any non-zero as
failure will read a perfectly good update as broken; test for `1` specifically.

## No telemetry

Same posture as the sibling package: no telemetry, no credential read beyond what your own `claude`
session already holds, nothing transmitted anywhere this tool does not tell you about.
