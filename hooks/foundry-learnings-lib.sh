#!/usr/bin/env bash
# foundry-learnings-lib — shared primitives for the Foundry learnings PRODUCERS:
#   - foundry-harvest-learnings.sh  (worker structured-return + worktree sidecar; channels return/sidecar)
#   - foundry-session-learnings.sh  (direct interactive-session reflection; channel session)
#
# SOURCE this file; it defines functions ONLY (no CLI dispatch), so sourcing executes nothing.
# Extracted from foundry-harvest-learnings.sh (feat-foundry-worktree-learnings-harvest, 6 audit rounds)
# so the two producers share ONE writer (content-addressed, atomic ln-claim, idempotent, fail-open) +
# ONE loss-log + ONE secret-scrub. The worker writer's behavior is preserved byte-for-byte except the
# additive mechanical secret-scrub (which redacts in-place WITHOUT changing record COUNTS, so the
# worker --selftest stays GREEN). See feat-foundry-session-learnings-capture AC-SLEARN-7.
#
# Do NOT `set -e` in a sourced lib; callers set their own `set -uo pipefail` and rely on fail-open.

# Idempotent source-guard (sourcing twice is a no-op).
[ -n "${_FOUNDRY_LEARNINGS_LIB:-}" ] && return 0 2>/dev/null
_FOUNDRY_LEARNINGS_LIB=1

# The MAIN-worktree .foundry parent root. CRITICAL: never resolve INTO a linked worktree (it gets
# wiped by teardown). Prefer CLAUDE_PROJECT_DIR (plugin contract); else the main worktree root via the
# common git dir (NOT --show-toplevel, which returns the LINKED worktree root).
_foundry_root() {
  local base="${CLAUDE_PROJECT_DIR:-}" cdir
  if [ -z "$base" ]; then
    cdir="$(git rev-parse --git-common-dir 2>/dev/null || true)"   # main .git, even from a linked worktree
    if [ -n "$cdir" ]; then base="$(cd "$(dirname "$cdir")" 2>/dev/null && pwd || true)"; fi
    [ -n "$base" ] || base="$(pwd)"
  fi
  printf '%s' "$base"
}

# Resolution-ONLY buffer-root walk-up (feat-foundry-capture-projectdir-resolve, AC-LCPR-1/-2/-6).
# Writes NOTHING; mutates nothing (the scrub/CAS/reject seam is UNTOUCHED). Constrained so it NEVER
# mis-resolves in this multi-repo control plane (gitignored child product repos + dispatch worktrees):
#   (1) a full directory walk-up from <start> picks the OUTERMOST ancestor carrying a `.foundry/`
#       DIRECTORY (the buffer partition's own home) — so a nested CHILD product repo (its own
#       `.foundry/`) under an outer workspace resolves to the OUTER workspace, never the child;
#   (2) when NO `.foundry/`-bearing ancestor exists, fall back to the MAIN worktree root = the
#       `git rev-parse --git-common-dir` PARENT (mirrors _foundry_root), NEVER `--show-toplevel` /
#       nearest-`.git` (which returns the LINKED-worktree root, wiped by teardown) — so a linked
#       dispatch worktree resolves to its MAIN root.
# A `.git` that is a FILE (a linked-worktree gitdir pointer) never qualifies as a root and never
# terminates the walk (the walk keys on `.foundry/` directories only; the git fallback keys on the
# git-common-dir). Prints the resolved ABSOLUTE root, or EMPTY if none resolves (caller fails closed).
_resolve_buffer_root_walkup() {  # [start_dir]
  local start dir prev outermost_foundry=""
  start="$(cd "${1:-$PWD}" 2>/dev/null && pwd || true)"
  [ -n "$start" ] || return 0
  dir="$start"
  while [ -n "$dir" ]; do
    [ -d "$dir/.foundry" ] && outermost_foundry="$dir"   # keep overwriting going UP → outermost wins
    prev="$dir"; dir="$(dirname "$dir")"
    [ "$dir" = "$prev" ] && break                        # reached the filesystem root
  done
  if [ -n "$outermost_foundry" ]; then
    printf '%s' "$outermost_foundry"; return 0
  fi
  # No `.foundry/` ancestor → MAIN worktree root via the git-common-dir PARENT (resolved relative to
  # <start>, so a linked-worktree pwd maps to the MAIN .git, not the linked one). Empty if not a repo.
  local cdir parent
  cdir="$(cd "$start" 2>/dev/null && git rev-parse --git-common-dir 2>/dev/null || true)"
  if [ -n "$cdir" ]; then
    parent="$(cd "$start" 2>/dev/null && cd "$cdir" 2>/dev/null && cd .. 2>/dev/null && pwd || true)"
    [ -n "$parent" ] && { printf '%s' "$parent"; return 0; }
  fi
  return 0
}

