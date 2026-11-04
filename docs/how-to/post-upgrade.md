# How to finish a plugin upgrade

An upgrade is two commands, in this order, and they do different halves of the work. Installing the
new plugin version is one thing; adopting it is another. **Installation can be complete while adoption is
still pending, and every output says so.**

## 1. The deterministic half — a script

```bash
cd <workspace>
npx update-agentic-workspace@latest        # add --dry-run first to see the plan and write nothing
```

It refreshes the marketplace, checks the version it is about to install against the version it was built
for (a stale npx cache stops the run before anything changes), updates the plugin in every scope that
enables it, reconciles the managed files (never overwriting one you edited), and seeds
`.foundry/permissions.yaml` when there is none. It previews every `claude` call and every path, each with
its class (`framework-owned`, `project-customization` or `retained-history`), and every settings key it
would change with a `widens` or `narrows` impact, before the first write. It ends with one line:

```
outcome: complete · next: run /foundry:post-upgrade in your next session (report: .foundry/upgrade-report.json) · cutover: pending
```

- `complete` exits `0`, `attention-needed` exits `2` (the run finished; read `compatibility.diagnoses` in the
  report), and `refused` (stopped, nothing changed) and `partial` (stopped after a change) both exit `1`.
  The line names the next action either way. A clean exit does not mean the project has moved to 2.0.
- `cutover:` is `pending` until a cutover record exists at `.foundry/migration/authority.yaml`; an
  unreadable record reads `unknown`. The updater only reads it.
- Retained history is not rewritten by default. `--legacy-maintenance` opts in to the old Amendments
  backfill and release-manifest true-up, and only while the cutover is `pending`.
- `--cleanup` moves retired artifacts into `.foundry/retired/<run-id>/` instead of deleting them.

`.foundry/upgrade-report.json` carries the versions it moved between, the phase verdicts, the settings
changes it made, the cutover state, and what is still pending. `.foundry/` is gitignored, so it stays local.

## 2. The judgement half — a skill

Open a session in the workspace and run `/foundry:post-upgrade`. It stops on a report that is not
schema `2` ("run the updater first") and on a `refused` or `partial` outcome (it prints the `next_action`);
it carries an `attention-needed` report's diagnoses into the pull request. Otherwise it walks to the end
without stopping — every step has a default, and anything you should see goes in the PR body instead of a
mid-walk question:

1. **Commit the updater's writes** — first on the PR branch, so the workspace's `main` matches what its
   sessions run (the report lists its last run; earlier uncommitted runs are recognised by their shape);
2. **Standing grants** you have already stated — in the session or a committed CLAUDE.md; one found only in
   memory becomes a proposal in the PR body — into `.foundry/permissions.yaml`, then compiled;
3. **Stack-profile relock** when the lock is behind the profile version the plugin ships;
4. **Truth pass** over `CLAUDE.md`, `docs/`, the prose under `.claude/` and your memory directory, starting
   from the report's `adoption.instructions_pending` list;
5. **Retired-artifact cleanup** (`--cleanup`), which moves only what the reference scan cleared;
6. **Branch garbage collection**, dry-run first, then `--apply` on the merged class — whatever the count;
7. **Required-check review** — required checks nothing posts are listed for you, never changed;
8. **Verification** — doctor green with no mechanical advisory left;
9. **One pull request** to `main` with a before/after table, the deletion list, and the cutover state stated
   as the report records it.

It never edits a frozen contract, never deletes a branch the gc did not classify as merged, and never
writes the agent-teams flag into a settings file. Team mode is a separate, optional step you take after
the upgrade (`/foundry:team-mode enable`); the upgrade lands every workspace in single-operator mode.

## Restore before cutover

Until a cutover record exists, each phase of the updater has a supported undo. The report's `failure` and
`mechanical_steps` say which phases ran.

- **marketplace-refresh** — re-register the marketplace as it was: `claude plugin marketplace remove
  agentic-foundry --scope <scope>`, then `claude plugin marketplace add <source> --scope <scope>` with the
  source your settings file named before (the report's `operator_review[]` records the before value).
- **plugin-update** — install the earlier version by hand: `claude plugin install foundry@agentic-foundry
  --scope <scope>` after pinning the marketplace to the tag you want. The updater itself never installs a
  lower version.
- **python-deps** — `python3 -m pip uninstall <module>` for each module the run installed (the report's
  `python_deps.installed` names them); the packages live in the user site, not the system interpreter.
- **cleanup** — move each quarantined entry back: the new report's `cleanup[].restore_from` (and
  `.foundry/retired/<run-id>/manifest.json`) names where it is; `mv .foundry/retired/<run-id>/<path> <path>`.
- **reinitialization** — `git restore` the managed files the report's `written[]` lists (the settings
  rows, the status line wrapper, the seeded policy file); nothing it wrote is outside git except the
  gitignored `.foundry/` files.

## Repair after cutover

Once a cutover record exists, shared work lives on GitHub and a restore on one clone would diverge from
it. Repair forward instead: rerun `npx update-agentic-workspace@latest` (it recomputes every phase from
disk and converges), then `/foundry:post-upgrade`; fix a wrong setting with a new commit, not by rewinding.
The authority-side recovery after cutover is described with the cutover itself, not here.

## Rollback limits

- The updater never installs a lower plugin version; a `downgrade` diagnosis stops the run instead.
- A quarantined artifact can be restored only on the clone that holds it, because `.foundry/retired/` is
  gitignored; a teammate's clone restores from git history.
- Nothing prunes the quarantine automatically; you delete a run directory under `.foundry/retired/` when
  you choose.

## Why the split

A script can do anything that is idempotent and reversible over files. It cannot decide whether a
sentence in your CLAUDE.md is still true, which grants you hold, or whether a branch is safe to
delete. Putting those in a versioned skill means the procedure matches the release you just
installed instead of being a prompt written after each one — and the line between the two halves is
fixed: anything that would move a frozen hash, delete a ref, or change a grant stays on the session
side, where you see it.

## Related

- `cli-update/README.md` — what the updater does, phase by phase, and its exit codes.
- [Turn a standing grant into policy](standing-grants.md).
- [Branching and cleanup](branching-and-cleanup.md) — the gc's classification.
- `/foundry:upgrade` — config drift only (has your adopter config drifted since it was set up?).
