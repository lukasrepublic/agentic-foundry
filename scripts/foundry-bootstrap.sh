#!/usr/bin/env bash
# foundry-bootstrap — the PHYSICAL prelude for a new adopter, as two independently runnable steps
# plus their composition (the historically documented one-command flow):
#
#   toolchain-install    machine/user scope. Adds the plugin marketplace + installs the plugin.
#                         No target directory. Touches no project. Needs only the Claude Code CLI.
#   project-scaffold      per-project scope, takes <target-dir>. Clones/adopts the repo, applies
#                         the runtime-partition .gitignore, seeds the operator registry, the gh
#                         identity jail and the commit-identity wiring, then hands off. REFUSES,
#                         before writing anything, unless toolchain-install has already run (this
#                         process, or a positive plugin-inventory probe of the declared
#                         marketplace) — the toolchain-affirmation.
#
# `/foundry:init` does the LOGICAL wiring (operator registry, env/identity mapping, app-exercise
# binding, doctor) — but it presupposes a repo that already exists, has the plugin installed, and
# is open in a `claude` session. This script automates everything up to that point, then hands off.
#
# Usage:
#   foundry-bootstrap.sh toolchain-install [--marketplace <owner/repo>] [--ref <ref>] [--channel stable|edge] [--dry-run]
#   foundry-bootstrap.sh project-scaffold <target-dir> [options] [--dry-run]
#   foundry-bootstrap.sh <target-dir> [options]   # combined form: toolchain-install THEN project-scaffold, composed in that order
#   foundry-bootstrap.sh --selftest               # hermetic; no network/claude/gh needed
#   foundry-bootstrap.sh --help
#
# Options (project-scaffold + the combined form, except --marketplace/--ref/--channel which every
# shape carrying the plugin-install step shares):
#   --template <owner/repo>     workspace template to clone (default: lukasrepublic/agentic-handbook)
#   --marketplace <owner/repo>  foundry plugin marketplace  (default: lukasrepublic/agentic-foundry)
#                               — toolchain-install's operations, project-scaffold's precondition
#                               probe, and the combined form all derive from this ONE declared
#                               value; there is no second default anywhere else in the script.
#   --ref <ref>                 pin the marketplace add to an explicit ref (tag or branch);
#                               overrides --channel for this invocation
#   --channel <stable|edge>     stable (the default): the shipped PINNED release tag. edge: the
#                               plugin repository's default-branch catalogue — UNSTABLE and
#                               UNPINNED, an explicit opt-in only, never the default; warns on
#                               stderr when selected. Byte-exact, no case-folding.
#   --template-ssh               clone the workspace template over SSH (git@github.com:) instead
#                               of the default anonymous, prompt-disabled https:// clone
#   --operator <id>             seed .claude/foundry-operators.json (replace op_example; requires an existing registry in the target)
#   --gh-account <name>         scaffold the per-project gh identity jail (.claude/gh-identity + .envrc)
#                               + wire NATIVE commit-identity isolation (git includeIf + useConfigOnly)
#   --git-author "Name <email>" pinned commit-identity source for the includeIf (else gh api user, else
#                               an interactive TTY prompt, else fail-closed); requires --gh-account to wire
#   --existing                  <target-dir> is an existing repo; skip the template clone
#   --dry-run                   print the plan; make NO changes (and skip the tool preflight)
#
# Idempotent: re-running is safe (plugin add/install already-present is not an error).
# Fail-closed preflight on the real path; the LOGICAL gate is still /foundry:doctor.
# BOOTSTRAP-USAGE-END
set -euo pipefail

DEFAULT_TEMPLATE="lukasrepublic/agentic-handbook"
DEFAULT_MARKETPLACE="lukasrepublic/agentic-foundry"
DEFAULT_MARKETPLACE_REF="v1.3.0"  # the pinned STABLE default; maintained by the release cut —
                                    # tests/test_bootstrap_install_pin.py asserts it equals
                                    # .claude-plugin/marketplace.json plugins[foundry].source.ref
EDGE_MARKETPLACE_REF="main"        # the plugin repository's default branch: the opt-in, UNSTABLE channel

TEMPLATE="$DEFAULT_TEMPLATE"
MARKETPLACE="$DEFAULT_MARKETPLACE"
OPERATOR=""
GH_ACCOUNT=""
GIT_AUTHOR=""
EXISTING=0
DRY_RUN=0
TARGET=""
STEP="combined"          # toolchain | scaffold | combined — set by parse_args's leading token

REF_OPT=""                 # raw --ref value, pre-validation
CHANNEL_OPT=""              # raw --channel value, pre-validation
TEMPLATE_SSH=0              # --template-ssh flag: SSH template clone instead of the https default
RESOLVED_REF=""             # the resolved marketplace ref (Terminology) — set once, by validate_arguments
RESOLVED_CHANNEL=""         # stable | edge | explicit (Terminology) — set once, by validate_arguments
PINNED_SOURCE=""            # "<marketplace>#<resolved ref>" — the pinned source argument (Terminology)

TOOLCHAIN_AFFIRMED=0      # source (A) of the toolchain-affirmation — process-local: never
                           # written to disk, never exported, never visible to a later invocation.

GH_SLUG=""                 # the validated-value global (Terminology, AC-BENV-9): normalize_account_slug's
                           # output for --gh-account, set ONCE by validate_arguments. Declared here for
                           # `set -u`; every later step reads this rather than recomputing the slug.
ENVRC_CHANGED=0            # whether append_wiring_stanza actually wrote $TARGET/.envrc THIS run
                           # (AC-BENV-5's report_direnv_status reads this to decide whether a
                           # `direnv allow` disclosure is warranted).

# The direnv wiring stanza (Terminology): exactly one #-comment line naming the factory, followed
# by the dispatch verb `use foundry_gh`. Deliberately contains neither "foundry_gh" nor "foundry.sh"
# in its OWN comment text, so re-scanning a converged .envrc on a later run never mistakes the
# comment line itself for an ambiguous reference (AC-BENV-3).
DIRENV_WIRING_COMMENT="# foundry — direnv dispatch verb for this project's gh identity isolation (run: direnv allow)"
DIRENV_DISPATCH_VERB="use foundry_gh"

log()  { printf '  • %s\n' "$*"; }
plan() { printf '  [plan] %s\n' "$*"; }
die()  { printf 'foundry-bootstrap: %s\n' "$1" >&2; exit "${2:-1}"; }

usage() {
  local src="${BASH_SOURCE[0]}"
  # Fail-closed guard: `sed -n '2,/pat/p'` with NO match for `pat` prints from line 2 to EOF (the
  # WHOLE script, function bodies included) rather than erroring -- if this sentinel is ever
  # deleted or renamed, dump the whole file's help text silently. Refuse instead of doing that.
  grep -q '^# BOOTSTRAP-USAGE-END' "$src" \
    || die "internal error: usage() sentinel '# BOOTSTRAP-USAGE-END' missing from $src (refusing to print the whole script as help text)" 1
  sed -n '2,/^# BOOTSTRAP-USAGE-END/p' "$src" | sed '$d' | sed 's/^# \{0,1\}//'
}

# run CMD... — execute, or (in dry-run) just print it
run() {
  if [ "$DRY_RUN" -eq 1 ]; then plan "$*"; else "$@"; fi
}

parse_args() {
  if [ $# -gt 0 ]; then
    case "$1" in
      toolchain-install) STEP="toolchain"; shift ;;
      project-scaffold)  STEP="scaffold";  shift ;;
    esac
  fi
  while [ $# -gt 0 ]; do
    case "$1" in
      --template)    TEMPLATE="${2:?--template needs a value}"; shift 2 ;;
      --marketplace) MARKETPLACE="${2:?--marketplace needs a value}"; shift 2 ;;
      --ref)         REF_OPT="${2:?--ref needs a value}"; shift 2 ;;
      --channel)     CHANNEL_OPT="${2:?--channel needs a value}"; shift 2 ;;
      --template-ssh) TEMPLATE_SSH=1; shift ;;
      --operator)    OPERATOR="${2:?--operator needs a value}"; shift 2 ;;
      --gh-account)  GH_ACCOUNT="${2:?--gh-account needs a value}"; shift 2 ;;
      --git-author)  GIT_AUTHOR="${2:?--git-author needs a value}"; shift 2 ;;
      --existing)    EXISTING=1; shift ;;
      --dry-run)     DRY_RUN=1; shift ;;
      -h|--help)     usage; exit 0 ;;
      -*)            die "unknown option: $1" 2 ;;
      toolchain-install|project-scaffold)
        # The step selector is only recognized as the LEADING token (above). Anywhere else in the
        # argument list it would otherwise fall through here and be silently adopted as TARGET —
        # `--marketplace acme/x toolchain-install` would then run a FULL combined-form scaffold
        # into a literal directory named `toolchain-install`. Refuse instead, naming the fix.
        die "the step name '$1' must come first: foundry-bootstrap.sh $1 [options]" 2
        ;;
      *)             [ -z "$TARGET" ] || die "unexpected extra argument: $1" 2; TARGET="$1"; shift ;;
    esac
  done
}

