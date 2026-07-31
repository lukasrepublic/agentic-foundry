#!/usr/bin/env python3
"""feat-foundry-environment-isolation-hygiene — Ryuk-style own-scoped resource reaper.

Agents that spin up ephemeral dev/test RESOURCES (containers, kind/k3d/minikube clusters,
LocalStack, dev servers) must bring them DOWN when the work is done. This engine is the
enforcement for the *forgetful* agent (a session that ends without tidying up): it reaps ONLY
the per-session resources THIS session owns, reconciled from live reality.

Design (audit-workflow-runtime-decouple lesson): a PURE decision core + a thin subprocess
executor. `decide_reap(candidates, session_id)` is pure and fixture-testable (proven by
scripts/foundry_checks/env-hygiene.py --selftest, no real daemon touched); command execution
is a thin adapter.

The load-bearing safety floor (§8 re-ground — Ryuk's OWNERSHIP model, not its trigger):

  * OWNERSHIP is an explicit marker: a container `foundry.session=<id>` label, a
    `foundry-<session-id>` cluster name, or a dev-server pid recorded WITH its process-identity
    signature (command + start-time). It is NEVER a start-snapshot delta.
  * The shared container-runtime daemon/host (Colima / Docker / Podman) is shared singleton
    infrastructure — it is NEVER auto-brought-down, only surfaced by `status`. Auto-stopping a
    shared daemon is racy under this workspace's defining mode (parallel sessions): Session A
    must never bring down a runtime Session B is actively reusing.
  * Tunnels / VPN are status-only — NEVER auto-reaped (highest connectivity risk).
  * The reap RECONCILES markers against LIVE reality (query the live daemon / `kind get
    clusters` / `ps`) — it never replays a persisted "kill-these" teardown LIST.
  * PID-recycling guard: a recorded pid is terminated only if it is alive AND its live process
    identity matches the recorded signature; on any mismatch it is SKIPPED (never kill a
    recycled/unrelated process).
  * Fail-safe: on missing/ambiguous ownership the reap set is EMPTY. No blind
    `prune`/`system prune`/kill-all, ever.

The reaper fires from the native SessionEnd hook (hooks/foundry-env-reap.sh) — the
`trap`/`finally` happy-path class — so it covers the orderly-exit forgetful agent, NOT a hard
crash (crash-survival = the deferred external reaper, out of scope; surfaced by `status`).
"""
import json
import os
import shutil
import signal
import subprocess
import sys

# The ownership label key (Ryuk-style). Stamped on containers at create-time via `--label`.
SESSION_LABEL_KEY = "foundry.session"

# Kinds that are shared/connectivity infrastructure — surfaced by status, NEVER in a reap set.
STATUS_ONLY_KINDS = frozenset({"daemon", "tunnel"})


# --------------------------------------------------------------------------------------------- #
# Ownership derivation (the create-time contract the agent/skill honors).
# --------------------------------------------------------------------------------------------- #
def expected_cluster_name(session_id):
    """The deterministic session-derived cluster name — the cluster-name analog of the
    `foundry.session=<id>` label. A cluster is owned iff its name equals this."""
    return "foundry-%s" % session_id


def container_label_args(session_id):
    """The `--label` args an agent adds at container create-time to stamp ownership."""
    return ["--label", "%s=%s" % (SESSION_LABEL_KEY, session_id)]


# --------------------------------------------------------------------------------------------- #
# THE PURE DECISION CORE — no I/O, no subprocess, no clock. Fixture-testable.
# --------------------------------------------------------------------------------------------- #
def decide_reap(candidates, session_id):
    """Return the subset of `candidates` this session OWNS and may bring down.

    Each candidate is a dict already reconciled against live reality by the caller:
      {"kind": "container", "id": <id>, "session_label": <label-value-or-None>}
      {"kind": "cluster",   "id": <name>, "name": <name>}
      {"kind": "pid",       "id": <pid>, "pid": <pid>, "alive": <bool>,
                            "record": {"session": <id>, "command": <str>, "start": <str>},
                            "live_command": <str>, "live_start": <str>}
      {"kind": "daemon", ...}   # shared runtime host — status-only
      {"kind": "tunnel", ...}   # VPN/tunnel — status-only

    The safety floor is enforced here:
      * daemon / tunnel are NEVER included.
      * with no session identity, the reap set is EMPTY (fail-safe).
      * a container is owned iff its `foundry.session` label == this session.
      * a cluster is owned iff its name == `foundry-<session-id>`.
      * a pid is owned iff its record.session == this session AND it is alive AND its live
        process identity matches the recorded (command, start) signature (pid-recycling guard);
        a dead pid is already gone (excluded, idempotent); an identity MISMATCH is SKIPPED.
      * an unknown kind or missing/ambiguous marker is EXCLUDED (fail-safe).
    """
    reap = []
    for c in candidates or []:
        if not isinstance(c, dict):
            continue  # malformed → fail-safe
        kind = c.get("kind")
        if kind in STATUS_ONLY_KINDS:
            continue  # shared singleton infra / connectivity — never auto-reaped
        if not session_id:
            continue  # no session identity → reap nothing (fail-safe)
        if kind == "container":
            if c.get("session_label") == session_id:
                reap.append(c)
        elif kind == "cluster":
            if c.get("name") == expected_cluster_name(session_id):
                reap.append(c)
        elif kind == "pid":
            rec = c.get("record") or {}
            if rec.get("session") != session_id:
                continue  # not ours
            if not c.get("alive"):
                continue  # already gone → nothing to bring down (idempotent)
            # PID-recycling guard: identity must match the recorded signature.
            if c.get("live_command") == rec.get("command") and c.get("live_start") == rec.get("start"):
                reap.append(c)
            # else: recycled/unrelated pid → SKIP (never kill an unowned process)
        # unknown kind → fail-safe, skip
    return reap


