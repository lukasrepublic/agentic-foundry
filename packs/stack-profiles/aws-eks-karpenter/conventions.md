# aws-eks-karpenter — IaC architecture, layering, GitOps, and blast-radius conventions

The first `profile_kind: infra` stack profile (`id: aws-eks-karpenter`): an **AWS EKS +
Karpenter** cluster delivered with **OpenTofu** (the `tofu` binary, not the legacy Terraform
binaries) for the cloud/cluster infrastructure and **ArgoCD GitOps** (App-of-Apps) for the
in-cluster manifests. These are the *genuine* commands and shapes such a stack uses in
2024-25 — loaded into the generic worker, never generated.

> **Threat model — TRUSTED OPERATOR.** This profile is operator-curated machinery,
> authorized at the normal `/foundry:authorize` gate. These conventions are an **advisory
> mistake-catcher FOR the trusted operator**, not a defense AGAINST them. The `infra_binding`
> command strings are **data** — they are read by the `id-plan` / `id-impact` / `id-verify` /
> `id-apply` consumers and executed later only through the **CTX command-policy guard**, never
> by this pack.

## Layers

The stack is organized inner-to-outer (the `architecture.layers` of the profile); a layer may
depend only on the layers below it (the `allowed_dependencies` contract):

1. **`network`** — VPC, subnets, route tables, NAT, security groups. The foundational fabric;
   it depends on nothing in-stack.
2. **`cluster`** — the EKS control plane, the OIDC provider + IRSA / Pod-Identity roles, and
   the baseline cluster add-ons (CNI, CoreDNS, kube-proxy). Depends on `network`.
3. **`capacity`** — Karpenter (`NodePool` / `EC2NodeClass`) plus any managed node groups: the
   node-provisioning layer. Depends on `cluster`.
4. **`platform`** — cluster-wide platform services: ArgoCD itself, ingress, cert-manager,
   external-secrets, the observability stack. Depends on `cluster`.
5. **`workloads`** — the application workloads ArgoCD reconciles from the GitOps manifests.
   Depends on `platform` (the platform services it consumes) and `capacity` (it schedules onto
   the provisioned nodes).

## Dependency direction (the allowed_dependencies rule)

Dependencies point **downward only** — an upper layer may depend on a lower one, never the
reverse. A `network` module reading a `cluster` output, or `capacity` reaching up into
`workloads`, is a layering violation. **This contract is declarative — no Foundry check
enforces it today.** Reviewers uphold it; `allowed_dependencies` records the intent a
reviewer checks against.

## GitOps realization (ties to `id-apply`)

There are **two realization paths**, and the `apply` slot documents both:

- **The infrastructure itself** (EKS, Karpenter controllers, the IAM/network) is realized with
  `tofu apply` — the OpenTofu state machine mutates AWS directly.
- **The in-cluster workloads + platform manifests** are realized via **GitOps**: a merge to the
  manifest paths under `gitops_paths` is reconciled by the ArgoCD controller — **never** a direct
  `kubectl apply`. The controller is the only thing that mutates the cluster for those paths.

`id-apply` reads the profile's `gitops_paths` **plus the change target** to pick the
**EXECUTE** (`tofu apply` for non-GitOps infra), **GENERATE** (write the manifest + let ArgoCD
reconcile), or **VERIFY-ONLY** (already-GitOps-managed) branch. The GitOps-verify-only decision
is sourced **here** (the paths) + the change classification — not from `ctx-posture`.

## Read-only command discipline (the `infra_binding` read-role)

The `plan`, `verify`, and `policy` slots are **read-only** and pinned to CTX-read-only leading
verbs — the loader's read-role allowlist rejects a mutating verb in any of them. They are
**local + deterministic + NO-cluster** (v2, ADR C3): the pre-merge structured evidence comes from
the candidate-vs-merge-base diff, **never a live cluster read**.

- **`plan`** = `tofu plan -detailed-exitcode` (the IaC delta) + a local **`helm template`** RENDER
  that materializes the GitOps manifests. The pre-merge GitOps change-evidence is the **git-diff of
  the committed (rendered) manifests vs the merge-base** (consumed by `parse_gitops_changes`) —
  **NOT `argocd app diff`**, which emits unified **text** (not JSON) and reaches **live cluster
  state** (the phantom structured `argocd app diff -o json` does not exist).
- **`verify`** = `tofu plan -detailed-exitcode` (must come back **empty** — exit 0, no drift).
  Post-merge ArgoCD realization (Synced ∧ Healthy via `argocd app get -o json`) is a **separate v2b
  concern**, not in this read slot.
- **`policy`** = `conftest test -o json --policy …/policy <rendered-manifests>` — the OPA/Rego
  **policy-as-code gate** over the **rendered** manifests, referencing the profile's
  [`policy/karpenter.rego`](policy/karpenter.rego) pack (render-first with `kustomize build` /
  `helm template` upstream of this slot). The `-o json` is the `parse_policy_findings` structured
  contract.

**`argocd app diff` is forbidden as a structured pre-merge source in EVERY read slot** (plan,
verify, AND policy) — the stale live-cluster source must not be smuggled into any of them.

Only **`apply`** (`tofu apply`) is a mutation, and it is posture-gated downstream by the CTX
runtime guard — this profile never runs it.

## Policy-as-code gating (the `infra_binding.policy` Rego pack)

