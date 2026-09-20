# The shape a blocker must earn

A candidate blocker is only a real blocker when it is a JSON object with these fields (never
free-form prose):

- `claim` — the one-line assertion of what is blocked and why.
- `evidence` — a non-empty array of concrete artifacts backing the claim (a command's captured
  output, a file path, a URL, or verbatim error text).
- `attempted` — a non-empty array of what was actually tried before escalating.
- `why_operator` — one of exactly: `external-provisioning`, `credential-step`,
  `no-consensus-after-research`, `security-widening`, `irreversible-action`.
- `handoff` — required whenever `why_operator` is `external-provisioning` or `credential-step`:
  an object with `cwd` (an absolute or `~`-relative path), `command` (one bare command, no
  chaining), `why`, and `expect`.

Anything that does not carry this shape is a Next Task, not a blocker.
