#!/usr/bin/env bash
# FG-COST-003: post a cost diff as a PR comment. This step reports; it MUST NOT be a required check.
# Pin the infracost CLI version in docs/standing-versions.md; do not float it.
set -euo pipefail
infracost breakdown --path . --format json --out-file /tmp/infracost-base.json
infracost diff --path . --compare-to /tmp/infracost-base.json --format json --out-file /tmp/infracost.json
infracost comment github --path /tmp/infracost.json \
  --repo "$GITHUB_REPOSITORY" --pull-request "$PR_NUMBER" \
  --github-token "$GITHUB_TOKEN" --behavior update
# Always exit 0 from the comment step: a cost diff informs the reviewer, it does not block the merge.
