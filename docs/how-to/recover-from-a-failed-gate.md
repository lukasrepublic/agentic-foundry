# How to recover from a failed gate

Every gate refusal in Foundry is written to be quotable and names its cause. This guide maps
each refusal to its recovery, in the order you'll meet them in the loop.

```
 where it failed          what refused              your move
 ──────────────           ────────────              ─────────
 spec-review     ──▶      Phase-0 pre-lint          fix the named defect, re-run (free)
 authorize       ──▶      a freeze floor            re-specify the contract, never relax the gate
 dispatch/PR     ──▶      spec-link CI job          add `Spec: <path>` to the PR body
 merge           ──▶      git-discipline hook       fix the red check; no bypass exists
 certify-local   ──▶      missing journeys/boot     write the journey / add the boot recipe
```

## 1. `spec-review` refused at the pre-lint phase

Deterministic pre-lints (size ceiling, reference closure) run before any LLM work and cost
nothing to re-run. `OVERSIZE:` means decompose: the spec size ceiling (fourteen criteria / eight
thousand words) has no override flag, deliberately. `DANGLING-REFERENCE:` names the citation that doesn't resolve;
fix the reference or remove the claim.

## 2. `authorize` refused to freeze

- **No review evidence** → run `/foundry:spec-review` first; its recorded row is the
  precondition and there is no routine skip.
- **A freeze floor failed** → the output names it (empty `allowed_paths`, a checkpoint whose
  locator can't bind, a bijection break between AC-IDs and checkpoints). The contract is
  under-specified: re-author it. **The gate is never the thing to relax.**

## 3. The `spec-link` CI job failed on your PR

A code diff reached CI with no lane signal. Add one line to the PR body:

```
Spec: specs/features/<product>/<domain>/<capability>/feat-<capability>.md
```

Docs-only diffs pass automatically as not-applicable.

## 4. The merge was refused

See the same section in [troubleshooting.md](../troubleshooting.md#the-merge-was-refused) —
short version: fix the red check; `--admin` has no supported path; a pending check means
wait.

## 5. `certify-local` refused

The refusal names the atom and what it lacks (journeys or a boot recipe). Write the missing
journey tagged with the atom's AC-IDs, or add the boot recipe to your stack profile. If the
atom genuinely has no runtime surface, it shouldn't be in the release manifest's certify
set — remove it there, visibly, rather than teaching the gate to pass silence.

## The one rule across all five

**Fix the thing the gate named; never the gate.** Every refusal above is the tool doing its
job. If you believe a refusal is a false positive, that's a bug worth filing — with the
refusal text — rather than a reason to bypass.
