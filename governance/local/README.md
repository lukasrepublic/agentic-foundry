# governance/local

The adopter layer. The plugin never overwrites anything in this directory.

- `rules.yaml` - your own rules (ids use your own prefix, never `FG-`) and `overrides:` of non-floor kit rules.
- `exceptions.yaml` - time-boxed exceptions: `rule`, `scope`, `reason`, `owner`, `expires` (YYYY-MM-DD).
- `baselines/` - existing violations frozen per check (`foundry_governance.py baseline`).
- `adr/` - architecture decision records.
- `.kit-base/` - machine-owned snapshots of the kit files last installed; the base of the three-way merge on upgrade.

This directory is private to your workspace and is refused by the upstream leak guard.
