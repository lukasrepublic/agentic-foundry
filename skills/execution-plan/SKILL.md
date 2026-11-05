---
name: execution-plan
description: Plan a programme into an execution plan of waves and batches (/foundry:execution-plan). Reads the programme's atoms as live issue nodes from its GitHub Project, finds the dependency closure and path overlap, computes the critical path and total float, writes execution-plan.yaml to the planner ref plan/<programme>, and projects wave and batch onto the Project items. Plans only; it never claims and never dispatches. Trigger on "plan the programme", "/foundry:execution-plan", or when the deck re-plans after an issue closes or a dependency changes.
---

# /foundry:execution-plan

Plans a **programme**. It claims nothing and dispatches nothing. Workers pick up batches later
(dl-05 dispatch, dl-11 claims). Vocabulary: `docs/how-to/execution-plan.md`.

The work is done by `${CLAUDE_PLUGIN_ROOT}/scripts/foundry_execution_plan.py`. This skill only runs it
and reports. Do not re-implement the planning here.

## Verbs

```
foundry_execution_plan.py plan    <programme> --project <owner>/<number>
foundry_execution_plan.py plan    <programme> --from-json <corpus.json>
foundry_execution_plan.py publish <programme> --project <owner>/<number> [--remote origin] [--dry-run]
```

- `plan` prints `execution-plan.yaml` on stdout and writes nothing.
- `publish` commits the file to the planner ref `plan/<programme>` (git plumbing; no work-tree
  change; never forced; an unchanged plan makes no commit). Then it sets the Project Number field
  `wave` and the Text field `batch` on each item. It creates the fields when they are absent.
- `--dry-run` prints the plan and writes nothing.

## What it reads

- The atoms of the programme: LIVE issue nodes from the Project through `gh api graphql`. It never uses search.
- Edges: native issue dependencies (`blocked_by`) and the order of an atom's own sub-issues.
- Paths: the `Components:` line of each issue body's `## Design` section.
- A closed atom satisfies its dependents.
- A `hold` label keeps the atom in the plan and leaves it out of the open wave. The skill reads the
  label and never writes it.

## What it writes

`execution-plan.yaml`, checked against `schema/execution-plan.schema.json`: `programme`,
`generated_from`, `waves[{n, batches[{id, atoms, paths, total_float}]}]`, `critical_path`, `held`,
`open_wave`, `max_concurrent`. The same corpus gives the same bytes. There are no timestamps.

## Refusals

Exit 2 with one `refused:` line. A dependency cycle refuses and names every atom in the cycle.
A plan that fails the schema refuses. No partial plan is ever written.

## Steps

1. Run `publish <programme> --project <owner>/<number> --dry-run`. Read the plan.
2. Run `publish` without `--dry-run`.
3. Report the waves, the critical path, `max_concurrent`, and the held atoms.
