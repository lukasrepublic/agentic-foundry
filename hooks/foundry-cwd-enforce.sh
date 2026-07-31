#!/usr/bin/env bash
# foundry-cwd-enforce — the additive write-jail (§5.4 Cluster-2 WRAP, Q2). Native
# worktree isolation sets a worker's cwd to its worktree but does NOT block an
# absolute-path Edit/Write/MultiEdit/NotebookEdit resolving OUTSIDE the worktree
# (cwd isolation ≠ write-jail). This hook closes that delta: in a linked worktree,
# write-tool targets are canonicalized and must resolve INSIDE the worktree root,
# else HARD STOP (exit 2, fail-closed).
#
# Native-compatible worker detection: a linked worktree has --git-dir != --git-common-dir
# (the main clone has them equal). So this needs NO dispatch-queue assignment.json — it
# composes with the native Agent `isolation: worktree` boundary. A main-clone / direct
# session (git-dir == git-common-dir) passes unconditionally.
#
# Fail-CLOSED in worker context (the guarantee is load-bearing): once a linked worktree
# is detected, ANY inability to parse the payload or canonicalize the target — python3
# unavailable, parse error, realpath failure — HARD STOPs (exit 2). A main-clone /
# direct session fails OPEN (no jail to enforce). Worker detection itself uses only git
# (no python3), so the fail-closed posture holds even when python3 is absent.
set -uo pipefail

payload="$(cat 2>/dev/null || true)"

# --- Worker-context detection FIRST (git only; NO python3 dependency). ---
cwd="$(pwd -P)"
gitdir="$(git -C "$cwd" rev-parse --absolute-git-dir 2>/dev/null || true)"
commondir="$(git -C "$cwd" rev-parse --git-common-dir 2>/dev/null || true)"
# Normalize common-dir to absolute.
case "$commondir" in /*) : ;; *) commondir="$(cd "$cwd" && cd "$(dirname "$commondir")" 2>/dev/null && pwd)/$(basename "$commondir")" 2>/dev/null || commondir="$commondir" ;; esac

# Not a git repo, or main clone (gitdir == commondir) → no worktree jail (fail-open).
[ -z "$gitdir" ] && exit 0
[ "$gitdir" = "$commondir" ] && exit 0

# --- Worker context (linked worktree). The jail is now load-bearing: fail CLOSED. ---
wt_root="$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$wt_root" ]; then
  printf '{"decision":"block","reason":"foundry-cwd-enforce: worker worktree root unresolved; fail-closed."}'; exit 2
fi

# python3 parses the tool payload. In worker context it must be present AND actually
# functional — a `command -v` presence check is insufficient (F2): a python3 that exists
# but errors would pass presence yet fail the parse, falling back open. Probe that it
# round-trips stdin; on absence OR non-zero exit, fail CLOSED.
if ! printf 'x' | python3 -c 'import sys; sys.exit(0 if sys.stdin.read()=="x" else 1)' >/dev/null 2>&1; then
  printf '{"decision":"block","reason":"foundry-cwd-enforce: python3 unavailable or non-functional in worker context; cannot parse tool payload to enforce the write-jail; fail-closed."}'; exit 2
fi

tool="$(printf '%s' "$payload" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("tool_name",""))
except Exception: print("")' 2>/dev/null || true)"
# Only write-capable tools are jailed. NotebookEdit (F1) carries its target in
# notebook_path, not file_path. This case is defense-in-depth; hooks.json restricts
# the matcher to the same set.
case "$tool" in Edit|Write|MultiEdit|NotebookEdit) : ;; *) exit 0 ;; esac

target="$(printf '%s' "$payload" | python3 -c 'import sys,json
try:
    ti = json.load(sys.stdin).get("tool_input", {}) or {}
    print(ti.get("file_path") or ti.get("notebook_path") or "")
except Exception: print("")' 2>/dev/null || true)"
[ -z "$target" ] && exit 0   # no path field (non-path edit); nothing to jail

# Canonicalize (handles abs paths + .. traversal + symlinks). -m: don't require
# existence. In worker context, canonicalization FAILURE fails CLOSED (F3) — no raw
# fallback (a raw un-normalized target would let `..` prefix-match the worktree root).
canon="$(realpath -m "$target" 2>/dev/null || python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$target" 2>/dev/null || true)"
wt_canon="$(realpath -m "$wt_root" 2>/dev/null || python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$wt_root" 2>/dev/null || true)"
if [ -z "$canon" ] || [ -z "$wt_canon" ]; then
  printf '{"decision":"block","reason":"foundry-cwd-enforce: target/worktree canonicalization failed in worker context; fail-closed."}'; exit 2
fi

case "$canon/" in
  "$wt_canon"/*) exit 0 ;;   # inside the worktree → allow
  *)
    esc="$(printf '%s' "worker write outside its worktree blocked (fail-closed write-jail): $canon not under $wt_canon" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read())[1:-1])')"
    printf '{"decision":"block","reason":"%s"}' "$esc"; exit 2 ;;
esac
