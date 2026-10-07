# Agentic Foundry

**Ticket-first delivery for agent-built software — as a Claude Code plugin.**

One artifact per change: **a ticket with a runnable "Done means".** The agent builds it on a
branch, runs the repository's own tests locally until green, opens one PR, and your platform —
branch protection and CI — decides the merge. A separate security lane covers auth, secrets and
custody changes. Specs, acceptance contracts and per-change authorization still exist, but they
are an **opt-in lane** the operator chooses, not the default path.

> Philosophy in one line: **gates make problems visible, not impossible** — and every gate has
> to earn its keep. A gate ships only if it names the observed failure it prevents; the
> operator's own judgment is what the automation serves, never what it replaces.

**Status: v1.18.5.** The 2.0 delivery rebase is in progress. Built solo, dogfooded daily.
The default path is on one page: **[context/operating-model.md](context/operating-model.md)**.

## The loop, in one picture

```
 YOU (operator)          THE AGENT                 YOUR PLATFORM (GitHub/CI)
 ──────────────          ─────────                 ─────────────────────────
      │
      │  a ticket: what / why / Done means (a command) / Paper allowed
      ▼
 ┌──────────────┐    ┌─────────────────────────┐
 │ foundry-ticket│───▶│ active ticket recorded; │   the paper guard refuses writes to
 │ start <issue> │    │ Done means = the check  │   specs/, docs/, .foundry/ … unless
 └──────────────┘    └─────────────────────────┘   the ticket allows them
                                │
                                ▼
                  ┌─────────────────────┐
                  │ dispatch: build it  │       isolated git worktree;
                  │ on a branch, run    │──────▶ foundry-test.sh until green
                  │ `Done means`        │           │ (local-green recorded for HEAD)
                  └─────────────────────┘           ▼
                                          ┌──────────────────────┐
                                          │   one PR per ticket  │
                                          │ push + non-draft PR   │
                                          │ refused until green   │
                                          └──────────┬───────────┘
                                                     ▼
                                          ┌──────────────────────┐
                                          │   THE MERGE FLOOR    │
                                          │ your branch protection│
                                          │ + CI checks, tiered   │
                                          │ honestly (A/B)        │
                                          └──────────┬───────────┘
      ┌──────────────────────────────────────────────┘ merged
      ▼
 ┌───────────────┐
 │  SIGN-OFF     │  ← you test it yourself. A practice,
 │  (yours)      │    deliberately not a machine gate.
 └───────────────┘
```

The Stop hook runs `Done means`: while it fails the session keeps working (three refusals, then it
may stop with the failure named). The platform is the gate; the operator's own test pass is the last
step.

## See it in action

The default loop, as you'd actually run it:

```text
> gh issue create --title "Repoint beta DNS" --body-file ticket.md
  … ticket.md: ## What / ## Why / ## Done means (one fenced command) / ## Paper allowed

> python3 "$CLAUDE_PLUGIN_ROOT/scripts/foundry-ticket.py" start 42
  … records the ticket in .claude/foundry-ticket.json (gitignored)

> /foundry:dispatch 42
  … an implementer persona builds it in an isolated worktree, runs foundry-test.sh until green,
    pushes once and opens one PR
  … your CI + branch protection decide the merge (the plugin's git-discipline hook
    refuses --admin bypasses and merges ahead of green checks — fail-closed)

> /foundry:merge-when-green 87
  … on your explicit instruction: waits for every check, merges the instant the PR is CLEAN
```

And the artifact the whole loop pivots on — a ticket you can read in one screen:

````markdown
## What
Rate-limit the public API.
## Done means
```
npx playwright test tests/rate-limit.spec.ts
```
## Paper allowed
docs/runbooks/rate-limit.md
````

## Where you run it: one repo, or a control plane over many

Foundry works **inside a single repository** — install it, `/foundry:init`, and the loop below is
live in that repo. That is the fastest way to try it, and the Quickstart takes that path.

But it is **designed to be operated from a control plane**: a small *workspace* repo that holds
your tickets and runbooks and hosts your code repositories as gitignored siblings, with the factory
dispatching work into each of them.

