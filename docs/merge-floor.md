# The merge floor — how a change actually reaches `main`

Foundry does **not** ship a bespoke merge gate. The floor is your platform's own enforcement
plus two plugin-shipped layers, and every layer is labeled with what it actually enforces.
(Why no bespoke gate? A client-side copy of a server-side control is strictly weaker than
the original, minus the platform's tamper resistance — see [DESIGN.md](DESIGN.md).)

## Which tier are you on?

```
                 ┌──────────────────────────────────┐
                 │ Does your plan enforce rulesets? │
                 │  (public repo on any plan, or    │
                 │   private repo on a paid plan)   │
                 └──────┬──────────────────┬────────┘
                        │ yes              │ no (e.g. private + Free)
                        ▼                  ▼
                ┌───────────────┐   ┌─────────────────────────────┐
                │    TIER A     │   │           TIER B            │
                │  server-side  │   │   advisory at the server,   │
                │   REQUIRED    │   │   LABELED as advisory —     │
                │ status checks │   │   the git-discipline hook   │
                │  on `main`    │   │   carries in-session blocking│
                └───────────────┘   └─────────────────────────────┘
                        │                  │
                        ▼                  ▼
                "nothing merges      "the server does not enforce;
                 without green        the hook blocks inside Claude
                 checks" — GitHub's   Code sessions; a human with
                 guarantee, not ours  push rights can still merge
                                      from their own terminal.
                                      We say so."
```

## The tier model

Determined by your platform plan (the shipped gate workflow prints a tier label in every
summary — a template string you set to match your plan when copying it; wired up in
QUICKSTART step 1). `scripts/foundry_tier_preflight.py` (below) is the one-command way to
apply the ruleset template and find out which tier your plan actually supports:

| Tier | Mechanism | Enforcement class |
|---|---|---|
| **A** | Branch protection / rulesets: your CI checks are **required status checks** on `main` | Server-side. An agent (or a human) cannot merge around it; there is nothing local to edit. |
| **B** | The same checks, always-reporting, **not** marked required (plans without rulesets: private repos on GitHub Free) | Advisory at the server — and **labeled advisory everywhere it appears**. The local git-discipline hook (below) carries the blocking behavior for work done through Claude Code sessions. |

`scripts/foundry_tier_preflight.py` applies the shipped create-shape ruleset template
(`rulesets/tier-a-merge-floor.json`) and then answers, from evidence and evidence alone,
whether Tier A is actually in effect: **a created ruleset is not evidence of enforcement** —
GitHub lets a private repo on a plan without ruleset enforcement create and store one, return
success, and enforce nothing. The only evidence this CLI accepts for `TIER-A` is a post-apply
read of the branch-rules probe (`GET rules/branches/{default_branch}`) — the endpoint that
reports what is actually enforced, never what was merely created. Run it read-only (no
`--apply`, no write token needed) to check the current state, or with `--apply` to install the
template first:

```
python3 scripts/foundry_tier_preflight.py --repo <owner>/<repo> \
  --context spec-link --context security-path --apply
```

On a private repository on a plan without ruleset enforcement, the honest and correct outcome
is `TIER-B (created-not-enforced)`: the ruleset is stored but the server does not enforce it,
so the checks above continue to run advisory-only.

## Layer by layer

1. **Your CI** — whatever your repo already runs (tests, lint, build). Foundry adds
   nothing here and replaces nothing.
2. **The gate workflows** (templates in this repo's own `.github/workflows/` — copy or
   adapt): `spec-link` (a code-change PR must carry a lane signal — see *The two lanes*
   below) and `security-path` (a diff touching auth/secrets/dependency surfaces requires a
   posted security-review verdict). Both are **always-reporting**: they
   post `success (not applicable)` rather than staying silent, so they can be marked
   required on Tier A without deadlocking.

### The two lanes (and the escape hatch you should know about)

`spec-link` passes a PR on any **one** of three conditions, and it is worth knowing all three
before you rely on the gate:

| Condition | Lane | What it means |
|---|---|---|
| `Spec: <workspace spec path>` in the PR body | **factory** | The change is governed by a frozen contract. This is the lane the product is built around. |
| `Lane: light` in the PR body, **or** a `lane:light` label | **light** | The author is asserting the change is charter-authorized rather than spec-authorized. |
| Every changed file is `.md`/`.txt`/`.png`/`.svg`/`.pdf` | n/a | Docs-only diff; the spec requirement does not apply. |

**The light lane is self-asserted, and nothing verifies a charter exists.** One line in a PR
body — or a label anyone with triage rights can add — and the spec-link requirement is
satisfied. The gate's summary says *"charter-authorized"*, but that phrase describes the
author's claim, not a check the workflow performed.

That is a deliberate trade, not an oversight: this gate's job is to make an ungoverned change
**visible** — in the PR body, in the checks tab, and in the audit trail — rather than
impossible. A mechanism-level ban would have to enumerate every way around it, and the
operator's own review is the terminal control regardless. But you should size the guarantee
accurately: `spec-link` green means *"a lane was declared"*, not *"a frozen contract
authorized this diff"*. If you want the stronger property, require the `Spec:` form and drop
the light lane from your copy of the workflow — it is four lines of shell.
3. **The git-discipline hook** (`hooks/foundry-git-discipline.sh`, PreToolUse) — governs
   what an agent can do *from inside a Claude Code session*, fail-closed:
   - `gh pr merge --admin` (a server-side-check bypass) → **refused outright**, no
     network call.
   - plain `gh pr merge` → admitted **only** after a live `gh pr checks` query returns
     all-green; a failing row, a pending row, a nonexistent PR, an API error, or an
     unrecognized verdict all **block**.
   - **The merge must name the PR explicitly.** `gh` resolves a PR from ambient state — the
     working directory's remote, `GH_CONFIG_DIR`/`GH_HOST`, the current branch — so the guard
     pins its verification query to the coordinates in your command (`--repo`, a `cd` target,
     inline `VAR=value` assignments) rather than inheriting its own. When those coordinates
     cannot be resolved, it **refuses** instead of falling back to an ambient lookup:

     | Command | Result |
     |---|---|
     | `gh pr merge 42 --repo owner/name` | verified against `owner/name`#42 |
     | `gh pr merge https://github.com/owner/name/pull/42` | verified — the URL is self-contained |
     | `cd svc && gh pr merge 42` | verified in `svc/` |
     | `gh pr merge --squash` *(no selector)* | **refused** — would grade whatever PR the current branch points at |
     | `cd "$DIR" && gh pr merge 42` | **refused** — the target directory is not a literal path |

     The refusal names the argument that resolves it and is worded distinctly from a
     check-failure refusal. **This costs explicitness**: a bare `gh pr merge --squash`, which
     older versions admitted, now requires a PR number or URL. That is deliberate — an unpinned
     query is not a weaker check, it is a check of a *different pull request*, and in a
     multi-repo workspace a same-numbered PR elsewhere could admit a merge whose own checks
     were red.
   - force-push to a protected branch → refused.
   - The hook has **no in-session off-switch**. To act around it, a human runs the
     command themselves in their own terminal — which is exactly the boundary it exists
     to draw.

## What this floor does and does not claim

- On **Tier A**, "nothing merges without green required checks" is a *platform*
  guarantee — the strongest claim we make, and it's GitHub making it, not us.
- On **Tier B**, the server does not enforce; the hook enforces *within sessions*, and a
  human with push rights can still merge from their own terminal. We say so. A tool that
  claims fail-closed enforcement from purely client-side machinery is overclaiming — the
  trust model ([DESIGN.md](DESIGN.md)) is built on not doing that.
- **`spec-link` green does not mean "front-authorized".** It means a lane was declared, and
  one of the two lanes is self-asserted (see *The two lanes* above). Front-authorization is
  enforced where the freeze happens — `/foundry:authorize` binding `spec_sha256` +
  `contract_sha256` — not by this check. Read the two together or you will over-trust the
  badge.
- The floor governs **admission**. Whether the release actually works is the
  certification tail's job (`/foundry:certify-local` — deploy once, run the real
  journeys), and whether it ships is the operator's sign-off. Three different questions,
  three different mechanisms, none pretending to be the others.
