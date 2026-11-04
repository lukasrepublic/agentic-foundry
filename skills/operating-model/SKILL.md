---
name: operating-model
description: Show, validate or explain the operating-model policy (/foundry:operating-model status|validate|explain <key>). The policy file .foundry/operating-model.yaml holds the tunables and the floors of the operating rules; no file means every default. A floor rule cannot be turned off. Trigger on "operating model", "operating-model policy", "floor rule", "max_builders", "report format", "communication style".
---

# /foundry:operating-model

The workspace policy file is `.foundry/operating-model.yaml`. It is optional. A workspace with no file gets
every default. The file has two kinds of key:

- **Tunable** keys carry a default. You may change them inside their allowed values (for example
  `deck.max_builders`, `report.max_lines`, `communication.style`).
- **Floor** keys are enforced rules. You can only write them as their enforced value (for example
  `merge.record_line_before_enqueue: true`, `secrets.value_reads_forbidden: true`). Any other value is
  refused with `floor-rule-disabled:<key>`.

An unknown key, a wrong type or a malformed file is also refused, with the key named. The reader never
replaces a broken file with defaults.

## Commands

Run the script from the plugin root. It is read-only.

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_operating_model.py" status
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_operating_model.py" validate
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry_operating_model.py" explain deck.max_builders
```

- `status` prints each key with its effective value, `floor` or `tunable`, and `source: default | file`.
  When `communication.style` is `off`, it says so.
- `validate` exits 0 when the file is valid or absent. It exits 2 and names every error
  (`floor-rule-disabled:<key>`, `unknown-key:<key>`, `invalid-value:<key>`, `malformed:<reason>`).
- `explain <key>` prints the rule, why it exists and the primitive that enforces it.
- `render-tick-section <A1|A5|A6|B1|B8|B9|C1|D2|E1|E2>` prints the policy-driven text of one section for the
  tick template and the brief. Optional `--session-size N` and `--prompt-size N` let section B1 and B8 show the
  smallest size: `min(deck.max_builders, session size, prompt size)`.

## Rules for you

- Do not edit a floor key to a different value to make a check pass. Report it.
- Read policy only through `foundry_operating_model.load`; do not parse the YAML yourself.
- The file is seeded by the workspace scaffold. The updater keeps your edits byte-for-byte.
