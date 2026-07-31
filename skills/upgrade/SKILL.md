---
name: upgrade
description: 'Post-plugin-update config hygiene (/foundry:upgrade). After `claude plugin update foundry@<marketplace>`, answers the two questions nothing else in Foundry could: has my adopter config drifted since it was set up, and is it even well-formed? Drives scripts/foundry-config.py — `check` reports per-key drift against a recorded baseline plus JSON-Schema validity (read-only, writes nothing), and `adopt` records the baseline. NEITHER verb ever writes .claude/foundry-operators.json or .claude/foundry-project.json — drift is reported, the operator edits by hand. Trigger after a plugin update, when the operator says "/foundry:upgrade", "did my config drift", "check my foundry config", or to record the baseline for the first time.'
---

# /foundry:upgrade — post-update config hygiene

**The name is kept deliberately.** There is no `upgrade` *verb* — but this is where an operator arrives after
`claude plugin update`, asking *"is my setup still right?"*. Discoverability at that moment matters more than
nominal symmetry with the verb names.

> **Historical note.** This skill was previously a tombstone: it described a gate-wiring-pin auto-heal that was
> retired (`foundry-wiring-hash.py`, `foundry-merge-gate.py`, and
> `foundry-doctor.py --heal`, now a documented no-op). That machinery is still gone. The skill now has a real
> job again — adopter-config drift, which is a different concern entirely.

## What it does

Foundry seeds two config files and then the adopter owns them:

| File | Role |
|---|---|
| `.claude/foundry-operators.json` | **Identity.** The front-authorization floor resolves `operator_id` against it. |
| `.claude/foundry-project.json` | **Project inventory.** Generic primitives read repo paths / boot recipes from here instead of hardcoding them. |

Until now nothing recorded what was written, and neither file had a schema — so *"has this changed?"* and
*"is it valid?"* were both unanswerable. `/foundry:doctor` only checks that the registry **resolves**; a
registry whose entries are missing every field but the key still passes.

## Procedure

1. **Report drift + validity** (read-only — writes nothing, including the baseline):
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-config.py" check
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-config.py" check --json   # machine-readable
   ```
   Exit codes follow `terraform plan -detailed-exitcode`:
   - **0** — every managed file is baselined, every key `clean`, both files schema-valid.
   - **2** — findings: drift, a schema violation, **or an unbaselined file**. A fresh install never exits 0.
   - **1** — error: a managed file unreadable/unparseable, or the baseline present but corrupt (which is
     never silently treated as "no baseline" — that would discard the drift a valid baseline would surface).

   Each drifted key is reported by **JSON Pointer** with its class — `local-edit`, `local-addition`,
   `locally-removed` — so one file can carry several classes at once. Comparison is **structural**: a
   re-indent or key reorder is not drift. Arrays compare as one atomic value.

2. **Record the baseline** (first run, or after deliberate edits you want to bless):
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-config.py" adopt --yes
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-config.py" adopt --yes --force   # replace an existing one
   ```
   `adopt` refuses (non-zero) without `--yes`, refuses to replace an existing baseline without `--force`, and
   refuses to record a baseline from a schema-invalid file. Replacing an existing baseline **prints what it is
   about to bless first** — otherwise `--force` is a laundering step that makes a pre-existing bad edit
   indistinguishable from a legitimate one forever.

3. **Remediate — the OPERATOR edits, not the agent.** Drift is a report, and remediation is a human step.
   **An agent running this skill MUST NOT edit `.claude/foundry-operators.json`**: key membership under
   `operators` is sufficient for `resolve_operator`, so writing that file mints an authorizer, which is exactly
   why the tool structurally refuses to. Report the drift and let the operator edit it. (For
   `.claude/foundry-project.json` — no authorization role — an agent edit is ordinary work, at the operator's
   request.) Then either fix the drift or `adopt` to bless it. Commit
   `.claude/foundry-config-baseline.json` — it is shared team state, like the config it tracks.

4. **Sanity-check the plugin itself** — unchanged, and a separate concern:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-doctor.py"
   ```

## What it does NOT do

- **It never writes either managed config file.** No verb, no flag. That is the atom's shape, not a policy
  setting: `resolve_operator` treats key membership in `operators` as sufficient, so any automated write into
  the registry would be a write primitive into the authorization substrate. There is no such path here.
- **It cannot tell you a value is *stale***, only that *you* changed it. Staleness would need a canonical
  shipped default document, which the plugin does not have (`/foundry:init` is an agent-driven skill, and
  `foundry-project.json` is inherently project-specific). Deferred, deliberately.
- **It does not run automatically.** No hook, no SessionStart. Mutating or nagging about adopter config unasked
  is the class of surprise the floor exists to prevent.

## Anti-patterns

- **Treating a `check` exit 0 as proof the plugin is healthy.** It checks adopter config; `/foundry:doctor`
  checks the plugin's five structural invariants. Different surfaces.
- **Running `adopt --force` to make a red `check` go green.** That blesses the drift instead of reviewing it.
  Read the plan it prints first — that is why it prints one.
- **Gitignoring the baseline.** It belongs in the repo with the config it tracks; otherwise every clone is
  unbaselined.
