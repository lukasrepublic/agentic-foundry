#!/usr/bin/env bash
# FG-OBS-001: validate the telemetry registry with OpenTelemetry Weaver, plus the owner-prefix policy.
# Custom names live under the owner prefix, e.g. "example.checkout.cart_size".
# Pin weaver per the stack kit's version-matrix template (governance/kits/stack/templates/version-matrix.md).
# Pinned-install form (placeholder):
#   curl -fsSL https://github.com/open-telemetry/weaver/releases/download/v<version>/weaver-x86_64-unknown-linux-gnu.tar.xz | tar xJ
set -euo pipefail
POLICIES="${POLICIES:-governance/kits/observability/templates/telemetry-policies}"
weaver registry check -r ./telemetry/registry
weaver registry check -r ./telemetry/registry --policy "$POLICIES"   # owner-prefix.rego
