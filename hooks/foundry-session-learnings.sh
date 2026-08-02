#!/usr/bin/env bash
# foundry-session-learnings — the DIRECT/lean interactive-session learnings PRODUCER
# (feat-foundry-session-learnings-capture). The third producer alongside the worker structured-return
# + worktree-sidecar producers (foundry-harvest-learnings.sh), for the plain operator-driven session
# that returns no value, spawns no worker, and leaves no worktree.
#
# Shape (A-only; the post-hoc fallback fork C was dropped after the §8 audit): an ENFORCED, once-per-
# session, re-entrancy-guarded `Stop` hook injects a single reflection turn; the model distills the
# session and emits records via the `capture` CLI; both go through the SHARED writer (content-addressed,
# idempotent, fail-open) + loss-log + mechanical secret-scrub. Records UNVALIDATED (/foundry:learn-distill
# is the schema authority). Channel `session`, task-id `session`.
#
# Subcommands:
#   foundry-session-learnings.sh stop                       # hooks.json Stop entry; reads payload on stdin
#   foundry-session-learnings.sh capture --session-id <id>  # the model runs this; records as JSONL on STDIN
#   foundry-session-learnings.sh --selftest
set -uo pipefail   # fail-open: never abort/wedge the operator's session

_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo .)"
# shellcheck source=foundry-learnings-lib.sh
. "$_LIB_DIR/foundry-learnings-lib.sh"

# Resolve the absolute path of THIS script for the injected reflection command (prefer the plugin root).
_self_path() {
  if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -f "$CLAUDE_PLUGIN_ROOT/hooks/foundry-session-learnings.sh" ]; then
    printf '%s/hooks/foundry-session-learnings.sh' "$CLAUDE_PLUGIN_ROOT"
  else
    printf '%s/foundry-session-learnings.sh' "$_LIB_DIR"
  fi
}

# The reflection prompt injected as the Stop block-decision `reason`. Embeds the resolved session_id +
# the EXACT command (records on STDIN via a temp file — NEVER on argv, so a secret-bearing record is not
# exposed in the process list before the write-path scrub). Instructs zero-records-is-fine + no secrets.
_reflection_prompt() {  # sid
  # SANITIZE the sid before it is interpolated into a command the prompt tells the model to "run
  # exactly", and QUOTE it in the emitted text (security review R-4). The value arrives from the hook
  # payload; a metacharacter- or instruction-shaped session_id would otherwise ride into a command the
  # model is directed to execute. Pre-existing, but this atom moves the body to additionalContext, which
  # takes delivery to the model from "maybe" to "always" — so it is closed here rather than inherited.
  local sid root self; sid="$(_sanitize "$1")"; self="$(_self_path)"
  # Resolve the buffer root to an ABSOLUTE path at emit time (the HOOK process DOES carry
  # CLAUDE_PROJECT_DIR; else the shared resolution-only walk-up; else _foundry_root, which always
  # returns something). Thread the RESOLVED absolute path into BOTH printed branches — NEVER the literal
  # "$CLAUDE_PROJECT_DIR", which expands EMPTY in the agent's Bash-tool shell (the FF-14 silent-loss bug:
  # CLAUDE_PROJECT_DIR is injected into HOOK envs only, not the Bash-tool shell where the agent runs the
  # printed command). AC-LCPR-2: primary `--project-dir <abs>`, fallback env-prefix `CLAUDE_PROJECT_DIR=<abs>`.
  root="${CLAUDE_PROJECT_DIR:-}"
  [ -n "$root" ] || root="$(_resolve_buffer_root_walkup "$PWD" 2>/dev/null || true)"
  [ -n "$root" ] || root="$(_foundry_root)"
  cat <<EOF
Reflect once on this session and capture durable LEARNINGS for the Foundry self-improvement
corpus (consumed by /foundry:learn-distill). Judge what is genuinely worth keeping —
reusable lessons, gotchas, decisions and their rationale; emitting ZERO records is fine for a low-value
session. Do NOT include secrets, credentials, tokens, or raw PII.

Steps: (1) write your records as JSONL (one compact JSON object per line) to a temp file, e.g.
/tmp/foundry-learnings.\$\$.jsonl; (2) run exactly (prefers the bare PATH command, else the absolute
script path so capture still works in the post-update window):
    if command -v foundry-learn-capture >/dev/null 2>&1; then cat /tmp/foundry-learnings.\$\$.jsonl | foundry-learn-capture --final --session-id "$sid" --project-dir "$root"; else cat /tmp/foundry-learnings.\$\$.jsonl | CLAUDE_PROJECT_DIR="$root" "$self" capture --session-id "$sid"; fi
Pass records on STDIN only (never on the command line). Emit records ONLY through this capture CLI —
never write a file directly under .foundry/session-learnings/ (a hand-written file the distiller
cannot read is silently lost). This reflection runs once per session, at the first qualifying idle.
EOF
}

# ------------------------- the HUMAN channel (feat-…-reflection-ux) ------------------------- #
# The `reason` of the Stop block decision is USER-FACING, and Claude Code captions EVERY Stop block as an
# "error" (upstream anthropics/claude-code #12667 / #34600 / #62139 — not ours to fix). So the reason is a
# SHORT (<=2 line) line whose first words contradict that caption, and the model-facing runbook goes to
# hookSpecificOutput.additionalContext instead (AC-RUX-1). It MUST stay a self-sufficient pointer to
# /foundry:learn-capture so a harness that drops additionalContext degrades to a MISSED capture, never an
# uninstructed session (R4). No shell command, no filesystem path, no session id in here.
_short_reason() {
  cat <<'EOF'
Foundry: routine learnings capture, once per session — this is not an error (the harness captions every Stop hook that way).
Reflect briefly, then capture via /foundry:learn-capture. Turn this off with FOUNDRY_SESSION_LEARNINGS=off.
EOF
}

# ------------------------- the opt-out knob (AC-RUX-3) ------------------------- #
# Claude Code has NO per-hook disable — only `disableAllHooks` (which would also kill git-discipline +
# cwd-enforce) or uninstalling the plugin. So Foundry owns this knob. Exactly three honored values;
# ANY other value degrades to the default `gated` (never an error, never a wedge).
_knob() {
  case "${FOUNDRY_SESSION_LEARNINGS:-}" in
    off)  printf 'off' ;;
    full) printf 'full' ;;
    *)    printf 'gated' ;;
  esac
}

# ------------------------- the substance gate (AC-RUX-2) ------------------------- #
# Classify the session from the harness-supplied transcript (read-only). SUBSTANTIVE if ANY of:
#   (a) a mutation tool_use  (Edit / Write / MultiEdit / NotebookEdit)
#   (b) a delegation tool_use (Agent / Task / Workflow)
#   (c) genuine user turns >= $FOUNDRY_SESSION_LEARNINGS_MIN_TURNS (default 3)
# CONSERVATIVE IN EXACTLY ONE DIRECTION: an absent / unreadable / unparseable transcript is treated as
# SUBSTANTIVE (inject). The gate may therefore only ever SUPPRESS on positive proof of insubstantiality —
# a read failure can never silently disable capture. `Bash` is deliberately NOT a signal (classifying it
# would mean parsing arbitrary shell — fragile and gameable both ways); a real Bash session reaches limb (c).
_session_is_substantive() {  # transcript_path -> prints substantive|insubstantial
  local tp="${1:-}" minturns="${FOUNDRY_SESSION_LEARNINGS_MIN_TURNS:-3}"
  case "$minturns" in ''|*[!0-9]*) minturns=3 ;; esac
  # `-f` (REGULAR file) not just `-r`: a FIFO / character device (/dev/zero) / directory would satisfy
  # `-r` and then never EOF, stalling the idle until the harness timeout (security review R-2).
  [ -n "$tp" ] && [ -f "$tp" ] && [ -r "$tp" ] || { printf 'substantive'; return 0; }
  _FOUNDRY_TP="$tp" _FOUNDRY_MINTURNS="$minturns" python3 -c '
import os, sys, json
MUT = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
DEL = {"Agent", "Task", "Workflow"}
KNOWN = ("user", "assistant")     # the discriminator values this classifier actually understands
# feat-foundry-learnings-substance-gate-synthetic-turns (AC-SYNT-1): the harness writes local
# slash-command records (the invocation, its stdout, and the isMeta caveat wrapper) into the
# transcript as type:"user" WITHOUT isMeta. A leading-tag match on one of these is POSITIVE
# synthetic proof (excludes); origin.kind=="human" or a promptSource field is POSITIVE
# structural human proof (dominates, even over a leading tag, since it is immune to a tag
# rename); an entry with neither signal counts, preserving the AC-RUX-2 fail-toward-inject
# direction. The tag match is a LEADING match only (stripped-whitespace startswith), never a
# substring test, and in the list content shape looks at the FIRST text block only.
SYNTHETIC_TAGS = ("<command-name>", "<local-command-stdout>", "<local-command-caveat>",
                  "<command-message>", "<command-args>")
