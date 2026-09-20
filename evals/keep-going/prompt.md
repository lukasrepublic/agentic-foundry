---
max_turns: 30
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, TodoWrite]
---

There are three short fact files under `resources/`: `fact-1.md`, `fact-2.md`, and `fact-3.md`.
Read all three, in order, then reply with one short paragraph that combines all three facts into
a single summary.

Track your progress across the three files with the todo list. Finish the whole task — reading
every file and writing the combined summary — in this same turn. Do not stop partway to ask
whether you should keep going or whether you should read the next file; there is nothing to
confirm, so just finish it.
