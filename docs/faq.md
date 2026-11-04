# FAQ

**Do I have to use all thirty-odd verbs?**
No. The core loop is three: `init → dispatch → merge-when-green`, with a ticket (a GitHub issue
with a runnable `Done means`) as the unit of work. Everything else is an optional catalog you can
ignore forever.

**Can I skip authorization for a small change?**
By default there is nothing to skip: assigning the ticket is the authorization, and your standing
grant in `.claude/foundry-operators.json` is recorded once. Only `security: true` work (or a spec
you choose to write) goes through `/foundry:authorize`, and that has no skip. Small changes can
also go plain: `/foundry:mode interactive` is plain Claude Code with zero ceremony, documented as a
first-class lane. Small changes deserve small process.

**Does Foundry write worse/slower code than plain Claude Code?**
Foundry doesn't write code at all — the same Claude Code agents do. It governs what they
build (a ticket), where (an isolated worktree), and what "done" means (a command that exits zero,
run locally before the push and again in CI).

**What happens if the agent tries to merge anyway?**
On Tier A, GitHub refuses — required checks are server-side. On Tier B, the plugin's
git-discipline hook refuses in-session (`--admin` always; plain merge unless checks are
live-green). What Tier B can and cannot promise is stated plainly in
[merge-floor.md](merge-floor.md) — we don't overclaim client-side enforcement.

**Do my tickets and specs survive if I stop using Foundry?**
Yes — tickets are issues in your forge, specs and contracts are plain markdown and YAML in your repo,
and git history is the ledger. No lock-in artifact exists.

**Can I use it on an existing codebase?**
Yes — nothing to extract or migrate. Write the next change as a ticket and the same loop applies.

**Does it work without GitHub?**
The artifacts do; the merge floor doesn't yet — it's built on GitHub branch
protection/rulesets and `gh`. GitLab is a stated go/no-go decision, not a promise.

**Is my code sent anywhere beyond Claude?**
Foundry adds no network calls of its own beyond `gh` (your GitHub) — the plugin's scripts
are local Python/bash. Your Claude Code data handling is unchanged.

**Why does the session keep going when I think it is done?**
Because the Stop hook runs the ticket's `Done means` command, and while it fails the work is not
done: "status ≠ functional". After three refusals the session may end with the failure named, so
a wrong `Done means` cannot trap you.

**Who is the "operator"?**
The human who holds the standing grant, authorizes specs on the opt-in lane, and signs off releases — registered in
`.claude/foundry-operators.json`. On a solo project that's you; on a team it's whoever
your review process designates (see the
[team review how-to](how-to/team-review-with-codeowners.md)).

**How do I keep agents from touching paper outside the task?**
The paper guard refuses a write under `specs/`, `docs/`, `.foundry/`, `status-reports/` or
`charters/` unless the active ticket's `Paper allowed` list names the path; dispatch runs in an
isolated worktree. Code, infra, tests and config are never gated.
