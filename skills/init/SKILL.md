---
name: init
description: Adopter scaffolder (/foundry:init). Stands up a new adopting project's Foundry wiring — operator registry, per-project gh identity isolation (multi-account machines), the app-exercise binding (the live-seam driver map, the generic analog of `make dev`), env/identity mapping — then fail-closes via /foundry:doctor. Branch-protection-as-code (Tier A) is retired pending a Rulesets-API rebuild (since an earlier realignment release); the current floor is Tier B advisory (ci.yml + btb-gates) + the git-discipline gh clause. Trigger when onboarding a new repo to Foundry, or extracting Foundry to a standalone plugin repo.
---

# /foundry:init

The adopter onboarding + extraction scaffolder. Foundry is the framework; the
adopting project is the example. `/foundry:init` wires a new adopter (or the extracted
standalone plugin repo) so the gates are live + fail-closed.

## When to trigger

- Onboarding a new repo to the Foundry plugin.
- Phase-4 extraction: splitting `foundry/` out to a standalone public plugin repo, with
  the adopter repo as adopter #0 consuming it.

## Procedure (fail-closed at the end)

1. **Control-plane preflight — run this FIRST, before any step below writes anything.**
   `/foundry:init` itself can be run from the wrong root (a hosted repo instead of the control
   plane above it — see `docs/how-to/multi-repo-control-plane.md`), and every step after this
   one writes a file. Run the scripted preflight and **STOP if it exits non-zero**: read the
   finding, `cd` to the named control plane, and re-run `/foundry:init` there instead.

   ```sh
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_control_plane.py" "$CLAUDE_PROJECT_DIR"
   ```

   A non-zero exit names the offending ancestor `.claude/foundry-project.json` and the one-line
   remedy. Only pass `--override` (exits 0, still prints the finding) for a deliberately
   independent adopter nested inside another repo's working tree — see the spec's residuals
   (`feat-foundry-control-plane-preflight`, AC-CPP-4/-4b). **This step is a practice, not a
   control** — `/foundry:init` is agent-driven prose, so nothing mechanically forces an agent to
   run it; `/foundry:doctor`'s own `control-plane` check (step 10 below) is the fail-closed
   backstop this step exists to make you hit early rather than late.

2. **Plugin/marketplace enablement — VERIFY-ONLY.**

   <!-- foundry:init-verify-only:plugin-enablement v1 -->
   VERIFY-ONLY. init does not write the plugin/marketplace enablement — a plugin cannot install
   or enable itself into the session already running it (the harness classifier denies a
   self-confinement edit; this is the ER's empirical finding, not new policy here). This step
   only reads and reports.

   - **Present** — confirm `/foundry:*` skills resolve (this very step running is the evidence).
     Report the verdict and continue.
   - **Absent or drifted** — **REFUSE**: report the finding and stop this step, pointing at the
     pre-session bootstrap's own doc — `${CLAUDE_PLUGIN_ROOT}/docs/QUICKSTART.md` (or, before
     install, `https://github.com/lukasrepublic/agentic-foundry/blob/main/docs/QUICKSTART.md`) —
     the shipped writer of this artifact ([[feat-foundry-bootstrap-cli]] AC-BCL-4(b)).
   <!-- /foundry:init-verify-only:plugin-enablement -->
3. **Operator registry.** Create `.claude/foundry-operators.json` with ≥1 operator
   (committed — `/foundry:authorize` resolves `operator_id` against this registry and
   records it in the frozen `acceptance-contract.yaml`; absent registry fails closed).
