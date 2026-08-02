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

1. **Plugin load.** Ensure the adopter loads the plugin (`claude --plugin-dir ./foundry`
   for local; a marketplace entry for installed). Confirm `/foundry:*` skills resolve.
2. **Operator registry.** Create `.claude/foundry-operators.json` with ≥1 operator
   (committed — `/foundry:authorize` resolves `operator_id` against this registry and
   records it in the frozen `acceptance-contract.yaml`; absent registry fails closed).
3. **GitHub identity isolation (multi-account machines).** `gh`'s active account is a
   single GLOBAL setting, so on a machine with >1 authenticated account an adopter
   onboarded without declaring the repo-owning account silently runs `gh` (PR
   create/merge, API) as whatever account is globally active — invisibly, since `git
   push` can still succeed via an SSH alias, until an admin-scoped call 404s. Engage the
   per-project jail. **Inspect `gh auth status`:**
   - **Single account → no-op** — leave `.claude/gh-identity` absent; the guard stays
     dormant (preserves the opt-in contract; single-account adopters are unaffected).
   - **>1 account →** prompt for the repo-owning account, then:
     (a) write `.claude/gh-identity` (one line — the account handle);
     (b) seed an isolated `~/.config/gh-<account>` jail by hand: `GH_CONFIG_DIR=~/.config/gh-<account>
     gh auth login --insecure-storage` (inline token storage, so the jail is real and not
     keyring-shared), then PROVE it via `GH_CONFIG_DIR=~/.config/gh-<account> gh api user --jq .login`
     and compare the printed login against the declared handle by hand
     (NOT `gh auth status`, which can read the shared keyring and would print something even for a
     stale ambient credential). The seed-and-prove sequence above does not authenticate the jail
     beyond these two manual commands — nothing else in this step does more. `--insecure-storage`
     keeps the token inline rather than keyring-shared; the trade is a plaintext token at rest, and
     `0700` on its own defends the wrong adversary here — it stops OTHER Unix accounts, not the
     realistic same-UID readers on a dev box (your own agent's Bash tool, MCP servers, npm/pip
     postinstall hooks, editor extensions). `gh` does not create the directory at `0700` by default,
     so set it explicitly right after `gh auth login`: `chmod 0700 ~/.config/gh-<account>` (check a
     pre-existing directory first — it may already be looser). `~/.config` is also a common
     dotfiles/backup sync root, so exclude the jail from any sync/backup tool; to revoke, run
     `GH_CONFIG_DIR=~/.config/gh-<account> gh auth logout` **and** revoke the token server-side
     (github.com → Settings → Developer settings) — local logout alone does not invalidate an
     already-issued token.
     (c) set `GH_CONFIG_DIR=~/.config/gh-<account>` in the gitignored
     `.claude/settings.local.json` env so Claude Code sessions inherit the jail.
     (d) wire commit-identity isolation by hand: write `user.name`/`user.email` into
     `~/.config/git/identity-<account>` (`git config --file ~/.config/git/identity-<account>
     user.name "Name"` / `... user.email "you@example.com"`), point a global `includeIf` at it —
     `git config --global includeIf.gitdir:<abs-project-path>/.path
     ~/.config/git/identity-<account>` — and set repo-local `git config user.useConfigOnly true` so a
     *missing* identity fails the commit instead of silently falling back to `~/.gitconfig`. The
     trailing slash on the `gitdir:` pattern matches the WHOLE SUBTREE beneath `<abs-project-path>`
     (git auto-appends `**`), not just this one repo — verified empirically: an unrelated repo cloned
     or nested underneath the project inherits the same identity — so `useConfigOnly` cannot catch
     that: it only fires when NO identity resolves, and here a *wrong* one does.
   - **Two layers.** Identity isolation has a gh-token layer (above) AND a git-transport
     layer: the repo's git remote must use that account's **SSH host alias**
     (`git@<alias>:owner/repo.git`), never a bare `github.com` URL, or pushes use the
     default key. **Cross-account/private template:** if the workspace was seeded from a
     private template owned by a *different* account, `gh repo create --template` 403s —
     use the local-seed path. (A fuller identity-isolation guide ships with the upcoming
     `agentic-handbook` template — see the README roadmap.)
   - **Automated prelude — `scripts/foundry-bootstrap.sh`.** A shipped bootstrap script covers
     sub-step (a) only, plus the commit-identity wiring of (d) above — it does **not** touch (b) or
     (c), which stay manual: `scripts/foundry-bootstrap.sh --gh-account <name>` is a one-command
     physical **prelude** that runs **before** and **outside** the Claude Code session in which
     `/foundry:init` runs (clone the `--template` workspace — default `lukasrepublic/agentic-handbook`
     — over your **ambient default SSH key**, before any identity isolation exists → install the
     plugin from `--marketplace`, default `lukasrepublic/agentic-foundry`, at whatever the
     marketplace's current HEAD is, with **no ref/version pin** — pass your own pinned tag or commit
     if you need one; an unpinned install is exactly the drift this workspace's own
     "every pin explicit" standing-versions rule exists to prevent → seed the operator → write the
     files below), then hands off to `claude` → `/foundry:init`. If you are reading this step
     **mid-session** — while already inside a running `/foundry:init` session, which is the only way
     step three is ever actually read — do not try to invoke it here: finish the manual sub-steps
     (a)–(d) above now, and use the script as the prelude on your next project (or exit this session
     and run it there — pass `--existing` if the target directory is non-empty, otherwise the script
     refuses to clone over it — and re-run `/foundry:init`).

     Under `--gh-account <name>` it writes `.claude/gh-identity` (the account handle) and an `.envrc`
     that exports `GH_CONFIG_DIR` (plus `FOUNDRY_OPERATOR` when `--operator` was also given) —
     **with `--existing` this OVERWRITES any `.envrc` already at the target** (it is written with a
     plain redirect, not merged), silently dropping whatever else lived there (`AWS_PROFILE`, other
     secrets paths, …); back it up first if one exists. Neither `.envrc` nor `.claude/gh-identity` is
     added to `.gitignore` by the script — decide your project's policy: the account handle in
     `.claude/gh-identity` is not a secret and is normally fine to commit, `.envrc` is a local
     execution-trust file and most direnv users keep it out of version control. Separately, it wires
     the commit-identity half of (d): a global `includeIf` on the canonicalized project gitdir — the
     **trailing slash makes the match cover the whole subtree beneath the project directory**, not
     only this one repo, so a repo cloned or nested underneath (a shape this workspace's own
     recommended layout uses) inherits the same identity too — pointing at a per-account include file
     `~/.config/git/identity-<account>` carrying `user.name`/`user.email`, plus repo-local
     `user.useConfigOnly` so a **missing** identity fails the commit closed (`useConfigOnly` does not
     catch a *wrong*-but-present one, e.g. from that subtree overlap). That `.envrc` only takes effect
     if **direnv** is installed, hooked into your shell, and you have run `direnv allow` here;
     otherwise it is **inert** and `GH_CONFIG_DIR` is never exported — which is why sub-step (c) above
     (`.claude/settings.local.json`) stays the carrier Claude Code sessions inherit.
   - **Commit identity — pass `--git-author "Name <email>"`.** It is the explicit, recommended source
     of the identity written into the include file above, and passing it makes the result
     **deterministic** — but it also gives up the only cross-check the script has: only the
     `Name <email>` grammar is validated, nothing confirms the email actually belongs to the declared
     account, and the result is written durably into the global include file. Verify it yourself once
     the run finishes: `git -C <target> config user.email` should read back what you intended, and
     `GH_CONFIG_DIR=~/.config/gh-<account> gh api user --jq .login` should equal the declared account.
     Without `--git-author` the script runs one `gh api user` probe **scoped to the declared**
     account's own jail (not the ambient `gh` account) — that check pins the login but not the host
     (an enterprise namesake with a matching login would still pass), and proxy/CA-trust variables
     are not stripped from the probe's environment — and adopts the answer only if the returned login
     matches the declared account — a login **mismatch** discards both the name and the email.
     Because the script never runs `gh auth login`, a freshly-scaffolded jail is **unauthenticated**,
     so on a first run that probe simply fails: on a **TTY** you are prompted for the name and email;
     without one the run exits **non-zero** naming the remedies. The resolved identity, once one is
     found, is written durably into `~/.config/git/identity-<account>`.
