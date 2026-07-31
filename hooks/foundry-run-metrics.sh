#!/usr/bin/env bash
# foundry-run-metrics — the run-duration capture PERSISTENCE SEAM (feat-foundry-run-duration-capture,
# AC-RDC-1..12). `workflows/release-wave.js` is a pure-compute sandbox that cannot write a file (no
# fs/import()/node:/require() — see the spec's Prior art + Design/notes sections); the ONLY proven way
# a wave's per-atom data reaches disk is the `PostToolUse(Agent|Workflow)` hook, exactly the seam
# `hooks/foundry-harvest-learnings.sh` already uses for worker learnings[]. This entry is wired ONE
# additive step, LISTED AFTER that one, in the SAME matcher's hooks[] array (hooks/hooks.json) — the
# learnings capture is a delivered floor. "Listed after" is NOT an execution-order guarantee: Claude
# Code may run same-matcher hooks concurrently, so this only means array position, never "runs first/
# after". The two hooks are resource-disjoint (learnings writes under .foundry/session-learnings/,
# this one under .foundry/run-metrics*), so concurrent execution is safe either way.
#
# ALWAYS exits 0 (AC-RDC-3a): this hook is a NEVER-BLOCK OBSERVER. A metrics module that turns a green
# run red is a worse defect than a missing row — stdout/stderr are discarded (never surfaced to the
# harness or the model; a diagnostic would repr a payload-controlled spec_ref), and nothing this script
# does can change the measured run's own outcome (AC-RDC-3b). Every refusal is instead persisted,
# durably and non-model-visibly, to `.foundry/run-metrics.loss.jsonl` by the Python write boundary
# itself (`scripts/foundry_run_metrics.py`'s `_log_loss()`) — see that module's docstring. Mirrors
# foundry-harvest-learnings.sh's own documented ALWAYS-exit-0 contract.
#
# Modes:
#   foundry-run-metrics.sh posttooluse    # reads hook stdin JSON (the PostToolUse payload); ALWAYS rc=0
#   foundry-run-metrics.sh --selftest
set -uo pipefail   # deliberately NOT -e: never abort, never fail the caller

_HERE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo .)"
_SCRIPTS_DIR="$(cd "$_HERE_DIR/../scripts" 2>/dev/null && pwd || echo "$_HERE_DIR/../scripts")"

# ENFORCED capture (PostToolUse on Agent|Workflow): read the tool's structured RETURN from the hook
# payload and hand it to the Python write boundary. ALWAYS exit 0 regardless of what happens inside —
# a missing python3, a crashed interpreter, a malformed payload, an unwritable ledger, an unreadable
# acceptance-contract.yaml are all non-fatal to the caller (AC-RDC-3a/-3b).
_posttooluse() {
  local payload
  payload="$(cat 2>/dev/null || true)"
  printf '%s' "$payload" | python3 "$_SCRIPTS_DIR/foundry_run_metrics.py" --posttooluse >/dev/null 2>&1 || true
  exit 0
}

# ----------------------------------- self-test ----------------------------------- #
_selftest() {
  local tmp fails=0
  tmp="$(mktemp -d)"
  _emit() { if [ "$2" -eq 0 ]; then echo "$1: PASS${3:+ — $3}"; else echo "$1: FAIL${3:+ — $3}"; fails=$((fails+1)); fi; }
  echo "foundry-run-metrics self-test:"

  # AC-RDC-3a: exit 0 for empty, malformed, and well-formed-but-unresolvable stdin.
  local rc_empty rc_malformed rc_wellformed
  printf '' | bash "$0" posttooluse >/dev/null 2>&1; rc_empty=$?
  printf 'not json {{{' | bash "$0" posttooluse >/dev/null 2>&1; rc_malformed=$?
  printf '%s' '{"tool_name":"Workflow","tool_response":[{"atom":"nonexistent/spec.md","impl":{"status":"ready"}}]}' | bash "$0" posttooluse >/dev/null 2>&1; rc_wellformed=$?
  if [ "$rc_empty" -eq 0 ] && [ "$rc_malformed" -eq 0 ] && [ "$rc_wellformed" -eq 0 ]; then
    _emit "AC-RDC-3a exit-0-for-every-payload-shape" 0 "empty/malformed/well-formed-unresolvable all rc=0"
  else
    _emit "AC-RDC-3a exit-0-for-every-payload-shape" 1 "empty=$rc_empty malformed=$rc_malformed wellformed=$rc_wellformed"
  fi

  rm -rf "$tmp" 2>/dev/null || true
  echo ""
  if [ "$fails" -eq 0 ]; then echo "FOUNDRY-RUN-METRICS-SELFTEST-GREEN"; return 0; else echo "FOUNDRY-RUN-METRICS-SELFTEST-RED"; return 1; fi
}

case "${1:-}" in
  posttooluse) _posttooluse ;;
  --selftest)  _selftest; exit $? ;;
  *) echo "usage: $0 posttooluse | --selftest" >&2; exit 2 ;;
esac
