---
name: authorize-release
description: Batch front-authorization review over a release's N atoms (/foundry:authorize-release) — ergonomics without relaxing PER-ATOM granularity. Displays every atom's acceptance-contract checkpoints, the operator confirms, and each atom is authorized singly via the standard /foundry:authorize flow. Trigger after release-DRAFT-shaping, before implement.
---

# /foundry:authorize-release

A batch review surface over a release's atoms. Ergonomics only — it does NOT
introduce an "authorize the release once" shortcut; every atom is authorized
per-atom (each carries its own `acceptance-contract.yaml` + `authorized:` record, and
`/foundry:dispatch` refuses to dispatch any atom that is not itself in state
`AUTHORIZED`).

## When to trigger

- "authorize release `<slug>`", "/foundry:authorize-release `<slug>`" — after the
  release is DRAFT-shaped (atoms enumerated) and BEFORE implementation dispatch
  (steel-man-1 ordering).

## Procedure

1. **Resolve** the release → its ordered atom list, each with a drafted
   `acceptance-contract.yaml`. Refuse any atom missing its contract (author it first).
2. **Review precondition.** Each atom's spec must have cleared the single-pass
   `/foundry:spec-review` (recorded content-bound; the deep opt-in `/foundry:audit` also
   satisfies it) or carry the operator `skip review; reason:` token. Authorization
   presumes REVIEWED specs.
3. **Batch display.** For each atom, show the scope + checkpoints the operator is signing
   (the per-atom dry-run from `foundry-authorize.py` without `--yes`). The operator
   reviews the whole wave at once.
4. **Per-atom confirm + freeze.** On operator confirmation, authorize EACH atom singly:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-authorize.py" \
     --spec <atom-spec> --contract <atom-contract> --operator <op> --mode <regular|lean> --yes
   ```
   Per-atom granularity is preserved — the operator may authorize a subset, defer
   others, or mark specific atoms hard. Each freeze records its own authorization-trail entry.
5. **Hand off** the authorized atoms to `/foundry:mode-autonomous` (or per-atom `/foundry:dispatch`).

## Anti-patterns

- **An "authorize the release once" shortcut** — there is none; per-atom authorization + per-atom merge re-check is invariant.
- **Authorizing atoms whose contracts skipped the audit** without the explicit operator token.
- **Self-confirming the batch** — the operator's review of the displayed checkpoints is the authority.
