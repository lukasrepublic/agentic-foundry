# Charter: handbook — governance section, one operating mode, adopter template, guides

**Date:** 2026-11-04 · **Mode:** noninteractive · **Repo:** agentic-handbook · **Base:** main (squash merges only)

## Goal
The adopter-facing side of governance-2-0.

- `OPERATING-MODEL.md`: replace "Operating modes" with one mode (a solo developer is a team of one; `github_scope` is part of setup); add "Governance" (what a rule, a kit, a blueprint, the adopter layer and the lock are; floor vs tunable; where each is enforced; the per-ticket Design/Security/Surfaces sections; exceptions and baselines; the shared message log in plain words).
- `CLAUDE.md` (adopter template): one paragraph pointing at `.foundry/operating-model.yaml`, `.foundry/governance.lock` and `governance/local/`; no rule text (enforcement is hooks and CI).
- `docs/guides/`: `start-a-new-project.md` and `adopt-an-existing-codebase.md` (discover → select → declare → apply → baseline, by PR), `work-each-day.md` (the four-block report, STALL, pause, before/after production lines, the Design section), `join-an-existing-team.md` (claims and leases, `github_scope.operators`, the message log), `upgrade-and-migrate.md` (the upgrade re-runs intake; what changes for existing sessions: no `cd`, `rm` only in scratch, heredocs, secret reads; the mode switch is gone), `diagnose-and-recover.md` (drift report, expired exceptions, lost claims).
- `docs/github-planning-surface.md`: one mode. `templates/ci.yml`: the governance-trace job and the comment "budgets are commands, not gates". `WORKFLOW.md` cross-links.
- `scripts/check-doc-links.sh` green; the guides' command table verified against `release/2.0.0` at the atom's head (the `check-guide-commands` practice from gn-12).

## Acceptance criteria
- WHEN the handbook is grepped for "single-operator", "team mode" or "Operating modes", THE SYSTEM SHALL return zero matches outside the changelog.
- WHEN every command the guides name is checked against the foundry head, THE SYSTEM SHALL resolve each one or mark it `pending <atom>`; zero `unresolved`.
- THE SYSTEM SHALL keep `check-doc-links.sh` green.

## Done when
- cli:bash scripts/check-doc-links.sh
- cli:test -z "$(git grep -lE 'single-operator|team mode|Operating modes' -- . ':!CHANGELOG.md')"
- file:OPERATING-MODEL.md
- cli:grep -qx '## Governance' OPERATING-MODEL.md

## Escalate when
- no-consensus-after-research

## Out of scope
- Foundry code.

## Scope (write boundary)
allowed_paths:
  - OPERATING-MODEL.md
  - CLAUDE.md
  - WORKFLOW.md
  - README.md
  - docs/
  - templates/
  - context/
  - scripts/
denied_paths: []

## Verification
- links script green; command table recorded in the PR body with counts (resolved / pending / unresolved = 0).

## Merge
agent-merges-on-green   # --squash

## Amendments
| date | what changed | why reality required it | auth_seq |
|---|---|---|---|
