#!/usr/bin/env bash
# fixture.sh — runs OUTSIDE the agent's sandbox, as you, only with `--scaffold` (case.yaml's
# context.scaffold_script). Places one release's state.yaml at the exact repo-relative path a
# resumed session looks for, with a next_action that names the one thing to do next.
set -euo pipefail

mkdir -p .foundry/releases/demo-widget-release

cat > .foundry/releases/demo-widget-release/state.yaml <<'STATE'
next_action: "run tests/test_rate_limit.py for AC-RL-1, then open the PR"
decisions:
  - "2026-09-19: scope frozen at authorize time; nothing else is in flight"
STATE
