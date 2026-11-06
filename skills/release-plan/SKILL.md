---
name: release-plan
description: Plan an Epic into a release plan of increments and batches (/foundry:release-plan). Reads the Epic's Stories as live issue nodes from its GitHub Project, finds the dependency closure and path overlap, computes the critical path and total float, writes release-plan.yaml to the planner ref plan/<epic>, and projects increment and batch onto the Project items. Plans only; it never claims and never dispatches. Trigger on "plan the Epic", "/foundry:release-plan", or when the deck re-plans after an issue closes or a dependency changes.
---

# /foundry:release-plan

Plans a **Epic**. It claims nothing and dispatches nothing. Workers pick up batches later
(dl-05 dispatch, dl-11 claims). Vocabulary: `docs/how-to/release-plan.md`.

The work is done by `${CLAUDE_PLUGIN_ROOT}/scripts/foundry_release_plan.py`. This skill only runs it
and reports. Do not re-implement the planning here.

## Verbs

```
foundry_release_plan.py plan    <epic> --project <owner>/<number>
foundry_release_plan.py plan    <epic> --from-json <corpus.json>
foundry_release_plan.py publish <epic> --project <owner>/<number> [--remote origin] [--dry-run]
```

- `plan` prints `release-plan.yaml` on stdout and writes nothing.
- `publish` commits the file to the planner ref `plan/<epic>` (git plumbing; no work-tree
  change; never forced; an unchanged plan makes no commit). Then it sets the Project Number field
  `increment` and the Text field `batch` on each item. It creates the fields when they are absent.
- `--dry-run` prints the plan and writes nothing.

## What it reads

- The Stories of the Epic: LIVE issue nodes from the Project through `gh api graphql`. It never uses search.
- Edges: native issue dependencies (`blocked_by`) and the order of a Story's own sub-issues.
- Paths: the `Components:` line of each issue body's `## Design` section.
- A closed Story satisfies its dependents.
- A `hold` label keeps the Story in the plan and leaves it out of the open increment. The skill reads the
  label and never writes it.

## What it writes

`release-plan.yaml`, checked against `schema/release-plan.schema.json`: `epic`,
`generated_from`, `increments[{n, batches[{id, Stories, paths, total_float}]}]`, `critical_path`, `held`,
`open_increment`, `wip_limit`. The same corpus gives the same bytes. There are no timestamps.

## Refusals

Exit 2 with one `refused:` line. A dependency cycle refuses and names every Story in the cycle.
A plan that fails the schema refuses. No partial plan is ever written.

## Steps

1. Run `publish <epic> --project <owner>/<number> --dry-run`. Read the plan.
2. Run `publish` without `--dry-run`.
3. Report the increments, the critical path, `wip_limit`, and the held Stories.