4. **Branch-protection-as-code — RETIRED, Tier A pending a rebuild (since an earlier realignment release).** This step
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
5. **App-exercise binding (the live-seam driver).** Declare the adopter's boot
   command (the generic analog of `make dev`) + the `surface → how-to-exercise` map
   (`ui:`/`api:`/`cli:`/`pipeline:`/`binary:`/…). A contract surface with no usable driver
   surfaces at certification time — `/foundry:certify-local` REFUSES naming it (the thin
   doctor carries no driver check).
6. **Env/identity mapping.** Map the adopter's own vars onto `FOUNDRY_*` (Foundry owns
   the `FOUNDRY_*` namespace; never bake a project-specific var into a primitive).
7. **Mechanical-rename port (extraction only).** Copy the adopter's `.claude/` primitives
   into `foundry/` under their foundry names (the KEEP→rename bulk) by hand — the
   `foundry-migrate.py` helper this step originally named was retired and does not ship. Deeper prose
   generification is a separate review pass.
8. **Stack-profile lock (opt-in adoption, `feat-foundry-stack-profile-lock-create`, AC-SPLC-7).**
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
9. **Run `/foundry:doctor`** — must be `DOCTOR-GREEN` before the adopter is live. Fail-closed.
   (Retired: this step used to also pin the gate wiring via `foundry-wiring-hash.py`;
   that script no longer exists in this repo, and the current thin doctor —
   `skills/doctor/SKILL.md` — carries no wiring-hash check, so there is nothing left to pin.)
