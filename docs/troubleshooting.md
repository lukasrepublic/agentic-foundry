# Troubleshooting — symptom first

Find your symptom, run the fix. Every fix here is copy-paste-runnable; if one drifts from
the shipped CLI, that's a bug — file it.

## `/foundry:doctor` is RED

The output names the failing probe. The five probes and their usual causes:

| Probe | Usual cause | Fix |
|---|---|---|
| `manifest` | corrupted plugin cache | reinstall (see wedged install, below) |
| `hooks` | a hook script missing from the cache | reinstall |
| `skills-frontmatter` | a locally-edited SKILL.md with broken YAML | revert the edit, or reinstall |
| `stack-profile-lock` | `.foundry/stack-profile.lock` points at a profile not in `packs/` | re-run `/foundry:relock`, or remove the lock |
| `operator-registry` | `.claude/foundry-operators.json` missing or invalid | re-run `/foundry:init`, then add yourself |

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
- **No boot recipe** → certification resolves the boot command from the **active stack
  profile's** `app_exercise_binding.boot`, which requires `.foundry/stack-profile.lock` to
  exist and resolve; see `packs/stack-profiles/<yours>/`.

  > **Known limitation (v1).** No shipped verb creates that lock. `/foundry:relock` refreshes
  > an existing one and refuses when there is none ("nothing to relock"), so an adopter who has
  > never had a lock cannot reach `certify-local`, `/foundry:verify`, or the `id-*` lane's
  > `infra_binding`. The `repos.<key>.boot_command` field in `.claude/foundry-project.json` is
  > accepted by the schema but is **not** read by certification today. Wiring that field as the
  > first-precedence boot recipe, and folding lock creation into `/foundry:init` (the
  > `terraform init` shape), are tracked fixes — until they land, this path is not reachable
  > from a clean install and we would rather say so than let you hunt for the flag.

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

# 3. clear the plugin cache (default location; FOUNDRY_PLUGINS_DIR overrides it if you set one)
rm -rf ~/.claude/plugins/cache/

# (the same cache clear also resolves a stale registry entry in
#  ~/.claude/plugins/installed_plugins.json — the uninstall step above rewrites it, but a
#  manual edit is safe if it doesn't)

# 4. reinstall clean
claude plugin marketplace add lukasrepublic/agentic-foundry#v1.0.0
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
