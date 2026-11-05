# CI budgets are tools, not gates

Operating-model rule C2. Continuous integration checks fast, coarse edges and fails fast. A performance budget or a
request-count budget is a command that a person runs on purpose. It reports; it does not gate.

## Why

A budget measures a noisy number that depends on the runner. Wired as a required check, it blocks merges on that noise,
and people learn to retry and then to disable it. As a report it informs the review and costs nothing when it is wrong.

Never use a path filter to relieve runner pressure. A required check that a path filter skips never reports, and the pull
request waits for it forever. Make the job cheaper, or make it a report.

## What to do

- Keep the required checks few: the build, the tests, the lint, the security scan.
- Put a budget check (Lighthouse CI, a bundle-size limit, a load-test threshold, a request-count limit) in its own job that
  is not a required check, or mark the job `continue-on-error: true`. Let it post its numbers as a comment or a summary.
- Run the heavy budget on a schedule or on demand, not on every push.

## The lint

`scripts/foundry_ci_budget_lint.py` reads workflow files and flags a job that measures a budget and is wired as a required
check. It is read-only.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_ci_budget_lint.py" .github/workflows --required-from required-checks.json
```

- With `--required NAME` (repeatable) or `--required-from FILE`, a budget job is flagged exactly when its check name is in
  that set. The file is a JSON list of names, or a ruleset or branch-protection document.
- With neither, the lint cannot see branch protection. It flags a budget job that blocks by construction, which is one
  without `continue-on-error: true`.

Exit codes: zero when no budget gate is found, one when at least one is found (each named with file, job and the matching
text), two for bad input. The lint matches job and step text against a word list, so a budget it does not recognize by name
passes. It is a visibility tool, and your own review of the workflow is the check that matters.
