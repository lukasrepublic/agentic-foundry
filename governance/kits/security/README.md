# security kit

Application security, supply chain, platform hardening, secrets, agent boundary, infra policy and the security-review trigger.
Rules: `rules.yaml` (FG-SEC-001..032). Floor rules: FG-SEC-026 (secret-read guard hook, shipped by the hook layer) and FG-SEC-032 (security reviewer).

- Checks: `checks/` (gitleaks base config, `scorecard-map.yaml`, `semgrep-ruleset.yaml`, `conftest-bundle.yaml`, CI snippets `*.yml.tmpl`). Each names the rule ids it enforces.
- Templates: `templates/THREAT-MODEL.md`, `templates/SECURITY.md`.
- Requirements are cited by id only (`v5.0.0-c.s.r`); the standard's own text is not reproduced here. Read it at the source named in each rule's `sources`.
