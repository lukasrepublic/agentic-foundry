# How to run a multi-repo control plane

One workspace can govern several code repos — an app repo, an infra repo, services — with
specs living in the workspace and each atom's code landing in its declared target repo.

```
  the workspace (specs + governance)          the hosted repos (code)
 ┌──────────────────────────────────┐
 │ specs/features/app/…             │        ┌───────────────┐
 │ specs/features/infra/…           │   ┌───▶│  app repo      │  (gitignored sibling
 │ .claude/foundry-project.json     │   │    └───────────────┘   subdir, own history)
 │   repos:                         │   │    ┌───────────────┐
 │     workspace: {…}      dispatch─┼───┼───▶│  infra repo    │
 │     app:   {path, kind, role}    │   │    └───────────────┘
 │     infra: {path, kind, role}    │───┘
 └──────────────────────────────────┘
   each contract carries target_repo: <key> — frozen, so where code lands
   is part of what you authorized
```

On disk that is **several git repositories inside one directory tree**:

```
acme-handbook/                      ◀── git repo #1 — the control plane. You commit this.
├── .claude/foundry-project.json        the manifest: repos{} maps a key → a path below
├── specs/features/…                    the WHAT, for every repo
├── .gitignore                          lists each hosted repo, root-anchored (/api/)
│
├── api/                            ◀── git repo #2 — gitignored, own history + CI + floor
│   └── .git/ · src/ · .foundry/build-provenance.yaml
└── infra/                          ◀── git repo #3 — gitignored, same deal
    └── .git/ · terraform/
```

`git status` in the control plane never shows anything from `api/` or `infra/`.

## The pattern: gitignored siblings, never git submodules

The control plane hosts each code repo as an **independent, gitignored sibling clone** at a
path the manifest declares — never a git submodule. A submodule pins the parent repository to
one exact child commit and entangles the two histories together; a control plane wants each
hosted repo evolving under its own origin, its own history, and its own release cadence, with
the workspace only ever recording *where* it lives and *which* atom is authorized to touch it.
That is why this pattern is plain clones plus a manifest — not a submodule, deliberately.

(Google `repo`'s `manifest.xml`, the `meta` tool's `.meta`, vcstool's `.repos` and `myrepos`
all converge on this same shape — a committed manifest declares the repo set, children are
plain independent clones, and the parent gitignores them.)

## The registry — `.claude/foundry-project.json`

`repos{}` is the manifest. Each key is the **dispatch key** an acceptance contract's
`target_repo:` field names; `path` is the one field the resolver (`foundry-wt`) actually
reads.

```json
{
  "schema_version": 1,
  "repos": {
    "app":   { "path": "./my-app",   "kind": "code", "role": "product" },
    "infra": { "path": "./my-infra", "kind": "code", "role": "infra" }
  }
}
```

This example validates against the shipped `schema/foundry-project.schema.json` — its one
required member is `schema_version`. Field ledger for the example above, inline against each
field: `path` is read, by `foundry-wt`'s resolver. `kind` and `role` are **inert** in this
example — accepted only because the schema's own `additionalProperties: true` lets them
through; no shipped code reads either one today (both are reserved for a future
registry-formalization atom).

The key `workspace` is **not** a `repos{}` row you add yourself: `foundry-wt` resolves it
**itself**, to the control-plane root, so a single-repo adopter's manifest needs no
`workspace` entry at all — nothing multi-repo activates until you add a hosted-repo key.

## The pairing rule: gitignore, then clone, then register

Two rules, always together:

- **The pairing rule.** Every hosted repo carries **both** a root-anchored `.gitignore` entry
  **and** a `repos{}` row — either alone is a defect. A `repos{}` row with no clone on disk is
  a **dangling entry**, indistinguishable downstream from a repo that simply has not been
  cloned yet (the DANGLE direction). A clone made **before** its `.gitignore` entry exists
  **spills** the hosted repo's own working tree — its secrets, its `.env` files, its runtime
  state — into the control plane's own history the next time you run `git add -A` (the SPILL
  direction).
