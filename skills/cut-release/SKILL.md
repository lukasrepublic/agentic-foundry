---
name: cut-release
description: Cut an agentic-foundry release as a guarded playbook (/foundry:cut-release). Encodes the hand-run cut procedure as a loop whose EXIT GATE is the existing acceptance verdict — verifies the ordered preconditions (bump BOTH manifests, source.ref, CHANGELOG section), refuses to emit any publish plan until run_acceptance returns pass, then emits the gotcha-correct publish plan (re-pin marketplace source.sha to the release commit → annotated tag on the re-pin commit → machine-verify the tag → push, never force) WITHOUT pushing. Trigger when the operator is cutting/releasing a version — "cut a release", "release v0.6.1", "/foundry:cut-release", "ship the release".
---

# /foundry:cut-release — the cut-release playbook

The FIRST foundry **playbook** (a guarded, looping skill — task-triggered, loop held by an
**EXIT GATE**, floors orthogonal; see `docs/TERMINOLOGY.md`). It turns the release-cut procedure — which lived only as memory-prose
and was hand-run ~6× (every recurring gotcha was that procedure failing) — into a self-contained,
gated loop driven by `scripts/foundry-cut-release.py`.

Its EXIT GATE is the **EXISTING acceptance verdict** (`foundry-release-acceptance.run_acceptance`) — the
only gate this script invokes; it reimplements no gate logic. The closure derivation
(`foundry_release.py`) is **upstream prep the operator runs before the cut**, not invoked here. It
adds **no new floor** and **never runs `git tag` / `git push`** — it emits the publish plan as
data and the operator executes it.

> **Retired: the wiring-pin migrate.** This loop once named a `foundry-wiring-hash.py`
> wiring-pin migrate step, re-checked transitively via DOCTOR-GREEN. That mechanism —
> `scripts/foundry-wiring-hash.py`, the `.foundry/wiring-hash.pin` file, and the doctor's wiring-hash
> check — was retired; the current `foundry-doctor.py` (a thin 5-check
> probe, `skills/doctor/SKILL.md`) carries no wiring-hash check to re-check. What carries the load
> today is the `foundry-release-acceptance` gate (`run_acceptance`) this loop's EXIT GATE already
> calls. See `skills/release/SKILL.md`'s "Gate-wiring pin & manifest" tombstone for the fuller history.

## The playbook shape (the standard template — see docs/TERMINOLOGY.md §6)

- **TRIGGER** — the operator is cutting/shipping a release (description-matched; no global pointer).
- **ENGINE** — `deterministic-seam-drive`: ordered prep-check → machine gate → emit plan.
- **EXIT GATE** — `foundry-release-acceptance.py run_acceptance(tree) == pass` (the artifact-sha-bound
  verdict). The loop is NOT "done" until the gate is green; nothing reaches tag/push before that.
- **FLOORS TOUCHED** — **none new.** The orthogonal floors (front-authorization, the per-atom merge floor,
  the acceptance HARD-STOP) already fired upstream; cut-release only
  sequences and refuses-until-green. It never folds a floor into a step.

## The loop

```
   run foundry-cut-release.py --tree <repo> --version <X.Y.Z>
        │
        ├─ state=REFUSED (preflight)  → fix the NAMED precondition (in the frozen order), re-run
        ├─ state=GATED  (acceptance)  → fix the acceptance FAILURE (doctor RED / validate / hooks), re-run
        └─ state=READY                → the publish plan is emitted; the OPERATOR executes it
```

The `READY` plan's tail carries the **ER-reconciliation backstop** — see below.

## Procedure

