# claude-plugin readiness checklist

Gate before a change built under this blueprint is declared production-ready. The `governance` block of `stack-profile.yaml` selects the kits and levels that enforce it.

- [ ] `.claude-plugin/plugin.json` valid; version consistent with the marketplace entry and changelog.
- [ ] Hooks are fail-closed, fast, and never widen permissions; each has a test.
- [ ] Every skill has frontmatter, a trigger description and a bounded scope.
- [ ] Python scripts are stdlib-first with pinned dev tooling; node CLI parts are locked.
- [ ] The plugin's own doctor reports GREEN; no private names or secrets ship.
- [ ] Release is tagged immutably after operator sign-off.
