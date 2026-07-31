---
id: bullmq-jobs
covers: ["queue-jobs", "worker-patterns", "retry-idempotency", "dead-letter-queue"]
parametrizes_from: []
---

## When to trigger

- Authoring producer-side `queue.add()` calls inside a route handler or service.
- Authoring a queue worker (`Worker`) class to process a queue.
- Designing or modifying retry policy (backoff, max attempts).
- Idempotency-key design for a job type.
- Configuring a dead-letter queue (DLQ) for failed jobs.
- Queue health monitoring (active, waiting, failed counters).

## Procedure

1. **Queue definition** in `src/queues/<name>.ts`: export a singleton `Queue` instance with the queue name as a typed constant. The cache/Redis connection is imported from the shared connection module — never inline a connection string.

2. **Producer call** in the route handler or service:
   ```
   await queue.add(JOB_NAME, data, {
     jobId: deriveIdempotencyKey(data),
     attempts: 3,
     backoff: { type: 'exponential', delay: 1000 },
   });
   ```
   Every `queue.add()` call has a `jobId` (idempotency key). Add a `// dispatched-to: <queue-name>` comment so the consumer is traceable from the producer.

3. **Consumer in `src/workers/<name>.worker.ts`**: export a `Worker` instance wrapping the queue name and a `processor` function. The processor receives a `Job<DataType>` and returns a result or throws on unrecoverable failure.

4. **Retry policy** — defaults: exponential backoff, 3 attempts. `{ attempts: 3, backoff: { type: 'exponential', delay: 1000 } }` means: first retry after 1 s, second after 2 s, third after 4 s. Override per spec when the job has different SLA requirements.

5. **Idempotency key**: derive deterministically from the job payload so that retrying the same logical operation does not produce duplicate side effects. Example: `jobId = \`<job-type>-${entityId}-${dateStr}\`` for a recurring ingestion job. Document the derivation strategy in a comment.

6. **DLQ pattern**: configure `removeOnFail: false` and a `failed` event listener that logs the failure and — for critical queues — publishes to an alerting channel. For spec-defined critical queues, a separate DLQ queue (`<name>-dlq`) receives failed jobs after max attempts via a manual `failed` event handler.

7. **Observability**: every job processor logs at start + end + outcome: `logger.info({ jobId, queueName, step: 'start' })` and `logger.info({ jobId, queueName, step: 'done', result })` (or `logger.error` on failure). Use the project's structured logger.

## Inputs

- The spec's requirements (job semantics, SLA, retry expectations, idempotency requirements).
- Existing queue and worker source for naming and structure conventions.
- The shared cache/Redis connection module.

## Outputs

No artifact. This skill emits prescriptive guidance inline. The implementing agent writes queue, worker, and producer files in the assigned worktree.

## Quality bar

- [ ] Every job has an idempotency-key strategy documented in a comment at the `queue.add()` call site.
- [ ] Every consumer has a max-retry policy (`attempts` ≥ 1, `backoff` configured).
- [ ] Every queue has a corresponding worker — no orphan queues that never get processed.
- [ ] The cache/Redis connection is imported from the shared connection module — never an inline connection string.
- [ ] Every job processor logs start + end + outcome via the structured logger.
- [ ] DLQ or failure-alerting configured for spec-designated critical queues.

## Common Rationalizations

| Rationalization | Why it's wrong | What to do instead |
|---|---|---|
| "The job data is unique enough; a separate idempotency key is overkill." | Without an explicit `jobId`, the queue generates a random ID — retries after a network blip enqueue duplicate jobs, producing duplicate emails, payments, or DB writes. | Derive a deterministic `jobId` from the job's semantic identity and pass it as `jobId` on every `queue.add()` call. |
| "I'll add a job name later; just use the queue name for now." | Unnamed jobs collapse all telemetry and monitoring into a single bucket, making it impossible to distinguish one job type's failures from another's in dashboards and alerts. | Declare a typed `JOB_NAME` constant at queue definition time and pass it as the first argument to `queue.add()` — even for queues with a single job type. |
| "Exponential backoff is unnecessary for fast jobs; I'll just retry immediately." | Immediate retries (no backoff) hammer a downstream service that is already overloaded — turning a transient fault into a cascading failure that outlasts the root cause. | Use `backoff: { type: 'exponential', delay: 1000 }` as the default; tune delay and `attempts` per the spec's SLA rather than removing backoff entirely. |
| "The DLQ is nice to have; I'll add it after launch." | Without a DLQ or a `failed` event listener, exhausted-retry jobs silently accumulate in the failed set — the first sign of a problem is often a downstream complaint, not an alert. | Configure `removeOnFail: false` and a `failed` event handler that logs + alerts for every queue at authoring time; add the DLQ sink in the same PR as the worker. |

## Skills this one composes with

- `fastify-backend-patterns` — producer-side dispatch (`queue.add()`) lives inside route handlers; the two skills are sequenced: fastify-backend-patterns for the route, bullmq-jobs for the dispatch call within the route.
- `vitest-unit` — worker processor logic is unit-tested in isolation via `vi.mock` on the `Worker` class.
- `api-integration-test` — integration-test authoring for queue-dispatch side effects requiring a live queue stack (e.g., `queue.add` spy + queue-down resilience patterns).

## Anti-patterns

- Never enqueue without an idempotency key (`jobId`). Retries without idempotency produce duplicate side effects (duplicate emails, duplicate DB writes, duplicate payments).
- Never default retries to infinite (`attempts: 0` means infinite retries). Set an explicit max and a DLQ or alerting path for exhausted retries.
- Never share a queue between services where one is producer-only and the other is producer+consumer, without an explicit spec line documenting the dual-role pattern.
- Never inline a connection string in a queue or worker file. Import from the shared connection module so connection management is centralized.
- Never skip observability (logging) on a job processor. Silent job failures are invisible until a downstream system complains.
- Never add a new queue without a corresponding worker file in the same PR. An orphan queue silently accumulates jobs with no processor.
