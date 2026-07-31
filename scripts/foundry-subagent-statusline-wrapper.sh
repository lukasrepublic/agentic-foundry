#!/usr/bin/env bash
# foundry-subagent-statusline-wrapper.sh — version-agnostic, fail-open self-resolving subagentStatusLine
# wrapper (feat-foundry-init-statusline-wrapper, AC-SLW-1).
#
# /foundry:init installs this into an adopter repo at .claude/hooks/foundry-subagent-statusline.sh and
# wires the adopter's subagentStatusLine.command to
# "$CLAUDE_PROJECT_DIR/.claude/hooks/foundry-subagent-statusline.sh" — the EXPANDABLE placeholder.
# (The plugin-root hook path-placeholder is HOOK-scoped and does NOT expand in a statusLine command, so
# this wrapper references it NOWHERE.) This thin resolver discovers the newest INSTALLED foundry subagent
# renderer in the plugin cache and execs it, passing the fleet-row payload through stdin.
#
# VERSION-SEGMENT SORT (the load-bearing §8 fix): select the newest by the <version> PATH-SEGMENT (the
# dir between /foundry/ and /scripts/), NOT a whole-path `sort -V`. A whole-path sort ranks by the
# MARKETPLACE name first, so with foundry installed under >1 marketplace an OLDER version under a
# lexically-greater marketplace could win — the stale-renderer failure this wrapper prevents. We emit a
# "<version>\t<fullpath>" line per candidate, `sort -V` on the version field, and map the max back to its
# path.
#
# FAIL-OPEN is the only invariant: no cache match / unreadable-or-absent renderer / any error → print
# NOTHING and `exit 0`. It can never break a prompt. The glob root is ${HOME} (overridable via the HOME
# env, so the resolution is exercisable over a throwaway fixture cache).
set +e

# Resolve the newest installed subagent renderer by the VERSION path-segment. The `[ -f ]` guard makes a
# no-match glob (which bash leaves as the literal pattern) yield nothing — no nullglob dependency.
selected="$(
  for cand in "${HOME}/.claude/plugins/cache/"*/foundry/*/scripts/foundry-subagent-statusline.sh; do
    [ -f "$cand" ] || continue
    # version segment = basename of the dir two levels up: .../foundry/<version>/scripts/<file>
    ver="$(basename "$(dirname "$(dirname "$cand")")")"
    printf '%s\t%s\n' "$ver" "$cand"
  done | sort -V -k1,1 | tail -1 | cut -f2-
)"

[ -n "$selected" ] || exit 0          # no cache match → fail-open
[ -r "$selected" ] || exit 0          # unreadable / absent renderer → fail-open

# exec the selected renderer: stdin (the fleet-row payload) passes through; forward "$@".
exec bash "$selected" "$@"
exit 0                                # only reached if exec itself fails → fail-open
