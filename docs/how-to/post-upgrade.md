# How to finish a plugin upgrade

An upgrade is two commands, in this order, and they do different halves of the work.

## 1. The deterministic half — a script

```bash
cd <workspace>
npx update-agentic-workspace@latest
```

It refreshes the marketplace, updates the plugin in every scope that enables it, reconciles the
managed files (never overwriting one you edited), backfills the `## Amendments` section every spec
needs for `/foundry:amend`, and seeds `.foundry/permissions.yaml` when there is none. It previews
every `claude` call and every path before the first write, prints one row per phase, and ends with:

```
next: run /foundry:post-upgrade in your next session (report: .foundry/upgrade-report.json)
```

That report carries the version it upgraded from and to, the phase verdicts, what the backfill did,
whether the policy file was created or kept, and which managed files were drifted. `.foundry/` is
gitignored, so it stays local.

## 2. The judgement half — a skill

Open a session in the workspace and run `/foundry:post-upgrade`. It refuses without a fresh report,
then walks, in order:

1. a commit of exactly what the updater wrote (the report lists every path), first on the PR branch —
   so the workspace's `main` matches what its sessions run;
2. the CHANGELOG sections between the two versions, as an inventory of what was retired and added;
3. standing grants into `.foundry/permissions.yaml` (grants only widen), compiled, until the doctor
   reads `policy in-sync`;
4. `requires_capabilities` on contracts that are not frozen (frozen ones are listed for `/foundry:amend`);
5. a relock when the stack-profile lock is behind the profile version the plugin ships;
6. a truth pass over `CLAUDE.md`, `docs/`, the prose under `.claude/` and your memory directory against
   the inventory — out-of-repo deletions are quoted in the PR body because the diff cannot show them;
7. retired-artifact cleanup (`--cleanup`), which removes only what the reference scan cleared;
8. branch garbage collection, dry-run first, then `--apply` on the merged class;
9. verification: doctor green with no mechanical advisory left;
10. one PR to `main` with a before/after table and the list of deletions.

It never edits a frozen contract, never deletes a branch the gc did not classify as merged, and never
writes the agent-teams flag into a settings file.

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
