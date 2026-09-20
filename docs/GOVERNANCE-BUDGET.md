# Governance budget

Five counts — skill prose lines, shipped scripts, floor rows, hook commands, pytest test count —
computed by `scripts/foundry-governance-budget.py` (AC-SUB-4, subtraction-wave, autonomy-
continuation R4). A pure reporter: read-only, writes nothing, exits 0 always.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-governance-budget.py"
```

## subtraction-wave (autonomy-continuation R4)

**Before** — branch base `7ffbff7` (v1.14.0 + #195, the commit `atom/subtraction-wave` forked
from), measured by running THIS script (from `atom/subtraction-wave`) with `--root` pointed
at a detached-HEAD checkout of that commit — the script did not exist at `7ffbff7` itself:

```bash
git worktree add -f --detach /tmp/base 7ffbff7
python3 scripts/foundry-governance-budget.py --root /tmp/base --json
```

**After** — this branch's own HEAD, `--root` defaulted to the real shipped tree.

| metric | before (7ffbff7) | after | delta |
|---|---:|---:|---:|
| skill prose lines | 9563 | 9560 | -3 |
| shipped scripts | 83 | 81 | -2 |
| floor rows | 72 | 71 | -1 |
| hook commands | 17 | 17 | 0 |
| pytest test count | 1911 | 1910 | -1 |

**What moved, and why the deltas are small net numbers over a five-item deletion wave:**

- **shipped scripts (-2, net):** AC-SUB-1b deleted 4 fleet-session scripts
  (`foundry-fleet-roster.py`, `foundry-fleet-session-registry.py`,
  `foundry-fleet-session-machinery.py`, `foundry-fleet-doctor.py`); AC-SUB-3/-4 added 2
  (`foundry-done-when-backfill.py`, `foundry-governance-budget.py`). `-4 + 2 = -2`.
- **floor rows (-1, net):** the same 4 fleet scripts' rows were removed; 3 new rows were added
  for the 2 new scripts (`--dry-run` allow / `--apply` ask for the backfill script, one allow row
  for the reporter). `-4 + 3 = -1`.
- **pytest test count (-1, net):** AC-SUB-1b deleted `tests/test_fleet_session_machinery.py`
  (dozens of `def test_*`); AC-SUB-1c deleted `tests/test_permission_floor_check.py` (43 `def
  test_*`, the doctor probe's own live seam, with its non-doctor-coupled module coverage already
  duplicated elsewhere per that commit's own message) but relocated two functions
  (`_functional_plugin_root`, the `MERGE_BASE_ENTRIES_DIGEST` self-check) rather than losing them;
  AC-SUB-2 added 7 new tests to `tests/test_command_deck_watch.py`; AC-SUB-3 added
  `tests/test_done_when_backfill.py` (20 tests); AC-SUB-4 added `tests/test_governance_budget.py`
  (12 tests). The large deletions and the new coverage this same wave added net to -1 — this is
  the number, not an estimate of it.
- **skill prose lines (-3, net):** AC-SUB-1a trimmed the retired run-metrics writer's stale
  RETIRED notice from `skills/dispatch/SKILL.md`; AC-SUB-1c updated `skills/doctor/SKILL.md`'s
  probe-count prose (seven-check → six-check, dropping the permission-floor bullet); AC-SUB-1d
  collapsed the escalate-to-operator paragraph in `skills/sd-debug/SKILL.md` and added one
  schema-pointer sentence each to `skills/intake/SKILL.md`, `skills/research-first/SKILL.md` and
  `skills/spec-review/SKILL.md`. Net effect across five edited files is a small trim, not a large
  one — none of AC-SUB-1's targets were large prose blocks to begin with.
- **hook commands (0):** no hook wiring changed in this wave.

**Reproduce this table:** checkout `atom/subtraction-wave`, run the two commands under "Before" /
"After" above. The numbers are read at run time from the tree, never hand-copied — a stale entry
here is a doc-rot bug the next run of this script would immediately reveal.
