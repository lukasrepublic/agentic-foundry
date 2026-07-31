# How to migrate from GitHub Spec Kit

Spec Kit and Foundry are complementary, not rivals: Spec Kit generates excellent
spec/plan/task documents; Foundry governs what happens *after* the documents — signed
authorization, the merge floor, certification against the running app. If you have Spec Kit
artifacts, most of your work is already done.

## The mapping

| Spec Kit artifact | Foundry equivalent | What changes |
|---|---|---|
| `spec.md` (feature spec) | `feat-<capability>.md` (atomic spec) | Split to one capability per file; each criterion gets a **stable AC-ID** (`AC-CAP-1`); the requirements live in a delimited `<!-- normative -->` region |
| `plan.md` | stays yours | Foundry doesn't govern planning; keep using it |
| `tasks.md` | the dispatch loop | tasks become dispatched atoms; ordering becomes the wave plan |
| constitution / principles | your workspace `CLAUDE.md` | same job, same place |
| — (no equivalent) | `acceptance-contract.yaml` | **the new artifact**: observable checkpoints per AC-ID, frozen at authorization |

## Steps

1. **Pick one Spec Kit spec** — your next one to implement, not your whole backlog.
2. **Run `/foundry:intake` and paste it.** Intake restructures rather than rewrites: atomic
   split, AC-IDs, the normative region, and it drafts the sibling acceptance contract from
   your acceptance criteria.
3. **Sharpen the checkpoints.** The one genuinely new discipline: each criterion needs an
   *observable* checkpoint — a surface, a locator, an expected result a runner can verify.
   "Users can export CSV" becomes `GET /export.csv → 200 + text/csv`. If a criterion can't
   be stated observably, that's a finding about the criterion.
4. **From here it's the standard loop**: spec-review → authorize → dispatch →
   certify-local → your sign-off.

## What you keep

Everything. Spec Kit's discovery/planning flow keeps producing the drafts; Foundry picks
them up at intake. Teams run both — the pipelines meet at the spec document.

## What has no equivalent going back

The frozen `authorized:` block (hash-bound approval), the merge-floor tie-in, and
certification evidence. If you later leave Foundry, your specs and contracts remain plain
markdown/YAML in your repo — nothing to export.