# The accepted ref grammar (Terminology, AC-BIP-1/AC-BIP-5): ^[A-Za-z0-9][A-Za-z0-9._/-]*$ with no
# '..' substring. Admits tags and branch names; excludes whitespace, '#', ';', '$', backtick and
# every other character that would change the meaning of the pinned source argument it is composed
# into (MARKETPLACE#REF). Prints nothing; returns non-zero on a value outside the grammar.
is_accepted_ref() {
  local v="$1"
  [ -n "$v" ] || return 1
  # Checked for an embedded newline FIRST and separately (same hazard, same fix, as --operator's
  # validator above): `grep -E '^...$'` matches PER LINE, so a multi-line value could otherwise
  # slip a malicious trailing line past the anchored regex below by having ANY one of its lines
  # satisfy the pattern on its own — forging extra `[plan]`-shaped lines in the dry-run plan and
  # the resolved-channel disclosure (AC-BIP-10), a whitespace value the accepted ref grammar is
  # supposed to exclude outright.
  case "$v" in
    *$'\n'*) return 1 ;;
  esac
  case "$v" in
    *..*) return 1 ;;
  esac
  printf '%s' "$v" | LC_ALL=C grep -Eq '^[A-Za-z0-9][A-Za-z0-9._/-]*$'
}

# The argument-validation function (Terminology, AC-BENV-6/AC-BENV-7): called from main() exactly
# once, immediately after parse_args and before ANY step that could write anything — the
# machine-scope lib install and obtain_repo included. The CLOSED validated set is exactly these
# three options (never --marketplace/--template, which belong to the sibling bootstrap-install-pin
# atom): GH_ACCOUNT (normalize_account_slug, into GH_SLUG — the single derivation every later step
# reads), GIT_AUTHOR ("Name <email>" grammar), OPERATOR (shell-inert allowlist). A rejection here
# exits before anything has been created or modified, inside or outside the target project.
validate_arguments() {
  if [ -n "$GH_ACCOUNT" ]; then
    GH_SLUG="$(normalize_account_slug "$GH_ACCOUNT")" \
      || die "invalid --gh-account slug (need ^[A-Za-z0-9._-]+\$, not '.'/'..'): $GH_ACCOUNT" 6
  fi

  if [ -n "$GIT_AUTHOR" ]; then
    case "$GIT_AUTHOR" in
      *" <"*">") : ;;
      *) die "malformed --git-author (expected 'Name <email>'): $GIT_AUTHOR" 6 ;;
    esac
  fi

  if [ -n "$OPERATOR" ]; then
    # Shell-inert (Terminology): composed solely of ^[A-Za-z0-9._-]+$, and never '.'/'..'. Checked
    # for an embedded newline FIRST and separately — `grep -E '^...$'` matches per LINE, so a
    # multi-line value could otherwise slip a malicious trailing line past the anchored regex below
    # by having ANY one of its lines satisfy the pattern on its own.
    case "$OPERATOR" in
      *$'\n'*) die "invalid --operator (must not contain a newline)" 6 ;;
    esac
    case "$OPERATOR" in
      .|..) die "invalid --operator (need ^[A-Za-z0-9._-]+\$, not '.'/'..'): $OPERATOR" 6 ;;
    esac
    printf '%s' "$OPERATOR" | LC_ALL=C grep -Eq '^[A-Za-z0-9._-]+$' \
      || die "invalid --operator (need ^[A-Za-z0-9._-]+\$, not '.'/'..'): $OPERATOR" 6
  fi

  # ── the pin resolution (Terminology, AC-BIP-1..7) ───────────────────────────────────────────
  # Resolved EXACTLY ONCE, here, before any write (AC-BIP-5's "no partially-scaffolded project"
  # holds because this runs before install_direnv_lib/obtain_repo). An unusable pin selector
  # (Terminology) fails closed: an empty/out-of-grammar --ref, a --channel outside {stable,edge}
  # (byte-exact, no case-folding), or a '#' inside --marketplace (which would smuggle a second ref
  # past the "${MARKETPLACE}#${REF}" composition below — AC-BIP-7).
  case "$MARKETPLACE" in
    *'#'*) die "invalid --marketplace (must not contain '#'): $MARKETPLACE" 6 ;;
  esac

  if [ -n "$CHANNEL_OPT" ]; then
    case "$CHANNEL_OPT" in
      stable|edge) : ;;
      *) die "invalid --channel (must be exactly 'stable' or 'edge', case-sensitive): $CHANNEL_OPT" 6 ;;
    esac
  fi

  if [ -n "$REF_OPT" ]; then
    is_accepted_ref "$REF_OPT" \
      || die "invalid --ref (need ^[A-Za-z0-9][A-Za-z0-9._/-]*\$, no '..'): $REF_OPT" 6
    RESOLVED_REF="$REF_OPT"
    RESOLVED_CHANNEL="explicit"
  elif [ "$CHANNEL_OPT" = "edge" ]; then
    RESOLVED_REF="$EDGE_MARKETPLACE_REF"
    RESOLVED_CHANNEL="edge"
  else
    RESOLVED_REF="$DEFAULT_MARKETPLACE_REF"
    RESOLVED_CHANNEL="stable"
  fi
  PINNED_SOURCE="${MARKETPLACE}#${RESOLVED_REF}"

  # AC-BIP-6(c): the UNSTABLE warning fires from THIS resolution point — never from inside the
  # --dry-run branch — so it is identical on the dry-run plan and the real path (AC-BIP-14(b)).
  if [ "$RESOLVED_CHANNEL" = "edge" ]; then
    printf 'foundry-bootstrap: WARNING: --channel edge selects the UNSTABLE, unpinned ref %s -- see --help\n' "$RESOLVED_REF" >&2
  fi
}

# Step — obtain the repo
obtain_repo() {
  if [ "$EXISTING" -eq 1 ]; then
    if [ "$DRY_RUN" -eq 1 ]; then plan "use existing repo at $TARGET (skip clone)"; return; fi
    [ -d "$TARGET/.git" ] || die "--existing given but $TARGET is not a git repo" 4
    log "using existing repo at $TARGET"
  else
    if [ "$DRY_RUN" -eq 0 ] && [ -e "$TARGET" ] && [ -n "$(ls -A "$TARGET" 2>/dev/null)" ]; then
      die "$TARGET exists and is non-empty; use --existing to wire an existing repo" 4
    fi
    # AC-BIP-8/AC-BIP-9: unauthenticated by default (no ambient SSH or stored-credential identity
    # attaches) — terminal credential prompting disabled (GIT_TERMINAL_PROMPT=0, so a not-yet-
    # public or absent repository fails fast instead of blocking on a username prompt) AND, since
    # that alone bounds only the terminal prompt (a configured credential helper — osxkeychain /
    # libsecret / store / gh's own helper — would otherwise still silently attach the adopter's
    # ambient GitHub token, and GIT_ASKPASS/core.askPass could still pop a blocking prompt), the
    # clone's own `-c credential.helper=`/`-c core.askPass=` (empty values, applied by `git clone`
    # to the new repo's config immediately after init but BEFORE the remote history is fetched —
    # git's own documented ordering, so this disables both classes for the fetch itself) disable
    # both classes for THIS invocation only — never the adopter's global git config. Placed AFTER
    # the URL/target (git's `-c` here is clone's own option and parses the same regardless of
    # position) so the plan text keeps `git clone https://github.com/...` LITERALLY adjacent, which
    # is what AC-BIP-8's frozen checkpoint locator greps for. --template-ssh is the opt-in back to
    # the ambient-identity SSH form (no claim about ssh's own prompt class — Residuals). `run`
    # prints "$*" in dry-run, so the transport/env prefix and both -c overrides are visible on the
    # clone plan line with no network (AC-BIP-10(d)).
    if [ "$TEMPLATE_SSH" -eq 1 ]; then
      run git clone "git@github.com:${TEMPLATE}.git" "$TARGET"
    else
      run env GIT_TERMINAL_PROMPT=0 git clone "https://github.com/${TEMPLATE}.git" "$TARGET" \
        -c credential.helper= -c core.askPass=
    fi
    [ "$DRY_RUN" -eq 1 ] || log "cloned $TEMPLATE → $TARGET"
  fi
}

