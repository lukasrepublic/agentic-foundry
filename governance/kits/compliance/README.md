# compliance kit

Licensing and provenance only. This kit carries no compliance-framework control text. Rules FG-LIC-001..003:
a license policy (allowlist by category, per-package exceptions, deny unknown) with a Python check, REUSE/SPDX
headers where the blueprint requires them, and an SBOM per release.

Check the policy: `python3 checks/license_policy_check.py templates/license-policy.yaml packages.json`.
The script needs PyYAML (it reads the policy as YAML). Expressions with parentheses are refused (exit 2), not guessed.
