# How to cut a release

The release cut is a guarded loop driven by `scripts/foundry-cut-release.py`. It refuses
until every precondition and the acceptance gate are green, then emits a **publish plan as
data** — it never tags, never pushes, never closes issues itself. You execute the plan.

```
  prep (you)                     the gate                        publish (you)
  ──────────                     ────────                        ─────────────
  bump plugin.json          ┌────────────────────┐
  bump marketplace.json ──▶ │ foundry-cut-release │──REFUSED──▶  fix the named
  write CHANGELOG section   │   --tree . \        │              precondition, re-run
  commit (release commit R) │   --version X.Y.Z   │──GATED────▶  fix the acceptance
                            └─────────┬──────────┘               failure, re-run
                                      │ READY
                                      ▼
                            the publish plan (data):
                              1. annotated tag vX.Y.Z at R
                              2. re-pin marketplace source.sha to the TAG COMMIT (R2)
                              3. push main + tag  (never force)
                              4. gh issue close … (review each first)
```

## Steps

1. **Prep** — pick the version (yours to pick; there is no inference), then:
   - bump `.claude-plugin/plugin.json` `version`
   - bump `.claude-plugin/marketplace.json` (version + `source.ref: vX.Y.Z`)
   - write the `## vX.Y.Z` CHANGELOG section
   - commit (the release commit **R**)

2. **Run the gate:**

   ```bash
   python3 scripts/foundry-cut-release.py --tree . --version X.Y.Z
   ```

   - `REFUSED` — a precondition failed and is named (mismatched manifests, missing
     CHANGELOG section, **a red test suite** — the gate runs the full suite and a release
     cannot cut over failing tests). Fix, re-run.
   - `GATED` — the acceptance gate (doctor / validate / hooks-executable) is red on the
     candidate tree. Fix the defect, never the gate; re-run.
   - `READY` — the publish plan prints.

3. **Execute the plan yourself**, in its order. The one subtle step: re-pin
   `marketplace source.sha` to the **tag commit** —

   ```bash
   git rev-parse vX.Y.Z^{commit}     # NOT the annotated-tag object hash
   ```

   — as a separate commit (R2), then push `main` and the tag. Never force-push; if a
   parallel push rejects, reconcile by merge (the tag is already immutable).

4. **Review the emitted `gh issue close` steps before running them.** The plan traces
   `ER #<n>` markers in the CHANGELOG section; a *"deferred to ER #n"* mention traces too,
   so drop any referenced-but-not-shipped issue from the list. Closing an already-closed
   issue is a harmless no-op.

5. **Verify as an adopter would:**

   ```bash
   claude plugin marketplace update && claude plugin update foundry@agentic-foundry
   ```

## The rules that make it safe

- The gate **runs the full test suite** on the candidate tree — metadata-clean is not
  enough; a red suite refuses the cut.
- The tool **emits, never executes** the publish plan — the side-effecting steps stay
  human.
- A red gate is fixed **in the tree, never in the gate**.
