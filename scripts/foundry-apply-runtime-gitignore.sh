#!/usr/bin/env bash
# foundry-apply-runtime-gitignore.sh — idempotent managed-block applier for the `.foundry/`
# runtime-partition default-deny fragment (feat-foundry-runtime-gitignore-leak-scan, AC-RGLS-3/4).
#
# Installs/converges a `FOUNDRY-RUNTIME-GITIGNORE-BEGIN`/`-END` managed block, whose interior is
# byte-identical to the sibling fragment `scripts/foundry-runtime.gitignore`, into
# `<repo-root>/.gitignore`. The fragment default-denies every child of `.foundry/` and re-includes
# only the small designed-tracked set (README.md, build-provenance.yaml,
# stack-profile.lock) — see the fragment file and the spec for why (a blanket `.foundry/` ignore
# cannot be re-included from inside; a name-based ignore list misses the file nobody thought of,
# which is exactly the class that leaked — see GO-PUBLIC.md §5.4).
#
#   foundry-apply-runtime-gitignore.sh <repo-root>
#   foundry-apply-runtime-gitignore.sh --help
#
# Behavior (AC-RGLS-3):
#   (a) VALIDATES the fragment before writing anything — a malformed/tampered fragment is a
#       refusal, never a bad block written into every adopter repo on the next upgrade run.
#   (b) CONVERGES <repo-root>/.gitignore on exactly one managed block whose interior is
#       byte-identical to the fragment, from any starting state (absent file, no block, a stale
#       block, an already-converged block, or two-or-more blocks) — retaining every line outside
#       the sentinels byte-for-byte.
#   (c) REFUSES, non-zero and WITHOUT WRITING, on a malformed sentinel state (a BEGIN with no
#       matching END, or an END preceding the first BEGIN) — naming the offending line, so an
#       unterminated range can never truncate the file.
#   (d) NEVER SILENTLY UN-IGNORES a designed-tracked member: because git resolves the LAST
#       matching pattern, an appended block of `!` re-includes would otherwise silently override
#       any earlier adopter rule that ignores one of those same paths. Before writing, any such
#       rule found outside the block is re-emitted as a "deviation line" after the END sentinel
#       (carrying forward its own preceding rationale comment, or a synthesized one if it had
#       none) — never left in place to be silently beaten by the block's re-includes.
#   (e) STUBS the recorded-bad-SHA file (`<repo-root>/.foundry/leak-scan/known-bad-shas.txt`) with
#       an explanatory header if absent; an existing one is left byte-unchanged.
#
# Exit codes: 0 on success (converged + stubbed); 1 on any refusal (usage error, invalid fragment,
# malformed target sentinel state, symlinked .gitignore, or any I/O failure) — and on every
# refusal path, <repo-root>/.gitignore is left completely untouched.
#
# The fragment path defaults to the sibling file next to this script (never `--root`-relative),
# overridable via $FOUNDRY_RUNTIME_GITIGNORE_FRAGMENT for test fixtures that stage a scratch
# scripts/ directory with a deliberately-malformed fragment alongside a copy of this script.

set -eo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRAGMENT_PATH="${FOUNDRY_RUNTIME_GITIGNORE_FRAGMENT:-$SELF_DIR/foundry-runtime.gitignore}"

BEGIN_TOKEN="FOUNDRY-RUNTIME-GITIGNORE-BEGIN"
END_TOKEN="FOUNDRY-RUNTIME-GITIGNORE-END"
SENTINEL_TOKEN="FOUNDRY-RUNTIME-GITIGNORE-"
BEGIN_LINE="# ${BEGIN_TOKEN} (managed by scripts/foundry-apply-runtime-gitignore.sh -- do not edit by hand)"
END_LINE="# ${END_TOKEN} (re-run the applier to converge; do not edit by hand)"
SYNTH_COMMENT="# deviation from the shipped .foundry/ runtime-partition default (scripts/foundry-apply-runtime-gitignore.sh)"

KNOWN_BAD_SHAS_RELPATH=".foundry/leak-scan/known-bad-shas.txt"

# The designed-tracked set (Terminology, spec): exactly these three, root-relative, and no others.
MEMBERS=(".foundry/README.md" ".foundry/build-provenance.yaml" ".foundry/stack-profile.lock")

