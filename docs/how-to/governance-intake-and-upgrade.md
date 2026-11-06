# Governance intake and upgrade

How a workspace gets its governance kits, and how it keeps them current without losing its own edits.
Intake and upgrade are the same step: `/foundry:init` and `/foundry:intake existing` run it the first time, and
`npx update-agentic-workspace` runs it again after every plugin refresh.

The script is `scripts/foundry_governance.py`. Exit codes everywhere: zero is ok, one is findings, two is refused, and three is
"decision needed" (nothing was written).

## The five steps

1. **discover** - ordered file-signal detectors over the tree (manifests, lockfiles, CI, infrastructure as code,
   containers, dependency names, authentication libraries). Each detector reports `found`, `not-found` or
   `could-not-look`. Nothing is guessed: a directory it cannot read is `could-not-look`, never "absent".

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_governance.py" discover --summary
   ```

2. **select** - the facts are scored against every blueprint's declared `detect` signatures and printed ranked,
   with the evidence for each. This is a signature table, not an inference. It always stops for you: without
   `--confirm` it exits with the decision-needed code and writes nothing. An empty tree ranks no blueprint above its threshold and says so.

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_governance.py" select
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_governance.py" select --confirm <blueprint-id>
   ```

   An LLM proposal may be passed as `--suggest <id>`; it is printed as a labelled suggestion and is never a
   selection.

3. **declare** - the declarations the kits cannot know: the ASVS level, the data classification, the trust
   boundaries (path globs of code that holds credentials or crosses to other systems) and the surfaces
   (`ui`, `api`, `cli`, `workflows`, `mcp`, `goal-agents`). Omitted surfaces default to the blueprint's own.

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_governance.py" declare --asvs-level <level> \
       --data-classification internal --trust-boundary 'src/auth/**'
   ```

4. **apply** - writes `.foundry/governance.lock`, scaffolds `governance/local/` (`README.md`,
   `exceptions.yaml`, `baselines/`, `adr/`), installs the selected kits' CI and hook snippets, renders
   `governance/rendered/`, and with `--pr` opens one pull request (branch, commit, push, `gh pr create`).
   Brownfield onboarding is by pull request, never by regeneration. A file you already have is kept, not
   overwritten, and running `apply` twice changes nothing. A dirty tracked tree is refused when `--pr` is used.

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_governance.py" apply --pr
   ```

   **Derive the architecture** (between select and baseline, for the architecture kit). The kit installs checks, but
   a brownfield tree has no layers declared yet. This step proposes them as DRAFTS from the tree itself. It never
   writes a baseline. See [derive-architecture.md](derive-architecture.md).

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_architecture_derive.py" --repo . --dry-run
   ```

5. **baseline** - after you confirm the drafts, freeze the violations that already exist, per check, so only new ones fail:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_governance.py" baseline --check <name> --findings <file>
   ```

## What lives where

| Path | Owner | Updater behaviour |
|---|---|---|
| `.foundry/governance.lock` | the script (machine-owned) | never overwritten by the file reconcile; bumped only by `upgrade` |
| `governance/local/**` | you | never overwritten; refused by the upstream leak guard |
| `governance/local/.kit-base/` | the script | snapshots of the kit files last installed, the base of the three-way merge |
| `governance/rendered/` | generated | regenerated; `check` reports it stale if hand-edited |
| `.github/workflows/*`, `.claude/hooks/*` installed from a kit | you, once installed | three-way merged on upgrade |

The old `.foundry/stack-profile.lock` is absorbed once: when `apply` or `upgrade` finds it and there is no
governance lock yet, its blueprint is carried into the governance lock. The file itself stays for
`/foundry:verify` and the doctor until they read the governance lock.

## Upgrade

`foundry_governance.py upgrade [--pr]` is what the updater runs after the plugin refresh. It:

- refuses a dirty tracked tree (nothing is changed);
- applies kit `migrations` (renamed rule ids) to `governance/local/`;
- regenerates the new kit snippets and merges them three-way against the snapshot of the old ones and your
  working file: your edit survives, or the file gets inline conflict markers (`<<<<<<< adopter`), and the run
  exits with the findings code. It never overwrites an edit, and a snippet you deleted stays deleted;
- bumps the lock and the rendered documents, and opens one pull request when `--pr` is given.

Review that pull request with `/foundry:post-upgrade`. `foundry_governance.py check` reports the same drift
without changing anything: a kit or blueprint behind the plugin, or a lock from another plugin version.

## Level and surface filters

`render` and `applicable` accept `--level N` and `--surfaces ui,api`, or `--from-lock` to use the lock's
per-kit levels and declared surfaces. A rule whose `applies_to.blueprints` names only surfaces (for example
`ui`) is left out when that surface is not declared. Floor rules are never filtered out.
`applicable` also adds the lock's declared trust boundaries to the paths of the security-review rule.

## Contributing upstream

`/foundry:upstream-submit` runs the leak guard (`leak-check`, and
`foundry-prepublication-leak-scan.py --governance-submission`) and refuses any file under `governance/local/`,
any rule id that is not `FG-`, any path the lock marks adopter-owned, and any product literal. It refuses; it
does not redact.

## A fresh workspace

With no lock and no `.foundry/operating-model.yaml`, every floor rule is already in force and every tunable has
its default. Intake adds the blueprint's kits and your declarations; it never switches a floor on, because
floors are never off.
