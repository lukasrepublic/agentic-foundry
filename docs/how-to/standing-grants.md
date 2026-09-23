# How to turn a standing grant into policy

A *standing grant* is something the operator has decided once and does not want to be asked
about again — "merge an atom's PR yourself once its checks are green", say. Until it is written
down as policy it lives in a memory file, a CLAUDE.md sentence, or the operator's head, and every
session re-reads it and re-decides whether it applies. `.foundry/permissions.yaml` is where such a
grant becomes one native Claude Code rule the platform enforces, one line the doctor reports, and
one thing the capability preflight can check a contract against before an atom is dispatched.

## 0. The file exists

`npx update-agentic-workspace` (and `create-agentic-workspace --existing`) seed an empty
`.foundry/permissions.yaml` when there is none: `schema_version: 1`, `grants: []`, and two
worked grants in comments. It is **operator-owned** from that moment — the seed is written once and
never reconciled, so your edits are never reported drifted and never overwritten. The plugin keeps
the identical starter at `context/permissions-template.yaml`; the schema is
`schema/permissions.schema.json`.

Before the seed, the doctor's line reads `policy absent — seed it: …`. Right after it, it reads
`policy drift` with two rules named: the compiler's own self-guard rules (`Edit` and `Write`
denied on the policy file) are not in the floor, so they are missing until the first `--write` in
the compile step below. That one run converges them and the line becomes `policy in-sync`.

## 1. Write the grant

One entry per grant. `id` is a slug, `tool` is one of `Bash | Edit | Write | Read | WebFetch |
Agent`, `pattern` is exactly what goes inside the native rule's parentheses, `mode` is either
`automatic` (proceed once every precondition has been verified by command) or `approval_required`
(one blocker line naming the grant; the operator decides), and `preconditions` come from the closed
set `ci-green, security-reviewed-label, spec-authorized, charter-committed, worktree-clean,
branch-up-to-date`.

```yaml
schema_version: 1
grants:
  - id: merge-atom-pr-when-green
    tool: Bash
    pattern: "gh pr merge:*"
    mode: automatic
    preconditions: [ci-green, branch-up-to-date]
```

Anything irreversible, security-adjacent or authorization-adjacent belongs in `approval_required`,
whatever the operator's appetite: the preconditions are verified by the agent by command and
recorded, not machine-enforced, so `automatic` is a statement of trust in that verification.

## 2. Compile it

```bash
python3 "$CLAUDE_PLUGIN_ROOT/scripts/foundry-permissions-compile.py" --check   # read-only: names every missing, extra or moved rule
python3 "$CLAUDE_PLUGIN_ROOT/scripts/foundry-permissions-compile.py" --write   # reconciles .claude/settings.json + the sidecar
```

`--write` composes the derived rules into `permissions.allow` (or `ask`, for `approval_required`)
and leaves every rule it did not derive alone. The doctor now reads `policy in-sync (1)`; a later
hand edit to the settings file shows as `policy drift (1)` until the next `--write`.

## 3. Let a contract name what it needs

An acceptance contract can declare `requires_capabilities:` — the tools its checkpoints shell out
to (`gh`, a cloud CLI, network). The capability preflight checks those against the grants above
before dispatch:

```bash
python3 "$CLAUDE_PLUGIN_ROOT/scripts/foundry-capability-preflight.py" --contract specs/.../acceptance-contract.yaml
```

A capability with no grant is reported before the atom starts, which is the whole point: a
classifier denial mid-run is the failure this replaces. Do not edit a contract that carries a
frozen `authorized:` block to add the field — that moves its hash; use `/foundry:amend`.

## What this does not do

- It does not grant anything the trust dialog has not: the compiled rules land in
  `.claude/settings.json` and take effect through the platform's own mechanism.
- It does not verify preconditions by machine (recorded in the schema as out of scope).
- It does not replace the never-relaxed floor: a grant cannot admit a force-push to `main` or an
  admin merge; the git-discipline hook refuses those regardless of policy.
