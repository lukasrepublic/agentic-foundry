#!/bin/bash
# CI helper (gv-19): install conftest, opa and kyverno at pinned versions, each verified against a recorded sha256.
# Research timestamp 2026-11-05T05:00Z from the vendors' GitHub releases pages (see .github/workflows/ci.yml).
# Upgrade = a reviewed edit of the version, the URL and the hash together.
set -euo pipefail
tools="$(mktemp -d)"
bindir="${HOME}/.local/bin"
mkdir -p "$bindir"
fetch() {
  curl --proto '=https' --tlsv1.2 -fsSL --retry 3 -o "$tools/$1" "$2"
  echo "$3  $tools/$1" | sha256sum -c -
}
fetch conftest.tgz https://github.com/open-policy-agent/conftest/releases/download/v0.71.1/conftest_0.71.1_Linux_x86_64.tar.gz c3f6b2a753bd56e377a1e51a95ae3e057f926f2e9139f7d5b5dfbbaf810f7985
fetch opa https://github.com/open-policy-agent/opa/releases/download/v1.22.0/opa_linux_amd64_static 791790254c598a942eb140959bcec9a47f844535a632fd01d11d870ba2a47047
fetch kyverno.tgz https://github.com/kyverno/kyverno/releases/download/v1.19.2/kyverno-cli_v1.19.2_linux_x86_64.tar.gz 357eaf1df8bc24ddd5a73979fadeaae6a032e8710f11af264229756e4956bf4a
tar -xzf "$tools/conftest.tgz" -C "$bindir" conftest
tar -xzf "$tools/kyverno.tgz" -C "$bindir" kyverno
install -m 0755 "$tools/opa" "$bindir/opa"
if [ -n "${GITHUB_PATH:-}" ]; then echo "$bindir" >> "$GITHUB_PATH"; fi
"$bindir/conftest" --version
"$bindir/opa" version
"$bindir/kyverno" version