def _leading_synthetic_tag(text):
    s = text.lstrip() if isinstance(text, str) else ""
    return any(s.startswith(tag) for tag in SYNTHETIC_TAGS)
MAX_BYTES = 64 * 1024 * 1024      # whole-file budget; past it we cannot cheaply classify -> inject
MAX_LINES = 200000                # line budget; ditto
MAX_LINE  = 1024 * 1024           # a single line past this is a giant tool_result dump, never signal
minturns = int(os.environ.get("_FOUNDRY_MINTURNS", "3") or 3)
turns = 0
parsed = 0                        # lines RECOGNISED as this schema (not merely dict-shaped)
try:
    seen_bytes = 0
    seen_lines = 0
    with open(os.environ["_FOUNDRY_TP"], "r", errors="replace") as fh:
        for line in fh:
            seen_bytes += len(line)
            seen_lines += 1
            if seen_bytes > MAX_BYTES or seen_lines > MAX_LINES:
                print("substantive"); sys.exit(0)   # over budget -> fail toward injecting
            if len(line) > MAX_LINE:
                continue
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue          # a single bad line is skipped, not fatal
            if not isinstance(e, dict):
                continue
            t = e.get("type")
            msg = e.get("message") if isinstance(e.get("message"), dict) else {}
            content = msg.get("content")
            # Count a line as PARSED only when it is RECOGNISED — a known discriminator AND a
            # content shape we can read. Counting every dict (the pre-review behaviour) meant a
            # renamed/renested schema would yield parsed>0, turns=0 -> `insubstantial` for EVERY
            # session on EVERY machine, silently ending capture with no error and no log row
            # (security review R-1). Recognition-based counting routes that to `substantive`.
            if t in KNOWN and isinstance(content, (str, list)):
                parsed += 1
            if t == "assistant" and isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        if b.get("name") in MUT or b.get("name") in DEL:
                            print("substantive"); sys.exit(0)
            elif t == "user":
                if e.get("isMeta"):
                    continue      # hook-injected / system-synthesised turn, not a genuine user turn
                origin = e.get("origin")
                human_proof = (isinstance(origin, dict) and origin.get("kind") == "human") \
                    or ("promptSource" in e)   # structural proof DOMINATES a leading tag match
                if isinstance(content, str):
                    if not human_proof and _leading_synthetic_tag(content):
                        continue  # positive synthetic proof (leading-tag match) -> not a user turn
                    if content.strip():
                        turns += 1
                elif isinstance(content, list):
                    if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                        continue  # a tool-result continuation is not a user turn
                    text_blocks = [b.get("text", "") for b in content
                                   if isinstance(b, dict) and b.get("type") == "text"]
                    if not human_proof and text_blocks and _leading_synthetic_tag(text_blocks[0]):
                        continue  # FIRST text block only is the anchor; a later block never excludes
                    if any(str(tb).strip() for tb in text_blocks):
                        turns += 1
    # UNRECOGNISED is absence-of-evidence, NOT evidence of insubstantiality: if not one line was
    # understood, the shape is unrecognised (a corrupt file, a wrong-but-valid JSONL file, or a harness
    # transcript-schema change — residual R3) and the gate MUST fail toward injecting rather than read
    # "no signal" as "no work".
    if parsed == 0:
        print("substantive")
    else:
        print("substantive" if turns >= minturns else "insubstantial")
except Exception:
    print("substantive")          # fail TOWARD injecting — never silently disable capture
' 2>/dev/null || printf 'substantive'
}

# --------------------------------- the Stop hook --------------------------------- #
# ENFORCED once-per-session reflection. Guards (ALL must pass to inject), cheapest+most decisive first:
#   (a) stop_hook_active != true   — the documented re-entrancy contract (never loop)
#   (b) CLAUDE_CODE_ENTRYPOINT == cli — interactive only; absent/ambiguous → fail toward NO-OP
#   (d) FOUNDRY_SESSION_LEARNINGS != off — the operator opt-out (AC-RUX-3), BEFORE any transcript I/O
#   (c) per-session marker absent  — once per session
#   (e) the session is SUBSTANTIVE unless the knob is `full` (AC-RUX-2) — placed AFTER (c) so a
#       requested/done session never pays for a transcript parse, and BEFORE _set_marker so a
#       SUPPRESSED session does not consume its once-per-session budget.
# On inject: set marker `requested`, print the short human `reason` + the runbook in
# hookSpecificOutput.additionalContext (AC-RUX-1). ALWAYS exit 0.
_stop() {
  local payload sid stop_active entrypoint state prompt tpath knob
  payload="$(cat 2>/dev/null || true)"
  sid="$(printf '%s' "$payload" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("session_id",""))
except Exception: print("")' 2>/dev/null || true)"
  [ -n "$sid" ] || sid="${CLAUDE_SESSION_ID:-unknown}"
  stop_active="$(printf '%s' "$payload" | python3 -c 'import sys,json
try: print(str(json.load(sys.stdin).get("stop_hook_active", False)))
except Exception: print("")' 2>/dev/null || true)"
  # (a) re-entrancy: the injected reflection turn itself triggers Stop again → never re-inject.
  case "$stop_active" in [Tt]rue|1) exit 0 ;; esac
  # (b) interactive-only: inject ONLY for a recognized interactive entrypoint; else fail to no-op.
  case "${CLAUDE_CODE_ENTRYPOINT:-}" in
    cli) : ;;
    *) exit 0 ;;
  esac
  # (d) operator opt-out (AC-RUX-3). `off` → silent no-op, and NO marker is written (so flipping the knob
  # back on mid-session leaves the session's reflection budget intact). Evaluated before any transcript I/O.
  knob="$(_knob)"
  [ "$knob" = "off" ] && exit 0
  # (c) once-per-session marker — EXACT-match inject ALLOWLIST (AC-COMPACT-2.1): inject iff the state is
  # the empty string (absent/empty file) or exactly `rearmed` (a manual-compaction re-arm); no-op for
  # `requested`/`done` AND any other/garbage/forged value (hardening vs the prior inject-unless-glob
  # fall-through). `$(...)` already strips trailing newlines, and _set_marker writes the bare token.
  state="$(_marker_state "$sid")"
  case "$state" in ''|rearmed) : ;; *) exit 0 ;; esac
  # (e) substance gate (AC-RUX-2) — skipped entirely under `full` (the pre-overhaul cadence). Suppression
  # happens BEFORE _set_marker, so an insubstantial idle leaves the marker untouched and a later
  # substantive idle in the same session still reflects.
  if [ "$knob" != "full" ]; then
    tpath="$(printf '%s' "$payload" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("transcript_path",""))
except Exception: print("")' 2>/dev/null || true)"
    if [ "$(_session_is_substantive "$tpath")" = "insubstantial" ]; then
      # Content-free observability (security review R-3). Without a row, an operator cannot tell
      # "the gate correctly suppressed 40 idle sessions" from "the gate has been broken for weeks" —
      # the corpus just quietly stops growing. Logs the OUTCOME only: no path, no transcript content.
      _log_attempt session session 0 "suppressed:insubstantial"
      exit 0
    fi
  fi
  # All guards pass → mark requested (fail-open: on marker failure, allow the stop, no inject) + inject.
  _set_marker "$sid" requested || exit 0
  prompt="$(_reflection_prompt "$sid")"
  # AC-RUX-1: the runbook is RELOCATED (not shortened) into the model-facing additionalContext; the
  # user-facing reason is the short non-alarming line. Values are passed via the ENV, never argv, so a
  # session id / path never lands in the process list.
  _FOUNDRY_REASON="$(_short_reason)" _FOUNDRY_CTX="$prompt" python3 -c 'import os,json
print(json.dumps({"decision":"block",
                  "reason":os.environ.get("_FOUNDRY_REASON","").strip(),
                  "hookSpecificOutput":{"hookEventName":"Stop",
                                        "additionalContext":os.environ.get("_FOUNDRY_CTX","")}}))' 2>/dev/null \
    || printf '{"decision":"block","reason":"Foundry: routine learnings capture, once per session (not an error). Capture via /foundry:learn-capture; disable with FOUNDRY_SESSION_LEARNINGS=off."}'
  exit 0
}

# NOTE: _cas_done is now RELOCATED into hooks/foundry-learnings-lib.sh (AC-PUBCAP-5, single
# implementation) — it is sourced from there, not defined here.

