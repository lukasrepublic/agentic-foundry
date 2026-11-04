#!/usr/bin/env bash
# FG-LIC-003: SBOM per release, from the released artifact. Pin syft/trivy per docs/standing-versions.md.
set -euo pipefail
ARTIFACT="${1:?artifact path or image ref}"
syft "$ARTIFACT" -o spdx-json=sbom.spdx.json
# Alternative: trivy image --format spdx-json --output sbom.spdx.json "$ARTIFACT"
