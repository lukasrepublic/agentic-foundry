---
name: dispatch
description: Dispatch one authorized atom's implementation to a worker via the NATIVE Agent tool (/foundry:dispatch). The lean replacement for the bespoke dispatch-queue + dispatch-agent stack — no queue/flock/manifest for SINGLE-REPO adopters. A MULTI-REPO adopter (workspace ⟷ product-clone) adds a minimal target_repo manifest + WorktreeCreate redirect + foundry-wt. Trigger to implement an AUTHORIZED atom in an isolated worktree, or point at workflows/release-wave.js for single-repo multi-atom fan-out.
---

# /foundry:dispatch

The lean dispatch path — a native-primitive REPLACEMENT for the bespoke dispatch stack. Worker spawn = the native **`Agent` tool**
(`isolation: "worktree"`, structured return); multi-atom fan-out = the native
**`Workflow` tool** (`workflows/release-wave.js`). For a **single-repo** adopter
this REPLACES the bespoke `dispatch-agent` + `.claude/dispatch-queue/` machinery (queue
manifests, flock, `wt claim`, WorktreeCreate prereqs, `result.json` polling, fan-out
hygiene) — native primitives subsume it.

> **Single-repo only.** Native `isolation:worktree` worktrees the **session repo** (the
> workspace). It CANNOT reach a separate gitignored **product clone**, so a **multi-repo**
> adopter (workspace ⟷ product-repo, e.g. acme-app, or self-hosting `agentic-foundry/`)
> still needs a re-extracted minimum: an explicit **`target_repo`** (a frozen+authorized
> contract field), a per-spawn **dispatch manifest**, the **`WorktreeCreate` hook**
> (`foundry-worktree-create.sh`) that redirects the worktree to the named product repo via
> **`foundry-wt`**, and worktree-time binding of the worker to that named repo (so its PR can only
> land there). See *Multi-repo dispatch* below. This is NOT the heavyweight bespoke queue — it is
> the one fact native isolation cannot provide (which repo to worktree).

## Native default vs. the deprecated bespoke fallback (feat-foundry-dispatch-on-native-workflow, AC-DNW-1..4)

**Native is the default, unconditionally, for both a single atom and a wave** — a single atom
dispatches via the native `Agent` tool (below); a multi-atom wave dispatches via the native
`Workflow` tool (`workflows/release-wave.js`, see *Multi-atom fan-out*). Neither routes
through the bespoke `dispatch-queue`/process-spawn machinery by default.

The bespoke process-spawn path (`foundry-fanout` / `foundry-spawn-worker` + the
`FOUNDRY_DISPATCH=bespoke` opt-in switch) went through deprecate-then-remove and was **REMOVED** — neither script ships and nothing consumes the switch. Native
`Agent`/`Workflow` dispatch is the only path (`workflows/release-wave.js` for single-repo
multi-atom fan-out).

## When to trigger

- "implement `<atom>`", "/foundry:dispatch `<atom>`", "dispatch the worker for `<atom>`".
- For a release WAVE (N atoms): use `workflows/release-wave.js` via the `Workflow` tool, not N serial dispatches.

## Preconditions (fail-closed)

1. **AUTHORIZED.** The atom's `acceptance-contract.yaml` must be state `AUTHORIZED`
   (`foundry_authz.spec_state`). An un-authorized atom is never dispatched — front
   authorization is the gate. Refuse otherwise: "atom `<x>` is not AUTHORIZED;
   run `/foundry:authorize` first."
2. **Spawn context.** The `Agent` tool's worktree-isolated worker must run from a
   context whose worker writes are not hook-blocked (a real dispatcher session, NOT a
   plain operator `claude -n` session — there, `worker-cwd-enforcement` fail-closes a
   non-null-`agent_id` subagent that has no `assignment.json`; do the work inline
   instead).
3. **Context-diet lint (advisory).** Before spawning, run the assembled prompt through
   `python3 scripts/foundry_dispatch_lint.py <prompt-file-or-stdin>` — it flags an inlined spec/contract body or a
   per-artifact block over the inline cap, naming the offending span. Advisory / fail-open at
   dispatch time (never blocks a spawn); add `--strict` to make a non-empty finding list a hard
   gate (e.g. in a CI preflight).

