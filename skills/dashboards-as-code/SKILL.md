---
name: dashboards-as-code
description: 'Migrate a fleet of hand-built monitoring dashboards into version-controlled code with a MACHINE-CHECKED equivalence proof. The five-part harness: immutable golden originals → a typed emitter with a generic panel factory whose unknown-kind fallback FLAGS (never silently guesses) → a renderer back to platform JSON → a semantic-signature extractor → ONE shared definition of MATCH used by both the per-dashboard convert self-verify AND the fleet gate (scripts/foundry-dashboard-fidelity.py: N/N MATCH fail-closed). Platform-generic (a Grafana-style schemaVersioned export is the illustration). Carries the path-scoped CI wiring, the restricted-PSA job hardening, and the CSI-before-stateful-metrics-store + sidecar-egress ordering prerequisites. Trigger: "port the dashboards to code", "dashboards as code", "prove the dashboard migration is lossless", "/foundry:dashboards-as-code".'
---

# dashboards-as-code — migrate hand-authored dashboards to code, provably lossless

A hand-port of a dashboard fleet is silently lossy by default; nobody notices the dropped panel
until an on-call engineer opens it mid-incident. This skill drives the migration through a
round-trip fidelity gate so an as-code migration CANNOT land unless it is provably lossless.

## The five-part harness

- **Golden originals are IMMUTABLE — never hand-edited.** One JSON export per dashboard,
  extracted from the running platform, is the diff oracle for the whole migration. Fixing a diff
  by editing the golden is falsifying the oracle.
- **The emitter uses a generic panel factory, and its unknown-kind fallback FLAGS — never
  silently guesses.** Common panel kinds (time-series, stat, gauge, table, logs, pie, bar) map
  through one factory; rows, template variables, and library-panel references are first-class. A
  panel kind the factory does not know routes to a default kind WITH the `x_foundry_unverified`
  flag set — the gate then refuses green on it, so it becomes a human-review item instead of a
  silent guess. A dashboard too irregular to emit cleanly may be hand-authored — it still goes
  through the same gate.
- **The renderer compiles code → platform JSON**, auto-discovering every compiled dashboard (a
  dashboard that fails to render is a gate failure, not a skipped file).
- **The semantic-signature extractor reduces both sides to parity-bearing fields only** — panels
  → {kind, queries, layout position}, template variables, datasource references. Byte comparison
  is useless (a newer builder SDK emits a different schemaVersion and injects defaults the old
  export lacks); the signature is the symmetric normalization. **Layout is enforced** — the right
  panels in the wrong places still fails.
- **ONE shared definition of MATCH, two call sites.** The `signature()` predicate in
  `scripts/foundry-dashboard-fidelity.py` is invoked by BOTH the per-dashboard convert
  self-verify (emit → compile → render → round-trip vs golden in one command) AND the fleet gate
  (`gate --golden <dir> --rendered <dir>` → **N/N MATCH**, fail-closed on any mismatch, missing
  pair, flagged panel, or zero pairs). A converter can never self-certify green under a looser
  rule than the merge gate enforces.

## Named symmetric normalizations (registered for BOTH sides, by construction)

- **Schema-version / injected-defaults noise** never enters the comparison (the signature
  projects parity-bearing fields only).
- **A placeholder target with no expressible query string is equivalent to absent.**
- **A library-panel reference is keyed by {name, uid} + grid position**; its server-resolved
  type/targets are ignored (the platform resolves them from the shared library at load).
- **A normalization can never be applied to only one side** — the extractor is one function called
  on both inputs, so a one-sided loosening is structurally inexpressible; the selftest proves a
  real query divergence still fails under the full normalization set.

## CI wiring (the merge check)

- **Path-scope the gate job** to the dashboards-as-code source tree (path-scoped workflow rules)
  so unrelated PRs are never gated on dashboards.
- **Restricted-PSA hardening:** a non-root CI pod under a restricted pod-security posture cannot
  `apk add` tooling at job time — call the underlying package-manager/build commands directly and
  redirect the tool's HOME/cache into the writable build directory.

## Ordering prerequisites (they wedge the migration if skipped)

- **Install the CSI driver + a default storage class BEFORE the stateful metrics store.** Modern
  Kubernetes removed the in-tree volume provisioner; the metrics store and the dashboard platform
  both need persistent volumes, and the stateful workload silently fails to bind storage without
  the CSI step.
- **Metrics sidecars under a default-deny pod network need an explicit egress allow to the
  metrics receiver** — an "all workloads emit metrics" posture interacts with the tenant
  network-isolation fragment; without the allow the sidecars cannot ship.

## Anti-patterns

- **Editing a golden to make a diff pass** — the oracle is immutable; fix the emitter or flag.
- **Byte-diffing exports** — thousands of false diffs from schema/default noise; compare the
  semantic signature.
- **A silent unknown-panel guess** — flag it; the gate turns it into review, not false green.
- **A looser self-verify than the gate** — one MATCH definition, two call sites, always.
- **Green on a partial fleet** — the verdict is N/N; a missing pair is a coverage failure.