die() { printf 'foundry-apply-runtime-gitignore: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: foundry-apply-runtime-gitignore.sh <repo-root>
       foundry-apply-runtime-gitignore.sh --help

Installs/converges the FOUNDRY-RUNTIME-GITIGNORE managed block (default-deny for `.foundry/`
runtime partitions, re-including the designed-tracked set) into <repo-root>/.gitignore, and stubs
<repo-root>/.foundry/leak-scan/known-bad-shas.txt if absent. Exits non-zero (no write) on a
malformed fragment or a malformed sentinel state in the target file.
EOF
}

# --- small array-membership helper (bash 3.2 has no associative arrays) ---
contains_idx() {  # contains_idx VALUE [array elements...]
  local val="$1"; shift
  local x
  for x in "$@"; do
    [ "$x" = "$val" ] && return 0
  done
  return 1
}

# --- fragment: load + validate (AC-RGLS-3a) ---------------------------------------------------
load_fragment() {
  FRAG_LINES=()
  if [ ! -f "$FRAGMENT_PATH" ]; then
    FRAG_ERROR="fragment not found: $FRAGMENT_PATH"
    return 1
  fi
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    FRAG_LINES+=("$line")
  done < "$FRAGMENT_PATH"
  return 0
}

validate_fragment_lines() {
  local deny_count=0 ln
  for ln in "${FRAG_LINES[@]}"; do
    case "$ln" in
      *"$SENTINEL_TOKEN"*)
        FRAG_ERROR="fragment line contains the sentinel token: $ln"
        return 1
        ;;
    esac
    case "$ln" in
      '') continue ;;
      '#'*) continue ;;
      '!'*)
        case "$ln" in
          '!/.foundry/'*) : ;;
          *)
            FRAG_ERROR="fragment re-include line not root-anchored under /.foundry/: $ln"
            return 1
            ;;
        esac
        ;;
      *)
        deny_count=$((deny_count + 1))
        if [ "$ln" != "/.foundry/*" ]; then
          FRAG_ERROR="fragment has an unexpected non-comment/non-re-include line (want exactly /.foundry/*): $ln"
          return 1
        fi
        ;;
    esac
  done
  if [ "$deny_count" -ne 1 ]; then
    FRAG_ERROR="fragment must contain exactly one deny line (/.foundry/*); found $deny_count"
    return 1
  fi
  return 0
}

# --- target .gitignore: load, scan sentinels, detect malformed state (AC-RGLS-3c) --------------
load_target() {
  local target="$1"
  TARGET_LINES=()
  if [ -e "$target" ] && [ -L "$target" ]; then
    die "refusing: $target is a symlink"
  fi
  if [ -f "$target" ]; then
    local line
    while IFS= read -r line || [ -n "$line" ]; do
      TARGET_LINES+=("$line")
    done < "$target"
  fi
}

scan_sentinels() {
  BLOCK_BEGIN=()
  BLOCK_END=()
  local i=0 open=-1 n=${#TARGET_LINES[@]}
  while [ "$i" -lt "$n" ]; do
    local ln="${TARGET_LINES[$i]}" is_begin=0 is_end=0
    case "$ln" in *"$BEGIN_TOKEN"*) is_begin=1 ;; esac
    case "$ln" in *"$END_TOKEN"*) is_end=1 ;; esac
    if [ "$is_begin" -eq 1 ] && [ "$is_end" -eq 1 ]; then
      die "malformed sentinel state: line $((i + 1)) carries both the BEGIN and END tokens: $ln"
    fi
    if [ "$is_begin" -eq 1 ]; then
      if [ "$open" -ge 0 ]; then
        die "malformed sentinel state: BEGIN sentinel at line $((open + 1)) has no matching END (another BEGIN found at line $((i + 1)) first)"
      fi
      open=$i
    elif [ "$is_end" -eq 1 ]; then
      if [ "$open" -lt 0 ]; then
        die "malformed sentinel state: END sentinel at line $((i + 1)) precedes the first BEGIN sentinel"
      fi
      BLOCK_BEGIN+=("$open")
      BLOCK_END+=("$i")
      open=-1
    fi
    i=$((i + 1))
  done
  if [ "$open" -ge 0 ]; then
    die "malformed sentinel state: BEGIN sentinel at line $((open + 1)) has no matching END sentinel"
  fi
}