## Procedure (single atom)

1. **Resolve** the atom spec + its frozen `acceptance-contract.yaml`; confirm AUTHORIZED.
2. **Invoke the native `Agent` tool** — no queue, no manifest:
   - `subagent_type`: the engineer agent (`foundry:app-engineer` / `foundry:infra-engineer` / `foundry:framework-engineer` per the atom's surface).
   - `isolation: "worktree"` — native worktree (auto-cleaned if unchanged). The
     `foundry-cwd-enforce` write-jail composes on top.
   - `schema`: the structured result (below) — REPLACES reading `.agent/result.json`.
   - `prompt`: the worker contract — a **claim check** (AC-WCD-1), not an inline payload:
     > Implement AUTHORIZED atom whose spec + frozen `acceptance-contract.yaml` are BY PATH
     > under `$CLAUDE_PROJECT_DIR/specs/features/…` — resolve and Read them in your own
     > context; do not expect either inlined here beyond a per-artifact cap of `<N>` chars
     > (`$CLAUDE_PROJECT_DIR/.foundry/dispatch-inline-cap`, default 2048 — a short quoted
     > checkpoint is fine, the whole normative region or the whole contract is not). Any
     > code-repo evidence artifact you produce (walk-evidence, test/build logs) resolves
     > relative to your OWN worktree root — never `$CLAUDE_PROJECT_DIR`. Do NOT weaken any
     > frozen acceptance-contract checkpoint. Run the live-seam walk (real boot + Chrome
     > MCP / real seam) against each frozen checkpoint's locator. Cut the PR via
     > `gh pr create`; the merge floor (branch protection / required CI checks, plus
     > `hooks/foundry-git-discipline.sh` within sessions) decides the merge. Return the structured result per the
     > worker return contract below (pointers only), INCLUDING your learning records
     > (friction, successes, distill candidates) in `learnings[]` — the durable
     > worker-emission channel; do NOT rely on files left in the worktree, which is
     > auto-cleaned.
3. **Consume the structured return** (native) — `{branch, pr_url, files_touched, seam_verdict, summary, learnings}`. No `result.json` polling; the `Agent` tool returns it directly. The worker's `learnings[]` are **captured automatically** — a `PostToolUse(Agent|Workflow)` hook (`foundry-harvest-learnings.sh posttooluse`) writes them into `.foundry/session-learnings/<date>/<sid>__<task>.jsonl` (the `/foundry:learn-distill` partition) when the tool call completes. **Do not** run `capture-return` by hand for an `Agent`/`Workflow` dispatch — the hook already did it (a manual run would double-capture). `capture-return` is only for a direct caller that bypasses the `Agent` tool. The PreToolUse(Bash) sidecar harvest is defense-in-depth for any explicit `git worktree remove`.
4. **The merge floor.** The PR is admitted by the native merge floor — branch protection / required
   CI checks (`docs/merge-floor.md`), plus `hooks/foundry-git-discipline.sh` for any merge attempted
   from inside a session. Merge per the atom's `merge_autonomy_mode` (Regular → operator/diff-review; Lean → pr-reviewer pass + merge-on-green (reviewer advisory, never the merge approval)).

**Run-duration capture — release-wave path only (feat-foundry-run-duration-capture, AC-RDC-1..12,
documentation note; this atom claims NO single-atom-dispatch coverage).** The SAME
`PostToolUse(Agent|Workflow)` hook seam step 3 uses for `learnings[]` also carries
`hooks/foundry-run-metrics.sh` (wired one additive entry after the learnings hook, same matcher) —
it appends one gathering-only row per atom to `.foundry/run-metrics.jsonl` when a `workflows/
release-wave.js` wave's tool call completes, host-reading the atom's fidelity fields
(`spec_sha256`/`auth_seq_*`) off its frozen `acceptance-contract.yaml` rather than trusting the
wave's return payload. This single-atom `/foundry:dispatch` path is **not** wired to that ledger —
a `PostToolUse(Agent)` firing here produces no run-metrics row; see the spec's Residuals for the
named follow-up that would add it.

## Worker return contract (claim-check) — AC-WCD-2

RETURN POINTERS ONLY: a status enum, evidence PATHS, and learnings[] — NEVER return a full file body.

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
 "evidence": ["<path — walk-evidence / test / build artifact, never the artifact's body>"],
 "learnings": [{"<record per the learn-distill buffer schema; forwarded UNVALIDATED>": "..."}]}
