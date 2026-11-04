# data-pipeline conventions

A batch/orchestrated data pipeline (Python via uv; an orchestrator plus dataframe/SQL engines). Dependencies point inward only.

## Layers

- **domain** -- dataset schemas, data contracts, business rules and the pure functions that define them. No I/O, no engine or orchestrator imports. Imports nothing from the other layers.
- **transform** -- pure transformations over frames/tables (select, join, aggregate). Deterministic: same input, same output. May import `domain`.
- **io** -- readers and writers for sources and sinks, partition handling, schema enforcement on read and write. May import `domain`.
- **orchestration** -- the DAG/asset definitions, schedules, retries and alerts that wire io and transform together. May import `io`, `transform` and `domain`.

Allowed edges, in prose: orchestration -> io, transform, domain; io -> domain; transform -> domain; domain -> nothing. `transform` and `io` never import each other.

## Conventions that matter for this type

- **Idempotent tasks.** A task re-run for the same partition produces the same result and leaves no duplicates (write-by-partition overwrite or merge on a natural key).
- **Partitioned IO.** Every dataset has an explicit partition key (usually the logical date); reads and writes address a partition, which makes backfills and replays cheap and safe.
- **Lineage and classification.** Each dataset declares its upstreams, owner and data classification (including PII); the orchestrator exposes this lineage and failures page the owner.
- Data-quality expectations (schema, nulls, uniqueness, freshness, volume) run on every load and fail the run when breached.

Tooling is `uv`-driven and locked; pins live in `standing-versions/manifest.yaml`.
