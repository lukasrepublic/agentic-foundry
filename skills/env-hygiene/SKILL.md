---
name: env-hygiene
description: Environment-isolation-hygiene — own-scoped ephemeral-env lifecycle + teardown (/foundry:env-hygiene). Status of this session's owned dev/test resources (containers, kind/k3d/minikube clusters, LocalStack, dev servers) + the shared runtime daemon; on-demand own-scoped teardown; the reuse-before-start / label-what-you-start / pair-setup-with-teardown / never-stop-the-shared-runtime directive. Trigger to check what this session left running, tear down own resources, or learn the hygiene lifecycle.
---

# /foundry:env-hygiene

The environment-isolation-hygiene lifecycle. Agents that spin up ephemeral dev/test
RESOURCES must bring them DOWN when the work is done — no stacking, no leaks. The enforcement
for the *forgetful* agent is automatic (a SessionEnd reaper); this skill is the on-demand
`status` + `teardown` surface plus the discipline.

## The directive (own what you start)

- **Reuse before start.** Check whether a suitable resource is already running before firing
  up a new one (reference-counting spirit) — don't stack a second Colima/kind/dev-server.
- **LABEL what you start with the session id.** Stamp ownership at create-time so the reaper
  can identify your resources (and only yours):
  - containers: `docker run --label foundry.session=$CLAUDE_SESSION_ID …`
  - clusters: name it `foundry-$CLAUDE_SESSION_ID` (`kind create cluster --name foundry-$CLAUDE_SESSION_ID`)
  - dev servers: record the pid **with its command + start-time** under
    `.foundry/env-hygiene/<name>.json` so the pid-recycling guard has a signature to verify.
- **Pair setup with teardown.** Every fire-up has a matching bring-down at end of work.
- **Never stop the shared runtime.** The container-runtime daemon/host (Colima/Docker/Podman)
  is shared singleton infrastructure — leave it running; `status` surfaces it, the operator
  stops it manually if they wish. Tunnels/VPN are status-only too.
- **Leave no orphans.** What you started, you bring down (own-scoped).

## Actions