# --------------------------------------------------------------------------------------------- #
# THE THIN EXECUTOR — brings down ONE owned resource via its idempotent down-verb.
# Tolerant of already-gone. NEVER brings down the shared runtime daemon/host or a tunnel.
# --------------------------------------------------------------------------------------------- #
def _run(argv, run=subprocess.run):
    try:
        run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return True
    except Exception:
        return False  # tolerant of already-gone / missing binary


def reap_resource(c, run=subprocess.run):
    """Bring down one OWNED resource idempotently. Returns a human label of what was reaped."""
    kind = c.get("kind")
    if kind == "container":
        cid = c.get("id")
        # docker stop then docker rm — both idempotent / tolerant of already-gone.
        _run(["docker", "stop", str(cid)], run=run)
        _run(["docker", "rm", str(cid)], run=run)
        return "container %s" % cid
    if kind == "cluster":
        name = c.get("name") or c.get("id")
        # kind delete cluster --name <session> — idempotent / tolerant of already-gone.
        _run(["kind", "delete", "cluster", "--name", str(name)], run=run)
        return "cluster %s" % name
    if kind == "pid":
        pid = c.get("pid") or c.get("id")
        try:
            pid_int = int(pid)
        except (ValueError, TypeError):
            return "pid %s (skipped: non-integer)" % pid
        # Defence-in-depth (security review Risk-2): a non-positive pid makes os.kill signal a
        # process GROUP (pid<0) or the caller's own group (pid==0) — a broadcast kill far beyond
        # one dev-server. Refuse anything that is not a specific, non-system pid. This never
        # relies on `ps -p` semantics for negatives.
        if pid_int <= 1:
            return "pid %s (skipped: non-positive/system pid)" % pid
        try:
            os.kill(pid_int, signal.SIGTERM)  # terminate the owned dev-server pid
        except (ProcessLookupError, ValueError, PermissionError):
            pass  # already gone / not ours — tolerant
        return "pid %s" % pid_int
    return "unknown %r" % (c.get("id"),)


# --------------------------------------------------------------------------------------------- #
# LIVE-REALITY SAMPLERS (impure) — enumerate candidate resources from the live daemon / ps /
# the per-session pid-record. NOT exercised by the selftest (which feeds decide_reap fixtures).
# --------------------------------------------------------------------------------------------- #
def _which(name):
    return shutil.which(name) is not None


def _sample_containers():
    """foundry-labelled containers from the live daemon (never the daemon itself)."""
    out = []
    if not _which("docker"):
        return out
    try:
        fmt = '{{.ID}}\t{{.Label "%s"}}' % SESSION_LABEL_KEY
        res = subprocess.run(
            ["docker", "ps", "-a", "--filter", "label=%s" % SESSION_LABEL_KEY, "--format", fmt],
            capture_output=True, text=True, check=False,
        )
        for line in (res.stdout or "").splitlines():
            if "\t" not in line:
                continue
            cid, label = line.split("\t", 1)
            if cid.strip():
                out.append({"kind": "container", "id": cid.strip(),
                            "session_label": (label.strip() or None)})
    except Exception:
        pass
    return out


def _sample_clusters():
    out = []
    if not _which("kind"):
        return out
    try:
        res = subprocess.run(["kind", "get", "clusters"], capture_output=True, text=True, check=False)
        for name in (res.stdout or "").split():
            name = name.strip()
            if name:
                out.append({"kind": "cluster", "id": name, "name": name})
    except Exception:
        pass
    return out


def _pid_record_dir(project_dir):
    return os.path.join(project_dir, ".foundry", "env-hygiene")