4. **GitHub identity isolation (multi-account machines) — VERIFY-ONLY.** `gh`'s active account
   is a single GLOBAL setting, so on a machine with >1 authenticated account an adopter
   onboarded without declaring the repo-owning account silently runs `gh` (PR create/merge,
   API) as whatever account is globally active — invisibly, since `git push` can still succeed
   via an SSH host alias, until an admin-scoped call 404s. **Inspect `gh auth status`:**
   - **Single account → no-op** — leave `.claude/gh-identity` absent; the guard stays
     dormant (preserves the opt-in contract; single-account adopters are unaffected).
   - **>1 account →** the verify-only region below reports the jail state; it does not seed one.

   <!-- foundry:init-verify-only:git-identity v1 -->
   VERIFY-ONLY. init does not write any part of the per-account jail. `scripts/foundry-bootstrap.sh`
   is the shipped script the pre-session bootstrap invokes — see its own doc,
   `${CLAUDE_PLUGIN_ROOT}/docs/QUICKSTART.md` (or, before install,
   `https://github.com/lukasrepublic/agentic-foundry/blob/main/docs/QUICKSTART.md`) — for the
   commands. This step only verifies, read-only:

   ```sh
   GH_CONFIG_DIR=~/.config/gh-<account> gh api user --jq .login   # compare against the declared handle
   git -C <target> config user.email                              # read back the resolved commit identity
   ```

   (NOT `gh auth status` — it can read the shared keyring and would print something even for a
   stale ambient credential.)

   **Disposition, one row per artifact — none of the three is written by this step:**

   | Artifact | Disposition | Owner / remedy |
   |---|---|---|
   | Commit identity — the global `includeIf` binding, the per-account include file, repo-local `useConfigOnly`, and the `.claude/gh-identity` marker | **REFUSE** | pre-session bootstrap owns it ([[feat-foundry-bootstrap-cli]] AC-BCL-7) |
   | The `gh` jail's **authentication** | **REPORT-ONLY** — no shipped writer | out of scope of the pre-session bootstrap's own Clarifications; `scripts/foundry-bootstrap.sh` never authenticates it |
   | The `GH_CONFIG_DIR` session-env carrier | **REPORT-ONLY** — no shipped writer | the pre-session bootstrap is forbidden from writing the gitignored local session-settings file (AC-BCL-4(d)); see `docs/identity-isolation.md` |

   **Absent or drifted** → the row's disposition above. **Present and matching** → report the
   verdict and continue.
   <!-- /foundry:init-verify-only:git-identity -->

   - **Two layers.** Identity isolation has a gh-token layer (above) AND a git-transport
     layer: the repo's git remote must use that account's **SSH host alias**
     (`git@<alias>:owner/repo.git`), never a bare `github.com` URL, or pushes use the
     default key. **Cross-account/private template:** if the workspace was seeded from a
     private template owned by a *different* account, `gh repo create --template` 403s —
     use the local-seed path.
5. **Branch-protection-as-code — RETIRED, Tier A pending a rebuild (since an earlier realignment release).** This step
   used to run `foundry-branch-protection.sh apply/verify <owner/repo>` to PUT + verify a
   universal `foundry-merge-gate` required-status backstop. Both the applier script and the
   `branch-protection.json` config it pinned are deleted
   — the `foundry-merge-gate` required-status context they enforced no longer exists to enforce.
   **Current floor on this repo: Tier B (advisory) only** — the `ci.yml` command battery +
   `btb-gates`' always-reporting `spec-link`/`security-path` checks report on every PR but are
   NOT a server-enforced required status, and `hooks/foundry-git-discipline.sh`'s `gh` clause
   blocks a Foundry-session `gh pr merge --admin` outright and requires `gh pr checks` all-green
   for a plain `gh pr merge` — but a human `git push` / the web UI / the REST API are not gated.
   **Tier A (a real server-enforced required status) is deferred**, to be rebuilt on GitHub's
   Rulesets API rather than the retired classic-branch-protection applier — do not re-introduce
   the deleted script/config to fill this gap.
6. **App-exercise binding (the live-seam driver).** Declare the adopter's boot
   command (the generic analog of `make dev`) + the `surface → how-to-exercise` map
   (`ui:`/`api:`/`cli:`/`pipeline:`/`binary:`/…). A contract surface with no usable driver
   surfaces at certification time — `/foundry:certify-local` REFUSES naming it (the thin
   doctor carries no driver check).