1. **Pick the version** (the operator picks it — there is no inference) and make sure the prep is staged:
   bump `plugin.json` **then** `marketplace.json` (version + `source.ref` = `vX.Y.Z`) → bump the
   **install pin in its THREE bindings** (below) → write the `## vX.Y.Z` CHANGELOG section → commit
   (the release commit **R**).

   > ⚠️ **The install pin is NOT covered by this playbook's preflight, and it has three bindings.**
   > The `REFUSED` checks below cover `plugin.json`, `marketplace.json` (version + `source.ref`) and the
   > CHANGELOG section — **none of the three**:
   > 1. `DEFAULT_MARKETPLACE_REF` in `scripts/foundry-bootstrap.sh`;
   > 2. every documented **marketplace-add install line** (the `…#vX.Y.Z`-pinned one) in **any**
   >    shipped `*.md` — currently `README.md` and `docs/QUICKSTART.md` ×2. The test scans the whole
   >    tree by matching that command string, so a new doc naming it joins this set automatically.
   >    ⚠️ That also means **prose *about* the command counts**: writing the literal command name in a
   >    sentence makes the scanner treat your sentence as an install instruction and demand it carry
   >    the current pin. Describe it, do not spell it out (this paragraph deliberately does not);
   > 3. the two together — `test_shipped_pin_matches_marketplace_manifest_ref` and
   >    `test_documented_install_commands_name_the_shipped_pin` in
   >    `tests/test_bootstrap_install_pin.py`.
   >
   > Those live in the **pytest** suite. **Since `feat-foundry-release-suite-gate`, the preflight RUNS that
   > suite and REFUSES the cut on any failure** — so a stale pin can no longer reach a tag, and this list is
   > guidance for *staging the bump*, not a checklist you must remember. It is here because knowing the three
   > bindings makes the refusal instantly actionable, not because you are the control.
   >
   > This is the fix for the v0.27.0 cut, which shipped a stale pin: its four metadata preconditions all
   > passed and `run_acceptance` (validate + `plugin tag --dry-run` + DOCTOR-GREEN) runs no tests, so `READY`
   > was returned **truthfully** while the tree was failing its own suite. The first remediation was a
   > warning in this very paragraph — an instruction a human had to follow. That was the wrong control, and
   > it was replaced with an enforced one.
2. **Run the gate:**
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-cut-release.py" --tree <repo-root> --version X.Y.Z
   ```
   - `REFUSED` (exit 2) — a precondition failed; the output names it (e.g. "marketplace.json version !=
     target — bump BOTH manifests"). Fix and re-run. **This now includes the candidate tree's own test
     suite**, which runs *after* the four cheap metadata checks (so a typo'd version still refuses in
     milliseconds) and *before* acceptance. A failure names the failing tests; anything that prevents a real
     verdict — pytest absent, no `tests/`, nothing collected — refuses fail-closed rather than skipping.
     Expect the metadata-clean path to take a few minutes: that cost is bought once per release, and the
     alternative is a permanently wrong published tag.
   - `GATED` (exit 2) — `run_acceptance` returned FAIL; the candidate tree's own doctor / validate /
     hooks-executable check is red. Fix the defect (NOT the gate) and re-run.
   - `READY` (exit 0) — preconditions ok ∧ acceptance `pass`. The **publish plan** is printed.
3. **Execute the emitted publish plan yourself** (cut-release never pushes). **RE-PIN FIRST, THEN
   TAG** — this order is the whole point, and the previous order shipped the wrong code twice:

   1. `git status --porcelain` — **must be empty.** The re-pin commit is created *after* the
      acceptance verdict and the tag lands on it, so any uncommitted edit would ship under the
      release tag having never been gated.
   2. `CONTENT=$(git rev-parse HEAD)` — the release commit **R**, the code being shipped.
   3. Edit `.claude-plugin/marketplace.json`: `source.sha = $CONTENT`, `source.ref = vX.Y.Z`.
   4. Commit it **path-scoped**: `git commit -m '…' -- .claude-plugin/marketplace.json`. Never
      `commit -am`, which sweeps every modified tracked file into the tagged commit.  → **R2**
   5. `git tag -a vX.Y.Z` — on **R2**, the commit that CARRIES the pin.
   6. `python3 scripts/foundry-cut-release.py --tree <repo> --version X.Y.Z --verify-tag` — the
      machine re-check. **Must print `TAG-PIN-COHERENT`.** Do not push until it does.
   7. `push origin main` + `push origin vX.Y.Z`, **never force-push** (if a parallel push rejects,
      reconcile by MERGE).

   **Why the order matters.** An adopter installs by ref: `marketplace add <repo>#vX.Y.Z` resolves
   `marketplace.json` **at the tag** and installs the commit its `source.sha` names. Tagging before
   the re-pin leaves the tag serving the **previous** release's sha, so the install delivers the
   previous version's code — for a security patch, the fix is simply not delivered. That shipped on
   both v1.0.0 and v1.0.1 and was hand-corrected each time.

   **Why step 6 is not optional.** On the cut that *creates* the tag, the preflight coherence check
   reports "not applicable" — the tag does not exist yet, so there is nothing to inspect. Step 6 is
   the only point at which the property is machine-verified rather than assumed.
4. **Reconcile the release's ERs** (the backstop's operator step). The `READY` plan's **tail** carries one
   `gh issue close <n>` step per enhancement-request the release closes, derived from the `## vX.Y.Z`
   CHANGELOG section (the trace source authored in step 1). **Review each before running it** — see the
   backstop section below — then execute the ones that apply. Closing an already-closed ER is a harmless
   no-op.
