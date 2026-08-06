# How to adopt Foundry on an existing codebase (brownfield)

You don't rewrite anything, and you don't spec the whole system. You pick **one capability**,
extract it into a spec, and run the loop on it — the rest of the codebase doesn't notice.

```
 your existing repo                       after the first loop
 ──────────────────                       ───────────────────
  src/…  (untouched)                       src/…  (one governed change merged)
  tests/… (untouched)                      specs/features/<product>/<domain>/<cap>/
                                             ├─ feat-<cap>.md
        /foundry:extract-spec  ───────▶      └─ acceptance-contract.yaml (frozen)
```

## Steps

1. **Install + wire** (same as greenfield):

   ```bash
   claude plugin marketplace add lukasrepublic/agentic-foundry#v1.2.1
   claude plugin install foundry@agentic-foundry
   # in a session: /foundry:init  →  /foundry:doctor → DOCTOR-GREEN
   ```

2. **Extract, don't author.** Pick the capability you're about to change anyway (the next
   feature or bugfix is the right first candidate — governance should ride work you were
   already doing):

   ```
   /foundry:extract-spec
   ```

   It surveys the code and promotes the chosen capability into a candidate spec with
   AC-IDs grounded in **observed behavior** — what the code does today, stated testably.

3. **Review, authorize, and change it through the loop.** From here it's the standard six
   verbs. Your first authorized atom is the *change* to the capability, with the extracted
   spec as its baseline.

4. **Grow coverage capability-by-capability.** Each time work touches a new area, extract
   it first. Coverage follows the work; there is no big-bang spec-the-world phase, and
   unspecced code keeps working untouched.

## What about my existing CI?

Kept, unchanged — the merge floor *is* your CI plus branch protection
([merge-floor.md](../merge-floor.md)). Foundry adds its two gate workflows (`spec-link`,
`security-path`) beside yours; docs-only and not-yet-governed changes pass them as
not-applicable.

## Mapping from other tools

Already have Spec Kit or OpenSpec artifacts? They map onto intake naturally — see
[migrate-from-spec-kit.md](migrate-from-spec-kit.md) and
[migrate-from-openspec.md](migrate-from-openspec.md).
