# Agentic Foundry

**Governed, spec-driven delivery for agent-built software — as a Claude Code plugin.**

Spec tools stop at "generate documents." Review bots are advisory. Agent platforms sandbox
execution but not *delivery*. Foundry runs the seam none of them run: **spec → operator
authorization → governed build → an honestly-tiered merge floor → certification against the
real running app → human sign-off.**

> Philosophy in one line: **gates make problems visible, not impossible** — and every gate has
> to earn its keep. A gate ships only if it names the observed failure it prevents; the
> operator's own judgment is what the automation serves, never what it replaces.

**Status: v1.4.2.** Built solo, dogfooded daily — Foundry is built *with* Foundry: every release
you can install was itself specced, authorized, floor-gated, and certified through it.

## The loop, in one picture

```
 YOU (operator)          THE AGENT                 YOUR PLATFORM (GitHub/CI)
 ──────────────          ─────────                 ─────────────────────────
      │
      │  "rate-limit the public API"
      ▼
 ┌──────────┐    ┌──────────────────────┐
 │  intake  │───▶│ atomic spec + accept- │        specs/features/<product>/api/rate-limit/
 └──────────┘    │ ance contract drafted │                    ├─ feat-api-rate-limit.md
      │          └──────────────────────┘                    └─ acceptance-contract.yaml
      ▼
 ┌──────────────┐ ┌─────────────────────┐
 │ spec-review  │▶│ 3 fresh-context     │
 └──────────────┘ │ reviewer lenses     │
      │           └─────────────────────┘
      ▼
 ┌──────────────┐
 │  AUTHORIZE   │  ← you read every checkpoint, you confirm.
 │  (no skip)   │    spec + contract hashes frozen, signed with your id
 └──────┬───────┘
        │ frozen contract
        ▼
                  ┌─────────────────────┐
                  │ dispatch: implement │       isolated git worktree,
                  │ against the frozen  │──────▶ PR opened
                  │ contract            │           │
                  └─────────────────────┘           ▼
                                          ┌──────────────────────┐
                                          │   THE MERGE FLOOR    │
                                          │ your branch protection│
                                          │ + CI checks, tiered   │
                                          │ honestly (A/B)        │
                                          └──────────┬───────────┘
      ┌──────────────────────────────────────────────┘ merged
      ▼
 ┌───────────────┐ ┌────────────────────────┐
 │ certify-local │▶│ deploy ONCE, run every │      per-atom pass/fail from
 └───────────────┘ │ atom's real Playwright │      the runner's own output
      │            │ journeys against it    │
      ▼            └────────────────────────┘
 ┌───────────────┐
 │  SIGN-OFF     │  ← you test it yourself. Recorded as a practice
 │  (yours)      │    note — deliberately not a machine gate.
 └───────────────┘
```

