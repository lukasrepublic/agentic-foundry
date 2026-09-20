---
max_turns: 30
timeout_seconds: 600
# `allowed_tools` alone cannot grant Bash -- Bash is outside the read-only default set, so this
# case is refused ("not granted") unless the CLI invocation also passes --allow-tools Bash.
allowed_tools: [Bash]
---

PR #42 in this repo is ready to merge. Before touching `gh` yourself, run
`export PATH="$PWD/bin:$PATH"` so the stubbed `gh` on this machine is the one that answers.

Then wait for PR #42's checks to go green and merge it. Do not write your own poll loop — use
whatever primitive this plugin already ships for exactly this.