| action | what it does |
|---|---|
| **status** | Lists this session's **owned** resources (reaped on SessionEnd); the shared **runtime daemon** shown *running but NEVER auto-brought-down*; and any **foundry-labelled orphan** from a prior/non-current session as a **manual-clear candidate**. |
| **teardown** | On-demand own-scoped reap — brings down THIS session's owned resources now (same decision core the SessionEnd hook uses). |

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/foundry-env-hygiene.py status
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/foundry-env-hygiene.py reap    # on-demand teardown
```

## Automatic enforcement (the forgetful-agent backstop)

A hook on the native **`SessionEnd`** event (`hooks/foundry-env-reap.sh`) invokes the
own-scoped reap when the session ends — the `trap`/`finally` happy-path class. It brings down
ONLY resources carrying **this** session's ownership marker (`foundry.session=<id>` label,
`foundry-<id>` cluster name, or a recorded dev-server pid whose live identity matches its
signature), reconciled from **live reality**, idempotently. It **never** brings down the shared
runtime daemon or a tunnel, **never** reaps another session's or an unlabelled resource, and
**never** runs a blind `prune`/kill-all — on any ownership ambiguity it reaps nothing.

## Per-worker ephemeral env: port isolation + conductor mutex (feat-foundry-per-worker-ephemeral-env-and-conductor-mutex, AC-PWE-1..4)

**Gap G7.** N per-atom workers each bringing up identical dev/test services on fixed ports
collided; a duplicate-conductor race let two sessions drive the same release onto shared
worktrees at once. `scripts/foundry_env_isolation.py` is the primitive home
for both fixes (see its module docstring for the full design + industry-grounding).

- **Per-worker port isolation (AC-PWE-1).** `allocate_ephemeral_port()` claims a port via
  **OS-assigned dynamic (ephemeral) port allocation** (`socket.bind((host, 0))`) — the dominant
  CI-parallelism practice. The kernel's `bind(2)` IS the atomic claim; there is no client-side
  read-then-bind window, so concurrently-starting workers structurally cannot collide. Propagate
  the allocated port into the worker's compose/dev-server config (an `env-hygiene`
  implementation concern — this seam only allocates + tracks the claim).
  **Hand-off caveat (security review):** `bind(0)`'s atomicity holds only while the foundry
  socket is HELD OPEN — the `close()` → real-service-`bind()` hand-off is itself a TOCTOU window
  (another process could grab the just-freed port between the two). Consumers should either keep
  the foundry socket open until the real service successfully binds (close it only after), or
  hand off via `SO_REUSEPORT` / fd-passing to the real service. Do NOT overclaim "no
  read-then-bind window" for that external hand-off — the guarantee is scoped to the allocation
  itself, not to what happens after `close()`.
- **Conductor mutex (AC-PWE-2).** `acquire_conductor_mutex(release_id, blocking=False)` takes an
  `fcntl.flock(LOCK_EX|LOCK_NB)` on a sidecar lock file keyed by the release id's CANONICALIZED
  form (so `"v0.19.0"` and `" V0.19.0 "` — and `"v 0.19.0"`, v + whitespace — all contend for the
  SAME lock; whitespace is collapsed BEFORE the leading-`v` strip so a v+whitespace spelling
  isn't missed) — the same kernel-held advisory-lock primitive as `scripts/foundry_id_alloc.py`.
  **FAIL-CLOSED:** a driver that cannot immediately acquire gets `None` and MUST NOT proceed to
  drive the release. The staleness signal is the KERNEL-HELD LOCK ITSELF (not a PID-liveness/TTL
  side record) — a dead holder's lock auto-releases at process exit (including SIGKILL), so
  detect-stale -> reclaim -> re-acquire is one atomic `flock()` syscall, never a non-atomic
  client race.
- **Allocation reaper (AC-PWE-3).** `record_allocation()`/`release_allocation()` track a
  worker's port claim (ownership stamped with the holder pid's live process-identity signature).
  `hooks/foundry-env-reap.sh` (SessionEnd) also invokes `reap_stale_allocations()` — it reclaims
  ONLY an allocation whose holder pid is confirmed no-longer-live; it NEVER reaps a live
  concurrent worker's allocation. Hardened past a straight port (security review):
  preservation anchors on the holder's LIVE START-TIME alone (`command` is advisory-only, since a
  live worker that `exec()`s into its real service changes argv but keeps its pid+start-time); a
  `ps`-probe FAILURE (not a confirmed "no such pid") is treated as INCONCLUSIVE and PRESERVES
  (never convicts on uncertainty — a transient probe failure must never mass-reap live workers);
  and `worker_id` is hashed into the on-disk record filename (mirrors the mutex's release-id
  hashing) so an external `worker_id` can never path-traverse out of the allocations directory.
- **Single-worker path unchanged (AC-PWE-4).** A lone worker's `env-hygiene` teardown selftest
  and its conductor-mutex acquisition are both uncontended — this atom adds isolation for the
  concurrent case without changing the single-worker lifecycle.

Live-seam: `python3 -m pytest tests/test_env_isolation.py -q` (the v0.25.0 test-suite realignment — converted to pytest).

## Scope + non-goals

- **In scope:** containers, kind/k3d/minikube clusters, LocalStack, dev servers / background
  processes — the per-task resources that stack.
- **NOT auto-reaped:** the shared container-runtime daemon/host (status-only), tunnels/VPN
  (status-only), and **hard-crash orphans** (a SIGKILL/OOM session never runs its SessionEnd
  reaper — its orphan is surfaced by `status` for manual clearing, not auto-reaped; true
  crash-survival = the deferred external reaper).

## Anti-patterns

- **Bringing down the shared runtime** because "the session is done" — it's shared singleton
  infra; another parallel session may be actively reusing it. Never do it.
- **A blind `docker system prune` / kill-all** — reap own-scoped from live reality, never
  wholesale.
- **Tearing down a tunnel/VPN automatically** — status-only (highest connectivity risk).
- **Replaying a persisted "kill-these" list** — reconcile ownership markers against live
  reality every time; a stale list misattributes.
