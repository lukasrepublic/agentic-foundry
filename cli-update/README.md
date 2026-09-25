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
4. **Reinitialization** — the same never-clobber managed-file reconcile and permission-floor
   reconcile `create-agentic-workspace --existing` already implements: an operator-edited file is
   reported drifted and left byte-identical, never overwritten. The permission-floor reconcile is
   additive with one narrow exception — a row shaped exactly like the floor's own root-glob rows,
   whose `(name, sub)` PAIR — not the script name alone — the shipped floor no longer declares, is
   retired from `allow`/`ask` (printed `[retired] <row>`): retiring one dropped subcommand of a
   script that still ships others removes only that stale pair, never the whole family, and a
   script deleted outright does not leave a standing grant behind forever. `.gitignore`'s own
   managed-block reconcile runs here too, so a workspace that predates a
   later block content change catches up on the next update run rather than staying stuck on
   whatever it was scaffolded with. The **Amendments backfill** runs here as well: every
   `*.md` under `specs/` (any basename — `feat-*`, `spec-*`, …) that has a normative region but no `## Amendments` section after it gets
   the empty section `/foundry:amend` requires (one row: `[amendments] backfilled N of M specs …`).
   The section sits outside the hashed normative region, so no `spec_sha256` and no authorization
   moves; a spec that already has the section, has no normative region, or is a symlink is never
   written. `.foundry/permissions.yaml` is **seeded** here when absent — an empty, commented
   starter for standing grants (`docs/how-to/standing-grants.md`) — and reported `[kept]` on
   every later run: operator-owned, never compared, never overwritten. The policy file's two
   self-guard deny rules are converged here (`[permissions] self-guard deny rules added (2)` /
   `already present`). Then the **retired-artifacts sweep**: every file an earlier release wrote and
   no current release reads (the shipped catalogue `retired-artifacts.json` — `wiring-hash.pin`, the
   old dispatcher files, a retired exec-guard hook, …) is reported `[stale] <path> — retired in vX
   (<why>); remove with --cleanup` on every run and removed only under `--cleanup`, only for
   catalogued paths of the catalogued kind (a link or the other kind is `[refused]`, left alone;
   so is a hook under `.claude/hooks/` that a hook command in `.claude/settings*.json` still
   names, and every hook when a settings file does not parse; so is **any catalogued path a
   workspace wiring file still names by its full relative path** (and any retired hook another hook
   script names by basename), printed `[refused] <path> — still referenced by <file>`, because a path
   the framework retired may be one you adopted for your own tooling. The scanned set is declared in
   the catalogue's `reference_scan`: CI dirs (`.github/`, `.gitlab/`, `.circleci/`, `.buildkite/`,
   `.gitea/`, `.forgejo/`), `scripts/`, `bin/`, `tools/`, `.githooks/`, `.husky/`, `.claude/hooks|
   commands|skills|agents/`, named config files (`.claude/settings*.json`, `.mcp.json`, `Makefile`,
   `package.json`, `pyproject.toml`, `Dockerfile`, `Jenkinsfile`, …) and the root's
   `*.md`/`*.sh`/`*.yml`. It is a substring heuristic, so a path assembled at run time is not seen;
   it fails closed — a symlink, an unreadable entry or an exceeded budget refuses every row; a
   directory row shows its entry count). `.claude/skills`, `.claude/agents` and files outside the catalogue are invisible to it.
   Finally `.claude/settings.local.json`, which the tracked-file reconcile never reads, has its
   version-pinned or gone floor rows **retired** (never anything added; a pinned `ask` row only
   when the tracked file carries the wildcard `ask` row that replaces it), written by the same
   rename-install that keeps the file's mode bits, previewed like every other write, and is
   otherwise untouched.
5. **The report and the hand-off** — every completed run writes `.foundry/upgrade-report.json`
   (`schema_version`, `ran_at`, `from_plugin_version` / `to_plugin_version`, the phase verdicts,
   what the Amendments backfill did, whether the policy file was `created` or `kept`, the drifted
   paths) and ends with one line: `next: run /foundry:post-upgrade in your next session`. That
   plugin skill owns the judgement half of an upgrade — standing grants into policy,
   `requires_capabilities` on unfrozen contracts, a truth pass over your own prose, branch garbage
   collection — and refuses without this report. `.foundry/` is gitignored; the report stays local.

Every run previews every `claude` invocation and every path it will touch **before** the first one
happens, and ends with a per-phase summary (`changed` / `already current` / `skipped: <reason>`).

## Flags

- `--cleanup` — also perform the destructive cache-prune, stale-registration removal and the
  retired-artifacts removal previewed above. Off by default; a flagless run removes nothing and
  prunes nothing.
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
