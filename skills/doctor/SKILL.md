---
name: doctor
description: Foundry health check (/foundry:doctor) — a thin, six-check probe (the v0.25.0 test-suite realignment shrank this from a 2,900-line drop-in-check registry to one file). Checks the plugin manifest loads, hooks.json parses with every referenced hook script present, every skills/*/SKILL.md frontmatter YAML-parses, the stack-profile lock (if any) resolves, the operator registry resolves, and the control-plane preflight (no dangling repos{} path, no ancestor manifest already governing this project dir). Plus five advisory-only lines never counted toward RED — permissions-policy, agent-teams, branches, statusline (which names the first missing piece of the token-bar wiring), and retired-artifacts (what the updater's sweep would remove under --cleanup). Fails CLOSED for an operator-invoked check (exit non-zero on any hard failure); the --session-start cadence is advisory (fail-open, never wedges a session). Trigger when the operator says "/foundry:doctor", "foundry health check", or to diagnose why a session looks unhealthy.
---

# /foundry:doctor

The Foundry self-diagnostic — a **thin, six-check probe** (the v0.25.0 test-suite realignment;
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
   The six checks, every run:
   1. **`manifest`** — `.claude-plugin/plugin.json` loads as JSON and carries a `version`.
   2. **`hooks`** — `hooks/hooks.json` parses as JSON and every referenced hook command script
      exists on disk.
   3. **`skills-frontmatter`** — every shipped `skills/*/SKILL.md` frontmatter YAML-parses (a
      colon-in-a-plain-scalar defect class that is cheap to catch here, expensive live).
   4. **`stack-profile-lock`** — `.foundry/stack-profile.lock` (if present) resolves against the
      shipped `packs/` tree; absent lock is `ok` ("not applicable"), not a failure. A lock whose
      ONLY difference is an OLDER version of the same profile id this installed plugin ships (the
      normal state right after a plugin update) is **ADVISORY** — `lock behind the profile version
      this plugin ships (<id> <old>→<new>) — run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-stack-profile.py" --relock``
      (AC-V118C-6). Any other mismatch — an id the plugin does not ship, a downgrade, a same-version
      content change, an unreadable lock — stays RED.
   5. **`operator-registry`** — `.claude/foundry-operators.json` resolves via `foundry_authz`.
   6. **`control-plane`** (feat-foundry-control-plane-preflight, AC-CPP-1/-2/-3/-3b) — no dangling
      `repos{}` path in this project's own manifest, and no ancestor
      `.claude/foundry-project.json` already names or governs this project directory as a hosted
      repo — a mistake-catcher, not a floor.

   The standalone `permission-floor` drift probe (feat-foundry-doctor-permission-floor-check,
   AC-DPF-1..8) was retired by subtraction-wave (autonomy-continuation R4, AC-SUB-1c). Nothing
   replaces it, and nothing needs to: since v1.18.0 the floor writes only its deny rows, and the
   plugin's own scripts run through the `foundry-plugin-scripts-allow.py` PreToolUse hook (Bash
   rules naming a script path never matched a real invocation — measured).

   Plus advisory-only lines, rendered the same way but never counted toward `DOCTOR-RED`:
   - **`permissions-policy`** (feat-foundry-authorization-capability-preflight-at-dispatch,
     AC-CPD-4 — replaces the R1 drift-only advisory, feat-foundry-authorization-standing-grants-
     as-policy AC-SGP-6) — runs `scripts/foundry-capability-preflight.py` over every atom of every
     ACTIVE release under `.foundry/releases/*/release.yaml`, printing `preflight over <n> active
     atom(s): <d> denied[, <c> not pre-granted]` — only a capability a DENY rule would refuse is a
     blocker; not pre-granted means the session's permission mode decides at run time — followed by
     `; policy absent|in-sync|drift (<k>) (.foundry/permissions.yaml vs .claude/settings.json)`, the
     same derivation `foundry-permissions-compile.py --check` runs, naming the two files compared.
     ADVISORY only on a denial or drift. Never RED.
   - **`agent-teams`** (feat-agent-teams-enablement, AC-ATE-4) — `agent-teams: on (settings env)`
     or `agent-teams: off`, derived from whether the effective settings files' `env` block sets
     `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` to `"1"` (`~/.claude/settings.json`, then the
     project's `.claude/settings.json`, then `.claude/settings.local.json`, ascending precedence).
     Never RED: flipping the flag is an adopter opt-in — see `docs/how-to/agent-teams.md`.
   - **`retired-artifacts`** (hotfix-v1.17.4, ER #236) — `retired-artifacts: none present` or `<n>
     present (<paths>) — run `npx update-agentic-workspace@<v> --cleanup``: files an earlier release wrote
     and no current release reads, from the catalogue the CLI ships (`cli/retired-artifacts.json`).
     Never RED: the updater reports them every run and removes them only under `--cleanup`.
     Every updater remedy the doctor prints names `npx update-agentic-workspace@<v>`, where `<v>` is
     this plugin's own `cli-update/package.json` version — never the bare name, which can run a
     stale npx cache (AC-V118C-8). The `statusline` line reads the `installed_plugins.json` record
     whose `projectPath` is this project, then the user-scope record — never another project's.
   - **`branches`** (branch-and-worktree-discipline, AC-BWD-3) — `branches: <n>
     merged-not-deleted, <m> stale worktrees`, computed by importing
     `scripts/foundry-worktree-gc.py`'s own classifier (ancestry-only, no live `gh` call — this
     stays a cheap, offline, every-run probe) over the session's own project dir. The line says
     what it measured (ER #244): `(ancestry only; squash-merged branches need the gc's gh check)`,
     plus `; <n> ref(s) outside the glob set — widen with --include` when the built-in prefixes
     dropped any — on a squash-merge repo ancestry alone reads 0, so run the gc itself for a live
     count. Reads `n/a (not a git checkout)` when it is not one. **ADVISORY** when `<n>` > 0 (it
     names the gc's `--dry-run` then `--apply`); `ok` at 0. Never RED: a repo-wide sweep is an operator/agent action
     (`--apply`, `ask`-tiered), never a doctor-enforced one — see
     `docs/how-to/branching-and-cleanup.md`.

   Each probe is individually crash-proof — an unexpected exception inside one check is reported
   as that check's own RED result (`probe crashed: <type>: <detail>`), never an uncaught
   traceback, so a single broken probe can never mask the others or wedge the advisory
   `--session-start` cadence.

2. **Interpret**:
   - `DOCTOR-GREEN` → all six checks passed.
   - `DOCTOR-RED` (exit 1) → at least one hard check failed; the `[XX ]`-marked line names it.
     Fix the named defect (edit the manifest / hooks.json / the offending skill frontmatter /
     re-lock the stack profile / fix the operator registry / resolve the named control-plane
     finding) and re-run.

3. **`--session-start` — advisory cadence, never blocks.** Wired into the SessionStart hook; on a
   failure it prints a `WARNING:` to stderr (naming the real merge-side floor) and **always exits
   0** — it never wedges a session, unlike the operator-invoked form above.

4. **`--heal` — a documented no-op.** `foundry-doctor.py --heal` prints
   `foundry doctor --heal: no-op (wiring auto-heal retired)` and exits 0. The
   wiring-hash-pin auto-heal machinery it used to drive (`.foundry/wiring-hash.pin`,
   `TRUSTED_ADVANCE`/`TAMPER`/`STALE` classification) was retired along with
   `foundry-wiring-hash.py` and `foundry-merge-gate.py` — there is no wiring-hash check in the
   six above and nothing for `--heal` to do. It is kept callable only so a SessionStart hook or a
   muscle-memory `/foundry:doctor --heal` from an older session does not hard-error.

5. **`--repo <owner/repo>` — accepted for back-compat, unused.** The branch-protection
   required-status check this flag used to drive was retired with `foundry-merge-gate.py`; the
   flag is still accepted (so an existing invocation does not error) but has no effect.

## Inputs

- The Foundry plugin tree (`.claude-plugin/plugin.json`, `hooks/hooks.json`, `skills/*/SKILL.md`).
- `.foundry/stack-profile.lock` (if present) + the shipped `packs/` tree.
- `.claude/foundry-operators.json` (operator registry).
- This project's own manifest + any ancestor `.claude/foundry-project.json` (control-plane).
- `~/.claude/settings.json` + `.claude/settings.json` + `.claude/settings.local.json` (advisory-only
  `agent-teams` line, ascending precedence).
- The session's own project dir's git refs/worktrees, read-only, via `scripts/foundry-worktree-
  gc.py`'s classifier (advisory-only `branches` line; `n/a` when the project dir is not a git repo).

## Outputs

- A per-check table (`[ok ]`/`[XX ]`/`[skip]` per check) + a `DOCTOR-GREEN` / `DOCTOR-RED`
  verdict (exit 0 / 1).

## Anti-patterns

- **Treating a DOCTOR-GREEN as proof the merge floor is sound.** It checks six structural
  invariants, not the merge-time floor — `.github/workflows/ci.yml` + `btb-gates` own that.
- **Treating `--session-start`'s advisory WARNING as an enforcement signal.** It fails open by
  design; the real enforcement is at merge time, not session start.
- **Expecting `--heal`/`--repo` to do anything.** Both are documented no-ops/back-compat shims —
  see steps 4/5 above.
