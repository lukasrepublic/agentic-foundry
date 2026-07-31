---
id: tenant-network-isolation
covers: ["network-policy", "multi-tenant-isolation", "default-deny", "cross-tenant-verification"]
parametrizes_from: []
---

## When to trigger

- Deploying multiple mutually-untrusted applications from ONE shared workload chart onto ONE
  shared cluster (the "template a chart per app, one namespace each" pattern).
- Adding a new tenant to a shared multi-tenant chart, or reviewing tenant isolation.
- Debugging cross-tenant reachability, or turning cluster NetworkPolicy enforcement on.

## The fragment — a per-namespace default-deny pair (bake it in the chart, every tenant inherits)

A Kubernetes cluster is a flat L3 network absent an ENFORCED NetworkPolicy. Isolation on a shared
chart is a CHART concern (baked once) plus a VERIFICATION concern (proven, not assumed). The chart
fragment renders a default-deny NetworkPolicy PAIR (ingress + egress, `podSelector: {}`) per
tenant namespace, parameterized by `podCidr`, `serviceCidr`, `dnsNamespace`/`dnsServiceIP`,
`dataTier: [{cidr, ports}]`, `servingPort`/`ingressSource`:

- **Egress-side denial is THE control — ingress-only does not stop initiation.** An ingress-only
  default-deny still lets tenant A INITIATE connections to B. The enforcing rule is the egress
  default-deny whose allow-list is: DNS, same-namespace, the declared data tier, and the internet
  EXCEPT the cluster CIDRs.
- **Put BOTH `podCidr` AND `serviceCidr` in the egress `ipBlock.except`.** Denying only the pod
  CIDR is insufficient: a pod can still reach a peer tenant via the peer's Service ClusterIP,
  because whether the CNI evaluates the rule pre- or post-DNAT is version-dependent — the
  ClusterIP DNAT bypass is closed only when the Service CIDR is excepted too.
- **Allow DNS REDUNDANTLY — a namespace selector for the DNS namespace AND an explicit `ipBlock`
  to the DNS Service IP on port 53** — so resolution works under EITHER DNAT behavior.
- **Declare the data tier explicitly** — `dataTier: [{cidr, ports}]` egress allowances for the
  app's DB/cache endpoints (a managed store inside the workload CIDR still needs this allow).
- **Same-namespace traffic is always permitted** (intra-app ingress + egress).
- **The chart's load-balancer default is INTERNAL.** An internet-facing L7 load balancer is a
  public-IP path that routes AROUND the pod-CIDR egress deny — expose publicly only as an
  explicit, reviewed exception.
- **Safe defaults: a tenant that declares nothing still gets a correct isolating policy** — the
  fragment's defaults deny cross-tenant both ways and allow only DNS + same-namespace + internet-
  minus-cluster-CIDRs.

## The verification recipe — prove the deny, don't assume it

- **A re-runnable canary script** (the `qa-engineer` recipe), given two tenant namespaces, asserts
  from a pod in tenant A — emitting pass/fail evidence for the acceptance contract:
  - **a tenant-B POD IP is BLOCKED** (connection timeout, not refusal-by-absence);
  - **a tenant-B Service CLUSTERIP is BLOCKED** — this is the assertion that proves the DNAT
    bypass is closed (a pod-CIDR-only policy passes the first probe and fails this one);
  - **tenant A's OWN Service is REACHABLE** (isolation did not break the app);
  - **the functional smoke stays green** — DNS resolves, the declared data tier connects, external
    egress works.
- **"Tenants are isolated" is a verified acceptance criterion, never a claim** — wire the canary
  into the change's test evidence whenever a tenant is added or the fragment is modified.

## Cluster prerequisites (the fragment is inert without them)

- **NetworkPolicy ENFORCEMENT must be ON** — on the VPC CNI that is an explicit addon
  configuration flag; without it every policy below is decoration.
- **PIN the CNI addon version** — NetworkPolicy evaluation semantics (including the pre/post-DNAT
  behavior) vary by version; a deterministic gate needs a pinned addon.
- **Re-audit any policy authored while enforcement was OFF** the moment enforcement flips on — a
  pre-existing unenforced NetworkPolicy silently ACTIVATES and may block exec-probe workloads or
  default-allow namespaces that were never reviewed against it.

## Anti-patterns

- **Ingress-only default-deny** — stops nobody from initiating; the egress side is the control.
- **Excepting only the pod CIDR** — the Service ClusterIP DNAT bypass stays open.
- **A single DNS allow** — breaks under the other DNAT evaluation order; allow both ways.
- **A public load balancer by default** — a hole around the egress deny.
- **Flipping enforcement on without re-auditing pre-existing policies** — silent activation.
- **Declaring isolation done without the canary** — unverified isolation is a claim, not a control.
