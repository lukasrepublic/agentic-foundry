#!/usr/bin/env bash
# foundry-statusline.sh — version-agnostic, fail-open self-resolving statusLine wrapper
# (feat-foundry-init-statusline-wrapper, AC-SLW-1; statusline-wiring v1.17.0, AC-SLW-3).
#
# Installed into an adopter repo at .claude/hooks/foundry-statusline.sh by `npx update-agentic-workspace`
# (and `create-agentic-workspace --existing` on a trusted workspace), which also sets
# statusLine.command to "$CLAUDE_PROJECT_DIR/.claude/hooks/foundry-statusline.sh" — the EXPANDABLE
# placeholder. (The plugin-root hook path-placeholder is HOOK-scoped and does NOT expand in a statusLine
# command, so this wrapper references it NOWHERE.) The line above carrying the feature id is the
# FRAMEWORK MARKER: the updater converges a wrapper that carries it and keeps one that does not.
#
# RESOLUTION ORDER (v1.17.0): the renderer the plugin ships is looked for
#   1. via installed_plugins.json under ${CLAUDE_CONFIG_DIR:-$HOME/.claude} — the installPath Claude Code
#      itself records for the plugin, so a non-default config root or cache layout still resolves;
#   2. via the plugin cache under the same root, newest by the <version> PATH-SEGMENT (the dir between
#      /foundry/ and /scripts/), never a whole-path `sort -V` — a whole-path sort ranks by the marketplace
#      name first, so with foundry installed under >1 marketplace an OLDER version under a lexically-
#      greater marketplace could win (the §8 fix this wrapper has always carried);
#   3. via the self-hosting source checkout, ${CLAUDE_PROJECT_DIR:-$PWD}/agentic-foundry/scripts/.
# When none resolves, the token bar is rendered HERE from the payload — `⌂ <dir>:<branch> · tok <bar> NN%`
# — so a missing renderer is visible as a plainer line, never as an absent one. `/foundry:doctor`'s
# `statusline:` advisory says which of the four pieces is missing.
#
# FAIL-OPEN is the only invariant: any error → print what could be built (possibly nothing) and `exit 0`.
set +e

# v1.18.2 — THROTTLE + SINGLE-FLIGHT. Claude Code re-runs the status line on every update; the
# renderer shells out to git (a full `git status`) and python, which on WSL took seconds. Runs then
# overlapped, each refresh spawning another, until the machine ran out of memory and Claude Code
# itself crashed (an adopter's WSL session: 44,616 spawns in 21 minutes, SIGBUS). So: one render per
# project at a time (an mkdir lock), its output cached for FOUNDRY_STATUSLINE_TTL seconds (default 5),
# and a refresh that finds a render in flight prints the last line and exits at once. The cache-hit
# path spawns only `cat` (and `date` where bash has no EPOCHSECONDS). Cache: ~/.cache/foundry-statusline.
# Security review (v1.18.2, Block): the cache lives in the user's OWN cache dir, never a shared /tmp,
# and is used only when it is a real directory owned by this user (not a symlink) — otherwise the
# wrapper renders directly, uncached (fail-open), so a planted dir/link is never read or written.
if [ -z "${FOUNDRY_STATUSLINE_INNER:-}" ]; then
  PAYLOAD="$(cat 2>/dev/null || true)"
  _d="${XDG_CACHE_HOME:-${HOME:-/nonexistent}/.cache}/foundry-statusline"
  mkdir -p -m 700 "$_d" 2>/dev/null
  if [ ! -d "$_d" ] || [ -L "$_d" ] || [ ! -O "$_d" ]; then
    printf '%s' "$PAYLOAD" | FOUNDRY_STATUSLINE_INNER=1 bash "$0" "$@" 2>/dev/null
    exit 0
  fi
  _k="${CLAUDE_PROJECT_DIR:-$PWD}"; _k="${_k//[^A-Za-z0-9]/_}"
  _cache="$_d/${_k}.out"; _lock="$_d/${_k}.lock"
  _now="${EPOCHSECONDS:-$(date +%s)}"; _ttl="${FOUNDRY_STATUSLINE_TTL:-5}"
  _ts=0; [ -r "$_cache.ts" ] && read -r _ts < "$_cache.ts" 2>/dev/null
  case "$_ts" in ''|*[!0-9]*) _ts=0 ;; esac
  if [ -r "$_cache" ] && [ "$_ts" -le "$_now" ] && [ $(( _now - _ts )) -lt "$_ttl" ]; then
    printf '%s' "$(< "$_cache")"; exit 0
  fi
  if ! mkdir "$_lock" 2>/dev/null; then
    # a lock whose ts is not written yet is FRESH (a render just started), never stale
    _lt="$_now"; [ -r "$_lock/ts" ] && read -r _lt < "$_lock/ts" 2>/dev/null
    case "$_lt" in ''|*[!0-9]*) _lt="$_now" ;; esac
    if [ $(( _now - _lt )) -gt 30 ]; then rm -f "$_lock/ts" 2>/dev/null; rmdir "$_lock" 2>/dev/null; fi
    [ -r "$_cache" ] && printf '%s' "$(< "$_cache")"
    exit 0
  fi
  printf '%s\n' "$_now" > "$_lock/ts" 2>/dev/null
  # bounded: a hung render (git on a stalled filesystem) is killed at 20 s where `timeout` exists,
  # so it can never outlive the 30 s stale-lock window and overlap the next one
  if command -v timeout >/dev/null 2>&1; then
    _out="$(printf '%s' "$PAYLOAD" | FOUNDRY_STATUSLINE_INNER=1 timeout 20 bash "$0" "$@" 2>/dev/null)"
  else
    _out="$(printf '%s' "$PAYLOAD" | FOUNDRY_STATUSLINE_INNER=1 bash "$0" "$@" 2>/dev/null)"
  fi
  printf '%s' "$_out" > "$_cache.tmp" 2>/dev/null && mv -f "$_cache.tmp" "$_cache" 2>/dev/null
  printf '%s\n' "${EPOCHSECONDS:-$(date +%s)}" > "$_cache.ts" 2>/dev/null
  rm -f "$_lock/ts" 2>/dev/null; rmdir "$_lock" 2>/dev/null
  printf '%s' "$_out"
  exit 0