# Apply the default-deny `.foundry/` runtime-partition .gitignore (AC-RGLS-4). Runs on the
# target's REAL path (never a dry-run no-op beyond the plan line) as soon as the repo exists,
# before any step below could write a runtime artifact `git add -A` might otherwise sweep up.
apply_runtime_gitignore() {
  local applier
  applier="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/foundry-apply-runtime-gitignore.sh"
  if [ "$DRY_RUN" -eq 1 ]; then
    plan "apply the default-deny .foundry/ runtime-partition .gitignore ($applier $TARGET)"
    return
  fi
  "$applier" "$TARGET"
  log "applied the .foundry/ runtime-partition default-deny .gitignore to $TARGET"
}

# The refuse-don't-merge failure shape (AC-BENV-3): names the offending path, states the reason,
# prints the dispatch verb VERBATIM, and states both remedies — add the line by hand, or remove the
# conflicting reference/symlink and re-run. Shared by every one of the three refusal triggers (an
# ambiguous .envrc, a symlinked .envrc, a symlinked installed lib) so all three carry the identical
# disclosure shape.
refuse_direnv_wiring() {
  local offending="$1" reason="$2"
  die "refusing to wire direnv for $offending: $reason -- add this line to $TARGET/.envrc by hand: $DIRENV_DISPATCH_VERB (or remove the conflicting reference/symlink and re-run)" 8
}

# ── the machine-scope step (Terminology, AC-BENV-1) ─────────────────────────────────────────────
# Installs the factory's direnv lib source BYTE-IDENTICALLY to direnv's own designated per-user
# global-lib scope, $HOME/.config/direnv/lib/foundry.sh (direnv auto-loads every *.sh file there on
# every evaluation and dispatches `use foundry_gh` to the use_foundry_gh function it defines — see
# scripts/foundry-direnv-lib.sh). Whole-file, atomic (a temp file created in the SAME directory,
# renamed over the target), with an already-existing file's permission mode carried across. Refuses
# (AC-BENV-3) rather than overwriting through a symlinked target. Idempotent: the content is always
# the shipped source, so a re-run converges to the same bytes.
install_direnv_lib() {
  local dir="$HOME/.config/direnv/lib" target="$HOME/.config/direnv/lib/foundry.sh" src

  if [ "$DRY_RUN" -eq 1 ]; then
    src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/foundry-direnv-lib.sh"
    plan "install the direnv lib byte-identically to $target ($src, defines use_foundry_gh)"
    return
  fi

  # R4 (accepted residual, security review): this checks only the LEAF ($target) for a symlink --
  # $HOME/.config/direnv or its lib/ subdirectory being a symlink is unchecked and deliberately not
  # refused, since dotfiles-manager tooling legitimately symlinks whole ~/.config subtrees. This is
  # a distinct residual from the leaf's own check-then-write TOCTOU window named in AC-BENV-3's
  # Residuals: that one is a race on THIS check; this one is about a directory ABOVE it entirely.
  if [ -L "$target" ]; then
    refuse_direnv_wiring "$target" "the installed lib path is a symlink"
  fi

  src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/foundry-direnv-lib.sh"
  mkdir -p "$dir"

  local mode=""
  if [ -f "$target" ]; then
    mode="$(stat -c '%a' "$target" 2>/dev/null || stat -f '%Lp' "$target" 2>/dev/null || true)"
  fi

  local tmp
  tmp="$(mktemp "$dir/.foundry-direnv-lib.XXXXXX")" || die "could not create a temp file in $dir" 8
  cp "$src" "$tmp"
  if [ -n "$mode" ]; then
    # R3 security-review remediation: a PLANTED pre-existing file (e.g. world-writable 0666, or
    # carrying a setuid/setgid/sticky bit) must not have those bits carried across under the guise
    # of "preserving the existing mode" -- clamp to at most 0755 (drops group/other WRITE and any
    # setuid/setgid/sticky bit) before applying it to the freshly-installed content. A mode already
    # within that envelope (e.g. the AC-BENV-1 mode-preservation checkpoint's 0640) is unaffected.
    local clamped_mode
    clamped_mode="$(printf '%03o' "$(( 0$mode & 0755 ))" 2>/dev/null || true)"
    if [ -n "$clamped_mode" ]; then
      chmod "$clamped_mode" "$tmp" 2>/dev/null || true
    fi
  fi
  mv -f "$tmp" "$target"
  log "installed the direnv lib to $target"
}

# Append-once with refuse-don't-merge (Terminology, AC-BENV-2/AC-BENV-3) of the wiring stanza into
# $1 (the target's .envrc). Never rewrites, reorders or deletes a pre-existing line; refuses,
# leaving the file byte-identical, on a symlinked target or an ambiguous foundry_gh/foundry.sh
# reference that is not itself the conforming dispatch verb.
append_wiring_stanza() {
  local envrc="$1"
  ENVRC_CHANGED=0

  if [ -L "$envrc" ]; then
    refuse_direnv_wiring "$envrc" "$envrc is a symlink"
  fi

  if [ ! -f "$envrc" ]; then
    printf '%s\n%s\n' "$DIRENV_WIRING_COMMENT" "$DIRENV_DISPATCH_VERB" > "$envrc"
    ENVRC_CHANGED=1
    return 0
  fi

  local conforming=0 ambiguous=0 line trimmed lineno=0 conforming_lineno=0
  while IFS= read -r line || [ -n "$line" ]; do
    lineno=$((lineno + 1))
    trimmed="$(printf '%s' "$line" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    if [ "$trimmed" = "$DIRENV_DISPATCH_VERB" ]; then
      conforming=1
      [ "$conforming_lineno" -eq 0 ] && conforming_lineno="$lineno"
    else
      case "$line" in
        *foundry_gh*|*foundry.sh*) ambiguous=1 ;;
      esac
    fi
  done < "$envrc"

  if [ "$ambiguous" -eq 1 ]; then
    refuse_direnv_wiring "$envrc" "it carries an ambiguous foundry_gh/foundry.sh reference"
  fi

  # R2 (cheap-mitigation, security review): a TEXTUALLY-present-but-context-inert dispatch verb
  # (inside a heredoc, an `if false`, after an unconditional `return`) would make this a wrongful
  # skip -- a full context parse is a spec-level question this atom does not take on, so the skip
  # is made merely OBSERVABLE instead: naming the exact line found, so a wrongful skip is at least
  # visible in the run's own output rather than silently reported as success.
  if [ "$conforming" -eq 1 ]; then
    log "existing conforming reference at $envrc line $conforming_lineno -- not appending"
    return 0
  fi

  # Ensure the append lands on its own line even when the pre-existing file has no trailing
  # newline — every pre-existing line must survive byte-for-byte, so this NEVER edits an existing
  # byte, only decides whether one more newline is needed before the stanza.
  if [ -s "$envrc" ] && [ "$(tail -c1 "$envrc" | wc -l)" -eq 0 ]; then
    printf '\n' >> "$envrc"
  fi
  printf '%s\n%s\n' "$DIRENV_WIRING_COMMENT" "$DIRENV_DISPATCH_VERB" >> "$envrc"
  ENVRC_CHANGED=1
}

# The direnv authorization disclosure (AC-BENV-5): fires only when the scaffold path wires the
# target (--gh-account given). direnv's allow hash covers ONLY .envrc content — never the installed
# lib, never an identity data file — so a run that touched only those needs NO re-authorization and
# must say so plainly, rather than training the adopter to re-run `direnv allow` needlessly.
report_direnv_status() {
  [ -n "$GH_ACCOUNT" ] || return 0
  if [ "$ENVRC_CHANGED" -eq 1 ]; then
    log "wired $TARGET/.envrc for direnv -- run: direnv allow $TARGET"
  else
    log "updated .claude/gh-identity${OPERATOR:+ and .claude/foundry-operator} and the installed direnv lib for '$GH_SLUG' -- no re-authorization needed (.envrc was not modified)"
  fi
}