# --------------------------------- the capture CLI --------------------------------- #
# The model runs this during the injected reflection turn — the TRUSTED reflection-turn closer
# (= bin --final semantics). Records as JSONL on STDIN. Ingest (canonicalize + reject + write) is the
# SHARED _ingest_records (channel `session`, task `session`); then the marker tail flips the marker to
# `done` via the relocated compare-and-set. ALWAYS exit 0 (fail-open). KEEPS its current absent→done
# behavior for a RESOLVED sid (COMPACT-2 CABS asserts: standalone capture with an explicit
# --session-id and an absent marker → marker `done`); the bin's stricter absent-refusal is NOT added
# here. Only the literal-`unknown` default flip is dropped when the sid is UNRESOLVED (the existing
# SLEARN/COMPACT callers always pass an explicit sid, so this changes none of their asserted behavior).
_capture() {
  local sid=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --session-id) sid="${2:-}"; shift 2 || shift ;;
      --session-id=*) sid="${1#*=}"; shift ;;
      *) shift ;;
    esac
  done
  [ -n "$sid" ] || sid="${CLAUDE_SESSION_ID:-}"
  local resolved=1; [ -n "$sid" ] || resolved=0
  sid="$(_sanitize "$sid")"
  _ingest_records "$sid" session session
  # Marker tail (this is the --final closer). Drop the legacy literal-`unknown` flip for an UNRESOLVED
  # sid (AC-PUBCAP-3.2); for a RESOLVED sid keep the absent→done behavior COMPACT-2 CABS asserts.
  # DELIBERATE ASYMMETRY vs the PUBLIC bin/foundry-learn-capture --final, which refuses absent→done
  # (requested-only). This trusted reflection closer legitimately flips its own resolved-sid absent
  # marker → done; same shared _cas_done, looser guard. Do NOT "unify" the two call sites.
  if [ "$resolved" -eq 1 ]; then
    if [ "${_INGEST_LOSS:-0}" -ne 0 ]; then
      # AC-CLO-4 (ER #70): a write loss must NOT consume the once-per-session marker — leave it
      # un-flipped so a retry can still capture, and say so loudly (content-free).
      echo "foundry-session-learnings: WRITE LOSS — once-per-session marker NOT flipped; fix the write path and re-run the capture" >&2
    else
      _cas_done "$sid"
    fi
  fi
  _prune_markers
  exit 0
}

# --------------------------------- the PreCompact re-arm --------------------------------- #
# Re-arm the once-per-session reflection at a MANUAL compaction boundary (AC-COMPACT-1) so the existing
# Stop reflection fires once per compaction SEGMENT instead of once per session. Sets the marker to
# `rearmed` (the Stop allowlist treats it as inject-eligible) ONLY when ALL hold: reason=="manual"
# (auto-compaction does NOT re-arm — no system-triggered amplification), interactive (the same exact-`cli`
# probe the Stop hook uses), and the session id RESOLVES to a real value (else SKIP — never key an
# `unknown` marker the Stop read can't match). NEVER blocks compaction, NEVER injects, NEVER emits a
# decision; ALWAYS exit 0 (fail-open). Re-arm is idempotent (rearmed→rearmed is a no-op).
_precompact() {
  local payload sid reason
  payload="$(cat 2>/dev/null || true)"
  # (a) manual-only.
  reason="$(printf '%s' "$payload" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("reason",""))
except Exception: print("")' 2>/dev/null || true)"
  [ "$reason" = "manual" ] || exit 0
  # (b) interactive-only (same exact-`cli` check as _stop).
  case "${CLAUDE_CODE_ENTRYPOINT:-}" in cli) : ;; *) exit 0 ;; esac
  # (c) resolved session id: payload session_id → $CLAUDE_SESSION_ID → UNRESOLVED → skip (no `unknown` key).
  sid="$(printf '%s' "$payload" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("session_id",""))
except Exception: print("")' 2>/dev/null || true)"
  [ -n "$sid" ] || sid="${CLAUDE_SESSION_ID:-}"
  [ -n "$sid" ] || exit 0
  _set_marker "$sid" rearmed || true   # idempotent; fail-open (never blocks compaction)
  _prune_markers
  exit 0
}

# ----------------------------------- self-test ----------------------------------- #
_selftest() {
  local tmp fails=0 date_part sid="ssid"
  # AC-DSST-1 (ER #76/#64): scratch precedence — mktemp -d ($TMPDIR) → $CLAUDE_PROJECT_DIR/.foundry/tmp
  # → SELFTEST-INCONCLUSIVE + exit 75. Never proceed with an empty $tmp (the "$tmp/proj" → mkdir /proj
  # absolute-root fall-through under an OS sandbox). Reads the ORIGINAL CLAUDE_PROJECT_DIR before the
  # isolated re-export below.
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/foundry-selftest.XXXXXX" 2>/dev/null)" || tmp=""
  if [ -z "$tmp" ] && [ -n "${CLAUDE_PROJECT_DIR:-}" ]; then
    mkdir -p "${CLAUDE_PROJECT_DIR}/.foundry/tmp" 2>/dev/null || true
    tmp="$(mktemp -d "${CLAUDE_PROJECT_DIR}/.foundry/tmp/selftest.XXXXXX" 2>/dev/null)" || tmp=""
  fi
  if [ -z "$tmp" ]; then
    echo "SELFTEST-INCONCLUSIVE: filesystem sandbox denied scratch creation (mkdtemp in \$TMPDIR and the .foundry/tmp fallback); re-run with write access or sandbox-bypassed"
    return 75
  fi
  # Point every downstream mktemp at the RESOLVED scratch (more hermetic; under a denied $TMPDIR the
  # internal ingest/marker mktemps would otherwise still fail). Selftest-scoped only.
  mkdir -p "$tmp/tmp" 2>/dev/null && export TMPDIR="$tmp/tmp" || true
  date_part="$(date -u +%F)"
  local proj="$tmp/proj"; mkdir -p "$proj/.foundry"; export CLAUDE_PROJECT_DIR="$proj"
  _emit() { if [ "$2" -eq 0 ]; then echo "$1: PASS${3:+ — $3}"; else echo "$1: FAIL${3:+ — $3}"; fails=$((fails+1)); fi; }
  _files() { find "$proj/.foundry/session-learnings/$date_part" -name "$1" 2>/dev/null | wc -l | tr -d ' '; }
  _recs()  { cat $(find "$proj/.foundry/session-learnings/$date_part" -name "$1" 2>/dev/null) 2>/dev/null | grep -c . || echo 0; }
  echo "foundry-session-learnings self-test:"

  # ---- AC-SLEARN-1: enforced once-per-session reflection, re-entrancy + interactive + marker guarded.
  # inject path: interactive, no stop_hook_active, marker absent → block decision emitted + marker=requested.
  local out_inject out_reentr out_marker out_headless r1=0
  out_inject="$(CLAUDE_CODE_ENTRYPOINT=cli; export CLAUDE_CODE_ENTRYPOINT; printf '%s' "{\"session_id\":\"$sid\",\"stop_hook_active\":false}" | _stop 2>/dev/null)"
  printf '%s' "$out_inject" | grep -q '"decision"[[:space:]]*:[[:space:]]*"block"' || r1=1
  [ "$(_marker_state "$sid")" = "requested" ] || r1=1
  # re-entrancy guard: stop_hook_active=true → NO inject (anti-tautology — must NOT block)
  out_reentr="$(CLAUDE_CODE_ENTRYPOINT=cli; export CLAUDE_CODE_ENTRYPOINT; printf '%s' "{\"session_id\":\"other\",\"stop_hook_active\":true}" | _stop 2>/dev/null)"
  printf '%s' "$out_reentr" | grep -q '"decision"' && r1=1
  # marker-present guard: a second idle for the SAME session → no re-inject
  out_marker="$(CLAUDE_CODE_ENTRYPOINT=cli; export CLAUDE_CODE_ENTRYPOINT; printf '%s' "{\"session_id\":\"$sid\",\"stop_hook_active\":false}" | _stop 2>/dev/null)"
  printf '%s' "$out_marker" | grep -q '"decision"' && r1=1
  # interactive-only guard: non-cli entrypoint → no inject (fail to no-op)
  out_headless="$(CLAUDE_CODE_ENTRYPOINT=headless; export CLAUDE_CODE_ENTRYPOINT; printf '%s' "{\"session_id\":\"fresh\",\"stop_hook_active\":false}" | _stop 2>/dev/null)"
  printf '%s' "$out_headless" | grep -q '"decision"' && r1=1
  [ -z "$(_marker_state fresh)" ] || r1=1
  _emit "AC-SLEARN-1 enforced-once-per-session-reflection-reentrancy-guarded" "$r1" "inject@cli+marker; reentrancy/marker/headless→no-inject"

  # ---- AC-SLEARN-2: pinned capture CLI; session_id threaded; idempotent content-addressed write.
  local r2=0
  printf '%s\n' '{"l":"alpha"}' '{"l":"beta"}' | _capture --session-id "S1" >/dev/null 2>&1
  [ "$(_files 'S1__session__*.jsonl')" -ge 1 ] || r2=1
  [ "$(_recs 'S1__session__*.jsonl')" -eq 2 ] || r2=1
  [ "$(_marker_state S1)" = "done" ] || r2=1
  # idempotency: same records again → still 1 file / 2 records (content-hash dedup)
  printf '%s\n' '{"l":"alpha"}' '{"l":"beta"}' | _capture --session-id "S1" >/dev/null 2>&1
  [ "$(_files 'S1__session__*.jsonl')" -eq 1 ] || r2=1
  [ "$(_recs 'S1__session__*.jsonl')" -eq 2 ] || r2=1
  _emit "AC-SLEARN-2 pinned-capture-cli-session-id-threaded" "$r2" "2 recs keyed by S1; marker=done; re-capture idempotent (1 file)"

  # ---- AC-SLEARN-3: marker OUTSIDE the consumed corpus; pruner sweeps >90d; resolver shared.
  local r3=0 mk
  mk="$(_marker_path S1)"
  case "$mk" in *"/.foundry/session-markers/"*) : ;; *) r3=1 ;; esac      # outside session-learnings/
  case "$mk" in *"/session-learnings/"*) r3=1 ;; esac                     # must NOT be in the glob root
  [ -f "$mk" ] || r3=1
  # prune: an old marker (mtime 100d) is swept; a fresh one survives
  local oldmk; oldmk="$(_marker_dir)/stale"; printf 'done' > "$oldmk"; touch -t "$(date -u -v-100d +%Y%m%d%H%M 2>/dev/null || date -u -d '100 days ago' +%Y%m%d%H%M)" "$oldmk" 2>/dev/null
  _prune_markers
  [ ! -f "$oldmk" ] || r3=1
  [ -f "$mk" ] || r3=1
  _emit "AC-SLEARN-3 marker-specified-outside-corpus" "$r3" "marker under .foundry/session-markers/ (not the corpus); >90d pruned, fresh kept"

  # ---- AC-SLEARN-4: fail-open + reconciled loss-log; zero-record = ok records:0 (not fail-open).
  local r4=0 log="$proj/.foundry/session-learnings/.harvest-log.jsonl"
  printf '' | _capture --session-id "ZERO" >/dev/null 2>&1            # zero records
  grep -q '"channel":"session".*"records":0,"outcome":"ok"' "$log" 2>/dev/null || r4=1
  # fail-open: unwritable buffer root → capture rc 0 + a loss row (stderr fallback acceptable)
  local roproj="$tmp/ro"; mkdir -p "$roproj/.foundry/session-learnings"; chmod 0500 "$roproj/.foundry/session-learnings"
  printf '%s\n' '{"l":"x"}' | ( export CLAUDE_PROJECT_DIR="$roproj"; _capture --session-id "RO" ) >/dev/null 2>&1; local rc4=$?
  chmod 0700 "$roproj/.foundry/session-learnings" 2>/dev/null || true
  [ "$rc4" -eq 0 ] || r4=1
  _emit "AC-SLEARN-4 fail-open-loss-logged" "$r4" "zero→ok records:0; unwritable buffer→rc0"

  # ---- AC-SLEARN-5: single producer path wired (hooks.json Stop -> this script).
  # scripts/foundry-doctor.py is a thin probe (manifest/hooks/skills-frontmatter/
  # stack-profile-lock/operator-registry only) and no longer carries a learnings-harvest check of
  # its own — that behavioral coverage lives in tests/test_learnings.py now. This control's
  # remaining, still-live concern is the hooks.json wiring itself.
  local r5=0 hj="$_LIB_DIR/hooks.json"
  [ -f "$hj" ] || hj="$_LIB_DIR/hooks.json"
  python3 -c 'import json,sys