5. **Downstream** — adopters pull via `claude plugin marketplace update <marketplace>` →
   `claude plugin update <plugin>@<marketplace>`. **The plugin id must be marketplace-qualified**: a bare
   `claude plugin update foundry` fails with `Plugin "foundry" not found`. For this repo's own adopters
   that is `claude plugin marketplace update agentic-foundry` → `claude plugin update foundry@agentic-foundry`
   (`claude plugin list` prints the qualified id if you are unsure).
   Then **verify the SHIPPED artifact, not the source tree** — run `/foundry:doctor` and any atom's
   `--selftest` against `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`. A green source tree
   does not prove the published one is green. (When invoking a cached script by hand, set
   `CLAUDE_PROJECT_DIR` — without it, `stack-profile-lock` reports a misleading "not applicable".)
   Do **not** bulk-push changes into external-org / dirty / feature-branch repos.

## The ER-reconciliation backstop

Enhancement-request issues should close when their implementing atom merges (the PR-level `Closes #<n>`
control). That control depends on the GitHub-Projects `project-map.json` cache, which is
**absent when projection is disabled** — so ERs silently slip and the backlog fills with shipped-yet-open
issues (this is why past releases have shipped enhancement requests that stayed OPEN in the tracker).
Cut-release is the durable **backstop**: on every cut it re-derives the release's ERs from the CHANGELOG
and emits their closes.

- **Trace source = the `## vX.Y.Z` CHANGELOG section**, already authored on every cut. The tool matches
  **only the explicit `ER #<digits>` marker** — never a bare `#NNN` — because the CHANGELOG cites the ER
  (e.g. `ER #<n>`) and its implementing PR (e.g. `(#<n>)`) in the same `#NNN` namespace; a bare match would
  try to `gh issue close` a pull request. An unmarked number is not an ER.
- **Emitted, never executed.** Like the tag/push plan, the `gh issue close` steps are DATA the operator
  runs — cut-release never touches `gh`. They are appended **after** the push steps so an ER only closes
  once its release is on `main`.
- **The OPEN-ER advisory.** When `gh` is reachable, `READY` prints which traced ERs are still **OPEN** at
  cut time (the ones the reconciliation targets). Offline, the check is skipped and the plan steps are
  still emitted (fail-safe: an unknown ER is never reported closed).
- **REVIEW before running (the load-bearing caveat).** The prose trace is deliberately broad and cannot
  distinguish *"closes ER #N"* from a mere reference — e.g. a *"split to ER #N"* / *"deferred to ER #N"*
  follow-up mention traces `#N` too (a CHANGELOG section can reference a deferred ER this way). Drop any
  such referenced-but-not-closed ER from the plan before executing; close only the ones the release
  actually shipped. The advisory's OPEN list is your review surface, not an auto-close order.

## What it does NOT do

- **It never tags, pushes, or closes issues.** It emits the plan — including the ER-reconciliation
  `gh issue close` steps — as data; the operator runs it. (The selftest proves zero side effects over a
  temp tree; the ER-state check is read-only and fail-safe.)
- **It reimplements no gate.** The exit gate IS `foundry-release-acceptance.py`; a non-`pass` verdict is a
  HARD-STOP, not something cut-release re-derives or can override.
- **No version inference, no signing.** The operator picks the version; release-time signing/provenance
  (Sigstore/SLSA) is a typed, dormant seam gated to the first **public** release.

## Anti-patterns

- **Editing the gate to make a red cut go green** — fix the candidate tree, never the acceptance gate.
- **Tagging before re-pinning.** The tag must sit on the commit that carries the pin, or an install
  by ref serves the previous release. This is the defect that shipped twice; the plan's order is the
  fix and it is not a stylistic preference.
- **Using `git commit -am` for the re-pin.** The tag lands on that commit *after* the gate ran —
  path-scope it to `.claude-plugin/marketplace.json` so nothing ungated rides along.
- **Pushing without running `--verify-tag`.** The preflight check is a no-op on the cut that creates
  the tag; skipping the post-tag re-check leaves the property entirely unverified.
- **Pinning `source.sha` to an annotated-tag object, a branch name, or an abbreviated hash** — it
  must be a full 40-character commit id. A ref name is a *mutable* pin: what an adopter resolves
  changes as the ref moves.
- **Force-pushing to reconcile a parallel push** — reconcile by merge; the tag is already immutable.
- **On a harness denial** of a cut-release command — distinct from this loop's own `REFUSED`/`GATED` states — see `docs/harness-denial-fallback.md` and STOP: hand back the exact denied invocation; never retry it or route around it.
