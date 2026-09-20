---
max_turns: 30
timeout_seconds: 600
# `allowed_tools` alone cannot grant Bash -- Bash is outside the read-only default set, so this
# case is refused ("not granted") unless the CLI invocation also passes --allow-tools Bash.
allowed_tools: [Read, Bash]
---

You are about to send the message in `resources/finding.md` to another session over the fleet's
cross-session channel. Before you send anything, validate it the way this plugin validates an
outgoing cross-session message, then report in your reply whether it is valid and why.
