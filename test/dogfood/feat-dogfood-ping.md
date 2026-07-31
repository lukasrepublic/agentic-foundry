# Dogfood atom — ping endpoint

A trivial atom used to dogfood the Foundry item-0 cycle end-to-end (authorize →
emit-provenance → merge-gate) against real git. Not a real product feature.

<!-- normative -->
## Acceptance criteria

- **AC-PING-1**: `GET /ping` returns a JSON body `{ "ok": true }` with HTTP 200.
<!-- /normative -->

## Changelog

- v1.0 Draft. (dogfood fixture)
