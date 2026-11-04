# Review checklist (one round, separate context)

- Conformance: read the ticket's cited requirement ids. Does the diff satisfy each one? Name any it does not.
- Tests: does each changed behavior have a test that fails without the change?
- Style: leave it to the linter and formatter. Do not comment on what a tool enforces.
- Commits: commit messages follow Conventional Commits (commitlint passes).
- Scope: the diff stays inside the ticket's allowed paths.
- Flaky tests: a quarantine entry has an owner, an issue, an entry date and an expiry.
