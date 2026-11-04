# stack kit

Approved stack, pinned versions, SHA-pinned CI actions, a dependency-update preset, and a named rejected
alternative for every new dependency. Rules: `FG-STK-001` to `FG-STK-006` in `rules.yaml`.

- `checks/pinning.rego`: conftest policy (`package standing.versions`) for digest or exact-version images,
  exact Helm `targetRevision`, and SHA-pinned `uses:`.
- `checks/scorecard-mapping.md`: rules mapped to Scorecard checks.
- `templates/`: Renovate config (preset pinned by tag), a version matrix with a research timestamp, a pinned
  workflow. Every version-bearing template carries a `researched` date; re-run the research on a schedule.

References: [OpenSSF Scorecard](https://github.com/ossf/scorecard/blob/main/docs/checks.md),
[Renovate presets](https://docs.renovatebot.com/config-presets/),
[conftest](https://www.conftest.dev/).
