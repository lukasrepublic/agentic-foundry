# node-web — architecture, layering, and dependency-direction conventions

The Node/TypeScript web stack profile (`id: node-web`), reverse-engineered from a mature,
real-world reference stack: a **Fastify** HTTP/API server, a **Next.js**/**Vite**
front-end, **TypeScript** end to end, **npm**/**pnpm** for dependency management,
**Vitest** for unit/integration tests, and **Playwright** for end-to-end. These are the
*genuine* commands and shapes such a stack uses — loaded into the generic worker, never
generated.

> **Threat model — TRUSTED OPERATOR.** This profile is operator-curated machinery,
> authorized at the normal `/foundry:authorize` gate. These conventions are an **advisory
> mistake-catcher FOR the trusted operator**, not a defense AGAINST them. They encode the
> house style so the generic factory's SDLC steps 4 (architecture/conventions), 6 (static
> validation), 7 (test recipe), and 8 (exercising the app's live seam) are stack-faithful.

## Layers

The stack is organized into four layers (the `architecture.layers` of the profile), inner
to outer:

1. **`domain`** — pure business types + logic. No I/O, no framework imports, no HTTP. The
   stable core; everything else may depend on it, it depends on nothing in this app.
2. **`data`** — persistence + external-service adapters (DB clients, repositories, the
   third-party SDK wrappers). Depends only on `domain`.
3. **`api`** — the Fastify server: routes, plugins, request/response schemas, controllers.
   Composes `data` to fulfill `domain` use-cases. Owns transport concerns (validation,
   auth middleware, serialization).
4. **`web`** — the Next.js/Vite front-end: pages, components, client data-fetching. Talks
   to `api` over HTTP (never imports `data`/`domain` server internals directly).

## Dependency direction (the allowed_dependencies rule)

Dependencies point **inward only** — an outer layer may import an inner layer, never the
reverse. This is the `architecture.allowed_dependencies` contract:

- `web` → `api` (over HTTP) only.
- `api` → `data`, `domain`.
- `data` → `domain`.
- `domain` → (nothing in-app).

A `domain` module importing from `api`/`data`/`web`, or `data` reaching into `api`, is a
layering violation. **This contract is declarative — no Foundry check enforces it today.**
Reviewers uphold it; `allowed_dependencies` records the intent a reviewer checks against.

## Conventions

- **TypeScript strict.** `tsconfig.json` runs `strict: true`; `tsc --noEmit` is the
  typecheck gate (no implicit `any`, no unchecked nulls).
- **Formatting + lint.** Prettier owns formatting; ESLint owns correctness/style. CI runs
  `prettier --check` and `eslint` with `--max-warnings=0`.
- **Validation at the boundary.** Request/response bodies are validated at the `api` edge
  (Fastify JSON schema / a runtime validator); typed contracts cross every layer boundary.
- **No secrets in code.** Configuration + secrets come from the environment; never commit
  `.env` with real values.
- **Tests co-located by layer.** Unit tests live beside the unit under test; integration
  tests exercise `api` + `data` against a real (ephemeral) DB; e2e drives the `web` + `api`
  surfaces together via Playwright.
- **Coverage floor.** The `test_recipe.coverage_gate` (80) is the minimum line coverage CI
  enforces.

## Live seam (app_exercise_binding)

The profile's `app_exercise_binding` is the generic analog of `make dev`: a `boot` command
that starts the stack and ≥1 `surface` exercises covering at least the **`ui`** (Next.js
page render) and **`api`** (Fastify endpoint) surfaces, so exercising the seam during
implementation and `/foundry:certify-local` at release time both have a stack-faithful,
resolvable boot target. (This profile asserts the STRUCTURE of these strings; their
drivability is the consumer's.)
