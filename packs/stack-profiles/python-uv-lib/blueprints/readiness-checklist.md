# python-uv-lib readiness checklist

Gate before a change built under this blueprint is declared production-ready. The `governance` block of `stack-profile.yaml` selects the kits and levels that enforce it.

- [ ] Public API surface (exported symbols) is documented with docstrings and type hints.
- [ ] Typed exceptions a caller can distinguish; no bare `Any` at the boundary.
- [ ] Import-time cost is low; optional extras are lazily imported.
- [ ] CHANGELOG entry per released version; semver respected.
- [ ] `uv.lock` committed; dev tools pinned per the standing-versions manifest.
