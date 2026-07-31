---
id: custody-dataplane
covers: ["security-groups-for-pods", "branch-eni", "custody-isolation", "pod-sg-enablement"]
parametrizes_from: []
---

## When to trigger

- Isolating a custodial/sensitive workload with per-pod security groups (security-groups-for-pods
  / branch ENI) so the data tier admits ONLY the dedicated pod SG.
- A SecurityGroupPolicy pod stuck `Pending` forever, or a branch-ENI pod whose every outbound
  connection times out.
- Reviewing the first per-pod-SG workload on a cluster (the enablers are cluster-wide substrate —
  the workload author is the least likely to notice they're absent).

## The five silent enablers (ALL must hold; every one fails silently)

- **1 — The VPC-resource-controller policy on the CLUSTER role.** The base cluster policy does NOT
  grant trunk/branch-ENI creation. Missing ⇒ `TrunkENICreationFailed: UnauthorizedOperation`, no
  node advertises `pod-eni` capacity, and SGP pods stay **Pending forever**.
- **2 — Trunk-CAPABLE worker nodes.** A node advertises branch-ENI capacity only if it registered
  AFTER the IAM policy was live AND ran the CNI with pod-ENI enabled. Pre-existing/bootstrap nodes
  do NOT retro-attach a trunk — **cycle the node or restart its CNI agent** so it re-registers.
  Karpenter-provisioned fresh nodes pick it up automatically once enabler 1 is live.
- **3 — SNAT exclusion for the data/peer CIDRs.** Default CNI SNAT sends pod→outside-VPC traffic
  out the node primary ENI, which carries the **NODE SG, not the pod SG** — a data-tier SG
  admitting only the pod SG silently drops it (`connection timeout`). Exclude the peered/data VPC
  CIDRs from SNAT so the pod SG survives the hop.
- **4 — Node-SG :53 ingress FROM the pod SGs.** Cluster DNS sits behind the node SG, whose :53
  ingress typically admits only the node SG itself — the branch-ENI pod's DNS queries (sourced
  from the pod SG) are dropped and resolution hangs. **The most-missed enabler**: DNS "already
  works" for every non-branch-ENI pod, and the symptom surfaces as a data-tier timeout.
- **5 — Verify the pod SG's LIVE egress rules were APPLIED — not just authored.** An SG that
  exists and a CR that references it look correct even when the egress rules never landed (a
  partial apply, a provider that dropped the rule). Assert the LIVE egress rules match intent —
  distinct from confirming the SG and CR exist.

## The diagnostic that works (order matters)

- **From a throwaway pod carrying the SGP label, test DNS FIRST, then the data port** — a DNS hang
  masquerades as a DB timeout, and testing the data port first sends you chasing the wrong layer.
- **The custody floor holds when: the data-tier SGs admit ONLY the pod SG** — a branch-ENI pod
  connects while an identical node-SG pod is blocked (run both probes; the pair IS the proof).

## The sibling custody-IaC footgun (route to the security-reviewer custody lens)

- **A deny-read-except-the-secrets-operator policy on a root-of-trust secret ALSO denies the IaC
  provisioner itself** — the provisioner's read-back on create (and every refresh) hits
  AccessDenied, so the hardening as authored can never apply and then deadlocks its own plan.
  **Fix: add the IaC management principal to the allow-set alongside the operator** — custody
  intent preserved (stray/wildcard principals still denied). Any sensitive-workload hardening that
  can lock out its own provisioner belongs on the **security-reviewer custody lens** together with
  the enabler-completeness review.

## Anti-patterns

- **Authoring the SecurityGroupPolicy + pod SG and calling it done** — the visible half only; the
  five cluster-level enablers are the substrate.
- **Debugging the data port before DNS** — the layers masquerade; DNS first.
- **Retro-trusting old nodes** — a node that predates the IAM fix is permanently trunk-less until
  cycled.
- **Loosening the data-tier SG to the node SG** to "fix" timeouts — that deletes the custody
  isolation the branch ENI exists for; fix the SNAT exclusion instead.
- **Trusting authored egress** — assert the live rules; existence of the SG proves nothing.