The high-blast Karpenter NodePool risks are expressed as a **Rego conftest policy pack** shipped as
profile content ([`policy/karpenter.rego`](policy/karpenter.rego)) and invoked via the `policy`
slot. Per **ADR C2** the rule **KIND** is the gating discriminator:

- The high-blast NodePool changes the operator **MAY legitimately authorize** — **AMI /
  `amiSelectorTerms`, `expireAfter`, `consolidationPolicy`/disruption, capacity-type** — are
  **`warn` rules tagged `# METADATA custom.severity: high`**. They land in `warnings[]` ⇒
  `gating: warn` ⇒ **ACKABLE** via a frozen `policy:high-blast-ack` `{rule, resource}` entry.
- A change that must **NEVER pass** (a true never-allowed violation) is a **`deny`** rule (lands in
  `failures[]` ⇒ a hard, un-ackable FAIL).
- **Every** gating rule (`deny` or high-`warn`) emits a structured object
  `{msg, severity, resource, rule}` with a **non-empty `resource`** in canonical
  `<kind>/<namespace>/<name>` form (cluster-scoped: `<kind>//<name>`; tofu-plane: the `<tf-address>`)
  — the identity the operator's frozen ack names, matched by the shipped `_resource_matches`.

The `conftest test -o json` output is the structured contract consumed by `parse_policy_findings`.
The v1 `blast_radius` advisory tier + the loader's **≥1-HIGH** invariant **STAY untouched**
(Correction C) — the karpenter HIGH risk lives in **both** tiers (advisory `blast_radius` + gating
policy pack).

## Karpenter blast-radius tiers (the `infra_binding.blast_radius` rule)

The `blast_radius` set encodes the design §6 Karpenter tiering as **machine-evaluable**
`{tier, action, resource_type, attr?}` rules. `id-impact` matches a planned action to a tier;
**HIGH** escalates to extra approval + a disruption-budget / canary rollout. The tiers reflect
**node churn**:

- **LOW** — no node churn: `limits`, `labels`/`annotations`, or **adding** a mutually-exclusive
  `NodePool`.
- **MEDIUM** — bounded, rolling churn: a `requirements` change, `consolidationPolicy`, or
  `consolidateAfter`.
- **HIGH** — **mass node replacement**: an **AMI change** (`amiSelectorTerms` — the Karpenter
  *Drift* case), `expireAfter` / `terminationGracePeriod`, a **capacity-type** flip, a node-group
  **replace**, or a **NodePool removal** (which drains + replaces every node it owns).

The profile carries **≥1 HIGH** rule (the loader enforces this — a no-HIGH infra profile is a
fail-closed authoring error). The tier set is **data here, logic in `id-impact`**: this profile
declares the tier→match rules; the consumer reads them to classify a plan.

## Small-fleet safe defaults (ER #87, grounding corrected ER #187)

Two silent foot-guns on the small fleets typical of staging / early production. Both fail
invisibly; both are AUTHORING RULES here, not tribal memory.

- **Node pools default to an ABSOLUTE disruption budget — never an unqualified percentage.**
  Author `disruption.budgets: [{nodes: "3"}]` (an absolute count bounds the blast radius
  regardless of fleet size; 3 concurrent disruptions rolls a small fleet in a couple of waves
  while leaving drain capacity). Karpenter's own documented formula is
  `allowed_disruptions = roundup(total × percentage) − total_deleting − total_notready`
  (karpenter.sh/docs/concepts/disruption) — it rounds **UP**, so a positive percentage always
  permits at least one disruption on any ≥1-node fleet (`roundup(7 × 0.10) = 1`); it never rounds
  down to zero. The real hazard of a *bare* percentage is the opposite one: it has **no absolute
  ceiling**, so the node count it authorises grows unbounded as the fleet grows (and round-up is
  aggressive on tiny fleets too — 50% of 3 = `roundup(1.5)` = 2). Karpenter evaluates multiple
  `budgets` entries by taking the **minimum** (most-restrictive), and Karpenter's own worked
  example pairs a percentage **with a companion absolute-count entry** —
  `budgets: [{nodes: "20%", reasons: ["Empty","Drifted"]}, {nodes: "5"}]` — describing the
  absolute entry as **"a ceiling to the previous budget."** Prefer an absolute budget, or a
  percentage combined with an absolute ceiling, so the blast radius stays bounded no matter the
  fleet size.
- **Validate-time lint rule:** flag any node pool whose disruption budget carries a bare
  percentage with no absolute-count companion (an uncapped ceiling) — surface it at
  `id-validate`, never at 2am.
- **Observability hint:** alert on `drifted-but-not-disrupted` and `pods-pending-too-long` —
  nothing watches the disruption stream by default, and an unbounded disruption wave is silent
  without it.
- **Worker-node roles include the managed-instance (SSM-core) policy BY DEFAULT** (the node-role
  module's `additional_policies` slot), so autoscaled AND bootstrap nodes register with the
  managed-instance service and are session-reachable for remote diagnostics out of the box — the
  substrate an autonomous-operations posture depends on.
- **The reachability diagnosis shape is IAM ONLY:** the node OS already ships the agent and egress
  is typically fine; attaching the managed-instance-core policy to the node roles fixes it with
  **no node replacement** (running nodes pick up the permission on the next credential refresh).
- **Plan-ordering lesson (adjacent):** never data-source-read at plan time what a paused/late
  module creates at apply time — a deterministic read of a not-yet-created resource errors the
  whole plan; defer it with an explicit `depends_on`.
