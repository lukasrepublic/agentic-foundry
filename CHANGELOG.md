# Changelog

All notable changes to Agentic Foundry are documented here (SemVer).

> **Release discipline.** `claude plugin update` is **version-keyed** — adopters only
> receive changes when the plugin `version` in `.claude-plugin/plugin.json` is bumped. Every
> meaningful change therefore bumps the version and lands under a dated release section below.
> Every release is itself specced, authorized, floor-gated, and certified through the tool
> (Foundry is built with Foundry), and each section records its security-review disposition.

## Unreleased

- **`--lock` creates a stack-profile lock — the missing half of the lock lifecycle.**
  `write_lock()` had exactly one caller, `relock_lock()`, which refuses when no
  `.foundry/stack-profile.lock` exists ("nothing to relock"); there was no shipped way to
  *adopt* one of the four stack profiles Foundry ships. `/foundry:verify`, `/foundry:certify-local`,
  and the `id-*` lane's `infra_binding` all gate on an active lock, so a fresh adopter could not
  reach any of them. `scripts/foundry-stack-profile.py --lock <id>[,<id>…]` resolves each named id
  against the trusted `packs/` tree and atomically writes a fresh lock, enforcing the SAME
  trusted-resolve guardrails `relock` already does (schema-valid, present in `packs/`,
  `requires_core` satisfied, no core-plugin `skills/` bundle leak) — validate-before-write, so a
  failure on any named id leaves no lock file and no `.tmp` residue. It refuses (no write) when a
  lock already exists (naming `/foundry:relock` as the refresh path) or is present but corrupt
  (naming the file as corrupt with a stated remedy, distinctly from the exists case), or when an
  id is unknown (listing the ids that are available). The per-entry field set is built by a SINGLE
  helper now shared between `--lock` and `relock_lock()` — no third hand-copy of the digest logic
  `resolve_lock()` verifies. `/foundry:init` offers profile selection and invokes this scripted
  path (never hand-writing a lock in prose); selecting no profile still completes normally, and a
  lockless workspace remains fully supported and `DOCTOR-GREEN`. (`feat-foundry-stack-profile-lock-create`)

## v1.0.1 — 2026-08-01

**Security fix for the git-discipline guard. Upgrade if you rely on it.** Two defects let the
guard be defeated; both were reproduced by execution before being fixed, and both are covered by
new regression tests that fail against v1.0.0.

- **The `gh pr merge` gate verified the wrong pull request.** Its `gh pr checks` query was built
  from a stripped argv running in the hook's own process context, dropping the repo selector, the
  working directory and the GitHub identity from the intercepted command. Because `gh` resolves a
  PR from ambient state, the guard graded whatever PR the ambient environment considered current.
  The visible symptom was a confusing refusal naming an unrelated branch; the **severe** direction
  was silent — a merge whose checks were genuinely failing could be **admitted** because a
  same-numbered PR in another repo was green. On a Tier-B repo this clause is the only in-session
  control preventing that merge. The query is now pinned to the command's own coordinates, or the
  merge is refused; there is no ambient fallback.

  *This costs explicitness:* a bare `gh pr merge --squash` with no PR selector now refuses. An
  unpinned query is not a weaker check — it is a check of a different pull request.
  (`feat-foundry-merge-verify-context`, auth_seq=1)

- **A path-qualified `git`/`gh`/`rm` bypassed every clause at once.** All three clause loops
  matched their verb by exact token equality, so an absolute or relative path escaped the whole
  guard — force-push to a protected branch, `branch -D`, `filter-repo`, `filter-branch`, repo
  deletion, and the merge gate. Verbs now resolve through one shared matcher that compares the
  final path segment (never a substring: `gitlab-runner`, `github-cli`, `git-lfs`, `gitk` are
  unaffected). (`feat-foundry-verb-path-resolution`, auth_seq=1)

Also fixed, found while reviewing the above: a backslash line continuation could splice a verb
past the scan; the `shlex`-failure fallback preserved quotes and missed a quoted verb; `--help`
invocations were refused; redirection operators could be mistaken for a PR selector; and the
check query's output — untrusted content from whatever host `GH_HOST` resolved to — is now
redacted and length-capped before being echoed into a refusal.

**Documented, not fixed:** a green verdict covers **CI check runs only**, not required reviews,
CODEOWNERS approval, or merge-queue eligibility. Two residuals are stated in the spec and
acknowledged by the operator: the check-then-merge race, and an identity `export`ed in an earlier
shell turn — a PreToolUse guard admits or refuses a command, it cannot rewrite one, so neither is
closable here.

**Security review:** performed on both atoms (separate-context reviewer, `hooks/**` security-path).
The reviews returned one Block each; both were reproduced, fixed, and pinned by test before this
release. See `docs/merge-floor.md` → *The git-discipline hook*.