10. **Status lines (opt-in, additive — isolation-first).** Offer to wire the isolation-first native
   `statusLine` + the `subagentStatusLine` fleet rows so the highest-value ambient signals are visible
   every prompt: *am I isolated (a linked worktree, green `⊞`) or on the main checkout (amber `⌂ main ⚠`)?
   what is this session doing right now (the native `⊙` task)? how close is auto-compact (the color-coded
   `ctx` bar)?* — plus one row per dispatched worker. Both are fail-open (any error drops a segment, never
   breaks a session); this step is optional and does not affect `DOCTOR-GREEN`.

   **Why a self-resolving wrapper, NOT the direct plugin path.** A `statusLine.command` is a project/user
   setting, not owned by a plugin — the plugin-root hook path-placeholder (`${CLAUDE_PLUGIN_ROOT}`) is
   **hook-scoped and does NOT expand in a `statusLine` command** (the statusline docs inject only
   `COLUMNS`/`LINES`), so the legacy direct wiring
   `bash "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-statusline.sh"` resolves to a broken literal path → the
   renderer never runs → no status line. Even if it expanded, the cache path is version-segmented and would
   break every release. The fix is **shell indirection through a stable, installed wrapper** that
   self-resolves the newest installed renderer by a cache version-glob, wired through the one placeholder
   confirmed to expand in a `statusLine` command — **`$CLAUDE_PROJECT_DIR`**. The shipped
   `scripts/foundry-statusline-wrapper.sh` / `scripts/foundry-subagent-statusline-wrapper.sh` are those
   wrappers (version-agnostic, upgrade/retire-resilient, fail-open).

   **Fresh wiring — no existing `statusLine`/`subagentStatusLine` key.** INSTALL the shipped wrappers into
   the adopter repo at `.claude/hooks/foundry-statusline.sh` (resp.
   `.claude/hooks/foundry-subagent-statusline.sh`), `chmod 0755`, then via a single load-modify-write over
   the PARSED `.claude/settings.json` (create it as `{}` if absent; preserve ALL other keys — never a
   textual patch) set `statusLine.command` (type `command`) and `subagentStatusLine.command` to the
   EXPANDABLE `$CLAUDE_PROJECT_DIR` form. This is the prescribed install/wiring command:

   <!-- foundry:statusline-prescribed-wiring (AC-SLW-2 anchor — the prescribed command lives in THIS block) -->
   ```sh
   # 1. Install the shipped self-resolving wrappers into the adopter repo (chmod 0755).
   install -m 0755 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-statusline-wrapper.sh" \
     "$CLAUDE_PROJECT_DIR/.claude/hooks/foundry-statusline.sh"
   install -m 0755 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-subagent-statusline-wrapper.sh" \
     "$CLAUDE_PROJECT_DIR/.claude/hooks/foundry-subagent-statusline.sh"
   # 2. Then, via load-modify-write over the PARSED .claude/settings.json (preserve all other keys), set:
   #      statusLine.command          = "$CLAUDE_PROJECT_DIR/.claude/hooks/foundry-statusline.sh"           (type: command)
   #      subagentStatusLine.command  = "$CLAUDE_PROJECT_DIR/.claude/hooks/foundry-subagent-statusline.sh"  (type: command)
   ```
   The init body **NO LONGER** prescribes `bash "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-statusline.sh"` as
   the wiring (the unexpandable direct path is removed from the recommended procedure; it survives below
   ONLY as the recognized migrate-FROM shape).

   **Existing `statusLine`/`subagentStatusLine` — the conservative migration classifier.** Init no longer
   blindly never-clobbers a foundry-OWNED-but-stale wiring (the re-run repair gap). Classify the existing
   command (resp. the subagent one) **foundry-owned-and-stale** iff EITHER:
   - **(a)** the command STRING is the EXACT recognized stale direct shape — a bare
     `bash "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-statusline.sh"` (resp. `…/foundry-subagent-statusline.sh`),
     **or**
   - **(b)** the command points at a LOCAL `.claude/` script whose **file CONTENT** resolves/`exec`s
     `foundry-statusline.sh`/`foundry-subagent-statusline.sh` **or** invokes the **RETIRED** macro-engine
     `…state.py --statusline` pointer helper (removed in the macro-workflow-engine retirement), AND is
     **not already** the current canonical wrapper (the upstream canonical shape — the staleness lives INSIDE the
     pointed-to wrapper file, not in the command string).

   Branch on the classification:
   - **foundry-owned-and-stale → MIGRATE** — install/refresh the canonical wrapper at
     `.claude/hooks/foundry-statusline.sh` (resp. subagent) and repoint the command to the
     `$CLAUDE_PROJECT_DIR` form above; **inform the operator**.
   - **current canonical wrapper** (a local `.claude/hooks/foundry-statusline.sh` whose content carries the
     `feat-foundry-init-statusline-wrapper` foundry marker) → **no-op** (already correct; content-recognizable
     so a future atom stays able to migrate it).
   - **FOREIGN → LEAVE UNTOUCHED + inform** (the never-clobber contract). Foreign is: no foundry marker, a
     non-local command, OR a foundry-rooted command **DECORATED** with extra env/flags/args (e.g.
     `FOUNDRY_STATUSLINE_EXTRAS=1 bash "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-statusline.sh" --x`) — its
     env/flags carry operator intent that must not be silently dropped. Migration is **byte-conservative:
     ONLY an EXACT recognized shape is rewritten.**

   - Opt-in extras (model / cost / ahead-behind) are OFF by default; the operator turns them on by setting
     the `FOUNDRY_STATUSLINE_EXTRAS` environment variable (truthy).
