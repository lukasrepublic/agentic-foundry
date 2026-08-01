# How to run a multi-repo control center

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

**The pattern is gitignored siblings + a manifest — not git submodules.** Each hosted repo
keeps a fully independent history; the control center is never coupled to a submodule
commit pointer.

On disk that is **several git repositories inside one directory tree**:

```
acme-handbook/                      ◀── git repo #1 — the control center. You commit this.
├── .claude/foundry-project.json        the manifest: repos{} maps a key → a path below
├── specs/features/…                    the WHAT, for every repo
├── .gitignore                          lists each hosted repo, root-anchored (/api/)
│
├── api/                            ◀── git repo #2 — gitignored, own history + CI + floor
│   └── .git/ · src/ · .foundry/build-provenance.yaml
└── infra/                          ◀── git repo #3 — gitignored, same deal
    └── .git/ · terraform/
```

`git status` in the control center never shows anything from `api/` or `infra/`.

## Run Claude from the control center, never from a hosted repo

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
rather than with an error, and no preflight check currently catches it.

You still *work on* code in the hosted repos — the factory dispatches workers into those working
trees. You just drive it from one session, at the top.

## Steps

1. **Clone the hosted repo into the workspace as a gitignored subdir** (add the dir to the
   workspace `.gitignore` first if the template hasn't already).

2. **Declare it** in `.claude/foundry-project.json`:

   ```json
   {
     "repos": {
       "workspace": { "path": ".",            "kind": "workspace" },
       "app":       { "path": "./my-app",     "kind": "code", "role": "product" },
       "infra":     { "path": "./my-infra",   "kind": "code", "role": "infra" }
     }
   }
   ```

3. **Target atoms at it.** The acceptance contract's `target_repo:` names the repo key.
   It lives in the hash-covered region — **changing where code lands requires
   re-authorization**, deliberately.

4. **Dispatch as usual.** The worker is redirected into the hosted repo's working tree; its
   PR and merge floor are that repo's own (each hosted repo carries its own branch
   protection + CI — tier is per-repo).

5. **Provenance flows back**: each built atom carries a `.foundry/build-provenance.yaml` in
   its code repo pinning the workspace commit it was authorized against — the cross-repo
   audit trail.

## Defaults and doctor

A fresh workspace seeds only the `workspace` self-entry and stays `DOCTOR-GREEN` as a
single-repo adopter; nothing multi-repo activates until you add an entry. An atom whose
contract names no `target_repo` is workspace-targeted — the single-repo default.

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
