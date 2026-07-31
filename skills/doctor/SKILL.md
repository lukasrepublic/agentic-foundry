---
name: doctor
description: Foundry health check (/foundry:doctor) — a thin, five-check probe (the v0.25.0 test-suite realignment shrank this from a 2,900-line drop-in-check registry to one file). Checks the plugin manifest loads, hooks.json parses with every referenced hook script present, every skills/*/SKILL.md frontmatter YAML-parses, the stack-profile lock (if any) resolves, and the operator registry resolves. Fails CLOSED for an operator-invoked check (exit non-zero on any hard failure); the --session-start cadence is advisory (fail-open, never wedges a session). Trigger when the operator says "/foundry:doctor", "foundry health check", or to diagnose why a session looks unhealthy.
---

# /foundry:doctor

The Foundry self-diagnostic — a **thin, five-check probe** (the v0.25.0 test-suite realignment;
`foundry-doctor.py` shrank from a 2,900-line drop-in-check registry, `--selftest` CLIs and all, to
one file). The
load-bearing behavioral assertions this file used to re-discover from the retired per-check
registry now live in the **one pytest suite** (`tests/`), run by CI on every PR —
`foundry-doctor.py` is a cheap, fast, every-session-safe probe, not the enforcement floor. The
**real merge-side enforcement** is the native floor (`.github/workflows/ci.yml` + the `btb-gates`
lane signal — Tier B advisory) plus `hooks/foundry-git-discipline.sh`'s deterministic `gh` clause.
`doctor` is a mistake-catcher for the operator, not a merge gate.

## When to trigger

- Operator: "/foundry:doctor", "is Foundry healthy?", "why does my session look off?".
- After editing `hooks/hooks.json`, any `skills/*/SKILL.md` frontmatter, or a stack-profile lock.
- Before a release, as a cheap sanity pass (the binding pre-cut gate is
  `foundry-release-acceptance.py`, which itself requires `DOCTOR-GREEN` — see
  `skills/release/SKILL.md`'s pre-cut acceptance gate).

## Procedure

1. **Run the doctor**:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-doctor.py"
   ```
   The five checks, every run:
   1. **`manifest`** — `.claude-plugin/plugin.json` loads as JSON and carries a `version`.
   2. **`hooks`** — `hooks/hooks.json` parses as JSON and every referenced hook command script
      exists on disk.
   3. **`skills-frontmatter`** — every shipped `skills/*/SKILL.md` frontmatter YAML-parses (a
      colon-in-a-plain-scalar defect class that is cheap to catch here, expensive live).
   4. **`stack-profile-lock`** — `.foundry/stack-profile.lock` (if present) resolves against the
      shipped `packs/` tree; absent lock is `ok` ("not applicable"), not a failure.
   5. **`operator-registry`** — `.claude/foundry-operators.json` resolves via `foundry_authz`.

   Each probe is individually crash-proof — an unexpected exception inside one check is reported
   as that check's own RED result (`probe crashed: <type>: <detail>`), never an uncaught
   traceback, so a single broken probe can never mask the others or wedge the advisory
   `--session-start` cadence.

2. **Interpret**:
   - `DOCTOR-GREEN` → all five checks passed.
   - `DOCTOR-RED` (exit 1) → at least one hard check failed; the `[XX ]`-marked line names it.
     Fix the named defect (edit the manifest / hooks.json / the offending skill frontmatter /
     re-lock the stack profile / fix the operator registry) and re-run.

3. **`--session-start` — advisory cadence, never blocks.** Wired into the SessionStart hook; on a
   failure it prints a `WARNING:` to stderr (naming the real merge-side floor) and **always exits
   0** — it never wedges a session, unlike the operator-invoked form above.

4. **`--heal` — a documented no-op.** `foundry-doctor.py --heal` prints
   `foundry doctor --heal: no-op (wiring auto-heal retired)` and exits 0. The
   wiring-hash-pin auto-heal machinery it used to drive (`.foundry/wiring-hash.pin`,
   `TRUSTED_ADVANCE`/`TAMPER`/`STALE` classification) was retired along with
   `foundry-wiring-hash.py` and `foundry-merge-gate.py` — there is no wiring-hash check in the
   five above and nothing for `--heal` to do. It is kept callable only so a SessionStart hook or a
   muscle-memory `/foundry:doctor --heal` from an older session does not hard-error.

5. **`--repo <owner/repo>` — accepted for back-compat, unused.** The branch-protection
   required-status check this flag used to drive was retired with `foundry-merge-gate.py`; the
   flag is still accepted (so an existing invocation does not error) but has no effect.

## Inputs

- The Foundry plugin tree (`.claude-plugin/plugin.json`, `hooks/hooks.json`, `skills/*/SKILL.md`).
- `.foundry/stack-profile.lock` (if present) + the shipped `packs/` tree.
- `.claude/foundry-operators.json` (operator registry).

## Outputs

- A per-check table (`[ok ]`/`[XX ]`/`[skip]` per check) + a `DOCTOR-GREEN` / `DOCTOR-RED`
  verdict (exit 0 / 1).

## Anti-patterns

- **Treating a DOCTOR-GREEN as proof the merge floor is sound.** It checks five structural
  invariants, not the merge-time floor — `.github/workflows/ci.yml` + `btb-gates` own that.
- **Treating `--session-start`'s advisory WARNING as an enforcement signal.** It fails open by
  design; the real enforcement is at merge time, not session start.
- **Expecting `--heal`/`--repo` to do anything.** Both are documented no-ops/back-compat shims —
  see steps 4/5 above.