# Destination = the partition /foundry:learn-distill consumes (.foundry/session-learnings/**).
_dest_root() { printf '%s/.foundry/session-learnings' "$(_foundry_root)"; }

_sanitize() { printf '%s' "${1:-}" | tr -c 'A-Za-z0-9._-' '-'; }

# Short content hash of a file (for content-addressed, idempotent filenames).
_content_hash() {
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" 2>/dev/null | cut -c1-12
  elif command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" 2>/dev/null | cut -c1-12
  else cksum "$1" 2>/dev/null | tr -c 'A-Za-z0-9' '-' | cut -c1-12; fi
}

# Distinct, filesystem-safe task id from a worktree path (worker producer).
_taskid_from_path() {
  local p="${1%/}" id
  case "$p" in
    *"/.worktrees/"*)        id="${p#*/.worktrees/}" ;;
    *"/.claude/worktrees/"*) id="${p#*/.claude/worktrees/}" ;;
    *) id="$(basename "$p")" ;;
  esac
  _sanitize "${id//\//-}"
}

# MECHANICAL secret-shape scrub (best-effort defense-in-depth — NOT a sufficient/sole control; see
# AC-SLEARN-7 + UL-0016). Redacts common secret shapes IN PLACE, per-line, so the record line COUNT is
# unchanged (the content hash is taken AFTER this, so redacted records dedup canonically). A novel
# shape escapes by construction — the authoritative boundary is operator review at the publish step.
_secret_scrub() {  # file (modified in place); fail-open (leaves file untouched on any error)
  local f="${1:-}"
  [ -n "$f" ] && [ -f "$f" ] || return 0
  python3 - "$f" <<'PY' 2>/dev/null || true
import sys, re
f = sys.argv[1]
# Per-line so the line COUNT never changes (records are one JSON object per line; an embedded PEM is
# escaped onto a single physical line within the JSON string).
PATS = [
    (re.compile(r'AKIA[0-9A-Z]{16}'), '<REDACTED-AWS-KEY>'),
    (re.compile(r'(?i)(bearer|authorization)([\"\s:=]+)[A-Za-z0-9._\-]{8,}'), r'\1\2<REDACTED-TOKEN>'),
    (re.compile(r'xox[baprs]-[A-Za-z0-9-]{8,}'), '<REDACTED-SLACK>'),
    (re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})'), '<REDACTED-GH>'),
    (re.compile(r'-----BEGIN[A-Z ]*PRIVATE KEY-----.*?-----END[A-Z ]*PRIVATE KEY-----'), '<REDACTED-PEM>'),
    # long high-entropy base64/hex run (≥40) — likely a secret/token. Bounded by word edges so it does
    # not gobble adjacent JSON punctuation.
    (re.compile(r'(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/=])'), '<REDACTED-HIENTROPY>'),
]
try:
    data = open(f, encoding='utf-8', errors='replace').read()
except Exception:
    sys.exit(0)
nl = data.endswith('\n')
out = []
for ln in data.split('\n'):
    for rx, repl in PATS:
        ln = rx.sub(repl, ln)
    out.append(ln)
res = '\n'.join(out)
# split('\n') on a trailing-newline string yields a trailing '' → join already preserves it; guard anyway
open(f, 'w').write(res)
PY
  return 0
}