```

`status` is a closed enum (`ready|failed`); `evidence` and `files_touched` are PATHS, never inlined
bodies (the claim-check rule above). The `learnings[]` records are the **durable worker-emission
channel**: the dispatcher captures them from the return into the session-learnings buffer (step 3),
so they survive the worktree auto-clean by construction — no teardown race, no dependence on reading
the ephemeral worktree. Records are forwarded UNVALIDATED; `/foundry:learn-distill` is the schema
authority.

## Multi-atom fan-out

Run `workflows/release-wave.js` through the `Workflow` tool with `args` = the
list of authorized atom spec paths. It pipelines each atom `implement → verify`
independently (native concurrency cap + journaling + `resumeFromRunId`), replacing the
bespoke fan-out-hygiene checklist + the durable queue for the in-session case.

### Per-worker ephemeral env isolation + the conductor mutex (feat-foundry-per-worker-ephemeral-env-and-conductor-mutex, AC-PWE-1..4)

Concurrent fan-out (up to the native Workflow concurrency cap) means N workers may each bring
up an ephemeral dev/test environment at once — a recorded design gap (a design-partner): identical fixed-port
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
  loop or `release-wave.js` orchestrator) starts DRIVING a release wave, it acquires
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

### Worker definition-of-done self-gate (feat-foundry-worker-dod-self-gate, AC-WDG-1..4)

Closes a recorded design gap (a design-partner empirical driver): a worker that is deterministically destined to BLOCK at the
merge floor can otherwise false-green into a wasted, context-rebuilding resume. Before
`release-wave.js` accepts an implement worker's `status: 'ready'` as ready, the worker's Verify stage
runs the **definition-of-done self-gate** — the ci.yml command battery + the btb-gates lane checks
(re-pointed from the retired bespoke checker) — over the worker's diff (the change
between the dispatch-recorded base sha and the worktree HEAD):

What the shipped probe actually runs (its fixed prompt in `workflows/release-wave.js`): the
ci.yml command battery locally against the candidate branch, every frozen `checkpoints[]`
locator (against the candidate AND against the dispatch-recorded base sha), and a check that
the PR carries its btb-gates lane signal. The dispatch-recorded base sha is captured ONCE, up
front, by a fixed framework-authored probe BEFORE any implement worker starts. (The
authorizing spec also designed allowed-paths diff-subset and `git merge-base` equality checks
— those are design intent not yet in the shipped probe prompt; the `security-path` CI gate and
reviewer pass cover scope today.)

**Mechanized, not trust-the-worker (AC-WDG-1).** The invocation is a SEPARATE, hardcoded `agent()` step
`release-wave.js` always spawns between the implement worker's return and the Verify stage — its prompt
is fixed framework content, never assembled from anything the implement worker said, so a worker cannot
silently skip it. **Result semantics:** if the gate RAN and >=1 check failed, `release-wave.js` marks
the impl `status: 'failed'` with the typed failing-check ids (a non-empty subset of `allowed-paths` /
`checkpoint` / `base-sha`) and a human reason, and SKIPS the Verify stage (AC-WDG-2). If the gate could
NOT be run to a verdict — script errors/absent/crashes/never invoked, DISTINCT from ran-and-failed — it
is treated as a fail-closed `gate-unrunnable` not-ready result; an unrunnable or un-invoked gate is
NEVER a pass (AC-WDG-4, enforced independently by both the script's own exit code and
`normalizeSelfGateResult()`'s pure-compute floor in `release-wave.js`).

**Anti-spoof (AC-WDG-1 mechanization).** There is no bespoke merge-gate module for a worker to shadow —
`scripts/foundry_merge_gate.py` does not exist (it does not exist). The spoof-resistance
instead comes from invocation shape: the self-gate step is a SEPARATE, framework-authored `agent()` call
with a FIXED prompt (`release-wave.js`, never assembled from anything the implement worker said), so a
worker cannot talk its way past it, and `normalizeSelfGateResult()` treats any malformed/absent/never-
produced return as `gate-unrunnable` (fail-closed) rather than a silent pass.

**NOT a trust boundary or security control.** A best-effort producer-side shift-left optimization only
— its sole claim is catching a deterministically-BLOCK-destined worker early. It has NO merge
authority: the native merge floor (`docs/merge-floor.md` — branch protection / required CI checks, plus
`hooks/foundry-git-discipline.sh` within sessions) re-derives its own checks independently and cannot be
bypassed by anything this self-gate reports; a worker that tampers with local state to pass the
self-gate is still caught there.

Live-seam: `python3 -m pytest tests/ -q` green in the worktree (the converted suite carries the self-gate's assertions).

## Multi-repo dispatch

When the atom's authorized contract carries a **`target_repo`** that is a product-repo KEY
(not `workspace`), dispatch is **main-loop only** and adds a minimal hand-off (the
`Workflow` sandbox cannot write files, so `release-wave.js` cannot drive multi-repo —
dispatch per-atom from `/foundry:dispatch` under `/foundry:mode-autonomous`'s `/loop`):

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

## Process-spawn fan-out — RETIRED (historical note)

The bespoke process-spawn orchestrator (`scripts/foundry-fanout`), its worker primitive
(`scripts/foundry-spawn-worker`, including the persona-scoped Bash grant and headless
`claude -p` mechanics this section used to specify), and the `FOUNDRY_DISPATCH=bespoke`
switch were **removed** — none of it ships. Multi-atom fan-out
is `workflows/release-wave.js` over native `Agent` isolation (worktrees). Genuinely
process-isolated multi-repo fan-out is a current limitation, tracked on the roadmap — not
a shipped capability. This heading is kept as a tombstone so older references land on the
honest disposition.

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
  gap exists, file it upstream via `/foundry:upstream-submit` rather than reimplementing
  harness-internal deferred loading inside foundry.

Regression check: if a fresh READY-probe measurement (same harness version) diverges materially from
67,891 tokens, treat it as a context-economy regression signal, not noise — re-baseline and record the
harness version with the new number.

## What is REPLACED vs retained

- **REPLACED by native (SINGLE-REPO):** queue manifests, `.lock` flock, `wt claim`,
  WorktreeCreate prereqs, `result.json` polling, fan-out hygiene, deferred-cleanup — subsumed
  by `Agent`/`Workflow` **only when the product code IS the session repo**.
- **RE-EXTRACTED for MULTI-REPO:** the `WorktreeCreate` hook + per-spawn manifest +
  `foundry-wt` (KEY→`repos.<key>.path`) + worktree-time repo binding — the irreducible minimum
  native isolation cannot provide. Generalized over `.claude/foundry-project.json` (no
  hardcoded repo names), validated by running (the source blueprint).
- **Retained as thin CUSTOM seam — SCALE ONLY:** a durable cross-session queue +
  `.agent/{assignment,result}` artifact contract, because `Workflow` `resumeFromRunId`
  is SAME-SESSION only. The lean single-repo default needs neither.

## Anti-patterns

- **Re-introducing a queue/flock/manifest for the SINGLE-REPO lean case.** Native worktree
  isolation + structured return already do this; the durable queue is scale-only. (The
  minimal `target_repo` manifest is REQUIRED for multi-repo — that is not the bespoke queue.)
- **Driving a MULTI-REPO wave through `release-wave.js`** (the `Workflow` sandbox can't write
  the manifest) — dispatch per-atom from the main loop instead.
- **Dispatching an un-authorized atom.** Front-authorization is unconditional.
- **Spawning a writing subagent from a plain operator session** (hook-block) — do the work inline there.
- **Reaching for the retired process-spawn fallback.** It was removed;
  native `Agent`/`Workflow` is the only path (AC-DNW-1/-2).
