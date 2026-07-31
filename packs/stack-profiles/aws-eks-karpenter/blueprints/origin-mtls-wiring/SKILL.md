---
id: origin-mtls-wiring
covers: ["origin-mtls", "cdn-origin-lock", "trust-store", "public-exposure"]
parametrizes_from: []
---

## When to trigger

- Exposing a service to the public internet behind a CDN/edge proxying to a cloud load-balancer
  origin — especially a sensitive/custody service (origin-mTLS is a hard prerequisite there).
- Debugging an edge TLS-handshake error on an origin pull, or reviewing an origin locked only by
  CDN IP ranges.

## The chain (vendor-neutral; ALB trust stores + CDN authenticated origin pulls are the concrete forms)

- **Private Root CA → a trust store attached to the load balancer's 443 listener in VERIFY mode →
  one per-host (wildcard-CN) leaf CLIENT certificate (clientAuth EKU) uploaded to the CDN's
  per-hostname origin-pull feature.** The origin then accepts ONLY connections bearing the edge
  provider's cert — "our-zone-only", closing the shared-CDN-IP bypass (the published egress
  ranges are shared by every tenant of the provider).
- **Only `verify` enforces — the permissive/passthrough mode is FAIL-OPEN.** The workload chart's
  toggle whitelists verify/off ONLY, so a typo fails closed instead of silently admitting.

## The load-bearing rollout ordering (and its rollback)

- **(a) attach the trust store in a no-enforce mode → (b) upload the leaf to the CDN per host →
  (c) flip the listener to `verify`.** Flipping verify BEFORE the CDN presents the cert breaks
  every handshake for every host on the listener — the ordering is load-bearing, not cosmetic.
- **Rollback is flipping the listener back to off** — one attribute, instant, no cert churn.

## The DNS trap (masquerades as an mTLS failure)

- **Use an explicit per-host proxied CNAME → the correct origin; a wildcard proxied record is a
  trap.** The wildcard resolves every host to the edge, but its ORIGIN is not the intended load
  balancer — the origin pull lands off-target and fails with a generic edge TLS-handshake error
  even though per-host origin-pull is "active".
- **Diagnostic rule: listener shows verify + trust store active + zero revoked ⇒ a handshake
  error is DNS/origin-routing, not the mTLS config.**

## Custody extras (before a signer/custody service is public)

- **Attach a CRL (empty, same Root CA) to the trust store and PROVE the revoke round-trip** — a
  leaked leaf must be revocable without re-rolling the CA.
- **The secret holding the CA/leaf key material carries a deny-by-default resource policy with a
  break-glass admin exception** — and include the IaC management principal in the allow-set so
  the hardening cannot lock out its own provisioner (the custody-dataplane sibling footgun).

## Anti-patterns

- **Locking the origin to CDN IP ranges alone** — proves the network, not your zone.
- **Running the listener in permissive mode "temporarily"** — fail-open is not a rollout stage;
  the no-enforce stage is for ATTACHING, and the flip to verify follows the cert upload.
- **A wildcard proxied record feeding origin pulls** — explicit per-host CNAMEs only.
- **Treating origin-mTLS as user auth** — it authenticates the zone; IDOR/BOLA on the backend is
  untouched by it (the security-reviewer authZ-honesty lens owns that).
