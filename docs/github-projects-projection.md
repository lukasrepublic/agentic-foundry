# GitHub Projects living-view projection

This doc covers the two operator-facing questions for wiring the org-Project projection into
your own product repo: **what token do I need**, and **how do I install the workflow**.

## Token scope — a project-scoped GitHub App, not a personal PAT

Writing to a GitHub org-level **Project (v2)** requires the **`project`-scoped** credential.
This is a hard requirement, not a convenience recommendation: the default per-repo
`GITHUB_TOKEN` that GitHub Actions injects automatically is **repo-scoped only** — it cannot
even **read** an org Project, let alone write to one (`addProjectV2ItemById`,
`updateProjectV2ItemFieldValue`, `addSubIssue` all fail against it). So the shipped CI
workflow (`.github/workflows/foundry-project-sync.yml`) never uses `secrets.GITHUB_TOKEN` for
the projection step; it reads a **separate** repository secret named after
`github_projects.auth_env` (default `FOUNDRY_PROJECTS_TOKEN`) that you provision yourself.

You have two ways to obtain a `project`-scoped credential:

- **A `project`-scoped GitHub App (RECOMMENDED).** Install a GitHub App on the org with the
  `Organization > Projects: Read and write` permission (plus `Issues: Read and write` /
  `Contents: Read` on the target repo), generate an installation access token, and store it as
  the `FOUNDRY_PROJECTS_TOKEN` repository secret (or rotate it via a token-vending step ahead
  of the sync job). This is the **least-privilege, auditable** choice: the App is
  org-installed, every write is attributable to the App's own identity (not a person), its
  scope is fixed at install time, and it is revocable independently of any individual's
  account or offboarding.
- **A personal PAT (project-scoped) — NOT RECOMMENDED for anything beyond a quick local
  trial.** A classic or fine-grained PAT with the `project` scope works functionally, but it is
  bound to one person's identity, is typically broader than the App path, and becomes a
  rotation / offboarding liability (the projection silently breaks — or worse, silently
  over-privileges — whenever that person's account changes). **Prefer the GitHub App over a
  personal PAT** for any shared/CI use of this projection.

The token is read from the `auth_env`-named environment variable **only** — PTC (the projector
CLI, `scripts/foundry-project-sync.py`) defines no `--token` flag and honors no config-file
token field; the credential is never logged, echoed, or written to config. `sync` fails CLOSED
(naming the missing env var, zero GitHub calls) when `github_projects.enabled: true` but the
named secret is unset.

## Ship model — a copy-in template, not an always-active workflow

`.github/workflows/foundry-project-sync.yml` ships in the `agentic-foundry` plugin repo as a
**copy-in template**: it is not an always-active workflow that runs in the plugin repo's own
CI (the plugin repo carries no `github_projects` config, so the projection config's own INACTIVE gate would make
it a no-op there regardless). It exists so **you** can install it into **your own product
repo**, where it reacts to *your* `specs/**` changes and PR/issue events.

**Install step:**

1. Copy the file into your product repo at the same path:
   `.github/workflows/foundry-project-sync.yml`.
2. Add a `github_projects` block to your repo's `.claude/foundry-project.json`, e.g.:
   `{ "github_projects": { "enabled": true, "org": "<your-org>", "project_number": <n>,
   "auth_env": "FOUNDRY_PROJECTS_TOKEN" } }`. `enabled` gates the projection; `org` + `project_number` identify the target
   org-level Project (v2); `auth_env` names the repository secret described above.
3. Add a repository secret named after `github_projects.auth_env` (default
   `FOUNDRY_PROJECTS_TOKEN`) holding the project-scoped GitHub App token described above.
4. Commit + push. The workflow now fires on the standard living-view triggers: a `push`/
   `pull_request` touching `specs/**`, `pull_request` `opened` / `ready_for_review` / `closed`,
   and `issues` lifecycle events — each run simply invokes PTC's `sync`, which re-derives the
   affected atom(s) from file-truth and idempotently upserts the Project view.

## The auto-close caveat (`Closes #<atom-issue>`)

An atom's implementation PR body should carry a `Closes #<atom-issue>` line — add it yourself
(or via your PR template) at PR-cut when the atom's issue number is present in the local
`.foundry/project-map.json` cache (written by PTC's `sync`). Nothing automates this line today. GitHub's
closing-keyword auto-close fires **only when the PR merges into the repository's default
branch** — a PR merged into any other branch, or a PR that is closed without merging, does
**not** close the linked issue. This is the intended coupling: Done ⇔ merged-to-default, driven
by the `pull_request:closed` (merged) trigger above re-projecting the atom's Status.

## One-way floor

The Project board is a **derived, disposable view**, never a source of truth: no shipped
module here reads a Project field back into a spec or acceptance-contract file. A hand-dragged
card is corrected back to the derived value on the next `sync` (self-heal) — the files plus the
merge floor's green checks remain authoritative.
