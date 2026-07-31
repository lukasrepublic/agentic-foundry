---
name: cut-release
description: Cut an agentic-foundry release as a guarded playbook (/foundry:cut-release). Encodes the hand-run cut procedure as a loop whose EXIT GATE is the existing acceptance verdict — verifies the ordered preconditions (bump BOTH manifests, source.ref, CHANGELOG section), refuses to emit any publish plan until run_acceptance returns pass, then emits the gotcha-correct publish plan (annotated tag → re-pin marketplace source.sha to the TAG COMMIT → push, never force) WITHOUT pushing. Trigger when the operator is cutting/releasing a version — "cut a release", "release v0.6.1", "/foundry:cut-release", "ship the release".
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
3. **Execute the emitted publish plan yourself** (cut-release never pushes): annotated tag at **R** →
   re-pin `marketplace source.sha` to the **tag commit** (`git rev-parse vX.Y.Z^{commit}` — NOT the
   annotated-tag object) as a separate commit **R2** → `push origin main` + `push origin vX.Y.Z`,
   **never force-push** (if a parallel push rejects, reconcile by MERGE).
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
- **Re-pinning `source.sha` to the annotated-tag object** instead of the tag *commit* — the plan gives the
  correct `^{commit}` form; use it.
- **Force-pushing to reconcile a parallel push** — reconcile by merge; the tag is already immutable.
