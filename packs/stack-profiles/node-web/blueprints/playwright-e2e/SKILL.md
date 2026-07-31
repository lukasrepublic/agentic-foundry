---
id: playwright-e2e
covers: ["e2e-testing", "browser-flow-testing", "network-mocking"]
parametrizes_from: ["test_recipe.e2e"]
---

## When to trigger

- Authoring an e2e test for a user-facing flow cited in a spec's acceptance criteria.
- Selector-strategy decisions (testid vs. role vs. label vs. text).
- Network-mocking decisions: mocking at the route level for deterministic test data.
- CI workflow changes for parallel e2e execution.
- Flaky-test diagnosis.
- Adding shared fixtures or test helpers for an e2e suite.

## Procedure

1. **Place the test under the target package's `e2e/<surface>/<feature>.spec.ts`** — `<surface>` is the web surface under test. Read the existing `e2e/` directory for naming and structure conventions before adding new files.

2. **Selector strategy** (in priority order):
   a. `page.getByTestId('<data-testid>')` — preferred; stable, intention-declaring.
   b. `page.getByRole('<role>', { name: '<accessible-name>' })` — second choice; semantically meaningful.
   c. `page.getByLabel('<label-text>')` — for form inputs with associated labels.
   d. CSS-class selectors — banned. Classes change with styling refactors; tests must survive style changes.

3. **Network mocking via `page.route`**: mock API responses at the network boundary — not at the function boundary. Use `page.route('**/api/<resource>/**', handler)` at the fixture or test level. The handler defines the response body; actual network traffic is intercepted. This keeps tests deterministic without a running backend.

4. **Fixture composition** via `test.extend`:
   - Create shared fixtures for setup that multiple tests need (e.g., authenticated state, seeded data).
   - Never share mutable state between tests. Each test starts from a known fixture state.
   - Use `test.use({ storageState: 'auth.json' })` for auth-state reuse across a suite.

5. **Parallelism**: tests are parallel-safe by default. If a test mutates global state (e.g., writes to a shared DB that affects other tests), isolate it with `test.describe.serial`. Mark the reason with a comment.

6. **Run command**: `{{ profile.test_recipe.e2e }}` from the project root. For a targeted run, scope to a surface: append `e2e/<surface>/` to the run command.

## Inputs

- The spec's acceptance criteria — every e2e test maps to at least one AC.
- Existing `e2e/**` for fixture and naming conventions.
- The running app under test — started in CI via the project's npm scripts.

## Outputs

No artifact. This skill emits prescriptive guidance inline. The implementing agent writes spec files in the assigned worktree.

## Quality bar

- [ ] Every test asserts at least one acceptance criterion (cite it in a comment: `// AC-N: <text>`).
- [ ] Every selector is `getByTestId` or `getByRole` — no CSS-class selectors (`locator('.class-name')` is banned).
- [ ] Every network mock cites the api route it intercepts in a comment.
- [ ] No `test.skip` on a failing or flaky test — raise to the operator instead.
- [ ] CI parallelism enabled (`workers` > 1 in `playwright.config.ts`) unless a spec explicitly requires serial execution.
- [ ] Fixtures clean up after themselves — no leaked state between tests in parallel mode.

## Common Rationalizations

| Rationalization | Why it's wrong | What to do instead |
|---|---|---|
| "The test is flaky because the animation takes variable time; I'll just add `waitForTimeout(3000)`." | Hard-coded sleeps make tests slow in the happy path and still flaky when the animation exceeds the timeout under load. They also mask the root cause (an element that never becomes stable). | Replace `waitForTimeout` with `waitForSelector` + a visibility or enabled state assertion; if the animation is the problem, expose a `data-animation-state="done"` attribute the test can await. |
| "Network mocking is complex; I'll test against the real API — it's only a staging environment." | Tests against real APIs have non-deterministic timing, can mutate shared staging data, and fail when the staging environment is down. These failures are environment failures, not test failures, and erode trust in the test suite. | Use `page.route('**/api/...')` to intercept and mock API calls for happy-path tests; reserve staging integration tests for explicit smoke-test suites that run on a separate schedule. |
| "I'll rely on CSS class selectors; someone will update them if the class names change." | CSS class selectors couple tests to implementation details (class naming, CSS module hashing). When a dev refactors a component's class names without changing behavior, every test using those selectors breaks. | Use `data-testid` attributes or ARIA roles (`getByRole`, `getByLabel`) as selectors; these are stable across visual refactors and communicate semantic intent. |
| "I'll skip trace-on-failure for now; I can reproduce the flaky test locally." | Without traces, a flaky test in CI that cannot be reproduced locally produces no actionable artifact. The next CI run may not repro either — the flake becomes invisible until it blocks a deploy. | Enable `trace: 'on-first-retry'` in `playwright.config.ts` so every retry in CI captures a full trace; post the trace URL or archive in the CI artifact for async debugging. |

## Skills this one composes with

- `vitest-unit` — complementary; playwright-e2e covers integration/flow tests, vitest-unit covers component-level invariants. Both are used together on a fully-tested feature.
- `nextjs-ssr-patterns` — when e2e tests cover web flows that exercise SSR routes.
- `vite-spa-patterns` — when e2e tests cover SPA flows (auth-gated; fixtures must handle auth setup via `storageState`).

## Anti-patterns

- Never `test.skip` a flaky test. Flaky tests are bugs. Raise to the operator with the failure detail; do not suppress.
- Never use `getByText` for stable identifiers — text changes with copy edits; `getByTestId` is the stable alternative.
- Never mock at the function boundary (e.g., `vi.mock` inside a Playwright test file). Playwright tests run against the real browser process; function-level mocking does not work across process boundaries. Mock at the network (`page.route`).
- Never share mutable state across tests — concurrent test workers will corrupt each other's state.
- Never assert on auto-generated IDs (e.g., numeric DB IDs, UUIDs) without `.toMatch(/<regex>/)`. Hard-coded ID values break across test environments.
- Never write a Playwright test for something that should be a vitest unit test (pure function, component rendering). Keep the test pyramid: many unit, fewer integration, fewest e2e.
