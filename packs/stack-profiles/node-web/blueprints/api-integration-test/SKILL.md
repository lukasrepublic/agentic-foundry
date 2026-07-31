---
id: api-integration-test
covers: ["integration-testing", "api-contract-testing", "side-effect-assertions"]
parametrizes_from: ["test_recipe.integration"]
---

## When to trigger

- The implementing agent authors integration tests for a spec whose ACs require database-state assertions, queue-dispatch verification, or audit-event rows.
- Test files live under the api package's integration-test directory (e.g. `test/*.integration.test.ts`).
- A spec's acceptance criteria reference: cookie Max-Age bounds, Retry-After headers, non-enumeration assertions, audit-event table rows, or queue `add` side effects.
- The operator says "author integration tests", "write API integration tests", or "add integration coverage for <feature>".

## Procedure

1. **Confirm the live stack is reachable.** Boot the project's local development stack and verify the API health endpoint responds before writing any test. Integration tests require the real backing services (database, cache, queue) running — they FAIL against mocked services. The boundary rule: if a test could run without a real database, it is a unit test; route to `vitest-unit` instead.

2. **Use `app.inject()` as the default invocation form.** Fastify's `app.inject({ method, url, headers, payload })` dispatches requests through the full route-handler stack without opening a real socket. This is faster than `supertest` + an HTTP server and avoids port-collision issues in parallel test runs. Reserve real-socket (`app.listen()` + `fetch()`) only when the test exercises HTTP/2 or WebSocket behavior that `inject()` cannot simulate.

3. **Establish per-test isolation.** Choose one of two strategies:
   - **Transaction rollback (preferred for speed)**: wrap each test in a database transaction via `BEGIN`; roll back in `afterEach`. Works when the code under test does NOT commit in a nested transaction.
   - **Table truncation (required for tests that commit)**: in `afterEach`, truncate all affected tables in reverse-FK order. Slower but universally safe.
   Additionally, namespace test-generated data to prevent bleed between parallel runs: prefix email addresses with the test-file slug, prefix IP addresses with a test-run ID, and prefix queue job IDs where applicable.

4. **Mock only external edges.** The database, cache, and queue are REAL (from the local stack). Mock only:
   - OAuth/OIDC provider endpoints (mock at the HTTP layer with `nock` or `msw`).
   - SMTP (point the SMTP config at the local mail-catcher; no mock needed unless asserting on email content is out of scope).
   - External payment/third-party APIs that are not part of the local stack.

5. **Assert the 5 canonical AC patterns.** These patterns cover the most common integration-level ACs:
   - **Cookie Max-Age bound**: `expect(setCookieHeader.match(/Max-Age=(\d+)/)?.[1]).toBeLessThanOrEqual(expectedSeconds + 1)` (±1s tolerance for clock skew between test process and server process).
   - **Retry-After header**: `expect(parseInt(res.headers['retry-after'], 10)).toBeGreaterThanOrEqual(1)` after a rate-limit-triggering sequence.
   - **Non-enumeration** (same response for unknown vs. invalid credential): assert `res1.statusCode === res2.statusCode && res1.json().message === res2.json().message` — do NOT assert on internal differentiation.
   - **Audit-event row**: query the database directly after the request and assert the expected row exists (`SELECT * FROM <audit-table> WHERE …` → `expect(row.rowCount).toBe(1)`).
   - **Queue add spy + queue-down resilience**: use `vi.spyOn(queue, 'add')` to assert dispatch; then simulate queue unavailability by stopping the queue connection and assert the HTTP response still returns the expected status code (resilience: queue failure must not fail the HTTP request).

6. **Author describe/it structure per AC.** One `describe` block per AC group; `it` names quote the AC assertion verbatim (or paraphrase closely). Example: `describe('AC-AUTH-4: rate-limit', () => { it('returns 429 after 5 failed attempts within 60s', ...) })`. This naming makes CI output directly readable against the spec.

7. **Run command**: `{{ profile.test_recipe.integration }}` from the project root for the target package. Confirm the integration-test script is configured to match the integration-test file glob.

## Inputs

- The spec's acceptance criteria — integration tests mirror the ACs exactly; AC wording drives the `it(...)` description.
- A running local development stack (database, cache, queue, mail-catcher).
- The api package's integration-test directory — read existing integration tests before authoring new ones to match fixture patterns, DB seeding helpers, and app bootstrap setup.
- The api package's routes and service layers — understand the code path before asserting on its side effects.

## Outputs

No artifact. This skill emits prescriptive guidance inline. The implementing agent writes test files under the api package's integration-test directory.

## Quality bar

