---
name: repos
description: The governed-repo fleet verbs (/foundry:repos <sync|status|foreach|validate>) over the repos{} registry — clone the not-cloned, fetch the matching, report the rest; never confused with /foundry:fleet, the SESSION ROSTER (one row per active Claude Code session, no repository governed at all). Trigger to bring a fresh clone of the workspace up to date, check every hosted repo's status at a glance, run a command across them, or catch an undeclared checkout.
---

# /foundry:repos

The control plane's fleet verbs over `.claude/foundry-project.json`'s `repos{}` — the
repo/vcstool/meta/myrepos category's universal triad (`sync` / `status` / `foreach`), plus the
round-trip validator (`validate`). `scripts/foundry_repo_fleet.py` consumes
`scripts/foundry_repo_registry.py`'s read-only classification — it does not re-derive it — and
adds the one thing the registry atom deliberately left out: verbs that **act**.

**Not to be confused with `/foundry:fleet`.** `/foundry:fleet` is the **session roster** — one row
per active Claude Code session, governing no repository at all. `/foundry:repos` governs the
`repos{}` manifest's hosted repositories. The two are fleet-shaped and share nothing else.

## The four verbs

| verb | what it does |
|---|---|
| `sync` | idempotent reconcile: **clone** every `not-cloned` row that declares a `remote`, **fetch** every `present` row whose `origin` is exactly `match`, report everything else untouched |
| `status` | one honest line per `repos.<key>` entry: present · origin · branch · ahead/behind · dirty |
| `foreach -- <cmd…>` | shell-free argv fan-out of `<cmd…>` over the present repos, fail-collecting — one failing repo never stops the rest |
| `validate` | the manifest ⟷ reality ⟷ gitignore round trip, **including the reverse direction**: a git checkout on disk with no `repos{}` row is reported `undeclared-checkout` |

```
scripts/foundry_repo_fleet.py sync     --root . [--json] [--timeout SECONDS]
scripts/foundry_repo_fleet.py status   --root . [--json]
scripts/foundry_repo_fleet.py foreach  --root . [--json] [--timeout SECONDS] -- git status --short
scripts/foundry_repo_fleet.py validate --root . [--json] [--max-depth N]
```

## Clone and fetch are the entire mutation vocabulary

No checkout, reset, merge, rebase, pull, push, clean, stash, branch, remote or submodule command —
and no `--force`/`-f`/`--hard`/`--force-sync` — exists anywhere in this tool's vocabulary. An
existing checkout is **never rewritten**: a dirty, diverged, or origin-mismatched row is reported,
never touched. `fetch` moves remote-tracking refs and nothing else; advancing a working tree is the
operator's move (or the child repo's own loop), never this control plane's.

## Security posture — read before running `sync` against an unfamiliar manifest

- **Egress is bounded.** `clone`/`fetch` only ever reach a remote a `repos.<key>` row **declares**
  — re-validated at the boundary (admitted form, no leading `-`, no control character, physical
  path confinement) independently of what the row classification claims, before any socket opens.
- **Every git child is hardened**, network-capable or not: `credential.helper=`, `core.askPass=`
  with the askpass env removed, `core.fsmonitor=`, `core.sshCommand=ssh -o BatchMode=yes` with
  `GIT_SSH_COMMAND` removed, `protocol.allow=never` with only https/ssh/file admitted, submodule
  recursion off. A cloned or fetched-into checkout's own hostile `.git/config` cannot direct or
  execute anything at this tool.
- **No promise that a cloned tree is inert.** A cloned repository's content is **untrusted**: this
  tool clones and fetches, it does not sandbox, review, or vouch for what it retrieves. Inside a
  Claude Code workspace root, a cloned repo's `CLAUDE.md`, `.claude/**` and `.mcp.json` become
  discoverable configuration for sessions rooted there — the same blast radius
  `docs/how-to/multi-repo-control-plane.md`'s session section already states for the control plane
  generally.
- **The manifest IS the declared consent.** A `repos.<key>` row with a `remote` is a committed,
  reviewable statement that this workspace governs that repository; `sync` never prompts before a
  first clone — write access to `.claude/foundry-project.json` is the egress authority.

## Exercise

`python3 -m pytest tests/test_repo_fleet.py -q` — the real module driven over materialized
`tmp_path` fixtures (git-init'd checkouts, local-path remotes, planted hostile `.git/config`
values, captured argv + child env). No gate consumes any verb's exit code; every exit code here is
advisory (`0` clean, `2` findings, `1` when the manifest is absent/unreadable/unparseable).
