# service-event-driven readiness checklist

Gate before a change built under this blueprint is declared production-ready. The `governance` block of `stack-profile.yaml` selects the kits and levels that enforce it.

- [ ] Every consumer is idempotent and tolerates redelivery; dedupe key documented.
- [ ] Dead-letter path and a replay procedure exist and are exercised.
- [ ] Event schemas versioned in `schemas/` (AsyncAPI) with compatibility checks in CI.
- [ ] Consumer lag, error rate and DLQ depth are alerted on.
- [ ] Graceful shutdown commits offsets only after processing.
