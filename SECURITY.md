# Security policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately via **GitHub Security Advisories**
("Report a vulnerability" on this repo) rather than public issues. You'll get an
acknowledgment within 72 hours and a status update within 14 days. Coordinated disclosure
is appreciated; credit is given unless you prefer otherwise.

In scope: the plugin's hooks (especially the git-discipline guard), scripts, CI workflow
templates, and any path where the plugin handles credentials, executes commands, or
mediates merges. Prompt-injection vectors against the shipped skills/agents are in scope.

## Supported versions

| Version | Supported |
|---|---|
| latest minor (currently 0.25.x) | ✅ |
| older | ❌ — upgrade path in CHANGELOG.md |

## What this project's security posture is (and is not)

The threat model is documented, honestly, in [docs/DESIGN.md](docs/DESIGN.md) and
[docs/merge-floor.md](docs/merge-floor.md) — including which guarantees are server-side
(Tier A), which are client-side and therefore bounded (Tier B, labeled advisory), and what
is deliberately left to the human operator. Supply-chain hygiene: all GitHub Actions are
SHA-pinned; releases are pinned to exact tag commits in the marketplace manifest; the
changelog records a security-review disposition per release.
