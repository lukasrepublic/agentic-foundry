# Branch and worktree discipline

Authored ONCE here (branch-and-worktree-discipline, AC-BWD-1, v1.16.0) and INCLUDED BY REFERENCE
from `skills/mode-autonomous/SKILL.md`, `skills/command-deck/SKILL.md`,
`skills/command-deck/tick-prompt.template.md`, and `skills/dispatch/SKILL.md` — never copied.
If you are reading one of those files and were pointed here, this section IS the full discipline;
treat a local paraphrase you find elsewhere as stale.

**The observed failure this closes** (charter `branch-and-worktree-discipline.md`): an agent
landing a release as one PR-to-`main` PER ATOM triggers one staging build+deploy per atom on any
adopter whose CI deploys from `main` (an adopter's app repo: `ci.yml` runs on `pull_request` +
`push: main`; its image-build workflow builds on `push: main`; a GitOps-writeback workflow deploys
from that build) — saturating the pipeline with noise. Separately, worktrees and branches
accumulate: after one programme the plugin repo itself carried sixty-three remote branches, most
already merged, because the shipped manual cleanup
(`skills/work-isolation/SKILL.md`'s `foundry-work-isolation.sh cleanup`, now SUPERSEDED by
`scripts/foundry-worktree-gc.py` for the post-merge sweep — see below) is a step nobody runs, and a
squash merge leaves the remote branch behind even when it is.

## The six rules

1. **One worktree, one branch, per atom.** Every atom builds in its own worktree on
   `atom/<id>`, cut from the **release's integration branch** (`release/<version>` — created from
   `main` at `/foundry:intake`'s last step, never mid-release). Never build two atoms on one
   branch; never build directly on `main` or on the release branch itself.
2. **Local-green before the first push.** Commits go to `atom/<id>` first. The atom's full local
   suite (and `foundry-doctor.py`) must pass BEFORE the first `git push` — **one push per atom,
   never a work-in-progress push.** A red local run is a reason to keep iterating locally, not to
   push and let CI find it.
3. **The atom's PR targets the release branch, not `main`.** `gh pr create --base
   release/<version>` (NOT `--base main`) whenever the release manifest names an
   `integration_branch` (`scripts/foundry_release.py`'s optional field — see
   `skills/cut-release/SKILL.md`). CI test checks still run per atom PR, exactly as before — only
   the merge target moves. `/foundry:merge-when-green` refuses a PR whose base is `main` when the
   manifest names an `integration_branch` (`skills/merge-when-green/SKILL.md` — the structured
   `blocked` remediation is `gh pr edit <n> --base release/<version>`).
4. **`main` receives ONE PR per release.** `release/<version>` → `main`, squash or merge per the
   repo's own branch protection, once every atom in the release has landed on the release branch —
   so a per-merge deploy trigger fires **once per release**, not once per atom. **Hotfixes are the
   one exception:** `hotfix/<id>` → `main` directly, stated plainly in the hotfix PR's own body (no
   release branch involved — a hotfix cannot wait for the next release to cut).
5. **Delete after `origin` contains the merge, never before.** Once a PR merges and
   `origin/<target>` (the release branch, for an atom PR; `main`, for the release PR) contains the
   commit, the worktree and BOTH the local and remote branch are deleted. **Never before** — a
   premature delete closes the PR instead of merging it (memory: #149 → #151). `git worktree
   remove` first (it may still hold the harvested worker-learnings sidecar,
   `skills/work-isolation/SKILL.md`), then `git branch -d`, then `git push origin --delete
   <branch>`.
6. **The release branch itself is deleted too** — after ITS PR to `main` merges and the release's
   tag exists (`skills/cut-release/SKILL.md`), the same way: `origin/main` contains the merge
   commit first, delete after.

## The codified cleanup: `scripts/foundry-worktree-gc.py`

Rule 5/6's "delete after, never before" was, until this atom, a step an operator or agent had to
remember to run by hand (`foundry-work-isolation.sh cleanup <repo> <branch>`,
`skills/work-isolation/SKILL.md`) — the step nobody ran, at the root of the sixty-three-branch
accumulation this charter opens with. That script's manual cleanup recipe is now **SUPERSEDED**
(left in place, not deleted, for the single still-live case: cleaning up ONE just-merged
worker's worktree+branch inline, mid-session, the instant its PR merges) by the sweep this script
runs over the whole repo at once:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-worktree-gc.py" --repo <dir> --dry-run   # default-safe
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-worktree-gc.py" --repo <dir> --apply     # deletes the merged class only
```

It lists every linked worktree and every local/remote branch matching `atom/*`, `release/*`,
`hotfix/*`, `fix/*`, `feat/*`, `docs/*`, classifies each by real git ancestry against
`origin/<default-branch>` (`git merge-base --is-ancestor`, falling back to `gh pr list --state
merged --head <branch>` for a squash/rebase merge whose tip is not a literal ancestor) into
`merged` / `open-pr` / `unmerged-no-pr` / `protected`, and — **only under `--apply`, and only for
the `merged` class** — removes the worktree and deletes both the local and remote branch.
`unmerged-no-pr` is always listed with its age and NEVER deleted, regardless of how old. `--repo`
outside the operator's home, or a dirty working tree, is refused outright. Run `--dry-run` first,
always; `--apply` is `ask`-tiered in the permission floor, same ceremony class as
`foundry-merge-when-green.py`'s own merge call.

`scripts/foundry-doctor.py` carries one advisory line, `branches: <n> merged-not-deleted, <m>
stale worktrees` (never RED — see the doctor's own module docstring, probe 9), computed by
importing this script's classifier, so the drift this discipline exists to prevent is visible on
every doctor run without a live `gh` call.

## See also

- `skills/cut-release/SKILL.md` — the release-branch flow (intake creates it, cut-release tags and
  retires it).
- `skills/intake/SKILL.md` — `release/<version>` creation, the manifest's own `integration_branch`
  field.
- `skills/merge-when-green/SKILL.md` — the base-branch refusal (rule 3).
- `skills/work-isolation/SKILL.md` — the superseded manual cleanup step, and the still-live
  inline single-worker case.
- `docs/how-to/branching-and-cleanup.md` — the worked adopter example + the gc recipe.
