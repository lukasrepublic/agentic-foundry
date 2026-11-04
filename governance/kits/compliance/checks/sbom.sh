#!/usr/bin/env bash
# FG-LIC-003: SBOM per release, from the released artifact.
# Pin syft/trivy per the stack kit's version-matrix template (governance/kits/stack/templates/version-matrix.md).
# Pinned-install form (placeholder):
#   curl -sSfL https://raw.githubusercontent.com/anchore/syft/v<version>/install.sh | sh -s -- -b /usr/local/bin v<version>
set -euo pipefail
ARTIFACT="${1:?artifact path or image ref}"
syft "$ARTIFACT" -o spdx-json=sbom.spdx.json
# Alternative: trivy image --format spdx-json --output sbom.spdx.json "$ARTIFACT"
