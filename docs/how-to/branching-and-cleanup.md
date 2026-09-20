# How to keep branch/worktree hygiene: one push per atom, one deploy per release

`context/branch-discipline.md` is the full discipline (cited by
`skills/mode-autonomous/SKILL.md`, `skills/command-deck/SKILL.md`,
`skills/command-deck/tick-prompt.template.md`, and `skills/dispatch/SKILL.md`). This page is the
worked adopter example plus the cleanup recipe (branch-and-worktree-discipline, `AC-BWD-7`,
v1.16.0).

## The failure this closes

An agent landing a release as one PR-to-`main` PER ATOM triggers one staging build+deploy per atom
on any adopter whose CI deploys from `main`. Quoted, never paraphrased, from an adopter's own app
repo (never a client name in a shipped file):

- `ci.yml`'s own header: `"AC-CMC-1 at auth_seq=4 states the PROPERTY — no change reaches main
  without this pipeline having run on it — plus an explicit EXACTLY-ONCE condition."` and its
  trigger block: `push: branches: [main]` plus `pull_request:` plus `merge_group:`.
- `app-image.yml`'s own header: *"Builds the production Dockerfile ... and pushes it, keyless via
  GitHub OIDC ... assuming the least-privilege push role"* — trigger: `push: branches: [main]` plus
  `workflow_dispatch:`.
- `gitops-writeback.yml`'s own header: *"After the sibling 'Build & push app image' workflow
  succeeds on `main`, this workflow re-derives every just-built image's digest from ECR, bumps the
  matching `@sha256` pin in EVERY ... GitOps manifest ... pushes ... (which ArgoCD tracks → it
  deploys the new images)"* — trigger: `workflow_run: workflows: ["Build & push app image"]`.

Three workflows, chained on `push: main`: an atom-per-PR-to-`main` release fires the whole chain —
build, push, GitOps writeback, ArgoCD deploy — once per atom instead of once per release. Separately,
worktrees and branches accumulate: after one programme the plugin repo itself carried
**sixty-three** remote branches, most already merged, because the manual cleanup step
(`foundry-work-isolation.sh cleanup`) is a step nobody runs, and a squash merge leaves the remote
branch behind even when it is run.

## The fix: a release branch batches the deploy trigger

1. `/foundry:intake`'s last step, when opening a new release, creates `release/<version>` from
   `main` and records it in the manifest as `integration_branch`.
2. Every atom builds on `atom/<id>` cut from `release/<version>`, and its own PR targets
   `release/<version>` — **not** `main`. Per-atom CI checks still run (they gate the atom PR); the
   `push: main` chain above simply never fires per atom, because no atom PR ever merges into
   `main`.
3. Once every atom has landed on `release/<version>`, **one** PR — `release/<version>` → `main` —
   merges the whole release at once. The `push: main` chain fires exactly once.
4. A hotfix is the one exception: `hotfix/<id>` → `main` directly (stated in the hotfix PR's own
   body) — it cannot wait for the next release to cut.

`skills/cut-release/SKILL.md`'s R/R2 procedure is otherwise unchanged; it now runs on
`release/<version>` instead of directly on a bespoke feature branch.

`/foundry:merge-when-green --release <id>` enforces the second rule above: it refuses (structured `blocked`,
`remediation: "gh pr edit <n> --base release/<version>"`) an atom PR whose base is `main` when the
named release's manifest carries an `integration_branch`.

### The security-review label re-pins on retarget, and the release branch's own merge-floor tier

Learned dogfooding this flow (recorded here, not as a new acceptance criterion): `btb-gates`'s
`security-path`/`spec-link` review label is `security-reviewed:<head12>-<base8>`, where `base8` is
derived from the **base ref name itself** (`.github/workflows/btb-gates-base.yml`):

```bash
BASE_TAG="$(printf '%s' "$BASE" | sha256sum | cut -c1-8)"
```

Retargeting a PR from `main` to `release/<version>` (`gh pr edit <n> --base release/<version>`, the
remediation above) therefore changes the expected label — a security-reviewed label pinned against
`main`'s own `base8` hash does not carry over to the new base and needs re-pinning against the
release branch's own hash.

An **unprotected** `release/<version>` also reads `merge floor tier: B (advisory)` from
`scripts/foundry_tier_preflight.py` — the checks still gate `/foundry:merge-when-green` (it never
merges on anything but every check green), but nothing server-side blocks an out-of-band merge the
way Tier A branch protection blocks one on `main`. Two reasonable choices, adopter's call: protect
`release/<version>` with the same required contexts as `main` for the release's lifetime, or accept
Tier B for the integration branch on the reasoning that `main` itself stays Tier A at the single
release PR, which is the one merge that actually needs it.

## The cleanup recipe: `scripts/foundry-worktree-gc.py`

`--dry-run`/`--apply` come FIRST, always — the permission-floor rows that tier this script are
argv prefix rules and only match with the mode flag as the first argument:

```bash
# Always dry-run first -- read-only, prints the classification.
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-worktree-gc.py" --dry-run --repo /path/to/repo

# Only after reviewing the dry-run output: delete the merged class.
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-worktree-gc.py" --apply --repo /path/to/repo
```

It classifies every linked worktree and every local/remote branch matching `atom/*`, `release/*`,
`hotfix/*`, `fix/*`, `feat/*`, `docs/*` by real git ancestry against `origin/<default-branch>`
(falling back to `gh pr list --state merged --head <branch>` for a squash/rebase merge whose tip is
not a literal ancestor — accepted ONLY when the PR's own `headRefOid` equals the branch's current
tip, so a stale merged-PR record left over from an earlier, different push at a REUSED branch name
can never mark today's unmerged commits as merged) into four classes:

| class | meaning | `--apply` deletes it? |
|---|---|---|
| `protected` | the default branch (`main`), or a name passed via `--protected` | never |
| `merged` | contained in the base, or a tip-matched merged PR reported by `gh` | **yes** — worktree + local (`-d`, falling back to `-D` only on that refusal) + remote branch |
| `open-pr` | not merged, but `gh` reports an open PR | never |
| `unmerged-no-pr` | neither — listed with its age | never, regardless of age |

Refuses outright (before classifying anything) on a `--repo` outside the operator's home directory,
or a dirty working tree. `scripts/foundry-doctor.py` carries one advisory line — `branches: <n>
merged-not-deleted, <m> stale worktrees` (never RED) — computed by importing this script's own
classifier, so drift is visible on every doctor run.

`foundry-work-isolation.sh cleanup <repo> <branch>` (`skills/work-isolation/SKILL.md`) is
**superseded** for the repo-wide sweep by this script (left in place, not deleted, for the one
still-live case: cleaning up a single just-merged worker's worktree+branch inline, mid-session).