# ── the toolchain-install step (Terminology) ────────────────────────────────────────────────────
# Machine/user scope: adds the declared marketplace and installs its foundry plugin. Nothing else.
# Needs only the Claude Code CLI (never git/gh — those are project-scaffold prerequisites).
toolchain_install_step() {
  [ "$DRY_RUN" -eq 1 ] || command -v claude >/dev/null 2>&1 || die "claude (Claude Code CLI) not found on PATH" 3
  if [ "$DRY_RUN" -eq 1 ]; then
    # AC-BIP-10(a): one line carrying both the token "channel" and the resolved channel value.
    plan "resolved channel: $RESOLVED_CHANNEL (ref $RESOLVED_REF)"
  fi
  run claude plugin marketplace add "$PINNED_SOURCE"
  run claude plugin install "foundry@${MARKETPLACE##*/}"
  if [ "$DRY_RUN" -eq 1 ]; then
    return
  fi
  TOOLCHAIN_AFFIRMED=1
  log "plugin foundry@${MARKETPLACE##*/} installed (toolchain-install)"
}

# A toolchain-only run has no target: no cd directive, no target path — name the next step instead.
toolchain_only_handoff() {
  [ "$DRY_RUN" -eq 1 ] && return 0
  # printf (a bash builtin), NOT cat: the toolchain-only path must run with ONLY the Claude CLI on
  # PATH (AC-BTSS-1) — an external cat here is an undeclared dependency the restricted-PATH test
  # exposes under CI's isolated python toolcache (locally /usr/bin leaks coreutils and hides it).
  printf '%s\n' \
    '' \
    "✓ toolchain-install complete (marketplace $MARKETPLACE, plugin foundry@${MARKETPLACE##*/})." \
    '' \
    'Next: scaffold a project with' \
    '' \
    '    foundry-bootstrap.sh project-scaffold <target-dir> [options]' \
    ''
}

# ── the toolchain-affirmation (Terminology) ─────────────────────────────────────────────────────
# EXACTLY two sources establish it; every other state is absent — no env var, no marker file, no
# flag, no query that merely exited without contradicting it.

# The toolchain-presence probe: read-only, bounded (<=15s wall-clock; a hang counts as
# unanswered), scoped to the DECLARED marketplace ($MARKETPLACE — no second default here).
# `claude plugin list --json` is a read-only inventory query (no add, no install); its `id` field
# is `<plugin-name>@<marketplace-short-name>`, the SAME short name toolchain_install_step derives
# via ${MARKETPLACE##*/}, so probe-time and install-time are pinned to one value.
#   exit 0 -> a positive match for the declared marketplace (affirms)
#   exit 2 -> a positive name match whose entry names no marketplace at all (affirms, name-only —
#             the caller must warn)
#   exit 1 -> missing query / non-zero exit / empty or unparseable output / no foundry plugin at all
#   exit 3 -> exceeded the 15s bound (a hang)
#   exit 4 -> a foundry plugin was reported, but of a DIFFERENT marketplace
# Exits 1/3/4 are all "absent" to the caller; they are distinguished only for diagnosability.
toolchain_presence_probe() {
  command -v claude >/dev/null 2>&1 || return 1

  local out_file deadline pid rc short
  out_file="$(mktemp)" || return 1
  claude plugin list --json >"$out_file" 2>/dev/null &
  pid=$!
  deadline=$(( $(date +%s) + 15 ))
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
      kill "$pid" 2>/dev/null
      wait "$pid" 2>/dev/null || true
      rm -f "$out_file"
      return 3
    fi
    sleep 0.2
  done
  rc=0
  wait "$pid" || rc=$?
  if [ "$rc" -ne 0 ]; then rm -f "$out_file"; return 1; fi

  short="${MARKETPLACE##*/}"
  rc=0
  python3 - "$out_file" "$short" <<'PY' || rc=$?
import json
import sys

path, short = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if not text.strip():
        sys.exit(1)  # empty output -> absent
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("not a list")
except Exception:
    sys.exit(1)  # unparseable output -> absent

# Precedence (fail-closed direction, deliberate): an exact match affirms immediately; short of
# that, a FOREIGN-marketplace entry outranks a name-only entry -- an inventory carrying BOTH (a
# foundry@some-other-marketplace entry AND a bare-name entry) must refuse (exit 4), never affirm
# name-only (exit 2), because the foreign entry is positive evidence the query CAN report a
# marketplace and this installation's isn't the declared one.
saw_foreign = False
name_only = False
for entry in data:
    if not isinstance(entry, dict):
        continue
    plugin_id = entry.get("id")
    if not isinstance(plugin_id, str) or not plugin_id:
        continue
    if "@" in plugin_id:
        name, _, mkt = plugin_id.partition("@")
    else:
        name, mkt = plugin_id, ""
    if name != "foundry":
        continue
    if not mkt:
        name_only = True
    elif mkt == short:
        sys.exit(0)  # positive match for the declared marketplace
    else:
        saw_foreign = True

if saw_foreign:
    sys.exit(4)
if name_only:
    sys.exit(2)
sys.exit(1)
PY
  rm -f "$out_file"
  return "$rc"
}

# require_toolchain_affirmed checks source (A) first and returns immediately when it is set; only
# a scaffold-only invocation (or a combined-form run whose install failed, which never reaches
# here under `set -e`) reaches the probe.
require_toolchain_affirmed() {
  [ "$TOOLCHAIN_AFFIRMED" -eq 1 ] && return 0
  local probe_rc=0
  toolchain_presence_probe || probe_rc=$?
  case "$probe_rc" in
    0)
      return 0
      ;;
    2)
      printf 'foundry-bootstrap: WARNING: the plugin inventory does not report which marketplace the installed foundry plugin came from; treating the name-only match as affirming the declared marketplace %s.\n' "$MARKETPLACE" >&2
      return 0
      ;;
    *)
      die "toolchain not affirmed for marketplace $MARKETPLACE — run: foundry-bootstrap.sh toolchain-install (or pass the same --marketplace to both steps)" 7
      ;;
  esac
}

preflight_scaffold() {
  command -v git >/dev/null 2>&1 || die "git not found on PATH" 3
  if [ -n "$GH_ACCOUNT" ]; then
    command -v gh >/dev/null 2>&1 || die "gh required for --gh-account but not found on PATH" 3
  fi

  # AC-BEPP-1d: obtain_repo's own --existing/.git check, duplicated HERE at the pre-write point so
  # a non-repository --existing target refuses before install_direnv_lib's machine-scope write
  # instead of after it (Design/notes) -- same message, same exit code (4). obtain_repo keeps its
  # own check as the backstop for the clone path, which never reaches this branch (EXISTING unset).
  if [ "$EXISTING" -eq 1 ]; then
    [ -d "$TARGET/.git" ] || die "--existing given but $TARGET is not a git repo" 4

    # AC-BEPP-1a/1b/1c: the registry precondition (Terminology) -- present, not a symlink, and its
    # content parses as a JSON object -- checked here, before install_direnv_lib, ONLY on the
    # --existing path: the clone path cannot check a registry that does not exist until the clone
    # completes (Out of scope; the clone path keeps its existing late refusal, Residuals).
    # python3's own availability is part of the precondition (Design/notes): refuse for a missing
    # parser in the same exit-3 tool-absence shape the git/gh checks above use, so a run that
    # cannot even PARSE the registry never reaches install_direnv_lib either.
    if [ -n "$OPERATOR" ]; then
      command -v python3 >/dev/null 2>&1 \
        || die "python3 not found on PATH (needed to seed the operator registry)" 3
      local reg="$TARGET/.claude/foundry-operators.json"
      if [ -L "$reg" ]; then
        die "operator registry at $reg is a symlink (refusing to read through it) -- replace it with a regular file (see /foundry:init) and re-run" 5
      fi
      [ -f "$reg" ] || die "operator registry not found at $reg -- run /foundry:init in the target first, or drop --operator" 5
      # The guarded parse (Terminology): read-only, never repairs/rewrites/normalizes the
      # registry it found (Clarifications -- family 2), reports the defect in one line, and never
      # lets a parser exception surface as a traceback.
      local reg_defect
      reg_defect="$(python3 - "$reg" <<'PY'
import json, sys
path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
except Exception as exc:
    print("could not be read (%s)" % exc)
    sys.exit(0)
if not text.strip():
    print("is empty")
    sys.exit(0)
try:
    data = json.loads(text)
except Exception as exc:
    print("is not valid JSON (%s)" % exc)
    sys.exit(0)
if not isinstance(data, dict):
    print("has a top level that is not a JSON object")
PY
)"
      [ -z "$reg_defect" ] || die "operator registry at $reg $reg_defect -- run /foundry:init in the target first, or drop --operator" 5
    fi
  fi
}