in_block() {  # in_block INDEX
  local idx="$1" k=0 n=${#BLOCK_BEGIN[@]}
  while [ "$k" -lt "$n" ]; do
    if [ "$idx" -ge "${BLOCK_BEGIN[$k]}" ] && [ "$idx" -le "${BLOCK_END[$k]}" ]; then
      return 0
    fi
    k=$((k + 1))
  done
  return 1
}

# --- does a non-comment, non-re-include line ignore the `.foundry/` DIRECTORY itself? -----------
# Risk #10: git cannot re-include a file whose parent directory is excluded, REGARDLESS of the
# excluding rule's position relative to the block's `!` lines (this is a structural git property,
# not an ordering one). So a bare directory-form exclusion of `.foundry` (with or without a leading
# `/` and/or a trailing `/`) makes every re-include in the appended block permanently INERT no
# matter where it sits. This is deliberately narrower than the wildcard-glob check below (a
# `/.foundry/*`-style pattern, which is exactly the block's OWN shape, excludes each CHILD -- not
# the parent directory entry -- so re-includes for other children still work; only the bare
# directory form is structurally fatal).
is_foundry_directory_exclusion_line() {
  local raw="$1" pat
  case "$raw" in
    '!'* | '#'*) return 1 ;;
  esac
  pat="$raw"
  case "$pat" in
    /*) pat="${pat#/}" ;;
  esac
  case "$pat" in
    .foundry | .foundry/) return 0 ;;
  esac
  return 1
}

# --- classify a non-comment, non-re-include line against the designed-tracked member set --------
# Return via $?: 0 = matches a member SPECIFICALLY (safe to relocate) ; 1 = no match at all
# (an unrelated adopter rule, left alone) ; 2 = matches a member but is NOT specific to it (Risk #9).
#
# "Specific" means: the pattern's possible match set is exactly the one member path. That requires
# BOTH (a) no glob metacharacter (`*`, `?`, `[`) -- a wildcard can also match other, unrelated
# files sharing the same shape (an adopter's blanket `*.lock`/`*.yaml`/`*.md`/`*.pin` ignore is
# exactly this) -- AND (b) the pattern is PATH-ANCHORED (contains a `/`, so gitignore matches it
# only at the anchored location) -- a bare basename with no `/` (even a literal, non-wildcard one
# like `README.md`) matches at ANY depth, so it can also match unrelated files elsewhere in the
# tree (this repository's own top-level README.md, for one).
#
# A match that fails either condition is NOT relocated -- relocating it would move a rule that also
# governs unrelated paths to a new position, and since gitignore is last-match-wins, that can
# silently change ignore status for those unrelated paths too (e.g. defeating a `!` re-include the
# adopter placed after it, for a completely different file). Such a line is refused, naming it,
# rather than silently relocated.
classify_designed_member_line() {
  local raw="$1" pat m base has_glob=0 anchored=0
  case "$raw" in
    '!'* | '#'*) return 1 ;;
  esac
  pat="$raw"
  case "$pat" in
    /*) pat="${pat#/}" ;;
  esac
  case "$pat" in
    */) return 1 ;;  # a dir-only pattern can't literally equal one of our (file) members
  esac
  [ -n "$pat" ] || return 1

  case "$pat" in
    *'*'* | *'?'* | *'['*) has_glob=1 ;;
  esac
  case "$pat" in
    */*) anchored=1 ;;
  esac

  if [ "$has_glob" -eq 1 ]; then
    for m in "${MEMBERS[@]}"; do
      if [ "$anchored" -eq 1 ]; then
        case "$m" in
          $pat) return 2 ;;
        esac
      else
        base="${m##*/}"
        case "$base" in
          $pat) return 2 ;;
        esac
      fi
    done
    return 1
  fi

  # literal (no glob metacharacters)
  for m in "${MEMBERS[@]}"; do
    if [ "$anchored" -eq 1 ]; then
      [ "$pat" = "$m" ] && return 0
    else
      base="${m##*/}"
      [ "$pat" = "$base" ] && return 2  # literal but non-anchored: also matches basenames elsewhere
    fi
  done
  return 1
}

