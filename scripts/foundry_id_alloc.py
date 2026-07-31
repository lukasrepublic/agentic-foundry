#!/usr/bin/env python3
"""Concurrency-safe ID-allocation mutex seam (ER #46, feat-foundry-id-allocation-mutex,
AC-IDMUTEX-1/2/3).

`allocate_id(counter_file)` is the missing first-party primitive: it takes an EXCLUSIVE advisory lock
(`fcntl.flock(LOCK_EX)`) on a dedicated sidecar lock file and holds it around the WHOLE durable
read-increment-write of a shared integer counter, so N parallel sessions allocating the next monotonic
ID (next spec number / next atom number) never collide and every caller gets a distinct, strictly
monotonic value. The seam is callable by the intake/dispatch path (a single-writer-under-lock
allocator); it owns ONLY race-free allocation, not the ID naming/namespace policy (which counter —
spec-number vs atom-number — is the caller's choice, via the `counter_file` argument).

Threat model — TRUSTED OPERATOR. This is a concurrency-correctness primitive, NOT a security boundary:
the lock guards a local counter against a benign race between the operator's own cooperating sessions,
never against an adversary (who could simply not call the allocator). No auth / secret / supply-chain
surface.

Design (researched POSIX single-host primitives — see the spec §Design):
  - `fcntl.flock(LOCK_EX)`, NOT `O_EXCL` "create-or-fail" lockfile. flock is a KERNEL-HELD advisory
    lock against an open file descriptor: the kernel RELEASES it automatically on `close()` /
    process-exit, so a crashed/killed holder CANNOT leave a stale lock — eliminating the entire
    stale-lock failure class (PID-liveness / mtime-TTL detection) that plagues O_EXCL lockfiles. This
    is the industry-standard single-host mutex for "serialize a counter read-modify-write" (it is how
    flock(1), pip's lock, and most build-tool counters do it). NOTE: the sidecar lock FILE persists
    (we never unlink it); only the LOCK auto-releases on fd close/exit — the crash-safe property is
    "no stale LOCK," not "no lock FILE."
  - The critical section is the WHOLE read -> increment -> write, held under ONE lock acquisition
    (never released-then-reacquired), so there is no TOCTOU window between observing and persisting.
  - Durable write: write + flush + os.fsync (in place) so the new value survives a crash of a later
    unrelated process. A crash BETWEEN acquire and the durable write leaves the counter at its
    pre-call value (the ID is simply not consumed) — never a half-written / corrupt value.
  - Fresh lock fd per call: allocate_id() open()s a NEW lock fd inside each call and close()s it before
    return. A fd cached at import does NOT serialize concurrent THREADS in one process (flock on an
    already-held fd just re-converts and returns) — the per-call fresh fd + os.fork (independent file
    descriptions) is the correct serialization.
  - Counter bootstrap: a missing/empty counter file is created UNDER the lock and the first allocated
    ID is 1 (last=0). A non-integer / corrupt counter value FAILS CLOSED (raises) — never treat-as-0,
    which would re-issue already-handed-out IDs.

POSIX precondition: `fcntl` is POSIX-only (foundry runs darwin/linux). Single-host only — flock does
not serialize across NFS / multiple hosts; foundry's parallel sessions share one local checkout (the
documented assumption). The import is guarded so an import on a non-POSIX platform raises a clear error.
"""
import os

try:
    import fcntl  # POSIX-only; the single-host advisory-lock primitive this seam is built on.
except ImportError as _e:  # pragma: no cover - foundry runs POSIX (darwin/linux)
    fcntl = None
    _FCNTL_IMPORT_ERROR = _e


class IDAllocError(Exception):
    """Raised on a fail-closed allocation condition (non-POSIX platform; corrupt counter value)."""


def _lock_path(counter_file):
    """The dedicated sidecar lock path for a counter — `<counter_file>.lock`. The lock is taken on the
    sidecar (never on the counter file itself) so the counter is never truncated/replaced while the
    lock fd is open."""
    return os.fspath(counter_file) + ".lock"


def _read_counter(counter_file):
    """Read the current last-allocated value from an OPEN-and-LOCKED critical section. A missing/empty
    file bootstraps to 0 (=> first allocated ID is 1). A non-integer / corrupt value FAILS CLOSED
    (raises IDAllocError) — never silently treated as 0, which would re-issue handed-out IDs."""
    try:
        with open(counter_file, "r", encoding="utf-8") as fh:
            raw = fh.read().strip()
    except FileNotFoundError:
        return 0
    if raw == "":
        return 0
    try:
        return int(raw)
    except ValueError as e:
        raise IDAllocError(
            f"counter file {counter_file!r} holds a non-integer value {raw!r}; "
            f"refusing to treat-as-0 (would re-issue already-allocated IDs)"
        ) from e


def _write_counter_durable(counter_file, value):
    """Durably persist the new last-allocated value: write + flush + os.fsync, so the value survives a
    later unrelated crash. The write happens INSIDE the held lock so it is part of the critical
    section."""
    with open(counter_file, "w", encoding="utf-8") as fh:
        fh.write(str(value))
        fh.flush()
        os.fsync(fh.fileno())


def allocate_id(counter_file):
    """Atomically allocate the next monotonic ID from a shared counter file (AC-IDMUTEX-1/2/3).

    Acquires an EXCLUSIVE advisory lock (fcntl.flock(LOCK_EX)) on the sidecar `<counter_file>.lock`
    BEFORE reading, holds it for the WHOLE read-increment-durable-write, and releases it on return
    (and automatically on process exit if the holder dies). Returns the newly allocated ID (last+1).

    The acquire BLOCKS until the lock is free (cooperating-session fairness is acceptable — the
    documented residual). The lock fd is opened fresh per call and closed before return.

    Raises IDAllocError on a non-POSIX platform (no fcntl) or a corrupt (non-integer) counter value.
    """
    if fcntl is None:  # pragma: no cover - foundry runs POSIX
        raise IDAllocError(
            f"fcntl unavailable (non-POSIX platform); allocate_id requires POSIX flock: {_FCNTL_IMPORT_ERROR}"
        )

    counter_file = os.fspath(counter_file)
    lock_path = _lock_path(counter_file)

    # Fresh lock fd per call (NOT cached at import) — independent file description so concurrent
    # processes (os.fork) each contend for the lock rather than sharing an already-held one.
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        # Blocking exclusive acquire — the entire RMW below is serialized behind this one lock.
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            # --- critical section: read -> increment -> durable write, all under the held lock. ---
            current = _read_counter(counter_file)
            new_value = current + 1
            _write_counter_durable(counter_file, new_value)
            return new_value
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)  # the LOCK also auto-releases here / on process exit (crash-safety).