try: wj=json.load(open(sys.argv[1])).get("hooks",{})
except Exception: sys.exit(1)
cmds=" ".join(h.get("command","") for arr in wj.values() for e in arr for h in e.get("hooks",[]))
sys.exit(0 if ("Stop" in wj and "foundry-session-learnings.sh stop" in cmds) else 1)' "$hj" 2>/dev/null || r5=1
  _emit "AC-SLEARN-5 single-producer-wired" "$r5" "hooks.json Stop→session-learnings"

  # ---- AC-SLEARN-7: MECHANICAL secret-scrub on the write path (selective, count-stable).
  local r7=0 secret='AKIAIOSFODNN7EXAMPLE'
  printf '%s\n' "{\"note\":\"key is $secret here\",\"keep\":\"plain-value-ok\"}" | _capture --session-id "SEC" >/dev/null 2>&1
  local secfile; secfile="$(find "$proj/.foundry/session-learnings/$date_part" -name 'SEC__session__*.jsonl' 2>/dev/null | head -1)"
  [ -n "$secfile" ] || r7=1
  if [ -n "$secfile" ]; then
    grep -q "$secret" "$secfile" 2>/dev/null && r7=1                  # secret MUST be gone
    grep -q 'REDACTED-AWS-KEY' "$secfile" 2>/dev/null || r7=1         # redaction marker present
    grep -q 'plain-value-ok' "$secfile" 2>/dev/null || r7=1          # non-secret NOT touched (selective)
    [ "$(grep -c . "$secfile")" -eq 1 ] || r7=1                       # count stable
  fi
  _emit "AC-SLEARN-7 mechanical-secret-scrub" "$r7" "AKIA redacted; non-secret preserved; count stable"

  # ---- AC-SLEARN-8: the oracle is real (read-back) WITH anti-tautology cases that fail on known-bad.
  # (i) re-entrancy anti-tautology: prove a block UNDER stop_hook_active would be caught (it must be absent);
  # (ii) scrub anti-tautology: prove an UNSCRUBBED secret would be caught (the secret must be absent above).
  local r8=0
  printf '%s' "$out_reentr" | grep -q '"decision"' && r8=1            # known-bad: re-entrant inject → would FAIL
  if [ -n "${secfile:-}" ]; then grep -q "$secret" "$secfile" 2>/dev/null && r8=1; fi   # known-bad: secret survives → would FAIL
  # and confirm the GOOD path still produced real artifacts (not a vacuous all-skip)
  [ "$(_files 'S1__session__*.jsonl')" -ge 1 ] || r8=1
  _emit "AC-SLEARN-8 selftest-real-oracle-anti-tautology" "$r8" "anti-tautology: re-entrant-inject + unscrubbed-secret both caught; real read-back artifacts present"

  # ============================ feat-foundry-learn-capture-on-compact ============================
  # ---- AC-COMPACT-1: PreCompact re-arms ONLY on manual + interactive + resolved sid; else no-op.
  local rc1=0
  ( CLAUDE_CODE_ENTRYPOINT=cli; export CLAUDE_CODE_ENTRYPOINT; printf '%s' '{"session_id":"CMP","reason":"manual"}' | _precompact ) >/dev/null 2>&1
  [ "$(_marker_state CMP)" = "rearmed" ] || rc1=1                 # manual+cli+sid → rearmed
  ( CLAUDE_CODE_ENTRYPOINT=cli; export CLAUDE_CODE_ENTRYPOINT; printf '%s' '{"session_id":"CMPa","reason":"auto"}' | _precompact ) >/dev/null 2>&1
  [ -z "$(_marker_state CMPa)" ] || rc1=1                         # auto → NO re-arm
  ( CLAUDE_CODE_ENTRYPOINT=headless; export CLAUDE_CODE_ENTRYPOINT; printf '%s' '{"session_id":"CMPh","reason":"manual"}' | _precompact ) >/dev/null 2>&1
  [ -z "$(_marker_state CMPh)" ] || rc1=1                         # non-interactive → NO re-arm
  ( CLAUDE_CODE_ENTRYPOINT=cli; export CLAUDE_CODE_ENTRYPOINT; unset CLAUDE_SESSION_ID; printf '%s' '{"reason":"manual"}' | _precompact ) >/dev/null 2>&1
  [ -z "$(_marker_state unknown)" ] || rc1=1                      # unresolved sid → no `unknown` marker
  _emit "AC-COMPACT-1 precompact-rearms-only-manual-interactive-identified" "$rc1" "manual+cli+sid→rearmed; auto/headless/unresolved→no marker"

  # ---- AC-COMPACT-2: race-safe exact-match allowlist + compare-and-set capture.
  local rc2=0 out_ra out_gb
  _set_marker "RA" rearmed
  out_ra="$(CLAUDE_CODE_ENTRYPOINT=cli; export CLAUDE_CODE_ENTRYPOINT; printf '%s' '{"session_id":"RA","stop_hook_active":false}' | _stop 2>/dev/null)"
  printf '%s' "$out_ra" | grep -q '"decision"[[:space:]]*:[[:space:]]*"block"' || rc2=1   # rearmed → inject
  [ "$(_marker_state RA)" = "requested" ] || rc2=1                                        # → requested
  _set_marker "GB" "garbage-xyz"
  out_gb="$(CLAUDE_CODE_ENTRYPOINT=cli; export CLAUDE_CODE_ENTRYPOINT; printf '%s' '{"session_id":"GB","stop_hook_active":false}' | _stop 2>/dev/null)"
  printf '%s' "$out_gb" | grep -q '"decision"' && rc2=1                                   # garbage → NO inject (hardening)
  _set_marker "CAS" rearmed; printf '%s\n' '{"l":"x"}' | _capture --session-id "CAS" >/dev/null 2>&1
  [ "$(_marker_state CAS)" = "rearmed" ] || rc2=1                 # CAS preserves a raced-in rearmed
  [ "$(_files 'CAS__session__*.jsonl')" -ge 1 ] || rc2=1         # records still written
  _set_marker "CQ" requested; printf '%s\n' '{"l":"y"}' | _capture --session-id "CQ" >/dev/null 2>&1
  [ "$(_marker_state CQ)" = "done" ] || rc2=1                     # requested → done
  printf '%s\n' '{"l":"z"}' | _capture --session-id "CABS" >/dev/null 2>&1
  [ "$(_marker_state CABS)" = "done" ] || rc2=1                   # standalone (absent) → done
  _emit "AC-COMPACT-2 race-safe-allowlist-and-cas" "$rc2" "rearmed→inject→requested; garbage→no-inject; capture preserves rearmed, sets done from requested/absent"

  # ---- AC-COMPACT-3: precompact wired by SPECIFIC command token under a PreCompact event.
  local rc3=0 hj3="$_LIB_DIR/hooks.json"
  python3 -c 'import json,sys
