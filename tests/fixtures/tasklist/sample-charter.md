# Charter: fixture atom for the manifest-to-tasklist projection

**Date:** 2026-09-19 · **Mode:** noninteractive · **Repo:** fixture-only

## Goal
A fixture charter — never authored/authorized for real. Copied byte-for-byte into a throwaway
temp-git-repo project dir by `tests/test_manifest_to_tasklist.py`, which then independently decides
whether to `git add`/`git commit` its copy (exercising both the AUTHORIZED and the un-committed
`AC-MTL-3` branch from the SAME source text).

## Acceptance criteria
- WHEN the projection is fed a release manifest naming this charter, THE PROJECTION SHALL read its
  `## Done when` / `## Escalate when` / `## Scope (write boundary)` sections for the task
  description.

## Done when
- test:tests/test_manifest_to_tasklist.py passes
- cli:python3 scripts/foundry-manifest-to-tasklist.py <release-id>

## Escalate when
- no-consensus-after-research

## Out of scope
- Real authorization of this charter.

## Scope (write boundary)
allowed_paths:
  - tests/fixtures/tasklist/sample-charter.md
denied_paths: []

## Verification
- pytest tests/test_manifest_to_tasklist.py -q → pass

## Merge
operator-merges

## Amendments
| date | what changed | why reality required it | auth_seq |
|---|---|---|---|
