# How to plan an epic (`/foundry:release-plan`)

The skill turns the live issues of an epic into a release plan. It plans only. It does not claim
work and it does not dispatch workers.

## Vocabulary

One sentence: a **epic** is an **release plan** of **increments**. Each increment holds **batches** of
**atoms** joined by `blocked_by` edges. The **critical path** bounds the time to finish. Off-path batches
carry **total float**.

| term | meaning | borrowed from |
|---|---|---|
| epic | the whole body of work | PMI: "a group of related projects managed in a coordinated way" |
| atom | one unit of work: a GitHub issue with a runnable Done means | the factory's existing unit noun |
| `blocked_by` | a dependency edge | GitHub's own issue relationship and REST field |
| batch | atoms that land as ONE pull request | the merge-queue noun (bors, Mergify, Aviator, Trunk, Graphite) |
| increment | batches with no edge between them and no path overlap | Argo CD sync increments, AWS MGN increments |
| release plan | the ordered sequence of increments, in `release-plan.yaml` | Terraform's release plan |
| critical path | the longest chain of batches | CPM; Bazel profile; Buck2 `log critical-path` |
| total float | how far a batch can slip without moving the end | GAO and CPM |

## Run it

```
python3 scripts/foundry_release_plan.py plan my-epic --project my-org/3
python3 scripts/foundry_release_plan.py publish my-epic --project my-org/3 --dry-run
python3 scripts/foundry_release_plan.py publish my-epic --project my-org/3
```

For a test, use `--from-json corpus.json`. The corpus lists atoms with `id`, `blocked_by`, `paths`,
`labels`, `state`. No network call is made.

## How it plans

1. It reads the open atoms. A closed atom is done and does not block.
2. It adds `blocked_by` edges from native issue dependencies. The order of an atom's own sub-issues is
   a sequence. The sub-issues of the epic issue itself have no order.
3. It reads each atom's paths from the `Components:` line under `## Design`.
4. The increment planner puts an atom in an increment after every increment of its blockers. It also puts two atoms with
   overlapping paths in different increments. A dependency cycle refuses and names the atoms.
5. Inside each increment, the merge-plan batching groups atoms that share a file or a component into a batch.
6. It finds the critical path. A batch weighs the sum of its atoms: one per atom, or the `size:` label weight
   (see `SIZE_WEIGHT` in the script). Total float is the slack of a batch.
7. `wip_limit` is the number of startable batches: batches with no held atom and no unfinished batch before them
   (an edge, or an earlier batch with overlapping paths). A hold therefore holds back every dependent.

## Hold

Add the `hold` label to an atom to keep it out of the open increment. The planner reads the label and never
changes it. The atom still blocks its dependents, and none of them counts as startable.

## Output

`publish` writes `release-plan.yaml` to the git ref `plan/<epic>`, and sets the Project fields
`increment` (Number) and `batch` (Text) on each item. The file follows `schema/release-plan.schema.json`.
The same corpus gives byte-identical output.

## Prior art

Terraform shows a graph-derived release plan, then walks it with bounded parallelism. CPM gives the
critical path and total float. Bazel and Buck2 report the critical path of a build. Argo CD sync increments order
work in increments. Merge queues test changes together as a batch.