# Step — seed the operator registry. An operator id ALREADY present in the registry (a prior run's
# record, possibly hand-edited afterward with a `github`/`added_at` value) is left untouched — only
# a genuinely absent id is seeded from the shipped `op_example` template (AC-BENV-10: a re-run must
# never wipe a value the adopter filled in after the first run).
seed_operator() {
  [ -n "$OPERATOR" ] || return 0
  local reg="$TARGET/.claude/foundry-operators.json"
  if [ "$DRY_RUN" -eq 1 ]; then plan "seed operator '$OPERATOR' into .claude/foundry-operators.json (requires an existing registry in the target)"; return; fi
  # AC-BEPP-1c backstop (security review R1): preflight_scaffold's [ -L ] check only runs on the
  # --existing path, and there is a TOCTOU window between it and this write besides. A symlinked
  # registry reaching THIS point -- the clone path (a hostile --template can ship the registry as a
  # symlink), or a target that became a symlink after preflight ran -- must refuse here too, before
  # [ -f ] (which follows symlinks) ever gets a chance to let a write land through it.
  [ -L "$reg" ] && die "operator registry at $reg is a symlink (refusing to write through it) -- replace it with a regular file" 5
  [ -f "$reg" ] || die "operator registry not found at $reg -- run /foundry:init in the target first, or drop --operator" 5
  OPERATOR="$OPERATOR" python3 - "$reg" <<'PY' || die "operator registry at $reg is unusable (guarded parse failed) -- run /foundry:init in the target first, or drop --operator" 5
import json, os, sys
reg = sys.argv[1]
op = os.environ["OPERATOR"]
try:
    with open(reg, encoding="utf-8") as fh:
        text = fh.read()
    d = json.loads(text)
except Exception as exc:
    sys.stderr.write("guarded parse failed: %s\n" % exc)
    sys.exit(1)
if not isinstance(d, dict):
    sys.stderr.write("guarded parse failed: top level is not a JSON object\n")
    sys.exit(1)
ops = d.setdefault("operators", {})
if op not in ops:
    seed = ops.pop("op_example", {"name": "", "github": "", "added_at": ""})
    ops[op] = {"name": seed.get("name") or op, "github": seed.get("github", ""), "added_at": seed.get("added_at", "")}
json.dump(d, open(reg, "w"), indent=2, ensure_ascii=False)
open(reg, "a").write("\n")
PY
  log "seeded operator '$OPERATOR' (replaced op_example)"
}

# Step — optional gh identity jail (per-project account isolation, AC-BENV-2/AC-BENV-4/AC-BENV-9).
# Writes the two INERT identity data files from the VALIDATED values only (GH_SLUG, never the raw
# GH_ACCOUNT), and applies the direnv wiring stanza — never a whole-file .envrc write; the adopter's
# .envrc gains one stanza and loses nothing (AC-BENV-2's dispatch verb: use foundry_gh).
seed_gh_identity() {
  [ -n "$GH_ACCOUNT" ] || return 0
  if [ "$DRY_RUN" -eq 1 ]; then
    plan "write .claude/gh-identity ($GH_SLUG) + apply the direnv wiring stanza (use foundry_gh) to .envrc"
    if [ -n "$OPERATOR" ]; then
      plan "write .claude/foundry-operator ($OPERATOR)"
    fi
    return 0
  fi
  mkdir -p "$TARGET/.claude"
  printf '%s\n' "$GH_SLUG" > "$TARGET/.claude/gh-identity"
  if [ -n "$OPERATOR" ]; then
    printf '%s\n' "$OPERATOR" > "$TARGET/.claude/foundry-operator"
  fi
  append_wiring_stanza "$TARGET/.envrc"
  log "scaffolded gh identity jail for account '$GH_SLUG'"
  report_direnv_status
}

# ── commit-identity isolation (AC-CIDENT-1 / AC-CIDENT-2) ───────────────────────
# Wire git's NATIVE per-project commit identity (includeIf -> a per-account include file)
# + the commit-time fail-loud (useConfigOnly), so commits under this project are attributed
# to the DECLARED account with no per-repo action and a missing identity FAILS the commit.
# No bespoke file/parser — git's own gitdir: matcher + git's own config writer do the work.

# Shared slug-normalization contract (AC-CIDENT-1 write side; AC-CIDENT-3 read side mirrors it).
# (1) strip leading/trailing whitespace (the .claude/gh-identity marker is printf '%s\n' -> trailing
#     newline; matches the .envrc `tr -d "[:space:]"`); (2) require ^[A-Za-z0-9._-]+$; (3) reject the
#     pure-dot slugs `.` / `..` (no path traversal into identity-<slug>). Prints the normalized slug on
#     success; non-zero (no output) on any violation — BEFORE the slug reaches a path or section name.
normalize_account_slug() {
  local raw="$1" slug
  slug="$(printf '%s' "$raw" | tr -d '[:space:]')"
  [ -n "$slug" ] || return 1
  case "$slug" in
    .|..) return 1 ;;
  esac
  printf '%s' "$slug" | LC_ALL=C grep -Eq '^[A-Za-z0-9._-]+$' || return 1
  printf '%s' "$slug"
}

# Resolve the declared "Name <email>" against the AC-CIDENT-1 order:
#   (1) --git-author "Name <email>"  (primary, pinned; malformed grammar -> fail closed)
#   (2) ONE gh api user call, scoped to the declared jail (GH_CONFIG_DIR=$HOME/.config/gh-<slug>) with
#       GH_TOKEN/GITHUB_TOKEN/GH_ENTERPRISE_TOKEN/GITHUB_ENTERPRISE_TOKEN/GH_HOST removed from the child
#       env (those five outrank whatever the config directory stores, or re-point the call at another
#       host) — trusted only when the probe's .login matches the declared slug (ASCII case-insensitive,
#       LC_ALL=C); a login mismatch, an empty field, or an unframeable payload discards BOTH the name
#       and the email, never a partial adopt
#   (3) interactive TTY prompt        ([ -t 0 ] only)
#   (4) fail closed                   (non-zero, no writes — never an empty/fabricated identity)
# Takes the declared account SLUG (normalize_account_slug's output) as $1 — never the raw --gh-account
# global — so the directory probed is byte-identical to the one the generated .envrc exports. Prints
# "<name>\n<email>" on success (the value-validation guard runs in the caller before any write, which
# also enforces the two-line shape of this output).
#
# Known residual gaps (documented here, not addressed by this atom — tracked separately):
#   (a) the `.login` cross-check below pins the declared PRINCIPAL but not the HOST: a jail whose
#       hosts.yml default resolves to an enterprise instance would resolve there, and an enterprise
#       namesake with a matching login would pass the check.
#   (b) proxy / CA-trust variables (HTTPS_PROXY, https_proxy, ALL_PROXY, CA-bundle env vars) are not
#       stripped from the probe's child environment below and could redirect or observe the
#       authenticated `gh api user` call; TLS bounds this hard, but it is outside this script's control.
resolve_declared_identity() {
  local slug="$1" name="" email="" probe="" login=""
  if [ -n "$GIT_AUTHOR" ]; then
    # Grammar `Name <email>`: everything before the final ` <` is the name; the <…>-bracketed token is
    # the email. A value not matching (no <…>, empty name, or empty email) fails closed.
    case "$GIT_AUTHOR" in
      *" <"*">") : ;;
      *) die "malformed --git-author (expected 'Name <email>'): $GIT_AUTHOR" 6 ;;
    esac
    name="${GIT_AUTHOR% <*}"
    email="${GIT_AUTHOR##* <}"; email="${email%>}"
  else
    # (2) probe gh ONCE, scoped to the DECLARED jail and stripped of every higher-precedence
    #     token/host variable; trust the answer only when .login IS the declared account.
    if command -v gh >/dev/null 2>&1; then
      probe="$(env -u GH_TOKEN -u GITHUB_TOKEN -u GH_ENTERPRISE_TOKEN -u GITHUB_ENTERPRISE_TOKEN \
                 -u GH_HOST GH_CONFIG_DIR="$HOME/.config/gh-$slug" \
                 gh api user --jq '[(.login // ""), (.name // ""), (.email // "")] | @tsv' \
                 2>/dev/null || true)"
      # discard WHOLE unless: exactly one line, exactly three tab-separated fields.
      if [ -n "$probe" ] && [ "$(printf '%s\n' "$probe" | wc -l)" -eq 1 ] \
         && [ "$(printf '%s' "$probe" | tr -cd '\t' | wc -c)" -eq 2 ]; then
        login="$(printf '%s' "$probe" | cut -f1)"
        if [ -n "$login" ] && [ "$(printf '%s' "$login" | LC_ALL=C tr 'A-Z' 'a-z')" \
           = "$(printf '%s' "$slug"  | LC_ALL=C tr 'A-Z' 'a-z')" ]; then
          name="$(printf '%s'  "$probe" | cut -f2)"
          email="$(printf '%s' "$probe" | cut -f3)"
          # an embedded newline/tab escaped by the CLI's own @tsv framing → discard both, never salvage.
          case "$name$email" in *'\n'*|*'\t'*) name=""; email="" ;; esac
        fi
      fi
    fi
    # (3) interactive prompt — ONLY with a TTY (no-TTY: CI/headless/piped/selftest -> skipped -> fail closed).
    if { [ -z "$name" ] || [ -z "$email" ]; } && [ -t 0 ]; then
      [ -n "$name" ]  || { printf 'commit-identity name for account %s: '  "$slug" >&2; IFS= read -r name; }
      [ -n "$email" ] || { printf 'commit-identity email for account %s: ' "$slug" >&2; IFS= read -r email; }
    fi
  fi
  # (4) fail closed if either is still empty (never write an empty/fabricated identity).
  [ -n "$name" ]  || return 1
  [ -n "$email" ] || return 1
  printf '%s\n%s\n' "$name" "$email"
}

