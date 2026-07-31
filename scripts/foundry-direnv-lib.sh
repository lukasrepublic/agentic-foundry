#!/usr/bin/env bash
# foundry-direnv-lib.sh — the factory's direnv global-lib source (feat-foundry-bootstrap-managed-
# block-writes, AC-BENV-1 / AC-BENV-4). Installed BYTE-IDENTICALLY by scripts/foundry-bootstrap.sh's
# machine-scope step to $HOME/.config/direnv/lib/foundry.sh -- direnv's own designated per-user
# global-lib directory (direnv auto-loads every *.sh file under ~/.config/direnv/lib/ on every
# evaluation, per the direnv changelog entry for 2.21.0, and dispatches a project .envrc's
# `use foundry_gh` line to the use_foundry_gh function below).
#
# DEFINITIONS ONLY: this file does nothing at load time beyond defining functions -- direnv reads
# every lib file on every `cd`, so a top-level side effect here would fire on every directory
# change on the machine, not only inside a foundry-wired project.
#
# The two files this reads are per-project, factory-written, and strictly INERT DATA: each is read
# through an input redirection into `tr`, never read via the dot builtin and never handed to the
# shell's expression-evaluation builtin, so nothing an adopter's repository carries in either file
# can execute here -- a `$(...)` or backtick form in either file lands as a literal string in the
# resulting exported value, nothing more.
#   .claude/gh-identity      -- the validated gh account slug (one line)
#   .claude/foundry-operator -- the operator id (one line), present only when --operator was given
#
# READ-SIDE VALIDATION (security-review remediation, PR #295 mandatory-review follow-up). `tr -d
# '[:space:]'` strips whitespace only -- it does NOT bound the character set, so a
# `.claude/gh-identity` carrying `work/../gh` (or an absolute path, or a bare empty file) would
# resolve GH_CONFIG_DIR to `$HOME/.config/gh`, gh's DEFAULT config directory: a silent
# cross-account leak (the exact failure this atom exists to close) and, since that directory can
# carry an attacker-supplied hosts.yml/config.yml (gh aliases execute), an amplifier beyond mere
# misattribution. The write side (scripts/foundry-bootstrap.sh's validate_arguments /
# normalize_account_slug) already bounds this value to `^[A-Za-z0-9._-]+$`; the SAME bound is
# re-applied here, on the READ side, because the file is adopter-repository content and this lib's
# only defense against a hand-edited, corrupted or symlink-swapped identity file. A rejected slug
# (or an unreadable/missing/non-regular gh-identity file, which is likewise treated as untrusted --
# a dispatched `use foundry_gh` with no readable identity file is itself an anomaly, since the
# seed step writes the file and the stanza together) POISONS GH_CONFIG_DIR to a path gh can never
# resolve to a real account, so `gh` fails VISIBLY instead of silently falling back to the default
# identity. `.claude/foundry-operator` gets the SAME allowlist on read, but no poisoning: an
# unexported FOUNDRY_OPERATOR just leaves the downstream operator-registry resolution to fail
# closed on its own, the way an absent file already does.

# _foundry_token_is_valid VALUE -- the read-side mirror of the write side's shell-inert allowlist:
# composed solely of ^[A-Za-z0-9._-]+$, non-empty, and never '.' or '..'. Pure `case`/glob (no grep
# spawn) so this stays as cheap as the rest of the dispatch path.
_foundry_token_is_valid() {
  local value="$1"
  [ -n "$value" ] || return 1
  case "$value" in
    .|..) return 1 ;;
  esac
  case "$value" in
    *[!A-Za-z0-9._-]*) return 1 ;;
  esac
  return 0
}

# _foundry_poison_gh_config_dir REASON -- fail-closed for GH_CONFIG_DIR: a path under a dedicated,
# never-legitimate namespace (never `.config/gh-<slug>`, so it can never coincide with a real
# per-account directory a validated slug would produce), plus ONE stderr line naming the file and
# the rejection reason. GH_CONFIG_DIR is ALWAYS exported here -- never left unset -- so a rejection
# cannot fall through to gh's own ambient/default config directory.
_foundry_poison_gh_config_dir() {
  local reason="$1"
  export GH_CONFIG_DIR="$HOME/.foundry-direnv-poison/REJECTED-IDENTITY"
  printf 'foundry-direnv-lib: %s -- GH_CONFIG_DIR poisoned so gh fails closed instead of falling back to the default account\n' "$reason" >&2
}

use_foundry_gh() {
  if command -v watch_file >/dev/null 2>&1; then
    watch_file .claude/gh-identity .claude/foundry-operator
  fi

  if [ -f .claude/gh-identity ] && [ -r .claude/gh-identity ]; then
    local raw_slug
    raw_slug="$(tr -d '[:space:]' < .claude/gh-identity)"
    if _foundry_token_is_valid "$raw_slug"; then
      export GH_CONFIG_DIR="$HOME/.config/gh-$raw_slug"
    else
      # The rejected value is folded into the diagnostic (a plain printf %s -- never executed,
      # never interpolated into anything that IS) rather than merely named as "invalid": this is
      # what still lets the AC-BENV-4 inertness fixture observe that a `$(...)` payload was
      # carried around as an inert STRING (its literal text reaches this message) and never once
      # reached a context that would have executed it.
      _foundry_poison_gh_config_dir ".claude/gh-identity holds an invalid or empty account slug (rejected value: $raw_slug)"
    fi
  else
    _foundry_poison_gh_config_dir ".claude/gh-identity is missing, not a regular file, or unreadable"
  fi

  if [ -f .claude/foundry-operator ] && [ -r .claude/foundry-operator ]; then
    local raw_operator
    raw_operator="$(tr -d '[:space:]' < .claude/foundry-operator)"
    if _foundry_token_is_valid "$raw_operator"; then
      export FOUNDRY_OPERATOR="$raw_operator"
    else
      printf 'foundry-direnv-lib: .claude/foundry-operator holds an invalid or empty operator id -- not exporting FOUNDRY_OPERATOR\n' >&2
    fi
  fi

  return 0
}
