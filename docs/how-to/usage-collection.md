# How to collect native usage per operator and outcome

Claude Code exports usage as OpenTelemetry events. Foundry reads those events, counts each provider request once,
joins them to dispatch bindings, and tells you what is missing. This is numeric usage plus correlation metadata
only: prompts, tool inputs/results, API bodies and credentials are never written to the dataset. Export failure is a
coverage finding, never a merge or delivery gate.

## 0. Name the dataset

In `.claude/foundry-project.json` set the directory the records go to (a private path you name):

```json
{ "usage": { "dataset": "/path/to/private/usage-dataset" } }
```

## 1. Configure the user-scope env

Project settings ignore OTEL keys (Claude Code 2.1.282+), so this is per operator, in your USER scope. Copy the
keys from `context/usage-export.env.example` into `~/.claude/settings.json` (`"env": {...}`) or your shell profile,
and set `OTEL_RESOURCE_ATTRIBUTES=foundry.operator=<your op_id>`. Do not enable prompt or tool-detail logging.

## 2. Start the pinned collector

Run the pinned collector-contrib image (the `file` exporter lives in contrib and is alpha) with
`context/otelcol-usage.yaml`. The pin's source of truth is the workspace's standing-versions matrix; never `latest`.
Collector output is newline-delimited OTLP JSON, one `resourceLogs` batch per line; `ingest` reads that shape.

```
docker run --rm -p 127.0.0.1:4318:4318 \
  -v <dataset>/collector:/var/lib/otelcol \
  -v "$PWD/context/otelcol-usage.yaml":/etc/otelcol-contrib/config.yaml:ro \
  ghcr.io/open-telemetry/opentelemetry-collector-releases/opentelemetry-collector-contrib:0.164.0@sha256:340c9ca6a36617084c6661bdc4f0fe92b7cda5a805d61673ec8db375fddc522b
```

The receiver is published on host loopback only; files land in `<dataset>/collector/` (ingest that directory).

## 3. Run one session

Dispatches bind work context automatically: the `SubagentStart` hook (`hooks/foundry-usage-bind.py`) appends a line
to `<dataset>/bindings.jsonl` from `<git-dir>/foundry-assignment.json` (`{"operator": ..., "issue": ...}`) in the
subagent's worktree. The deck can also bind explicitly:

```
python3 scripts/foundry_usage.py bind --session <session_id> [--agent <agent_id>] --operator <op_id> --issue <n> --worktree <path>
```

## 4. Ingest and check coverage

```
python3 scripts/foundry_usage.py ingest <dataset>/collector
python3 scripts/foundry_usage.py attribute [--since 2026-10-28T00:00:00Z --until 2026-11-04T00:00:00Z]
python3 scripts/foundry_usage.py coverage  [--since ... --until ...] [--operator op_a --operator op_b]
```

`coverage` should show at least one attributed record (`attributed.total` above zero). Attribution order: exact
`session_id` + `agent_id` binding, else the session's single bound outcome, else `lead-overhead` (a lead handling
several outcomes), else `unallocated`. It never infers an outcome from a commit or a mutable "current ticket".

`complete` is `false` whenever any `transcript-fallback` record, any `unknown_shape` event (an event missing a
required field; tokens are not inferred), or a configured operator with zero records in the window exists.
`export: not-arriving` lists the user-scope env keys to check; the same answer is in
`<dataset>/export-status.json` for the deck.

## Worked example (the intake metrics-and-token-accounting document, "Worked example")

`tests/fixtures/gn_usage/worked-example/` reproduces it: A (accepted; Alice `60k` before the week, Bob `40k` this
week), B (accepted, `200k`), C (in progress, `500k`), plus `100k` lead overhead. Over the week, total consumption is
`840,000`; A and B lifetime `300,000`, mean `150,000` per accepted outcome. C and overhead stay visible and are not charged
to A or B; Bob's acceptance of A does not move Alice's 60k into his own total.

## Best-effort fallback

`foundry_usage.py fallback-transcripts --cli-version <installed>` imports `~/.claude/projects/**/*.jsonl`, an
internal, version-unstable format: only lines stamped with that CLI version, only numeric fields. Records are marked
`source: transcript-fallback` and coverage stays `complete: false`.

## Attribution timing and source precedence

- Joins are time-guarded: a request is attributed only from bindings whose `at` is at or before the request. A
  request earlier than every binding in its session is `lead-overhead` when the session later binds an outcome,
  else `unallocated`; adding a binding later never changes the rows before it. `bind --at ISO` back-dates one.
- An OTEL record replaces a `transcript-fallback` record with the same request id, never the reverse. Transcript
  `requestId` and OTEL `request_id` are assumed to share the provider request id format; if they differ the same
  request is counted twice, so `coverage` reports `fallback-overlap-unverified` whenever both sources contributed.
- `coverage.complete` is also false (with a named reason) for an empty window, `export-not-arriving`, records with
  no operator, and CLI versions without agent lineage that contributed records: missing evidence is unavailable,
  not zero.
