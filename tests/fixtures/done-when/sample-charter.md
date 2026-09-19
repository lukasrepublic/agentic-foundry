# Charter: fixture atom for the done_when/escalate_when parser

**Date:** 2026-09-19 · **Mode:** noninteractive · **Repo:** fixture-only

## Goal
A fixture charter — never authored/authorized for real. Exercises the leniency of the tick
prompt's markdown-list parser (`foundry_command_deck_watch._parse_markdown_list_section`)
against a `## Done when` / `## Escalate when` pair, including a bullet that uses `*` instead of
`-` and one with extra leading whitespace.

## Acceptance criteria
- WHEN the parser is fed this file, THE PARSER SHALL return both lists non-empty.

## Done when
- test:tests/test_command_deck_watch.py::test_fixture_charter_parses passes
* cli:python3 scripts/foundry_command_deck_watch.py status fixture-programme --root .

## Escalate when
- external-provisioning
  - no-consensus-after-research

## Out of scope
- Real authorization of this charter.

## Scope (write boundary)
allowed_paths:
  - tests/fixtures/done-when/sample-charter.md
denied_paths: []

## Verification
- pytest tests/test_command_deck_watch.py -q → pass

## Merge
operator-merges

## Amendments
| date | what changed | why reality required it | auth_seq |
|---|---|---|---|
