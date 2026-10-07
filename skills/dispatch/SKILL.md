---
name: dispatch
description: Dispatch one ticket's implementation to a worker via the NATIVE Agent tool (/foundry:dispatch). The lean replacement for the bespoke dispatch-queue stack - no queue/flock/manifest for SINGLE-REPO adopters. A MULTI-REPO adopter (workspace + product-clone) adds a minimal target_repo manifest + WorktreeCreate redirect + foundry-wt. Trigger to implement a ticket (or an authorized atom, on the opt-in spec lane) in an isolated worktree.
---

# /foundry:dispatch

The lean dispatch path. Worker spawn = the native **`Agent` tool** (`isolation: "worktree"`,
structured return). For a **single-repo** adopter this REPLACES the bespoke `dispatch-agent` +
`.claude/dispatch-queue/` machinery (queue manifests, flock, `wt claim`, `result.json` polling) -
native primitives subsume it. Several tickets are several `Agent` calls in one message, each in its
own worktree; the native concurrency cap and `/loop` are the orchestration.

The unit of work is a **ticket** (a GitHub issue with a `## Done means` command; see
`context/operating-model.md`). The worker's definition of done is that command exiting 0.

> **Single-repo only.** Native `isolation:worktree` worktrees the **session repo** (the
> workspace). It CANNOT reach a separate gitignored **product clone**, so a **multi-repo**
> adopter (workspace + product-repo) still needs a re-extracted minimum: an explicit
> **`target_repo`**, a per-spawn **dispatch manifest**, the **`WorktreeCreate` hook**
> (`foundry-worktree-create.sh`) that redirects the worktree to the named product repo via
> **`foundry-wt`**, and worktree-time binding of the worker to that named repo (so its PR can only
> land there). See *Multi-repo dispatch* below.

**Native is the only path.** The bespoke process-spawn path (`foundry-fanout` /
`foundry-spawn-worker` + the `FOUNDRY_DISPATCH=bespoke` switch) was removed; nothing consumes the switch.

## When to trigger

- "implement `<ticket>`", "/foundry:dispatch `<ticket>`", "dispatch the worker for `<ticket>`".

## Preconditions (fail-closed)

1. **A ticket with a runnable `Done means`.** Start it with `scripts/foundry-ticket.py start <issue>`
   (the active ticket scopes the paper guard and the Stop hook). A ticket without a `Done means`
   command is refused: "ticket `<x>` has no `## Done means`; add one." On the **opt-in spec lane**
   (`security: true`, or an operator-chosen spec) the atom's `acceptance-contract.yaml` must instead be
   `AUTHORIZED` (`foundry_authz.spec_state`); refuse otherwise: "run `/foundry:authorize` first."
2. **Spawn context.** The `Agent` tool's worktree-isolated worker must run from a
   context whose worker writes are not hook-blocked (a real dispatcher session, NOT a
   plain operator `claude -n` session - there, `worker-cwd-enforcement` fail-closes a
   non-null-`agent_id` subagent that has no `assignment.json`; do the work inline
   instead).
3. **Context-diet lint (advisory).** Before spawning, run the assembled prompt through
   `python3 scripts/foundry_dispatch_lint.py <prompt-file-or-stdin>` - it flags an inlined spec/contract body or a
   per-artifact block over the inline cap, naming the offending span. Advisory / fail-open at
   dispatch time (never blocks a spawn); add `--strict` to make a non-empty finding list a hard
   gate (e.g. in a CI preflight).

## Procedure (single ticket)

