"""tests/test_env_isolation.py — converted from scripts/foundry_checks/per-worker-ephemeral-env.py.

Ports the real AC-PWE-1..4 behavioral assertions the drop-in selftest drove directly against the
now-RELOCATED `scripts/foundry_env_isolation.py` (see that module's docstring: it was mistakenly
the SOLE home for this real, load-bearing production primitive — consumed at runtime by
`hooks/foundry-env-reap.sh` and the `dispatch`/`env-hygiene` skill procedures — so it is moved out
of the doomed `scripts/foundry_checks/` directory rather than deleted). CLI/doctor scaffolding is
dropped; the real os.fork()-based concurrency proofs and negative controls are kept.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time

import pytest

from conftest import load_module

env_iso = load_module("scripts/foundry_env_isolation.py", "foundry_env_isolation")

requires_fork = pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX os.fork()")

_N_PARALLEL_PORTS = 12


def _fork_collect(fn, n, *args):
    readers, children = [], []
    for _ in range(n):
        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(r)
            result = fn(*args)
            os.write(w, str(result).encode())
            os.close(w)
            os._exit(0)
        os.close(w)
        readers.append(r)
        children.append(pid)
    out = []
    for r in readers:
        out.append(os.read(r, 32).decode())
        os.close(r)
    for pid in children:
        os.waitpid(pid, 0)
    return out


def _child_allocate_port_hold_briefly(host):
    sock, port = env_iso.allocate_ephemeral_port(host)
    time.sleep(0.05)
    sock.close()
    return port


@requires_fork
def test_ac_pwe_1_nonwriting_ports_distinct_under_real_concurrency():
    ports = [int(p) for p in _fork_collect(_child_allocate_port_hold_briefly, _N_PARALLEL_PORTS, "127.0.0.1")]
    assert len(set(ports)) == _N_PARALLEL_PORTS


class TestCanonicalizeReleaseId:
    def test_whitespace_and_v_prefix_converge(self):
        assert (env_iso.canonicalize_release_id("  V0.99.0-SELFTEST  ")
                == env_iso.canonicalize_release_id("v0.99.0-selftest")
                == env_iso.canonicalize_release_id("V 0.99.0-selftest"))

    def test_v_whitespace_digit_converges(self):
        assert (env_iso.canonicalize_release_id("v 1.0")
                == env_iso.canonicalize_release_id("v1.0")
                == env_iso.canonicalize_release_id("V   1.0"))

    def test_distinct_ids_never_collapse(self):
        assert env_iso.canonicalize_release_id("v10") != env_iso.canonicalize_release_id("v1.0")


def _child_try_acquire(release_id, base_dir, hold_s):
    h = env_iso.acquire_conductor_mutex(release_id, base_dir=base_dir, blocking=False)
    if h is None:
        return "refused"
    time.sleep(hold_s)
    env_iso.release_conductor_mutex(h)
    return "acquired"


@requires_fork
class TestConductorMutex:
    def test_three_spellings_of_same_release_race_to_exactly_one_winner(self, tmp_path):
        base_dir = str(tmp_path)
        rid_a, rid_b, rid_c = "  V0.99.0-SELFTEST  ", "v0.99.0-selftest", "V 0.99.0-selftest"
        readers, children = [], []
        for rid in (rid_a, rid_b, rid_c):
            r, w = os.pipe()
            pid = os.fork()
            if pid == 0:
                os.close(r)
                res = _child_try_acquire(rid, base_dir, 0.3)
                os.write(w, res.encode())
                os.close(w)
                os._exit(0)
            os.close(w)
            readers.append(r)
            children.append(pid)
        results = []
        for r in readers:
            results.append(os.read(r, 32).decode())
            os.close(r)
        for pid in children:
            os.waitpid(pid, 0)
        assert sorted(results) == ["acquired", "refused", "refused"]

    def test_stale_lock_from_sigkilled_holder_is_reclaimed_by_exactly_one(self, tmp_path):
        base_dir = str(tmp_path)
        rid = "v0.99.1-selftest-stale"
        ready_r, ready_w = os.pipe()
        kpid = os.fork()
        if kpid == 0:
            os.close(ready_r)
            h = env_iso.acquire_conductor_mutex(rid, base_dir=base_dir, blocking=False)
            os.write(ready_w, b"x" if h is not None else b"n")
            os.close(ready_w)
            time.sleep(60)
            os._exit(0)
        os.close(ready_w)
        got = os.read(ready_r, 1)
        os.close(ready_r)
        assert got == b"x"
        os.kill(kpid, signal.SIGKILL)
        os.waitpid(kpid, 0)

        readers, children = [], []
        for _ in range(2):
            r, w = os.pipe()
            pid = os.fork()
            if pid == 0:
                os.close(r)
                res = _child_try_acquire(rid, base_dir, 0.2)
                os.write(w, res.encode())
                os.close(w)
                os._exit(0)
            os.close(w)
            readers.append(r)
            children.append(pid)
        results = []
        for r in readers:
            results.append(os.read(r, 32).decode())
            os.close(r)
        for pid in children:
            os.waitpid(pid, 0)
        assert sorted(results) == ["acquired", "refused"]


class TestDecideStaleAllocations:
    def test_exec_change_control_preserved_by_start_time_alone(self, tmp_path):
        """FIX-1: a live worker's allocation is preserved by START-TIME alone; `command` is
        advisory-only. Simulated by tampering the persisted record's `holder_command` (never
        `holder_start`) after a genuine record_allocation() over the current real process."""
        base_dir = str(tmp_path)
        my_pid = os.getpid()
        sock, port = env_iso.allocate_ephemeral_port("127.0.0.1")
        wid = "pwe-selftest-execd"
        try:
            env_iso.record_allocation(wid, port, holder_pid=my_pid, base_dir=base_dir)
            recs = env_iso.list_allocations(base_dir)
            rec = next(r for r in recs if r.get("worker_id") == wid)
            rec["holder_command"] = "totally-different-command-post-exec"
            rec_path = rec.pop("_path")
            with open(rec_path, "w", encoding="utf-8") as fh:
                json.dump(rec, fh)
            stale = env_iso.decide_stale_allocations(env_iso.list_allocations(base_dir))
            assert not any(r.get("worker_id") == wid for r in stale)
        finally:
            env_iso.release_allocation(wid, base_dir=base_dir)
            sock.close()

    def test_probe_failure_is_inconclusive_never_convicts(self):
        """FIX-2: a `ps` probe FAILURE must classify inconclusive and PRESERVE (never reap) a
        live worker. Fault-injected via a scoped monkeypatch of subprocess.run, restored
        immediately after, win or lose."""
        real_run = subprocess.run

        def _raising_run(*_a, **_k):
            raise OSError("simulated ps probe failure")

        my_pid = os.getpid()
        cmd, start = env_iso.own_process_signature(my_pid)
        rec = {"worker_id": "pwe-selftest-probe-fail", "port": 0, "holder_pid": my_pid,
               "holder_command": cmd, "holder_start": start}
        subprocess.run = _raising_run
        try:
            stale = env_iso.decide_stale_allocations([rec])
        finally:
            subprocess.run = real_run
        assert rec not in stale

    def test_dead_pid_is_reapable(self):
        rec = {"worker_id": "x", "port": 0, "holder_pid": 999999999, "holder_command": "gone",
               "holder_start": "never"}
        stale = env_iso.decide_stale_allocations([rec])
        assert rec in stale

    def test_malformed_holder_pid_is_reapable(self):
        rec = {"worker_id": "x", "port": 0, "holder_pid": "not-a-pid"}
        assert env_iso.decide_stale_allocations([rec]) == [rec]


class TestWorkerIdTraversalContainment:
    def test_traversal_worker_id_cannot_escape_allocations_dir(self, tmp_path):
        """FIX-3: `worker_id` is external input and must never escape the allocations dir on
        record/release — hashed into the filename, so a `../../..`-shaped id is contained
        STRUCTURALLY."""
        base_dir = str(tmp_path)
        d = env_iso._alloc_dir(base_dir)
        before = set(os.listdir(d))
        traversal_id = "../../../../../../tmp/pwe-traversal-poc-%d" % os.getpid()
        escape_target = os.path.join("/tmp", "pwe-traversal-poc-%d" % os.getpid())
        path = env_iso.record_allocation(traversal_id, 0, holder_pid=os.getpid(), base_dir=base_dir)
        try:
            assert os.path.dirname(os.path.realpath(path)) == os.path.realpath(d)
            assert not os.path.isfile(escape_target)
        finally:
            env_iso.release_allocation(traversal_id, base_dir=base_dir)
            try:
                os.unlink(escape_target)
            except OSError:
                pass
        assert set(os.listdir(d)) == before


@requires_fork
class TestAllocationLifecycle:
    def test_normal_completion_leaves_no_record(self, tmp_path):
        base_dir = str(tmp_path)

        def _child(ready_w):
            sock, port = env_iso.allocate_ephemeral_port("127.0.0.1")
            env_iso.record_allocation("pwe-selftest-normal", port, base_dir=base_dir)
            os.write(ready_w, str(port).encode())
            os.close(ready_w)
            sock.close()
            env_iso.release_allocation("pwe-selftest-normal", base_dir=base_dir)

        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(r)
            _child(w)
            os._exit(0)
        os.close(w)
        os.read(r, 32)
        os.close(r)
        os.waitpid(pid, 0)
        assert env_iso.list_allocations(base_dir) == []

    def test_reap_stale_allocations_reclaims_only_dead_workers(self, tmp_path):
        base_dir = str(tmp_path)
        env_iso.record_allocation("dead-worker", 12345, holder_pid=999999999, base_dir=base_dir)
        env_iso.record_allocation("live-worker", 12346, holder_pid=os.getpid(), base_dir=base_dir)
        reaped = env_iso.reap_stale_allocations(base_dir)
        assert "dead-worker" in reaped
        assert "live-worker" not in reaped
        remaining = {r["worker_id"] for r in env_iso.list_allocations(base_dir)}
        assert remaining == {"live-worker"}
        env_iso.release_allocation("live-worker", base_dir=base_dir)