try: wj=json.load(open(sys.argv[1])).get("hooks",{})
except Exception: sys.exit(1)
ev=wj.get("PreCompact",[])
ok=any("session-learnings.sh precompact" in h.get("command","") for e in ev for h in e.get("hooks",[]))
sys.exit(0 if ok else 1)' "$hj3" 2>/dev/null || rc3=1
  _emit "AC-COMPACT-3 precompact-wired-by-command-token" "$rc3" "hooks.json PreCompact event has a command containing session-learnings.sh precompact"

  # ---- AC-COMPACT-4: anti-tautology — done-without-precompact stays no-op; precompact never blocks; round-trip.
  local rc4=0 out_dwp out_pc rc_pc out_rt
  _set_marker "DWP" done
  out_dwp="$(CLAUDE_CODE_ENTRYPOINT=cli; export CLAUDE_CODE_ENTRYPOINT; printf '%s' '{"session_id":"DWP","stop_hook_active":false}' | _stop 2>/dev/null)"
  printf '%s' "$out_dwp" | grep -q '"decision"' && rc4=1         # known-bad: done w/o precompact re-injects → FAIL
  [ "$(_marker_state DWP)" = "done" ] || rc4=1                   # unchanged
  out_pc="$( CLAUDE_CODE_ENTRYPOINT=cli; export CLAUDE_CODE_ENTRYPOINT; printf '%s' '{"session_id":"PCD","reason":"manual"}' | _precompact 2>/dev/null )"; rc_pc=$?
  [ "$rc_pc" -eq 0 ] || rc4=1                                    # precompact exits 0 (never blocks)
  printf '%s' "$out_pc" | grep -q '"decision"' && rc4=1         # precompact emits NO decision
  _set_marker "RT" done
  ( CLAUDE_CODE_ENTRYPOINT=cli; export CLAUDE_CODE_ENTRYPOINT; printf '%s' '{"session_id":"RT","reason":"manual"}' | _precompact ) >/dev/null 2>&1
  [ "$(_marker_state RT)" = "rearmed" ] || rc4=1                 # done →(precompact)→ rearmed
  out_rt="$(CLAUDE_CODE_ENTRYPOINT=cli; export CLAUDE_CODE_ENTRYPOINT; printf '%s' '{"session_id":"RT","stop_hook_active":false}' | _stop 2>/dev/null)"
  printf '%s' "$out_rt" | grep -q '"decision"[[:space:]]*:[[:space:]]*"block"' || rc4=1   # rearmed →(Stop)→ inject
  [ "$(_marker_state RT)" = "requested" ] || rc4=1              # → requested
  _emit "AC-COMPACT-4 selftest-real-oracle-anti-tautology" "$rc4" "done-no-precompact→no-inject; precompact→exit0/no-decision; done→rearmed→requested round-trip"

  # ============================ feat-foundry-learnings-buffer-contract ============================
  local distill="$_LIB_DIR/../scripts/foundry-distill.py"
  local log="$proj/.foundry/session-learnings/.harvest-log.jsonl"
  _drift_total() { python3 "$distill" --drift-json --root "$proj" 2>/dev/null | python3 -c 'import sys,json;print(sum(json.load(sys.stdin)["counts"].values()))' 2>/dev/null || echo -1; }

  # ---- AC-LBC-1: readability == read_records admissibility. A zero-/low-token DICT is READABLE
  # (forwarded + admitted), NOT rejected and NOT drift — admitted-but-unpromoted is not loss.
  local rl1=0
  printf '%s\n' '{"x":"the and for it"}' | _capture --session-id "ZTOK" >/dev/null 2>&1   # stopword-only dict
  [ "$(_files 'ZTOK__session__*.jsonl')" -ge 1 ] || rl1=1                                  # forwarded (admitted)
  [ "$(_drift_total)" -eq 0 ] || rl1=1                                                      # admitted ⇒ not drift
  _emit "AC-LBC-1 readability-invariant-is-read-records-admissibility" "$rl1" "zero-token dict forwarded+admitted; whole-buffer scan_drift=0 (admitted-but-unpromoted != loss)"

  # ---- AC-LBC-2: producer reject-only + content-free + all-rejected != ok:0.
  local rl2=0 sek='AKIAIOSFODNN7EXAMPLE'
  printf '%s\n' 'totally-not-json' '[1,2]' '{"keepme":"alpha bravo charlie"}' | _capture --session-id "REJ" >/dev/null 2>&1
  [ "$(_recs 'REJ__session__*.jsonl')" -eq 1 ] || rl2=1                  # only the 1 dict forwarded (array+garbage dropped)
  grep -q '"records":0,"outcome":"reject:unparseable"' "$log" || rl2=1   # unparseable reason, records:0
  grep -q '"records":0,"outcome":"reject:non-object"' "$log" || rl2=1    # array → non-object reason
  printf '%s\n' "raw $sek leak not-json" | _capture --session-id "REJSEC" >/dev/null 2>&1   # secret in a REJECTED line
  grep -q "$sek" "$log" && rl2=1                                         # content-free: secret from a rejected line NEVER logged
  local ok0_b rj_b ok0_a rj_a
  ok0_b="$(grep -c '"records":0,"outcome":"ok"' "$log" 2>/dev/null || echo 0)"
  rj_b="$(grep -c '"outcome":"reject:' "$log" 2>/dev/null || echo 0)"
  printf '%s\n' 'x' '9' | _capture --session-id "ALLBAD" >/dev/null 2>&1 # all-rejected batch
  ok0_a="$(grep -c '"records":0,"outcome":"ok"' "$log" 2>/dev/null || echo 0)"
  rj_a="$(grep -c '"outcome":"reject:' "$log" 2>/dev/null || echo 0)"
  [ "$ok0_a" -eq "$ok0_b" ] || rl2=1                                     # all-rejected did NOT mask as ok records:0
  [ "$rj_a" -eq "$((rj_b+2))" ] || rl2=1                                 # +2 reject rows (unparseable + non-object)
  _emit "AC-LBC-2 producer-reject-only-content-free" "$rl2" "non-object/unparseable rejected (reason in outcome, records:0); dict forwarded; rejected-line secret NOT logged; all-rejected != ok:0"

  # ---- AC-LBC-3: CLI-only bypass-close sentinel present in BOTH the reflection prompt + learn-capture SKILL.
  local rl3=0 sentinel='never write a file directly under .foundry/session-learnings/'
  _reflection_prompt "S1" | grep -qF "$sentinel" || rl3=1
  grep -qF "$sentinel" "$_LIB_DIR/../skills/learn-capture/SKILL.md" 2>/dev/null || rl3=1
  _emit "AC-LBC-3 cli-only-write-path-bypass-closed" "$rl3" "verbatim sentinel in reflection prompt + learn-capture SKILL.md"

  # ---- AC-LBC-5: real read-back oracle + anti-tautology (the drift scan is not vacuous; a bypass IS caught).
  local rl5=0
  printf '%s\n' '{"bypass":"me"}' > "$proj/.foundry/session-learnings/bypass-stray.jsonl"   # hand-write skipping the CLI
  python3 "$distill" --drift-json --root "$proj" 2>/dev/null | python3 -c 'import sys,json
