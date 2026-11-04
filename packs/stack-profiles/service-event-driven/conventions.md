# service-event-driven conventions

An event-driven Python service: it consumes events from a broker, applies domain rules, persists state and publishes events. Dependencies point inward only.

## Layers

- **domain** -- pure business rules and event/value types. No I/O, no broker or database client, no clock or randomness except through injected ports. Imports nothing from the other layers.
- **data** -- persistence adapters (repositories, outbox table access). May import `domain` only.
- **messaging** -- consumers, producers, serialisation, retry and dead-letter handling. May import `data` and `domain`. Handlers translate a decoded event into a domain call; they hold no business rules.
- **api** -- the thin HTTP surface (health, readiness, admin/replay endpoints). May import `messaging`, `data` and `domain`.

Allowed edges, in prose: api -> messaging, data, domain; messaging -> data, domain; data -> domain; domain -> nothing. Anything else is an architecture-kit violation.

## Conventions that matter for this type

- **Idempotent consumers.** Every handler tolerates redelivery and reordering: derive a dedupe key from the event id, record it in the same transaction as the state change, and commit offsets only after that transaction succeeds.
- **Schema registry / versioned schemas.** Event schemas live in `schemas/` (AsyncAPI or a registry subject), are versioned, and every change passes a compatibility check in CI before a producer may emit it.
- **Transactional outbox.** State change and the event it implies are written in one transaction to an outbox table; a relay publishes from the outbox. Never publish directly inside a business transaction.
- Poison messages go to a dead-letter topic with the failure reason; a documented replay procedure exists and is exercised.

Tooling is `uv`-driven and locked; pins live in `standing-versions/manifest.yaml`. Config and secrets come from the environment.