# The never-write-empty + injection guard, enforced on EVERY resolution path: name + email must each be
# non-empty + single-line, and the email must contain '@'. (`git config --file user.email ""` exits 0 and
# `set -euo pipefail` will NOT catch it, so this must run BEFORE any write.) Fail closed otherwise.
validate_identity_value() {
  local kind="$1" val="$2"
  [ -n "$val" ] || die "commit-identity $kind is empty (refusing to write a fabricated identity)" 6
  case "$val" in
    *$'\n'*) die "commit-identity $kind is not single-line (refusing to write)" 6 ;;
  esac
  if [ "$kind" = "email" ]; then
    case "$val" in
      *"@"*) : ;;
      *) die "commit-identity email lacks '@': $val (refusing to write)" 6 ;;
    esac
  fi
}

# Step — wire NATIVE commit-identity isolation (fires ONLY when --gh-account is supplied:
# coupling-by-design; a --git-author-only invocation does NOT wire — no slug, no include file).
seed_commit_identity() {
  [ -n "$GH_ACCOUNT" ] || return 0

  # AC-BENV-9: consume the ALREADY-validated slug (set once, by validate_arguments) rather than
  # recomputing it here — one derivation, so the marker, GH_CONFIG_DIR and this include file can
  # never disagree.
  local slug="$GH_SLUG"

  local inc="$HOME/.config/git/identity-$slug"

  if [ "$DRY_RUN" -eq 1 ]; then
    plan "resolve declared identity (--git-author/gh api user/prompt) for account '$slug' (probe jail-scoped to $HOME/.config/gh-$slug)"
    # AC-CIDW-5(e): dry-run must DISCLOSE a pending migration, naming the rule to be deleted — but
    # this branch runs before $TARGET necessarily exists (a fresh non-`--existing` target isn't
    # cloned under --dry-run), so the canonical path — and therefore any real read of the global
    # config — is only computable when $TARGET already exists on disk.
    local dry_canon dry_prefix dry_broad
    if dry_canon="$(cd "$TARGET" 2>/dev/null && pwd -P)/"; then
      case "$(uname -s)" in
        Darwin) dry_prefix="gitdir/i:" ;;
        *)      dry_prefix="gitdir:" ;;
      esac
      dry_broad="${dry_prefix}${dry_canon}"
      plan "write $inc (git config --file user.name/user.email) + wire the narrow global includeIf binding (${dry_prefix}${dry_canon}.git and ${dry_prefix}${dry_canon}.git/) -> include"
      if git config --global --get-all "includeif.${dry_broad}.path" >/dev/null 2>&1; then
        plan "remove the superseded broad includeIf rule for this project (pattern: ${dry_broad}) via git config --global --unset-all"
      fi
    else
      plan "write $inc (git config --file user.name/user.email) + wire the narrow global includeIf binding (<canonical>.git and <canonical>.git/) -> include; migrate any superseded broad rule for this project"
    fi
    plan "set repo-local useConfigOnly=true in $TARGET (commit-time fail-loud)"
    return
  fi

  # AC-CIDENT-2 ordering / AC-CIDW-9: the target must already be a git repo — specifically, its
  # gitdir must be a DIRECTORY — before the per-repo --local write. The narrow binding written
  # below is only correct because the project's gitdir IS <canon>.git; a linked worktree, a
  # `--separate-git-dir` clone, or a submodule AS THE TARGET has `.git` as a FILE, and both narrow
  # entries would go inert (silently: a global [user] block, if present, still resolves — repo-
  # local useConfigOnly does not catch a WRONG identity, only a MISSING one). Already refused two
  # layers upstream by `obtain_repo`'s identical gate; this one makes the precondition explicit
  # rather than incidental (AC-CIDW-9) for any future caller that reaches this function directly.
  [ -d "$TARGET/.git" ] || die "commit-identity isolation needs a git repo at $TARGET (clone/--existing first; \$TARGET/.git must be a directory, not a worktree/separate-git-dir/submodule)" 6

  # Resolve + validate the declared identity (fail-closed on no TTY / no source / empty / bad email).
  local id name email
  id="$(resolve_declared_identity "$slug")" \
    || die "could not resolve a commit identity for '$slug' (give --git-author 'Name <email>', authenticate gh, or run interactively)" 6
  [ "$(printf '%s\n' "$id" | wc -l)" -eq 2 ] \
    || die "malformed resolved identity (refusing to write)" 6
  name="$(printf '%s' "$id" | sed -n '1p')"
  email="$(printf '%s' "$id" | sed -n '2p')"
  validate_identity_value name "$name"
  validate_identity_value email "$email"

  # (b) the per-account include file — the durable DECLARED-identity record, written via git's OWN writer
  # (git quotes/escapes the values; a name/email cannot break the [user] block). AC-CIDENT-3 reads it back.
  mkdir -p "$(dirname "$inc")"
  git config --file "$inc" user.name "$name"
  git config --file "$inc" user.email "$email"

  # (c) THE NARROW BINDING + MIGRATION (feat-foundry-commit-identity-gitdir-scope, AC-CIDW-1..9).
  # Canonicalize the project gitdir (symlink-resolved absolute), unchanged from the shipped
  # implementation. On darwin (case-insensitive volume) use gitdir/i: ; Linux case-sensitive uses
  # gitdir:. THE NARROW BINDING (Terminology) is exactly two entries — the canonical path + `.git`
  # and the canonical path + `.git/` — each written as an idempotent same-key SET (`git config
  # --global`, never `--add`: the two are distinct subsections, which invites the wrong reflex of
  # appending a duplicate per run instead of overwriting). THE SUPERSEDED BROAD RULE (Terminology)
  # is the shipped single-entry pattern this atom replaces — the canonical path with a trailing /
  # and no .git component, which git expands to `**` and matches every repo nested beneath the
  # project (the defect this atom fixes, AC-CIDW-4).
  local canon match_prefix
  canon="$(cd "$TARGET" && pwd -P)/"
  case "$(uname -s)" in
    Darwin) match_prefix="gitdir/i:" ;;
    *)      match_prefix="gitdir:" ;;
  esac

  local broad_subsection narrow1_subsection narrow2_subsection broad_key
  broad_subsection="${match_prefix}${canon}"
  narrow1_subsection="${match_prefix}${canon}.git"
  narrow2_subsection="${match_prefix}${canon}.git/"
  broad_key="includeif.${broad_subsection}.path"

  # ── AC-CIDW-5/8 — enumerate every global includeIf.*.path key ONCE, NUL-delimited
  # (--get-regexp -z --name-only) so a path containing whitespace can never be word-split into the
  # wrong key (AC-CIDW-5b). Every comparison below is against the literal lowercase "includeif." —
  # git LOWERCASES section+key names on read, so comparing against "includeIf." would silently
  # match nothing (AC-CIDW-5a, measured). --name-only never fetches the include-file PATH VALUES —
  # only key names — for any rule but this run's own broad key (unset below); that is what keeps
  # the AC-CIDW-8 report free of the resolved user.email, the personal data this atom exists to
  # stop disclosing. Output is written to a FILE, not captured via `$(...)`: bash silently drops
  # embedded NUL bytes on command-substitution capture, which would corrupt NUL-delimited parsing.
  local enum_file enum_rc=0 other_broad_rules=""
  enum_file="$(mktemp)" || die "mktemp failed while enumerating the global includeIf entries" 6
  git config --global --get-regexp -z --name-only '^includeif\.' >"$enum_file" 2>/dev/null || enum_rc=$?
  if [ "$enum_rc" -ne 0 ] && [ "$enum_rc" -ne 1 ]; then
    rm -f "$enum_file"
    die "failed to enumerate the global includeIf entries (git config exit $enum_rc)" 6
  fi
  local key subsection
  while IFS= read -r -d '' key; do
    case "$key" in
      includeif.*.path) subsection="${key#includeif.}"; subsection="${subsection%.path}" ;;
      *) continue ;;
    esac
    [ "$subsection" = "$broad_subsection" ]   && continue  # this run's own broad rule — handled below
    [ "$subsection" = "$narrow1_subsection" ] && continue
    [ "$subsection" = "$narrow2_subsection" ] && continue
    # "broad-shaped": a gitdir[/i]: pattern ending in / that is NOT a narrow SECOND entry (.git/)
    # belonging to some OTHER project — i.e. exactly the superseded-broad-rule shape, for a
    # canonical path other than this invocation's.
    case "$subsection" in
      gitdir:*/|gitdir/i:*/)
        case "$subsection" in
          *.git/) : ;;  # a different project's narrow second entry — not broad, say nothing
          *) other_broad_rules="${other_broad_rules}${subsection}"$'\n' ;;
        esac
        ;;
    esac
  done < "$enum_file"
  rm -f "$enum_file"

  # ── AC-CIDW-5 — remove the superseded broad rule for THIS canonical path, keyed byte-exact.
  # `--unset-all` (never `--remove-section`, which deletes the WHOLE subsection and — for a bare
  # "includeIf" section with an empty subsection — would resolve to `includeIf.` and wipe every
  # includeIf rule on the machine, measured; and never in-place text editing, which bypasses
  # `.gitconfig.lock` and cannot see git's own escaping of `"`/`\` in subsection names) exits 5
  # when the key is absent — the clean-machine / already-migrated case: SUCCESS. Every OTHER
  # non-zero exit (lock contention, a malformed global config, a read-only $HOME) is FATAL — a
  # blanket `|| true` would swallow all of those and report success with the broad rule still
  # live, the fail-open direction this spec rejects design (B) for.
  local unset_rc=0
  git config --global --unset-all "$broad_key" || unset_rc=$?
  if [ "$unset_rc" -ne 0 ] && [ "$unset_rc" -ne 5 ]; then
    die "failed to remove the superseded broad commit-identity rule for $TARGET (git config --unset-all exit $unset_rc): $broad_key" 6
  fi
  [ "$unset_rc" -eq 0 ] && log "migrated away the superseded broad commit-identity rule for $TARGET (was: $broad_subsection)"

  # ── AC-CIDW-5(d) — re-read the global configuration after removal; refuse if a broad rule for
  # THIS canonical path is somehow still resolvable. A scratch-HOME happy-path test alone can
  # never prove this against a real hand-edited ~/.gitconfig.
  local reread_rc=0
  git config --global --get-all "$broad_key" >/dev/null 2>&1 || reread_rc=$?
  if [ "$reread_rc" -eq 0 ]; then
    die "the superseded broad commit-identity rule for $TARGET survived removal: $broad_key" 6
  elif [ "$reread_rc" -ne 1 ]; then
    die "post-removal re-read of the global includeIf configuration failed unexpectedly (git config exit $reread_rc)" 6
  fi

  # ── AC-CIDW-1/2/3/4/6 — write THE NARROW BINDING: two idempotent same-key sets. The value is
  # written via git's own writer (no raw interpolation).
  git config --global "includeIf.${narrow1_subsection}.path" "$inc"
  git config --global "includeIf.${narrow2_subsection}.path" "$inc"

  # ── AC-CIDW-8 — report OTHER broad rules (a different canonical path) observed in the same
  # enumeration: PATTERN ONLY, never a resolved user.email (never even read here — see above),
  # with a re-run instruction. Never modified by this run — migrating an unrelated project's rule
  # would mean editing global config for a project this invocation was not pointed at.
  if [ -n "$other_broad_rules" ]; then
    local pattern
    while IFS= read -r pattern; do
      [ -n "$pattern" ] || continue
      printf 'foundry-bootstrap: WARNING: another superseded broad commit-identity rule is still live (pattern: %s) -- re-run: foundry-bootstrap.sh <that-project-dir> --existing --gh-account <slug> to migrate it (not modified by this run)\n' "$pattern" >&2
    done <<< "$other_broad_rules"
  fi

  # (a) AC-CIDENT-2 — repo-local useConfigOnly: git refuses to auto-guess an identity and FAILS the commit
  # when none is configured (commit-time fail-loud, per-repo; never --global without explicit opt-in).
  git config --file "$TARGET/.git/config" user.useConfigOnly true

  log "wired commit-identity isolation for '$slug' (includeIf ${narrow1_subsection} + ${narrow2_subsection} -> $inc; repo-local useConfigOnly)"
}

