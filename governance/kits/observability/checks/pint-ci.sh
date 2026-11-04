#!/usr/bin/env bash
# FG-OBS-002: lint Prometheus rules with pint. Config: .pint.hcl, minimal example below.
#   rule {
#     match { kind = "alerting" }
#     annotation "runbook_url" { required = true }
#     label "severity" { required = true }
#   }
set -euo pipefail
pint --config .pint.hcl ci
