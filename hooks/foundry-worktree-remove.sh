#!/usr/bin/env bash
# foundry-worktree-remove — the WorktreeRemove hook for multi-repo dispatch (UL-0022).
# Generalized port of the source handbook's .claude/hooks/git-worktree-remove.sh.
#
# Fires when Claude Code unwires a worker's `isolation: worktree` session. INFORMATIONAL +
# best-effort only: it NEVER removes the worktree or branch (post-merge teardown is the
# dispatcher's job via foundry-work-isolation.sh, AC-6) and NEVER blocks (always exit 0).
# It logs the unwire and best-effort reaps any process whose cwd is inside the worktree
# (e.g. a `make dev` server) so a torn-down worktree dir is releasable.
set -uo pipefail

_ws_root() {
  if [ -n "${CLAUDE_PROJECT_DIR:-}" ]; then (cd "$CLAUDE_PROJECT_DIR" 2>/dev/null && pwd -P) && return 0; fi
  local cd; cd="$(git rev-parse --git-common-dir 2>/dev/null || true)"
  if [ -n "$cd" ]; then case "$cd" in /*) : ;; *) cd="$(pwd)/$cd" ;; esac; (cd "$(dirname "$cd")" 2>/dev/null && pwd -P) && return 0; fi
  git rev-parse --show-toplevel 2>/dev/null || pwd -P
}

if [ "${1:-}" = "--selftest" ]; then
  # Contract: never blocks (exit 0), logs to the dispatch log, leaves the worktree on disk.
  tmp="$(mktemp -d)"; ws="$tmp/ws"; mkdir -p "$ws/.foundry"
  out="$(CLAUDE_PROJECT_DIR="$ws" bash "$0" <<<'{"name":"agent-deadbeef","cwd":"'"$ws"'/.worktrees/app/x/y"}' 2>&1)"; rc=$?
  rmdir "$tmp/ws/.foundry" 2>/dev/null; rm -rf "$tmp"
  if [ "$rc" -eq 0 ]; then echo "AC-MRDISPATCH-WTRM never-blocks: PASS"; echo "FOUNDRY-WORKTREE-REMOVE-SELFTEST-GREEN"; exit 0
  else echo "AC-MRDISPATCH-WTRM never-blocks: FAIL (rc=$rc)"; echo "FOUNDRY-WORKTREE-REMOVE-SELFTEST-RED"; exit 1; fi
fi

WS="$(_ws_root)"
LOG="$WS/.foundry/dispatch.log"
payload="$(cat 2>/dev/null || true)"
wt="$(printf '%s' "$payload" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("cwd","") or "")
except Exception: print("")' 2>/dev/null || true)"
mkdir -p "$WS/.foundry" 2>/dev/null || true
printf '%s worktree-remove: unwire cwd=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || true)" "$wt" >> "$LOG" 2>/dev/null || true
# best-effort dev-server reap (never change exit status)
if [ -n "$wt" ] && command -v lsof >/dev/null 2>&1; then
  lsof -a -d cwd +D "$wt" 2>/dev/null | awk 'NR>1{print $2}' | xargs -r kill -9 2>/dev/null || true
fi
exit 0