handoff() {
  cat <<EOF

✓ Physical prelude complete. Finish the LOGICAL wiring inside a session:

    cd $TARGET
    claude
    > /foundry:init        # operator registry · env/identity mapping · app-exercise binding
    > /foundry:doctor      # must print DOCTOR-GREEN (the fail-closed go-live gate)

EOF
  # AC-BHT-2/2b: the identity-isolation guidance. This block is the ONLY emission site for it
  # (no second copy exists anywhere else in the script), it sits inside this unconditionally-
  # invoked emitter, and it carries no dry-run-scoped branch -- so the dry-run and real-path
  # output are the same bytes by construction, never by two texts happening to agree.
  # Intent form, not past-tense narration: this text is true on --dry-run (nothing was written
  # yet) AND on an already-converged real run (report_direnv_status above may have just said the
  # .envrc stanza was already conforming and nothing was appended) -- a past-tense "wrote X and
  # wired Y" would contradict that line four lines up on a re-run.
  if [ -n "$GH_ACCOUNT" ]; then
    cat <<EOF2
--gh-account scaffolds the gh identity jail for '$GH_SLUG': $TARGET/.claude/gh-identity, an .envrc direnv stanza, and git-native commit identity (includeIf + repo-local useConfigOnly).
Blocking enforcement of the .claude/gh-identity marker is OPT-IN: it needs a PreToolUse(Bash) guard, and no hook this plugin ships performs it.
If you cloned the default workspace template the guard is already there and already wired; on --existing or a custom --template you wire it yourself.
How: see docs/identity-isolation.md in this foundry plugin's own tree.

EOF2
  fi
}

# ── the project-scaffold step (Terminology) ─────────────────────────────────────────────────────
# Per-project scope, takes $TARGET. Refuses (before any write) unless the toolchain-affirmation is
# established. AC-BTSS-12: apply_runtime_gitignore runs FIRST, before every other operation that
# writes under the target, so a later-added scaffold write can never land before the default-deny
# ignore exists. (Folded into main()'s own case arm below, not a separate function: AC-BENV-6's
# static locator scans main()'s OWN body for a single, literal, ordered mention of
# validate_arguments/install_direnv_lib/obtain_repo — a call nested inside a helper function would
# not be visible to that scan.)
main() {
  parse_args "$@"
  validate_arguments
  case "$STEP" in
    toolchain)
      printf 'foundry-bootstrap%s → toolchain-install\n' "$([ "$DRY_RUN" -eq 1 ] && echo ' (dry-run)')"
      toolchain_install_step
      toolchain_only_handoff
      ;;
    scaffold|combined)
      [ -n "$TARGET" ] || { usage; die "missing <target-dir>" 2; }
      if [ "$STEP" = "combined" ]; then
        printf 'foundry-bootstrap%s → toolchain-install + project-scaffold → %s\n' "$([ "$DRY_RUN" -eq 1 ] && echo ' (dry-run)')" "$TARGET"
        toolchain_install_step
      else
        printf 'foundry-bootstrap%s → project-scaffold → %s\n' "$([ "$DRY_RUN" -eq 1 ] && echo ' (dry-run)')" "$TARGET"
      fi
      if [ "$DRY_RUN" -eq 1 ]; then
        plan "check the toolchain-install precondition (scaffold-needs-toolchain: satisfied in-process by a toolchain-install step that already ran in this invocation, else a bounded <=15s read-only plugin-inventory probe scoped to marketplace $MARKETPLACE)"
      else
        require_toolchain_affirmed
        preflight_scaffold
      fi
      install_direnv_lib
      obtain_repo
      apply_runtime_gitignore
      seed_operator
      seed_gh_identity
      seed_commit_identity
      # AC-BHT-2b: the bare, 2-space-indented, unconditional call below is the locator's
      # structural proof of single-source emission -- never wrapped in an if/case, never a
      # second copy elsewhere in main(). The indentation is checkpoint-shaped, not a style choice
      # (sed -n '/^main/,/^}/p' | grep -Eq '^..handoff$') -- do not reflow it to match its siblings.
  handoff
      ;;
    *)
      die "internal error: unknown step '$STEP'" 1
      ;;
  esac
}

