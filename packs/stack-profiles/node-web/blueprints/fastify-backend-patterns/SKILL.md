---
id: fastify-backend-patterns
covers: ["fastify-routes", "schema-validation", "fastify-plugins", "db-migrations", "queue-dispatch"]
parametrizes_from: ["static_validation.typecheck"]
---

## When to trigger

- Implementing a spec that names api work in the backend package.
- Authoring a new route handler (GET/POST/PUT/PATCH/DELETE) for a resource.
- Adding or modifying schema validation (request body, query params, response shape).
- Writing a new Fastify plugin (encapsulating a service, middleware, or feature module).
- Authoring a DB migration in the api migrations directory.
- Wiring a queue producer-side dispatch from a route handler.

## Procedure

1. **Place the route file** under `src/routes/<resource>/<verb>.ts` per the package convention. Register the route via a Fastify plugin encapsulated in `src/routes/<resource>/index.ts` (or per the existing folder structure — read `src/routes/` before adding new files).

2. **Declare request and response schemas** using Fastify's schema-validator integration (Zod or TypeBox per the project convention). Attach schemas to the route options object: `{ schema: { body: BodySchema, response: { 200: SuccessSchema, 4xx: ErrorSchema } } }`. No unvalidated routes.

3. **Register route via Fastify plugin**: wrap route registrations in `fastify.register(async function(instance) { instance.get(...) })`. Plugin scope ensures encapsulation of prefix, decorators, and hooks.

4. **Error handler**: return a structured `{ error: string, code: string }` (or the project's error shape) rather than throwing untyped errors. Use `fastify.setErrorHandler` at the plugin level for domain-specific error mappings. Never throw a raw JS `Error` from a route handler — map it to an HTTP status code with a structured body.

5. **DB migration**: place the migration file in `migrations/<timestamp>-<slug>.sql` (or the migration-tool format the project uses). Migrations are append-only and reversible — include a down migration or add a `-- no-op down: <reason>` comment. Never edit a landed migration.

6. **Queue producer dispatch**: follow the `bullmq-jobs` skill's producer pattern. Add a `// dispatched-to: <queue-name>` comment at each `queue.add()` call site so the consumer is traceable from the producer.

7. **Static validation + tests**: run typecheck with `{{ profile.static_validation.typecheck }}` from the project root, scoped to the target package, plus lint, unit, and integration tests before marking the PR ready.

### Dependency installation

**Default command for population on a fresh worktree or after a `git pull` that includes a lockfile change**: `npm ci` — lockfile-respecting, deterministic install. Run from the project root. This is the form CI workflows and Dockerfiles use. The bare `npm install` form is acceptable only when the lockfile is known-stale or for the initial devcontainer bootstrap; for the per-PR verifier flow, `npm ci` is the default.

**Authoring-time exception**: `npm install <pkg>` (which mutates the manifest and lockfile) is permitted ONLY when the spec explicitly authorizes the package by name. Drift between the authorized package and the installed package is a defect — verify the spec names the exact package before running `npm install <pkg>`. After it mutates the lockfile, subsequent verifier reruns in the same worktree use `npm ci` against the updated lockfile. Outside that explicit authorization, `npm ci` is the only permitted form.

## Inputs

- The spec's requirements and API contract.
- Existing backend-package source for convention-matching (route structure, plugin registration pattern, error shape).
- OpenAPI / contract docs when present in the spec or the package's docs.

## Outputs

No artifact. This skill emits prescriptive guidance inline. The implementing agent writes route, schema, plugin, and migration files in the assigned worktree.

## Quality bar

- [ ] Every route has request and response schemas attached; no unvalidated input accepted.
- [ ] Every route returns structured errors `{ error, code }` — no untyped `throw new Error(...)` reaching the client.
- [ ] Every DB migration is reversible (down migration present or `-- no-op down:` comment explaining why).
- [ ] Every queue producer call cites the consumer with a `// dispatched-to: <queue>` comment.
- [ ] Secrets (DB URI, cache URL, third-party API keys) pulled from `process.env` at startup — not per-request, not inline.
- [ ] Rate-limiting applied at the router level for write-heavy or public-facing endpoints.
- [ ] Lint + typecheck + tests pass before the PR is marked ready for review.

## Common Rationalizations

| Rationalization | Why it's wrong | What to do instead |
|---|---|---|
| "This endpoint only accepts internal traffic; schema validation is overkill." | Internal callers also make mistakes, and "internal only" designations erode over time. Unvalidated input that reaches a DB query or downstream service becomes an injection vector regardless of caller origin. | Attach request and response schemas to every route — internal and external alike. The cost is one schema; the benefit is type-safety, OpenAPI compatibility, and rejection of malformed payloads at the framework layer. |
| "I'll put the schema inline in the handler function — it's easier to read in context." | Inline schemas bypass Fastify's schema-compiler caching and are re-compiled on every request, degrading performance. They also make it impossible to share the schema across routes (e.g., a shared response shape). | Define schemas as named constants in a `schemas/<resource>.ts` file and import them into the route options object. |
| "The route returns a 200 with `{ success: true }` — I don't need a structured error body for a success path." | Omitting the error-response schema means Fastify skips serialization validation on non-2xx paths, allowing untyped errors to leak stack traces or internal field names to callers. | Declare both the success response schema AND at least one error response schema (`4xx` or specific codes) on every route. |
| "It's a simple single-field add; I don't need a down migration." | Migrations without reversals block rollbacks during incidents — a failed deploy cannot be reverted if the DB schema is in a forward-only state. | Always include a down migration or a `-- no-op down: <reason>` comment explaining why reversal is intentionally omitted (e.g., a data-destructive column drop would lose prod data). |

## Skills this one composes with

- `bullmq-jobs` — job-dispatch patterns for producer-side `queue.add()` calls inside route handlers.
- `vitest-unit` — unit-test authoring for route handlers, services, and utility functions.
- `api-integration-test` — integration-test authoring for routes requiring live-stack assertions (database, cache, queue).
- `playwright-e2e` — when the spec's e2e flow crosses api boundaries (mocked via `page.route`).

## Anti-patterns

- Never edit a landed migration. Migrations are append-only; fix a mistake by adding a corrective forward migration.
- Never throw an untyped error from a route handler. All errors map to structured HTTP responses with a `code` field.
- Never hard-code secrets or DB URIs in source code. All secrets are env vars read at startup.
- Never mix Fastify v3 and v4 plugin signatures. The spec must declare the Fastify version; the implementing agent matches that version.
- Never bypass schema validation with `// @ts-ignore` or by omitting the `schema` property on a route. Unvalidated routes are a security risk.
- Never share a queue connection instance across multiple Fastify plugins without explicit scoping — follow the connection pattern in the existing queue files.
