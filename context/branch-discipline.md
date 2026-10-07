# Branch discipline

Measured on an adopter app repo before this page existed: 890 pull requests in 56 days (median
5 files), each a 32-minute CI run, every merge an image build and a staging deploy; zero release
branches among 963 remotes. The rules below are **enforced by the git-discipline hook**, not
requested in prose.

1. **One ticket, one branch, one PR.** `git switch -c <ticket>-<slug>` from `main`. Commit as
   often as you like on the branch; nothing you commit there runs CI.
2. **Local-green before the first push.** `"$CLAUDE_PLUGIN_ROOT/scripts/foundry-test.sh"`
   runs the repository's own CI command (Taskfile `ci`/`test`, `make test`, `npm test`, pytest)
   and records `<git-dir>/foundry-local-green` for HEAD. `git push` and a non-draft
   `gh pr create` are **refused** without it (clause (j); `--local-green=off` in `hooks.json`
   is the only opt-out). Deletes, `--dry-run` and tag pushes are exempt.
3. **Draft until done.** Open the PR as a draft while iterating; mark it ready when `Done means`
   passes locally. CI on drafts should be skipped by the repo's workflow (`ready_for_review` +
   path filters + `concurrency: cancel-in-progress` — see the handbook's CI template).
4. **Deploy on promote, not on merge.** Image build + GitOps write-back on a tag or a
   `deploy/<env>` label. A merge to `main` is integration, not a release.
5. **Branch protection is the merge gate.** Required checks on, `strict` (up-to-date) **off** —
   it voided PRs on every co-author merge. No `--admin`, ever.
6. **Clean up after the merge is on `origin/main`:** delete the local branch, the remote branch and
   the worktree — `python3 "$CLAUDE_PLUGIN_ROOT/scripts/foundry-worktree-gc.py" --apply`. Never
   before (a premature delete closes the PR).
7. **Docs do not go through the code repo's pipeline.** Documentation lives in the workspace
   repo (no CI); a docs-only PR to a code repo is a defect.

The plugin's own repository additionally cuts releases from a `release/<version>` branch
(`skills/cut-release`), no more often than monthly.
