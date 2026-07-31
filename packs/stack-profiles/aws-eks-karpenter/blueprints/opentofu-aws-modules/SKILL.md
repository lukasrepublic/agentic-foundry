---
id: opentofu-aws-modules
covers: ["opentofu-terraform-modules", "aws-resource-authoring", "tfstate-backend", "secret-references", "provider-pinning"]
parametrizes_from: ["infra_binding.plan", "infra_binding.apply"]
---

## When to trigger

- Authoring or modifying an OpenTofu/Terraform module or an env-scoped root in the IaC repo.
- Adding, changing, or removing an AWS resource declaration.
- Changes to state-backend configuration.
- Authoring or modifying env-scoped tfvars (per-environment value sets).
- A provider version bump affecting resource schemas.
- Secret-reference pattern decisions (a secret manager vs. a parameter store vs. an external-secrets path).

## Procedure

1. **Determine env scope**: the target environment (or a shared/cross-env scope). Every module edit is env-scoped; never touch two environments in a single change unless the spec explicitly authorizes it. Default to the lowest environment first and promote upward.

2. **Place the module**: child modules under the IaC repo's reusable-module path (env-agnostic); root modules under the per-environment instantiation path. Read the existing directory layout and follow it before introducing new paths.

3. **Naming convention**: follow the IaC repo's documented resource-naming pattern (typically `<env>-<resource-type>-<purpose>`). The formal naming spec lives in the IaC repo's conventions; match the pattern of existing resources rather than inventing a new one.

4. **Secret references**: always resolve secrets through a `data` source against the secret manager or parameter store. Never declare secret values inline in `.tf` files or `*.tfvars`. The `data "<type>" "<name>" { name = var.secret_name }` pattern is required; the secret name is an input variable, never hard-coded.

5. **Format, validate, plan**: run the formatter (idempotent — always before committing), then the validator (catches schema errors), then the read-only plan in the env root being modified. The active profile's read-only plan command is `{{ profile.infra_binding.plan }}` — run it to compute the change delta; do not invent a plan invocation.

6. **Plan output in the change record**: for every environment-bound change, attach the full plan output to the change record / PR body under a `## Plan output` section. The reviewer reads the plan before approving. This is mandatory.

7. **Apply is operator-confirmation-only**: the framework does NOT apply from inside a worker session. Surface the exact mutation command for the operator — the active profile's apply command is `{{ profile.infra_binding.apply }}` — and let the operator (or the GitOps controller, per the repo's realization model) own execution. The plan confirms intent; the operator owns the mutation.

## Inputs

- The spec's §Requirements and any infra-design sections.
- The existing IaC repo for convention-matching (module structure, naming, variable conventions).
- The account-level constraints (region, network CIDRs, account identity) defined by the IaC repo's bootstrap docs.

## Outputs

No artifact. This skill emits prescriptive guidance inline. The implementing agent writes the OpenTofu/Terraform files in the assigned IaC worktree.

## Quality bar

- [ ] Every module has versioned provider declarations (e.g., `required_providers { aws = { source = "hashicorp/aws", version = "~> 5.0" } }`) — no floating `latest`.
- [ ] Every secret accessed via a `data` source — never declared as an inline literal in `.tf` or `*.tfvars`.
- [ ] Every environment-touching change has a `## Plan output` block in the change record.
- [ ] State files (`terraform.tfstate`, `*.tfstate.backup`) never manually edited and never committed.
- [ ] `*.tfvars` files containing env-specific values never committed; values managed via the secret manager or an approved external mechanism.
- [ ] The formatter is run immediately before commit — no formatting diff in the change.
- [ ] The validator passes before the change is marked ready for review.

## Common Rationalizations

| Rationalization | Why it's wrong | What to do instead |
|---|---|---|
| "The plan output looks fine from the last run; I'll apply directly without re-running the plan." | Plans are point-in-time snapshots. Between the last plan and the apply, another operator may have applied a change, or a resource's real-world state may have drifted. Applying a stale plan can destroy or recreate unexpected resources. | Always re-run the plan immediately before the apply in the same session; treat a plan older than 30 minutes as stale. |
| "Hardcoding the account ID in the module saves a variable — it only runs in one account anyway." | A hardcoded account ID couples the module to a specific account; running it in a second account (or a future region) requires a code change rather than a variables override. It also leaks the account ID into version control. | Reference the account ID via `data.aws_caller_identity.current.account_id` or a variable; never hardcode a 12-digit account ID in module source. |
| "The state bucket is already set up; I don't need to add server-side encryption to the backend config." | An unencrypted remote state backend stores state in plaintext — state often contains resource ARNs, secrets (from values that were mismarked non-sensitive), and endpoint strings. Encryption at rest is a compliance requirement, not a preference. | Declare `encrypt = true` in every `backend "s3"` block; enforce bucket SSE via a bucket policy + a server-side-encryption configuration resource. |
| "The module source is from a trusted registry; I don't need to pin the version." | Unpinned module versions silently pull breaking changes on the next init. A module that worked on day one may produce a destructive diff on day 30 because the upstream maintainer bumped a major version. | Pin every module source with an explicit `version = "x.y.z"` constraint; review the changelog before bumping the pin in a dedicated change. |

## Skills this one composes with

- `helm-k8s-patterns` — when the IaC change provisions an AWS resource (an RDS instance, an S3 bucket, an IAM role) that a Helm chart then references via values or an external-secrets path. The two are sequenced: OpenTofu provisions the resource first; the chart references the output.

## Anti-patterns

- Never apply against a live environment from inside the worker session. Surface the apply command for the operator (or let the GitOps controller reconcile, per the repo's realization model).
- Never edit `terraform.tfstate` or `*.tfstate.backup` manually. State corruption is unrecoverable without the remote backend.
- Never commit `*.tfvars` containing real env values. Use secret-manager `data` sources or an approved values-injection mechanism.
- Never use `count` for resource sets that have stable identity (e.g., IAM roles, S3 buckets). Use `for_each` with a string key for stable resource addresses in state.
- Never share a child module between environments without explicit env-parameterization via input variables. A module that silently inherits its environment from a hard-coded default is a blast-radius risk.
- Never add a `depends_on` override without understanding why the implicit dependency graph is failing. A hidden `depends_on` creates ordering surprises.
