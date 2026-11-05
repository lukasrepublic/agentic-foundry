# Charter: ticket translation and review — Design/Security/Surfaces sections, lenses, governance-trace CI, records

**Date:** 2026-11-04 · **Mode:** noninteractive · **Repo:** agentic-foundry · **Base:** release/2.0.0

## Goal
Design §5.3–§5.4; port prompt rules D3, G2, G3, C2. Grounding: Spec-Kit plan gates citing the constitution by article, Kiro design-then-tasks, MADR Confirmation, Google design-doc review escalation.

- `scripts/foundry-ticket.py start`: parses `## Design` (components, reused pattern, cited rule ids), `## Security` (SEC ids or `none: <why>`), `## Surfaces` (one line per blueprint surface or `n/a: <why>`); the design-required class (paths in `governance.design_required_paths` ∪ the blueprint's, SEC paths, size tag `L`) refuses to start without a Design citing ≥ 1 existing id (`design-required`); below the class the sections are a checklist. `read-issue --json` exposes the parsed sections and the applicable ids.
- Design review: `skills/dispatch` step 0 for the design-required class — a fresh-context `spec-reviewer`-style pass (persona `agents/design-reviewer.md`, read-only) over the Design against the rendered governance; Block or clear; the operator gives one word only on a Block the agent cannot fix.
- Review lenses: `agents/pr-reviewer.md` gains the conformance lens (Block: deviation from a cited id; a governed path with no Design; a missing Surfaces line); `agents/security-reviewer.md` triggers on declared boundary paths; both receive the brief renderer's rule block.
- CI `governance-trace` (`.github/workflows/btb-gates-base.yml` job template shipped in `governance/kits/delivery/checks/`): PR names a ticket; cited ids exist; the diff's paths fall within the Design's components; `governance/rendered/` not stale; no expired exception.
- G3: `foundry-authorize.py` refuses when the spec or contract has uncommitted changes. G2: the merge step (`queue-land` / `merge-when-green`) writes the delivered-by record (PR, merger login, head sha) in the same action as the merge; authorship alone never proves attestation (documented). C2: `scripts/foundry_ci_budget_lint.py` flags a performance or request-count budget wired as a required check.

## Acceptance criteria
- WHEN a ticket in the design-required class lacks a Design or cites an unknown id, `start` SHALL refuse with `design-required` naming the missing piece; a cited existing id SHALL admit. (rules A4, D3)
- WHEN a diff touches a path outside the Design's components, `governance-trace` SHALL fail naming the path (control: inside passes).
- WHEN the spec tree is dirty, `foundry-authorize.py` SHALL refuse with `commit-before-authorize`. (rule G3)
- WHEN a merge completes through `queue-land`, the delivered-by record SHALL exist with the merger login and head sha (stubbed gh). (rule G2)
- WHEN a CI workflow fixture wires a budget check as required, the budget lint SHALL flag it; as a non-required report it SHALL pass. (rule C2)
- AC-OM-10 THE SYSTEM SHALL generate a traceability table mapping every port-prompt rule (A1..G3) to ≥ 1 AC in this programme's charters (`scripts/foundry_om_trace.py`), and the test SHALL fail on an unmapped rule.

## Done when
- test:tests/test_ticket_design_sections.py
- test:tests/test_governance_trace.py
- test:tests/test_om_trace.py
- file:agents/design-reviewer.md
- file:scripts/foundry_om_trace.py

## Escalate when
- no-consensus-after-research

## Out of scope
- Kit content; the deck.

## Scope (write boundary)
allowed_paths:
  - scripts/foundry-ticket.py
  - scripts/foundry-authorize.py
  - scripts/foundry_queue_land.py
  - scripts/foundry-merge-when-green.py
  - scripts/foundry_ci_budget_lint.py
  - scripts/foundry_om_trace.py
  - agents/pr-reviewer.md
  - agents/security-reviewer.md
  - agents/design-reviewer.md
  - skills/dispatch/SKILL.md
  - governance/kits/delivery/checks/
  - .github/workflows/btb-gates-base.yml
  - .github/workflows/ci.yml
  - tests/test_ticket_design_sections.py
  - tests/test_governance_trace.py
  - tests/test_om_trace.py
  - tests/test_ticket_first.py
  - tests/test_authorize*.py
  - tests/fixtures/tickets/
  - docs/permission-floor.json
  - cli/permission-floor.json
  - tests/test_permission_floor_map.py
  - tests/fixtures/floor-drift-corpus.json
  - hooks/foundry-plugin-scripts-allow.py
denied_paths: []

## Verification
- pytest green; CI workflow edits SHA-pinned; fresh-context security-reviewer pass (touches .github/ and agents/).

## Merge
agent-merges-on-green

## Amendments
| date | what changed | why reality required it | auth_seq |
|---|---|---|---|
| 2026-11-04 | Scope adds `governance/kits/conduct/rules.yaml` (fix the dangling `check.ref: skills/pr-reviewer` → `agents/pr-reviewer.md`); the conformance, flag-cleanup (FG-DLV-008) and rollout (FG-OPS-008) lenses the quality/delivery/operations kits bind to `agents/pr-reviewer.md` are delivered here. | gv-05's review found the dangling ref in the merged conduct kit; gv-06's rules bind lenses that only this atom adds. | 1 |
| 2026-11-05 | Scope adds `hooks/hooks.json` (ownership transfers from gv-11, merged: wire the paper guard into the Bash PreToolUse group so its new Bash write-target coverage is live) and `.claude-plugin/plugin.json` (one line registering `agents/design-reviewer.md`; the agent-hardening registry test requires it). Rule B7 is mapped by gv-13's amended AC. `scripts/foundry_research_note.py` ruled-path ownership check (gv-14 follow-up e) moves to gv-19. | the builder parked both needs in open_items; the Bash coverage is inert without the wiring. | 1 |
| 2026-11-05 (b) | Scope adds `hooks/foundry-paper-guard.py` and `scripts/foundry_ticket_store.py` (the routed gv-10/gv-11 follow-ups b and d live there), `tests/test_paper_guard*.py`, `tests/test_claims_expiry.py`, `tests/test_hooks_guards.py`, `tests/test_plugin_scripts_allow.py`, and the shared gh stubs under `tests/`. The HMAC on `ci_allowed`/`issue_paper_allowed` is a VISIBILITY control (the key is created by the allow-tier `start`; the operator's own test pass bounds it) and the docstrings say so. | the deck routed those follow-ups into this atom without widening the path list; the security review flagged the unchartered files and the over-claimed signing. | 1 |
