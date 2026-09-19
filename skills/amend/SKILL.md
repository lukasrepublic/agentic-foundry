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
   against a git commit — the frozen `authorized:` trailer stores only hashes, never the prior
   field values, so the diff needs a commit that reproduces them. It does NOT trust `--ref`
   (default `HEAD`) at face value: it walks the commit history backwards from `--ref` and
   VERIFIES each candidate by recomputing both hashes, using the first commit whose (spec,
   contract) blobs both match the trailer's `spec_sha256`/`contract_sha256` — the exact state
   that was signed. `--ref` is only a starting point for that walk, never a trusted override (a
   caller who commits an already-widened contract and then runs `/foundry:amend` against HEAD is
   still refused — the walk finds the earlier, correctly-matching commit and diffs against
   THAT). If no commit in that history matches, the CLI fails closed rather than guessing.
   `operator_id`/`merge_autonomy_mode` for the re-freeze are read from that VERIFIED baseline
   commit's own trailer, never from the mutable working-tree trailer (which sits below the hash
   sentinel and could be hand-edited without moving any hash).

2. **Run the CLI.**
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-amend.py" \
     --spec <spec> --contract <contract> [--why "<what reality required>"]
   ```
   It prints (a) the normative-region diff, then (b)/(c)/(d) any boundary/identifier/rigor
   changes it found.

3. **Non-widening → done, no operator turn.** The spec MUST already carry a `## Amendments`
   section located after `<!-- /normative -->` and outside any fenced code block — if not, the
   CLI refuses before any write (nothing recorded, contract byte-identical). Otherwise: the
   `## Amendments` row is appended FIRST (outside the normative region — its own hash is
   asserted unchanged by the append, or the CLI reverts and refuses), THEN the freeze-write, THEN
   the completion record. Net effect on success: `spec_sha256`/`contract_sha256` recomputed,
   `auth_seq` bumped, `supersedes` set to the prior `contract_sha256`, the `## Amendments` row,
   and a `.foundry/security-audit.jsonl` record naming the spec + both new hashes + the new
   `auth_seq` (plus the verified baseline commit — see step 1). **No audit-ledger row is required
   or consulted** anywhere in this path — mirrors `/foundry:authorize`'s own
   audit-drops-precondition posture, one step further (amend does not even look at the §8 audit
   ledger).

4. **Widening or security-flagged → refuses, routes to authorize.** A non-zero exit with a
   structured `{status: "needs-operator", widened_fields: [...], diff_summary: "...",
   remediation: "/foundry:authorize <spec>"}` on stdout, and the contract is left byte-unchanged.
   A boundary field changed (`scope`, `checkpoints[].intended`/`ack`, `system_grounding`,
   `preconditions`, `build_gates`, `post_apply_checks`, `mandatory_review`,
   `requires_capabilities`, **and `target_repo`** — a round-2 addition, STRICTER than the frozen
   AC-AMND-1(b) list: a silent venue change is never non-widening even though the spec text
   doesn't name it), a checkpoint's `locator`/`expect.value` introduced a new identifier token
   (AWS account id, `arn:` case-insensitive, an IPv4 literal, a hostname/URL, or a grounded
   artifact identifier — including a checkpoint whose `ac_id` is absent from the baseline, new or
   renamed-away-from, checked against an EMPTY baseline so any identifier token in it convicts), a
   checkpoint's rigor was reduced (`expect.value` lowered, or its `matches` regex value changed AT
   ALL — direction unknowable; `expect.op` weakened, or changed to/from an op outside the fixed
   ranking — direction unknowable; `expect.baseline` pre-change→none; `surface` repointed OR
   DELETED), or the CANDIDATE contract's `mandatory_review` names `security` at all (regardless of
   whether that field changed this amendment) — hand the printed remediation to
   `/foundry:authorize` (`skills/authorize/SKILL.md`) and get the operator's explicit confirmation
   there. Do NOT retry `/foundry:amend` on the same diff, and do NOT hand-edit the contract to
   route around the refusal.

5. **Report** the resulting `auth_seq` and both hashes (non-widening path), or the refusal's
   `widened_fields` + remediation (widening path).

## Inputs

- `<spec>` / `<acceptance-contract.yaml>` — the ALREADY-AUTHORIZED pair, edited in place.
- `--ref` (default `HEAD`) — a STARTING POINT for the backwards git-log walk that resolves the
  verified baseline commit; never a trusted override (see step 1).
- `--why` — optional free text for the `## Amendments` row's "why reality required it" column.

## Residuals — accepted identifier-token limitations (RISK R3)

The identifier-token classifier (AC-AMND-1(c)) is a fixed, testable set of regexes, not a full
grounding oracle. Two classes of token it does NOT catch, accepted for this wave:

- A **bare single-label host** (no dot: `prod-db`, `localhost`) — the hostname pattern requires a
  dot-separated label + a letters-only suffix, so a single-label name never matches.
- **Unicode homoglyph/confusable substitutions** in a hostname (e.g. a Cyrillic-lookalike `а` for
  Latin `a`) — the classifier compares literal codepoints, not visual similarity.

Both are named here rather than silently absorbed into "it just works" — a widening that relies
on either to slip past the classifier is a real gap, not a false negative this atom claims to close.

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
