---
name: revert
description: Governed incident-revert (/foundry:revert) — first-class, NOT a bypass of the no-skip front-authorization gate. Cuts a revert PR restoring a previously-AUTHORIZED state, reusing the prior authorization (no new contract), still subject to the merge floor + re-certified. Trigger on an escaped-defect merge needing rollback.
---

# /foundry:revert

The steady-state incident-response path. Front-authorization is
unconditional (no skip), so an emergency revert is neither impossible nor an un-gated
hole: it REUSES the prior good state's authorization (you are restoring something the
operator already authorized + that was proven at its own release), and still clears the merge floor + re-certification.

## When to trigger

- A staging/prod escape defect on a merged atom (the governed trigger fires a
  the operator flags the incident); resolution is a forward-fix OR this revert.
- "/foundry:revert `<bad-merge-sha|PR>`".

## Procedure

1. **Identify** the bad merge `<bad-sha>` and the prior good state `<good-sha>` to
   restore (a state that was AUTHORIZED + proven at its own release). A revert to never-authorized
   history (e.g. pre-Foundry) requires NORMAL authorization, not this path.
2. **Write the revert-authorization record** to the fail-closed audit trail (reuses the
   prior authorization; does NOT author a new acceptance-contract):
   ```bash
   python3 -c "import sys; sys.path.insert(0,'${CLAUDE_PLUGIN_ROOT}/scripts'); \
     import foundry_audit_log as al; \
     al.append_record({'action':'revert-authorization','operator_id':'<op>', \
       'reverts':'<bad-sha>','restores':'<good-sha>','restores_contract_sha256':'<good contract_sha256>', \
       'reason':'<incident reason>'})"
   ```
3. **Cut the revert PR** (`git revert <bad-sha>` or restore `<good-sha>`'s tree) →
   `gh pr create`. The committed `.foundry/build-provenance.yaml` marker of the restored
   state records the prior pinned authorization for traceability; the merge floor (branch
   protection / required CI checks, plus `hooks/foundry-git-discipline.sh` within sessions —
   see `docs/merge-floor.md`) governs admission the same as any PR.
4. **Re-certify the reverted candidate** — `/foundry:certify-local` re-runs the restored
   state's journeys (they passed at that release; they must pass again — a broken revert
   surfaces here). Expedited (operator-attested) but NEVER skips authorization-existence
   or re-certification.
5. **Merge** via the merge floor like any merge.

## Anti-patterns

- **Treating revert as a skip** of the front-auth gate — it REUSES prior authorization; it does not bypass it.
- **Skipping re-certification** of the reverted candidate — a broken revert must be caught before acceptance.
- **Reverting to never-authorized history** without normal authorization.