- **The ordering rule.** Add the `.gitignore` entry first, clone second, register the
  `repos{}` row third. Reversing the first two steps is exactly how a spill happens.

## The session rule — run Claude from the control-plane root, never from a hosted repo

```bash
cd ~/work/acme-handbook       # ✅ the factory is live here
claude

cd ~/work/acme-handbook/api   # ❌ a plain session with none of the factory
claude
```

Claude Code resolves everything from the session's project directory (`CLAUDE_PROJECT_DIR`,
falling back to the working directory), and every piece of the factory lives at the control
center's root: the plugin wiring (`.claude/settings.json`), the operator registry, the `repos{}`
manifest, your specs, and the governance hooks. Start a session inside `api/` and none of it
loads — no `/foundry:*` verbs, no authorization, no git-discipline guard. It fails by **absence**
rather than with an error.

A repo nested **under** the control-plane root is already inside the session's scope — Claude
Code treats every directory below the project root as reachable, and `--add-dir` exists for
directories *outside* that root. `--add-dir` is therefore neither needed to reach a nested
hosted repo, nor a remedy for a session that was started in the wrong place to begin with: bolting
`--add-dir` onto a session rooted inside `api/` still never loads the control plane's own wiring.

**State the consequence honestly: a control-plane-root session holds read/write reach over
every hosted repo's working tree — that is its blast radius, not just the repo an atom
targets.** The control plane's own `.gitignore` *hides* those edits from the control plane's
own `git status`; it does not confine them. A contract's `target_repo` and
`scope.allowed_paths` bind the **jailed worker** dispatched into a worktree — never the root
session itself, which can still touch anything on disk. If a hosted path must stay genuinely
out of reach from the root session, the remedy is a `permissions.deny` rule for that path in
`.claude/settings.json`; the session-scope boundary described above is not that remedy.

**The control-plane preflight catches the wrong-root mistake — in the common case.**
`/foundry:doctor`'s `control-plane` check (`scripts/foundry_control_plane.py`) walks upward from
the session's project directory looking for an ancestor `.claude/foundry-project.json`; if one
names your session root as a hosted repo (`repos{}` entry), or your session is merely rooted
somewhere below one, it reports **RED**, naming the control plane and the remedy.
`/foundry:init` also runs it as its first step, before writing anything. See
`feat-foundry-control-plane-preflight` for the full contract — in particular, it is a
**mistake-catcher for the operator, not a floor**: `--session-start` warns but still exits `0`,
and the operator-invoked `/foundry:doctor` exit code is its only real enforcement.

### The residual — when this cannot catch you

These checks are reachable **whenever the plugin loads**, which is the common case: `claude
plugin install` writes `enabledPlugins` to `~/.claude/settings.json` **user-wide**, so the plugin
(and the doctor) load even from inside a hosted repo — pointed at the wrong root, which is exactly
what the check convicts. The checks are **unreachable only** on a machine where the plugin was
instead enabled **per-project**, in the control plane's own `.claude/settings.json` — there, a
session started inside a hosted repo does not load the plugin at all, so it fails by absence and
the doctor is never reached to convict it. This is a narrow residual, not a general claim that the
mistake is undetectable — most adopters install the plugin the ordinary way (user-wide) and the
check is live for them.

You still *work on* code in the hosted repos — the factory dispatches workers into those working
trees. You just drive it from one session, at the top.

## `target_repo` and provenance

Every acceptance contract's `target_repo:` field names a `repos{}` key — the atom's code lands
in that repo, and nowhere else. Each atom built this way writes a
`.foundry/build-provenance.yaml` file in its own code repo, pinning the exact control-plane
commit the atom was authorized against — the cross-repo audit trail an operator can always walk
back.

Re-pointing `target_repo` after authorization is not a quiet edit: it lives inside the
hash-covered region of the frozen `acceptance-contract.yaml`, so moving where code lands
invalidates the frozen `contract_sha256` and forces re-authorization — the `target_repo freeze`
label below states this as a derived, machine-checked fact, not a promise.

