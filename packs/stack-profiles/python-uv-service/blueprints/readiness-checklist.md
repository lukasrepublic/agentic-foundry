# python-uv-service readiness checklist

Gate before a change built under this blueprint is declared production-ready. The `governance` block of `stack-profile.yaml` selects the kits and levels that enforce it.

- [ ] Liveness (`/healthz`) and readiness endpoints exist and are exercised by the boot recipe.
- [ ] Every non-public route enforces authn/authz; request models validate at the ASGI edge.
- [ ] Config and secrets come from the environment; none in code or fixtures.
- [ ] `uv.lock` committed; CI runs `uv sync --locked`.
- [ ] RED metrics per route and structured logs with request-id correlation.
- [ ] OpenAPI generated from typed models and committed or published.
