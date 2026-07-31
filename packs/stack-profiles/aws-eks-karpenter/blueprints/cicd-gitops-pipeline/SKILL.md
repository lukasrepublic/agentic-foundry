---
id: cicd-gitops-pipeline
covers: ["ci-build-template", "immutable-registry-push", "gitops-write-back", "applicationset-activation", "deploy-notifications"]
parametrizes_from: []
---

## When to trigger

- Standing up the delivery pipeline for a service migrating onto the EKS/GitOps target (new CI job,
  registry repo, gitops values file, or ApplicationSet entry).
- Debugging a registry push 403/400, a service that "deployed but didn't change", or notifications
  that double-post or go silent.
- Onboarding an externally-hosted (GitHub-origin) repo into the same delivery contract.

## The pipeline shape (six parts, one contract)

1. **Shared CI build template.** Every service `include:`s ONE shared build template and sets only a
   few per-app variables (`APP_NAME`, `REGISTRY_REPO`, `DOCKERFILE`, `VALUES_FILE`, `DEPLOY_BRANCH`).
   Build logic lives in exactly one place — a gotcha fixed in the template is fixed for the fleet.
2. **Rootless build → immutable commit-SHA tag.** The image is built rootless (BuildKit) and pushed
   to an immutable-tag registry where the tag IS the commit SHA (`imageTagMutability: IMMUTABLE` is a
   supply-chain control, never a nuisance to disable).
3. **GitOps write-back.** On a successful deploy-branch build, a bot bumps `image.tag` in the
   service's values file in the gitops repo (push or auto-merged MR). The write-back is the ONLY
   bridge from CI to CD — no `kubectl`/`helm` from CI.
4. **ApplicationSet over the ONE shared multi-tenant workload chart.** Each service is one generator
   entry rendering the shared chart — never a bespoke Application per service.
5. **One-line activation, sync-wave ordered.** A service goes live by uncommenting exactly one
   generator/app-of-apps path line and is DARK until then; ordering across services is sync-waves,
   not merge timing.
6. **Dual-channel notifications, label-scoped.** CI build/deploy lifecycle (started/succeeded/failed)
   posts to the CI channel FROM the build side; ArgoCD sync/health (sync-running/succeeded/failed/
   health-degraded) posts to a SEPARATE gitops channel FROM the CD controller's notification
   subscriptions. The ApplicationSet stamps a label on each generated Application and the
   subscriptions match ONLY that label — platform apps carry no label and stay silent; nothing
   double-posts (each channel has exactly one producer).

## Both build origins, one downstream contract

- **`build_origin: self-hosted-ci`** — the default: services on the self-hosted CI host build
  in-cluster on a rootless-BuildKit runner using WORKLOAD IDENTITY for registry auth.
- **`build_origin: oidc-runner`** — externally-hosted (GitHub-origin) repos build on hosted runners
  via cloud OIDC to the registry (no repo import), and write back CROSS-HOST via the gitops host's
  API with an auto-merged MR (the two SCM mains diverge — never assume a shared remote).
- **Both origins emit the SAME write-back + notification contract** — downstream (ApplicationSet,
  activation, channels) cannot tell them apart.

## Baked-in guardrails (each was fixed once, generically, on the evidence engagement)

- **Immutable-tag pre-check (the same-commit rebuild 400).** The build HEADs the registry manifest
  for the SHA tag FIRST and NO-OPS when it already exists — layer pushes are idempotent but the
  manifest PUT for an existing immutable tag is a deterministic 400 (distinct from a retriable
  daemon-flap push failure). Corollary rule: a CONFIG-ONLY change (e.g. a prep-time secret consumed
  at build) requires a NEW COMMIT to produce a new image — never flip the registry to MUTABLE to
  dodge it (immutability is the control).
- **Identity-shadow unset (the wrong-account push 403).** The shared template's `before_script`
  UNSETS ambient static cloud credentials — `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
  `AWS_SESSION_TOKEN`, `AWS_PROFILE` — so the SDK default chain falls through to workload/pod
  identity (static env vars otherwise WIN the chain and authenticate as the wrong account). Triage
  rule for ANY push 403: read the DENIED PRINCIPAL in the cloud audit log FIRST — a cross-account/
  user principal means identity shadowing, not a missing IAM grant.
- **Push-policy re-scope on every new repo.** The push IAM policy is scoped to the SET of repo ARNs;
  adding a service's registry repo without re-applying that policy pushes layers then 403s on the
  final manifest HEAD. Rule: adding a registry repo ALWAYS re-applies the scoped push policy over
  the full ARN set (never a narrowly-targeted apply of just the new repo).
- **Protected-branch write-back (the manual first deploy).** Write-back to a protected gitops branch
  is rejected until the bot holds a push exception — until granted, the FIRST deploy of each service
  is a manual branch→PR→merge tag bump; say so in the service runbook instead of letting each team
  rediscover it.
- **Restricted-PSA notify hygiene.** The notify step uses NO-INSTALL tooling (busybox
  `wget --post-data` with a printf-built payload — never `apk add curl jq` under a restricted Pod
  Security posture), and the failure-path `when:` lives INSIDE the CI rule (a top-level `when:` is
  overridden by `rules:` and the failure notification never fires).

## Anti-patterns

- **A bespoke pipeline or Application per service** — the shared template + ApplicationSet ARE the
  mechanism; per-service copies re-open every gotcha this blueprint closes.
- **Flipping the registry mutable** to "fix" a same-commit rebuild — the 400 is the control working;
  cut a new commit.
- **Treating a push 403 as a missing grant** before reading the denied principal — identity
  shadowing and stale policy scope both masquerade as authorization bugs.
- **One notification channel (or unlabeled subscriptions)** — CI and CD lifecycles have different
  producers and audiences; unlabeled subscriptions double-post platform apps or silence migrated ones.
- **CI-side `kubectl`/`helm` deploys** — the write-back + ArgoCD reconcile is the only deploy path
  (see conventions.md §GitOps realization).
