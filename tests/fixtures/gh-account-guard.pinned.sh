#!/usr/bin/env bash
# PINNED FROM: lukasrepublic/agentic-handbook @ ac923eadbd8a8c476d2f0f0bf125a10b642cf63e
#   path   : .claude/hooks/gh-account-guard.sh (the SHIPPED workspace-template guard variant)
#   pinned : 2026-07-28, by feat-foundry-upstream-submit-identity (issue #21 item b)
#   why    : agreement is proven against a byte copy of the REAL shipped guard, driven over its
#            real stdin protocol (AC-URIDENT-2/-7 fixture), not against this spec's prose
#            restatement of it. A later upstream change to the shipped guard is a deliberate
#            re-pin of this file, never a silent drift — see the sibling spec's Clarifications.
# BYTE-IDENTICAL BELOW THIS HEADER to the pinned commit's file (only this header comment block
# and the shebang line above it are added; nothing below is edited).
#
# Fail-loud PreToolUse(Bash) guard — per-project `gh` IDENTITY ISOLATION (opt-in). (UL-0007)
#
# `gh`'s active account is a single GLOBAL setting (~/.config/gh/hosts.yml), so an operator with
# >1 GitHub account can silently run gh (PR create/merge, API) as the WRONG identity on some
# project — and because `git push` can be correct via an SSH alias, the mismatch hides until a
# 404/permission error. This guard pins each project's gh to the account declared in the NEAREST
# .claude/gh-identity (walking up from the command's cwd), so:
#   • switching between handbook projects never collides on the one global account;
#   • a NESTED repo declaring a different account is enforced as ITS account, not the umbrella's
#     (cwd-aware resolution — AC-5 nested mixed-identity);
#   • a gh command under the wrong/unset GH_CONFIG_DIR is BLOCKED fail-loud, never run as the
#     wrong identity.
#
# OPT-IN: if no .claude/gh-identity exists (walking up), the guard is DORMANT (allows everything) —
# single-account adopters are unaffected until they declare an identity. Generic: no account is
# hardcoded; the expected identity is derived per-project. Cheap, NO network (env/file compare only).
input="$(cat)"
if command -v jq >/dev/null 2>&1; then
  cmd="$(printf '%s' "$input" | jq -r '.tool_input.command // ""')"
else
  cmd="$(printf '%s' "$input" | python3 -c 'import sys,json; d=json.load(sys.stdin); print((d.get("tool_input") or {}).get("command",""))' 2>/dev/null || true)"
fi

# Not a gh invocation (word-boundary; does not match "github"/"weight") → allow untouched.
printf '%s' "$cmd" | grep -Eq '(^|[[:space:]]|[;&|(])gh([[:space:]]|$)' || exit 0

# Resolve THIS command's declared identity: the nearest .claude/gh-identity walking up from the
# command's cwd (AC-5), falling back to the project root.
_find_idfile() {
  local d="${1:-$PWD}"
  while [ -n "$d" ] && [ "$d" != "/" ]; do
    [ -f "$d/.claude/gh-identity" ] && { printf '%s' "$d/.claude/gh-identity"; return 0; }
    d="$(dirname "$d")"
  done
  [ -n "${CLAUDE_PROJECT_DIR:-}" ] && [ -f "$CLAUDE_PROJECT_DIR/.claude/gh-identity" ] \
    && printf '%s' "$CLAUDE_PROJECT_DIR/.claude/gh-identity"
  return 0
}
idfile="$(_find_idfile "$PWD")"

# OPT-IN: no declared identity anywhere up the tree → guard dormant, allow.
{ [ -n "$idfile" ] && [ -f "$idfile" ]; } || exit 0
acct="$(tr -d '[:space:]' < "$idfile")"
[ -z "$acct" ] && exit 0   # empty declaration → not configured → dormant

expect="$HOME/.config/gh-$acct"
[ "${GH_CONFIG_DIR:-}" = "$expect" ] && exit 0

echo "BLOCKED: gh must use this project's GitHub identity '$acct' (declared in $idfile)." >&2
echo "  GH_CONFIG_DIR is '${GH_CONFIG_DIR:-<unset>}', expected '$expect'." >&2
echo "  Fix: 'direnv allow' (terminal) or set GH_CONFIG_DIR in .claude/settings.json env (Claude Code);" >&2
echo "  bootstrap the account once via .claude/hooks/gh-identity-bootstrap.sh." >&2
exit 2