1. **Resolve** the ticket (`gh issue view <n>`): its `## Done means` command and `## Paper allowed` list.
2. **Invoke the native `Agent` tool** - no queue, no manifest:
   - `subagent_type`: the engineer agent (`foundry:app-engineer` / `foundry:infra-engineer` / `foundry:framework-engineer` per the ticket's surface).
   - `isolation: "worktree"` - native worktree (auto-cleaned if unchanged). The
     `foundry-cwd-enforce` write-jail composes on top.
   - `schema`: the structured result (below) - REPLACES reading `.agent/result.json`.
   - `prompt`: the worker contract - a **claim check** (AC-WCD-1), not an inline payload:
     > Implement the ticket whose body (`## Done means`, `## Paper allowed`) is at `<issue url>` -
     > fetch it in your own context; do not expect it inlined here beyond a per-artifact cap of `<N>`
     > chars (`$CLAUDE_PROJECT_DIR/.foundry/dispatch-inline-cap`, default 2048). Any evidence
     > artifact you produce (test/build logs) resolves relative to your OWN worktree root - never
     > `$CLAUDE_PROJECT_DIR`. You are done when the `Done means` command exits 0. Follow
     > `context/branch-discipline.md`: build on a branch cut from the integration branch, run
     > `scripts/foundry-test.sh` until green, push once, and open the PR via `gh pr create`; the
     > merge floor (branch protection / required CI checks, plus
     > `hooks/foundry-git-discipline.sh` within sessions) decides the merge. Return the structured
     > result per the worker return contract below (pointers only).
3. **Consume the structured return** (native) - `{branch, pr_url, files_touched, seam_verdict, summary}`. No `result.json` polling; the `Agent` tool returns it directly.
4. **Security lane.** Run the `security-reviewer` agent when the ticket is `security: true` or touches auth/secrets/custody paths; the `security-reviewed` label on the PR is the CI-enforced lane (`.github/workflows/btb-gates-base.yml`, `security-path-base`).
5. **The merge floor.** The PR is admitted by the native merge floor - branch protection / required
   CI checks (`docs/merge-floor.md`), plus `hooks/foundry-git-discipline.sh` for any merge attempted
   from inside a session. Use `/foundry:merge-when-green` to wait for green.

## Worker return contract (claim-check) — AC-WCD-2

RETURN POINTERS ONLY: a status enum and evidence PATHS — NEVER return a full file body.

This rule is stated VERBATIM in every dispatch template (this skill's step-2 worker contract
above) so a compliant worker never
round-trips an artifact it could reference by path instead: a spec, a contract, a diff, a log — the
worker Reads it directly in its own context and returns only the PATH to it (plus the structured
fields below), never its body.

## Result schema (replaces result.json)

```json
{"branch": "<str>", "pr_url": "<str|null>", "files_touched": ["<path>"],
 "seam_verdict": "PASS|FAIL|EVIDENCE-MISSING|NOT-APPLICABLE",  # worker-self-reported live-seam result ("walk_verdict" is the legacy name for this field)
 "summary": "<str>", "status": "ready|failed",
 "evidence": ["<path — walk-evidence / test / build artifact, never the artifact's body>"]}
```

`status` is a closed enum (`ready|failed`); `evidence` and `files_touched` are PATHS, never inlined
bodies (the claim-check rule above).

## Multi-ticket fan-out

Issue several `Agent` calls in one message, each `isolation: "worktree"` (native concurrency cap).
A recurring driver is the native `/loop` + `/foundry:merge-when-green`; there is no foundry wave
orchestrator.

### Per-worker ephemeral env isolation + the conductor mutex (feat-foundry-per-worker-ephemeral-env-and-conductor-mutex, AC-PWE-1..4)

Concurrent fan-out (up to the native Workflow concurrency cap) means N workers may each bring
up an ephemeral dev/test environment at once — a recorded design gap: identical fixed-port
compose services collided and forced live-seam walks to serialize, and a duplicate-conductor
race let two sessions drive the SAME release onto shared worktrees concurrently.
`scripts/foundry_env_isolation.py` fixes both:

- **Non-colliding port/namespace per worker (AC-PWE-1).** Each worker's ephemeral env claims
  its port via `allocate_ephemeral_port()` — **OS-assigned dynamic (ephemeral) ports**
  (`socket.bind((host, 0))`). The kernel's atomic `bind(2)` IS the coordination: concurrently
  STARTING workers cannot be handed the same port (no unsynchronized read-then-bind). **Hand-off
  caveat:** that atomicity holds only while the socket is HELD OPEN — the `close()` →
  real-service-`bind()` hand-off is its own TOCTOU window, so a worker should keep the foundry
  socket open until its real service binds (or hand off via `SO_REUSEPORT`/fd-passing), never
  `close()` then assume the port is still reserved.
- **Conductor mutex — one driver per release (AC-PWE-2).** Before a driver (a `/foundry:dispatch`
  loop) starts DRIVING a release, it acquires
  `acquire_conductor_mutex(release_id, blocking=False)` — `fcntl.flock(LOCK_EX|LOCK_NB)` keyed
  by the release id's CANONICALIZED form, so differently-spelled invocations of the same release
  (including a `v`+whitespace spelling, e.g. `"v 0.19.0"` — whitespace is collapsed BEFORE the
  leading-`v` strip) contend for the SAME lock. **FAIL-CLOSED**: acquisition failure means the
  driver MUST NOT proceed. A dead holder's lock is reclaimable without a separate staleness check
  — the kernel auto-releases a flock at process exit/SIGKILL, so reclaim is the SAME atomic
  `flock()` call, never a racy two-step client protocol.
- **Reaped on worker death (AC-PWE-3).** `hooks/foundry-env-reap.sh` (SessionEnd) also reclaims
  a worker's port allocation if it died abnormally (crash/SIGKILL) without releasing —
  preservation anchors on the holder's LIVE START-TIME alone (`command` is advisory-only: a live
  worker that `exec()`s into its real service changes argv but keeps its pid+start-time), and a
  `ps`-probe FAILURE is treated as inconclusive and PRESERVES rather than convicting a live
  worker — so it NEVER reclaims a still-live concurrent worker's allocation, including under a
  transient probe error.
- **Single-worker path is unchanged (AC-PWE-4).** A lone dispatch acquires the conductor mutex
  without contention and the existing `env-hygiene` teardown lifecycle is untouched.

Live-seam: `python3 -m pytest tests/test_env_isolation.py -q` (converted to pytest in the v0.25.0
test-suite realignment).

