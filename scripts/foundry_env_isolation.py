#!/usr/bin/env python3
"""foundry_env_isolation — per-worker ephemeral-env port/namespace isolation + the conductor mutex
(feat-foundry-per-worker-ephemeral-env-and-conductor-mutex, AC-PWE-1..4).

RELOCATED (2026-07-27) from `scripts/foundry_checks/per-worker-ephemeral-env.py` —
that module was mistakenly the SOLE home for this REAL, load-bearing production primitive (its own
docstring noted "allowed_paths for this atom has no separate top-level script slot"), consumed at
runtime by `hooks/foundry-env-reap.sh` and the `dispatch`/`env-hygiene` skill procedures. The
`scripts/foundry_checks/` drop-in-doctor-check directory does not ship; this file is the
production half of that atom, in a proper top-level script slot.
The check/selftest half is converted to `tests/test_env_isolation.py` (pytest).

Gap G7 (empirical driver, design-partner runs): N per-atom worktrees each brought up identical
compose services -> host-port collisions forced live-seam walks to serialize, and a
duplicate-conductor race had two sessions driving the same release onto shared worktrees.

Mechanism choices (research-first consensus per the spec's Prior art section — the two
REQUIREMENTS are settled industry consensus; only the port MECHANISM was a design-time choice):

  * AC-PWE-1 (per-worker port isolation) — **OS-assigned dynamic (ephemeral) ports**
    (`socket.bind((host, 0))`). This is the dominant CI-parallelism practice (pytest-xdist,
    testcontainers, etc. all delegate port choice to the kernel rather than hand-rolling a
    static/offset scheme). The kernel's `bind(2)` is the atomic claim itself — there is no
    client-side read-then-bind window a concurrent allocation could race, so "coordinated so
    concurrently-starting workers cannot be assigned the same namespace" is a STRUCTURAL
    property of the mechanism, not a lock foundry has to build and could get wrong.

  * AC-PWE-2 (conductor mutex) — `fcntl.flock(LOCK_EX[|LOCK_NB])` on a sidecar lock file keyed
    by a CANONICALIZED release id, the SAME kernel-held-advisory-lock primitive this repo already
    uses for `scripts/foundry_id_alloc.py` (id-allocation-mutex). The staleness SIGNAL (Residuals)
    is the KERNEL-HELD LOCK ITSELF, not a side PID-liveness/TTL record: the kernel auto-releases a
    flock at `close()`/process-exit (including SIGKILL), so "detect-stale -> reclaim ->
    re-acquire" collapses into ONE `flock()` syscall — there is no separate client-side reclaim
    step for two conductors to race non-atomically over. This satisfies the AC-PWE-2 "same held
    flock" single-winner-primitive clause directly, and eliminates the whole PID-liveness/TTL
    stale-lock detection class (the same argument `foundry_id_alloc.py`'s docstring makes).
    FAIL-CLOSED: `acquire_conductor_mutex(..., blocking=False)` (the default) uses
    `LOCK_EX | LOCK_NB` — a driver that cannot immediately acquire gets `None` back and MUST NOT
    proceed to drive the release. `canonicalize_release_id` collapses whitespace BEFORE applying
    the leading-'v' strip (security-review FIX-4): `"v 1.0"` (v + whitespace) and `"v1.0"` must
    converge to the SAME canonical key, else two spellings of the same release could acquire the
    mutex independently — the exact double-conductor race this AC exists to prevent.

  * AC-PWE-3 (env-reaper) — an ownership record (`{worker_id, port, holder_pid, holder_command,
    holder_start}`) written atomically (temp+rename) at allocation time, reconciled against LIVE
    `ps` reality at reap time using the SAME liveness discriminator SHAPE as
    `scripts/foundry-env-hygiene.py`'s PID-recycling guard, hardened past a straight port, per a
    security review of this atom:
      - **Identity anchor = start-time alone, `command` advisory-only (FIX-1).** Preservation
        requires the LIVE holder pid to be alive AND its live start-time to match the recorded
        `holder_start`. `command` is NOT required to match: a live worker that `exec()`s into its
        real service (record-then-`exec docker compose`, or any argv/setproctitle rewrite) keeps
        its pid + start-time but changes its command — requiring a command match would wrongly
        convict that still-live worker.
      - **Convict/exonerate asymmetry (FIX-2).** A `ps` probe that itself fails/raises is
        "inconclusive", never "dead" — a transient probe failure must EXONERATE, never
        manufacture a false "dead" that would mass-reap live workers.
      - **Filename hashing (FIX-3).** `worker_id` is external input; only a sha256-hashed
        filename touches the filesystem — a `../../..`-shaped worker_id cannot escape the
        allocations dir.

Threat model — TRUSTED OPERATOR; a mechanical reaper/mutex over the local host, not a defense
against a hostile worker.
"""
import fcntl
import hashlib
import json
import os
import re
import socket
import subprocess
import tempfile
import time

