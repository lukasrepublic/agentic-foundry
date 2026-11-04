# OpenSSF Scorecard mapping

| Rule | Scorecard check | What it covers |
|---|---|---|
| FG-STK-002, FG-STK-003 | [Pinned-Dependencies](https://github.com/ossf/scorecard/blob/main/docs/checks.md#pinned-dependencies) | actions pinned to a SHA, images pinned by digest, no unpinned installs |
| FG-STK-005 | [Dependency-Update-Tool](https://github.com/ossf/scorecard/blob/main/docs/checks.md#dependency-update-tool) | Renovate or Dependabot is configured |

`checks/pinning.rego` enforces the same shape locally with `conftest test --policy checks --namespace standing.versions FILE`.