7. **Env/identity mapping.** Map the adopter's own vars onto `FOUNDRY_*` (Foundry owns
   the `FOUNDRY_*` namespace; never bake a project-specific var into a primitive).
8. **Mechanical-rename port (extraction only).** Copy the adopter's `.claude/` primitives
   into `foundry/` under their foundry names (the KEEP→rename bulk) by hand — the
   `foundry-migrate.py` helper this step originally named was retired and does not ship. Deeper prose
   generification is a separate review pass.
9. **Stack-profile lock (opt-in adoption, `feat-foundry-stack-profile-lock-create`, AC-SPLC-7).**
   Foundry ships four stack profiles under `packs/stack-profiles/` (`aws-eks-karpenter`, `node-web`,
   `python-uv-lib`, `python-uv-service`) — `/foundry:verify`, `/foundry:certify-local`, and the
   `id-*` lane's `infra_binding` all gate on an ACTIVE `.foundry/stack-profile.lock`. Offer the
   operator a choice of the shipped ids (or "none"). **On a choice**, invoke the SCRIPTED create
   path below — never hand-write a lock in prose (a hand-written lock skips the trusted-resolve
   guardrails `resolve_lock()` enforces and can strand the adopter on the very first `/foundry:doctor`
   run). **On "none"**, complete this step with no lock — a lockless workspace is a fully-supported,
   `DOCTOR-GREEN` state (AC-SPLC-8); adopt a profile later via the same command.

   <!-- foundry:stack-profile-lock-create-prescribed (AC-SPLC-7 anchor — the prescribed command lives in THIS block) -->
   ```bash
   # Creates .foundry/stack-profile.lock for the named id(s) (comma-separated for >1 profile).
   # Refuses (no write) if a lock already exists (see /foundry:relock), an id is unknown, or an id
   # fails a trusted-resolve guardrail (schema-invalid / core-incompatible / bundle-leaking).
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-stack-profile.py" --lock <id>[,<id>…]
   ```
   <!-- /foundry:stack-profile-lock-create-prescribed -->

   If the operator selects no profile, skip this block entirely and move on — do not run `--lock`
   with an empty/placeholder id.
10. **Run `/foundry:doctor`** — must be `DOCTOR-GREEN` before the adopter is live. Fail-closed.
   (Retired: this step used to also pin the gate wiring via `foundry-wiring-hash.py`;
   that script no longer exists in this repo, and the current thin doctor —
   `skills/doctor/SKILL.md` — carries no wiring-hash check, so there is nothing left to pin.)
11. **Status lines (opt-in, additive — isolation-first) — VERIFY-ONLY.** A wired native
   `statusLine` + `subagentStatusLine` fleet surfaces the highest-value ambient signals every
   prompt (isolation state, current task, context-window headroom, one row per dispatched
   worker) — both are fail-open and opt-in, and this step's verdict does not affect
   `DOCTOR-GREEN`.

   <!-- foundry:init-verify-only:statusline v1 -->
   VERIFY-ONLY. init does not write the `statusLine`/`subagentStatusLine` wiring — no shipped
   writer owns this artifact today ([[feat-foundry-bootstrap-cli]] AC-BCL-4(c) forbids the
   pre-session bootstrap from emitting it, at any nesting level, and states it is not relocated
   to another writer). This step only reads and reports.

   - **Present** — read `.claude/settings.json`. A local `.claude/hooks/foundry-statusline.sh`
     carrying the `feat-foundry-init-statusline-wrapper` marker, with a `statusLine.command`
     value in the `$CLAUDE_PROJECT_DIR` form, is the recognized wired shape — report it. Any
     other value is reported as-is (foreign or stale); it is never touched.
   - **Absent or drifted** — **REPORT-ONLY**: report that no shipped writer wires this today and
     point at the pre-session bootstrap's own doc — `${CLAUDE_PLUGIN_ROOT}/docs/QUICKSTART.md`
     (or, before install, `https://github.com/lukasrepublic/agentic-foundry/blob/main/docs/QUICKSTART.md`)
     — for the by-hand remedy, then continue; absence here is a fully-supported, non-blocking
     state, not a defect.

   The shipped `scripts/foundry-statusline-wrapper.sh` / `scripts/foundry-subagent-statusline-wrapper.sh`
   remain the wrappers to install by hand if you opt in — QUICKSTART carries the exact commands
   and why the `$CLAUDE_PROJECT_DIR` indirection is required (`${CLAUDE_PLUGIN_ROOT}` does not
   expand inside a `statusLine` command).
   <!-- /foundry:init-verify-only:statusline -->