# Durable reconciliation row (at-least-once / loss signal). Fail-open. ALSO emits to stderr so the
# signal survives even when the buffer ROOT is unwritable (the systemic-failure case).
_log_attempt() {  # channel task_id records outcome
  local root log ts row
  root="$(_dest_root)"; log="$root/.harvest-log.jsonl"
  ts="$(date -u +%FT%TZ 2>/dev/null || echo unknown)"
  row="$(printf '{"ts":"%s","channel":"%s","task_id":"%s","records":%s,"outcome":"%s"}' \
    "$ts" "${1:-?}" "${2:-?}" "${3:-0}" "${4:-?}")"
  if ! { mkdir -p "$root" 2>/dev/null && printf '%s\n' "$row" >> "$log" 2>/dev/null; }; then
    echo "foundry-harvest-log: $row" >&2   # durable fallback when the buffer root is unwritable
  fi
  return 0
}

# Atomic write of a file of raw record-lines into the buffer. Echoes the record count on success;
# returns non-zero (fail-open) on any write failure, logging the outcome either way. The secret-scrub
# runs BEFORE the content hash so redacted records dedup canonically and counts are stable.
# On failure, sets _WRITE_LOSS_CAUSE (AC-CLO-1, ER #70) — the FAILING PATH + the OS error text, content-
# free (paths + error only, NEVER record content) — and logs the ATTEMPTED record count (AC-CLO-2), not 0.
_WRITE_LOSS_CAUSE=""
_write_records() {  # src_file session_id task_id channel
  local src="$1" sid="$2" taskid="$3" channel="$4"
  local date_part dest_dir dest tmp n base k h n_att err
  _WRITE_LOSS_CAUSE=""
  n_att="$(grep -c '[^[:space:]]' "$src" 2>/dev/null)"; case "$n_att" in ''|*[!0-9]*) n_att=0 ;; esac
  date_part="$(date -u +%F 2>/dev/null || echo unknown-date)"
  dest_dir="$(_dest_root)/$date_part"
  if ! err="$(mkdir -p "$dest_dir" 2>&1)"; then
    _WRITE_LOSS_CAUSE="cannot create $dest_dir: ${err:-unknown error}"
    echo "WARN: foundry-learnings: $_WRITE_LOSS_CAUSE (fail-open)" >&2
    _log_attempt "$channel" "$taskid" "$n_att" fail-open; return 1
  fi
  tmp="$(mktemp "$dest_dir/.harvest.XXXXXX" 2>&1)"   # on failure mktemp prints nothing to stdout; $tmp = the error text
  if [ -z "$tmp" ] || [ ! -f "$tmp" ]; then
    _WRITE_LOSS_CAUSE="mktemp failed in $dest_dir: ${tmp:-unknown error}"
    echo "WARN: foundry-learnings: $_WRITE_LOSS_CAUSE (fail-open)" >&2
    _log_attempt "$channel" "$taskid" "$n_att" fail-open; return 1
  fi
  grep -v '^[[:space:]]*$' "$src" > "$tmp" 2>/dev/null || true   # forward UNVALIDATED; drop blank lines only
  _secret_scrub "$tmp"                                           # AC-SLEARN-7 mechanical scrub (in-place; count-stable)
  n="$(grep -c . "$tmp" 2>/dev/null || echo 0)"
  # CONTENT-ADDRESSED, IDEMPOTENT, COLLISION-SAFE filename: <session_id>__<task-id>__<sha8>.jsonl
  k=1
  h="$(_content_hash "$tmp")"
  base="$dest_dir/${sid}__${taskid}__${h}"; dest="${base}.jsonl"
  while ! ln "$tmp" "$dest" 2>/dev/null; do
    if [ ! -e "$dest" ]; then            # ln failed for a non-collision reason (e.g. fs w/o hardlinks)
      if ! err="$(mv -f "$tmp" "$dest" 2>&1)"; then
        rm -f "$tmp" 2>/dev/null || true
        _WRITE_LOSS_CAUSE="write to $dest failed: ${err:-unknown error}"
        echo "WARN: foundry-learnings: $_WRITE_LOSS_CAUSE (fail-open)" >&2
        _log_attempt "$channel" "$taskid" "$n" fail-open; return 1
      fi
      break
    fi
    if cmp -s "$tmp" "$dest" 2>/dev/null; then   # IDENTICAL content already captured → idempotent skip
      rm -f "$tmp" 2>/dev/null || true
      _log_attempt "$channel" "$taskid" "$n" dup
      echo "foundry-learnings: $channel: $n record(s) already captured (idempotent) → $dest" >&2
      printf '%s' "$n"; return 0
    fi
    k=$((k+1)); dest="${base}.${k}.jsonl"      # hash collision with different content (≈never) → suffix
    [ "$k" -gt 100000 ] && break
  done
  rm -f "$tmp" 2>/dev/null || true             # ln succeeded → drop the redundant temp link
  _log_attempt "$channel" "$taskid" "$n" ok
  echo "foundry-learnings: $channel: $n record(s) → $dest" >&2
  printf '%s' "$n"; return 0
}

