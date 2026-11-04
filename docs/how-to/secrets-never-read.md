# Never read a secret value

Operating-model rule D1. Verify a secret by **metadata only**: does it exist, which version, which ARN, who may read it.
Never print it, echo it, paste it, or read-modify-write it. Write a secret from a generated temp file with a cleanup
trap. Never print an infra plan body that resolves secrets at plan time. Private key material never enters an agent's
context.

## A command-text hook is not a boundary

`hooks/foundry-secret-read-guard.py` refuses the commands whose job is to print a secret, and names the verb it
refused. It reads **command text**. It cannot see a script that does the read for you, an interpreter that opens the
file, a `$VAR` it cannot resolve, or a path it was never told about. Claude Code's own documentation says the same of
Bash deny rules: they are not a security boundary. The sandbox has no built-in credential deny list either; only the
files and variables you list are protected.

So treat the hook as a loud mistake-catcher. **Only an IAM-level boundary prevents the read.**

| If you want the agent unable to read | Remove this at the identity |
|---|---|
| AWS Secrets Manager values | `secretsmanager:GetSecretValue`, `secretsmanager:BatchGetSecretValue` |
| AWS SSM SecureString values | `ssm:GetParameter*` on SecureString paths, and `kms:Decrypt` on their key |
| Anything encrypted with a KMS key | `kms:Decrypt` |
| GCP secrets | `secretmanager.versions.access` |
| Azure Key Vault secrets | the `get` secret permission (or the `Key Vault Secrets User` role) |
| Kubernetes Secret data | `get`/`list` on `secrets` in the agent's Role |
| Vault data | the `read` capability on the path |

Give the agent its own identity with the least access that does the job, short-lived and scoped to the task, and let the
hook catch the slips on top of that.

## What the guard refuses by default

Each refusal prints the verb. The verbs are the ones whose documented output is the value itself:

- `aws secretsmanager get-secret-value`, `aws secretsmanager batch-get-secret-value`
- `aws ssm get-parameter --with-decryption` (also `get-parameters`, `get-parameters-by-path`)
- `aws kms decrypt`, `aws configure export-credentials`, `aws configure get <secret key>`
- `aws eks get-token`, `aws ecr get-authorization-token`, `aws sso get-role-credentials`,
  `aws codeartifact get-authorization-token`, `aws rds generate-db-auth-token`, `aws ssm get-parameter-history --with-decryption`
- `aws sts get-session-token`, `aws sts assume-role`, `aws iam create-access-key`,
  `aws iam create-service-specific-credential`, `aws ecr get-login-password`
- `gcloud secrets versions access`, `gcloud auth print-access-token` (and `print-identity-token`),
  `gcloud iam service-accounts keys` (everything except `list`)
- `az keyvault secret show`, `az keyvault secret download`, `az keyvault certificate download`,
  `az account get-access-token`, `az storage account keys list`, `az acr credential show`
- `vault kv get`, `vault read`
- `kubectl get secret -o yaml|json|jsonpath|go-template` (any `-o` except `name` and `wide`), `kubectl config view --raw`,
  `kubectl create token`, `kubectl view-secret` (and the `oc` forms)
- `op read`, `op item get`, `op inject`, `op document get`
- `gh auth token`, `gh auth status --show-token`
- `terraform show -json`, `terraform state pull`, `terraform state show`, `terraform output -json|-raw` (and `tofu`)
- `git credential fill`, `sops -d`, `ansible-vault view|decrypt`, `gpg --decrypt` (`-d`), `age --decrypt` (`-d`), `vault print token`,
  `doppler secrets get|download`, `kubectl get secret --template`
- `pass show`, `secret-tool lookup`, `security find-generic-password`, `gpg --export-secret-keys`
- `printenv`, bare `env`, `export -p`, bare `set` or `declare -x|-p` that dump the environment, and `echo`/`printf` of a variable named like a secret
- **No-print roots:** a reader (`cat`, `head`, `grep`, `cp`, `mv`, `scp`, `tar`, `curl -d @file`, a `<` redirect, a
  here-string, an interpreter's inline `open(...)`) pointed at one of: `~/.ssh`, `~/.aws`, `~/.config/gcloud`,
  `~/.kube`, `.env*`, `~/.git-credentials`, `~/.netrc`, `~/.docker/config.json`, `~/.npmrc`, `~/.pypirc`,
  `~/.config/gh`, `~/.claude/.credentials.json`, `~/.gnupg`, `~/.vault-token`, `~/.azure`, `~/.terraform.d`,
  `*.tfstate*`, `/proc/*/environ`. `~/x` means `x` under any user's home (`~root`, `/home/alice`). The `.env*` exempt
  suffixes are `.example`, `.sample`, `.template`, `.dist` and `.defaults`. `cp`, `mv` and `install` are judged by their
  SOURCE operands, so `cp .env.example .env` works. A here-string into a reader (`base64 -d <<< "$X"`) and a
  brace-expanded reader path (`cat ~/.ss{h,}/id_rsa`) are refused. Using a key without printing it
  (`ssh -i ~/.ssh/id_ed25519 host`, `kubectl --kubeconfig ~/.kube/config get pods`) is not a read and is admitted.
  **A reader whose target is an unknowable `$VAR` or command substitution is admitted** - that is a practice, not a
  control; the IAM boundary above is the control.

## Metadata verbs that are fine

Use these to verify. They are not refused:

- `aws secretsmanager describe-secret`, `aws secretsmanager list-secrets`, `aws ssm get-parameter` without decryption,
  `aws sts get-caller-identity`
- `gcloud secrets versions describe|list`, `gcloud iam service-accounts keys list`
- `az keyvault secret list`
- `kubectl describe secret` (prints key names and byte sizes only), `kubectl get secret -o name`
- `vault kv metadata get`, `vault status`
- `terraform plan -no-color`, `terraform state list`

Learn the traps: some "metadata" verbs print values for certain types. A `describe` of a service-account token secret, or
`aws lambda get-function-configuration` on a function whose environment holds a key, prints the value. When in doubt,
ask for a smaller field (`--query` for the name or version) rather than the whole object.

## Extending the lists (add only)

`.foundry/operating-model.yaml`:

```yaml
secrets:
  value_reads_forbidden: true            # FLOOR - cannot be false
  forbidden_verbs:                       # extra phrases: the command word, then sub-words in order
    - aws iam create-login-profile
    - mycli reveal
  no_print_roots:                        # extra paths: a directory prefix or a basename glob
    - ~/.config/op
    - "*.pem"
```

Both lists are merged **over** the built-in defaults. They can only add. A default cannot be removed: the defaults live
in the hook, an item that starts with `!` or `-` (or a `remove:` mapping) makes
`foundry_operating_model.py validate` fail with `floor-rule-disabled:secrets.forbidden_verbs.removal` (or
`...no_print_roots.removal`), `value_reads_forbidden: false` fails with `floor-rule-disabled:secrets.value_reads_forbidden`,
and a policy file the hook cannot load leaves the defaults in force and prints one `policy-additions-dropped:<reason>` line on stderr.

## When the guard refuses you

Do the same job by metadata: check that the secret exists and which version is current, and compare a **digest** or a
**version id** rather than the value. If a value really must be set, write it from a generated temp file (owner-only permissions) with
a `trap ... EXIT` that removes the file, and never print the file. If you need the value read, say so and let the operator
do it outside the agent session.
