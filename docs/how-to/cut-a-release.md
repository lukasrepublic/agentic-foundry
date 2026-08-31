# How to cut a release

The release cut is a guarded loop driven by `scripts/foundry-cut-release.py`. It refuses
until every precondition and the acceptance gate are green, then emits a **publish plan as
data** — it never tags, never pushes, never closes issues itself. You execute the plan.

```
  prep (you)                     the gate                        publish (you)
  ──────────                     ────────                        ─────────────
  bump plugin.json only     ┌────────────────────┐
  write CHANGELOG section ──▶ │ foundry-cut-release │──REFUSED──▶  fix the named
  commit (release commit R) │   --tree . \        │              precondition, re-run
                            │   --version X.Y.Z   │──GATED────▶  fix the acceptance
                            └─────────┬──────────┘               failure, re-run
                                      │ READY
                                      ▼
                            the publish plan (data):
                              1. re-pin marketplace version + source.sha = R,
                                 source.ref = vX.Y.Z (path-scoped commit R2)
                              2. annotated tag vX.Y.Z on R2 — the commit CARRYING the pin
                              3. --verify-tag  →  must print TAG-PIN-COHERENT
                              4. push main + tag  (never force)
                              5. gh issue close … (review each first)
```

## Steps

1. **Prep** — pick the version (yours to pick; there is no inference), then:
   - bump `.claude-plugin/plugin.json` `version` **only**
   - write the `## vX.Y.Z` CHANGELOG section
   - commit (the release commit **R**)

   **`.claude-plugin/marketplace.json` does NOT bump here.** Its `version` and `source.ref`
   move to the re-pin commit **R2** below, landing together with `source.sha` — never in R
   (`feat-foundry-install-line-unpinning`). Bumping the catalogue in R, ahead of the commit that
   carries it, would make the default branch advertise a catalogue version whose pinned commit does
   NOT carry it — so an adopter resolving a tagless registration mid-cut is told about a version R2
   has not shipped yet, and is served the previous release. See the version-bump step of
   [`skills/cut-release/SKILL.md`](../../skills/cut-release/SKILL.md) for the full reasoning.

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

3. **Execute the plan yourself, in its order — RE-PIN FIRST, THEN TAG.**

   ```bash
   git status --porcelain                     # must be EMPTY (see the warning below)
   CONTENT=$(git rev-parse HEAD)              # the release commit R
   # edit .claude-plugin/marketplace.json: source.sha = $CONTENT, then set version = X.Y.Z
   # and source.ref = vX.Y.Z -- ALL THREE fields land here, in R2, never in R (see Prep above)
   git commit -m 'release: re-pin …' -- .claude-plugin/marketplace.json   # R2, PATH-SCOPED
   git tag -a vX.Y.Z -m 'agentic-foundry vX.Y.Z'                          # on R2
   python3 scripts/foundry-cut-release.py --tree . --version X.Y.Z --verify-tag
   # → TAG-PIN-COHERENT before you push anything
   # On a repo whose main is protected with enforce_admins the main push is REFUSED for everyone.
   # Create R and R2 on a branch and land each by PR -- TWO PRs, the bump then the re-pin, because
   # source.sha must name the commit AS IT LANDS on main and a squash landing does not preserve a
   # branch commit's SHA. Read main's HEAD after the bump PR merges; that is the sha the re-pin PR
   # pins. Tag only once R2 is on main. See skills/cut-release/SKILL.md.
   git push origin main && git push origin vX.Y.Z                          # never force
   ```

   **Why this order.** An adopter installs by ref, which resolves `marketplace.json` **at the
   tag** and installs the commit its `source.sha` names. Tag first and the tag serves the
   *previous* release's sha — the install delivers the previous version's code. That shipped on
   v1.0.0 and v1.0.1 and was hand-corrected both times.

   **`source.sha` names R, the tag's PARENT — and that is correct.** A commit cannot contain its
   own hash, so the pin names the content commit while the tag sits on the re-pin commit. It is
   also the field that decides what ships: per the plugin-marketplace docs, `sha` **outranks**
   `ref`, and since Claude Code v2.1.141 a deleted ref does not block an install whose sha still
   resolves. The catalogue is read at the ref; the plugin is installed at the sha. The gate
   enforces the adjacency, so a pin reaching further back than the tag's parent is refused.
   Full reasoning, and why the field is kept rather than dropped:
   [`skills/cut-release/SKILL.md` → *The install pin*](../../skills/cut-release/SKILL.md).

   **Why the tree must be clean, and why the commit is path-scoped.** The tag now lands on R2,
   created *after* the acceptance gate ran. `git commit -am` would sweep every modified tracked
   file into it and publish that under the release tag, ungated.

   **Why `--verify-tag` is not optional.** On the cut that creates the tag, the preflight
   coherence check reports "not applicable" — the tag does not exist yet. This is the only step
   that machine-verifies what an adopter will actually resolve.

   Never force-push; if a parallel push rejects, reconcile by merge (the tag is already
   immutable).

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
