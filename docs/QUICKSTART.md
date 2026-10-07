# Quickstart — zero to your first merged ticket

Every command below is copy-paste-runnable — kept current by the CI doc-drift suites, so no
version pin here can go stale. If a command here ever drifts from the shipped CLI, that's a
bug — file it (a CI doc-drift test locks the pins on our side).

The default path is on one page: **[context/operating-model.md](../context/operating-model.md)**.
What you'll produce, artifact by artifact:

```
 you type                      what appears in your repo
 ────────                      ─────────────────────────
 /foundry:init            ──▶  .claude/foundry-operators.json   (who may authorize; standing grant)
                               .claude/foundry-project.json     (project config)

 gh issue create          ──▶  a ticket: What / Why / Done means (a command) / Paper allowed

 foundry-ticket.py start  ──▶  <git-dir>/foundry-ticket.json   (not in the tree; scopes the paper guard
                                                                  and the Stop hook)

 /foundry:dispatch        ──▶  an isolated worktree → foundry-test.sh green → one PR →
                                 YOUR checks decide the merge

 /foundry:merge-when-green ─▶  (on your say-so) waits for every check, merges when CLEAN
```

## Prerequisites

- **Claude Code** (CLI or desktop).
- **python3** with `pyyaml`, `jsonschema` (`pip install -r requirements.txt`, runtime deps) and
  `pytest` (`pip install -r requirements-dev.txt`, dev deps).
- **node 22+** — for the `npx` bootstrap/updater and, if your tickets use them, Playwright tests.
- A repo you own, on GitHub, with CI you trust (the floor derives from YOUR checks).

## Before your first session

Multi-account machines only — if `gh`'s only authenticated account already owns every repo you
onboard, skip this section: `/foundry:init` (below) still **verifies and reports** on the
artifacts named here, it just never redirects you to it.

A plugin cannot write its own session's confinement, so the per-account identity jail is seeded
**before** any Claude Code session runs — a physical, out-of-session step, not something
`/foundry:init` can do for you. The pre-session step that owns this write is
`scripts/foundry-bootstrap.sh`'s `project-scaffold` (or the combined `<target-dir>` form): it
clones the workspace template, installs the plugin from the declared marketplace, then writes
`.claude/gh-identity` (the account handle) and an `.envrc` exporting `GH_CONFIG_DIR`, wires the
git-native commit-identity `includeIf` binding, seeds the operator registry, and applies the
runtime-partition `.gitignore` block — before handing off to `claude` so you land in
`/foundry:init` with all of that already in place:

```bash
scripts/foundry-bootstrap.sh <target-dir> --gh-account <name> [--git-author "Name <email>"] [--existing]
```

**Two things it does NOT do, and you do by hand:**

- **Authenticate the jail.** `scripts/foundry-bootstrap.sh` never runs `gh`'s own login flow.
  Seed it yourself: `GH_CONFIG_DIR=~/.config/gh-<account> gh auth login --insecure-storage`
  (inline token storage keeps the jail real rather than keyring-shared — the trade is a
  **plaintext token at rest**, so also `chmod 0700 ~/.config/gh-<account>` and keep that
  directory out of any dotfiles/backup sync tool).
  To revoke, a local `gh auth logout` is **not enough** — the token stays valid until you also
  revoke it server-side (github.com → Settings → Developer settings); local logout alone does
  not invalidate an already-issued token.
- **Export `GH_CONFIG_DIR` into your Claude Code session.** Set it yourself in the gitignored
  `.claude/settings.local.json` env, or rely on the `.envrc` the bootstrap wrote — which only
  fires if **direnv** is installed, hooked into your shell, and you've run `direnv allow` here;
  otherwise it stays inert and nothing is exported. See
  [identity-isolation.md](identity-isolation.md) for the full session-env-carrier story.

**Coverage — the four artifacts no shipped writer owns today.** `/foundry:init` verifies and
reports on each; none of them is written by anything this plugin ships.

| Artifact | Status | By-hand remedy |
|---|---|---|
| `statusLine`/`subagentStatusLine` wiring | no shipped writer | install `scripts/foundry-statusline-wrapper.sh` (resp. `-subagent-`) into `.claude/hooks/`, `chmod 0755`, then set `statusLine.command` / `subagentStatusLine.command` in `.claude/settings.json` to the `$CLAUDE_PROJECT_DIR` form shown in the wrapper scripts' own header comments. |
| `sandbox.enabled` (native Bash sandbox) | no shipped writer | set `sandbox.enabled: true` in `.claude/settings.json` yourself — see `code.claude.com/docs/en/sandboxing`. |
| the `gh` jail's authentication | no shipped writer | `GH_CONFIG_DIR=~/.config/gh-<account> gh auth login --insecure-storage`, as above. |
| the `GH_CONFIG_DIR` session-env carrier | no shipped writer | set it in `.claude/settings.local.json`, or use direnv + the `.envrc` the bootstrap script wrote, as above. |

