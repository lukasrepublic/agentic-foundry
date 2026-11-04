# security kit

Application security, supply chain, platform hardening, secrets, agent boundary, infra policy and the security-review trigger.
Rules: `rules.yaml` (FG-SEC-001..032). Floor rules: FG-SEC-026 (secret-read guard hook) and FG-SEC-032 (security reviewer).

- Checks: `checks/`. Each file names the rule ids it enforces.
  - `gitleaks.toml` (extends the scanner defaults), `semgrep-rules.yaml` (own rules with cwe and owasp metadata),
    `scorecard-map.yaml` (check to rule, with a gate flag), `asvs-level-1.yaml`, `-2`, `-3` (requirement ids only, cumulative),
    `policy/*.rego` and `conftest-bundle.yaml` (infra policy), and CI snippets `ci-*.yml.tmpl`.
- Templates: `templates/THREAT-MODEL.md`, `templates/SECURITY.md`.
- Requirements are cited by id only (`v5.0.0-c.s.r`); the standard's own text is not reproduced. Read it at the source named in each rule's `sources`.

## Using the checks

Adopters copy this kit's `checks/` directory into their repository as `security-checks/` (or reference the plugin path) and copy the
`ci-*.yml.tmpl` snippets into `.github/workflows/` as `.yml`. The snippets run from the repository root. Examples:

- `semgrep scan --metrics=off --error --config p/owasp-top-ten --config p/secrets --config security-checks/semgrep-rules.yaml`
- `conftest test -p security-checks/policy <manifests>` (from the plugin: `-p governance/kits/security/checks/policy`)
- `gh attestation verify <artifact> --repo <owner>/<repo>`

## Enabling push protection (FG-SEC-025)

On GitHub: repository Settings, Code security, Secret protection, enable "Push protection". Organization owners can enable it for all
repositories at once. Pair it with the CI scanner in `ci-gitleaks.yml.tmpl`; push protection stops the push, CI covers history and forks.

## Declarations and boundary paths

Adopter declarations live in `.foundry/governance.lock` (`declarations`): the ASVS level (FG-SEC-001), trust boundaries with their paths
(FG-SEC-007) and the SLSA level (FG-SEC-010). Paths in `declarations.trust_boundaries[]` extend the FG-SEC-032 path set once the resolver
consumes them (gv-15); until then FG-SEC-032 triggers on its own globs only.

## Planned, placeholder and deviations

- Semgrep pin: the registry packs (`p/owasp-top-ten`, `p/secrets`) cannot be pinned by tag or commit. The own rules file is versioned
  with the repo and the CLI is pinned by exact version in the snippet. The upstream `semgrep-rules` repository publishes no release tags
  (checked 2026-11-04), so pinning by tag is not possible there; this is a deviation from "pinned by tag".
- Conftest: a local policy (`checks/policy`) ships and runs with no registry. No OCI bundle is published; the `optional_remote` entry in
  `conftest-bundle.yaml` is a PLACEHOLDER.
- `hooks/foundry-secret-read-guard.py` (FG-SEC-026) is planned under gv-11 and does not exist yet.
- Level filtering is deferred to the resolver (gv-15): `render` and `applicable` ignore `level` today; every rule carries a valid `level`.
- Scorecard gating: rows marked `gate: true` fail the snippet below the threshold; other rows are reported only.
