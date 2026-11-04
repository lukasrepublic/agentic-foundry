#!/usr/bin/env bash
# FG-COST-003: post a cost diff as a PR comment. This step reports; it MUST NOT be a required check.
#
# SECURITY: run under the `pull_request` event (or `workflow_run` without a PR-head checkout). NEVER run it
# under `pull_request_target` with the PR head checked out: the PR controls the HCL and the module fetches,
# and that context holds secrets.
#
# Pin the infracost CLI per the stack kit's version-matrix template
# (governance/kits/stack/templates/version-matrix.md). Pinned-install form (placeholder):
#   curl -fsSL https://github.com/infracost/infracost/releases/download/v<version>/infracost-linux-amd64.tar.gz | tar xz
# Fork PRs get no comment under `pull_request` (the token is read-only there); the diff step still logs.
# Requires: BASE_REF (base branch name), PR_NUMBER, GITHUB_REPOSITORY, GITHUB_TOKEN.
set -euo pipefail
tmp="$(mktemp -d)"
trap 'git worktree remove --force "$tmp/base" >/dev/null 2>&1 || true; rm -rf "$tmp"' EXIT
git fetch --no-tags origin "${BASE_REF:?base branch name}"
git worktree add --detach "$tmp/base" "origin/${BASE_REF}"
infracost breakdown --path "$tmp/base" --project-name cost-diff --format json --out-file "$tmp/base.json" || true
infracost diff --path . --project-name cost-diff --compare-to "$tmp/base.json" --format json --out-file "$tmp/diff.json" || true
infracost comment github --path "$tmp/diff.json" \
  --repo "$GITHUB_REPOSITORY" --pull-request "$PR_NUMBER" \
  --github-token "$GITHUB_TOKEN" --behavior update || true
# Every step above is best-effort: a cost diff informs the reviewer and never blocks the merge.