NAME = "per-worker-ephemeral-env"

_HERE = os.path.dirname(os.path.abspath(__file__))
# repo root = one dir up from this file (scripts/foundry_env_isolation.py -> repo root).
_REPO_ROOT = os.path.dirname(_HERE)


def _live_root():
    return os.environ.get("CLAUDE_PLUGIN_ROOT") or _REPO_ROOT


# --------------------------------------------------------------------------------------------- #
# AC-PWE-1: per-worker ephemeral port allocation — OS-assigned dynamic ports (the atomic claim).
# --------------------------------------------------------------------------------------------- #
def allocate_ephemeral_port(host="127.0.0.1"):
    """Atomically claim a non-colliding port via OS-assigned dynamic (ephemeral) port allocation.
    `bind((host, 0))` hands the ASSIGNMENT DECISION to the kernel — an atomic, process-safe
    operation by construction (POSIX bind(2)); there is no client-side unsynchronized
    read-then-bind window a concurrent allocation could race. Returns (sock, port): an OPEN,
    LISTENING socket holding the claim (the caller keeps it open for the allocation's lifetime;
    close() — or process exit, including a crash — releases it back to the kernel) and the
    assigned port number.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    sock.bind((host, 0))
    sock.listen(1)
    return sock, sock.getsockname()[1]


# --------------------------------------------------------------------------------------------- #
# Shared: live process-identity probe (independently re-derived pid-recycling-guard shape).
# --------------------------------------------------------------------------------------------- #
def _probe_pid(pid):
    """Return (status, live_command, live_start) for `pid`, sampled from live `ps`.

    `status` is one of three states (security-review FIX-2 — convict/exonerate asymmetry: a
    reaper decision core must EXONERATE on uncertainty, never manufacture a false "dead" out of
    a probe it could not actually run):

      - "alive"        — ps ran cleanly and reports a live process at this pid.
      - "dead"         — ps ran cleanly and CONFIRMS no such process (a clean non-zero/no-output
                          result IS the "no such pid" signal on this platform's `ps`).
      - "inconclusive" — the probe itself could not be trusted: `subprocess.run` raised (e.g. a
                          resource-pressure fork failure, the `ps` binary unavailable, an
                          EINTR-adjacent OSError). NEVER treated as "dead" — a transient probe
                          failure must never cause a mass-reap of live workers.
    """
    try:
        res = subprocess.run(["ps", "-o", "lstart=,command=", "-p", str(pid)],
                              capture_output=True, text=True, check=False)
    except Exception:
        return "inconclusive", None, None
    line = (res.stdout or "").strip()
    if res.returncode != 0 or not line:
        return "dead", None, None
    parts = line.split(None, 5)
    live_start = " ".join(parts[:5]) if len(parts) >= 5 else None
    live_cmd = parts[5] if len(parts) >= 6 else (line if not live_start else None)
    return "alive", live_cmd, live_start


def own_process_signature(pid=None):
    """The (command, start) signature for `pid` (default: the current process) — the stamp a
    worker records at allocation time so the reaper can later verify identity."""
    pid = pid or os.getpid()
    _status, cmd, start = _probe_pid(pid)
    return cmd, start


# --------------------------------------------------------------------------------------------- #
# AC-PWE-3: per-worker allocation ownership records + the pure reap decision core.
# --------------------------------------------------------------------------------------------- #
def _alloc_dir(base_dir=None):
    base = base_dir or os.path.join(
        os.environ.get("CLAUDE_PROJECT_DIR") or _REPO_ROOT, ".foundry", "per-worker-env"
    )
    d = os.path.join(base, "allocations")
    os.makedirs(d, exist_ok=True)
    return d


def _alloc_record_filename(worker_id):
    """Hash `worker_id` into a safe filename component (security-review FIX-3; mirrors
    `_mutex_lock_path`'s treatment of `release_id`). `worker_id` is EXTERNAL input (a
    worker/session identifier) and must NEVER flow unsanitized into a path join — a
    `../../..`-shaped worker_id must not escape the allocations dir on record/release. Hashing
    makes escape STRUCTURALLY impossible (a hex digest has no path separators), rather than
    relying on a reject-list that could miss a variant."""
    digest = hashlib.sha256(str(worker_id).encode("utf-8")).hexdigest()[:24]
    return "%s.json" % digest


def record_allocation(worker_id, port, holder_pid=None, base_dir=None):
    """Atomically record ownership of a port allocation (temp+rename — never a partial-write
    a concurrent reader could observe), stamped with the holder pid's LIVE process-identity
    signature at record time (the pid-recycling guard). The RAW `worker_id` is preserved inside
    the record body (for identification by callers); only the on-disk FILENAME is hashed (FIX-3).
    Returns the record path."""
    holder_pid = holder_pid if holder_pid is not None else os.getpid()
    d = _alloc_dir(base_dir)
    cmd, start = own_process_signature(holder_pid)
    rec = {
        "worker_id": worker_id, "port": port, "holder_pid": holder_pid,
        "holder_command": cmd, "holder_start": start, "recorded_at": time.time(),
    }
    path = os.path.join(d, _alloc_record_filename(worker_id))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-alloc-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def release_allocation(worker_id, base_dir=None):
    """Cooperative release on normal completion/abort (AC-PWE-3 happy path) — remove the
    ownership record. Idempotent: a missing record is a no-op (already released)."""
    d = _alloc_dir(base_dir)
    path = os.path.join(d, _alloc_record_filename(worker_id))
    try:
        os.unlink(path)
        return True
    except FileNotFoundError:
        return False


def list_allocations(base_dir=None):
    """All current allocation records (each annotated with its own `_path`)."""
    d = _alloc_dir(base_dir)
    out = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json"):
            continue
        p = os.path.join(d, fn)
        # Defence-in-depth (same posture as foundry-env-hygiene.py): never follow a symlink.
        if os.path.islink(p) or not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8") as fh:
                rec = json.load(fh)
        except Exception:
            continue  # unreadable record -> fail-safe, skip (never guessed-at)
        rec["_path"] = p
        out.append(rec)
    return out


def decide_stale_allocations(records):
    """PURE decision core (AC-PWE-3, fixture-testable, no I/O beyond the ps probe already
    embedded in each record's identity check). Returns the subset of `records` that are
    REAPABLE — owned by a no-longer-live worker:

      * holder pid probe is "inconclusive" (the `ps` probe itself raised/could not be trusted)
        -> NEVER reapable (security-review FIX-2 — convict/exonerate asymmetry: a transient
        probe failure must EXONERATE, never manufacture a false "dead" that would mass-reap
        live workers).
      * holder pid probe is "dead" (ps ran cleanly and CONFIRMS no such process) -> reapable
        (the owner is gone; its allocation leaked).
      * holder pid probe is "alive" AND its LIVE start-time matches the recorded `holder_start`
        -> NEVER reapable (a live concurrent worker's allocation — the AC-PWE-3 safety floor).
        Start-time is the immutable PID-recycling anchor (security-review FIX-1): `command` is
        advisory-only and is NOT required to match for preservation, because a live worker that
        `exec()`s into its real service (record-then-exec, or any argv/setproctitle rewrite)
        keeps its pid+start-time but changes its command — requiring a command match would
        wrongly convict that still-live worker.
      * holder pid probe is "alive" but the LIVE start-time does NOT match -> reapable (the pid
        was recycled; the ORIGINAL owner is definitely gone).
      * a malformed/missing holder pid -> reapable (it can never be a confirmed live owner).
    """
    reapable = []
    for rec in records or []:
        if not isinstance(rec, dict):
            continue  # malformed -> fail-safe, skip entirely (never guessed-at)
        pid = rec.get("holder_pid")
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            reapable.append(rec)
            continue
        status, _live_cmd, live_start = _probe_pid(pid)
        if status == "inconclusive":
            continue  # probe uncertainty -> EXONERATE (FIX-2), never reap on an unproven probe
        if status == "dead":
            reapable.append(rec)
            continue
        # status == "alive": preserve iff the immutable start-time anchor matches (FIX-1).
        if live_start == rec.get("holder_start"):
            continue  # LIVE owner, start-time confirmed -> NEVER reap
        reapable.append(rec)  # alive but start-time mismatch -> pid recycled, original owner gone
    return reapable


def reap_stale_allocations(base_dir=None):
    """Reap ONLY allocations owned by a no-longer-live worker (AC-PWE-3). Invoked from
    `hooks/foundry-env-reap.sh` (SessionEnd) alongside the existing env-hygiene reap, and
    callable on-demand. Returns the list of reaped worker_ids."""
    records = list_allocations(base_dir)
    stale = decide_stale_allocations(records)
    reaped = []
    for rec in stale:
        p = rec.get("_path")
        try:
            os.unlink(p)
            reaped.append(rec.get("worker_id"))
        except FileNotFoundError:
            pass
    return reaped


# --------------------------------------------------------------------------------------------- #
# AC-PWE-2: conductor mutex — single-writer flock keyed by a CANONICALIZED release id.
# --------------------------------------------------------------------------------------------- #
def canonicalize_release_id(release_id):
    """Two spellings of the SAME release id map to the SAME canonical key: case-folded,
    outer-whitespace-stripped, internal whitespace collapsed, an optional leading 'v'/'V'
    (version-prefix) normalized away.

    Security-review FIX-4: whitespace is collapsed BEFORE the leading-'v' check is applied via a
    single regex that tolerates whitespace BETWEEN the 'v' and the version digits (`"v 1.0"` and
    `"v1.0"` must converge to the SAME key — a v-strip that only matched `s[1].isdigit()` missed
    this and would let two spellings of the SAME release acquire the mutex independently, the
    exact double-conductor race AC-PWE-2 exists to prevent). Collision-safety is preserved: the
    strip only fires when 'v'/whitespace is immediately followed by a digit, so a non-version-
    shaped id (or two genuinely DISTINCT ids) never collapses onto each other.
    """
    s = (release_id or "").strip().lower()
    s = " ".join(s.split())  # collapse all internal whitespace to single spaces
    s = re.sub(r"^v\s*(?=\d)", "", s)  # strip a leading 'v' (+ any whitespace) only before a digit
    return s


def _mutex_lock_dir(base_dir=None):
    base = base_dir or os.path.join(
        os.environ.get("CLAUDE_PROJECT_DIR") or _REPO_ROOT, ".foundry", "per-worker-env"
    )
    d = os.path.join(base, "conductor-locks")
    os.makedirs(d, exist_ok=True)
    return d


def _mutex_lock_path(release_id, base_dir=None):
    canon = canonicalize_release_id(release_id)
    digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()[:24]
    return os.path.join(_mutex_lock_dir(base_dir), "%s.lock" % digest)


def acquire_conductor_mutex(release_id, base_dir=None, blocking=False):
    """Acquire the single-writer conductor mutex for `release_id` (AC-PWE-2), keyed by its
    CANONICALIZED form. FAIL-CLOSED default: `blocking=False` uses `LOCK_EX | LOCK_NB` — a
    driver that cannot immediately acquire gets `None` back and MUST NOT proceed to drive the
    release. Returns an opaque lock-fd handle on success (pass to `release_conductor_mutex`),
    or `None` on refusal.

    Staleness signal = the kernel-held flock itself (see module docstring): the kernel
    auto-releases a dead holder's lock at close()/process-exit (including SIGKILL), so
    "detect-stale -> reclaim -> re-acquire" is not a multi-step client protocol — it is this ONE
    `flock()` syscall, atomic by construction, so two conductors racing the SAME stale lock
    cannot both win.
    """
    path = _mutex_lock_path(release_id, base_dir)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    flags = fcntl.LOCK_EX if blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        fcntl.flock(fd, flags)
    except OSError:
        os.close(fd)
        return None
    return fd


def release_conductor_mutex(handle):
    """Release a handle returned by `acquire_conductor_mutex`. `None` is a no-op."""
    if handle is None:
        return
    try:
        fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        os.close(handle)