- [ ] Every integration test runs against a live local stack — no mock substitutions for the database, cache, or queue.
- [ ] Per-test isolation: either transaction rollback in `afterEach` OR table truncation in `afterEach`. No test depends on state left by a prior test.
- [ ] Test-generated emails, IPs, and job IDs are namespaced to the test-file slug + a UUID to prevent parallel-run bleed.
- [ ] `app.inject()` used as default invocation; real-socket form justified inline when used.
- [ ] Each integration test's `it(...)` description is traceable to an acceptance criterion.
- [ ] The 5 canonical AC patterns (Cookie Max-Age, Retry-After, non-enumeration, audit-event row, queue add spy + queue-down) are applied where the corresponding AC type appears.
- [ ] The integration-test run passes before commit.
- [ ] No test file imports another `*.integration.test.ts` — each integration test is self-contained.

## Common Rationalizations

| Rationalization | Why it's wrong | What to do instead |
|---|---|---|
| "I'll mock the database with an in-memory SQLite substitute — it's faster." | SQLite and a production-grade database have different behavior for constraints, json/jsonb columns, LISTEN/NOTIFY, advisory locks, and row-level security. A green test against SQLite can mask a red result against the real engine. The local-stack database is the source of truth for the contract. | Boot the local stack and run against the real database. The overhead is a one-time stack start, not per-test. Per-test overhead is milliseconds via `app.inject()`. |
| "Integration tests are slow; I'll run them only in CI." | Integration tests run in under a few seconds per test using `app.inject()` against the local stack. "Slow" is a myth carried over from environments that boot a new server per test. Running locally catches isolation failures (leaked state, sequence-dependent tests) that CI parallel shards can miss. | Run the integration-test command locally before every PR push. If a test takes >5s, profile the DB query — it is a query performance defect, not a reason to skip local runs. |
| "The audit-event row assertion is fragile; I'll assert on the HTTP response instead." | HTTP response shape verifies the API contract; it does NOT verify that the side effect (audit-event write) occurred. An implementation that returns 200 but silently drops audit events passes the HTTP assertion and ships a compliance hole. | Assert on the DB row directly after the request. The assertion is not fragile — it is load-bearing for compliance. If the schema changes, update the assertion; that is the point. |
| "Per-test isolation via transaction rollback is good enough; I don't need namespaced emails." | Transaction rollback cleans up DB rows, but it does NOT prevent rate-limiter state (stored in the cache, not rolled back) from bleeding across tests. A test that triggers a rate limit leaves cache counters that affect subsequent tests in the same run. | Use both: transaction rollback for the DB + namespaced email/IP for cache-keyed state. The namespace is a UUID suffix — two lines of setup, prevents an entire class of flaky failures. |
| "I'll use `supertest` instead of `app.inject()` — it's more realistic." | `supertest` opens a real TCP socket, which requires a free port, is ~10× slower than `inject()`, and creates server-lifecycle complexity (must explicitly close the server after each test or CI leaks the port). `app.inject()` exercises the identical Fastify handler stack without the socket overhead. Realistic enough. | Use `app.inject()`. The only valid exceptions are tests for HTTP/2-specific behavior or WebSocket upgrade flows that `inject()` cannot simulate. Justify inline when used. |

## Skills this one composes with

- `fastify-backend-patterns` — route handler patterns and Fastify plugin lifecycle; read before authoring tests that touch app bootstrap or plugin registration.
- `vitest-unit` — sibling skill for unit-level test authoring (mocks, timers, component-level invariants); routes here when the AC does NOT require a live stack.
- `bullmq-jobs` — queue job shape, worker contract, and queue-down resilience patterns; read when authoring queue add spy + queue-down resilience assertions (pattern 5).
- `playwright-e2e` — e2e layer above integration tests; integration tests own the API contract layer; Playwright owns the browser behavior layer. Do not duplicate assertions across layers.

## Anti-patterns

- **Do NOT mock the database, cache, or queue.** These are real services from the local stack; mocking them defeats the purpose of an integration test and hides behavioral gaps between mock and real.
- **Do NOT rely on test ordering.** Each integration test must be fully self-contained and runnable in isolation. If a test depends on state produced by a prior test, the suite is brittle — add fixture setup in `beforeEach`.
- **Do NOT use `supertest` + `app.listen()` as default.** `app.inject()` is faster, port-collision-free, and exercises the identical handler stack. Use real-socket form only for HTTP/2 or WebSocket tests, with justification inline.
- **Do NOT assert on HTTP response alone when the AC requires a side effect.** Side effects (audit-event rows, queue dispatches, cache counters) require direct assertions against the backing store — HTTP response shape does not prove the side effect occurred.
- **Do NOT skip namespacing of test-generated data.** Non-namespaced emails and IPs bleed into cache-backed rate-limiter state and cause flaky failures under parallel test runs. Always namespace with a UUID suffix.
