Hygiene:
- Pair every start with a teardown. Stop the containers, clusters, port-forwards, worktrees and scratch output you started, scoped by your own project label. Orphans are swept when the SubagentStop sweep is wired (gv-11); until then tear down yourself.
- Check the right disk. Measure the mount your work writes to, not only the root disk.
- Push every head early. A container restart wipes local disk, so push work in progress.
- Use one worktree per builder. `git stash` is shared across worktrees, so do not use it. Save diffs to files instead.
