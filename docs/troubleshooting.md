# Troubleshooting — symptom first

Find your symptom, run the fix. Every fix here is copy-paste-runnable; if one drifts from
the shipped CLI, that's a bug — file it.

## `/foundry:doctor` is RED

The output names the failing probe. The seven probes and their usual causes:

| Probe | Usual cause | Fix |
|---|---|---|
| `python-deps` | `python3` cannot import `yaml` or `jsonschema` (a fresh machine or container) | run `npx update-agentic-workspace@latest` — it installs what is missing |
| `manifest` | corrupted plugin cache | reinstall (see wedged install, below) |
| `hooks` | a hook script missing from the cache | reinstall |
| `skills-frontmatter` | a locally-edited SKILL.md with broken YAML | revert the edit, or reinstall |
| `stack-profile-lock` | `.foundry/stack-profile.lock` points at a profile not in `packs/` | re-run `/foundry:relock`, or remove the lock |
| `operator-registry` | `.claude/foundry-operators.json` missing or invalid | re-run `/foundry:init`, then add yourself |
| `control-plane` | session rooted in a hosted repo, or below the control plane, or a dangling `repos{}` path | see below |

## `/foundry:init` reports a status line, sandbox, or gh-jail finding instead of wiring it

That's expected, not a bug. `/foundry:init` only **verifies and reports** on the
`statusLine` wiring, the native Bash sandbox enable, the `gh` jail's
authentication, and the `GH_CONFIG_DIR` session-env carrier — it never writes any of them.
The status-line wiring has a shipped writer since v1.17.0: `npx update-agentic-workspace@latest` (and
`create-agentic-workspace --existing --reconcile-floor` on a trusted workspace) installs the
wrappers and adds the keys when absent. The other three still have **no shipped writer** (a
plugin cannot edit its own session's confinement), so init's job there is to name what it found
and, when nothing is wired, point at the by-hand remedy. See
[QUICKSTART.md → Before your first session](QUICKSTART.md#before-your-first-session)
for the exact commands and the two `gh` jail caveats (plaintext token at rest; a local logout does
not revoke server-side).

## `foundry doctor` reports a `permissions-policy` advisory line

Doctor renders one ADVISORY line, never `[adv ]`-paired with the seven structural probes above and
never RED by design — a stale-permission workspace must never wedge a session. It is the policy
drift state, the same derivation `foundry-permissions-compile.py --check` runs, naming the two files
it compares: `policy absent`, `policy in-sync`, or `policy drift (<k>)`, each followed by
`(.foundry/permissions.yaml vs .claude/settings.json)`.

Remedies:

- **`policy drift (<k>)`** — reconcile the compiled settings from the policy source (`--check` is
  read-only; `--write` reconciles):

  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-permissions-compile.py" --check
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-permissions-compile.py" --write
  ```

- **Checking a declared capability set directly** — to see which deny rule refuses which
  capability a contract declares, run the preflight; whether to lift a deny is your call:

  ```bash
  python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-capability-preflight.py" --contract <path-to-acceptance-contract.yaml>
  ```

- **`policy absent`** — no `.foundry/permissions.yaml` yet; not itself a problem. The line names
  the remedy: the updater seeds an empty, commented starter (operator-owned
  from then on, never reconciled), or copy the plugin's `context/permissions-template.yaml`. See
  [how-to/standing-grants.md](how-to/standing-grants.md) for writing and compiling a grant.

## The token bar (`tok ██████░░░░ 69%`) is missing from the status line

The status line is rendered by the plugin's `scripts/foundry-statusline.sh`, reached through a thin
wrapper at `.claude/hooks/foundry-statusline.sh` and a `statusLine` key in `.claude/settings.json`.
Run `/foundry:doctor` and read its `statusline:` advisory line: it names the FIRST missing piece —
no `statusLine` key, wrapper absent, wrapper without the framework marker (yours, never touched),
or no renderer resolvable from this machine (with the config root it looked under). The first two
are fixed by `npx update-agentic-workspace@latest`, which wires both on a trusted workspace and refreshes a
framework-owned wrapper. Even with no renderer the wrapper now prints `⌂ <dir>:<branch> · tok <bar> NN%`
itself, so a plainer line means "renderer not found", never "nothing configured".

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

## No stack-profile lock yet

`/foundry:verify` and the `id-*` lane gate on an active stack profile. If there is no
`.foundry/stack-profile.lock` yet, create one:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-stack-profile.py" --lock <id>[,<id>…]
```

(`/foundry:init` offers this during onboarding; run it directly to adopt a profile later.) It
refuses — with no write — if a lock already exists (run `/foundry:relock` to refresh instead),
the lock file present is corrupt (the refusal names the remedy), an id is unknown (the refusal
lists the ids available under `packs/stack-profiles/`), or any named id is schema-invalid,
core-incompatible, or leaks into the core plugin's `skills/` bundle. A lockless workspace is a
fully-supported, `DOCTOR-GREEN` state.

## The authorize gate refused to freeze

- **Contract validation failed** → the output names the freeze floor that failed. Fix the
  contract (re-specify); the gate is never the thing to relax.

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
# Run every numbered step in EACH scope that carries a registration -- user AND project.
# A bare invocation (no --scope) touches only the default scope, and a stale scope silently
# shadows a fresh one (a stale project-scoped row shadows a fresh user-scoped one), which is
# how this recovery itself goes stale if run half-scoped.

# 1. uninstall the plugin from this session's config
claude plugin uninstall foundry@agentic-foundry --scope user
claude plugin uninstall foundry@agentic-foundry --scope project

# 2. remove the marketplace registration
claude plugin marketplace remove agentic-foundry --scope user
claude plugin marketplace remove agentic-foundry --scope project

# 3. clear the plugin cache (default location) -- filesystem, not scoped, so this runs once
rm -rf ~/.claude/plugins/cache/

# (the same cache clear also resolves a stale registry entry in
#  ~/.claude/plugins/installed_plugins.json — the uninstall step above rewrites it, but a
#  manual edit is safe if it doesn't)

# 4. reinstall clean, per scope
claude plugin marketplace add lukasrepublic/agentic-foundry --scope user
claude plugin install foundry@agentic-foundry --scope user
claude plugin marketplace add lukasrepublic/agentic-foundry --scope project
claude plugin install foundry@agentic-foundry --scope project
```

```
/foundry:doctor        # expect: DOCTOR-GREEN
```

**A lighter touch, when nothing is actually wedged.** The recovery above is blunt on purpose — it
discards every version of every plugin from every marketplace, which is right when nothing on disk
can be trusted. If the install itself is fine and you just want a stale/duplicate marketplace registration gone,
`npx update-agentic-workspace@latest --cleanup` is the surgical alternative: it removes only a
registration no scope still enables, and lists superseded plugin-cache versions without deleting them
(v1.18.2: deleting one out from under a running session broke every hook in it — close your sessions
first if you remove one by hand) — never the blunt `rm -rf` above. Run it without `--cleanup`
first to preview what it would remove; nothing is deleted until you pass the flag.

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
