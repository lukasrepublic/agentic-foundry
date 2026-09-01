#!/usr/bin/env bash
# foundry-statusline.sh — the isolation-first native statusLine (feat-foundry-isolation-statusline).
#
# Claude Code invokes a statusLine.command with the session context as a JSON payload on STDIN. This
# renderer surfaces the highest-value ambient signals for the parallel-autonomous-session operator,
# isolation-first:
#
#   ⌂ <repo>:<branch>[+ahead/-behind,?untracked] · ⊙ <native task> · tok ███░░ NN%
#
#   1. REPO ORIENTATION (leads, color-coded): `<glyph> <repo>:<branch>[+a/-b,?u]` — the repo basename
#      (from the git COMMON-dir, so it is identical for a main checkout OR a linked worktree of the same
#      repo), the branch (short-sha when detached, never empty), and a git-state cluster
#      `[+ahead/-behind,?untracked]` rendered only when out-of-sync / dirty (ahead+behind default 0 with
#      no upstream, so the bracket is never malformed). A linked/dedicated worktree → green `⊞`; the main
#      checkout → amber `⌂` (an honest "not isolated"; red is reserved for the tok bar). Detection =
#      realpath-normalized `git rev-parse --absolute-git-dir` ≠ `--git-common-dir`. A non-repo cwd
#      suppresses the segment.
#   2. NATIVE TASK: `⊙ <label>` read from the session todos file
#      `<CLAUDE_CONFIG_DIR||~/.claude>/todos/<sanitized-sessionId>-agent-*.json` (newest mtime, the
#      `in_progress` entry, `activeForm` with a `content` fallback). Suppressed when none. NO @status.
#   3. CONTEXT BAR: `tok ███░░ NN%` from `context_window.remaining_percentage` with the 16.5%
#      auto-compact-buffer transform, color-coded at the context-bar thresholds (green <50 / yellow 50–65 /
#      orange 65–80 / red+⚠ >80). Omitted when the field is absent/unparseable.
#   3b. OVER-BUDGET ESCALATION (feat-foundry-session-per-mandate, AC-SPM-1/-2, additive AFTER the tok
#      bar): when the RAW used-percentage (`100 − remaining_percentage`, distinct from the bar's buffer-
#      adjusted display value) is >= the configured threshold, append a distinct-glyph/red escalation
#      nudge naming the handoff move (`/foundry:context snapshot`). The threshold is read at RENDER
#      TIME from `$CLAUDE_PROJECT_DIR/.foundry/context-threshold` (a bare 1–99 integer), fail-safe to
#      the default 65 on ANY invalid shape (absent file, non-numeric, fractional, zero/negative,
#      >=100). ADVISORY ONLY — never blocks/delays/exits non-zero; degrades to the current
#      non-escalated rendering when `remaining_percentage` is absent/non-numeric/out-of-range.
#   4. SESSION MODE (feat-foundry-session-posture, additive — never displaces the leading isolation
#      token): `⚙️ factory` / `⚡ noninteractive` / `⏸ interactive`, resolved for the payload
#      `.session_id` (sanitized) via `foundry_session_mode.py resolve`. Default/absent/unreadable/
#      invalid stored value -> `⚙️ factory` (fail-safe, handled inside the resolver). Only a HARD
#      failure to execute the resolution at all omits the segment; the line never breaks.
#      Fork policy (feat-foundry-autonomous-fork-policy, AC-AFP-5) appends `+auto` to this SAME
#      glyph when `fork_policy=two-way-auto` (e.g. `⚡ noninteractive+auto`) — additive, same
#      fail-open wrapper, never a standalone segment.
#
# Opt-in extras (model / cost / ahead-behind) are OFF by default, gated behind FOUNDRY_STATUSLINE_EXTRAS.
#
# FAIL-OPEN is the only invariant: the line runs on every prompt and must NEVER break a session. Any
# error (no jq, git absent, malformed/empty payload, missing todos, non-TTY/no stdin) drops that
# segment and the rest still renders; the process always `exit 0`.
set +e

ESC=$'\033'
GREEN="${ESC}[32m"
AMBER="${ESC}[33m"
ORANGE="${ESC}[38;5;208m"
RED="${ESC}[1;31m"
RESET="${ESC}[0m"
AUTO_COMPACT_BUFFER_PCT=16.5
MAX_STDIN=1048576

