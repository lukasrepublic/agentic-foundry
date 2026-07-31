#!/usr/bin/env bash
# foundry-worktree-create — the WorktreeCreate hook for multi-repo dispatch (UL-0022),
# extended with the GENUINELY-EMPTY-QUEUE native pass-through (ER #43, AC-WNP-1..5).
# Generalized port of the source handbook's .claude/hooks/git-worktree-create.sh.
#
# Fires when Claude Code spawns a subagent with `isolation: worktree`. Reads the hook
# envelope on stdin ({session_id, name, cwd, …}); consumes the oldest pending dispatch
# manifest from <workspace>/.foundry/dispatch-queue/ under a portable mkdir-lock;
# bind-checks the manifest's target_repo against the atom's AUTHORIZED contract (AC-5,
# fail-closed); claims a worktree OF the named product repo via foundry-wt; writes
# .agent/assignment.json; and PRINTS THE WORKTREE PATH ON STDOUT — the command-hook
# cwd-override the harness adopts as the spawned worker's cwd.
#
# THE REAL CONTRACT (Claude Code `WorktreeCreate`, authoritative — code.claude.com/docs/en/hooks):
# emit-path-or-block. The hook MUST print a worktree path on stdout and exit 0, or the
# spawn is BLOCKED — any nonzero exit, or exit-0-with-empty-stdout, fails creation. There is
# NO fall-through to a default/native worktree the harness picks on your behalf. So:
#   - a PRODUCT dispatch (a manifest is present) redirects into the bound product repo (unchanged).
#   - the GENUINELY-EMPTY queue (no queue dir / zero *.json manifests, AC-WNP-1) creates + emits a
#     NATIVE worktree of the CURRENT (workspace) repo instead of failing the spawn — the only way to
#     honor a non-product `isolation:worktree` dispatch under this contract.
#   - EVERY other outcome — lock-acquisition failure, a malformed/empty-target_repo manifest,
#     bind-mismatch, claim-failure, or an unsafe/unsanitizable envelope label (AC-WNP-5) — stays
#     fail-closed: exit nonzero, NO stdout, the harness BLOCKS the spawn (it is never silently
#     relanded as a native workspace worktree; AC-WNP-2/-3). The redirect failing must never wedge
#     a spawn into an UNSAFE state — it fails the redirect (blocks), never the safety floor.
#
# Concurrency: a portable `mkdir` spinlock (atomic on POSIX) serializes the manifest
# consume — no util-linux flock dependency (an improvement over the source blueprint,
# which fails-open without util-linux flock on macOS).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WTBIN="$HERE/../scripts/foundry-wt"
JAIL="$HERE/foundry-cwd-enforce.sh"

