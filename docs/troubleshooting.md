# Troubleshooting — symptom first

Find your symptom, run the fix. Every fix here is copy-paste-runnable; if one drifts from
the shipped CLI, that's a bug — file it.

## `/foundry:doctor` is RED

The output names the failing probe. The seven probes and their usual causes:

| Probe | Usual cause | Fix |
|---|---|---|
| `manifest` | corrupted plugin cache | reinstall (see wedged install, below) |
| `hooks` | a hook script missing from the cache | reinstall |
| `skills-frontmatter` | a locally-edited SKILL.md with broken YAML | revert the edit, or reinstall |
| `stack-profile-lock` | `.foundry/stack-profile.lock` points at a profile not in `packs/` | re-run `/foundry:relock`, or remove the lock |
| `operator-registry` | `.claude/foundry-operators.json` missing or invalid | re-run `/foundry:init`, then add yourself |
| `control-plane` | session rooted in a hosted repo, or below the control plane, or a dangling `repos{}` path | see below |
| `permission-floor` | a malformed `docs/permission-floor.json` in the plugin tree (the ONLY case that reddens this probe — see below) | reinstall/update the plugin |

## `/foundry:init` reports a status line, sandbox, or gh-jail finding instead of wiring it

That's expected, not a bug. `/foundry:init` only **verifies and reports** on the
`statusLine`/`subagentStatusLine` wiring, the native Bash sandbox enable, the `gh` jail's
authentication, and the `GH_CONFIG_DIR` session-env carrier — it never writes any of them.
There is **no shipped writer** for any of those four artifacts today (a plugin cannot edit its
own session's confinement), so init's job there is to name what it found and, when nothing is
wired, point at the by-hand remedy. See
[QUICKSTART.md → Before your first session](QUICKSTART.md#before-your-first-session)
for the exact commands and the two `gh` jail caveats (plaintext token at rest; a local logout does
not revoke server-side).

## `foundry doctor` reports `permission-floor` as `[adv ]` (advisory)

`permission-floor` compares the workspace's EFFECTIVE permission configuration — BOTH
`.claude/settings.json` **and** `.claude/settings.local.json` — against the plugin's shipped
`docs/permission-floor.json`. A mismatch here is **advisory, never RED, and never auto-fixed**:
local divergence from the shipped floor is frequently a deliberate operator choice, so the doctor
reports it and stops rather than reddening a legitimate local grant. The one case that DOES redden
this probe (`[XX ]`) is a `docs/permission-floor.json` that fails schema validation — a broken
plugin install, not a configuration mismatch.

The finding that matters most is **`ask-shadowed-ceremony`**: an `ask` rule the map ships as a
front-authorization ceremony (e.g. `foundry-authorize.py`) that is now covered by a broader
`allow` rule. This is exactly what happens when you accept the harness's "always allow" persist
option on a ceremony prompt — it writes the new `allow` into **`.claude/settings.local.json`**
with no second trust dialog, and that file is easy to forget is even consulted. The summary line
leads with **"the front-authorization prompt is not firing"** whenever this fires. Remedy: open
`.claude/settings.local.json`, find the overly-broad `allow` rule the finding names, and narrow or
remove it so the ceremony prompt fires again.

Other findings (`deny-missing`, `stale-plugin-path`, and the informational `allow-absent` /
`unclassified` buckets) are named on their own finding line with a one-line remedy; run
`/foundry:doctor` (not `--session-start`) for the full report — `--session-start` only shows the
actionable lines plus one count of the informational ones.

## `foundry doctor` reports `control-plane` RED

You started the session in the wrong place, or `.claude/foundry-project.json` has a stale
`repos{}` entry — the `control-plane` probe names exactly which:

- **"session rooted in a HOSTED repo"** — you started Claude Code inside a repo an ancestor
  `.claude/foundry-project.json` already names in its `repos{}` (the common case: `claude plugin
  install` enables the plugin user-wide, so it loads there too, pointed at the wrong root). Exit,
  `cd` to the named control plane, and start the session there instead — see
  [multi-repo-control-plane.md](how-to/multi-repo-control-plane.md).
- **"session rooted BELOW a control plane"** — same fix, one level more general: your session
  root is a subdirectory of an ancestor control plane that does not itself name it as a hosted
  repo (e.g. a scratch directory under the plane). `cd` to the named ancestor.
- **"repos.\<key\>.path does not resolve to an existing directory"** — a `repos{}` entry in
  *this* project's own manifest points nowhere. Fix the path, or clone the repo there; an
  unresolved `target_repo` degrades five `/foundry:authorize` grounding floors to warnings (see
  *Authorize printed `warn: … degraded` lines*, below).
- **A deliberately independent adopter nested inside a hosted repo** is a legitimate layout —
  `scripts/foundry_control_plane.py --override <dir>` exits `0` while still printing the finding,
  for scripting around it once you've confirmed it's intentional.
- **A git worktree** (`.git` is a file, not a directory) is never convicted by this check,
  regardless of where it is nested — this factory's own worker-dispatch tooling relies on that.

This check runs at every `--session-start` too, but only **warns** there and still exits `0` — the
operator-invoked `/foundry:doctor` exit code is its only enforcement.

## `gh pr merge` was refused: "the PR being merged cannot be resolved unambiguously"

The guard verifies checks by querying the PR you are merging, and it will not guess which PR
that is. Name it explicitly:

```bash
gh pr merge <pr-number> --repo owner/name --squash        # the PR number + its repo
gh pr merge https://github.com/owner/name/pull/<pr-number> --squash   # or a self-contained URL
```

Common causes: no PR selector at all (the query would fall back to whatever PR your current
branch points at), a `cd "$VAR"` whose target is not a literal path, or `--repo` given without a
value.

**This message is not a check failure.** If checks were genuinely red you would instead see
*"not every check is green"* with the failing rows. This one means the command could not be
pinned to a single pull request — previously the guard would silently verify a *different* PR
that merely shared a number, which could admit a red merge. See
[merge-floor.md](merge-floor.md) → *The git-discipline hook*.

## The merge was refused

The git-discipline hook blocked `gh pr merge`. This is the floor working, not breaking:

- **`--admin` refused** — always, no network call. There is no supported bypass; if the
  checks are wrong, fix the checks.
- **Plain merge refused with a named check** — a check is failing, pending, or unreadable.
  `gh pr checks <n>` shows you the same list the hook saw. Fix the red check; a *pending*
  check means wait, not force.
- **Refused with an API error** — the hook fails closed on any error. Check `gh auth status`
  and network, re-run.

To act around the hook deliberately, run the command yourself in your own terminal — that
human step is exactly the boundary the hook exists to draw
([merge-floor.md](merge-floor.md)).

## `certify-local` refused instead of running

By design it never passes vacuously. The refusal names what's missing:

- **No journeys tagged for an atom** → write the Playwright journeys the contract's AC-IDs
  name, or remove the atom from the release manifest.
- **No boot recipe** → certification resolves the boot recipe with **the project's own
  declaration first**:
  1. `repos.<key>.boot_command` in `.claude/foundry-project.json`, keyed under the release's
     resolved venue (`workspace` for the merge-gate sentinel / single-repo self-host default, or
     the explicit `target_repo` key) — wins whenever it is a non-empty string, and the active
     stack profile is **not consulted at all**.
  2. Otherwise, the **active stack profile's** `app_exercise_binding.boot`, which requires
     `.foundry/stack-profile.lock` to exist and resolve; see `packs/stack-profiles/<yours>/`.

  A refusal always names declaring `boot_command` as the remedy, and additionally names
  "activate a different stack profile" only when a `.foundry/stack-profile.lock` already exists
  (relocking is reachable only once a lock exists — naming it unconditionally would be a
  dead-end pointer).

  If there is no lock yet, create one:

  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-stack-profile.py" --lock <id>[,<id>…]
  ```

  (`/foundry:init` offers this during onboarding; run it directly to adopt a profile later.) It
  refuses — with no write — if a lock already exists (run `/foundry:relock` to refresh instead),
  the lock file present is corrupt (the refusal names the remedy), an id is unknown (the refusal
  lists the ids available under `packs/stack-profiles/`), or any named id is schema-invalid,
  core-incompatible, or leaks into the core plugin's `skills/` bundle.

  > **Resolved (previously known limitations).** Earlier releases shipped no lock-create verb —
  > `/foundry:relock` only refreshed an existing lock ("nothing to relock") — and
  > `repos.<key>.boot_command` was accepted by the schema but never read. Both are fixed in this
  > release: `--lock` (above) creates the lock (`feat-foundry-stack-profile-lock-create`), and
  > `boot_command` is now the first-precedence boot recipe
  > (`feat-foundry-boot-recipe-precedence`) — certification is reachable from a clean install by
  > either path.

## The authorize gate refused to freeze

- **"DRAFT — no review recorded"** → run `/foundry:spec-review <spec>` first; its evidence
  row is the precondition.
- **Contract validation failed** → the output names the freeze floor that failed. Fix the
  contract (re-specify); the gate is never the thing to relax.
- **Oversize spec** → the spec size ceiling (fourteen criteria / eight thousand words) has no override. Decompose into
  smaller atoms.

## Authorize printed `warn: … degraded` lines and froze anyway

**Read these before you confirm — they mean less was checked than usual.**

When a contract's `target_repo` names a `repos{}` key whose `path` does not resolve to a real
directory, there is no venue root to ground against, and five floors degrade to a printed
warning instead of running:

| Floor | Warning |
|---|---|
| surface ⊆ scope | `surface⊆scope check degraded` |
| doctor-row baseline | `doctor-row-baseline check degraded` |
| system-grounding | `system-grounding floor SKIPPED` |
| `allowed_paths` grounding | `allowed_paths grounding degraded` |
| checkpoint-locator grounding | `checkpoint locator grounding degraded` |

The freeze then proceeds and `auth_seq` still increments. This is deliberate — it exists so a
repo you simply have not cloned yet cannot wedge an authorization — but it means **a typo in
`target_repo` looks exactly like a not-yet-cloned repo**.

- **You expected the degrade** (the repo genuinely is not cloned here): fine, carry on.
- **You did not**: you have a manifest defect. Check `target_repo` against the `repos{}` keys in
  `.claude/foundry-project.json`, fix it, and re-authorize — the earlier freeze validated far
  less than a normal one.

Note that the *absent* `target_repo` case behaves oppositely and **fails closed** with
*"matches ZERO paths under the venue root"*, because the scope then grounds against the
workspace root and matches nothing.

## Wedged or stale install

If `claude plugin list` shows a stale version, doctor reports drift you can't explain, or a
second install source got wired in by mistake, recover cleanly rather than debugging in place:

```bash
# 1. uninstall the plugin from this session's config
claude plugin uninstall foundry@agentic-foundry

# 2. remove the marketplace registration
claude plugin marketplace remove lukasrepublic/agentic-foundry

# 3. clear the plugin cache (default location; see docs/QUICKSTART.md's "Where things live" for
#    the fleet-doctor lookup-root override, which does not move this cache)
rm -rf ~/.claude/plugins/cache/

# (the same cache clear also resolves a stale registry entry in
#  ~/.claude/plugins/installed_plugins.json — the uninstall step above rewrites it, but a
#  manual edit is safe if it doesn't)

# 4. reinstall clean
claude plugin marketplace add lukasrepublic/agentic-foundry#v1.5.0
claude plugin install foundry@agentic-foundry
```

```
/foundry:doctor        # expect: DOCTOR-GREEN
```

**Do not stack install sources.** The marketplace install and a directory-sourced local
plugin (`claude --plugin-dir`) expand `${CLAUDE_PLUGIN_ROOT}` to different roots, so hooks
wired from both fire twice with possibly-different versions. Pick one source per repo.

## Hooks firing twice

You stacked install sources — see directly above.

## A gate went red in CI after a fork PR

The leak gate degrades on fork PRs (secrets don't resolve there) and reports the term scan
as **DEGRADED — NOT RUN**, never as a pass. On any *other* event, an empty denylist means
the repository secret is unset — set it, don't bypass the gate.

## Still stuck?

Open an issue with the doctor output and the exact refusal text — every refusal is written
to be quotable.