# ----- per-session marker (direct-session producer; pure once-per-session re-entrancy guard) ----- #
# Lives OUTSIDE the .foundry/session-learnings partition (so it is never globbed as a record by
# /foundry:learn-distill, nor pruned by the buffer). Content is `requested` | `done`. session_id-scoped.
_marker_dir()  { printf '%s/.foundry/session-markers' "$(_foundry_root)"; }
_marker_path() { printf '%s/%s' "$(_marker_dir)" "$(_sanitize "${1:-unknown}")"; }
_marker_state(){ cat "$(_marker_path "${1:-unknown}")" 2>/dev/null || true; }
_set_marker() {  # sid state   (atomic write-temp-then-mv); returns non-zero on failure (fail-open)
  local d t; d="$(_marker_dir)"
  mkdir -p "$d" 2>/dev/null || return 1
  t="$(mktemp "$d/.mk.XXXXXX" 2>/dev/null)" || return 1
  printf '%s' "${2:-}" > "$t" 2>/dev/null || { rm -f "$t" 2>/dev/null; return 1; }
  mv -f "$t" "$(_marker_path "${1:-unknown}")" 2>/dev/null || { rm -f "$t" 2>/dev/null; return 1; }
  return 0
}
_prune_markers() {  # best-effort sweep of markers older than the buffer retention window (90 days)
  local d; d="$(_marker_dir)"
  [ -d "$d" ] && find "$d" -type f -mtime +90 -delete 2>/dev/null || true
  return 0
}

# Compare-and-set the marker to `done` (AC-COMPACT-2.2 / AC-PUBCAP-5). RELOCATED from
# foundry-session-learnings.sh into the shared lib so the hook `capture` subcommand AND
# bin/foundry-learn-capture call the SAME implementation (no second drifting copy). Preserve a
# `rearmed` that raced in during the reflection turn (a manual /compact landed mid-reflection) so the
# next idle re-injects for the new segment; otherwise flip to `done`. The caller's $sid is already
# _sanitize'd; the marker fns sanitize idempotently.
_cas_done() {  # sid
  [ "$(_marker_state "$1")" = "rearmed" ] || _set_marker "$1" done
}

# --------------------------------- shared record ingest --------------------------------- #
# RELOCATED per-line JSON-object reject pre-pass (AC-PUBCAP-4/5; was the inline FOUNDRY_REJ python3
# block in _capture). Reads STDIN; writes canonical sorted-keys dict lines to <out>; writes
# reason-ONLY tokens (`unparseable` / `non-object`, NEVER the line content — closes the secret window)
# to <rej>. A dict line is canonicalized + forwarded UNCHANGED (the distiller stays authority on
# clustering VALUE — no field normalization here). Fail-open (never aborts the caller's session).
_reject_filter() {  # out rej   (records on STDIN)
  local out="$1" rej="$2"
  FOUNDRY_REJ="$rej" python3 -c '
import sys, os, json
rejf = open(os.environ["FOUNDRY_REJ"], "w") if os.environ.get("FOUNDRY_REJ") else None
for line in sys.stdin:
    line = line.rstrip("\n")
    if not line.strip(): continue
    try:
        rec = json.loads(line)
    except Exception:
        if rejf: rejf.write("unparseable\n")      # reason ONLY, never the line
        continue
    if not isinstance(rec, dict):
        if rejf: rejf.write("non-object\n")        # a JSON array/scalar parses but is not a record
        continue
    print(json.dumps(rec, sort_keys=True, separators=(",", ":")))
if rejf: rejf.close()
' > "$out" 2>/dev/null || true
}

