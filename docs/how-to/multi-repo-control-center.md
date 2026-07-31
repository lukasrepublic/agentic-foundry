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