```
   SINGLE REPO                          CONTROL PLANE  (what it is built for)
   ───────────                          ─────────────

   your-app/                            acme-handbook/          ◀── the workspace; you run
   ├── .claude/  ← the wiring           ├── .claude/                Claude from HERE
   ├── docs/     ← the runbooks         ├── docs/               ◀── the WHAT for every repo
   └── src/      ← the code             │
                                        ├── api/    ◀── its own git repo, gitignored
                                        ├── web/    ◀── its own git repo, gitignored
                                        └── infra/  ◀── its own git repo, gitignored
```

Why it matters: real projects are an app, some services, and the infrastructure under them. The
control plane keeps **one workspace** (tickets, runbooks, the operator registry) across all of them,
while each repo keeps its own history, CI, and merge floor. A ticket names the repo it changes
(`target_repo: api`), and the factory dispatches a worker into that repo's working tree.

> **If you use a control plane, start your Claude session at the control plane — never inside a
> hosted repo.** Everything the factory needs (the plugin wiring, the operator registry, the repo
> manifest, your runbooks) resolves from the session's project directory. Open a session inside
> `api/` and none of that corpus is there. Whether the `/foundry:*` verbs themselves appear
> depends on where the plugin was enabled — and the common case is the dangerous one, because
> `claude plugin install` enables it **user-wide**: the verbs load, pointed at the wrong root,
> so the factory *looks* available while the corpus, registry and manifest are all absent.

