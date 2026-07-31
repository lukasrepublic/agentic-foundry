#!/usr/bin/env bash
# foundry-harvest-learnings — durably capture a worker's learning records so a worktree teardown can
# never silently drop them (closes the worker-emission-loss gap; the 2026-06-15 incident).
#
# PRIMARY channel (durable, no teardown race): the worker returns its records in the dispatch result
# `learnings[]`; the parent captures them via `capture-return` the moment the worker returns — the
# worktree need not still exist (actor/supervisor pattern; CI artifact-upload-before-teardown).
# DEFENSE-IN-DEPTH: a file-sidecar harvest on the explicit `git worktree remove` seam (the literal
# incident + handbook-compat workers). `WorktreeRemove` is at/post-deletion + undocumented → NOT a seam.
#
# Modes:
#   foundry-harvest-learnings.sh capture-return [<session_id>] [<task_id>]   # stdin: worker result JSON
#   foundry-harvest-learnings.sh harvest <worktree_path> [<session_id>]
#   foundry-harvest-learnings.sh pretooluse        # reads hook stdin JSON; acts only on git worktree remove
#   foundry-harvest-learnings.sh --selftest
#
# CONTRACT (do not drift): records forwarded UNVALIDATED (the learn-distill consumer is the schema
# authority); atomic write to a date-partitioned PER-WORKER path so fan-out never overwrites; harvest
# precedes removal; WARN (stderr) on an absent sidecar for a worker that reported success; FAIL-OPEN
# (never block teardown/dispatch on a harvest error) — and every attempt logs a durable reconciliation
# row to `.harvest-log.jsonl` so a silent loss is detectable out-of-band.
set -uo pipefail   # deliberately NOT -e: fail-open, never abort the caller

# Shared writer/marker/scrub primitives — extracted into foundry-learnings-lib.sh so this worker
# producer and the direct-session producer (foundry-session-learnings.sh) share ONE writer + loss-log
# + secret-scrub (feat-foundry-session-learnings-capture). Sourcing defines functions only.
# Provides: _foundry_root _dest_root _sanitize _content_hash _taskid_from_path _secret_scrub
#           _log_attempt _write_records (+ marker fns, unused here).
_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo .)"
# shellcheck source=foundry-learnings-lib.sh
. "$_LIB_DIR/foundry-learnings-lib.sh"

# PRIMARY: capture learnings from a worker's structured RETURN (stdin JSON), in the parent. Fail-open.
_capture_return() {  # [session_id] [task_id]   stdin: {"branch":..,"learnings":[...]}
  local sid="${1:-${CLAUDE_SESSION_ID:-unknown}}" taskid_arg="${2:-}"
  local payload tmp branch taskid
  payload="$(cat 2>/dev/null || true)"
  tmp="$(mktemp 2>/dev/null)" || { echo "WARN: foundry-harvest: mktemp failed (fail-open)" >&2; return 0; }
  branch="$(printf '%s' "$payload" | python3 -c '
import sys, json
def canon(r):
    # CANONICAL form (sort_keys) so the SAME logical record dedups regardless of which serializer
    # produced it (Agent tool result vs the Workflow re-serialization) — content-addressed idempotency
    # must be logical-identity, not byte-identity. String records are re-parsed when possible.
    if isinstance(r, str):
        try: r = json.loads(r)
        except Exception: return r.rstrip("\n")
    return json.dumps(r, sort_keys=True, separators=(",", ":"))
try: d = json.load(sys.stdin)
except Exception: d = {}
recs = d.get("learnings") or []
with open(sys.argv[1], "w") as fh:
    for r in recs:
        fh.write(canon(r) + "\n")
print(d.get("branch") or "")
' "$tmp" 2>/dev/null || true)"
  [ -n "$taskid_arg" ] && taskid="$(_sanitize "$taskid_arg")" || taskid="$(_sanitize "${branch:-worker}")"
  if [ ! -s "$tmp" ]; then
    rm -f "$tmp" 2>/dev/null || true
    _log_attempt return "$taskid" 0 empty   # no learnings in the return is not an error
    return 0
  fi
  _write_records "$tmp" "$sid" "$taskid" return >/dev/null || true
  rm -f "$tmp" 2>/dev/null || true
  return 0
}

