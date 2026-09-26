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
  # Contract: never blocks (exit 0), logs to the dispatch log, leaves the worktree on disk, and NEVER
  # touches a process outside the removed worktree (v1.18.2: the payload's session cwd is the project
  # root; reaping by it killed the Claude Code session itself).
  tmp="$(mktemp -d)"; ws="$tmp/ws"; mkdir -p "$ws/.foundry" "$ws/.claude/worktrees/x"
  ( cd "$ws" && exec sleep 30 ) & keep=$!
  ( cd "$ws/.claude/worktrees/x" && exec sleep 30 ) & reap=$!
  sleep 0.3
  out="$(CLAUDE_PROJECT_DIR="$ws" bash "$0" <<<'{"hook_event_name":"WorktreeRemove","cwd":"'"$ws"'","worktree_path":"'"$ws"'/.claude/worktrees/x"}' 2>&1)"; rc=$?
  sleep 0.3
  ok=1; kill -0 "$keep" 2>/dev/null || ok=0
  out2="$(CLAUDE_PROJECT_DIR="$ws" bash "$0" <<<'{"cwd":"'"$ws"'","worktree_path":"'"$ws"'"}' 2>&1)"; rc2=$?
  sleep 0.3; kill -0 "$keep" 2>/dev/null || ok=0
  kill "$keep" "$reap" 2>/dev/null; wait 2>/dev/null
  rm -rf "$tmp"
  if [ "$rc" -eq 0 ] && [ "$rc2" -eq 0 ] && [ "$ok" -eq 1 ]; then echo "AC-MRDISPATCH-WTRM never-blocks + never-reaps-outside-the-worktree: PASS"; echo "FOUNDRY-WORKTREE-REMOVE-SELFTEST-GREEN"; exit 0
  else echo "AC-MRDISPATCH-WTRM: FAIL (rc=$rc rc2=$rc2 root-process-survived=$ok)"; echo "FOUNDRY-WORKTREE-REMOVE-SELFTEST-RED"; exit 1; fi
fi

WS="$(_ws_root)"
LOG="$WS/.foundry/dispatch.log"
payload="$(cat 2>/dev/null || true)"
# v1.18.2: the worktree being removed is `worktree_path`. The payload's `cwd` is the SESSION's cwd —
# usually the project root — and reaping by it `kill -9`'d every process under the project, the
# Claude Code session included (found by the 2026-09-26 machinery audit).
wt="$(printf '%s' "$payload" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("worktree_path","") or "")
except Exception: print("")' 2>/dev/null || true)"
mkdir -p "$WS/.foundry" 2>/dev/null || true
printf '%s worktree-remove: unwire worktree_path=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || true)" "$wt" >> "$LOG" 2>/dev/null || true
# best-effort dev-server reap (never change exit status), ONLY strictly inside a worktree directory
# of this project, never the project root or anything above it, never this hook's own ancestry
[ -n "$wt" ] && [ -d "$wt" ] || exit 0
wt_real="$(cd "$wt" 2>/dev/null && pwd -P)" || exit 0
case "$wt_real/" in
  "$WS/.worktrees/"?*|"$WS/.claude/worktrees/"?*) : ;;
  *) exit 0 ;;
esac
[ "$wt_real" = "$WS" ] && exit 0
command -v lsof >/dev/null 2>&1 || exit 0
ancestors=" $$ "; p=$$
while [ -n "$p" ] && [ "$p" != 1 ] && [ "$p" != 0 ]; do
  p="$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')"; [ -n "$p" ] && ancestors="$ancestors$p "
done
lsof -a -d cwd +D "$wt_real" 2>/dev/null | awk 'NR>1{print $2}' | sort -u | while read -r pid; do
  case "$ancestors" in *" $pid "*) continue ;; esac
  kill -TERM "$pid" 2>/dev/null || true
done
exit 0
