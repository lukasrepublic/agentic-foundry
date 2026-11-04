# node-web readiness checklist

Gate before a change built under this blueprint is declared production-ready. The `governance` block of `stack-profile.yaml` selects the kits and levels that enforce it.

- [ ] Health and readiness endpoints; structured JSON logging with request-id correlation.
- [ ] Request bodies validated at the Fastify edge; secure headers and a CORS allow-list.
- [ ] Lockfile committed; `npm audit --production` clean or excepted.
- [ ] Bundle-size budget set and enforced; routes code-split.
- [ ] Accessibility checks run on the primary user journeys.
