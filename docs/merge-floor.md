# The merge floor — how a change actually reaches `main`

Foundry does **not** ship a bespoke merge gate. The floor is your platform's own enforcement
plus the plugin-shipped layers below, and every layer is labeled with what it actually enforces.
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
  --context security-path-base --apply
```

On a private repository on a plan without ruleset enforcement, the honest and correct outcome
is `TIER-B (created-not-enforced)`: the ruleset is stored but the server does not enforce it,
so the checks above continue to run advisory-only.

## Layer by layer

1. **Your CI** — whatever your repo already runs (tests, lint, build). Foundry adds
   nothing here and replaces nothing.
2. **The gate workflows** (templates in this repo's own `.github/workflows/` — copy or
   adapt): `security-path-base` (a diff touching auth/secrets/dependency surfaces requires a
   posted security-review verdict, the `security-reviewed:<head12>-<base8>` label). It runs on
   `pull_request_target`, so GitHub takes its definition from your default branch rather than the
   PR's merge ref — a fork cannot rewrite the gate that grades it. It is checkout-free by design,
   which is what makes that trigger safe. It is **always-reporting**: it posts
   `success (not applicable)` rather than staying silent, so it can be marked required on Tier A
   without deadlocking. `shell-parse-bash32` (in `btb-gates.yml`) parses every shipped `*.sh`
   under bash 3.2.

### One lane by default; the spec lane is opt-in

There is no merge-time lane signal. Until v2.0.0 a `spec-link-base` job required a code-change PR
to carry a `Spec:` trailer or a `lane:light` label; it measured *"a lane was declared"*, not *"a
frozen contract authorized this diff"*, and its required-check status was removed from branch
protection before the job was deleted. The default path is a ticket with a runnable `Done means`
([context/operating-model.md](../context/operating-model.md)); the opt-in spec lane
(`security: true` work, or an operator-chosen spec) enforces front-authorization where the freeze
happens — `/foundry:authorize` binding `spec_sha256` + `contract_sha256` — not at merge time.

### The git-discipline hook

3. **The git-discipline hook** (`hooks/foundry-git-discipline.sh`, PreToolUse) — governs
   what an agent can do *from inside a Claude Code session*, fail-closed:

   **Structured observations, not string-scan blindness** (feat-foundry-guards-guard-structured-
   observations). This hook tokenizes
   the command with `scripts/foundry_shell_scan.py` — a small, stdlib-only, heredoc-aware scanner
   — before scanning it: a guarded verb mentioned only in *prose* inside a provably inert-sink
   heredoc body (`cat > <path>`, `cat >> <path>`, `tee <path>`, `git commit -F -`,
   `gh pr create --body-file -`, `gh issue create --body-file -`, with no other consumer and no
   collision with any other clause's mention of the same path) is admitted rather than blocked —
   a commit message, doc, spec, or fixture that merely *names* `git push --force` no longer false-
   blocks. Every clause rule above is unchanged; only what text it scans changed, and the closed
   sink set convicts on any doubt (an unterminated heredoc, an unbalanced quote, a heredoc fed to
   `bash`/`sh`/`eval`/`source`/`python3 -`, multiple heredocs on one clause, a variable/expansion
   redirect target, CRLF line endings — all still block). When it blocks, it now
   `exit`s with code `2` **and** prints one JSON object on stdout (`hookSpecificOutput.permissionDecision: "deny"` +
   an `observation` naming the guard, the offending evidence, whether the block is `retryable`,
   and an executable `remediation` — e.g. `/foundry:merge-when-green <pr>` for an unverifiable or
   non-green `gh pr merge`); the
   stderr line is the reason followed by that remediation, replacing the old blanket "run the
   command yourself outside the agent" sentence.
   - `gh pr merge --admin` (a server-side-check bypass) → **refused outright**, no
     network call.
   - `gh pr merge --auto` (any merge method) → **admitted without a query** (v1.18): it hands
     the merge to the platform, which merges only once the branch's **required** checks pass.
     On a Tier B repo with no required checks the platform merges at once — there, `--auto` is
     no stronger than the repo's own protection, so apply Tier A.
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
     | `gh pr merge <pr> --repo owner/name` | verified against that PR in `owner/name` |
     | `gh pr merge https://github.com/owner/name/pull/<pr>` | verified — the URL is self-contained |
     | `cd /abs/path/svc && gh pr merge <pr>` | verified in that directory |
     | `gh pr merge --squash` *(no selector)* | **refused** — would grade whatever PR the current branch points at |
     | `cd "$DIR" && gh pr merge <pr>` | **refused** — the target directory is not a literal path |
     | `cd svc && gh pr merge <pr>` | **refused** — a relative path this guard cannot resolve against the shell's own cwd |
     | `pushd … ` / `(cd … && gh …)` | **refused** — directory changes the scan does not model |

     All five `--repo` spellings are recognised (`--repo V`, `--repo=V`, `-R V`, `-RV`, `-R=V`);
     an unrecognised repo-selector token refuses rather than being ignored. Inline environment
     assignments are carried only for an explicit GitHub-identity allowlist (`GH_TOKEN`,
     `GITHUB_TOKEN`, `GH_ENTERPRISE_TOKEN`, `GITHUB_ENTERPRISE_TOKEN`, `GH_HOST`, `GH_REPO`,
     `GH_CONFIG_DIR`); anything else refuses, because variables like `GH_PAGER`/`GH_BROWSER` are
     programs `gh` executes and `PATH` would let a planted `gh` forge a green verdict.

     **What a green verdict covers.** `gh pr checks` reports **CI check runs only**. It does
     not report required reviews, CODEOWNERS approval, or merge-queue eligibility, so an admit
     means "CI is green", never "this PR is mergeable". Those rules are the server's to enforce
     (Tier A); on Tier B nothing enforces them.

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
- **Front-authorization is not a merge-time check.** On the opt-in spec lane it is enforced where
  the freeze happens — `/foundry:authorize` binding `spec_sha256` + `contract_sha256`. No gate job
  reads it; the merge floor admits on checks, and the operator's review is what reads the spec.
- The floor governs **admission**. Whether the change actually works is the ticket's `Done means`
  command plus CI (local-green before push, CI to confirm), and whether it is delivered is the
  operator's own test pass. Three different questions, three different mechanisms, none
  pretending to be the others.
