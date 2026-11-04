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

## Operator-defined style (instruction text)

`communication.style: operator-defined` makes `render-tick-section E2` print the text of
`communication.operator_style_file`. **That text is instruction text by design**: the operator wrote it and
agents follow it, so it is returned inside a block labelled `BEGIN/END OPERATOR STYLE GUIDANCE`. To keep the
command from reading arbitrary files, the file must be a regular `.md` or `.txt` file under `.foundry/` (no
dot-file, no symlink, no FIFO), at most 4 KiB. Control and format characters are removed. An empty file is
refused with `style-file-empty`; an oversize file with `style-file-too-large`.

## YAML aliases

YAML 1.1 reads an unquoted `off` or `no` as `false`, and `on` or `yes` as `true`. Only two keys have an
`off` value: `communication.style` and `messaging.chat_mirror`. On those two keys, `false`/`no`/`off` mean `off`;
`true`/`yes`/`on` mean `on` where `on` is allowed (`chat_mirror`) and are refused for `style`. Quote the word
if you want to be explicit. On a floor key, any such alias is `floor-rule-disabled:<key>`.

## File safety

The policy file must be a regular file under the project's `.foundry/` (a symlink is refused with
`policy-path-not-regular-file`; a path that leaves the project with `policy-path-outside-project`), at most
64 KiB. A syntax error reports only the line and column.

## Rules for you

- Do not edit a floor key to a different value to make a check pass. Report it.
- Read policy only through `foundry_operating_model.load`; do not parse the YAML yourself.
- The file is seeded by the workspace scaffold. The updater keeps your edits byte-for-byte.
