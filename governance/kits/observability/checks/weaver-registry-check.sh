#!/usr/bin/env bash
# FG-OBS-001: validate the telemetry registry with OpenTelemetry Weaver.
# Custom names live under the owner prefix, e.g. "acme.checkout.cart_size". Pin the weaver version per standing-versions.
set -euo pipefail
weaver registry check -r ./telemetry/registry
weaver registry check -r ./telemetry/registry --policy ./telemetry/policies   # owner-prefix policy, rego