12. **Native Bash sandbox (opt-in write-confinement hardening) — VERIFY-ONLY.** Enabling
    Claude Code's native OS-level Bash sandbox kernel-confines a dispatched worker's Bash
    **writes** to its worktree (macOS Seatbelt / Linux+WSL2 bubblewrap; subagent- +
    git-worktree-aware) — opt-in, advisory, and this step's verdict does not affect
    `DOCTOR-GREEN` either.

    <!-- foundry:init-verify-only:sandbox v1 -->
    VERIFY-ONLY. init does not write the native OS-level Bash sandbox enable — no shipped writer
    owns this artifact today ([[feat-foundry-bootstrap-cli]] AC-BCL-4(c) forbids the pre-session
    bootstrap from emitting `sandbox.enabled` too, and states it is not relocated to another
    writer). This step only reads and reports.

    - **Present** — read `.claude/settings.json`'s `sandbox` key. Report whether it is present
      and its value (`sandbox.enabled` is the on/off signal; `sandbox.filesystem` only
      widens/narrows an already-enabled scope).
    - **Absent or drifted** — **REPORT-ONLY**: report that no shipped writer enables it today and
      point at the pre-session bootstrap's own doc — `${CLAUDE_PLUGIN_ROOT}/docs/QUICKSTART.md`
      (or, before install, `https://github.com/lukasrepublic/agentic-foundry/blob/main/docs/QUICKSTART.md`)
      — for the by-hand remedy, then continue; absence here is a fully-supported, non-blocking
      state (reads default to the whole machine either way, sandboxed or not).

    Grounding for the exact key shape: the official Claude Code sandboxing docs
    (`code.claude.com/docs/en/sandboxing`).
    <!-- /foundry:init-verify-only:sandbox -->
13. **Runtime-partition `.gitignore` (default-deny, leak-prevention).** Run `scripts/foundry-apply-runtime-gitignore.sh <repo-root>`
    against the adopter repo. It installs the
    default-deny `.foundry/*` block (re-including only the small designed-tracked set:
    `README.md`, `build-provenance.yaml`, the `/foundry:relock` pin, `stack-profile.lock`) as an idempotent
    managed block in the repo's root `.gitignore`, so a routine `git add -A` after a factory session
    can no longer sweep an unlisted runtime artifact into a commit — the class of leak recorded in
    [Doc: GO-PUBLIC.md] §5.4. `scripts/foundry-bootstrap.sh` already invokes this on the real target
    path for a freshly-cloned or `--existing` repo; run it by hand for a repo bootstrapped before this
    step existed. Before flipping any repo's visibility to public, also run
    `scripts/foundry-prepublication-leak-scan.py --root <repo-root>` — its clean verdict requires a
    managed block to be present (it fails closed on a repo with none) plus all four of its scopes
    (working tree, full history, tracked `.foundry/` state, and a remote direct-SHA probe) to run
    clean; see the script's own `--help` for its remote/known-bad-SHA options.

