#!/usr/bin/env bash
# foundry-env-reap — the SessionEnd reaper hook for environment-isolation-hygiene
# (feat-foundry-environment-isolation-hygiene, AC-ENVHY-3/4) AND the per-worker ephemeral-env
# port/namespace allocation reaper (feat-foundry-per-worker-ephemeral-env-and-conductor-mutex,
# AC-PWE-3).
#
# Fires on the native SessionEnd event (session teardown) — the trap/finally happy-path class —
# NOT the per-turn Stop event (which fires at the end of every response and would reap the
# session's own resources mid-work). Thin executor: it resolves the session id and delegates
# ALL decision + teardown to the pure engines:
#   - scripts/foundry-env-hygiene.py (containers/clusters/dev-server pids, own-scoped, reaps ONLY
#     this session's owner-labelled resources reconciled from live reality)
#   - scripts/foundry_env_isolation.py's reap_stale_allocations() (per-worker ephemeral
#     port/namespace allocations — reclaims ONLY an allocation whose holder pid is no longer
#     live, per its recorded process-identity signature; a LIVE concurrent worker's allocation is
#     NEVER touched — see that module's decide_stale_allocations()).
# Neither ever brings down the shared container-runtime daemon/host or a tunnel. Best-effort +
# never blocks (always exit 0).
set -uo pipefail

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
ENGINE="${PLUGIN_ROOT}/scripts/foundry-env-hygiene.py"
PER_WORKER_ENV_CHECK="${PLUGIN_ROOT}/scripts/foundry_env_isolation.py"

if [ "${1:-}" = "--selftest" ]; then
  # Contract: never blocks (exit 0); delegates to the engine reap over the current (empty) env.
  out="$(CLAUDE_SESSION_ID="selftest-noop" bash "$0" </dev/null 2>&1)"; rc=$?
  if [ "$rc" -eq 0 ]; then echo "AC-ENVHY-3 reaper never-blocks: PASS"; echo "FOUNDRY-ENV-REAP-SELFTEST-GREEN"; exit 0
  else echo "AC-ENVHY-3 reaper never-blocks: FAIL (rc=$rc)"; echo "FOUNDRY-ENV-REAP-SELFTEST-RED"; exit 1; fi
fi

# Resolve the session id from the SessionEnd payload (STDIN JSON) or the environment.
payload="$(cat 2>/dev/null || true)"
session="$(printf '%s' "$payload" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("session_id","") or "")
except Exception: print("")' 2>/dev/null || true)"
if [ -z "$session" ]; then session="${CLAUDE_SESSION_ID:-${FOUNDRY_SESSION_ID:-}}"; fi

# Delegate to the pure env-hygiene engine (containers/clusters/dev-server pids). No session id ->
# the engine fail-safes (reaps nothing).
if [ -f "$ENGINE" ]; then
  if [ -n "$session" ]; then
    python3 "$ENGINE" reap --session "$session" 2>/dev/null || true
  else
    python3 "$ENGINE" reap 2>/dev/null || true
  fi
fi

# Delegate to the per-worker ephemeral-env allocation reaper (AC-PWE-3): reclaims ONLY
# allocations owned by a no-longer-live worker (PID-liveness + process-identity-signature
# discriminator) — never a live concurrent worker's allocation. Session-id-independent (the
# discriminator is per-allocation-record, not per-session), so it always runs best-effort.
#
# Security-review FIX-5b: the check-module PATH is passed as a real argv token (never spliced
# into the inline python source as a quoted string) — a path containing a quote/space/backslash
# would otherwise break (or, worse, be misinterpreted by) the string-spliced form.
if [ -f "$PER_WORKER_ENV_CHECK" ]; then
  python3 -c '
import sys
import importlib.util
try:
    check_path = sys.argv[1]
    spec = importlib.util.spec_from_file_location("pwe_reap", check_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.reap_stale_allocations()
except Exception:
    pass
' "$PER_WORKER_ENV_CHECK" 2>/dev/null || true
fi

exit 0