## The minimal path

The three-verb core loop, on one repo, with nothing else read first. The full governance model —
the hook layer and the merge-floor tiers — is covered afterward in **the full install**, below.

## 0. Install

Scaffolding a brand-new workspace? Start with the pre-session bootstrap wizard — it writes the
permission floor **before** a session exists, then hands off:

```bash
npx create-agentic-workspace --dir my-workspace
```

Already have a repo? Wire the plugin into it directly:

```bash
claude plugin marketplace add lukasrepublic/agentic-foundry
claude plugin install foundry@agentic-foundry
```

Already installed from an earlier release? Bring it current with one command, run from inside the
workspace directory:

```bash
npx update-agentic-workspace@latest
```

It refreshes the marketplace (migrating a pre-v1.7.0 tag-pinned registration if it finds one),
updates the plugin in **every scope that enables it**, and re-runs the managed-file and
permission-floor reconcile, so a floor written by an older scaffold picks up rules a later release
added. It reads the marketplace catalogue manifest back to confirm the refresh actually landed —
the CLI's own "success" line does not prove that. The per-scope plugin updates are not individually
verified, so if a session still loads the previous version, check each scope's registration. Add `--cleanup` to also prune superseded plugin-cache versions and a stale
registration no scope still enables; without the flag it previews and removes nothing.

Confirm inside a session in your repo:

```
/foundry:doctor        # → DOCTOR-GREEN (7 probes: python deps, manifest, hooks, skills, profile lock, operators, control-plane)
```

## 1. Wire your repo (once)

```
/foundry:init
```

This seeds the **operator registry** (`.claude/foundry-operators.json` — add yourself) and the
project config (`.claude/foundry-project.json`). Then set up your merge floor: run
`scripts/foundry_tier_preflight.py --repo <owner>/<repo> --context <your-gate-checks> --apply` to
apply the shipped ruleset template and print the honest tier (`TIER-A`, `TIER-B` with its cause,
or `PREFLIGHT-ERROR`) — see [merge-floor.md](merge-floor.md) for what each verdict means and why
a created ruleset is not, on its own, evidence of enforcement. (`init` does not apply branch
protection itself; `foundry_tier_preflight.py` is the command that does.)

`/foundry:init` also **verifies and reports** — it never writes — the status line wiring, the
native Bash sandbox enable, and the git-identity jail: a plugin cannot edit its own session's
confinement. See **Before your first session**, above, for what owns each of those writes and the
by-hand remedy where nothing does.

**Existing codebase?** Nothing to extract: write the next change as a ticket and the loop below
applies as-is.

## 2. Write the ticket

A GitHub issue, one screen at most:

````markdown
## What
Users can export their data as CSV.
## Why
Support is hand-exporting it every week.
## Done means
```
python -m pytest tests/test_export_csv.py -q
```
## Paper allowed
docs/runbooks/export.md
````

`Done means` is a **command**, not a sentence: it is what the agent runs before it stops, what the
reviewer runs, and what you run for sign-off. `Paper allowed` lists the only documents the change may
touch under `specs/`, `docs/`, `.foundry/`, `status-reports/` or `charters/`; code, infra, tests and
config are never gated. Not sure what the ticket should say? `/foundry:intake` walks you through it.

Then start it in the session:

```bash
python3 "$CLAUDE_PLUGIN_ROOT/scripts/foundry-ticket.py" start 42
```

## 3. Authorization is the standing grant

Assigning the ticket **is** the authorization; your standing grant in
`.claude/foundry-operators.json` (`standing_authorization: true`) is recorded once. Only
`security: true` work (auth, secrets, custody, production data) or a design too large for a ticket
takes the opt-in spec lane: `/foundry:authorize` freezes a spec + contract with your operator id and
has no skip.

## 4. Build

```
/foundry:dispatch 42
```

