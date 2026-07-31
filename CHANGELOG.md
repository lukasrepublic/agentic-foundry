# Changelog

All notable changes to Agentic Foundry are documented here (SemVer).

> **Release discipline.** `claude plugin update` is **version-keyed** — adopters only
> receive changes when the plugin `version` in `.claude-plugin/plugin.json` is bumped. Every
> meaningful change therefore bumps the version and lands under a dated release section below.
> Every release is itself specced, authorized, floor-gated, and certified through the tool
> (Foundry is built with Foundry), and each section records its security-review disposition.

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