# DEFENSE-IN-DEPTH: harvest ONE worktree's sidecar before removal. Always returns 0 (fail-open).
_harvest_one() {  # worktree_path [session_id]
  local wt="${1:-}" sid="${2:-${CLAUDE_SESSION_ID:-unknown}}"
  [ -n "$wt" ] || return 0
  local sidecar="$wt/.agent/learnings.jsonl" result="$wt/.agent/result.json" taskid
  taskid="$(_taskid_from_path "$wt")"
  if [ ! -f "$sidecar" ]; then
    if [ -f "$result" ] && grep -Eq '"status"[[:space:]]*:[[:space:]]*"(ready|success|complete|completed|passed|green|ok)"' "$result" 2>/dev/null; then
      echo "WARN: foundry-harvest: worker '$taskid' reported success but emitted no .agent/learnings.jsonl sidecar (the worker-emission contract was not honored)" >&2
      _log_attempt sidecar "$taskid" 0 warn-absent
    fi
    return 0
  fi
  _write_records "$sidecar" "$sid" "$taskid" sidecar >/dev/null || true
  return 0
}

# ENFORCED capture (PostToolUse on Agent|Workflow): when a worker/wave tool call completes, read its
# structured RETURN from the hook payload and capture each worker's learnings[] into the parent buffer
# — automatically, no skill step (closes the unenforced-skill-step gap). The return survives worktree
# teardown by construction (we read the tool result, never the ephemeral worktree). ALWAYS exit 0.
_posttooluse() {
  local payload sid workdir f taskid
  payload="$(cat 2>/dev/null || true)"
  sid="$(printf '%s' "$payload" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("session_id",""))
except Exception: print("")' 2>/dev/null || true)"
  [ -n "$sid" ] || sid="${CLAUDE_SESSION_ID:-unknown}"
  workdir="$(mktemp -d 2>/dev/null)" || exit 0
  # Extract (task-id → records) from the tool result. Handles: the Agent single-object return
  # {branch,learnings[]}; the release-wave array [{atom,impl:{branch,learnings[]},verdict}]; and the
  # tool result carried as tool_output/tool_response, possibly as {"type":"text","text":"<json>"} or a
  # JSON string. Records forwarded UNVALIDATED. Writes one file per task-id into $workdir.
  printf '%s' "$payload" | python3 -c '
import sys, json, os, re
workdir = sys.argv[1]
def safe(s):
    return re.sub(r"[^A-Za-z0-9._-]", "-", str(s or "worker")) or "worker"
try: payload = json.load(sys.stdin)
except Exception: sys.exit(0)
res = None
for k in ("tool_output", "tool_response", "tool_result"):
    if k in payload: res = payload[k]; break
def as_obj(x):
    if isinstance(x, dict) and "text" in x and set(x.keys()) <= {"type", "text"}: x = x["text"]
    if isinstance(x, str):
        try: return json.loads(x)
        except Exception: return None
    return x
def canon(r):
    # CANONICAL (sort_keys) so the SAME logical record dedups across serializers (Agent result vs the
    # Workflow re-serialization) — logical-identity content-addressing, not byte-identity.
    if isinstance(r, str):
        try: r = json.loads(r)
        except Exception: return r.rstrip("\n")
    return json.dumps(r, sort_keys=True, separators=(",", ":"))
workers = []
def collect(o):
    o = as_obj(o)   # unwrap {type,text}/stringified JSON at EVERY level (handles the realistic
                    # content-block-ARRAY shape [{"type":"text","text":"<json>"}], not just the root)
    if isinstance(o, dict):
        ls = o.get("learnings")
        if isinstance(ls, list) and ls:
            workers.append((o.get("branch") or o.get("atom") or (o.get("impl") or {}).get("branch") or "worker", ls))
        if isinstance(o.get("impl"), (dict, list, str)): collect(o["impl"])
    elif isinstance(o, list):
        for it in o: collect(it)
collect(res)
for task, ls in workers:
    with open(os.path.join(workdir, safe(task) + ".jsonl"), "a") as fh:
        for r in ls:
            fh.write(canon(r) + "\n")
' "$workdir" 2>/dev/null || true
  for f in "$workdir"/*.jsonl; do
    [ -e "$f" ] || continue
    taskid="$(basename "$f" .jsonl)"
    _write_records "$f" "$sid" "$taskid" return >/dev/null || true
  done
  rm -rf "$workdir" 2>/dev/null || true
  exit 0
}

# PreToolUse(Bash): act ONLY on `git worktree remove`; harvest each target, then ALWAYS exit 0.
_pretooluse() {
  local payload cmd sid paths
  payload="$(cat 2>/dev/null || true)"
  cmd="$(printf '%s' "$payload" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("tool_input",{}).get("command",""))
except Exception: print("")' 2>/dev/null || true)"
  case "$cmd" in *worktree*remove*) : ;; *) exit 0 ;; esac
  sid="$(printf '%s' "$payload" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("session_id",""))
except Exception: print("")' 2>/dev/null || true)"
  [ -n "$sid" ] || sid="${CLAUDE_SESSION_ID:-unknown}"
  paths="$(printf '%s' "$cmd" | python3 -c '
import sys, shlex
try: toks = shlex.split(sys.stdin.read())
except Exception: sys.exit(0)
for j in range(len(toks)-1):
    if toks[j]=="worktree" and toks[j+1]=="remove":
        print("\n".join(t for t in toks[j+2:] if not t.startswith("-"))); break
' 2>/dev/null || true)"
  if [ -n "$paths" ]; then
    while IFS= read -r wt; do [ -n "$wt" ] && _harvest_one "$wt" "$sid" || true; done <<EOF
$paths
EOF
  fi
  exit 0
}

# ----------------------------------- self-test ----------------------------------- #
_selftest() {
  local tmp fails=0 date_part sid="testsid"
  tmp="$(mktemp -d)"; date_part="$(date -u +%F)"
  _emit() { if [ "$2" -eq 0 ]; then echo "$1: PASS${3:+ — $3}"; else echo "$1: FAIL${3:+ — $3}"; fails=$((fails+1)); fi; }
  # filenames are content-addressed (<sid>__<task>__<hash>.jsonl) — match per-worker by GLOB.
  _files() { find "$proj/.foundry/session-learnings/$date_part" -name "$1" 2>/dev/null | wc -l | tr -d ' '; }
  _recs()  { cat $(find "$proj/.foundry/session-learnings/$date_part" -name "$1" 2>/dev/null) 2>/dev/null | grep -c . || echo 0; }
  echo "foundry-harvest self-test:"
  local proj="$tmp/proj"; mkdir -p "$proj/.foundry"; export CLAUDE_PROJECT_DIR="$proj"

  # AC-LEARN-1: capture from the structured RETURN, in the parent (no worktree involved).
  local result='{"branch":"atom-alpha","status":"ready","learnings":[{"l":"a"},{"l":"b"},{"l":"c"}]}'
  printf '%s' "$result" | _capture_return "$sid" >/dev/null 2>&1
  if [ "$(_files "${sid}__atom-alpha__*.jsonl")" -ge 1 ] && [ "$(_recs "${sid}__atom-alpha__*.jsonl")" -eq 3 ]; then _emit "AC-LEARN-1 return-capture-in-parent" 0 "3 records from return → parent buffer"; else _emit "AC-LEARN-1 return-capture-in-parent" 1 "files=$(_files "${sid}__atom-alpha__*.jsonl") n=$(_recs "${sid}__atom-alpha__*.jsonl")"; fi

  # AC-LEARN-2: sidecar harvest via the real PreToolUse(Bash) seam, BEFORE removal (wt still present).
  local wt2="$tmp/wt/.worktrees/repoA/atom-2"; mkdir -p "$wt2/.agent"; printf '%s\n' '{"s":1}' '{"s":2}' > "$wt2/.agent/learnings.jsonl"
  ( printf '%s' "{\"tool_input\":{\"command\":\"git worktree remove --force $wt2\"},\"session_id\":\"$sid\"}" | _pretooluse ) >/dev/null 2>&1
  if [ "$(_files "${sid}__repoA-atom-2__*.jsonl")" -ge 1 ] && [ "$(_recs "${sid}__repoA-atom-2__*.jsonl")" -eq 2 ] && [ -d "$wt2" ]; then _emit "AC-LEARN-2 sidecar-harvest-before-explicit-removal" 0 "2 records; worktree intact at harvest"; else _emit "AC-LEARN-2 sidecar-harvest-before-explicit-removal" 1 "files=$(_files "${sid}__repoA-atom-2__*.jsonl") n=$(_recs "${sid}__repoA-atom-2__*.jsonl") wt=$([ -d "$wt2" ]&&echo y||echo n)"; fi

  # AC-LEARN-3: fail-open (unwritable buffer → rc 0) AND the durable loss-log records a reconciliation row.
  local log="$proj/.foundry/session-learnings/.harvest-log.jsonl" logged=1
  [ -f "$log" ] && grep -q '"channel":"return"' "$log" && grep -q '"task_id":"atom-alpha"' "$log" && grep -q '"records":3' "$log" && logged=0
  local roproj="$tmp/ro"; mkdir -p "$roproj/.foundry/session-learnings"; chmod 0500 "$roproj/.foundry/session-learnings"
  printf '%s' "$result" | ( export CLAUDE_PROJECT_DIR="$roproj"; _capture_return "$sid" ) >/dev/null 2>&1; local rc3=$?
  chmod 0700 "$roproj/.foundry/session-learnings" 2>/dev/null || true
  if [ "$logged" -eq 0 ] && [ "$rc3" -eq 0 ]; then _emit "AC-LEARN-3 fail-open-and-loss-logged" 0 "loss-log row present; capture rc=0 on unwritable buffer"; else _emit "AC-LEARN-3 fail-open-and-loss-logged" 1 "logged=$logged rc=$rc3"; fi

  # AC-LEARN-4: fan-out → N distinct files; SAME-key collision → no overwrite (both survive);
  # absent-sidecar-ready→WARN, failed→silent.
  rm -rf "$proj/.foundry/session-learnings"
  local i; for i in 1 2 3 4 5; do printf '%s' "{\"branch\":\"atom-f$i\",\"learnings\":[{\"r\":$i}]}" | _capture_return "$sid" >/dev/null 2>&1; done
  local cnt; cnt="$(find "$proj/.foundry/session-learnings/$date_part" -name "${sid}__atom-f*.jsonl" 2>/dev/null | wc -l | tr -d ' ')"
  # collision: two workers, SAME branch + session → must NOT overwrite (both records survive)
  printf '%s' '{"branch":"dup","learnings":[{"w":"A"}]}' | _capture_return "$sid" >/dev/null 2>&1
  printf '%s' '{"branch":"dup","learnings":[{"w":"B"}]}' | _capture_return "$sid" >/dev/null 2>&1
  local dupcnt duprecs
  dupcnt="$(find "$proj/.foundry/session-learnings/$date_part" -name "${sid}__dup*.jsonl" 2>/dev/null | wc -l | tr -d ' ')"
  duprecs="$(cat $(find "$proj/.foundry/session-learnings/$date_part" -name "${sid}__dup*.jsonl" 2>/dev/null) 2>/dev/null | grep -c . || echo 0)"
  # CONCURRENCY: 20 parallel captures of the SAME task-id must not clobber (atomic `ln` claim) — the
  # real fan-out scenario. All 20 records must survive across distinct suffixed files.
  local j; for j in $(seq 1 20); do ( printf '%s' "{\"branch\":\"conc\",\"learnings\":[{\"j\":$j}]}" | _capture_return "$sid" ) >/dev/null 2>&1 & done; wait
  local concrecs; concrecs="$(cat $(find "$proj/.foundry/session-learnings/$date_part" -name "${sid}__conc*.jsonl" 2>/dev/null) 2>/dev/null | grep -c . || echo 0)"
  local wt3="$tmp/wt/.worktrees/repoA/atom-3"; mkdir -p "$wt3/.agent"; echo '{"status":"ready"}' > "$wt3/.agent/result.json"
  local wt4="$tmp/wt/.worktrees/repoA/atom-4"; mkdir -p "$wt4/.agent"; echo '{"status":"failed"}' > "$wt4/.agent/result.json"
  local wr wf; wr="$(_harvest_one "$wt3" "$sid" 2>&1 1>/dev/null)"; wf="$(_harvest_one "$wt4" "$sid" 2>&1 1>/dev/null)"
  if [ "$cnt" -eq 5 ] && [ "$dupcnt" -eq 2 ] && [ "$duprecs" -eq 2 ] && [ "$concrecs" -eq 20 ] && printf '%s' "$wr" | grep -q WARN && ! printf '%s' "$wf" | grep -q WARN; then
    _emit "AC-LEARN-4 fan-out-distinct-collision-safe-warn-absent" 0 "5 distinct; same-key→2/2; 20 concurrent→20 records (atomic, no clobber); ready→WARN, failed→silent"
  else _emit "AC-LEARN-4 fan-out-distinct-collision-safe-warn-absent" 1 "cnt=$cnt dupcnt=$dupcnt duprecs=$duprecs concrecs=$concrecs wr='$wr' wf='$wf'"; fi

  # AC-LEARN-5: ENFORCED PostToolUse capture from the tool RETURN — Agent (single obj, tool_output as
  # {type,text} stringified) AND Workflow (array, tool_response with nested impl.learnings).
  rm -rf "$proj/.foundry/session-learnings"
  local agent_payload='{"session_id":"'"$sid"'","tool_name":"Agent","tool_output":{"type":"text","text":"{\"branch\":\"pt-atom\",\"learnings\":[{\"e\":1},{\"e\":2}]}"}}'
  ( printf '%s' "$agent_payload" | _posttooluse ) >/dev/null 2>&1
  local wf_payload='{"session_id":"'"$sid"'","tool_name":"Workflow","tool_response":[{"atom":"a1","impl":{"branch":"wf-b1","learnings":[{"w":1}]},"verdict":{"verdict":"PASS"}},{"atom":"a2","impl":{"branch":"wf-b2","learnings":[{"w":2}]}}]}'
  ( printf '%s' "$wf_payload" | _posttooluse ) >/dev/null 2>&1
  # realistic content-block-ARRAY shape: tool_response = [{"type":"text","text":"<json>"}]
  local cb_payload='{"session_id":"'"$sid"'","tool_name":"Agent","tool_response":[{"type":"text","text":"{\"branch\":\"cb-atom\",\"learnings\":[{\"c\":1}]}"}]}'
  ( printf '%s' "$cb_payload" | _posttooluse ) >/dev/null 2>&1
  # IDEMPOTENCY: the SAME worker captured by BOTH PostToolUse(Agent) and PostToolUse(Workflow) (the
  # nested-fan-out overlap) must land ONCE — content-addressed filename dedups it.
  # Same logical record reaches the two hooks with DIFFERENT key ORDER (Agent serializer vs the
  # Workflow re-serialization) — canonicalization must still dedup it to ONE file/record.
  local idem_agent='{"session_id":"'"$sid"'","tool_name":"Agent","tool_output":{"type":"text","text":"{\"branch\":\"idem\",\"learnings\":[{\"d\":1,\"x\":2}]}"}}'
  local idem_wf='{"session_id":"'"$sid"'","tool_name":"Workflow","tool_response":[{"atom":"x","impl":{"branch":"idem","learnings":[{"x":2,"d":1}]}}]}'
  ( printf '%s' "$idem_agent" | _posttooluse ) >/dev/null 2>&1
  ( printf '%s' "$idem_wf" | _posttooluse ) >/dev/null 2>&1
  local pt_agent pt_wf1 pt_wf2 pt_cb idem_files idem_recs
  pt_agent="$(_recs "${sid}__pt-atom__*.jsonl")"; pt_wf1="$(_files "${sid}__wf-b1__*.jsonl")"; pt_wf2="$(_files "${sid}__wf-b2__*.jsonl")"; pt_cb="$(_files "${sid}__cb-atom__*.jsonl")"
  idem_files="$(_files "${sid}__idem__*.jsonl")"; idem_recs="$(_recs "${sid}__idem__*.jsonl")"
  if [ "$pt_agent" -eq 2 ] && [ "$pt_wf1" -ge 1 ] && [ "$pt_wf2" -ge 1 ] && [ "$pt_cb" -ge 1 ] && [ "$idem_files" -eq 1 ] && [ "$idem_recs" -eq 1 ]; then
    _emit "AC-LEARN-5 enforced-posttooluse-capture" 0 "Agent {type,text}→2; Workflow array→2; content-block-array→captured; both-hooks-same-worker→1 file/1 record (idempotent)"
  else _emit "AC-LEARN-5 enforced-posttooluse-capture" 1 "agent=$pt_agent wf1=$pt_wf1 wf2=$pt_wf2 cb=$pt_cb idem_files=$idem_files idem_recs=$idem_recs"; fi

  rm -rf "$tmp" 2>/dev/null || true
  echo ""
  if [ "$fails" -eq 0 ]; then echo "FOUNDRY-HARVEST-SELFTEST-GREEN"; return 0; else echo "FOUNDRY-HARVEST-SELFTEST-RED"; return 1; fi
}

case "${1:-}" in
  capture-return) _capture_return "${2:-}" "${3:-}"; exit 0 ;;
  posttooluse)    _posttooluse ;;
  harvest)        _harvest_one "${2:-}" "${3:-}"; exit 0 ;;
  pretooluse)     _pretooluse ;;
  --selftest)     _selftest; exit $? ;;
  *) echo "usage: $0 capture-return [<sid>] [<task>] | posttooluse | harvest <worktree> [<sid>] | pretooluse | --selftest" >&2; exit 2 ;;
esac