# --- stdin: non-TTY guarded + size-bounded so a no-stdin / interactive-TTY call can never hang ---
PAYLOAD=""
if [ ! -t 0 ]; then
  PAYLOAD="$(head -c "$MAX_STDIN" 2>/dev/null || true)"
fi

_HAVE_JQ=0
command -v jq >/dev/null 2>&1 && _HAVE_JQ=1

jqr() {
  # Extract a single field from the payload; empty on any error or absent jq.
  [ "$_HAVE_JQ" -eq 1 ] || return 0
  printf '%s' "$PAYLOAD" | jq -r "$1" 2>/dev/null || true
}

_abspath() {
  # Physical (symlink-resolved) absolute path for a directory; echo as-is otherwise. Never fails.
  if [ -d "$1" ]; then (cd "$1" 2>/dev/null && pwd -P) || printf '%s' "$1"; else printf '%s' "$1"; fi
}

# Resolve the session cwd from the payload (the isolation test runs against THIS checkout).
DIR="$(jqr '(.workspace.current_dir // .workspace.project_dir // .cwd // empty)')"
[ -n "$DIR" ] || DIR="${CLAUDE_PROJECT_DIR:-$PWD}"

# ── segment 1: repo orientation (LEADS, color-coded) <glyph> <repo>:<branch>[+a/-b,?u] ─────────────
ISO=""
if git -C "$DIR" rev-parse --git-dir >/dev/null 2>&1; then
  GD="$(git -C "$DIR" rev-parse --absolute-git-dir 2>/dev/null)"
  CDR="$(git -C "$DIR" rev-parse --git-common-dir 2>/dev/null)"
  case "$CDR" in
    /*) CD="$CDR" ;;
    *)  CD="$DIR/$CDR" ;;
  esac
  GD="$(_abspath "$GD")"
  CD="$(_abspath "$CD")"
  # repo basename from the COMMON git-dir: dirname of the common `.git` is the repo working dir, so REPO
  # is identical for a main checkout AND a linked worktree of the same repo.
  REPO="$(basename "$(dirname "$CD")" 2>/dev/null)"
  BR="$(git -C "$DIR" symbolic-ref --short HEAD 2>/dev/null)"
  SHA="$(git -C "$DIR" rev-parse --short HEAD 2>/dev/null)"
  [ -n "$BR" ] || BR="$SHA"   # detached HEAD → short-sha, never empty
  # git-state cluster [+ahead/-behind,?untracked]; ahead/behind DEFAULT 0 when there is no upstream
  # (@{u} exits non-zero with empty stdout) — without the :-0 the cluster would render malformed.
  AH="$(git -C "$DIR" rev-list --count '@{u}..HEAD' 2>/dev/null || echo 0)"; AH=${AH:-0}
  BH="$(git -C "$DIR" rev-list --count 'HEAD..@{u}' 2>/dev/null || echo 0)"; BH=${BH:-0}
  U="$(git -C "$DIR" status --porcelain --untracked-files=normal 2>/dev/null | grep -c '^??')"; U=${U:-0}
  CLUSTER=""
  if [ "$((AH + BH + U))" -gt 0 ] 2>/dev/null; then
    CLUSTER="[+${AH}/-${BH},?${U}]"
  fi
  if [ -n "$REPO" ] && [ -n "$BR" ]; then
    if [ -n "$GD" ] && [ -n "$CD" ] && [ "$GD" != "$CD" ]; then
      # linked / dedicated worktree → green ⊞
      ISO="${GREEN}⊞ ${REPO}:${BR}${CLUSTER}${RESET}"
    else
      # main checkout → amber ⌂ (the "not isolated" signal retained)
      ISO="${AMBER}⌂ ${REPO}:${BR}${CLUSTER}${RESET}"
    fi
  fi
fi

# ── segment 2: native in-progress task (⊙ <label>) ────────────────────────────────────────────────
TASK=""
RAWSID="$(jqr '(.session_id // empty)')"
SID="$(printf '%s' "$RAWSID" | tr -cd 'A-Za-z0-9_-')"   # sanitizeSessionId: restrict charset, reject empty
if [ -n "$SID" ] && [ "$_HAVE_JQ" -eq 1 ]; then
  TODOS_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/todos"
  if [ -d "$TODOS_DIR" ]; then
    TODOFILE="$(ls -t "$TODOS_DIR/${SID}"-agent-*.json 2>/dev/null | head -1)"
    if [ -n "$TODOFILE" ] && [ -f "$TODOFILE" ]; then
      LABEL="$(jq -r 'map(select(.status=="in_progress"))[0] | (.activeForm // .content // empty)' "$TODOFILE" 2>/dev/null || true)"
      [ -n "$LABEL" ] && [ "$LABEL" != "null" ] && TASK="⊙ ${LABEL}"
    fi
  fi
fi

# ── segment 3: context-pressure bar (tok ███░░ NN%) ───────────────────────────────────────────────
TOK=""
REM="$(jqr '(.context_window.remaining_percentage // empty)')"
if [ -n "$REM" ]; then
  USED="$(awk -v r="$REM" -v b="$AUTO_COMPACT_BUFFER_PCT" 'BEGIN{
            ur=(r-b)/(100-b)*100; if(ur<0)ur=0; u=100-ur; u=int(u+0.5);
            if(u<0)u=0; if(u>100)u=100; print u }' 2>/dev/null)"
  if [ -n "$USED" ]; then
    FILL=$((USED/10)); [ "$FILL" -gt 10 ] && FILL=10; [ "$FILL" -lt 0 ] && FILL=0
    EMPTY=$((10-FILL))
    BAR=""
    i=0; while [ "$i" -lt "$FILL" ]; do BAR="${BAR}█"; i=$((i+1)); done
    i=0; while [ "$i" -lt "$EMPTY" ]; do BAR="${BAR}░"; i=$((i+1)); done
    if [ "$USED" -lt 50 ]; then COL="$GREEN"; WARN=""
    elif [ "$USED" -lt 65 ]; then COL="$AMBER"; WARN=""
    elif [ "$USED" -lt 80 ]; then COL="$ORANGE"; WARN=""
    else COL="$RED"; WARN=" ⚠"; fi
    TOK="${COL}tok ${BAR} ${USED}%${WARN}${RESET}"
  fi
fi

# ── segment 3b: over-budget escalation nudge (feat-foundry-session-per-mandate, AC-SPM-1/-2) ───────
# Additive AFTER the tok bar above — never replaces it. ADVISORY ONLY: this block can only ever
# APPEND text to $TOK; it never sets a non-zero exit, never sleeps/blocks, never touches $OUT gating.
if [ -n "$TOK" ]; then
  # AC-SPM-1: escalate only on a REM that is a genuinely-numeric, in-[0,100]-range value; any other
  # shape (absent — TOK already empty above — non-numeric, or out-of-range) degrades to the current
  # non-escalated $TOK rendering computed above (never a crash).
  if printf '%s' "$REM" | grep -Eq '^[0-9]+(\.[0-9]+)?$|^-[0-9]+(\.[0-9]+)?$'; then
    RAWUSED="$(awk -v r="$REM" 'BEGIN{
                u = 100 - r;
                if (u < 0 || u > 100) { print -1; exit }
                printf "%d", (u + 0.5) }' 2>/dev/null)"
    if [ -n "$RAWUSED" ] && [ "$RAWUSED" -ge 0 ] 2>/dev/null; then
      # AC-SPM-2: threshold read at RENDER TIME from $CLAUDE_PROJECT_DIR/.foundry/context-threshold;
      # fail-safe to the default 65 on ANY invalid shape — never a crash, never a disabled segment.
      THRESHOLD=65
      THRESH_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
      THRESH_FILE="$THRESH_ROOT/.foundry/context-threshold"
      if [ -f "$THRESH_FILE" ]; then
        THRESH_RAW="$(tr -d '[:space:]' < "$THRESH_FILE" 2>/dev/null)"
        case "$THRESH_RAW" in
          ''|*[!0-9]*)
            ;;   # empty / non-numeric / fractional ('.') / negative ('-') -> keep the default
          *)
            # all-digit string; range-gate to 1-99 (rejects 0, and >=100).
            if [ "$THRESH_RAW" -ge 1 ] 2>/dev/null && [ "$THRESH_RAW" -le 99 ] 2>/dev/null; then
              THRESHOLD="$THRESH_RAW"
            fi
            ;;
        esac
      fi
      if [ "$RAWUSED" -ge "$THRESHOLD" ] 2>/dev/null; then
        TOK="${TOK} ${RED}⛔ over budget → /foundry:context snapshot${RESET}"
      fi
    fi
  fi
fi

# ── segment 4: session mode posture (⚙️ factory / ⚡ noninteractive / ⏸ interactive) ─────────────────
# Additive; appended after the context bar, before the opt-in extras — the leading isolation-
# orientation token (segment 1) is never displaced. Fail-safe default handled INSIDE the resolver
# (absent/unreadable/invalid stored value -> "factory", exit 0); this segment is omitted ONLY when the
# resolution cannot be executed at all (python3 absent, script absent, non-zero exit, unparsable stdout).
MODESEG=""
if command -v python3 >/dev/null 2>&1; then
  MODE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd 2>/dev/null)}"
  MODE_SCRIPT="${MODE_PLUGIN_ROOT}/scripts/foundry_session_mode.py"
  if [ -f "$MODE_SCRIPT" ]; then
    MRESOLVED="$(python3 "$MODE_SCRIPT" resolve --session-id "$SID" 2>/dev/null)"
    MRC=$?
    if [ "$MRC" -eq 0 ]; then
      case "$MRESOLVED" in
        factory) MODESEG="⚙️ factory" ;;
        noninteractive) MODESEG="⚡ noninteractive" ;;
        interactive) MODESEG="⏸ interactive" ;;
        *) MODESEG="" ;;   # unparsable stdout -> treat as an exec failure, omit (never fabricate)
      esac
    fi
  fi
fi

# ── segment 4b: fork-policy suffix (feat-foundry-autonomous-fork-policy, AC-AFP-5) ──────────────────
# Additive: appended to the SAME mode glyph (never a standalone token) so the operator always sees
# "this session may auto-answer" right next to the posture — never before segment 1 (isolation),
# never fabricated when the mode glyph itself is absent. Fail-safe: any resolution failure (script
# absent, non-zero exit, unparsable stdout) leaves MODESEG unchanged — no crash, no fabricated "+auto".
if [ -n "$MODESEG" ] && command -v python3 >/dev/null 2>&1 && [ -f "$MODE_SCRIPT" ]; then
  FPRESOLVED="$(python3 "$MODE_SCRIPT" resolve-fork-policy --session-id "$SID" 2>/dev/null)"
  FPRC=$?
  if [ "$FPRC" -eq 0 ] && [ "$FPRESOLVED" = "two-way-auto" ]; then
    MODESEG="${MODESEG}+auto"
  fi
fi

# ── opt-in extras (model / cost / ahead-behind), OFF by default ───────────────────────────────────
EXTRAS=""
case "${FOUNDRY_STATUSLINE_EXTRAS:-}" in
  ""|0|false|False|FALSE|no|off|OFF) ;;
  *)
    parts=""
    MODEL="$(jqr '(.model.display_name // empty)')"
    [ -n "$MODEL" ] && parts="$MODEL"
    COST="$(jqr '(.cost.total_cost_usd // empty)')"
    if [ -n "$COST" ]; then
      CFMT="$(awk -v c="$COST" 'BEGIN{printf "%.2f", c}' 2>/dev/null)"
      [ -n "$CFMT" ] && parts="${parts:+$parts }\$${CFMT}"
    fi
    if git -C "$DIR" rev-parse --git-dir >/dev/null 2>&1; then
      if git -C "$DIR" rev-parse '@{u}' >/dev/null 2>&1; then
        AH="$(git -C "$DIR" rev-list --count '@{u}..HEAD' 2>/dev/null || echo 0)"
        BH="$(git -C "$DIR" rev-list --count 'HEAD..@{u}' 2>/dev/null || echo 0)"
        parts="${parts:+$parts }[+${AH}/-${BH}]"
      fi
    fi
    [ -n "$parts" ] && EXTRAS="${ESC}[2m${parts}${RESET}"
    ;;
esac

# ── join non-empty segments with ' · ' (isolation leads) ──────────────────────────────────────────
OUT=""
for seg in "$ISO" "$TASK" "$TOK" "$MODESEG" "$EXTRAS"; do
  [ -z "$seg" ] && continue
  if [ -z "$OUT" ]; then OUT="$seg"; else OUT="$OUT · $seg"; fi
done
printf '%s\n' "$OUT"
exit 0