## What is enforced, and what is practice

Being honest about which of these rules a machine actually holds — and which rely on you — is
the point of this section. Every label below is derived by exercising the shipped code, not
asserted from memory; drift here fails this plugin's own test suite.

- **repo-key resolution** — machine-enforced. `foundry-wt resolve` fails closed on an unknown
  `repos{}` key, on a `path` that is not an existing directory, and on a path that escapes the
  workspace root.
- **dispatch bind-check** — machine-enforced. `hooks/foundry-worktree-create.sh` calls
  `foundry-wt bind-check` on its redirect path; when the dispatch manifest's `target_repo`
  disagrees with the atom's authorized contract, the bind check fails and **no worktree is
  created** — the dispatch is dropped fail-closed.
- **target_repo freeze** — machine-enforced. `target_repo` sits inside the region
  `contract_sha256` covers, above the trailer sentinel, so re-pointing it after authorization
  invalidates the frozen hash and forces re-authorization; the hook's bind-check above reads
  that same frozen value.
- **authorization venue floors** — not-enforced-today. With a `target_repo` that does not
  resolve to a cloned repo, `scripts/foundry-authorize.py` prints `warn: … degraded` /
  `SKIPPED` for every venue-grounded floor (surface⊆scope, the doctor-row baseline,
  system-grounding, `allowed_paths` grounding, checkpoint-locator grounding) and **freezes
  anyway** — the venue floors *degrade to warnings* and the write proceeds regardless,
  incrementing `auth_seq` in the frozen `authorized:` trailer.
- **doctor registry validation** — machine-enforced. The operator-invoked `/foundry:doctor`
  reads `repos{}` and exits non-zero on a dangling entry or on a wrongly-rooted session; the
  advisory `--session-start` cadence still exits zero regardless, on purpose, so it never wedges
  a session.
- **pairing rule** — practice. Nothing machine-checks that a `.gitignore` entry and a `repos{}`
  row were added together.
- **clone-before-register ordering** — practice. Nothing machine-checks that the clone happened
  before the `repos{}` row was written.
- **session-root rule** — machine-enforced. The operator-invoked doctor now catches a session
  rooted inside a hosted repo, or nested below the plane without being one itself (the residual
  above still applies) — this is a mistake-catcher for the operator, not a merge-blocking floor.

## Steps

1. **Clone the hosted repo into the workspace as a gitignored subdir** (add the dir to the
   workspace `.gitignore` first, per the pairing/ordering rules above).

2. **Declare it** in `.claude/foundry-project.json` — see the registry section above for the
   field reference and a schema-valid example.

3. **Target atoms at it.** The acceptance contract's `target_repo:` names the repo key — see
   the `target_repo` and provenance section above.

4. **Dispatch as usual.** The worker is redirected into the hosted repo's working tree; its
   PR and merge floor are that repo's own (each hosted repo carries its own branch
   protection + CI — tier is per-repo).

5. **Provenance flows back** — see the `target_repo` and provenance section above.

## Defaults and doctor

An atom whose contract names no `target_repo` is workspace-targeted — the single-repo default.
Nothing multi-repo activates until you add a hosted-repo entry to `repos{}`.

## The rules

- **One atom, one target repo.** A change spanning two repos is two atoms with an
  explicit dependency, not one atom with two targets.
- **The merge floor is per-repo** — check each hosted repo's tier honestly
  ([merge-floor.md](../merge-floor.md)); a Tier-A workspace does not confer Tier A on a
  hosted repo.
- **Never commit a hosted repo into the workspace** — the gitignore is load-bearing.

---

**Setting up a project from scratch this way?** The workspace template carries the full
step-by-step guide — layout, per-repo merge floors, shipping an atom across two repos, and
day-two operations: [agentic-handbook → docs/control-plane.md](https://github.com/lukasrepublic/agentic-handbook/blob/main/docs/control-plane.md).
