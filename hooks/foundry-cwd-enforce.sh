#!/usr/bin/env bash
# foundry-cwd-enforce — the additive write-jail (§5.4 Cluster-2 WRAP, Q2). Native
# worktree isolation sets a worker's cwd to its worktree but does NOT block an
# absolute-path Edit/Write/MultiEdit/NotebookEdit resolving OUTSIDE the worktree
# (cwd isolation ≠ write-jail). This hook closes that delta: in a linked worktree,
# write-tool targets are canonicalized and a target inside ANOTHER checkout of the same
# repository (the main checkout, a sibling linked worktree, the shared git dir) is a HARD
# STOP (exit 2, fail-closed). Writes outside every checkout of the repository ($HOME/.claude,
# the temp dirs, unrelated paths) are ordinary work and are admitted (v1.18, AC-V118B-1).
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

# Decide (feat v118-b, AC-V118B-1). The jail's job is to keep a linked-worktree session from
# writing into a SIBLING checkout of the SAME repository — the main checkout or another linked
# worktree — where its edits would land on someone else's branch. It is NOT a whole-filesystem
# sandbox: a write that lands outside every worktree of this repository (the operator's
# ~/.claude memory/plans, the system temp dirs, an unrelated directory) is ordinary work and is
# admitted. Every path — target and worktree roots alike — is resolved PHYSICALLY
# (os.path.realpath: symlinks followed, `..` collapsed; a not-yet-existing file resolves through
# its deepest existing parent), and the target is attributed to the MOST SPECIFIC worktree root
# containing it, so nesting in either direction is handled.
#   ALLOW  target is under the session's own worktree                              (a)
#   ALLOW  target is under $HOME/.claude/                                          (b)
#   ALLOW  target is under $TMPDIR, /tmp, /private/tmp or /private/var/folders     (c)
#   BLOCK  target is under the main checkout, another linked worktree, or the
#          shared git dir of this repository (the floor — checked BEFORE b/c/d)
#   ALLOW  target is outside every worktree of this repository                     (d)
# Fail-CLOSED in worker context (F3): a canonicalization failure BLOCKS; if the repository's
# worktree list cannot be enumerated, (d) is unavailable and only (a)/(b)/(c) admit.
# bash-3.2 parse compat: the heredoc lives inside a function body, never inside `$(...)`.
_cwd_enforce_decide() {
  TARGET="$target" WT_ROOT="$wt_root" COMMON_DIR="$commondir" CWD="$cwd" python3 - <<'PY'
import json, os, subprocess, sys

def canon(p):
    return os.path.realpath(os.path.join(os.environ["CWD"], os.path.expanduser(p)))

def ancestor_ids(path):
    """(st_dev, st_ino) of the deepest EXISTING ancestor of `path` and of every directory above it,
    nearest first. Identity, not spelling: on a case-insensitive volume `/Users/x/Repo` and
    `/Users/x/repo` are one directory, and os.path.realpath keeps the case as typed (v1.18.0
    security review Block 5 — a case-variant path used to slip past a string comparison)."""
    p = path
    while p and not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    ids = []
    while True:
        try:
            st = os.stat(p)
            ids.append((st.st_dev, st.st_ino))
        except OSError:
            pass
        parent = os.path.dirname(p)
        if parent == p:
            return ids
        p = parent

def root_id(root):
    try:
        st = os.stat(root)
        return (st.st_dev, st.st_ino)
    except OSError:
        return None

def under(path, root):
    """`path` is inside `root` (or is it), by filesystem identity when `root` exists, else by the
    resolved spelling."""
    rid = root_id(root)
    if rid is not None:
        return rid in ancestor_ids(path)
    return path == root or path.startswith(root.rstrip("/") + "/")

def depth_in(path, root):
    """How far up from `path` the root sits (smaller = more specific); None when not inside."""
    rid = root_id(root)
    ids = ancestor_ids(path)
    return ids.index(rid) if rid in ids else None

def block(msg):
    print("BLOCK " + json.dumps(msg)[1:-1])
    sys.exit(0)

try:
    target = canon(os.environ["TARGET"])
    own = canon(os.environ["WT_ROOT"])
except Exception:
    block("foundry-cwd-enforce: target/worktree canonicalization failed in worker context; fail-closed.")
if not target or not own:
    block("foundry-cwd-enforce: target/worktree canonicalization failed in worker context; fail-closed.")

# The repository's worktrees (main checkout first) + its shared git dir.
roots, enumerated = [], False
try:
    out = subprocess.run(["git", "-C", own, "worktree", "list", "--porcelain"],
                         capture_output=True, text=True, timeout=15)
    if out.returncode == 0:
        for line in out.stdout.splitlines():
            if line.startswith("worktree "):
                roots.append(canon(line[len("worktree "):]))
        enumerated = bool(roots)
except Exception:
    enumerated = False
if os.environ.get("COMMON_DIR"):
    try:
        roots.append(canon(os.environ["COMMON_DIR"]))
    except Exception:
        pass

# Most specific containing root wins (own worktree nested in the main checkout, or vice versa).
containing = [r for r in set(roots) | {own} if under(target, r)]
if containing:
    # most specific = nearest ancestor by identity (not the longest spelling)
    best = min(containing, key=lambda r: (depth_in(target, r) if depth_in(target, r) is not None else 1 << 30))
    if root_id(best) == root_id(own):
        print("ALLOW"); sys.exit(0)                                              # (a)
    block("worker write into a sibling checkout of this repository blocked (fail-closed "
          "write-jail): %s is under %s, not this session's worktree %s" % (target, best, own))

exempt = [os.path.join(os.path.expanduser("~"), ".claude")]
for t in (os.environ.get("TMPDIR", ""), "/tmp", "/private/tmp", "/private/var/folders"):
    if t:
        exempt.append(t)
for e in exempt:
    try:
        if under(target, canon(e)):
            print("ALLOW"); sys.exit(0)                                          # (b) / (c)
    except Exception:
        continue

if enumerated:
    print("ALLOW"); sys.exit(0)                                                  # (d)
block("worker write outside its worktree blocked (fail-closed write-jail): %s not under %s, "
      "and this repository's worktree list could not be enumerated to prove it is outside "
      "every sibling checkout" % (target, own))
PY
}
verdict="$(_cwd_enforce_decide 2>/dev/null || true)"
case "$verdict" in
  ALLOW) exit 0 ;;
  "BLOCK "*)
    printf '{"decision":"block","reason":"%s"}' "${verdict#BLOCK }"; exit 2 ;;
  *)
    printf '{"decision":"block","reason":"foundry-cwd-enforce: write-jail evaluator produced no verdict in worker context; fail-closed."}'; exit 2 ;;
esac
