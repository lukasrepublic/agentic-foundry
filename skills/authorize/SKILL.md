---
name: authorize
description: The Foundry front-authorization gate (/foundry:authorize). Display an atom's acceptance-contract checkpoints to the operator, get explicit confirmation, then freeze the operator-signed authorization (spec_sha256 + contract_sha256) onto acceptance-contract.yaml. The load-bearing v1 safety action — an un-authorized spec can never reach main. Trigger when the operator says "authorize <spec>", "/foundry:authorize", or after a contract is drafted at Specify-exit and before any implementation dispatch.
---

# /foundry:authorize

The operator-facing half of the front-authorization gate. The
mechanical write is performed by `${CLAUDE_PLUGIN_ROOT}/scripts/foundry-authorize.py`
(validated, logged, byte-canonical). This skill is the **operator-confirmation
wrapper** — it never re-implements the freeze logic.

## When to trigger

- Operator: "authorize `<spec>`", "/foundry:authorize `<spec-or-contract>`", "freeze the contract for `<atom>`".
- After an `acceptance-contract.yaml` is drafted (Specify-exit, a distinct authoring phase) and BEFORE any implementation dispatch.
- After a merge-time spec defect forces a re-authorization (RE-AUTH-AFTER-IMPL — pass `--reauth-after-impl`).

## Procedure

1. **Locate the pair.** Resolve the atom's spec path and its sibling
   `acceptance-contract.yaml` (`specs/features/<…>/acceptance-contract.yaml`). If no
   contract exists, the atom is DRAFT — it must be authored first (a distinct
   contract-authoring phase; in dispatch mode by a separate `qa-engineer` worker).

2. **Review precondition (HARDENED).** Confirm the spec has cleared a review — by default,
   `/foundry:spec-review` (`skills/spec-review/SKILL.md`), not the retired-as-default deep
   `adversarial-spec-audit` engine (`skills/audit/SKILL.md`, kept dormant-invocable for an
   exceptional deep audit only). This script's own audit-ledger precondition (`find_audit`) is
   UNCHANGED and is the **NORMAL path**: it fail-closes on a spec with no matching
   `.foundry/audit-ledger.jsonl` row (`spec_sha256` match + `rounds >= 1` + a non-`{fail,rejected,
   abandoned}` verdict). A spec reviewed via `/foundry:spec-review` already has this row —
   `skills/spec-review/SKILL.md`'s Phase 3 records it (`foundry-audit-record.py --rounds 1 --tier
   single-pass-review --verdict plateau-clean`) as part of the review itself, so `find_audit`
   finds it the same way it always found a deep spec audit row; no special-casing, no code change. A spec
   that instead ran the dormant deep audit records its ledger row the old way (see
   `skills/audit/SKILL.md` step 7). **`--skip-audit-reason "<reason>"` stays the OPERATOR-ONLY
   EXCEPTION** (an atom that skipped review outright, a deliberate operator override) — it is not
   the routine path for a spec-review-covered atom, which should already satisfy `find_audit`
   without it. Either path, do not authorize a DRAFT that cleared neither.

3. **Dry-run + DISPLAY.** Run the CLI WITHOUT `--yes`:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-authorize.py" \
     --spec <spec> --contract <contract> --operator <op_id> --mode regular|lean
   ```
   This validates the contract against the freeze floors (1-4, bijection via the
   spec) and prints the exact checkpoints — the live-seam PASS criteria — the
   operator is being asked to sign. If validation FAILS, stop and report; the
   contract must be fixed (re-specified), not the gate relaxed.

4. **Operator confirmation (the independent authority).** Present the displayed
   scope + checkpoints to the operator verbatim and get an explicit yes. This human
   confirm IS the front-funnel authority and, in the lean loop, the
   independence substitute for a separate authoring context. Do NOT self-confirm.

5. **Freeze (--yes).** On confirmation, re-run with `--yes` (add
   `--reauth-after-impl` if an impl branch/PR already exists). The CLI writes a
   record-before-action entry to the security-audit trail, freezes the `authorized:` block
   below the sentinel, and writes a completion entry. `merge_autonomy_mode` is set
   here and travels hash-covered with the contract.

6. **Report** the resulting state (AUTHORIZED), `auth_seq`, and the two hashes. The
   atom may now be dispatched/implemented; its merge is admitted by the native merge
   floor (`docs/merge-floor.md` — branch protection / required CI checks, plus
   `hooks/foundry-git-discipline.sh` within sessions) against this frozen record.

## Inputs

- `<spec>` — the authorized atomic spec (its normative region is hashed into `spec_sha256`).
- `<acceptance-contract.yaml>` — the drafted contract (must pass freeze floors 1-4).
- `<op_id>` — the operator id; resolved against `.claude/foundry-operators.json` (or `$FOUNDRY_OPERATOR`). Fail-closed if unregistered.
- `--mode regular|lean` — the implementation-merge autonomy mode for this atom.

## Outputs

- An `acceptance-contract.yaml` with a frozen `authorized:` block (operator-signed `spec_sha256` + `contract_sha256`, `auth_seq`, `merge_autonomy_mode`).
- Security-audit trail entries (`authorize-intent` + `authorize-complete`) in `.foundry/security-audit.jsonl`.

## Anti-patterns

- **Self-confirming.** The operator's explicit yes at step 4 is the authority; an agent must never supply `--yes` without it. Front-authorization is UNCONDITIONAL — there is NO skip phrase (unlike the deep spec audit).
- **Relaxing the contract to pass the gate.** A failing freeze-floor means the contract is under-specified — re-specify it (re-author checkpoints), never weaken the floors.
- **Re-implementing the freeze.** Always invoke `foundry-authorize.py`; never hand-write the `authorized:` block (byte-canonical hashing + newline canonicalization + monotonic `auth_seq` are easy to get subtly wrong).
- **Authorizing a DRAFT that cleared no review** (no `/foundry:spec-review` evidence row, no dormant-deep-audit row) without the operator's explicit `--skip-audit-reason "<reason>"` flag — the ONLY escape hatch, reserved for a genuine operator exception, never the routine path (see step 2 above).
- **On a harness denial** (e.g. the classifier blocks `--yes`), see `docs/harness-denial-fallback.md` and STOP: hand the operator the exact denied invocation; never retry it or route around the classifier.
