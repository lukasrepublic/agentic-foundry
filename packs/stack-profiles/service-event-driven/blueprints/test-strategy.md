# service-event-driven test strategy

What each test lane proves for this blueprint; the exact commands live in `stack-profile.yaml` (`test_recipe`).

- Unit: handlers as pure functions over decoded events.
- Integration: a real broker (containerised) with produce-consume round trips.
- Contract: schema compatibility check on every schema change.
- Chaos-lite: redelivery, out-of-order and poison-message cases.
