---
name: amend
description: One-step re-freeze of a living, already-authorized spec (/foundry:amend). Recomputes spec_sha256/contract_sha256, bumps auth_seq, and records the amendment WITHOUT an operator step — unless the change widens a boundary (scope, checkpoint intended/ack, system_grounding, an identifier token in a checkpoint locator/expect.value, or a checkpoint-rigor reduction) or the contract's mandatory_review names a security review, in which case it refuses and routes to /foundry:authorize. Trigger when the operator or agent says "amend <spec>", "/foundry:amend", or implementation reality has changed an authorized spec's normative text or its contract after /foundry:authorize already ran.
---

# /foundry:amend

The mechanical write is performed by `${CLAUDE_PLUGIN_ROOT}/scripts/foundry-amend.py`
(validated, logged, byte-canonical — it never re-implements the freeze logic, delegating to
`foundry_authz.validate_spec_contract` and `foundry_authz.authorize`, the SAME functions
`/foundry:authorize` calls). This skill drives when/how to invoke it.

## When to trigger

- Operator or agent: "amend `<spec>`", "/foundry:amend `<spec-or-contract>`".
- After a spec's normative text or its `acceptance-contract.yaml` is edited **during
  implementation of an already-AUTHORIZED atom** (an `authorized:` trailer is already present)
  — the spec is a living document; this is the normal path, not an exception.
- **Never** for a DRAFT (no `authorized:` trailer yet) — that is `/foundry:authorize`'s job, the
  FIRST freeze. Amend only ever RE-freezes an existing authorization.

## Procedure

1. **Commit the last-authorized baseline first.** `/foundry:amend` diffs the working tree
   against `git show HEAD:<path>` (or `--ref <ref>`) to compute what changed — the frozen
   `authorized:` trailer stores only hashes, never the prior field values, so `HEAD` must still
   hold the state the trailer's hashes describe. If it does not (nothing to diff against), the
   CLI fails closed rather than guessing.

2. **Run the CLI.**
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-amend.py" \
     --spec <spec> --contract <contract> [--why "<what reality required>"]
   ```
   It prints (a) the normative-region diff, then (b)/(c)/(d) any boundary/identifier/rigor
   changes it found.

3. **Non-widening → done, no operator turn.** Exit 0: `spec_sha256`/`contract_sha256`
   recomputed, `auth_seq` bumped, `supersedes` set to the prior `contract_sha256`, a row appended
   to the spec's `## Amendments` table, and a `.foundry/security-audit.jsonl` record naming the
   spec + both new hashes + the new `auth_seq`. **No audit-ledger row is required or consulted**
   anywhere in this path — mirrors `/foundry:authorize`'s own audit-drops-precondition posture,
   one step further (amend does not even look at the §8 audit ledger).

4. **Widening or security-flagged → refuses, routes to authorize.** A non-zero exit with a
   structured `{status: "needs-operator", widened_fields: [...], diff_summary: "...",
   remediation: "/foundry:authorize <spec>"}` on stdout, and the contract is left byte-unchanged.
   A boundary field changed (`scope`, `checkpoints[].intended`/`ack`, `system_grounding`,
   `preconditions`, `build_gates`, `post_apply_checks`, `mandatory_review`,
   `requires_capabilities`), a checkpoint's `locator`/`expect.value` introduced a new identifier
   token (AWS account id, `arn:`, hostname/URL, or a grounded artifact identifier), a
   checkpoint's rigor was reduced (`expect.value` lowered, `expect.op` weakened,
   `expect.baseline` pre-change→none, `surface` repointed), or the CANDIDATE contract's
   `mandatory_review` names `security` at all (regardless of whether that field changed this
   amendment) — hand the printed remediation to `/foundry:authorize` (`skills/authorize/SKILL.md`)
   and get the operator's explicit confirmation there. Do NOT retry `/foundry:amend` on the same
   diff, and do NOT hand-edit the contract to route around the refusal.

5. **Report** the resulting `auth_seq` and both hashes (non-widening path), or the refusal's
   `widened_fields` + remediation (widening path).

## Inputs

- `<spec>` / `<acceptance-contract.yaml>` — the ALREADY-AUTHORIZED pair, edited in place.
- `--ref` (default `HEAD`) — the git ref holding the previously-frozen baseline to diff against.
- `--why` — optional free text for the `## Amendments` row's "why reality required it" column.

## Outputs

- Non-widening: an `acceptance-contract.yaml` with a re-frozen `authorized:` block, a new row in
  the spec's `## Amendments` table, and `amend-intent` + `amend-complete` entries in
  `.foundry/security-audit.jsonl`.
- Widening: nothing written; a structured refusal on stdout naming the exact widened fields.

## Anti-patterns

- **Re-implementing the freeze.** Always invoke `foundry-amend.py`; it delegates the actual
  write to `foundry_authz.authorize` — never hand-bump `auth_seq` or hand-edit the `authorized:`
  block.
- **Treating a security-flagged atom as amendable.** `mandatory_review` naming `security` ALWAYS
  routes to `/foundry:authorize`, even for a change that widens nothing else.
- **Amending a DRAFT.** No `authorized:` trailer means there is nothing to re-freeze — run
  `/foundry:authorize` for the first freeze.
- **Retrying past a refusal.** A `needs-operator` refusal is the classifier working as designed;
  hand it to `/foundry:authorize`, don't loosen the diff or the classifier to make it pass.

## See also

- `skills/authorize/SKILL.md` — the FIRST freeze (DRAFT → AUTHORIZED) and the destination for
  every widening/security-flagged amendment this skill refuses.