## v1.0.0 — 2026-07-31

The initial public release. Everything below is what ships, stated in full rather than as a
delta — v1.0.0 is the baseline later entries diff against.

### The core loop

Six verbs govern a change from fuzzy ask to signed-off release:

- **`/foundry:intake`** — interactive discovery → an atomic spec (stable AC-IDs, a delimited
  normative region, a hard 14-AC/8,000-word ceiling with no override) + a sibling acceptance
  contract declaring observable checkpoints.
- **`/foundry:spec-review`** — deterministic pre-lints (size ceiling, reference closure — zero
  token cost, run first), then three fresh-context reviewer lenses (prior-art;
  steel-man + adversarial; per-AC rubric), one remediation round, the review recorded
  content-bound to the spec's hash. A conditional security lens fires on security-flagged specs.
- **`/foundry:authorize`** — the front gate. The operator reads every checkpoint and confirms;
  the spec + contract hashes are frozen and signed. An unauthorized spec cannot reach `main`
  through the factory; there is no skip.
- **`/foundry:dispatch`** — an implementer persona builds the atom in an isolated git worktree
  against the frozen contract and opens a PR. Scope is contract-bound (`allowed_paths`);
  widening requires re-authorization.
- **`/foundry:certify-local`** — deploys the release once locally and runs every atom's tagged
  Playwright journeys against that single instance; per-atom pass/fail from the runner's own
  output. Refuses — never passes vacuously — when journeys or the boot recipe are missing.
- **`/foundry:release accept`** — records the operator's own sign-off as a practice note,
  deliberately not a machine gate.

### The merge floor

No bespoke merge gate. The floor is the platform's own enforcement, honestly tiered
(`docs/merge-floor.md`): required status checks where the plan enforces rulesets (Tier A,
server-side), always-reporting checks plus a fail-closed client-side git-discipline hook where
it doesn't (Tier B, labeled advisory everywhere it appears). The hook refuses `gh pr merge
--admin` outright and admits a plain merge only on a live all-green `gh pr checks` read.
`scripts/foundry_tier_preflight.py` applies the shipped ruleset template and reports the tier
from post-apply evidence — a created ruleset is never taken as proof of enforcement.

### The release gate

`scripts/foundry-cut-release.py` refuses a cut unless the candidate tree's manifests agree,
the CHANGELOG section exists, the acceptance gate is green, **and the tree's own full test
suite passes** — metadata-clean is not enough. It emits the publish plan (tag, marketplace
re-pin to the tag commit, pushes, issue closes) as data the operator executes; the tool
itself never tags, pushes, or closes anything.

### The catalog

Beyond the six core verbs: brownfield extraction (`extract-spec`), release-wave fan-out,
the infra-delivery (`id-*`) and software-delivery (`sd-*`) craft sequences, stack profiles
(node-web, aws-eks-karpenter, python-uv-lib, python-uv-service) with per-profile blueprints,
a citation-graph MCP server, session-learnings capture, fleet/status tooling, upstream
submission (`upstream-submit`: label-ensure on the target repo, a degraded no-permission
path, and hard identity isolation — a dedicated `GH_CONFIG_DIR`, no ambient token reuse,
and refusal (exit 4) rather than ever submitting as the wrong identity), and the
zero-ceremony interactive mode as a documented first-class lane. All optional; the six-verb
loop never requires them.

### Security posture

- The proprietary-term leak gate runs on every PR with its term list held **outside the
  repository** (a repository secret in CI; an operator-local file otherwise). Findings report
  path, line, and a term index — never the matched text — so a public Actions log cannot
  republish what the gate protects. Fork PRs degrade visibly to structural markers (never a
  silent pass); an empty term list on any other event refuses.
- **Paths are a scanned surface too**: a term in a file or directory *name* (including
  history-only paths, one `git log --raw` away) convicts, and every finding's path field is
  span-exact redacted (`docs/term[3]/notes.md`) — locating power intact, the term withheld,
  in every finding class including structural markers and read errors.
- Every third-party GitHub Action is pinned to a 40-char commit SHA.
- A mechanical secret scan (PEM/key/token/JWT patterns) runs on every PR diff.
- Security-review routing: any diff touching auth, secrets, or supply-chain paths requires a
  posted security-review disposition.
- Hooks are fail-closed: the git-discipline guard, the cloud-CLI exec guard (inert until a
  wrapper is configured), and worktree write containment.

**Security review (this release):** the leak-gate hardening shipped through a full security
review; both blocking findings (a history-scope exclusion that could mask the gate's own term
list, and a missing fail-closed verdict sentinel) plus five risks were fixed pre-merge, each
with a regression test.

### Evidence

More than 1000 pytest tests; a five-probe doctor green in under a second; a CI doc-drift suite
locks this changelog's claims, the README's install pin, and the verb catalog against the
shipped tree.
