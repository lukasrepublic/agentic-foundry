#!/usr/bin/env bash
# FG-OBS-002: lint Prometheus rules with pint. Pin pint per the stack kit's version-matrix template
# (governance/kits/stack/templates/version-matrix.md). Pinned-install form (placeholder):
#   go install github.com/cloudflare/pint/cmd/pint@v<version>
# .pint.hcl, minimal example. The SHOULD-level items report as warnings:
#   rule {
#     match { kind = "alerting" }
#     annotation "runbook_url" { severity = warning
#                                required = true }
#     label "severity"         { severity = warning
#                                required = true }
#   }
set -euo pipefail
pint --config .pint.hcl ci