d=json.load(sys.stdin); sys.exit(0 if d["counts"]["unpartitioned"]>=1 and "bypass-stray.jsonl" in d["paths"]["unpartitioned"] else 1)' || rl5=1
  rm -f "$proj/.foundry/session-learnings/bypass-stray.jsonl"
  [ "$(_drift_total)" -eq 0 ] || rl5=1                                   # removed → back to 0 (partitioned records != drift)
  [ "$(_files 'REJ__session__*.jsonl')" -ge 1 ] || rl5=1                 # GOOD path produced real artifacts (not all-skip)
  _emit "AC-LBC-5 selftest-real-oracle-anti-tautology" "$rl5" "planted root bypass→unpartitioned drift caught (path read back); removed→0; real artifacts present"

  # ============================ feat-foundry-learn-capture-entrypoint ============================
  # ---- AC-PUBCAP-5: single-impl lib relocation + producer re-point regression (real read-back).
  local rp5=0 rprompt _cmdline _primary
  rprompt="$(_reflection_prompt "SOMESID")"
  # (a) the re-point happened: the prompt names the bare command, and the PRIMARY producer branch
  # carries no version-pinned /0.x literal. The abs-path FALLBACK legitimately embeds the install
  # dir (…/0.x/… on a versioned marketplace install) — scope the guard to the primary (if/then)
  # branch only; guarding the whole prompt false-REDs every versioned adopter (#38/#39).
  printf '%s' "$rprompt" | grep -qF 'foundry-learn-capture' || rp5=1
  _cmdline="$(printf '%s' "$rprompt" | grep -F 'foundry-learn-capture --final')"   # the command line only
  _primary="${_cmdline%%; else *}"                                                 # the if/then (primary) branch
  printf '%s' "$_primary" | grep -Eq '/0\.[0-9]' && rp5=1                          # primary must NOT be version-pinned
  # the absolute-path fallback is still present (no availability regression). The rendered fallback is
  # `"<abs>/foundry-session-learnings.sh" capture …` (note the closing quote between .sh and capture).
  printf '%s' "$rprompt" | grep -Eq 'foundry-session-learnings\.sh"? capture' || rp5=1
  # (b) _cas_done is RELOCATED to the lib: it resolves as a function after sourcing the lib, AND the hook
  # file itself no longer DEFINES it (the single-implementation invariant).
  type _cas_done >/dev/null 2>&1 || rp5=1                                 # sourced (from the lib)
  grep -Eq '^[[:space:]]*_cas_done\(\)' "$_LIB_DIR/foundry-session-learnings.sh" && rp5=1   # hook no longer defines it
  grep -Eq '^[[:space:]]*_cas_done\(\)' "$_LIB_DIR/foundry-learnings-lib.sh" || rp5=1       # lib now defines it
  _emit "AC-PUBCAP-5 single-impl-lib-relocation-producer-repoint-regression-green" "$rp5" "reflection prompt names bare foundry-learn-capture (no /0.x literal) + abs-path fallback; _cas_done sourced from lib, not defined in hook"

  # ======================= feat-foundry-session-learnings-reflection-ux ======================= #
  # Fixture transcripts for the substance gate. `insub` is the shape that triggered this atom: one user
  # turn, read-only Bash only. The three substantive fixtures exercise limbs (a)/(b)/(c) INDEPENDENTLY so
  # no single limb can carry the whole gate.
  local tdir="$tmp/transcripts"; mkdir -p "$tdir"
  cat > "$tdir/insub.jsonl" <<'EOJ'
{"type":"user","message":{"role":"user","content":"run the doctor"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{}}]}}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","content":"DOCTOR-GREEN"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"green"}]}}
EOJ
  cat > "$tdir/sub_mut.jsonl" <<'EOJ'
{"type":"user","message":{"role":"user","content":"fix the typo"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Edit","input":{}}]}}
EOJ
  cat > "$tdir/sub_del.jsonl" <<'EOJ'
{"type":"user","message":{"role":"user","content":"explore this"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Agent","input":{}}]}}
EOJ
  cat > "$tdir/sub_turns.jsonl" <<'EOJ'
{"type":"user","message":{"role":"user","content":"one"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"a"}]}}
{"type":"user","message":{"role":"user","content":"two"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"b"}]}}
{"type":"user","message":{"role":"user","content":"three"}}
EOJ
  printf 'not json at all\n{"type":\n' > "$tdir/corrupt.jsonl"
  # SCHEMA DRIFT: well-formed JSONL of OBJECTS whose discriminator this classifier does not know
  # (the harness renames `type`, or nests the message). Every line is a dict, so a dict-counting
  # `parsed` would report `insubstantial` and silently end capture fleet-wide (security review R-1).
  # Recognition-based counting must route this to `substantive`.
  cat > "$tdir/unknown_schema.jsonl" <<'EOJ'
{"kind":"human","payload":{"role":"user","body":"do the thing"}}
{"kind":"model","payload":{"role":"assistant","body":[{"sort":"call","tool":"Edit"}]}}
{"event":{"type":"user","message":{"role":"user","content":"nested out of reach"}}}
EOJ
  # A valid-but-WRONG file (a real JSONL that is not a transcript) — the cheapest suppression oracle
  # (security review R-3.2). Must also fail toward injecting.
  printf '%s\n' '{"ts":"2026-07-30T00:00:00Z","channel":"session","records":0,"outcome":"ok"}' > "$tdir/wrong_file.jsonl"
  _rux_stop() {  # sid transcript -> emitted stdout (CLI entrypoint, guards live)
    CLAUDE_CODE_ENTRYPOINT=cli; export CLAUDE_CODE_ENTRYPOINT
    printf '{"session_id":"%s","stop_hook_active":false,"transcript_path":"%s"}' "$1" "$2" | _stop 2>/dev/null
  }
  _rux_get() {  # json dotted-path -> string
    printf '%s' "$1" | python3 -c 'import sys,json
try: d=json.load(sys.stdin)
except Exception: print(""); sys.exit(0)
for k in sys.argv[1].split("."):
    d = d.get(k) if isinstance(d, dict) else None
print(d if isinstance(d,str) else "")' "$2" 2>/dev/null
  }

  # ---- AC-RUX-1: short user-facing reason; runbook RELOCATED (not deleted) to additionalContext.
  local ru1=0 out1 reason1 ctx1 sentinel1='never write a file directly under .foundry/session-learnings/'
  out1="$(_rux_stop "RUX1" "$tdir/sub_mut.jsonl")"
  reason1="$(_rux_get "$out1" reason)"
  ctx1="$(_rux_get "$out1" hookSpecificOutput.additionalContext)"
  [ -n "$reason1" ] || ru1=1
  [ "$(printf '%s\n' "$reason1" | grep -c .)" -le 2 ] || ru1=1          # <=2 lines
  printf '%s' "$reason1" | grep -qF 'not an error' || ru1=1             # contradicts the harness caption
  printf '%s' "$reason1" | grep -qF 'FOUNDRY_SESSION_LEARNINGS=off' || ru1=1
  printf '%s' "$reason1" | grep -qF '/foundry:learn-capture' || ru1=1   # self-sufficient pointer (R4)
  printf '%s' "$reason1" | grep -qE '\.sh|/tmp|\.foundry/|\|' && ru1=1  # no shell cmd / filesystem path
  printf '%s' "$reason1" | grep -qF 'RUX1' && ru1=1                     # no session id
  [ "$(_rux_get "$out1" hookSpecificOutput.hookEventName)" = "Stop" ] || ru1=1
  printf '%s' "$ctx1" | grep -qF 'foundry-learn-capture' || ru1=1       # runbook present in MODEL channel
  printf '%s' "$ctx1" | grep -qF "$sentinel1" || ru1=1                  # LBC-3 sentinel travels with it
  printf '%s' "$ctx1" | grep -qF 'RUX1' || ru1=1                        # session id threaded (model side)
  _emit "AC-RUX-1 short-reason-runbook-relocated-to-additionalcontext" "$ru1" "reason <=2 lines, no cmd/path/sid, says 'not an error' + names knob + /foundry:learn-capture; additionalContext carries runbook + LBC-3 sentinel + sid"

  # ---- AC-RUX-2: suppress ONLY on positive proof; each limb independent; fail TOWARD inject.
  local ru2=0 f
  printf '%s' "$(_rux_stop "RUXI" "$tdir/insub.jsonl")" | grep -q '"decision"' && ru2=1   # insubstantial → no inject
  [ -z "$(_marker_state RUXI)" ] || ru2=1                                                 # …and marker NOT consumed
  for f in sub_mut sub_del sub_turns; do
    printf '%s' "$(_rux_stop "RUX_$f" "$tdir/$f.jsonl")" | grep -q '"decision"[[:space:]]*:[[:space:]]*"block"' || ru2=1
  done
  printf '%s' "$(_rux_stop "RUXC" "$tdir/corrupt.jsonl")" | grep -q '"decision"' || ru2=1  # corrupt → inject
  printf '%s' "$(_rux_stop "RUXA" "$tdir/does-not-exist.jsonl")" | grep -q '"decision"' || ru2=1 # absent → inject
  printf '%s' "$(_rux_stop "RUXE" "")" | grep -q '"decision"' || ru2=1                     # empty path → inject
  # R-1: well-formed JSONL, UNRECOGNISED schema → must inject (dict-counting would have suppressed)
  printf '%s' "$(_rux_stop "RUXUS" "$tdir/unknown_schema.jsonl")" | grep -q '"decision"' || ru2=1
  [ "$(_session_is_substantive "$tdir/unknown_schema.jsonl")" = "substantive" ] || ru2=1
  # R-3.2: a valid-but-wrong JSONL file is not proof of insubstantiality either
  [ "$(_session_is_substantive "$tdir/wrong_file.jsonl")" = "substantive" ] || ru2=1
  # R-2: a non-regular file (FIFO) must never be read — it would stall the idle, not classify it
  local fifo="$tdir/fifo.jsonl"; rm -f "$fifo"; mkfifo "$fifo" 2>/dev/null \
    && { [ "$(_session_is_substantive "$fifo")" = "substantive" ] || ru2=1; rm -f "$fifo"; }
  # a suppressed idle must leave a LATER substantive idle able to reflect (marker budget intact)
  printf '%s' "$(_rux_stop "RUXI" "$tdir/sub_mut.jsonl")" | grep -q '"decision"' || ru2=1
  _emit "AC-RUX-2 substance-gate-suppresses-only-on-positive-proof" "$ru2" "insubstantial→no-inject+no-marker; mutation/delegation/3-turns each→inject; corrupt/absent/empty→inject (fail-toward); suppressed session can still reflect later"

  # ---- AC-RUX-3: off / full / gated (+ garbage degrades to gated, never an error).
  local ru3=0
  printf '%s' "$(FOUNDRY_SESSION_LEARNINGS=off _rux_stop "RUXOFF" "$tdir/sub_mut.jsonl")" | grep -q '"decision"' && ru3=1
  [ -z "$(_marker_state RUXOFF)" ] || ru3=1                                   # off writes NO marker
  printf '%s' "$(FOUNDRY_SESSION_LEARNINGS=full _rux_stop "RUXFULL" "$tdir/insub.jsonl")" | grep -q '"decision"' || ru3=1
  printf '%s' "$(FOUNDRY_SESSION_LEARNINGS=wat _rux_stop "RUXGB" "$tdir/insub.jsonl")" | grep -q '"decision"' && ru3=1
  printf '%s' "$(FOUNDRY_SESSION_LEARNINGS=wat _rux_stop "RUXGB2" "$tdir/sub_mut.jsonl")" | grep -q '"decision"' || ru3=1
  [ "$(FOUNDRY_SESSION_LEARNINGS=off _knob)" = "off" ] || ru3=1
  [ "$(FOUNDRY_SESSION_LEARNINGS=wat _knob)" = "gated" ] || ru3=1
  # the min-turns threshold is honoured (2-turn transcript is insubstantial at 3, substantive at 2)
  printf '%s' "$(FOUNDRY_SESSION_LEARNINGS_MIN_TURNS=2 _rux_stop "RUXMT" "$tdir/insub.jsonl")" | grep -q '"decision"' && ru3=1
  printf '%s' "$(FOUNDRY_SESSION_LEARNINGS_MIN_TURNS=1 _rux_stop "RUXMT2" "$tdir/insub.jsonl")" | grep -q '"decision"' || ru3=1
  _emit "AC-RUX-3 operator-opt-out-off-full-gated" "$ru3" "off→no-inject+no-marker; full→inject on insubstantial; garbage→gated; MIN_TURNS honoured"

  # ---- AC-RUX-4: anti-tautology — every guard proven load-bearing on known-bad shapes.
  local ru4=0 out_rt1 out_rt2
  # (i) relocation is real, not a deletion: known-bad = runbook left in the reason
  printf '%s' "$reason1" | grep -qF 'foundry-learn-capture --final' && ru4=1
  [ -n "$ctx1" ] || ru4=1
  # (ii) the pre-existing guards still no-op (re-entrancy / headless / marker states)
  out_rt1="$(CLAUDE_CODE_ENTRYPOINT=cli; export CLAUDE_CODE_ENTRYPOINT; printf '{"session_id":"RUXR","stop_hook_active":true,"transcript_path":"%s"}' "$tdir/sub_mut.jsonl" | _stop 2>/dev/null)"
  printf '%s' "$out_rt1" | grep -q '"decision"' && ru4=1
  out_rt2="$(CLAUDE_CODE_ENTRYPOINT=headless; export CLAUDE_CODE_ENTRYPOINT; printf '{"session_id":"RUXH","stop_hook_active":false,"transcript_path":"%s"}' "$tdir/sub_mut.jsonl" | _stop 2>/dev/null)"
  printf '%s' "$out_rt2" | grep -q '"decision"' && ru4=1
  _set_marker "RUXD" done;   printf '%s' "$(_rux_stop "RUXD" "$tdir/sub_mut.jsonl")"  | grep -q '"decision"' && ru4=1
  _set_marker "RUXG" wat-xy; printf '%s' "$(_rux_stop "RUXG" "$tdir/sub_mut.jsonl")"  | grep -q '"decision"' && ru4=1
  # (iii) the gate itself is not vacuous — the classifier disagrees across the two shapes
  [ "$(_session_is_substantive "$tdir/insub.jsonl")" = "insubstantial" ] || ru4=1
  [ "$(_session_is_substantive "$tdir/sub_mut.jsonl")" = "substantive" ] || ru4=1
  # (iv) the good path produced a real, parseable artifact (not a vacuous all-skip)
  [ -n "$(_rux_get "$out1" hookSpecificOutput.additionalContext)" ] || ru4=1
  _emit "AC-RUX-4 selftest-real-oracle-anti-tautology" "$ru4" "runbook-in-reason would FAIL; reentrancy/headless/done/garbage-marker all still no-op; classifier separates the two fixtures; real parseable artifact"

  # ================ feat-foundry-learnings-substance-gate-synthetic-turns ================ #
  # Fixtures for the two-signal (origin/promptSource dominates; leading-tag excludes; neither
  # counts) limb-(c) classifier. MIN_TURNS=1 margin engineering (spec design note): at the
  # default threshold of 3 a single miscounted phantom record cannot flip the observable
  # outcome; at 1 it always does, so a regression in any single tag's exclusion fails loudly.
  local synt_names=(cn cso cav cmsg cargs)
  local synt_bodies=(
    '<command-name>/model</command-name>'
    '<local-command-stdout>Set model to Fable 5</local-command-stdout>'
    '<local-command-caveat>Caveat: local command output below</local-command-caveat>'
    '<command-message>model</command-message>'
    '<command-args></command-args>'
  )

  # ---- AC-SYNT-1: local-command records are not user turns (the ordered two-signal rule).
  local sy1=0 si nm body
  si=0
  while [ "$si" -lt 5 ]; do
    nm="${synt_names[$si]}"; body="${synt_bodies[$si]}"
    # (i) per-tag margin case: ONLY the synthetic record (no isMeta, no origin) -> no decision,
    # no marker write, at MIN_TURNS=1 (exercises the <local-command-caveat> belt-and-braces
    # branch too — non-isMeta here, unlike a real caveat record which IS isMeta and already
    # skipped upstream).
    printf '{"type":"user","message":{"role":"user","content":"%s"}}\n' "$body" > "$tdir/synt_only_$nm.jsonl"
    printf '%s' "$(FOUNDRY_SESSION_LEARNINGS_MIN_TURNS=1 _rux_stop "SYNT_ONLY_$nm" "$tdir/synt_only_$nm.jsonl")" | grep -q '"decision"' && sy1=1
    [ -z "$(_marker_state "SYNT_ONLY_$nm")" ] || sy1=1
    si=$((si+1))
  done
  # (iii) anchor/override proofs (entries without origin unless stated).
  # a genuine string-shape turn that merely MENTIONS a tag mid-string still counts (leading-tag
  # anchor, not a substring test).
  printf '%s\n' '{"type":"user","message":{"role":"user","content":"why does <command-name> show up in my transcript?"}}' > "$tdir/synt_anchor_midstring.jsonl"
  [ "$(FOUNDRY_SESSION_LEARNINGS_MIN_TURNS=1 _session_is_substantive "$tdir/synt_anchor_midstring.jsonl")" = "substantive" ] || sy1=1
  # list-shape: first text block is genuine prose, a LATER block begins with a tag -> still
  # counts (first-block anchor; paired below with the negative control: tag genuinely first ->
  # excluded, proving the anchor is load-bearing in both directions).
  cat > "$tdir/synt_anchor_listshape.jsonl" <<'EOJ'
{"type":"user","message":{"role":"user","content":[{"type":"text","text":"genuine prose"},{"type":"text","text":"<local-command-stdout>data</local-command-stdout>"}]}}
EOJ
  [ "$(FOUNDRY_SESSION_LEARNINGS_MIN_TURNS=1 _session_is_substantive "$tdir/synt_anchor_listshape.jsonl")" = "substantive" ] || sy1=1
  cat > "$tdir/synt_anchor_listshape_excluded.jsonl" <<'EOJ'
{"type":"user","message":{"role":"user","content":[{"type":"text","text":"<local-command-stdout>data</local-command-stdout>"},{"type":"text","text":"genuine prose after"}]}}
EOJ
  [ "$(FOUNDRY_SESSION_LEARNINGS_MIN_TURNS=1 _session_is_substantive "$tdir/synt_anchor_listshape_excluded.jsonl")" = "insubstantial" ] || sy1=1
  # structural human proof (origin.kind:"human") dominates a leading tag match.
  printf '%s\n' '{"type":"user","message":{"role":"user","content":"<command-name>/model</command-name>"},"origin":{"kind":"human"}}' > "$tdir/synt_origin_dominates.jsonl"
  [ "$(FOUNDRY_SESSION_LEARNINGS_MIN_TURNS=1 _session_is_substantive "$tdir/synt_origin_dominates.jsonl")" = "substantive" ] || sy1=1
  # the second structural signal (a promptSource field, no origin at all) dominates too.
  printf '%s\n' '{"type":"user","message":{"role":"user","content":"<command-name>/model</command-name>"},"promptSource":"typed"}' > "$tdir/synt_promptsource_dominates.jsonl"
  [ "$(FOUNDRY_SESSION_LEARNINGS_MIN_TURNS=1 _session_is_substantive "$tdir/synt_promptsource_dominates.jsonl")" = "substantive" ] || sy1=1
  _emit "AC-SYNT-1 local-command-records-excluded-from-turn-count" "$sy1" "all 5 tags excluded solo (no decision/marker @ MIN_TURNS=1); mid-string mention counts; list-shape first-block anchor counts/excludes correctly; origin.kind:human + promptSource both dominate a leading tag"

  # ---- AC-SYNT-2: the cadence wording is honest, in every emitter.
  local sy2=0 outw reasonw ctxw sentinelw='never write a file directly under .foundry/session-learnings/'
  outw="$(_rux_stop "SYNTW" "$tdir/sub_mut.jsonl")"
  reasonw="$(_rux_get "$outw" reason)"
  ctxw="$(_rux_get "$outw" hookSpecificOutput.additionalContext)"
  printf '%s' "$ctxw" | grep -qF 'once per session, at the first qualifying idle' || sy2=1
  printf '%s' "$ctxw" | grep -qF "$sentinelw" || sy2=1                                # AC-LBC-3 sentinel still travels
  printf '%s' "$reasonw" | grep -qF 'once per session' || sy2=1
  printf '%s' "$reasonw" | grep -qF 'Before this session ends' && sy2=1
  printf '%s' "$reasonw" | grep -qF 'end-of-session' && sy2=1
  printf '%s' "$ctxw" | grep -qF 'Before this session ends' && sy2=1
  printf '%s' "$ctxw" | grep -qF 'end-of-session' && sy2=1
  # the rendered replacement phrase itself carries no shell-expansion metacharacter (the runbook
  # heredoc is unquoted).
  printf '%s' "$ctxw" | grep -F 'once per session, at the first qualifying idle' | grep -Eq '[$`\\]' && sy2=1
  # _short_reason's heredoc stays single-quoted (source-level regression).
  grep -A1 '^_short_reason() {' "$_LIB_DIR/foundry-session-learnings.sh" | grep -qF "cat <<'EOF'" || sy2=1
  _emit "AC-SYNT-2 honest-once-per-session-cadence-wording" "$sy2" "additionalContext + reason carry the honest literal; neither channel carries the two banned phrases; wording metacharacter-free; _short_reason heredoc still single-quoted"

  # ---- AC-SYNT-3: the live-seam is a real behavioral oracle with anti-tautology.
  local sy3=0
  # (ii) each per-tag fixture PLUS one genuine origin.kind:"human" turn flips to a block at the
  # same MIN_TURNS=1 threshold — proving the exclusion does not over-suppress.
  si=0
  while [ "$si" -lt 5 ]; do
    nm="${synt_names[$si]}"; body="${synt_bodies[$si]}"
    {
      printf '{"type":"user","message":{"role":"user","content":"%s"}}\n' "$body"
      printf '{"type":"user","message":{"role":"user","content":"what next?"},"origin":{"kind":"human"}}\n'
    } > "$tdir/synt_plus_human_$nm.jsonl"
    printf '%s' "$(FOUNDRY_SESSION_LEARNINGS_MIN_TURNS=1 _rux_stop "SYNT_HUMAN_$nm" "$tdir/synt_plus_human_$nm.jsonl")" | grep -q '"decision"[[:space:]]*:[[:space:]]*"block"' || sy3=1
    si=$((si+1))
  done
  # (iv) the python-failure fallback emitter, driven by forcing python3 to fail (a shell function
  # override — the same interpreter, so it shadows every `python3` call this hook makes).
  local out_fb reason_fb
  export CLAUDE_SESSION_ID="SYNTFB"
  python3() { return 127; }
  out_fb="$(_rux_stop "SYNTFB" "")"
  unset -f python3
  unset CLAUDE_SESSION_ID
  reason_fb="$(printf '%s' "$out_fb" | grep -o '"reason":"[^"]*"')"
  [ -n "$reason_fb" ] || sy3=1
  printf '%s' "$reason_fb" | grep -qF 'once per session' || sy3=1
  printf '%s' "$reason_fb" | grep -qF 'Before this session ends' && sy3=1
  printf '%s' "$reason_fb" | grep -qF 'end-of-session' && sy3=1
  # anti-tautology: proves the FALLBACK shape (no hookSpecificOutput) was really hit — if the
  # python3 override failed to engage, the primary emitter would run and this would FAIL.
  printf '%s' "$out_fb" | grep -q 'hookSpecificOutput' && sy3=1
  printf '%s' "$out_fb" | python3 -c 'import sys,json; json.load(sys.stdin)' >/dev/null 2>&1 || sy3=1
  _emit "AC-SYNT-3 selftest-real-oracle-anti-tautology" "$sy3" "per-tag fixture+human-turn flips to block @ MIN_TURNS=1; python3-failure fallback reason carries the honest literal, no banned phrase, and is provably the no-hookSpecificOutput fallback shape (real+valid JSON)"

  rm -rf "$tmp" 2>/dev/null || true
  echo ""
  if [ "$fails" -eq 0 ]; then echo "FOUNDRY-SESSION-LEARNINGS-SELFTEST-GREEN"; return 0; else echo "FOUNDRY-SESSION-LEARNINGS-SELFTEST-RED"; return 1; fi
}

case "${1:-}" in
  stop)        _stop ;;
  precompact)  _precompact ;;
  capture)     shift; _capture "$@" ;;
  --selftest)  _selftest; exit $? ;;
  *) echo "usage: $0 stop | precompact | capture --session-id <id> (records JSONL on stdin) | --selftest" >&2; exit 2 ;;
esac