11. **Native Bash sandbox (opt-in write-confinement hardening).** Offer to enable Claude Code's native
    OS-level Bash sandbox so a dispatched worker's Bash **writes** are kernel-confined to its
    worktree (macOS Seatbelt / Linux+WSL2 bubblewrap; subagent- + git-worktree-aware). Reuse the SAME
    load-modify-write seam as the status-line step (step 10) over the adopter's project `.claude/settings.json`:
    - Read the adopter's `.claude/settings.json` (create it as `{}` if absent), as parsed JSON.
    - **If it has no `sandbox` key** → **enable the sandbox** by setting `sandbox.enabled` = `true`
      (the documented on-switch, whose default boundary confines Bash writes to the working directory +
      session temp dir), **preserving all other keys** (load-modify-write the parsed JSON, not a textual
      patch). Inform the operator the native Bash sandbox is now ON (worker writes OS-confined to the
      worktree). Note: `sandbox.enabled: true` is the enable signal — `sandbox.filesystem`
      (`allowWrite`/`denyRead`/…) only widens/narrows that scope, it is NOT the on-switch.
    - **If a `sandbox` key already exists** → **leave it untouched and inform the operator** (never
      clobber/compose an operator's existing sandbox config; the operator tunes it manually).
    - Grounding for the exact key shape: the official Claude Code sandboxing docs
      (`code.claude.com/docs/en/sandboxing`).
    This is **opt-in hardening, advisory — not a both-modes floor**: it is additive / fail-open and does
    NOT affect `DOCTOR-GREEN` (matching the status-line step). It confines **writes only** (reads default
    to the whole machine), is native-Windows-unsupported, and can fall back to unsandboxed if OS deps are
    missing. (The companion `native-bash-sandbox` doctor check this line originally named was retired with the drop-in registry — the thin doctor carries no sandbox check.)
    when the sandbox is off.
12. **Runtime-partition `.gitignore` (default-deny, leak-prevention).** Run `scripts/foundry-apply-runtime-gitignore.sh <repo-root>`
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

## Inputs / Outputs

- In: the adopter repo + its boot command + surface map + operator id(s) + (multi-account machines) the repo-owning gh account.
- Out: a wired, DOCTOR-GREEN adopter (registry + identity isolation + driver map; branch-protection Tier A is deferred — see step 4).

## Anti-patterns

- **Going live on DOCTOR-RED** — the doctor is the fail-closed go-live gate.
- **Onboarding a multi-account machine without declaring the repo-owning gh account** — the
  jail stays dormant and `gh` silently runs as the globally-active account (invisible until an
  admin-scoped call 404s). Single-account machines: correctly a no-op.
- **Trusting `gh auth status` as proof of isolation** — it can read the shared keyring; prove the
  jail with `gh api user` under `GH_CONFIG_DIR` (step 3's manual seed does exactly this).
- **Baking a project-specific var** (`<PROJECT>_*`) into a foundry primitive — map onto `FOUNDRY_*`.
- **Enabling non-local auto-merge at all** — no distinct-principal poster ships; there is no supported unattended-merge posture.
- **Skipping the app-exercise binding** — without a live-seam driver the evidence floor is unrealizable for that adopter.
- **Hand-writing `.foundry/stack-profile.lock` in prose** — always the scripted `--lock` path (step
  8); a hand-written lock skips the trusted-resolve guardrails and can fail the very first
  `/foundry:doctor`/`resolve_lock()` check it is supposed to pass.
