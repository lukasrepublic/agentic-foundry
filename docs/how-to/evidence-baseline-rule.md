# The evidence and baseline rule

Operating-model rule G1. No run writes the evidence store, and no figure becomes a baseline, until one run is green end to
end.

## Why

A baseline taken from a half-working run records the breakage as normal. Every later run is then compared with a number
that was never true, and a real regression hides behind it. An evidence store that holds output from a run that did not
finish proves nothing, and later readers cannot tell.

## What "green end to end" means

All of these hold for the run:

- Every step ran. A skipped step, a step that could not start and a step that timed out each count as not green. A skipped
  check is not a pass.
- The run exited zero, apart from the findings you mean to freeze.
- The tools the run used were present at the pinned versions. A check that did not run is reported as not run.
- You read the output, not only the exit code.

Until you have such a run, report the numbers as a first measurement and write nothing to the evidence store.

## Freezing a baseline

When the first green run still shows violations that exist today, freeze them per check so that only new ones fail:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_governance.py" baseline --check <name> --findings <file>
```

`<file>` is a JSON list (or `-` for standard input); each item is a string or an object, one for each finding. The verb writes `governance/local/baselines/<name>.json`, sorted and de-duplicated. Review the file in the pull request like
code. A baseline is a decision, so record who took it and from which run.

## What is enforced

This is a practice. `foundry_governance.py baseline` writes whatever findings file you give it; it cannot tell whether the
run that produced the file was green. The rule is checked by the person who freezes the baseline and by the reviewer. The
exceptions kit (`governance/kits/exceptions/`) holds the related rules for baselines and expiry, and
[Foundry Governance](../governance.md) shows how exceptions are reported.