An implementer persona builds the ticket in an **isolated git worktree**, runs
`scripts/foundry-test.sh` until your repo's own CI command is green locally, pushes once and opens
one PR. The git-discipline hook refuses the push and a non-draft PR until local-green is recorded for
HEAD. While `Done means` fails, the Stop hook keeps the session working. Merges wait on your repo's
own checks — details in **the full install**, below.

Prefer hands-on? Plain Claude Code on a branch is the zero-ceremony lane (`/foundry:mode
interactive`): you implement and review yourself. Small changes deserve small process.

## 5. Merge — on your say-so

```
/foundry:merge-when-green 87
```

Only on your explicit instruction (or a ticket that says `merge: auto`): waits for every check and
merges through the one already-permitted squash merge the instant the PR is `CLEAN`.
Deploy on promote (a tag or a label), not on every merge.

## 6. Sign off — you, not the machine

Close the ticket with the `Done means` output; then run the thing yourself. Your own test pass is the
last step of delivery — a practice, deliberately **not** a machine gate: the automation's job ends at
making problems visible; the judgment is yours.

---

**The whole loop:** `ticket → dispatch → floor → merge`. Three verbs plus your own CI. Everything
else in the catalog is optional — see [docs/VERBS-QUICK-REF.md](VERBS-QUICK-REF.md) for the full
list.

## The full install

The minimal path above is the whole discipline. This section states plainly what enabling the
plugin does to every session in every repo where it's installed, what the merge floor actually
enforces — none of it is new machinery beyond the minimal path;
it's the same three verbs, explained in full.

### The hook layer — not optional today

**The hook layer is not optional today.** Installing and enabling the plugin wires its hooks
into every session in every repo on your machine — there is no supported way to disable it, no
environment variable, no config flag, and no reduced-ceremony install that turns it off. At
minimum, these guards are wired:

- **`hooks/foundry-git-discipline.sh`** — a `PreToolUse` guard on `gh pr merge`: refuses
  `--admin` (a server-side-check bypass) outright, admits `--auto` (the platform's required
  checks enforce the wait), and admits any other merge only after a live `gh pr checks` query
  returns all-green. Fail-closed on any error, pending row, or unknown state. It also refuses
  `git push` and a non-draft PR creation until `scripts/foundry-test.sh` has recorded
  local-green for HEAD.
- **`hooks/foundry-cwd-enforce.sh`** — inside a dispatched worktree, canonicalizes every
  write-tool target and hard-stops (fail-closed) any write resolving into the main checkout or
  another worktree of the same repository, closing the gap native worktree isolation leaves
  open. Writes outside every checkout of the repository (`~/.claude/`, the temp dirs, unrelated
  paths) are ordinary work and are admitted.

An environment-gated off-switch for this layer has been proposed and is **parked pending an
operator decision** — it does not ship today, and this document promises nothing about it.

### The merge floor, in full

The PR merges when **your repo's checks** are green, per your tier:

- **Tier A** (rulesets available): required status checks on `main` — server-enforced.
- **Tier B** (plans without rulesets): the same checks always-reporting, plus the
  git-discipline guard above.

Details + exact hook behavior: [merge-floor.md](merge-floor.md).

## Pick one install path — do not stack

There are two ways to get this plugin into a session: the marketplace install (`claude plugin
marketplace add` + `claude plugin install`, as in **Install** above) and a directory-sourced
local plugin (`claude --plugin-dir ./agentic-foundry`, for a private or vendored checkout).
**Use exactly one.** Plugin hook commands are additive with whatever else is wired into a repo,
and are de-duplicated by command string — but each install source expands
`${CLAUDE_PLUGIN_ROOT}` to a different root, so the two sources produce two different command
strings and de-duplication does not save you. Running both against the same repo means every
hook fires twice, with two possibly-different plugin versions disagreeing about what's current.
Pick one source per repo and stay on it.

## Something red?

A red doctor, a wedged install, a refused merge, a stale plugin version — every recovery
runbook is in **[troubleshooting.md](troubleshooting.md)**, symptom-first.

## Where things live

| Artifact | Path |
|---|---|
| Tickets | GitHub issues (the record); the active one in the git dir (`git rev-parse --git-path foundry-ticket.json`), never committed |
| Opt-in specs + contracts | `specs/features/<product>/<domain>/<capability>/` |
| Operator registry / project config | `.claude/foundry-operators.json` / `.claude/foundry-project.json` |
| Decisions + research | `.foundry/decisions/`, `.foundry/research/` + your git history (the ledger) |
| Stack profiles | `packs/stack-profiles/` (node-web, aws-eks-karpenter, python-uv-lib, python-uv-service) |
