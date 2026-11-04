# delivery kit

Branches, commits, merge and release. Rules: `rules.yaml` (FG-DLV-001..019). Floors: branch discipline
(FG-DLV-001), the merge-queue obligations (FG-DLV-004, 012..019), never editing CI to pass (FG-DLV-010) and the delivered-by record (FG-DLV-011).

Templates and snippets: a commitlint config, a release-please config and a CODEOWNERS template. The merge
queue, merge plan and paper guard are shipped by the factory scripts and hooks named in each rule's `check.ref`.

## Planned

Shipped later: `scripts/foundry_queue_land.py`, `scripts/foundry_merge_plan.py` and `hooks/foundry-paper-guard.py`
(gv-10). The flag-cleanup lens that FG-DLV-008 names is added to `agents/pr-reviewer.md` by gv-16.