def _sample_pids(project_dir):
    """Read per-session pid-record files (ownership MARKERS) and reconcile against live `ps`."""
    out = []
    rec_dir = _pid_record_dir(project_dir)
    if not os.path.isdir(rec_dir):
        return out
    for fn in sorted(os.listdir(rec_dir)):
        if not fn.endswith(".json"):
            continue
        rec_path = os.path.join(rec_dir, fn)
        # Defence-in-depth (security review Risk-3): never follow a symlinked record (a planted
        # symlink could redirect the read); skip anything that isn't a plain file.
        if os.path.islink(rec_path) or not os.path.isfile(rec_path):
            continue
        try:
            with open(rec_path, encoding="utf-8") as fh:
                rec = json.load(fh)
        except Exception:
            continue  # unreadable record → fail-safe, skip
        pid = rec.get("pid")
        # Reject a missing / non-integer / non-positive / system pid at read-time (the executor
        # also guards, but reconcile fail-safe here too — never enumerate a broadcast-kill pid).
        try:
            if pid is None or int(pid) <= 1:
                continue
        except (ValueError, TypeError):
            continue
        alive, live_cmd, live_start = _probe_pid(pid)
        out.append({"kind": "pid", "id": pid, "pid": pid, "alive": alive,
                    "record": rec, "live_command": live_cmd, "live_start": live_start})
    return out


def _probe_pid(pid):
    """Return (alive, live_command, live_start) for a pid, sampled from live `ps`."""
    try:
        res = subprocess.run(["ps", "-o", "lstart=,command=", "-p", str(pid)],
                             capture_output=True, text=True, check=False)
        line = (res.stdout or "").strip()
        if res.returncode != 0 or not line:
            return False, None, None
        # `ps -o lstart=,command=` → "<24-char lstart> <command...>"
        parts = line.split(None, 5)
        live_start = " ".join(parts[:5]) if len(parts) >= 5 else None
        live_cmd = parts[5] if len(parts) >= 6 else (line if not live_start else None)
        return True, live_cmd, live_start
    except Exception:
        return False, None, None


def _detect_daemon():
    """Surface the shared container-runtime host as a status-only entry (never reaped)."""
    for rt in ("colima", "docker", "podman"):
        if _which(rt):
            return {"kind": "daemon", "id": rt}
    return None


def enumerate_candidates(session_id, project_dir):
    """All live candidate resources across every covered kind + the status-only infra."""
    cands = []
    cands.extend(_sample_containers())
    cands.extend(_sample_clusters())
    cands.extend(_sample_pids(project_dir))
    daemon = _detect_daemon()
    if daemon:
        cands.append(daemon)
    return cands


# --------------------------------------------------------------------------------------------- #
# CLI: status | reap
# --------------------------------------------------------------------------------------------- #
def _resolve_session(explicit):
    return explicit or os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("FOUNDRY_SESSION_ID") or ""


def _resolve_project_dir():
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def _is_owned(c, session_id):
    return bool(decide_reap([c], session_id))


def cmd_status(session_id, project_dir, out=sys.stdout):
    cands = enumerate_candidates(session_id, project_dir)
    owned, orphans, infra = [], [], []
    for c in cands:
        if c.get("kind") in STATUS_ONLY_KINDS:
            infra.append(c)
        elif _is_owned(c, session_id):
            owned.append(c)
        else:
            orphans.append(c)  # a prior/non-current session's resource → manual-clear candidate
    print("env-hygiene status (session=%s)" % (session_id or "<none>"), file=out)
    print("  owned by this session (reaped on SessionEnd):", file=out)
    for c in owned:
        print("    - %s %s" % (c.get("kind"), c.get("id")), file=out)
    print("  shared runtime daemon (running; NEVER auto-brought-down — stop it manually if you wish):", file=out)
    for c in infra:
        print("    - %s %s [status-only]" % (c.get("kind"), c.get("id")), file=out)
    print("  foundry-labelled orphans from a prior/non-current session (MANUAL-clear candidates):", file=out)
    for c in orphans:
        print("    - %s %s" % (c.get("kind"), c.get("id")), file=out)
    return 0


def cmd_reap(session_id, project_dir, out=sys.stdout):
    if not session_id:
        print("env-hygiene reap: no session id → fail-safe, reaped nothing", file=out)
        return 0
    cands = enumerate_candidates(session_id, project_dir)
    to_reap = decide_reap(cands, session_id)
    left = [c for c in cands if c not in to_reap]
    reaped = [reap_resource(c) for c in to_reap]
    print("env-hygiene reap (session=%s)" % session_id, file=out)
    print("  reaped (own-scoped): %s" % (", ".join(reaped) if reaped else "<none>"), file=out)
    left_desc = ", ".join("%s %s" % (c.get("kind"), c.get("id")) for c in left) or "<none>"
    print("  deliberately LEFT (shared runtime daemon / tunnels / other sessions): %s" % left_desc, file=out)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    session_id, cmd = "", None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--session" and i + 1 < len(argv):
            session_id = argv[i + 1]; i += 2; continue
        if a in ("status", "reap"):
            cmd = a; i += 1; continue
        i += 1
    session_id = _resolve_session(session_id)
    project_dir = _resolve_project_dir()
    if cmd == "reap":
        return cmd_reap(session_id, project_dir)
    return cmd_status(session_id, project_dir)


if __name__ == "__main__":
    sys.exit(main())
