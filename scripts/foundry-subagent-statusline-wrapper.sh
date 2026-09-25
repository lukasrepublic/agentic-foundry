#!/usr/bin/env bash
# foundry-subagent-statusline.sh — version-agnostic, fail-open self-resolving subagentStatusLine wrapper
# (feat-foundry-init-statusline-wrapper, AC-SLW-1; statusline-wiring v1.17.0, AC-SLW-3).
#
# Installed at .claude/hooks/foundry-subagent-statusline.sh by `npx update-agentic-workspace`, which also
# sets subagentStatusLine.command to "$CLAUDE_PROJECT_DIR/.claude/hooks/foundry-subagent-statusline.sh".
# The line above carrying the feature id is the FRAMEWORK MARKER the updater converges on.
#
# Same three-step resolution as the main wrapper (installed_plugins.json under
# ${CLAUDE_CONFIG_DIR:-$HOME/.claude}, then the cache newest by version segment, then the self-hosting
# source). A sub-agent row has no inline fallback: with no renderer it prints nothing and exits 0.
set +e

PAYLOAD="$(cat 2>/dev/null || true)"
CFG="${CLAUDE_CONFIG_DIR:-${HOME}/.claude}"
RENDERER="foundry-subagent-statusline.sh"

selected=""
if [ -r "${CFG}/plugins/installed_plugins.json" ] && command -v jq >/dev/null 2>&1; then
  # v1.18.0: THIS project's record first, then the user-scope record, then any — never simply the
  # first record, which may be another project's install at another version.
  ip="$(jq -r --arg p "${CLAUDE_PROJECT_DIR:-$PWD}" '(.plugins."foundry@agentic-foundry" // ."foundry@agentic-foundry" // []) | (if type=="array" then . else [.] end) | ((map(select(.projectPath == $p)) + map(select(.projectPath == null)) + .)[0] // {}) | (.installPath // empty)' "${CFG}/plugins/installed_plugins.json" 2>/dev/null)"
  [ -n "$ip" ] && [ -r "${ip}/scripts/${RENDERER}" ] && selected="${ip}/scripts/${RENDERER}"
fi
if [ -z "$selected" ]; then
  selected="$(
    for cand in "${CFG}/plugins/cache/"*/foundry/*/scripts/${RENDERER}; do
      [ -f "$cand" ] || continue
      ver="$(basename "$(dirname "$(dirname "$cand")")")"
      printf '%s\t%s\n' "$ver" "$cand"
    done | sort -V -k1,1 | tail -1 | cut -f2-
  )"
fi
if [ -z "$selected" ] || [ ! -r "$selected" ]; then
  src="${CLAUDE_PROJECT_DIR:-$PWD}/agentic-foundry/scripts/${RENDERER}"
  [ -r "$src" ] && selected="$src"
fi

[ -n "$selected" ] || exit 0
[ -r "$selected" ] || exit 0
# The resolved file must be the shipped renderer (its own header line), not merely a file at a
# plausible path — security review, Risk 3.
grep -q '^# foundry-subagent-statusline.sh' "$selected" 2>/dev/null || exit 0
printf '%s' "$PAYLOAD" | bash "$selected" "$@"
exit 0
