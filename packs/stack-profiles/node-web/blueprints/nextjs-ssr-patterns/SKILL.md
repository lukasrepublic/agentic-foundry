---
id: nextjs-ssr-patterns
covers: ["nextjs-app-router", "ssr-isr-ssg", "seo-metadata", "server-components"]
parametrizes_from: ["static_validation.build"]
---

## When to trigger

- Implementing a spec that names a Next.js web change.
- Deciding between SSR, ISR, SSG, or static page for a new route and its SEO/caching implications.
- Authoring a route handler (`route.ts`) or a server/client component.
- Metadata API work (`generateMetadata`, `metadata` export) for SEO.
- Sitemap or robots.txt authoring; `next/image` or image-optimization configuration.
- Next.js version-bump impact assessment on App Router behavior.

## Procedure

1. **Determine page type** from the spec's requirements: static (no dynamic data at render time), ISR (revalidate on schedule), SSR (dynamic per-request), or dynamic segment (generateStaticParams + fallback). Default: ISR with `revalidate` set explicitly.

2. **Place the file under `app/<route>/page.tsx`** in the web package per Next.js App Router file-system routing. Route groups use `(group-name)/` prefix. Parallel routes use `@slot` prefix. Layout segments use `layout.tsx`. Follow the existing folder structure in the package's `app/` directory before introducing new patterns.

3. **Decide server-component-default vs. client component**: default to server component (no directive). Add `'use client'` only when the component uses browser-only APIs, React state (`useState`, `useReducer`), or event listeners. Push the `'use client'` boundary as low in the tree as possible — never at the page level if only a leaf needs interactivity.

4. **Data-fetching pattern**:
   - Static: `fetch(url, { cache: 'force-cache' })` or no fetch (build-time data).
   - ISR: `fetch(url, { next: { revalidate: N } })` where N is seconds.
   - SSR (per-request dynamic): `fetch(url, { cache: 'no-store' })` or `noStore()` import.
   - Never use `getServerSideProps` or `getStaticProps` (Pages Router idioms — App Router rejects these).

5. **Metadata API**: for static routes export `const metadata: Metadata = { title, description, openGraph }`. For dynamic routes implement `export async function generateMetadata({ params }): Promise<Metadata>`. Every public-facing page MUST declare metadata; missing metadata is a build warning and an SEO failure.

6. **SEO checklist** before marking the feature done: (a) page is included in `sitemap.xml` (or explicitly excluded with justification); (b) `robots.txt` respects indexing intent; (c) canonical URL set in `metadata.alternates.canonical`; (d) `next/image` used for all content images with `alt` text; (e) `lang` attribute on `<html>` set in root `layout.tsx`.

7. **Build verification**: confirm the project builds cleanly with `{{ profile.static_validation.build }}` from the project root, scoped to the target package; run lint + typecheck before marking the PR ready.

### Dependency installation

**Default command for population on a fresh worktree or after a `git pull` that includes a lockfile change**: `npm ci` — lockfile-respecting, deterministic install. Run from the project root. This is the form CI workflows and Dockerfiles use. The bare `npm install` form is acceptable only when the lockfile is known-stale or for the initial devcontainer bootstrap; for the per-PR verifier flow, `npm ci` is the default.

**Authoring-time exception**: `npm install <pkg>` (which mutates the manifest and lockfile) is permitted ONLY when the spec explicitly authorizes the package by name. Drift between the authorized package and the installed package is a defect — verify the spec names the exact package before running `npm install <pkg>`. After it mutates the lockfile, subsequent verifier reruns in the same worktree use `npm ci` against the updated lockfile. Outside that explicit authorization, `npm ci` is the only permitted form.

## Inputs

- The spec's requirements and design.
- Existing web-package routes for convention-matching (read before writing new routes).
- The design-system baseline doc.
- The relevant layout spec when cited by the spec.
- The package's `app/` directory structure for routing-segment conventions.

## Outputs

No artifact. This skill emits prescriptive guidance inline. The implementing agent writes page/route/component/layout files in the assigned worktree.

## Quality bar

- [ ] Every page declares its data-fetching strategy explicitly (no implicit caching defaults left uncommented).
- [ ] Every dynamic route has a `generateMetadata` function returning at minimum `{ title, description }`.
- [ ] Every consumer-facing page is sitemap-eligible or explicitly excluded with a one-line comment.
- [ ] No `'use client'` on a page component that has no interactive state — the directive is at the leaf component level only.
- [ ] No inline `<img>` tag for content or hero images — use `next/image` with explicit `width` / `height` or `fill`.
- [ ] No `getServerSideProps` or `getStaticProps` anywhere in the web package.
- [ ] Lint + typecheck + build pass before the PR is marked ready for review.

## Common Rationalizations

| Rationalization | Why it's wrong | What to do instead |
|---|---|---|
| "I'll mark this component `'use client'` to be safe — Client Components are more flexible." | `'use client'` pushes the component and all its imports to the browser bundle, bypassing SSR. For content-listing pages, this destroys the SEO value that is the primary reason to use Next.js App Router instead of a SPA. | Default every new component to a Server Component; add `'use client'` only when the component requires browser APIs, event handlers, or React state — and document why in a comment. |
| "Server Components and Client Components can import each other freely; the build will sort it out." | A Client Component importing a Server Component silently converts the Server Component to a Client Component (Next.js serializes it). This causes surprising data-fetching behavior and can expose server-only data to the browser bundle. | Follow the Server → Client tree direction; pass Server Component output as `children` props to Client Components rather than importing them directly. |
| "I'll skip `generateStaticParams` for now and use dynamic rendering — it's simpler." | Dynamic rendering for high-traffic listing pages adds per-request latency and increases compute cost. `generateStaticParams` enables ISR so frequently accessed pages are served from CDN without a server round-trip. | Implement `generateStaticParams` for listing and detail pages from the first PR; use `revalidate` to balance freshness vs. CDN cache hit rate per the spec's SLA. |
| "Data fetching in a `useEffect` works fine; I'll refactor to a Server Component later." | `useEffect` data fetching causes a client-side waterfall: the JS bundle loads, renders an empty shell, then triggers the fetch. This produces a layout shift, hurts CWV scores (LCP), and eliminates SSR's hydration advantage for the initial page load. | Fetch data in a Server Component's async function body so the page HTML is pre-rendered with data; `useEffect` data fetching is an anti-pattern in App Router for data-critical surfaces. |

## Skills this one composes with

- `vitest-unit` — for unit-test authoring of server actions, route handlers, or utility functions inside the implementing PR.
- `playwright-e2e` — for e2e flows that traverse web pages end-to-end.
- Does NOT compose with `vite-spa-patterns` or `fastify-backend-patterns` — different bundler, different routing model.

## Anti-patterns

- Never use `getServerSideProps` or `getStaticProps` — those are Pages Router idioms. App Router uses fetch with cache directives.
- Never add `'use client'` at the page (route segment) level. Push the boundary to the interactive leaf component only.
- Never bypass `next/image` for content-managed images. Raw `<img>` defeats layout-shift prevention and image optimization.
- Never hard-code an environment-specific URL in a server component. Use `process.env.NEXT_PUBLIC_*` (public, browser-visible) or server-only env vars (private, never exposed to client).
- Never ship a dynamic route without a `generateMetadata` — missing metadata fails the SEO quality bar and the acceptance criteria.
- Never omit the `revalidate` time on an ISR page — leaving it undefined falls back to Next.js defaults which may be wrong for the use case.
