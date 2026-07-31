---
id: helm-k8s-patterns
covers: ["helm-chart-authoring", "k8s-deployment-strategy", "probes", "external-secrets", "rbac-manifests"]
parametrizes_from: []
---

## When to trigger

- Authoring a new Helm chart or modifying an existing one in the IaC repo's charts tree.
- Authoring or modifying per-environment values files.
- Secret-reference work via an external-secrets operator.
- A deployment-strategy choice (RollingUpdate vs. canary vs. blue-green).
- Probe authoring (readinessProbe, livenessProbe, startupProbe).
- RBAC changes (ServiceAccount, Role, RoleBinding, ClusterRole, ClusterRoleBinding).
- HPA, PDB, or PVC authoring.

## Procedure

1. **Chart structure** under the charts tree's `<name>/`: `Chart.yaml` + `values.yaml` (defaults) + per-environment values files + a `templates/` directory. Read the existing chart structure before modifying — follow the naming and indentation conventions used in sibling charts.

2. **Secret references via external-secrets**: never inline secret values in values files. Use an `ExternalSecret` resource that references the secret name in the secret manager. The `values.yaml` holds only the secret-name path; the external-secrets controller resolves the value at deploy time.

3. **Deployment strategy**: default to `RollingUpdate` with `maxUnavailable: 0` and `maxSurge: 1`. Canary or blue-green strategies are authored only when the spec explicitly requires them — they add operational complexity that must be justified.

4. **Probe defaults** (all three required for production workloads):
   - `readinessProbe`: HTTP GET on the health endpoint; `initialDelaySeconds: 5`, `periodSeconds: 10`.
   - `livenessProbe`: HTTP GET on the liveness endpoint; `initialDelaySeconds: 15`, `periodSeconds: 20`.
   - `startupProbe`: for slow-starting workloads (e.g., a migration runner); `failureThreshold: 30`, `periodSeconds: 10`.

5. **Pre-change render sequence**: lint the chart (schema + YAML validation), then render the templates with the target environment's values and eyeball the output, then diff the rendered release against the running cluster before a production-bound change. Lint and render are CI-safe and run in CI; the cluster diff requires cluster access and is operator-run.

6. **`helm install` / `helm upgrade` is operator-confirmation-only**: surface the exact upgrade command for the operator (release, chart, environment values, namespace). Do NOT run an install or upgrade against any live environment from inside the worker session.

## Inputs

- The spec's §Requirements and the deploy-target details (namespace, cluster, env scope).
- The existing charts tree for convention-matching (chart structure, label conventions, probe defaults).
- The secret-manager paths (if the change references new secrets — they must be pre-created by an `opentofu-aws-modules` step).

## Outputs

No artifact. This skill emits prescriptive guidance inline. The implementing agent writes the chart and values files in the assigned IaC worktree.

## Quality bar

- [ ] Every chart has `values.yaml` (defaults) + a values file per target environment.
- [ ] Every secret accessed via external-secrets; no inline secret values in values files.
- [ ] Every `Deployment` has `readinessProbe` + `livenessProbe`; `startupProbe` for slow-starting workloads.
- [ ] Every `Deployment` has `resources.requests` + `resources.limits` (CPU + memory).
- [ ] `PodDisruptionBudget` present for production workloads (`minAvailable: 1`).
- [ ] RBAC granted minimally — no `cluster-admin` ClusterRoleBinding without an explicit authorizing spec line.
- [ ] The chart lints clean; the templates render without errors.

## Common Rationalizations

| Rationalization | Why it's wrong | What to do instead |
|---|---|---|
| "Environment-specific secrets are small; I'll just put them in `values.yaml` for convenience." | Values files are committed to version control. Any secret in `values.yaml` — even non-production credentials — becomes part of the git history forever, invalidating the secret and potentially exposing it to anyone with repo access. | Reference secrets via the external-secrets operator (`ExternalSecret` CRD + a SecretStore); secrets live in the secret manager, not in any values file. |
| "Resource limits slow down development; I'll add them before production." | Without CPU and memory limits, a single misbehaving pod can exhaust node resources and evict neighboring pods — a lower-environment incident with no limits can mimic production load but hide OOM-kill patterns that only appear at limit-time. | Set `resources.requests` and `resources.limits` in every Deployment template from the first change; use the spec's SLA and baseline load estimates to size them. |
| "The readiness probe is optional for a background worker — it's not serving HTTP." | Without a readinessProbe, the orchestrator treats the pod as ready immediately after container start. If the worker's queue or DB connection is still initializing, it begins processing jobs before its dependencies are healthy, causing spurious failures. | Add a readinessProbe for every workload — an HTTP `/healthz` for API pods, an `exec` command that verifies queue connectivity for worker pods. |
| "I'll bump `Chart.yaml` version after the feature is stable — version bumps cause noise in diffs." | Helm's upgrade idempotency tracking depends on `Chart.yaml`'s `version` field. A chart deployed without a version bump is indistinguishable from the previous release in `helm history`, making rollbacks and incident timelines ambiguous. | Bump `Chart.yaml` `version` (semver patch) in every change that touches a chart template or default value; version noise in diffs is a feature, not a bug. |

## Skills this one composes with

- `opentofu-aws-modules` — when a chart values file references an AWS resource (an IAM role ARN, an S3 bucket name, an RDS endpoint) provisioned by OpenTofu. The two are sequenced: OpenTofu provisions the resource first; the chart references the output.

## Anti-patterns

- Never inline secrets in values files — not even for a lower environment. The external-secrets operator resolves them; inline values create audit-trail and rotation problems.
- Never run an install or upgrade against a live environment from inside the worker session. Surface the command; the operator runs it.
- Never grant a `cluster-admin` ClusterRoleBinding without an explicit authorizing spec line and a named justification.
- Never skip probes "to be added later" — probes missing at deploy time mean the orchestrator has no way to know if the pod is healthy. A pod without a readiness probe is immediately added to load-balancer rotation regardless of startup state.
- Never modify deployed chart values without diffing against the running cluster first to understand the delta.
- Never use the `default` namespace for application workloads. Every workload has a named namespace defined in the IaC repo's namespace manifest.
