---
id: vitest-unit
covers: ["unit-testing"]
parametrizes_from: ["test_recipe.unit", "test_recipe.coverage_gate"]
---

## When to trigger

- Authoring a unit test for a function, React component, hook, or async handler.
- Coverage-gate work: ensuring the target package meets its coverage threshold.
- Snapshot review: deciding whether to update or reject a snapshot diff.
- Async-pattern test work: testing promises, async/await, timeouts, or event-driven code.
- Mock-strategy decisions: which layer to mock (module boundary vs. network boundary).

## Procedure

1. **Place test file colocated with implementation**: `<file>.test.ts` (non-React) or `<file>.test.tsx` (React components) in the same directory as the source file. This makes it obvious when a test is missing and prevents "lost test" drift.

2. **Test pyramid discipline**: write many unit tests, fewer integration tests, fewest e2e tests. A unit test covers one function or component in isolation. If a test requires multiple real modules to be wired together, consider whether it should be an integration test.

3. **Coverage threshold** for the target package is `{{ profile.test_recipe.coverage_gate }}`. Do not invent a threshold; use the profile's gate. Verify with the project's coverage run that the PR does not lower the threshold below the defined minimum.

4. **Snapshots** only for stable serialization (config objects, API response shapes, serialized schemas). Never snapshot rendered React DOM — rendered output is brittle (class names, whitespace, auto-generated IDs change between builds). Replace DOM snapshots with role/testid assertions.

5. **Async patterns**: always `await` returned promises. Never use the `done` callback pattern (deprecated idiom; async/await is the supported form in Vitest). For timer-based code, use `vi.useFakeTimers()` + `vi.runAllTimers()`.

6. **Mocks via `vi.mock`** at the module boundary: `vi.mock('../path/to/module')` hoists the mock above imports. Scope mocks to the test file — never use `globalThis` or `vi.doMock` for cross-file state. Reset mocks between tests with `vi.clearAllMocks()` in `afterEach`.

7. **Run command**: `{{ profile.test_recipe.unit }}` from the project root for the target package.

## Inputs

- The spec's acceptance criteria — unit tests cover the invariants asserted by the ACs.
- Existing tests in the targeted package for convention-matching (describe/it naming, mock patterns, file placement).

## Outputs

No artifact. This skill emits prescriptive guidance inline. The implementing agent writes test files in the assigned worktree.

## Quality bar

- [ ] Every PR meets the coverage threshold `{{ profile.test_recipe.coverage_gate }}` — do not lower the threshold.
- [ ] Every snapshot has a one-line comment: `// Snapshot: <what is being serialized>`. No naked snapshots.
- [ ] Every async test uses `await` — no `done` callback pattern.
- [ ] No `it.skip` or `test.skip` without a `// TODO: <reason>` comment referencing the change that will fix it.
- [ ] Mocks scoped to the test file; no global mock state that leaks across test files.
- [ ] `vi.clearAllMocks()` (or `vi.resetAllMocks()`) in `afterEach` when mocks are used.

## Common Rationalizations

| Rationalization | Why it's wrong | What to do instead |
|---|---|---|
| "The snapshot updated automatically; I'll just commit it without reviewing the diff." | Auto-accepted snapshot diffs silently encode regressions into the golden file. A changed snapshot is a claim that "this is the new correct output" — committing it without inspection is committing an unreviewed spec change. | Open the snapshot diff in the PR and read every changed line; if a snapshot change is intentional, note it in the PR description; reject unexpected changes. |
| "`expect(asyncFn()).resolves.toBe(value)` is equivalent to `await expect(asyncFn()).resolves.toBe(value)`." | Without `await`, Vitest does not wait for the assertion promise — the test passes even if the assertion throws because the rejection goes unhandled. Coverage shows green; the bug ships. | Always `await` assertions against `.resolves` and `.rejects`; or use `await asyncFn()` + a synchronous `expect` to make the awaiting explicit. |
| "I'll define the mock at the top of the describe block; all tests in this file need it." | Module-level mock state leaks across test files when the runner shares workers. A test that depends on a mock defined in another file's `beforeAll` will pass in isolation and fail in suite. | Scope mocks with `vi.mock()` at the file level and reset them with `vi.clearAllMocks()` in `afterEach`; never share mock state across describe blocks in different files. |
| "Skipping `--coverage` locally is fine; CI will catch coverage regressions." | By the time CI reports a coverage regression, the PR is already open and the fix requires a second commit + review cycle. Coverage is cheapest to check locally before push. | Run the coverage form of the unit run as part of pre-commit work; fix coverage gaps before pushing. |

## Skills this one composes with

- `playwright-e2e` — complementary; vitest-unit covers component-level invariants, playwright-e2e covers user-facing flow integration.
- `nextjs-ssr-patterns` — when unit-testing Next.js server actions, route handlers, or utility functions.
- `vite-spa-patterns` — when unit-testing React components or hooks.
- `fastify-backend-patterns` — when unit-testing Fastify route handlers, service layers, or validators.

## Anti-patterns

- Never snapshot rendered React DOM (the rendered HTML tree). DOM snapshots break on any styling or structural change. Use `getByRole` or `getByTestId` assertions instead.
- Never use the `done` callback for async tests. Use `async/await`; the `done` pattern masks unhandled rejections.
- Never `it.skip` a failing test without a TODO comment referencing the change that will fix it. A bare `it.skip` is invisible technical debt.
- Never mock at the function boundary when a network mock is more appropriate. For code that calls a real HTTP endpoint, prefer `msw` or a `fetch` mock at the network layer rather than replacing the function itself.
- Never write a vitest unit test for a flow that crosses network or process boundaries — those belong in Playwright e2e. The boundary is: if the test requires a running server, it is not a unit test.
- Never invent a coverage threshold lower than the profile's gate to make tests pass.