Set it up: **[the control-plane guide](https://github.com/lukasrepublic/agentic-handbook/blob/main/docs/control-plane.md)**
— the on-disk layout, step-by-step multi-repo setup, and shipping an atom across two repos. The
workspace itself starts from the **[agentic-handbook](https://github.com/lukasrepublic/agentic-handbook)**
template.

## Quickstart

Starting from nothing? `npx create-agentic-workspace` is the pre-session bootstrap wizard — it
previews and writes the permission floor **before** a session exists, then hands off:

```bash
npx create-agentic-workspace --dir my-workspace
```

Already have a repo?

```bash
claude plugin marketplace add lukasrepublic/agentic-foundry
claude plugin install foundry@agentic-foundry
# in your repo's Claude Code session:
/foundry:init       # wire your repo (operator registry, hooks, project config)
```

Already installed, and want to be current? One command, run from inside the workspace:

```bash
npx update-agentic-workspace
```

It refreshes the marketplace (migrating a pre-v1.7.0 tag-pinned registration if it finds one),
updates the plugin in **every scope that enables it**, and re-runs the workspace and
permission-floor reconcile so your floor picks up rules a release added. It reads the marketplace
catalogue back to confirm it actually moved; the per-scope updates are not individually verified, so
if a session still loads the old version, check each scope's registration. `--cleanup` additionally removes stale registrations and retired
workspace files; without that flag it previews and removes nothing. Plugin-cache versions are never
removed by the updater, because a running session may still use one.

Then follow **[docs/QUICKSTART.md](docs/QUICKSTART.md)** — zero to your first merged ticket.
Want the full guided build? The **[Acme Links tutorial](https://github.com/lukasrepublic/agentic-handbook/blob/main/docs/example-acme-links/README.md)**
takes an empty repo to a governed, live-proven merge in seven checkpointed steps.

## Start here

Not sure which verb starts the thing you want to do? Find your task below. Every shipped verb,
grouped by stage, is on **[docs/VERBS-QUICK-REF.md](docs/VERBS-QUICK-REF.md)**.

| I want to... | Run this |
|---|---|
| Wire my repo for the factory (once) | `/foundry:init` |
| Have a ticket built in an isolated worktree | `/foundry:dispatch` |
| Wait for a PR's checks and merge it (on my say-so) | `/foundry:merge-when-green` |
| Check whether my repo is wired up correctly | `/foundry:doctor` |
| Turn a fuzzy ask into a ticket (or a spec, if I opt in) | `/foundry:intake` |
| Freeze a spec + contract for `security: true` work | `/foundry:authorize` |
| Cut a release | `/foundry:cut-release` |
| Recover from a red gate or a wedged install | [docs/how-to/](docs/how-to/) |

## The core loop is three verbs

`init → dispatch → merge-when-green`

That's the whole discipline, plus your own CI. The other ~29 skills are an **optional catalog** —
the opt-in spec lane (`intake`, `authorize`), infra-delivery (`id-*`) craft for OpenTofu/K8s/ArgoCD
shops, multi-repo (`repos`), release cutting and upgrade tooling. Use three verbs, ignore the rest,
add lanes when you need them. Want zero ceremony for a small change? Plain Claude Code on a branch
is the documented escape hatch — `/foundry:mode interactive` says so out loud.

## The merge floor, honestly

Foundry does not ship a bespoke merge gate. The floor is your platform's own enforcement,
honestly labeled:

| Tier | What enforces it | Who gets it |
|---|---|---|
| **A — enforced** | Branch protection / rulesets: required status checks on `main` | Public repos on any plan; private repos on paid plans |
| **B — advisory, labeled** | The same CI checks, always-reporting + the plugin's client-side git-discipline hook (refuses `--admin` bypass; admits plain merge only on live all-green checks; fail-closed on any error) | Private repos on plans without rulesets |

No tier is silently overclaimed: the `security-path-base` and `shell-parse-bash32` gate jobs label their
tier in every summary. Why the
tiers are honest rather than uniform — and what a client-side hook can and cannot promise —
is the heart of the [trust model](docs/DESIGN.md). Full mechanics: **[docs/merge-floor.md](docs/merge-floor.md)**.

## How it compares

| | Spec Kit / OpenSpec / BMAD | Review bots (CodeRabbit…) | Agent platforms (Devin, Cursor…) | **Foundry** |
|---|---|---|---|---|
| Unit of work | spec documents | — | plans, ephemeral | ✅ a ticket with a runnable `Done means`; a spec only if you opt in |
| Stops early? | — | — | often | ✅ the Stop hook runs `Done means`; the session keeps going while it fails |
| Merge enforcement | prompt packs | advisory comments | — | ✅ tiered floor, honestly labeled |
| Local-green before push | — | — | — | ✅ push and non-draft PR refused until `foundry-test.sh` is green for HEAD |
| Human authority | varies | — | sandbox-level | ✅ operator sign-off is terminal, by design |

The opt-in spec lane (`security: true` work) keeps the hash-bound contract: `/foundry:authorize` has
no skip, and the freeze is operator-signed. It is a lane the operator chooses, not a gate every
change passes through — the trade and how it was measured: [docs/merge-floor.md](docs/merge-floor.md).

**When NOT to use Foundry:** exploratory prototyping (use plain Claude Code — our
interactive mode *is* that), teams not on Claude Code, GitLab (not yet supported), or if
you want an autonomous tool that merges without you — we built the opposite on purpose.
Honest full comparison: **[docs/comparison.md](docs/comparison.md)**.

## Built with itself (the numbers)

More than 1500 pytest tests · doctor green in under a second · every third-party GitHub Action
SHA-pinned · the changelog documents every security-review disposition per release. The 2.0 rebase
came from measuring this very workflow on two adopter workspaces (paper outweighed shipped
code by lines, several times over) and deleting the paper.
These claims are **CI-locked** — a doc-drift test fails the build when they stop being true.

## Docs

**[The docs home](docs/README.md)** — tutorials · how-to guides · reference · explanation.
Direct links: [Quickstart](docs/QUICKSTART.md) · [How-to guides](docs/how-to/) ·
[Architecture](docs/architecture.md) · [Design & trust model](docs/DESIGN.md) ·
[Merge floor](docs/merge-floor.md) · [Comparison](docs/comparison.md) ·
[Glossary](docs/glossary.md) · [Troubleshooting](docs/troubleshooting.md) ·
[Changelog](CHANGELOG.md)

**Running it over several repositories** — the control-plane model, its on-disk layout, and the
step-by-step multi-repo setup live with the workspace template:
**[agentic-handbook → docs/control-plane.md](https://github.com/lukasrepublic/agentic-handbook/blob/main/docs/control-plane.md)**.

## Roadmap (near-term, honest)

- Compliance-evidence reporting (provenance pins → EU-AI-Act / SOC2 artifacts).
- GitLab: a go/no-go decision, stated openly rather than promised.
- More stack profiles (community-driven — see good first issues).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The floor: tests + doctor stay green; no claim ever
exceeds shipped enforcement; features to Foundry go through a ticket. Good first issues:
stack profiles and reference-agent generification.

## License

[MIT](LICENSE). The core plugin is and will remain open source.
