# data-pipeline readiness checklist

Gate before a change built under this blueprint is declared production-ready. The `governance` block of `stack-profile.yaml` selects the kits and levels that enforce it.

- [ ] Every task is idempotent and re-runnable for a date partition.
- [ ] Data contracts (schema + freshness + volume expectations) are checked on every load.
- [ ] Lineage and data classification declared for each dataset; PII handling documented.
- [ ] Backfill and replay procedures exist and have been rehearsed.
- [ ] Alerting on failure, lateness and data-quality breaches; run metadata retained.
