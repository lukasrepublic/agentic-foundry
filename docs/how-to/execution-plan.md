# How to plan a programme (`/foundry:execution-plan`)

The skill turns the live issues of a programme into an execution plan. It plans only. It does not claim
work and it does not dispatch workers.

## Vocabulary

One sentence: a **programme** is an **execution plan** of **waves**. Each wave holds **batches** of
**atoms** joined by `blocked_by` edges. The **critical path** bounds the time to finish. Off-path batches
carry **total float**.

| term | meaning | borrowed from |
|---|---|---|
| programme | the whole body of work | PMI: "a group of related projects managed in a coordinated way" |
| atom | one unit of work: a GitHub issue with a runnable Done means | the factory's existing unit noun |
| `blocked_by` | a dependency edge | GitHub's own issue relationship and REST field |
| batch | atoms that land as ONE pull request | the merge-queue noun (bors, Mergify, Aviator, Trunk, Graphite) |
| wave | batches with no edge between them and no path overlap | Argo CD sync waves, AWS MGN waves |
| execution plan | the ordered sequence of waves, in `execution-plan.yaml` | Terraform's execution plan |
| critical path | the longest chain of batches | CPM; Bazel profile; Buck2 `log critical-path` |
| total float | how far a batch can slip without moving the end | GAO and CPM |

## Run it

```
python3 scripts/foundry_execution_plan.py plan my-programme --project my-org/3
python3 scripts/foundry_execution_plan.py publish my-programme --project my-org/3 --dry-run
python3 scripts/foundry_execution_plan.py publish my-programme --project my-org/3
```

For a test, use `--from-json corpus.json`. The corpus lists atoms with `id`, `blocked_by`, `paths`,
`labels`, `state`. No network call is made.

## How it plans

1. It reads the open atoms. A closed atom is done and does not block.
2. It adds `blocked_by` edges from native issue dependencies. The order of an atom's own sub-issues is
   a sequence. The sub-issues of the programme issue itself have no order.
3. It reads each atom's paths from the `Components:` line under `## Design`.
4. The wave planner puts an atom in a wave after every wave of its blockers. It also puts two atoms with
   overlapping paths in different waves. A dependency cycle refuses and names the atoms.
5. Inside each wave, the merge-plan batching groups atoms that share a file or a component into a batch.
6. It finds the critical path. A batch weighs the sum of its atoms: one per atom, or the `size:` label weight
   (see `SIZE_WEIGHT` in the script). Total float is the slack of a batch.
7. `max_concurrent` is the number of startable batches: batches with no held atom and no unfinished batch before them
   (an edge, or an earlier batch with overlapping paths). A hold therefore holds back every dependent.

## Hold

Add the `hold` label to an atom to keep it out of the open wave. The planner reads the label and never
changes it. The atom still blocks its dependents, and none of them counts as startable.

## Output

`publish` writes `execution-plan.yaml` to the git ref `plan/<programme>`, and sets the Project fields
`wave` (Number) and `batch` (Text) on each item. The file follows `schema/execution-plan.schema.json`.
The same corpus gives byte-identical output.

## Prior art

Terraform shows a graph-derived execution plan, then walks it with bounded parallelism. CPM gives the
critical path and total float. Bazel and Buck2 report the critical path of a build. Argo CD sync waves order
work in waves. Merge queues test changes together as a batch.