# ── selftest ──────────────────────────────────────────────────────────────────
# Hermetic: exercises arg-parsing + the dry-run plan only. No network, no clone,
# no claude/gh required. Asserts the plan covers every step and leaves no side effects.
selftest() {
  local fails=0 out tmp rc
  check() { if eval "$2"; then echo "  [ok] $1"; else echo "  [XX] $1"; fails=$((fails+1)); fi; }

  # 1. --help exits 0 and prints usage naming both steps -- and stays BOUNDED to the header (a
  # missing/renamed BOOTSTRAP-USAGE-END sentinel would fail-open into dumping the whole script,
  # function bodies included; a known function-body token must never surface in --help output).
  out="$("${BASH_SOURCE[0]}" --help 2>&1)"
  check "help prints usage" '[ -n "$out" ] && printf "%s" "$out" | grep -q "toolchain-install" && printf "%s" "$out" | grep -q "project-scaffold"'
  check "help stays bounded to the header (no function body leaks through)" \
    '! printf "%s" "$out" | grep -q "toolchain_install_step()"'

  # 2. missing target (combined form) → usage error, exit 2
  set +e; "${BASH_SOURCE[0]}" --dry-run >/dev/null 2>&1; rc=$?; set -e
  check "missing target-dir fails closed (exit 2)" "[ $rc -eq 2 ]"

  # 3. unknown option → exit 2
  set +e; "${BASH_SOURCE[0]}" x --bogus >/dev/null 2>&1; rc=$?; set -e
  check "unknown option fails closed (exit 2)" "[ $rc -eq 2 ]"

  # 4. combined-form dry-run plan covers clone + plugin install + handoff, no side effects
  tmp="$(mktemp -d)"
  out="$("${BASH_SOURCE[0]}" "$tmp/new" --dry-run 2>&1)"
  check "dry-run plans the template clone" 'printf "%s" "$out" | grep -q "git clone"'
  check "dry-run plans the plugin install" 'printf "%s" "$out" | grep -q "plugin install foundry@"'
  check "dry-run plans the runtime-gitignore application (AC-RGLS-4)" 'printf "%s" "$out" | grep -q "foundry-apply-runtime-gitignore.sh"'
  check "dry-run plans the direnv lib install even without --gh-account (AC-BENV-1)" 'printf "%s" "$out" | grep -q "direnv/lib/foundry.sh"'
  check "dry-run prints the /foundry:init handoff" 'printf "%s" "$out" | grep -q "/foundry:init"'
  check "dry-run made no changes" '[ ! -e "$tmp/new" ]'

  # 4b. [pinned marketplace ref / edge channel / https template clone] — AC-BIP-11: the default
  # plan names an explicit release-tag ref, --channel edge announces itself as UNSTABLE, and the
  # default template-clone transport is anonymous https.
  check "pinned marketplace ref default names an explicit semver release tag" \
    'printf "%s" "$out" | grep -Eq "marketplace add.*#v[0-9]+[.][0-9]+[.][0-9]+"'
  out="$("${BASH_SOURCE[0]}" "$tmp/edge" --dry-run --channel edge 2>&1)"
  check "edge channel resolves to the edge ref and warns UNSTABLE" \
    'printf "%s" "$out" | grep -q "UNSTABLE" && printf "%s" "$out" | grep -Eq "marketplace add.*#main"'
  out="$("${BASH_SOURCE[0]}" "$tmp/https" --dry-run 2>&1)"
  check "https template clone is the default transport" \
    'printf "%s" "$out" | grep -q "https://github.com/"'

  # 5. --operator + --gh-account surface in the plan
  out="$("${BASH_SOURCE[0]}" "$tmp/w" --dry-run --operator op_demo --gh-account demoacct 2>&1)"
  check "dry-run plans operator seed" 'printf "%s" "$out" | grep -q "seed operator .op_demo."'
  check "dry-run plans gh identity jail" 'printf "%s" "$out" | grep -q "gh-identity (demoacct)"'
  check "dry-run plans commit-identity includeIf (narrow binding)" 'printf "%s" "$out" | grep -q "narrow global includeIf binding"'
  check "dry-run plans repo-local useConfigOnly" 'printf "%s" "$out" | grep -q "useConfigOnly=true"'
  check "dry-run discloses the jail-scoped probe directory" 'printf "%s" "$out" | grep -q "config/gh-demoacct"'
  check "dry-run plans the direnv lib install (AC-BENV-1/AC-BENV-11)" 'printf "%s" "$out" | grep -q "direnv/lib/foundry.sh"'
  check "dry-run plans the direnv wiring stanza (AC-BENV-2/AC-BENV-11)" 'printf "%s" "$out" | grep -q "use foundry_gh"'

  # 5b. commit-identity is COUPLED to --gh-account: --git-author alone does NOT wire it.
  out="$("${BASH_SOURCE[0]}" "$tmp/ga" --dry-run --git-author "Dev <dev@example.com>" 2>&1)"
  check "--git-author alone does not wire commit-identity" '! printf "%s" "$out" | grep -q "useConfigOnly"'

  # 6. --existing skips the clone
  out="$("${BASH_SOURCE[0]}" "$tmp/e" --existing --dry-run 2>&1)"
  check "--existing skips the clone" 'printf "%s" "$out" | grep -q "skip clone" && ! printf "%s" "$out" | grep -q "git clone"'

  # 7. [toolchain-only] — the toolchain step plans its two operations, no target argument needed.
  set +e; out="$("${BASH_SOURCE[0]}" toolchain-install --dry-run --marketplace demo/nonstandard-marketplace 2>&1)"; rc=$?; set -e
  check "[toolchain-only] plans the marketplace add for the declared marketplace" \
    'printf "%s" "$out" | grep -q "marketplace add" && printf "%s" "$out" | grep -q "demo/nonstandard-marketplace"'
  check "[toolchain-only] plans the plugin install and needs no target argument (exit 0)" \
    'printf "%s" "$out" | grep -q "plugin install foundry@" && [ '"$rc"' -eq 0 ]'

  # 8. [scaffold-only] — the scaffold step plans the per-project operations, and installs nothing.
  out="$("${BASH_SOURCE[0]}" project-scaffold "$tmp/pso" --dry-run --operator op_demo --gh-account demoacct 2>&1)"
  check "[scaffold-only] plans the per-project operations" \
    'printf "%s" "$out" | grep -q "git clone" && printf "%s" "$out" | grep -q "foundry-apply-runtime-gitignore.sh" && printf "%s" "$out" | grep -q "seed operator" && printf "%s" "$out" | grep -q "gh-identity" && printf "%s" "$out" | grep -q "useConfigOnly"'
  check "[scaffold-only] plans no plugin install" '! printf "%s" "$out" | grep -q "plugin install foundry@"'

  # 9. [combined-steps] — the combined form plans both step names.
  out="$("${BASH_SOURCE[0]}" "$tmp/cmb" --dry-run 2>&1)"
  check "[combined-steps] plan names both step names" \
    'printf "%s" "$out" | grep -q "toolchain-install" && printf "%s" "$out" | grep -q "project-scaffold"'

  # 10. [scaffold-needs-toolchain] — the scaffold plan discloses the toolchain-presence precondition.
  out="$("${BASH_SOURCE[0]}" project-scaffold "$tmp/sn2" --dry-run 2>&1)"
  check "[scaffold-needs-toolchain] plan discloses the toolchain precondition check" \
    'printf "%s" "$out" | grep -q "toolchain-install precondition"'

  rm -rf "$tmp"
  echo
  if [ "$fails" -eq 0 ]; then echo "BOOTSTRAP-SELFTEST-GREEN"; else echo "BOOTSTRAP-SELFTEST-RED ($fails failed)"; exit 1; fi
}

if [ "${1:-}" = "--selftest" ]; then selftest; exit 0; fi
main "$@"