Two hard gates — **authorize** (yours) and **the floor** (your platform's) — then an honest
tail: **certify** produces machine evidence, and the final **sign-off** is yours. Everything
between the gates is the agent's job.

## See it in action

The core loop, as you'd actually type it:

```text
> /foundry:intake "rate-limit the public API"
  … interactive discovery → writes specs/features/<product>/api/rate-limit/feat-api-rate-limit.md
    + acceptance-contract.yaml (stable AC-IDs, observable checkpoints)

> /foundry:spec-review specs/features/<product>/api/rate-limit/feat-api-rate-limit.md
  … deterministic pre-lints → 3 fresh-context reviewer questions (prior-art,
    steel-man+adversarial, per-AC rubric) → one remediation round → review recorded

> /foundry:authorize specs/features/<product>/api/rate-limit/feat-api-rate-limit.md
  … shows you the contract's checkpoints → you confirm → spec+contract hashes frozen

> /foundry:dispatch feat-api-rate-limit
  … implementer persona builds it in an isolated worktree → opens a PR
  … your CI + branch protection decide the merge (the plugin's git-discipline hook
    refuses --admin bypasses and merges ahead of green checks — fail-closed)

> /foundry:certify-local api-v2
  … deploys the release ONCE locally, runs every atom's tagged Playwright journeys
    against that one instance → per-atom pass/fail from the runner's own output

> /foundry:release accept api-v2 --operator you --verdict accepted
  … records YOUR sign-off — a practice note, never a machine gate
```

And the artifact the whole loop pivots on — an acceptance contract you can read:

```yaml
spec_ref: specs/features/<product>/api/rate-limit/feat-api-rate-limit.md
scope:
  allowed_paths: ["src/api/**"]
checkpoints:
  - ac_id: AC-RATE-1
    surface: "api:/v1/*"
    locator: "GET /v1/anything x101 within 60s"
    expect: { op: equals, value: "HTTP 429 on request 101" }
```

## Where you run it: one repo, or a control plane over many

Foundry works **inside a single repository** — install it, `/foundry:init`, and the loop below is
live in that repo. That is the fastest way to try it, and the Quickstart takes that path.

But it is **designed to be operated from a control plane**: a small *workspace* repo that holds
your specifications and hosts your code repositories as gitignored siblings, with the factory
dispatching work into each of them.

```
   SINGLE REPO                          CONTROL PLANE  (what it is built for)
   ───────────                          ─────────────

   your-app/                            acme-handbook/          ◀── specs live here; you run
   ├── .claude/  ← the wiring           ├── .claude/                Claude from HERE
   ├── specs/    ← the WHAT             ├── specs/features/…    ◀── the WHAT for every repo
   └── src/      ← the code             │
                                        ├── api/    ◀── its own git repo, gitignored
                                        ├── web/    ◀── its own git repo, gitignored
                                        └── infra/  ◀── its own git repo, gitignored
```

Why it matters: real projects are an app, some services, and the infrastructure under them. The
control plane keeps **one governed corpus of specs** across all of them, while each repo keeps its
own history, CI, and merge floor. A contract names its venue with `target_repo: api`, and the
factory dispatches a worker into that repo's working tree.

> **If you use a control plane, start your Claude session at the control plane — never inside a
> hosted repo.** Everything the factory needs (the plugin wiring, the operator registry, the repo
> manifest, your specs) resolves from the session's project directory. Open a session inside
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
claude plugin marketplace add lukasrepublic/agentic-foundry#v1.4.2
claude plugin install foundry@agentic-foundry
# in your repo's Claude Code session:
/foundry:init       # wire your repo (operator registry, hooks, project config)
```

Then follow **[docs/QUICKSTART.md](docs/QUICKSTART.md)** — zero to your first governed merge.
Existing codebase? Start at `/foundry:extract-spec` (brownfield → spec, then the same loop).
Want the full guided build? The **[Acme Links tutorial](https://github.com/lukasrepublic/agentic-handbook/blob/main/docs/example-acme-links/README.md)**
takes an empty repo to a governed, live-proven merge in seven checkpointed steps.

## Start here

Not sure which verb starts the thing you want to do? Find your task below. Every shipped verb,
grouped by loop stage, is on **[docs/VERBS-QUICK-REF.md](docs/VERBS-QUICK-REF.md)**.

| I want to... | Run this |
|---|---|
| Turn a fuzzy ask into a reviewable spec | `/foundry:intake` |
| Get a fresh-context review of a draft spec | `/foundry:spec-review` |
| Give my go-ahead before any code gets touched | `/foundry:authorize` |
| Have the authorized spec built in an isolated worktree | `/foundry:dispatch` |
| Prove a release works against a real running instance | `/foundry:certify-local` |
| Record my own sign-off on a release | `/foundry:release` |
| Check whether my repo is wired up correctly | `/foundry:doctor` |
| Turn an existing codebase into a candidate spec | `/foundry:extract-spec` |
| Recover from a red gate or a wedged install | [docs/how-to/](docs/how-to/) |

## The core loop is six verbs

`intake → spec-review → authorize → dispatch → certify-local → release accept`

That's the whole discipline. The other ~60 skills are an **optional catalog** — release-wave
fan-out, infra-delivery (`id-*`) craft for OpenTofu/K8s/ArgoCD shops, brownfield extraction,
citation-graph MCP, fleet/status tooling. Use six verbs, ignore the rest, add lanes when you
need them. Want zero ceremony for a small change? `/foundry:mode-interactive` is the
documented escape hatch — pure native Claude Code, no pipeline.

## The merge floor, honestly

Foundry does not ship a bespoke merge gate. The floor is your platform's own enforcement,
honestly labeled:

| Tier | What enforces it | Who gets it |
|---|---|---|
| **A — enforced** | Branch protection / rulesets: required status checks on `main` | Public repos on any plan; private repos on paid plans |
| **B — advisory, labeled** | The same CI checks, always-reporting + the plugin's client-side git-discipline hook (refuses `--admin` bypass; admits plain merge only on live all-green checks; fail-closed on any error) | Private repos on plans without rulesets |

No tier is silently overclaimed: the `spec-link-base`, `security-path-base` and `shell-parse-bash32` gate jobs label their
tier in every summary. Why the
tiers are honest rather than uniform — and what a client-side hook can and cannot promise —
is the heart of the [trust model](docs/DESIGN.md). Full mechanics: **[docs/merge-floor.md](docs/merge-floor.md)**.

## How it compares

| | Spec Kit / OpenSpec / BMAD | Review bots (CodeRabbit…) | Agent platforms (Devin, Cursor…) | **Foundry** |
|---|---|---|---|---|
| Spec artifacts | ✅ generate them | — | plans, ephemeral | ✅ + frozen, hash-bound contracts |
| Authorization before build | IDE/UX approvals at best | — | — | ✅ operator-signed hash freeze; the factory lane has no skip<sup>†</sup> |
| Merge enforcement | prompt packs | advisory comments | — | ✅ tiered floor, honestly labeled |
| Certification vs the running app | — | — | — | ✅ deploy-once + real journey suite<sup>‡</sup> |
| Human authority | varies | — | sandbox-level | ✅ operator sign-off is terminal, by design |

<sup>‡</sup> **Reachability caveat, stated plainly.** The certification machinery ships and works,
but a *fresh* adopter cannot currently reach it: `/foundry:certify-local` takes its boot recipe
only from an active stack profile, and no shipped code path creates the
`.foundry/stack-profile.lock` that a profile is resolved through. The `repos.<key>.boot_command`
field the schema advertises for this is read by nothing. Two atoms are specified to close this
(`boot-recipe-precedence`, `stack-profile-lock-create`) and are awaiting authorization; until
they ship, read this row as "built and exercised in this repo", not "available on first install".

<sup>†</sup> `/foundry:authorize` itself has no skip — the freeze is unconditional and
operator-signed. Separately, the `spec-link-base` merge gate accepts a **declared light lane**
for changes that are not spec-driven, applied as the `lane:light` LABEL. It used to be a
`Lane: light` line in the PR body; that was removed because a PR body is written by its author,
so a fork could self-declare the light lane and skip the check entirely. A label can only be
applied by someone with write access — so the declaration is still discretionary, but it is no
longer self-asserted by an outside contributor.
So "nothing merges unauthorized" is a property of the factory lane, not of every possible
PR. The trade and how to remove it: [docs/merge-floor.md → *The two lanes*](docs/merge-floor.md#the-two-lanes-and-the-escape-hatch-you-should-know-about).

**When NOT to use Foundry:** exploratory prototyping (use plain Claude Code — our
interactive mode *is* that), teams not on Claude Code, GitLab (not yet supported), or if
you want an autonomous tool that merges without you — we built the opposite on purpose.
Honest full comparison: **[docs/comparison.md](docs/comparison.md)**.

## Built with itself (the numbers)

More than 1000 pytest tests · doctor green in under a second · every third-party GitHub Action
SHA-pinned · every release specced, reviewed, authorized, floor-gated, and certified through
the tool itself · the changelog documents every security-review disposition per release.
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
- Spec ⟷ code drift detection wired to frozen contracts.
- GitLab: a go/no-go decision, stated openly rather than promised.
- More stack profiles (community-driven — see good first issues).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The floor: tests + doctor stay green; no claim ever
exceeds shipped enforcement; features to Foundry go through Foundry. Good first issues:
stack profiles and reference-agent generification.

## License

[MIT](LICENSE). The core plugin is and will remain open source.
