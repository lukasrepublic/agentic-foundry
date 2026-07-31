---
name: index
description: List the foundry machinery — a read-only catalog of every skill, playbook, and agent with how to trigger it (/foundry:index). Derives name → machinery → trigger from the skills/agents frontmatter, computed on demand so it never drifts. Trigger when the operator asks "what can I run", "list the foundry machinery / skills / commands", "show the catalog / index", "what playbooks are there", "/foundry:index".
---

# /foundry:index — the machinery catalog

A thin, **read-only** index of every piece of foundry machinery — each `skill`, `playbook`, and `agent`
with its trigger — so the operator can see *what exists and how to invoke it*. It is the operational face
of the [Terminology Standard](../../docs/TERMINOLOGY.md): **verb → machinery → example prompt**.

It **derives** the catalog from the `skills/*/SKILL.md` + `agents/*.md` frontmatter at run time (the source
of truth), so it can never go stale — there is no committed catalog to maintain. It is **not a driver**: it
lists; it never runs anything.

## When to trigger

- "what can I run?", "list the foundry machinery / skills / commands", "show the catalog / index",
  "what playbooks are there?", "/foundry:index".

## Use

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-index.py"          # rendered markdown catalog
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-index.py" --json   # the data form
```

Output is a `name | machinery | trigger` table (machinery ∈ `skill` / `playbook` / `agent`) plus a
one-line count summary. The `trigger` column is the first segment of each item's `description` — where, per
the standard, the canonical verb-object and natural-language trigger phrases live. To trigger an item, use
its verb-object (`/foundry:<name>`) or one of its natural-language phrases.

## What it does NOT do

- **It never invokes anything** — it is a read-only catalog, not a runner.
- **It maintains no stored list** — the catalog is computed from frontmatter on every run, so it cannot
  drift from the actual machinery.