# --- write the converged .gitignore atomically, same-directory temp + rename (mode-preserving) --
write_output() {
  local target="$1" dir tmp mode="" o
  dir="$(dirname "$target")"
  tmp="$(mktemp "$dir/.foundry-runtime-gitignore.XXXXXX")" || die "could not create a temp file in $dir"
  if [ -f "$target" ]; then
    mode="$(stat -c '%a' "$target" 2>/dev/null || stat -f '%Lp' "$target" 2>/dev/null || true)"
  fi
  : > "$tmp"
  for o in "${OUT[@]}"; do
    printf '%s\n' "$o" >> "$tmp"
  done
  if [ -n "$mode" ]; then
    chmod "$mode" "$tmp" 2>/dev/null || true
  fi
  mv -f "$tmp" "$target"
}

# --- the whole apply, over an already-resolved <repo-root> ---------------------------------------
apply() {
  local root="$1" target="$root/.gitignore"

  load_fragment || die "invalid fragment ($FRAGMENT_PATH): $FRAG_ERROR"
  validate_fragment_lines || die "invalid fragment ($FRAGMENT_PATH): $FRAG_ERROR"

  load_target "$target"
  scan_sentinels   # dies (no write) on any malformed sentinel state

  local n=${#TARGET_LINES[@]} last_end=-1
  if [ "${#BLOCK_END[@]}" -gt 0 ]; then
    last_end="${BLOCK_END[$((${#BLOCK_END[@]} - 1))]}"
  fi

  # Scan every line OUTSIDE any block for a pattern matching a designed-tracked member
  # (AC-RGLS-3d). Already-valid deviation lines (positioned after the last END, immediately
  # preceded by a comment) are left untouched -- this is what makes a converged repo idempotent
  # across re-runs. Everything else matching is relocated: its own immediately-preceding
  # contiguous comment lines travel with it (or a synthesized comment is supplied), and it is
  # re-emitted as a deviation line after the (possibly newly-inserted) END sentinel.
  CONFLICT_COMMENT_STARTS=()
  CONFLICT_PATTERN_IDX=()
  REMOVE_IDX=()
  SYNTH_BEFORE_IDX=()

  local i=0
  while [ "$i" -lt "$n" ]; do
    if in_block "$i"; then i=$((i + 1)); continue; fi
    local ln="${TARGET_LINES[$i]}"
    case "$ln" in
      '' | '#'* | '!'*) i=$((i + 1)); continue ;;
    esac

    # Risk #10: a bare directory-form exclusion of `.foundry` makes every re-include in the
    # appended block structurally inert, regardless of order -- refuse rather than report a
    # convergence the applier knows to be dead on arrival.
    if is_foundry_directory_exclusion_line "$ln"; then
      die "refusing: line $((i + 1)) ignores the .foundry directory itself, which makes every re-include in the managed block permanently inert (git cannot re-include a file whose parent directory is excluded), regardless of this line's position: $ln"
    fi

    local classification=0
    classify_designed_member_line "$ln" && classification=0 || classification=$?
    if [ "$classification" -eq 2 ]; then
      die "refusing: line $((i + 1)) matches a designed-tracked member via a wildcard or a non-anchored (bare-basename) pattern, which would also match unrelated paths elsewhere in the tree -- relocating it would move its position and could silently change ignore status for those unrelated paths too (gitignore is last-match-wins): $ln"
    fi
    if [ "$classification" -eq 0 ]; then
      if [ "$last_end" -ge 0 ] && [ "$i" -gt "$last_end" ]; then
        local prev_is_comment=0
        if [ "$i" -gt 0 ]; then
          case "${TARGET_LINES[$((i - 1))]}" in
            '#'*) prev_is_comment=1 ;;
          esac
        fi
        if [ "$prev_is_comment" -eq 0 ]; then
          SYNTH_BEFORE_IDX+=("$i")
        fi
      else
        local j=$((i - 1)) start=$i
        while [ "$j" -ge 0 ]; do
          if in_block "$j"; then break; fi
          case "${TARGET_LINES[$j]}" in
            '#'*) start=$j; j=$((j - 1)) ;;
            *) break ;;
          esac
        done
        CONFLICT_COMMENT_STARTS+=("$start")
        CONFLICT_PATTERN_IDX+=("$i")
        local k=$start
        while [ "$k" -le "$i" ]; do
          REMOVE_IDX+=("$k")
          k=$((k + 1))
        done
      fi
    fi
    i=$((i + 1))
  done

  # Assemble OUT: the new canonical block replaces the first pre-existing block's position (or is
  # appended once, if none existed); every non-block, non-relocated line is copied through
  # unchanged and in order (AC-RGLS-3b); relocated conflicts are appended, each as a deviation
  # line after the block, carrying (or synthesizing) its rationale comment (AC-RGLS-3d).
  OUT=()
  local anchor_orig=-1
  if [ "${#BLOCK_BEGIN[@]}" -gt 0 ]; then
    anchor_orig="${BLOCK_BEGIN[0]}"
  fi

  local inserted=0 fl
  i=0
  while [ "$i" -lt "$n" ]; do
    if [ "$anchor_orig" -ge 0 ] && [ "$i" -eq "$anchor_orig" ] && [ "$inserted" -eq 0 ]; then
      OUT+=("$BEGIN_LINE")
      for fl in "${FRAG_LINES[@]}"; do OUT+=("$fl"); done
      OUT+=("$END_LINE")
      inserted=1
    fi
    if in_block "$i"; then i=$((i + 1)); continue; fi
    if contains_idx "$i" "${REMOVE_IDX[@]}"; then i=$((i + 1)); continue; fi
    if contains_idx "$i" "${SYNTH_BEFORE_IDX[@]}"; then
      OUT+=("$SYNTH_COMMENT")
    fi
    OUT+=("${TARGET_LINES[$i]}")
    i=$((i + 1))
  done
  if [ "$inserted" -eq 0 ]; then
    OUT+=("$BEGIN_LINE")
    for fl in "${FRAG_LINES[@]}"; do OUT+=("$fl"); done
    OUT+=("$END_LINE")
    inserted=1
  fi

  local ci=0 cn=${#CONFLICT_PATTERN_IDX[@]}
  while [ "$ci" -lt "$cn" ]; do
    local cstart="${CONFLICT_COMMENT_STARTS[$ci]}" cpat="${CONFLICT_PATTERN_IDX[$ci]}"
    OUT+=("")
    if [ "$cstart" -lt "$cpat" ]; then
      local k=$cstart
      while [ "$k" -lt "$cpat" ]; do
        OUT+=("${TARGET_LINES[$k]}")
        k=$((k + 1))
      done
    else
      OUT+=("$SYNTH_COMMENT")
    fi
    OUT+=("${TARGET_LINES[$cpat]}")
    ci=$((ci + 1))
  done

  write_output "$target"
}