## Multi-repo dispatch

When the ticket (or authorized contract) names a **`target_repo`** that is a product-repo KEY
(not `workspace`), dispatch is **main-loop only** and adds a minimal hand-off (the
`Workflow` sandbox cannot write files - dispatch per ticket from `/foundry:dispatch`, under a
native `/loop` if recurring):

1. **Write the manifest** to `<workspace>/.foundry/dispatch-queue/<task>.json` *before* the
   `Agent` spawn: `{target_repo, task, agent, dispatcher_session_id, contract_ref}`
   (atomic temp+rename). `target_repo` MUST equal the authorized contract's `target_repo`.
2. **Spawn** the worker (`Agent`, `isolation: "worktree"`). The `WorktreeCreate` hook
   (`foundry-worktree-create.sh`) consumes the oldest manifest, runs `foundry-wt bind-check`
   (manifest == contract `target_repo`, fail-closed) + `foundry-wt claim <target_repo> …`,
   **stages the atom's spec + acceptance-contract into the claimed worktree** (stage-by-copy, same
   relative path) and **hard-preflights** the staged copies against the frozen
   `authorized.spec_sha256`/`contract_sha256` (feat-foundry-worktree-on-native-isolation,
   AC-WNI-1/-2 — closes a recorded design gap, a jail that previously hid the spec and still reported a
   false-green `exit:0`; see `skills/work-isolation/SKILL.md` → *Spec-staged-and-preflighted
   jail*), and only then prints the **product-repo** worktree path → the worker's cwd. Any
   preflight failure hard-fails the spawn instead (typed diagnostic in `.foundry/dispatch.log`,
   no stdout path). The write-jail then jails to the product worktree (unchanged).
3. **The merge venue.** Steps 1-2 already bind the worker's worktree to the authorized `target_repo`
   (manifest bind-check + spec/contract preflight) — the worker's PR is cut FROM that worktree, so it
   can only land against its own repo. The merge floor (`docs/merge-floor.md` — branch protection /
   required CI checks, plus `hooks/foundry-git-discipline.sh` within sessions) then governs admission
   in that repo the same way it does for single-repo dispatch.

## Context economy — AC-WCD-4

Every dispatch pays a **fixed session-preamble cost** before a worker does anything: the measured
baseline is **67,891 cache-creation tokens** for a worker that replied the single word "READY" (the
SDLC retrospective's cleanest fixed-overhead measurement;
harness-version-sensitive — record the harness version alongside any re-measurement, per this atom's
Residuals). This section documents the levers foundry controls vs. the levers it does not, so a
regression is checkable against that number:

- **foundry-owned (this atom + AC-WCD-1/-2/-3):** the claim-check I/O rule — dispatch prompts
  reference specs/contracts/evidence BY PATH (never inlined beyond the per-artifact cap), and workers
  return pointers + structured fields, never full artifact bodies. This is the transport cost per
  dispatch/fan-out, and it is what a growing corpus scales with if left unchecked (the 0.9.1
  large-spec-inlined-into-args defect re-bought the same 39K-token spec 5× per audit pass before the
  0.9.2 path-based Claim Check fixed that call site).
- **harness-owned, OUTSIDE foundry's control (documented + upstreamable, not reimplemented here):**
  **deferred tool/skill loading** — the full plugin surface (skills, tool schemas) is loaded into a
  worker's preamble whether or not the dispatched task needs it; and **preamble/system-prompt size**
  more generally — the fixed 67,891-token READY cost is paid before any tool call. Where a measured
  gap exists, file it upstream with `gh issue create` rather than reimplementing
  harness-internal deferred loading inside foundry.

Regression check: if a fresh READY-probe measurement (same harness version) diverges materially from
67,891 tokens, treat it as a context-economy regression signal, not noise — re-baseline and record the
harness version with the new number.

## What is REPLACED vs retained

- **REPLACED by native (SINGLE-REPO):** queue manifests, `.lock` flock, `wt claim`,
  WorktreeCreate prereqs, `result.json` polling, fan-out hygiene, deferred-cleanup - subsumed
  by `Agent` **only when the product code IS the session repo**.
- **RE-EXTRACTED for MULTI-REPO:** the `WorktreeCreate` hook + per-spawn manifest +
  `foundry-wt` (KEY to `repos.<key>.path`) + worktree-time repo binding - the irreducible minimum
  native isolation cannot provide. Generalized over `.claude/foundry-project.json` (no
  hardcoded repo names).

## Anti-patterns

- **Re-introducing a queue/flock/manifest for the SINGLE-REPO lean case.** Native worktree
  isolation + structured return already do this. (The minimal `target_repo` manifest is REQUIRED
  for multi-repo - that is not the bespoke queue.)
- **Dispatching a ticket with no `Done means`.** The worker has no way to know it is done.
- **Spawning a writing subagent from a plain operator session** (hook-block) - do the work inline there.
- **Reaching for the retired process-spawn fallback.** It was removed; native `Agent` is the only path.
