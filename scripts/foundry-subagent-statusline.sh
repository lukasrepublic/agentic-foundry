#!/usr/bin/env bash
# foundry-subagent-statusline.sh — the subagentStatusLine fleet-row renderer
# (feat-foundry-isolation-statusline, AC-ISL-4).
#
# Wired to the `subagentStatusLine` settings key (a SEPARATE key from `statusLine`). Renders one JSON
# row per dispatched worker carrying worker-name · status · token count — the lightweight native analog
# of a control-pane fleet view. Mirrors a proven upstream subagent-statusline output contract.
#
# Output contract: one JSON line per row — {"id": "<task id>", "content": "<row body>"}
#   row body = "<name> [<status>] <tok> tok". Omitting an id preserves a native default row.
#
# NO worktree token: the native subagentStatusLine payload carries no confirmed per-worker worktree
# field, so the schema does not invent one (worktree surfacing is a fleet-layer concern).
#
# FAIL-OPEN: on any parse error (no jq, malformed/empty stdin, non-TTY) it emits NOTHING so the native
# default rows are preserved; it runs locally and consumes no API tokens. Always exit 0.
set +e

# stdin: non-TTY guarded so a no-stdin / interactive-TTY call can never hang.
raw_stdin=""
if [ ! -t 0 ]; then
  raw_stdin="$(cat 2>/dev/null || true)"
fi
[ -n "$raw_stdin" ] || exit 0

command -v jq >/dev/null 2>&1 || exit 0

# Validate + slice the workers array — malformed JSON → emit nothing (fail-open, default rows preserved).
tasks_json="$(printf '%s' "$raw_stdin" | jq -c '.tasks // []' 2>/dev/null)"
[ $? -eq 0 ] && [ -n "$tasks_json" ] || exit 0

count="$(printf '%s' "$tasks_json" | jq 'length' 2>/dev/null || echo 0)"
[ -n "$count" ] || count=0

i=0
while [ "$i" -lt "$count" ]; do
  task="$(printf '%s' "$tasks_json" | jq -c ".[$i]" 2>/dev/null || true)"
  i=$((i+1))
  [ -n "$task" ] || continue

  task_id="$(printf '%s' "$task" | jq -r '.id // empty' 2>/dev/null || true)"
  [ -n "$task_id" ] || continue                    # no id → would preserve a default row; omit entirely

  task_name="$(printf '%s' "$task" | jq -r '(.name // .label // "worker")' 2>/dev/null || echo worker)"
  task_status="$(printf '%s' "$task" | jq -r '.status // "unknown"' 2>/dev/null || echo unknown)"
  tok_raw="$(printf '%s' "$task" | jq -r '.tokenCount // empty' 2>/dev/null || true)"

  tok_str="—"
  if [ -n "$tok_raw" ] && [ "$tok_raw" != "null" ]; then
    tok_int="$(printf '%s' "$tok_raw" | jq -r 'floor' 2>/dev/null || echo 0)"
    tok_int="${tok_int:-0}"
    if [ "$tok_int" -ge 1000 ] 2>/dev/null; then
      tok_str="$(awk -v t="$tok_int" 'BEGIN{printf "%.1fk", t/1000}' 2>/dev/null || echo "${tok_int}")"
    else
      tok_str="$tok_int"
    fi
  fi

  row_content="${task_name} [${task_status}] ${tok_str} tok"
  jq -nc --arg id "$task_id" --arg content "$row_content" '{"id": $id, "content": $content}' 2>/dev/null || true
done

exit 0
