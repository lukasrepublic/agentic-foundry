# FAQ

**Do I have to use all sixty-odd verbs?**
No. The core loop is six: `intake → spec-review → authorize → dispatch → certify-local →
release accept`. Everything else is an optional catalog you can ignore forever.

**Can I skip authorization for a small change?**
Not through the factory — front-authorization has no skip, by design. But small changes
don't have to go through the factory: `/foundry:mode-interactive` is plain Claude Code with
zero ceremony, documented as a first-class lane. Small changes deserve small process.

**Does Foundry write worse/slower code than plain Claude Code?**
Foundry doesn't write code at all — the same Claude Code agents do. It governs what they
build (an authorized spec), where (an isolated worktree, scoped paths), and what "done"
means (observable checkpoints, certified against a running instance).

**What happens if the agent tries to merge anyway?**
On Tier A, GitHub refuses — required checks are server-side. On Tier B, the plugin's
git-discipline hook refuses in-session (`--admin` always; plain merge unless checks are
live-green). What Tier B can and cannot promise is stated plainly in
[merge-floor.md](merge-floor.md) — we don't overclaim client-side enforcement.

**Do my specs/contracts survive if I stop using Foundry?**
Yes — they're plain markdown and YAML in your repo, and git history is the ledger. No
lock-in artifact exists.

**Can I use it on an existing codebase?**
Yes: `/foundry:extract-spec` surveys the code and promotes a capability into a candidate
spec, which rides the same loop. See the
[brownfield how-to](how-to/adopt-on-an-existing-codebase.md).

**Does it work without GitHub?**
The artifacts do; the merge floor doesn't yet — it's built on GitHub branch
protection/rulesets and `gh`. GitLab is a stated go/no-go decision, not a promise.

**Is my code sent anywhere beyond Claude?**
Foundry adds no network calls of its own beyond `gh` (your GitHub) — the plugin's scripts
are local Python/bash. Your Claude Code data handling is unchanged.

**Why does certification refuse instead of passing when journeys are missing?**
Because a vacuous pass is the exact failure the tool exists to prevent: "status ≠
functional". A refusal names what's missing; a green lie compounds.

**Who is the "operator"?**
The human who authorizes specs and signs off releases — registered in
`.claude/foundry-operators.json`. On a solo project that's you; on a team it's whoever
your review process designates (see the
[team review how-to](how-to/team-review-with-codeowners.md)).

**How do I keep agents from touching files outside the task?**
The contract's `scope.allowed_paths` is frozen at authorization; dispatch runs in an
isolated worktree; the `spec-link` gate ties the PR to its authorizing spec. Widening
scope requires re-authorization.
