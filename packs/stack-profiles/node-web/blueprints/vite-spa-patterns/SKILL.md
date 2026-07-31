---
id: vite-spa-patterns
covers: ["vite-spa", "code-splitting", "client-env-handling", "auth-gated-routing"]
parametrizes_from: ["static_validation.build"]
---

## When to trigger

- Implementing a spec that names a Vite + React SPA change.
- Adding or modifying a route in the SPA (auth-gated by default).
- Authoring build-config changes in `vite.config.ts`.
- Env-handling decisions: which variables need `VITE_` prefix; distinguishing public vs. server-only values.
- Code-splitting boundary decisions (lazy-loading a new feature module).
- Debugging a Vite build issue — chunk size warnings, tree-shaking problems, env-var not injected.

## Procedure

1. **Determine route type**: default is auth-gated. A public route is an exception that requires an explicit decision recorded in the spec. If in doubt, gate it.

2. **Place the route file** per the routing-library convention the project uses. Follow the existing router setup in the package's `src/router/` (or equivalent) before introducing a new pattern.

3. **Lazy-load the route** via dynamic import to enforce code-splitting boundary: `const MyPage = lazy(() => import('./pages/MyPage'))` wrapped in `<Suspense fallback={<LoadingSpinner />}>`. Every route is lazy-loaded unless it is the top-level app shell.

4. **Env access via `import.meta.env.VITE_*`** (never `process.env` — Vite does not expose `process.env` by default). Variables without the `VITE_` prefix are only available at build time in `vite.config.ts`; they are NOT injected into the client bundle. Secret values (API keys, tokens) must NEVER use the `VITE_` prefix — they become part of the public bundle.

5. **Build-config changes** go in `vite.config.ts` at the package root. Document every config addition with a one-line comment explaining why. Do not add plugins speculatively.

6. **Build verification + lint + typecheck**: confirm the package builds cleanly with `{{ profile.static_validation.build }}` from the project root, scoped to the target package, and run lint + typecheck before marking the PR ready.

### Dependency installation

**Default command for population on a fresh worktree or after a `git pull` that includes a lockfile change**: `npm ci` — lockfile-respecting, deterministic install. Run from the project root. This is the form CI workflows and Dockerfiles use. The bare `npm install` form is acceptable only when the lockfile is known-stale or for the initial devcontainer bootstrap; for the per-PR verifier flow, `npm ci` is the default.

**Authoring-time exception**: `npm install <pkg>` (which mutates the manifest and lockfile) is permitted ONLY when the spec explicitly authorizes the package by name. Drift between the authorized package and the installed package is a defect — verify the spec names the exact package before running `npm install <pkg>`. After it mutates the lockfile, subsequent verifier reruns in the same worktree use `npm ci` against the updated lockfile. Outside that explicit authorization, `npm ci` is the only permitted form.

## Inputs

- The spec's requirements and design.
- Existing SPA-package source for convention-matching (read router and layout setup before writing new routes).
- The design-system baseline doc.
- The current `vite.config.ts` and `tsconfig.json` before any config modification.

## Outputs

No artifact. This skill emits prescriptive guidance inline. The implementing agent writes route/component/config files in the assigned worktree.

## Quality bar

- [ ] Every SPA route is auth-gated by default; any public exception has an explicit spec line authorizing it.
- [ ] Every env var accessible in the client bundle is prefixed `VITE_`; secret values are NOT prefixed `VITE_`.
- [ ] Every route (except the top-level app shell) is lazy-loaded with `lazy()` + `<Suspense>`.
- [ ] No SEO meta tags (`<title>`, `<meta name="description">`) in an auth-gated SPA that is not indexed.
- [ ] Bundle-size budget per package is respected — verify the build produces no chunk-size warnings above the configured threshold.
- [ ] Lint + typecheck pass before the PR is marked ready for review.

## Common Rationalizations

| Rationalization | Why it's wrong | What to do instead |
|---|---|---|
| "Code-splitting is a premature optimization; I'll add it when the bundle gets too large." | Without lazy-loading, every new route adds to the initial bundle that every user downloads on first load — including routes they will never visit. Bundle size regressions compound with each PR and are expensive to reverse later. | Wrap every new route in `lazy()` + `<Suspense>` from the first PR; the incremental cost is two lines of code and the habit prevents bundle creep. |
| "I'm copying this pattern from the Next.js web app; it works the same way in Vite." | Next.js App Router patterns (Server Components, `getServerSideProps`, `generateStaticParams`, `next/image`, `next/font`) do not exist in Vite. Copying them into a Vite SPA produces TypeScript errors at best and silent runtime failures at worst. | Read the existing SPA `src/` before writing any new code; follow the patterns there (React Router / TanStack Router, `import.meta.env`, client-side data fetching) rather than Next.js equivalents. |
| "Cache-busting is handled by the CDN; I don't need to configure content-hash filenames." | A CDN without content-hash filenames requires manual cache invalidation on every deploy. Without invalidation, users receive stale JS bundles after a deploy — the application state and the API contract diverge silently. | Ensure `vite.config.ts` uses `build.rollupOptions.output.entryFileNames` with `[hash]` tokens (the Vite default); confirm the CDN is configured to serve `Cache-Control: immutable` for hashed assets. |
| "The `define` block in `vite.config.ts` is optional; I can pass build-time constants another way." | Build-time constants passed via environment variables without the `define` block are not tree-shaken — dead-code branches (e.g., `if (IS_DEV) { ... }`) remain in the production bundle. The `define` block is the Vite mechanism for eliminating them. | Use `define: { 'import.meta.env.VITE_IS_DEV': JSON.stringify(mode === 'development') }` (or equivalent) in `vite.config.ts` so Rollup can eliminate dead branches during tree-shaking. |

## Skills this one composes with

- `vitest-unit` — for unit tests of SPA components, hooks, and utilities.
- `playwright-e2e` — for e2e flows testing SPA flows (auth-gated; fixtures must handle auth setup).
- Does NOT compose with `nextjs-ssr-patterns` — different bundler, different routing model, no SSR in a Vite SPA.

## Anti-patterns

- Never use `process.env` in SPA source code. Vite exposes `import.meta.env`; `process.env` is undefined at runtime in the browser.
- Never ship a route without lazy-loading except the top-level app shell. Un-chunked routes bloat the initial bundle.
- Never add SEO meta tags to an auth-gated SPA with no public indexing requirement.
- Never mix Vite and Next.js patterns in shared package code. Shared code must be framework-agnostic; framework-specific idioms belong in the respective app package.
- Never commit a `VITE_*` variable containing a secret (API key, token, DB credential). Vite inlines these into the browser bundle — they are public.
- Never modify `vite.config.ts` speculatively. Every config change must map to a spec requirement.
