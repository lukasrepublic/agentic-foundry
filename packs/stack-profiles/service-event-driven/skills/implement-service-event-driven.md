# Implement under service-event-driven

Load `conventions.md` first. Work the atom inside the layer its contract names and respect the allowed edges (api -> messaging -> data -> domain).

1. **domain** changes: write pure functions and types first, with unit tests that need no broker or database.
2. **messaging** changes: a handler decodes the event, calls the domain, and returns; make it idempotent (dedupe key stored in the same transaction) and add a redelivery test. A new or changed event schema needs the compatibility check.
3. **data** changes: repositories and outbox writes only; integration-test against a real containerised database.
4. **api** changes: keep it thin; health and readiness must stay exercised by the boot recipe.

Before declaring done run the profile's `static_validation` and `test_recipe` commands and tick the touched items in `blueprints/readiness-checklist.md`. Never edit `standing-versions/` pins inside an atom.