stub_known_bad_shas() {
  local root="$1" path="$root/$KNOWN_BAD_SHAS_RELPATH"
  [ -f "$path" ] && return 0
  mkdir -p "$(dirname "$path")"
  cat > "$path" <<'EOF'
# Recorded-bad SHAs for scripts/foundry-prepublication-leak-scan.py's remote-probe scope.
#
# One 40-hex git object name per line. Blank lines and full-line `#` comments are ignored; any
# other non-conforming line is treated as an error by the scan (fail-closed), never silently
# skipped. This file lives inside the ignored .foundry/ runtime partition and is read at runtime,
# never tracked: a committed list of exactly the object names that recover previously-leaked
# material would itself be a recovery index published with the repository.
#
# Add a line here whenever a leak is discovered and its old object name is known (for example a
# pre-rewrite commit or blob SHA that a rewritten-away history might still let a remote serve).
# An empty (but present) file is a valid statement: nothing is currently recorded as bad.
EOF
}

main() {
  local root="$1"
  [ -d "$root" ] || die "not a directory: $root"
  apply "$root"
  stub_known_bad_shas "$root"
  printf 'foundry-apply-runtime-gitignore: converged %s/.gitignore\n' "$root"
}

case "${1:-}" in
  -h | --help) usage; exit 0 ;;
  '') usage >&2; die "expected exactly one argument: <repo-root>" ;;
esac
[ $# -eq 1 ] || die "expected exactly one argument: <repo-root> (got $#)"

main "$1"
