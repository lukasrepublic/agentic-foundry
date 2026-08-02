---
name: certify-local
description: Deploy a release ONCE locally and run its full tagged journey suite against that one instance (/foundry:certify-local <release>, introduced in the v0.25.0 certification realignment, CONSTITUTION.md §V factory-mode tail). Resolves the release manifest + the active stack profile's boot recipe, runs plain `npx playwright test --grep` over every atom's journey tags, and reports per-atom pass/fail with the runner's own output as evidence — no verdict engine, no custom evidence format. REFUSES (never a vacuous pass) naming the missing prerequisite when there is no journey suite or no boot recipe. Trigger to certify a release before recording operator acceptance.
---

# /foundry:certify-local `<release>`

The release train's first certification step (CONSTITUTION.md §V `factory` mode: spec → plan →
build → integrate → **certify locally** → operator acceptance → staging). One deploy, one real
run of every atom's already-merged E2E journeys, the runner's own output as the evidence — no
new evidence format, no re-judged verdict.

## When to trigger

- "certify `<release>` locally", "/foundry:certify-local `<release>`", after a release's atoms
  are all merged and before recording operator acceptance (`/foundry:release accept`, see
  `skills/release/SKILL.md`'s tail).
- NEVER as a substitute for the operator's own test pass — this is the machine-derived evidence
  that pass exists to consult, not the sign-off itself (see the Anti-patterns section below).

## Procedure

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/certify-local/certify_local.py" <release-id> \
  [--project-dir <dir>] [--plugin-root <dir>] [--boot-wait <seconds>] [--timeout <seconds>] [--json]
```

1. **Resolve the release manifest** (`scripts/foundry_release.py`'s `load_release` — the SAME
   loader `/foundry:release` drives). An unknown/malformed `<release-id>` is a hard error, no
   emission.
2. **Collect every atom's `journeys[]` tags** (`context/feat-spec-template.md`'s Journeys
   section — the AC-tagged E2E-suite source) and union them into ONE `--grep` regex. **REFUSE**
   ("no journey suite") if no atom in the release declares any tag — nothing to certify.
3. **Resolve the release's target repo** — EVERY atom's `target_repo`, resolved through the SAME
   `_resolve_repo` multi-repo pin every other release primitive uses, then asserted to AGREE
   (compared by resolved directory, not the raw config string). **REFUSE**, naming the split, if
   atoms resolve to differing repos — a release deploys as ONE unit, certify-local cannot boot
   more than one. Then look for a `playwright.config.*` at that repo's root. **REFUSE** ("no
   journey suite") if none exists — journeys are declared but there is no Playwright suite to
   run them.
4. **Resolve the boot recipe — project declaration first, the stack profile as fallback**
   (feat-foundry-boot-recipe-precedence, AC-BRP-1..7). The release's resolved venue's
   `repos.<key>.boot_command` in `.claude/foundry-project.json` **wins whenever it is a
   non-empty string** — the active stack profile is not consulted at all. Otherwise, resolves
   the **active stack profile's** `app_exercise_binding.boot` — via the SAME resolution
   `scripts/foundry-verify.py` uses (`foundry-stack-profile.py`'s `read_lock`/`resolve_lock`,
   imported read-only, never redefined). A malformed/unreadable manifest degrades to the profile
   path (never raises) but is reported on its own line, distinct from "declared nothing"
   (AC-BRP-4/5). **REFUSE** ("no boot recipe") if NEITHER source yields a recipe — naming
   declaring `boot_command` as the always-actionable remedy, and "activate a different stack
   profile" only when a `.foundry/stack-profile.lock` already exists (AC-BRP-3).
5. **Deploy ONCE.** Launch the boot command as a single background process, cwd = the target
   repo root. This is the SAME `make dev`-analog every stack profile already declares — no new
   deploy mechanism.
6. **Run the FULL tagged journey suite** — plain `npx playwright test --grep <pattern>
   --reporter=json` against that one running instance. The JSON reporter is Playwright's OWN
   evidence format; nothing here re-implements or re-formats it.
7. **Tear the boot process down** (SIGTERM, SIGKILL after a grace period) regardless of the
   suite's outcome.
8. **Report per-atom pass/fail** — a GROUPING VIEW over Playwright's own per-test `ok` verdicts:
   for each atom's declared tags, every test whose TITLE contains that tag (the classic
   title-tag `--grep` convention — no separate tag API) contributes its own outcome; a tag with
   zero matching titles is a named miss (declared coverage that was never written), not silently
   dropped. The runner's raw stdout/stderr/JSON report is the evidence attached alongside the
   per-atom table — never a custom score, never a re-judged pass.

## Inputs / Outputs

- In: `<release-id>` (resolved via `CLAUDE_PROJECT_DIR/.foundry/releases/<id>/release.yaml`),
  `.claude/foundry-project.json`'s `repos.<key>.boot_command` (first-precedence) / the active
  `.foundry/stack-profile.lock` (fallback), the target repo's `playwright.config.*` + journey
  spec files.
- Out: `{"verdict": "pass"|"fail", "release_id", "profile_id", "repo_root", "grep_pattern",
  "boot_recipe": {"command", "provenance", "repos_key"},
  "playwright": {...the runner's own output...}, "atoms": {<id>: {"verdict", "tags"}}}`, or a
  `CertifyError` in one of two distinguishable message classes: **`REFUSED (nothing
  dispatched): …`** for every PRE-DISPATCH precondition above (no journey suite / no boot recipe
  / an unknown release / the release's atoms resolving to differing target repos), and
  **`ERRORED mid-run: …`** for a failure AFTER the boot process was launched (it died before the
  suite ran, or the suite itself timed out/failed to launch) — the booted process is always torn
  down before either raises.

## Anti-patterns

- **Treating a certify-local PASS as the delivery sign-off.** It is the machine-derived evidence
  the operator's own test pass consults — the constitution's "operator sign-off is the terminal gate" principle (context/constitution-template.md §I.5), "Delivery sign-off — operator-held, the
  terminal step" states plainly that the operator tests every release themselves and gives the
  final judgement LAST; no gate or CI job asserts that practice ran, and none should.
  `certify-local` feeds that judgement, it does not replace it.
- **A vacuous pass when there is nothing to run.** No journeys declared, or no boot recipe
  resolvable, is always a named REFUSE — never a silent green.
- **Re-implementing Playwright's own reporter/assertion engine.** The evidence IS the runner's
  own JSON report + stdout/stderr; the per-atom table is a grouping view over it, never an
  independently computed verdict.
- **Booting more than once / leaving the boot process running.** One deploy, one run, always
  torn down — matching the charter's "deploy the release once locally" framing exactly.