14. **Guided first atom (the hello-loop).** Close onboarding by walking the operator through
    ONE small, throwaway atom end-to-end, so the first real run of the loop happens before you
    need it for something that matters:

    - **`/foundry:intake`** — describe one trivial capability (e.g. "print a greeting") and let
      it produce a spec + acceptance contract under `specs/**`.
    - **`/foundry:spec-review`** — get that spec reviewed and recorded.
    - **`/foundry:authorize`** — this is the one-keypress ask: a native confirmation prompt
      fires here, and the operator answers it. Explain it as it fires — it is the front gate. This
      session never answers it on the operator's behalf; only a human keypress can.
    - **`/foundry:dispatch`** — an implementer builds the toy atom on its own branch.
    - **merge** — the operator merges the resulting change through their own normal review flow.

    **Cleanup.** The whole walk lives on a scratch branch carrying a throwaway spec under
    `specs/**` — once the operator has seen the loop end to end, delete it: remove the scratch
    branch and its spec files rather than leaving toy content behind.

## What init verifies — and what it no longer does

Init is not purely conversational — it still writes three artifacts (the closing line below).
What changed is the five surfaces below: init used to prescribe writing four of them; now it
only verifies and reports on all five, and names the real owner for each.

- **status lines** — verifies: whether `statusLine`/`subagentStatusLine` are wired, and to what.
  no longer does: install the wrapper scripts or set the `.claude/settings.json` keys. owner:
  no shipped writer — wire them by hand, see QUICKSTART's "Before your first session".
- **native Bash sandbox** — verifies: whether `sandbox.enabled` is set, and its value.
  no longer does: enable the sandbox. owner: no shipped writer — enable it by hand, see
  QUICKSTART's "Before your first session".
- **git identity** — verifies: the per-account jail's authentication (the read-only `gh api
  user` proof) and the resolved commit identity. no longer does: seed the jail, wire the global
  `includeIf`, or write `.claude/gh-identity`. owner: pre-session bootstrap
  (`scripts/foundry-bootstrap.sh` owns the commit-identity half; see step 4's verify-only
  region for the two sub-artifacts that have no shipped writer at all).
- **plugin enablement** — verifies: that `/foundry:*` skills resolve. no longer does: install
  or enable the plugin/marketplace. owner: pre-session bootstrap.
- **permission floor** — verifies: reports what `/foundry:doctor`'s `permission-floor` probe
  finds today (a degraded form until a sibling probe lands — see the probe's own output).
  no longer does: write or repair `.claude/settings.json`'s `permissions` key.
  owner: pre-session bootstrap.

**init still writes** three artifacts, unaffected by anything above: `.claude/foundry-operators.json`
(step 3), `.foundry/stack-profile.lock` via the scripted `--lock` path (step 9), and the
`.gitignore` managed block via the shipped applier (step 13).

## Inputs / Outputs

- In: the adopter repo + its boot command + surface map + operator id(s) + (multi-account machines) the repo-owning gh account.
- Out: a wired, DOCTOR-GREEN adopter (registry + identity isolation + driver map; branch-protection Tier A is deferred — see step 5).

## Anti-patterns

- **Going live on DOCTOR-RED** — the doctor is the fail-closed go-live gate.
- **Onboarding a multi-account machine without declaring the repo-owning gh account** — the
  jail stays dormant and `gh` silently runs as the globally-active account (invisible until an
  admin-scoped call 404s). Single-account machines: correctly a no-op.
- **Trusting `gh auth status` as proof of isolation** — it can read the shared keyring; prove the
  jail with `gh api user` under `GH_CONFIG_DIR` (step 4's verify-only region does exactly this).
- **Baking a project-specific var** (`<PROJECT>_*`) into a foundry primitive — map onto `FOUNDRY_*`.
- **Enabling non-local auto-merge at all** — no distinct-principal poster ships; there is no supported unattended-merge posture.
- **Skipping the app-exercise binding** — without a live-seam driver the evidence floor is unrealizable for that adopter.
- **Hand-writing `.foundry/stack-profile.lock` in prose** — always the scripted `--lock` path (step
  9); a hand-written lock skips the trusted-resolve guardrails and can fail the very first
  `/foundry:doctor`/`resolve_lock()` check it is supposed to pass.
