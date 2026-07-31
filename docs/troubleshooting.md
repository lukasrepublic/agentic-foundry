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
- **No boot recipe** → your stack profile must define how to deploy the release once
  locally; see `packs/stack-profiles/<yours>/`.

## The authorize gate refused to freeze

- **"DRAFT — no review recorded"** → run `/foundry:spec-review <spec>` first; its evidence
  row is the precondition.
- **Contract validation failed** → the output names the freeze floor that failed. Fix the
  contract (re-specify); the gate is never the thing to relax.
- **Oversize spec** → the spec size ceiling (fourteen criteria / eight thousand words) has no override. Decompose into
  smaller atoms.

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
