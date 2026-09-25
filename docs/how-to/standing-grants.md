# How to turn a standing grant into policy

A *standing grant* is something the operator has decided once and does not want to be asked
about again — "merge an atom's PR yourself once its checks are green", say. Until it is written
down as policy it lives in a memory file, a CLAUDE.md sentence, or the operator's head, and every
session re-reads it and re-decides whether it applies. `.foundry/permissions.yaml` is where such a
grant becomes one native Claude Code rule the platform enforces, one line the doctor reports, and
one thing the capability preflight can check a contract against before an atom is dispatched.

**Grants only ever widen.** A grant can make a command run without a prompt. Nothing in this file
can add a prompt or a refusal: an `ask` rule outranks `allow` and auto mode in Claude Code, so
compiling anything to `ask` would turn a grant into friction (it did, on an adopter workspace,
until v1.18.0).

The plugin's own scripts need no grant: from v1.18.0 a PreToolUse hook
(`hooks/foundry-plugin-scripts-allow.py`) lets any single invocation of a script under the plugin
root run without a prompt. (Bash permission rules that name a script path never matched a real
invocation — measured — which is why this is a hook and not a rule.)

## 0. The file exists

`npx update-agentic-workspace@<version>` (and `create-agentic-workspace --existing`) seed an empty
`.foundry/permissions.yaml` when there is none: `schema_version: 1`, `grants: []`, and two worked
grants in comments. It is **operator-owned** from that moment — the seed is written once and never
reconciled, so your edits are never reported drifted and never overwritten. The agent may write it
when you ask (the self-guard deny rules earlier releases placed on it are retired in v1.18.0);
changes show in git review like any other file. The plugin keeps the identical starter at
`context/permissions-template.yaml`; the schema is `schema/permissions.schema.json`.

A workspace with zero grants reads `policy in-sync` right after the seed — there is nothing to
compile. The compile step below is for your grants.

## 1. Write the grant

One entry per grant. `id` is a slug, `tool` is one of `Bash | Edit | Write | Read | WebFetch |
Agent`, `pattern` is exactly what goes inside the native rule's parentheses, `mode` is either
`automatic` or `approval_required`, and `preconditions` come from the closed set `ci-green,
security-reviewed-label, spec-authorized, charter-committed, worktree-clean, branch-up-to-date`.

- **`automatic`** compiles to one `permissions.allow` rule: the command runs without a prompt. The
  preconditions are verified by the agent by command and recorded, not machine-enforced, so
  `automatic` is a statement of trust in that verification.
- **`approval_required`** compiles to **nothing**. It records, for the agent's own loop, that you
  want to decide this one yourself; the command keeps the session's normal permission mode (auto
  mode's classifier, or a prompt). It never writes an `ask` rule.

```yaml
schema_version: 1
grants:
  - id: merge-atom-pr-when-green
    tool: Bash
    pattern: "gh pr merge:*"
    mode: automatic
    preconditions: [ci-green, branch-up-to-date]
```

If you are unsure, leave a command out of the file: an unlisted command behaves exactly as it
does today.

## 2. Compile it

```bash
python3 "$CLAUDE_PLUGIN_ROOT/scripts/foundry-permissions-compile.py" --check   # read-only: names every missing, extra or moved rule
python3 "$CLAUDE_PLUGIN_ROOT/scripts/foundry-permissions-compile.py" --write   # reconciles .claude/settings.json + the sidecar
```

`--write` composes the derived `allow` rules into `.claude/settings.json` and leaves every rule it
did not derive alone. It also takes back what earlier releases compiled and v1.18.0 no longer
derives — `ask` rules from `approval_required` grants and the two self-guard deny rules. The doctor
then reads `policy in-sync (.foundry/permissions.yaml vs .claude/settings.json)`; a later hand edit
to the settings file shows as `policy drift (<k>)` until the next `--write`.

## 3. Let a contract name what it needs

An acceptance contract can declare `requires_capabilities:` — the tools its checkpoints shell out
to (`gh`, a cloud CLI, network). The capability preflight checks those against your settings and
grants before dispatch:

```bash
python3 "$CLAUDE_PLUGIN_ROOT/scripts/foundry-capability-preflight.py" --contract specs/.../acceptance-contract.yaml
```

Only a capability a **deny** rule would refuse blocks the atom (`missing`, exit 3). A capability
that is simply not pre-granted is listed under `classifier` — the session's permission mode decides
at run time — and never blocks. Do not edit a contract that carries a frozen `authorized:` block to
add the field — that moves its hash; use `/foundry:amend`.

## What this does not do

- It does not grant anything the trust dialog has not: the compiled rules land in
  `.claude/settings.json` and take effect through the platform's own mechanism.
- It does not verify preconditions by machine (recorded in the schema as out of scope).
- It never adds a prompt or a refusal.