fi

PAYLOAD="$(cat 2>/dev/null || true)"
CFG="${CLAUDE_CONFIG_DIR:-${HOME}/.claude}"
RENDERER="foundry-statusline.sh"

selected=""
# 1. installed_plugins.json (jq when available; the file is small and the key shape is fixed)
if [ -r "${CFG}/plugins/installed_plugins.json" ] && command -v jq >/dev/null 2>&1; then
  # v1.18.0: THIS project's record first, then the user-scope record, then any — never simply the
  # first record, which may be another project's install at another version.
  ip="$(jq -r --arg p "${CLAUDE_PROJECT_DIR:-$PWD}" '(.plugins."foundry@agentic-foundry" // ."foundry@agentic-foundry" // []) | (if type=="array" then . else [.] end) | ((map(select(.projectPath == $p)) + map(select(.projectPath == null)) + .)[0] // {}) | (.installPath // empty)' "${CFG}/plugins/installed_plugins.json" 2>/dev/null)"
  [ -n "$ip" ] && [ -r "${ip}/scripts/${RENDERER}" ] && selected="${ip}/scripts/${RENDERER}"
fi
# 2. the plugin cache, newest by version segment
if [ -z "$selected" ]; then
  selected="$(
    for cand in "${CFG}/plugins/cache/"*/foundry/*/scripts/${RENDERER}; do
      [ -f "$cand" ] || continue
      ver="$(basename "$(dirname "$(dirname "$cand")")")"
      printf '%s\t%s\n' "$ver" "$cand"
    done | sort -V -k1,1 | tail -1 | cut -f2-
  )"
fi
# 3. the self-hosting source checkout
if [ -z "$selected" ] || [ ! -r "$selected" ]; then
  src="${CLAUDE_PROJECT_DIR:-$PWD}/agentic-foundry/scripts/${RENDERER}"
  [ -r "$src" ] && selected="$src"
fi

# The resolved file must be the shipped renderer, not merely a file at a plausible path (security
# review, Risk 3): its own header line is required before it is run. A miss falls through to the
# inline bar, and so does a renderer that exits non-zero — `exec` on the right of a pipe only
# replaces the subshell, so the bar below is what "never silently absent" rests on.
if [ -n "$selected" ] && [ -r "$selected" ] && grep -q '^# foundry-statusline.sh' "$selected" 2>/dev/null; then
  if printf '%s' "$PAYLOAD" | bash "$selected" "$@"; then
    exit 0
  fi
fi

# 4. inline fallback — the bar itself, from the payload, so it is never silently absent
jqr() { printf '%s' "$PAYLOAD" | jq -r "$1" 2>/dev/null || true; }
DIR=""; REM=""
if command -v jq >/dev/null 2>&1; then
  DIR="$(jqr '(.workspace.current_dir // .workspace.project_dir // .cwd // empty)')"
  REM="$(jqr '(.context_window.remaining_percentage // empty)')"
fi
[ -n "$DIR" ] || DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
LABEL="$(basename "$DIR" 2>/dev/null)"
BRANCH="$(git -C "$DIR" symbolic-ref --short HEAD 2>/dev/null || true)"
[ -n "$BRANCH" ] && LABEL="${LABEL}:${BRANCH}"
OUT="⌂ ${LABEL}"
case "$REM" in
  ''|*[!0-9.]*) ;;
  *)
    USED="$(printf '%.0f' "$(printf '100 - %s\n' "$REM" | bc -l 2>/dev/null || echo 0)" 2>/dev/null)"
    [ -n "$USED" ] || USED=0
    [ "$USED" -lt 0 ] 2>/dev/null && USED=0
    [ "$USED" -gt 100 ] 2>/dev/null && USED=100
    FILLED=$(( USED / 10 )); BAR=""
    i=0; while [ $i -lt 10 ]; do if [ $i -lt $FILLED ]; then BAR="${BAR}█"; else BAR="${BAR}░"; fi; i=$((i+1)); done
    OUT="${OUT} · tok ${BAR} ${USED}%"
    ;;
esac
printf '%s\n' "$OUT"
exit 0