# Shared ingest orchestrator (AC-PUBCAP-4/5). Reads STDIN → _reject_filter → logs one content-free
# reject row per rejected line → writes the forwarded records (or logs a genuine `ok records:0` ONLY
# when there were zero forwarded lines AND zero rejects). Does NOT touch the marker — the CALLER owns
# the marker tail (the two-caller mode split, AC-PUBCAP-3). Behavior is byte-compatible with the old
# _capture reject/log/write so AC-LBC-2 stays GREEN. Sets observability globals the caller can read
# (AC-CLO-2/-3, ER #70 — the ledger reconciles: _INGEST_READ = _INGEST_ACCEPTED + _INGEST_REJECTS +
# _INGEST_LOSS):
#   _INGEST_READ     — count of non-blank input lines read
#   _INGEST_ACCEPTED — count of records persisted (or idempotent-dup'd)
#   _INGEST_REJECTS  — count of per-line rejects
#   _INGEST_LOSS     — count of RECORDS that failed to persist (not a batch flag)
_INGEST_READ=0
_INGEST_ACCEPTED=0
_INGEST_REJECTS=0
_INGEST_LOSS=0
_ingest_records() {  # sid channel taskid   (records on STDIN)
  local sid="$1" channel="$2" taskid="$3" tmp rej reason nfwd wrote
  _INGEST_READ=0; _INGEST_ACCEPTED=0; _INGEST_REJECTS=0; _INGEST_LOSS=0; _WRITE_LOSS_CAUSE=""
  if ! tmp="$(mktemp 2>/dev/null)" || [ -z "$tmp" ]; then
    # scratch unavailable: records were never read into the reject filter — consume STDIN just to
    # COUNT the loss (nothing can be written anyway; AC-CLO-2's records-never-read case).
    _INGEST_READ="$(grep -c '[^[:space:]]' 2>/dev/null)"; case "$_INGEST_READ" in ''|*[!0-9]*) _INGEST_READ=0 ;; esac
    _INGEST_LOSS="$_INGEST_READ"
    _WRITE_LOSS_CAUSE="cannot create ingest scratch (mktemp failed in the system temp dir)"
    _log_attempt "$channel" "$taskid" "$_INGEST_LOSS" fail-open
    return 0
  fi
  rej="$tmp.rej"
  _reject_filter "$tmp" "$rej"
  # Emit one content-free reject row per rejected line BEFORE the empty-tmp short-circuit so an
  # all-rejected batch is recorded as reject:*, never masked as a false `ok records:0`.
  if [ -s "$rej" ]; then
    while IFS= read -r reason; do
      [ -n "$reason" ] || continue
      _log_attempt "$channel" "$taskid" 0 "reject:$reason"
      _INGEST_REJECTS=$((_INGEST_REJECTS+1))
    done < "$rej"
  fi
  rm -f "$rej" 2>/dev/null || true               # sibling artifact; _write_records never touches it
  nfwd="$(grep -c . "$tmp" 2>/dev/null)"; case "$nfwd" in ''|*[!0-9]*) nfwd=0 ;; esac
  _INGEST_READ=$((nfwd + _INGEST_REJECTS))
  if [ ! -s "$tmp" ]; then
    rm -f "$tmp" 2>/dev/null || true
    # genuinely-empty input (no forwarded lines AND no rejects) → legitimate `ok records:0`; an
    # all-rejected batch already logged its reject:* rows above (do NOT also log a masking ok:0).
    [ "$_INGEST_REJECTS" -eq 0 ] && _log_attempt "$channel" "$taskid" 0 ok
    return 0
  fi
  # AC-CLO-2: a failed batch loses ALL its forwarded records — count them, don't flag `1`. The count
  # is captured via a scratch FILE, not `$(…)` — a command substitution would subshell _write_records
  # and drop the _WRITE_LOSS_CAUSE global (AC-CLO-1) on the floor.
  if _write_records "$tmp" "$sid" "$taskid" "$channel" > "$tmp.n" 2>&2; then
    wrote="$(cat "$tmp.n" 2>/dev/null)"
    case "$wrote" in ''|*[!0-9]*) wrote="$nfwd" ;; esac
    _INGEST_ACCEPTED="$wrote"
  else
    _INGEST_LOSS="$nfwd"
  fi
  rm -f "$tmp" "$tmp.n" 2>/dev/null || true
  return 0
}