_ws_root() {
  if [ -n "${CLAUDE_PROJECT_DIR:-}" ]; then (cd "$CLAUDE_PROJECT_DIR" 2>/dev/null && pwd -P) && return 0; fi
  local cd; cd="$(git rev-parse --git-common-dir 2>/dev/null || true)"
  if [ -n "$cd" ]; then
    case "$cd" in /*) : ;; *) cd="$(pwd)/$cd" ;; esac
    (cd "$(dirname "$cd")" 2>/dev/null && pwd -P) && return 0
  fi
  git rev-parse --show-toplevel 2>/dev/null || pwd -P
}

_lock()   { local l="$1" t="${2:-5}" i=0; while ! mkdir "$l" 2>/dev/null; do i=$((i+1)); [ "$i" -gt $((t*20)) ] && return 1; sleep 0.05; done; return 0; }
_unlock() { rmdir "$1" 2>/dev/null || true; }
_log()    { mkdir -p "$1/.foundry" 2>/dev/null || true; printf '%s worktree-create: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || true)" "$2" >> "$1/.foundry/dispatch.log" 2>/dev/null || true; }

# --------------------------------------------------------------------- AC-WNI-1/-2
# _stage_and_preflight <workspace-root> <worktree-path> <contract_ref> — stages the
# atom's acceptance-contract (at <contract_ref>, workspace-relative unless absolute)
# and the spec it names (its own `spec_ref` field) into the worktree at their SAME
# relative repository paths under the worktree root (stage-by-COPY, never a symlink —
# the worker sees a stable snapshot even if the parent workspace mutates afterward),
# then preflights the STAGED copies — never the source — against the frozen
# authorization: BOTH must be readable AND their recomputed hashes must equal the
# `authorized.spec_sha256` / `authorized.contract_sha256` on the (staged) contract's
# own trailer. The hash bases mirror the authorization freeze exactly (imported from
# the canonical `foundry_contract` module, never reimplemented): `spec_sha256` over
# the staged spec's normative region (`foundry_contract.spec_sha256`), `contract_sha256`
# over the staged contract's PROPER region — bytes BEFORE the frozen
# `# === FOUNDRY-AUTHORIZED-TRAILER …` sentinel (`foundry_contract.contract_sha256_bytes`)
# — so comparing against the frozen trailer values is non-circular (the hash never
# covers the trailer that stores it). The staged contract is also re-validated with
# `foundry_contract.validate_contract_bytes` (the SAME structural/integrity floor
# authorize + the merge gate apply — including the sentinel-injection guard) — never
# reimplemented here — so a staged contract that fails that floor is untrustworthy
# even if its hash happens to match.
#
# BOTH staging destinations (contract AND spec) are containment-checked BEFORE any
# write: the realpath-normalized destination must resolve strictly under the
# realpath-normalized worktree root, and the destination must not already be a
# symlink (a tracked symlink at the staged leaf path would otherwise have `copyfile`
# write THROUGH it, outside the jail). A `..`-escaping or symlink-shadowed ref is
# rejected fail-closed BEFORE `makedirs`/`copyfile` ever runs — this closes the exact
# G7 false-green class the escaped copy would otherwise silently pass (byte-identical
# to its source, hash-matching, but landed outside the worktree the write-jail
# confines).
#
# Prints exactly ONE typed token to stdout and returns 0 (pass) or 1 (fail):
#   OK | spec-unreadable | spec-absent | spec-hash-mismatch | spec-path-escape
#      | contract-unreadable | contract-absent | contract-hash-mismatch
#      | contract-path-escape | contract-integrity
# On any non-OK token the caller MUST treat the worker as hard-failed — no stdout
# worktree path, non-success — closing the false-green class this atom exists for
# (gap G7: a jail that hid the spec silently and still reported exit:0).
_stage_and_preflight() {
  local ws="$1" wt="$2" cref="$3"
  SCRIPTS_DIR="$HERE/../scripts" python3 - "$ws" "$wt" "$cref" <<'PY'
import os, sys, shutil

ws, wt, cref = sys.argv[1:4]
sys.path.insert(0, os.environ["SCRIPTS_DIR"])
import foundry_contract as fc  # canonical hashing + integrity validation — never reimplemented
import yaml


def _resolve(base, ref):
    return ref if os.path.isabs(ref) else os.path.join(base, ref)


def _rel_under(base, path):
    ap, ab = os.path.abspath(path), os.path.abspath(base)
    if ap == ab or ap.startswith(ab + os.sep):
        return os.path.relpath(ap, ab)
    return os.path.basename(ap)  # outside the workspace root: best-effort flat staging


def _fail(token):
    print(token)
    sys.exit(1)


def _staged_path_or_fail(root, rel, token):
    """Join root/rel, then HARD-REQUIRE the realpath-normalized result stays under the
    realpath-normalized root — a relative ref containing '..' (a manifest's
    contract_ref, or a contract's own spec_ref) must never escape the worktree jail
    (the same escape class AC-WNP-5's <agent>/<task> label sanitizer already
    forecloses). Checked BEFORE any filesystem write."""
    dst = os.path.join(root, rel)
    root_r = os.path.realpath(root)
    dst_r = os.path.realpath(dst)
    if not (dst_r == root_r or dst_r.startswith(root_r + os.sep)):
        _fail(token)
    return dst


contract_src = _resolve(ws, cref)
contract_rel = cref if not os.path.isabs(cref) else _rel_under(ws, cref)
staged_contract = _staged_path_or_fail(wt, contract_rel, "contract-path-escape")

# 1. Read the SOURCE contract only to learn spec_ref (where to stage the spec) and to
#    locate the worktree staging target for the contract itself. The actual preflight
#    verification below runs entirely against the STAGED copies, never this source read.
try:
    with open(contract_src, "rb") as fh:
        c_data = yaml.safe_load(fh.read()) or {}
except FileNotFoundError:
    _fail("contract-absent")
except Exception:
    _fail("contract-unreadable")

spec_ref = (c_data.get("spec_ref") or "").strip()
if not spec_ref:
    _fail("spec-absent")
spec_src = _resolve(ws, spec_ref)
spec_rel = spec_ref if not os.path.isabs(spec_ref) else _rel_under(ws, spec_ref)
staged_spec = _staged_path_or_fail(wt, spec_rel, "spec-path-escape")

# 2. STAGE BY COPY (never a symlink) at the same relative repository path under the
#    worktree root. A PRE-EXISTING symlink at either staged destination (e.g. a path
#    the freshly-checked-out product repo itself tracks) is rejected fail-closed
#    BEFORE copying — `shutil.copyfile` would otherwise follow it and write THROUGH
#    it, outside the jail; never unlinked, never followed. A missing source simply
#    leaves the staged copy absent — caught by the existence checks below (never
#    silently substituted).
for dst, token in ((staged_contract, "contract-path-escape"), (staged_spec, "spec-path-escape")):
    if os.path.islink(dst):
        _fail(token)
for src, dst in ((contract_src, staged_contract), (spec_src, staged_spec)):
    try:
        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        shutil.copyfile(src, dst)
    except Exception:
        pass

# 3. PREFLIGHT the STAGED copies against the frozen authorization — contract first
#    (its trailer carries the frozen hashes the spec is checked against).
if not os.path.exists(staged_contract):
    _fail("contract-absent")
try:
    with open(staged_contract, "rb") as fh:
        staged_c_raw = fh.read()
    staged_c_data = yaml.safe_load(staged_c_raw) or {}
    contract_hash = fc.contract_sha256_bytes(staged_c_raw)
except Exception:
    _fail("contract-unreadable")

# The SAME structural/integrity floor authorize + the merge gate apply (sentinel-
# injection guard: a second `authorized:` block above the sentinel, required fields,
# non-vacuous checkpoints, …) — reused via the canonical module, never reimplemented.
try:
    integrity_ok, _errs, _warns = fc.validate_contract_bytes(staged_c_raw)
except Exception:
    integrity_ok = False
if not integrity_ok:
    _fail("contract-integrity")

frozen = staged_c_data.get("authorized") or {}
if contract_hash != frozen.get("contract_sha256"):
    _fail("contract-hash-mismatch")

if not os.path.exists(staged_spec):
    _fail("spec-absent")
try:
    spec_hash = fc.spec_sha256(staged_spec)
except Exception:
    _fail("spec-unreadable")
if spec_hash != frozen.get("spec_sha256"):
    _fail("spec-hash-mismatch")

print("OK")
sys.exit(0)
PY
}

# Consume oldest manifest + redirect. stdout = worktree path on success (rc 0).
# Return-code taxonomy (AC-WNP-1/-2 — the load-bearing routing signal for the dispatcher below):
#   0  = product path emitted (unchanged product-redirect success)
#   10 = GENUINELY-EMPTY queue (no queue dir, or dir present with ZERO *.json entries by glob
#        presence alone) -> the ONLY code the dispatcher routes to the native branch (AC-WNP-1)
#   1  = lock-acquisition failure (a manifest MAY be present but is unconsumable under contention)
#   3  = malformed / empty-target_repo manifest (a manifest IS present but corrupt)
#   2  = bind-mismatch or claim-failure
#   4  = AC-WNI-2 staged spec/contract preflight hard-fail (typed diagnostic logged)
#   5  = AC-WNI-2/RISK-4 manifest carries no contract_ref — staging/preflight skipped
#        would silently re-open gap G7; fail closed instead
# Every code other than 0 and 10 stays fail-closed at the dispatcher (AC-WNP-2 floor): a
# contended or corrupt PRODUCT manifest is never misread as "empty" and relanded native.
_redirect() {
  local WS QUEUE LOCK
  WS="$(_ws_root)"; QUEUE="$WS/.foundry/dispatch-queue"; LOCK="$QUEUE/.lock.d"
  # AC-WNP-1 emptiness test: GLOB PRESENCE ONLY, judged before any lock/parse — a directory
  # entry matching *.json counts as a manifest regardless of its contents (a corrupt entry is
  # handled below as malformed/fail-closed, never mistaken for empty).
  [ -d "$QUEUE" ] || return 10                      # no queue dir at all -> genuinely empty
  local -a _q_glob=( "$QUEUE"/*.json )
  [ -e "${_q_glob[0]}" ] || return 10                # dir present, zero *.json entries -> genuinely empty
  _lock "$LOCK" 5 || return 1                        # lock-acquisition failure: fail-closed (NOT empty)
  local m; m="$(ls -1t "$QUEUE"/*.json 2>/dev/null | tail -1)"   # ls -t newest-first; tail -1 = oldest (FIFO)
  if [ -z "$m" ] || [ ! -f "$m" ]; then _unlock "$LOCK"; return 3; fi   # present-but-vanished race: malformed fail-closed
  local tr task agent contract
  read -r tr task agent contract < <(python3 - "$m" <<'PY' 2>/dev/null
import json,sys
try: d=json.load(open(sys.argv[1]))
except Exception: d={}
print(d.get("target_repo","") or "", d.get("task","") or "", d.get("agent","") or "", d.get("contract_ref","") or "")
PY
)
  if [ -z "$tr" ] || [ -z "$task" ] || [ -z "$agent" ]; then
    # audit remediation (F3): a denied/corrupt PRODUCT manifest is never SILENTLY destroyed — log
    # before the poison-removal (the rm still runs; it correctly unwedges the queue, and the
    # encountering spawn already fails closed — this is a trail, not a confinement change).
    _log "$WS" "DROPPED-MALFORMED-MANIFEST $(basename "$m")"
    rm -f "$m"; _unlock "$LOCK"; return 3   # malformed manifest: fail-closed (AC-WNP-2)
  fi
  # AC-5: deterministic bind — manifest target_repo MUST equal the authorized contract target_repo.
  if [ -n "$contract" ]; then
    local cpath; case "$contract" in /*) cpath="$contract" ;; *) cpath="$WS/$contract" ;; esac
    if ! "$WTBIN" bind-check "$tr" "$cpath" >/dev/null 2>&1; then
      _log "$WS" "DROPPED-BIND-MISMATCH tr=$tr != contract $(basename "$m")"
      rm -f "$m"; _unlock "$LOCK"; return 2        # fail-closed: never create a worktree on bind mismatch
    fi
  fi
  # Claim UNDER the lock; consume the manifest ONLY on a successful claim, so a transient
  # claim failure (git lock, momentarily-unavailable repo) leaves the manifest in place to
  # retry instead of silently stranding a product-targeted worker into the workspace (audit
  # remediation: consume-on-success, with a dispatch-log signal on either outcome).
  local path; path="$("$WTBIN" claim "$tr" "$task" --as="$agent" 2>/dev/null)"
  if [ -z "$path" ]; then
    _log "$WS" "CLAIM-FAILED $tr/$task (manifest retained for retry: $(basename "$m"))"
    _unlock "$LOCK"; return 2
  fi
  rm -f "$m"; _unlock "$LOCK"                        # consume only AFTER a successful claim
  _log "$WS" "redirect $tr/$task -> $path"

  # AC-WNI-1/-2: stage the atom's spec + acceptance-contract into the worktree at their
  # same relative repo paths (stage-by-COPY), then hard-preflight the staged copies
  # against the frozen authorization BEFORE the worker is allowed to see the worktree at
  # all. A jail that hides the spec must fail fast and typed here, not silently report
  # exit:0 downstream (gap G7 — the design-partner false-green class this atom closes). A manifest
  # with an EMPTY/absent contract_ref is itself the un-mitigated gap (the manifest
  # schema declares contract_ref as a required field, skills/dispatch/SKILL.md §Multi-
  # repo dispatch) — fail closed here too, rather than silently emit a worktree with
  # nothing staged/preflighted.
  if [ -n "$contract" ]; then
    local diag; diag="$(_stage_and_preflight "$WS" "$path" "$contract")"
    local prc=$?
    if [ "$prc" -ne 0 ]; then
      _log "$WS" "PREFLIGHT-FAIL $tr/$task: $diag"
      return 4
    fi
    _log "$WS" "preflight-ok $tr/$task: $diag"
  else
    _log "$WS" "PREFLIGHT-SKIP-NO-CONTRACT $tr/$task"
    return 5
  fi

  mkdir -p "$path/.agent"
  python3 - "$path/.agent/assignment.json" "$tr" "$task" "$agent" "$contract" <<'PY' 2>/dev/null || true
import json,sys
p,tr,task,agent,contract=sys.argv[1:6]
json.dump({"target_repo":tr,"task":task,"agent":agent,"contract_ref":contract}, open(p,"w"))
PY
  printf '%s\n' "$path"
  return 0
}

# AC-WNP-5: reject (never coerce) any envelope-derived <agent>/<task> label component that is
# empty/absent, contains a path separator or a `..` segment, begins with `-`/`/`/`.`, or contains
# any character outside the safe allowlist [A-Za-z0-9._-]. foundry-wt applies NO confinement to
# the label components it is handed, so this floor MUST be enforced here, before the label ever
# reaches `foundry-wt claim` (a confined label cannot escape .worktrees/workspace/ nor inject a
# hostile git ref).
_sanitize_label() {
  local v="${1:-}"
  [ -n "$v" ] || return 1                 # empty/absent
  case "$v" in */*) return 1 ;; esac      # path separator
  case "$v" in *..*) return 1 ;; esac     # ".." segment (also rejects a bare "..")
  case "$v" in -*|/*|.*) return 1 ;; esac # begins with -, /, or .
  case "$v" in *[!A-Za-z0-9._-]*) return 1 ;; esac  # outside the safe allowlist
  return 0
}

# AC-WNP-1/-3: the genuinely-empty-queue branch. Derives <agent>/<task> from the captured hook
# envelope (name -> agent, session_id -> task), sanitizes each (AC-WNP-5), and creates + emits a
# NATIVE worktree of the CURRENT repo via the existing `foundry-wt claim workspace <task> --as=<agent>`
# ("workspace" resolves to CLAUDE_PROJECT_DIR / _ws_root(), independent of actual cwd). Any failure
# here (missing/unsafe label fields, current dir not a git repo, `git worktree add` failure) is
# honest fail-closed: return nonzero with NO stdout — never run un-isolated, never with an unsafe
# label as a silent consolation. The resulting worktree is a LINKED worktree, so the existing
# foundry-cwd-enforce.sh write-jail applies to it automatically (confinement parity).
_native() {
  local envelope="$1" name sid agent task path
  # NOTE: the envelope is passed as argv[1] (NOT piped on stdin) — a `python3 - <<PY` heredoc
  # supplies the script's OWN source via stdin, so piping the envelope on stdin too would starve
  # `sys.stdin` inside the script (both would race the same fd). argv keeps the manifest-parse and
  # envelope-parse the same "python3 inline" shape while avoiding that stdin collision.
  read -r name sid < <(python3 - "$envelope" <<'PY' 2>/dev/null
import json,sys
try: d=json.loads(sys.argv[1])
except Exception: d={}
print(d.get("name","") or "", d.get("session_id","") or "")
PY
)
  agent="${name:-}"; task="${sid:-}"
  _sanitize_label "$agent" || return 1
  _sanitize_label "$task"  || return 1
  path="$("$WTBIN" claim workspace "$task" --as="$agent" 2>/dev/null)"
  [ -n "$path" ] && [ -d "$path" ] || return 1
  printf '%s\n' "$path"
  return 0
}

# --------------------------------------------------------------------------- selftest
_selftest() {
  local tmp ok=1; tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' RETURN
  local ws="$tmp/ws"; mkdir -p "$ws/.claude" "$ws/.foundry/dispatch-queue"
  # the workspace root ITSELF is a git repo (matches production — it's the repo the plugin is
  # installed into) so the AC-WNP-1 native branch (`foundry-wt claim workspace …`) has a real repo
  # to create a linked worktree of.
  ( cd "$ws" && git init -q && git -c user.email=t@t -c user.name=t commit -q --allow-empty -m init )
  mkdir -p "$ws/app"; ( cd "$ws/app" && git init -q && git -c user.email=t@t -c user.name=t commit -q --allow-empty -m init )
  printf '{ "schema_version":1, "repos": { "app": { "path": "app" } } }\n' > "$ws/.claude/foundry-project.json"
  # AC-WNI-1/-2/-3 (RISK-3 hardening): a genuinely FROZEN, SCHEMA-VALID spec+contract
  # fixture (real normative-region / contract-proper hashes under a full `authorized:`
  # trailer, non-empty scope.allowed_paths + checkpoints) — not a bare 2-field stub —
  # so it also passes `foundry_contract.validate_contract_bytes`'s integrity floor
  # (RISK-3), and the legacy AC-MRDISPATCH-2/-3/-9 redirects below still exercise the
  # real staging + preflight path unchanged in OUTCOME (still PASS), proving AC-WNI-4
  # non-disruption.
  SCRIPTS_DIR="$HERE/../scripts" python3 - "$ws" <<'PY'
import os, sys
ws = sys.argv[1]
sys.path.insert(0, os.environ["SCRIPTS_DIR"])
import foundry_contract as fc

spec_path = os.path.join(ws, "spec.md")
with open(spec_path, "w") as f:
    f.write("# demo spec\n\n<!-- normative -->\ndemo acceptance criteria body\n<!-- /normative -->\n")

spec_hash = fc.spec_sha256(spec_path)
proper = (
    "spec_ref: spec.md\n"
    "spec_sha256: %s\n"
    "target_repo: app\n"
    "scope:\n"
    "  allowed_paths:\n"
    "    - \"demo/**\"\n"
    "checkpoints:\n"
    "  - ac_id: \"AC-DEMO-1\"\n"
    "    surface: \"cli:demo\"\n"
    "    locator: \"true\"\n"
    "    expect: {op: \"non-empty\", baseline: \"pre-change\"}\n"
) % spec_hash
proper_b = proper.encode("utf-8")
contract_hash = fc.contract_sha256_bytes(proper_b)
trailer = (
    "authorized:\n"
    "  operator_id: op_test\n"
    "  authorized_at: \"2026-01-01T00:00:00Z\"\n"
    "  auth_seq: 1\n"
    "  spec_sha256: %s\n"
    "  contract_sha256: %s\n"
    "  merge_autonomy_mode: lean\n"
) % (spec_hash, contract_hash)
frozen = fc.freeze_proper_and_trailer(proper_b, trailer)
with open(os.path.join(ws, "contract.yaml"), "wb") as f:
    f.write(frozen)
PY
  local wsP; wsP="$(cd "$ws" && pwd -P)"

  # ---- AC-WNP-1: GENUINELY-EMPTY queue -> creates + emits a NATIVE workspace worktree, exit 0 ----
  # (superseded from the old AC-MRDISPATCH-4 "no-manifest -> fall-through (nonzero, empty stdout)"
  # control — v1.1 §8 remediation: the WorktreeCreate contract is emit-path-or-block, no fall-through,
  # so the genuinely-empty branch must itself create+emit a native worktree of the current repo.)
  local out4 rc4
  out4="$(printf '{"name":"probe-agent","session_id":"probe-task"}' | CLAUDE_PROJECT_DIR="$ws" bash "$0" 2>/dev/null)"; rc4=$?
  local r4=1
  [ "$rc4" -eq 0 ]         || { echo "  AC-WNP-1: empty-queue rc=$rc4 (want 0)" >&2; r4=0; }
  [ -n "$out4" ] && [ -d "$out4" ] || { echo "  AC-WNP-1: empty-queue emitted no worktree path ('$out4')" >&2; r4=0; }
  case "$out4" in
    "$wsP/.worktrees/workspace/probe-agent/probe-task") : ;;
    *) echo "  AC-WNP-1: unexpected native worktree path '$out4'" >&2; r4=0 ;;
  esac
  local ntop; ntop="$(git -C "$out4" rev-parse --show-toplevel 2>/dev/null || true)"
  [ "$ntop" = "$out4" ] || { echo "  AC-WNP-1: native worktree not a valid git checkout (top='$ntop')" >&2; r4=0; }
  if [ "$r4" -eq 1 ]; then echo "AC-WNP-1 no-manifest-native-fallback: PASS"; else echo "AC-WNP-1 no-manifest-native-fallback: FAIL"; ok=0; fi

  # ---- AC-MRDISPATCH-2: manifest -> product worktree redirect (stdout path inside product repo) ----
  printf '{"target_repo":"app","task":"t-demo","agent":"platform-engineer","contract_ref":"contract.yaml"}\n' > "$ws/.foundry/dispatch-queue/t-demo.json"
  local out2 rc2; out2="$(CLAUDE_PROJECT_DIR="$ws" bash "$0" </dev/null 2>/dev/null)"; rc2=$?
  local r2=1
  [ "$rc2" -eq 0 ] || { echo "  AC-2: redirect rc=$rc2 (want 0)" >&2; r2=0; }
  [ -n "$out2" ] && [ -d "$out2" ] || { echo "  AC-2: no worktree path emitted ('$out2')" >&2; r2=0; }
  local top; top="$(git -C "$out2" rev-parse --show-toplevel 2>/dev/null || true)"
  [ "$top" = "$out2" ] || { echo "  AC-2: show-toplevel '$top' != worktree '$out2'" >&2; r2=0; }
  # the worktree's git-common-dir must point at the PRODUCT repo (app), not the workspace
  local common; common="$(git -C "$out2" rev-parse --git-common-dir 2>/dev/null || true)"
  case "$common" in "$wsP/app"/*|"$wsP/app/.git") : ;; *) echo "  AC-2: worktree not linked to product repo (common=$common)" >&2; r2=0 ;; esac
  if [ "$r2" -eq 1 ]; then echo "AC-MRDISPATCH-2 product-worktree-redirect: PASS"; else echo "AC-MRDISPATCH-2 product-worktree-redirect: FAIL"; ok=0; fi

  # ---- AC-MRDISPATCH-3: write-jail (unchanged foundry-cwd-enforce) jails to the product worktree ----
  local r3=1
  if [ -d "$out2" ]; then
    local rc_in rc_out
    ( cd "$out2" && printf '{"tool_name":"Write","tool_input":{"file_path":"%s/in.txt"}}' "$out2" | bash "$JAIL" >/dev/null 2>&1 ); rc_in=$?
    ( cd "$out2" && printf '{"tool_name":"Write","tool_input":{"file_path":"%s/outside.txt"}}' "$wsP" | bash "$JAIL" >/dev/null 2>&1 ); rc_out=$?
    [ "$rc_in" -eq 0 ]  || { echo "  AC-3: in-worktree write blocked (rc=$rc_in, want 0)" >&2; r3=0; }
    [ "$rc_out" -eq 2 ] || { echo "  AC-3: out-of-worktree write allowed (rc=$rc_out, want 2)" >&2; r3=0; }
  else r3=0; fi
  if [ "$r3" -eq 1 ]; then echo "AC-MRDISPATCH-3 write-jail-and-denied-binding: PASS"; else echo "AC-MRDISPATCH-3 write-jail-and-denied-binding: FAIL"; ok=0; fi

  # ---- bind-mismatch fail-closed (AC-5 enforcement at the hook): wrong target_repo -> no worktree ----
  local rbind=1
  printf '{"target_repo":"infra","task":"t-bad","agent":"platform-engineer","contract_ref":"contract.yaml"}\n' > "$ws/.foundry/dispatch-queue/t-bad.json"
  local outb rcb; outb="$(CLAUDE_PROJECT_DIR="$ws" bash "$0" </dev/null 2>/dev/null)"; rcb=$?
  [ "$rcb" -ne 0 ] || { echo "  bind: mismatched manifest produced rc=$rcb (want nonzero fail-closed)" >&2; rbind=0; }
  [ -z "$outb" ]   || { echo "  bind: mismatched manifest created worktree '$outb' (want none)" >&2; rbind=0; }

  # ---- AC-MRDISPATCH-9: end-to-end (redirect -> worker write -> repo-aware teardown) ----
  local r9=1
  [ "$rbind" -eq 1 ] || r9=0
  if [ -d "$out2" ]; then
    echo "worker change" > "$out2/worked.txt"
    [ -f "$out2/worked.txt" ] || { echo "  AC-9: worker write failed" >&2; r9=0; }
    ( cd "$out2" && git -c user.email=t@t -c user.name=t add -A && git -c user.email=t@t -c user.name=t commit -q -m work )
    CLAUDE_PROJECT_DIR="$ws" "$WTBIN" rm app t-demo --as=platform-engineer >/dev/null 2>&1
    # teardown of a committed-but-unpushed branch is refused by -d (never force) — worktree dir may remain; that's the no-force guarantee
  else r9=0; fi
  if [ "$r9" -eq 1 ]; then echo "AC-MRDISPATCH-9 e2e-extraction: PASS"; else echo "AC-MRDISPATCH-9 e2e-extraction: FAIL"; ok=0; fi

  [ "$ok" -eq 1 ] && echo "FOUNDRY-WORKTREE-CREATE-SELFTEST-GREEN" || { echo "FOUNDRY-WORKTREE-CREATE-SELFTEST-RED"; return 1; }
}

if [ "${1:-}" = "--selftest" ]; then _selftest; exit $?; fi

# Capture the hook envelope BEFORE the redirect/native decision (replaces the old bare
# `cat >/dev/null` drain) — the native branch (AC-WNP-1) needs `name`/`session_id` from it;
# the product redirect is unaffected (it reads the manifest, not the envelope).
ENVELOPE="$(cat 2>/dev/null || true)"

_redirect
_RC=$?
case "$_RC" in
  0)  exit 0 ;;                          # product path already emitted on stdout
  10) _native "$ENVELOPE"; exit $? ;;    # AC-WNP-1: genuinely-empty queue -> native create+emit
  *)  exit 1 ;;                          # AC-WNP-2/-3: every other outcome is fail-closed, no stdout
esac
