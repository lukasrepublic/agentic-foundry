# Changelog

All notable changes to Agentic Foundry are documented here (SemVer).

> **Release discipline.** `claude plugin update` is **version-keyed** — adopters only
> receive changes when the plugin `version` in `.claude-plugin/plugin.json` is bumped. Every
> meaningful change therefore bumps the version and lands under a dated release section below.
> Every release is itself specced, authorized, floor-gated, and certified through the tool
> (Foundry is built with Foundry), and each section records its security-review disposition.

## v1.10.0 — 2026-09-04

### The command deck becomes a verb you can arm, stop and re-arm

`/foundry:command-deck <programme-id>` arms a recurring watcher over one programme and manages it:
`status`, `stop`, `restart`, `tick`, `prompt`, `list`. Each tick re-measures the ready-set from
disk, dispatches what the wave barrier has unblocked, verifies independently, lands what passes, and
reports Accomplishments / Next / Blockers.

v1.5.0 shipped the tick contract as a delta to `/foundry:mode-autonomous`, which left two things
broken. The capability's own name did not resolve it — the operator hit this directly: *"I thought
the command would be command-deck but its mode-autonomous?"* — because skill selection matches the
`description` frontmatter and `/foundry:command-deck` was listed only in the body, the one place
that cannot cause an invocation. And nothing could arm, inspect, stop or re-arm a watcher at all.

- **`skills/command-deck/SKILL.md`** — the verb. It also writes down the shape that works, which
  was measured rather than assumed: the deck is the operator's own session woken by a recurring
  scheduled job, so it holds their authority by construction. It is **never a subagent** (packaging
  operator authority into a brief and delegating it is the exact shape a permission classifier
  refuses, and refusing it is correct), **never a background shell loop**, and it **never
  self-grants** a denied permission — it surfaces the denial as its single blocker and stops.
- **`skills/command-deck/tick-prompt.template.md`** — the operating discipline a tick fires under,
  rendered per programme.
- **`scripts/foundry_command_deck_watch.py`** — renders that prompt from the live ready-set and
  keeps the watcher record at `.foundry/watchers/<programme>.json`. The record exists because a
  scheduled job is **session-only** and auto-expires after 7 days, so "this programme is meant to be
  watched" cannot live in the job. It is a note, never an authority: nothing derives from it, and
  `status` prints it and the live measurement side by side without reconciling them, because only
  the session holding the job can say whether it still fires.
- **`scripts/foundry_command_deck.py` is unchanged.** It remains the derivation — `ready_set` with
  the authorization re-derivation and the wave barrier, `is_idle`, `wake_seconds`, `may_land`. The
  new module is the surface around it, not a second copy of it.
- **`skills/mode-autonomous/SKILL.md`** gains one disambiguation clause in its `description`. Both
  skills now name the other and the condition under which the other is the right verb — in the
  field selection actually matches, since a body cannot fix a mis-selection.

`restart` is how a tick prompt is **edited**: the scheduler is delete-then-create, and rewriting the
prompt each time the deck learns a rule is why it gets good.

### The cut-release playbook now hands over the tool that actually delivers the release

`skills/cut-release/SKILL.md`'s Downstream step wrote out, by hand, the marketplace re-point, the
per-scope plugin update, and the cache read-back — the exact work `npx update-agentic-workspace` has
performed since v1.9.0. The playbook never named it, so every cut re-derived a manual procedure that
already had a shipped, tested implementation.

Step 5 now leads with `npx update-agentic-workspace` and says what each of its four phases does: the
pre-v1.7.0 pinned-`ref` migration per affected scope, the plugin update in every scope that enables
it **verified by reading back the refreshed cache manifest** rather than trusting a success line
(the v1.4.1 scar, made mechanical), the opt-in `--cleanup` prune that previews and removes nothing
without the flag, and the managed-file plus permission-floor reconcile — which is what lets an
adopter's floor pick up a rule a release added instead of staying at whatever the last scaffold
wrote. The hand-run commands stay, re-framed as the explanation and the no-npm fallback, and the
one side effect worth saying out loud is stated: healing a pinned scope ends in `plugin install`, so
a scope where the plugin was deliberately disabled has it re-enabled.

**Security review:** required and performed — the diff adds a script that writes to the corpus and
prose in two skills. Separate context, read-only, against the PR head: **no Block, no Risk, three
Nits.** It confirmed the slug guard holds by construction (a literal-ASCII `re.fullmatch` returning
the value it validated, so traversal never reaches `os.path.join`, with `resolve_programme`'s
realpath containment as a second non-overlapping layer) and that every value interpolated into the
rendered prompt is a validated slug, a closed enum, or `as_data`-sanitised. Two Nits were fixed
before merge: a watcher record holding valid-but-non-object JSON raised an uncaught `AttributeError`
where the module promises a refusal, and the workspace path was interpolated unquoted into the
tick's own `cd`. One is tracked and deliberately not fixed: the template directs the tick to read
the manifest's YAML comments, which `yaml.safe_load` discards and the sanitiser therefore never
sees — not a defect while the operator authors the manifests, and the reason that instruction stays
pointed at the manifest rather than widened to arbitrary referenced files.

## v1.9.1 — 2026-09-02

### The migration no longer drops a registration setting the adopter chose

Found by the operator on the first real adopter workspace to hit the v1.9.0 migration — a
`profile_kind`-agnostic settings defect, not a plugin one.

The AC-UAW-4 migration heals a pre-v1.7.0 tag-pinned registration by a `marketplace remove` →
tagless `add` pair. That replaces the **whole** `extraKnownMarketplaces.<marketplace>` entry, and
the repair introduced in v1.9.0 restored only the settings file's **top-level** keys — so any
sibling of `source` inside that entry was silently dropped. The observed case was
`autoUpdate: false`: an adopter who had deliberately disabled auto-update on this marketplace had
it re-enabled, with the preview never saying so.

The carried-forward set is an explicit **allowlist**, currently just `autoUpdate`. A
skip-`source` denylist would carry an *unanticipated* key forward by default, and the dangerous
shape is a second pin-like key: the shipped `claude` binary carries `ref`, `commit`, `scope` and
`lastUpdated` strings in marketplace context, and restoring a stale `commit` would be pin
resurrection under a different name — defeating the very migration this repair serves. Claude Code
publishes no schema for that object, so a denylist cannot be completed. An unknown key is therefore
dropped and the platform re-adds what it still wants. The restore is additive within the allowlist:
if the platform's own re-added entry declares the key, that value wins.

AC-UAW-15(a) already required the scope's settings be left semantically unchanged "apart from the
registration itself"; the pinned `source` is the registration, an `autoUpdate` toggle beside it is
not. So this is a defect against an existing criterion, not a new one — no re-authorization.

### The update package's own version is now forced to move with its pin

A latent release hazard, live on this very cut: `update-agentic-workspace@0.1.0` was already
published, so bumping only its `create-agentic-workspace` dependency pin would have left the
registry serving a package pinned to the previous shared modules — a fully green release shipping
the fix to nobody. The publish workflow's "skip if already on the registry" gate makes that
silent. A test now asserts that when the pin moves, `update-agentic-workspace`'s own version
moves with it.

Both npm packages therefore ship this release: `create-agentic-workspace` 0.9.1 and
`update-agentic-workspace` 0.1.1.

## v1.9.0 — 2026-09-01

### `npx update-agentic-workspace` — one command that brings an installed workspace current

**The receiving side of a release, not just the cutting side.** Every prior release documented the
upgrade procedure as a hand-run sequence transcribed out of the cut-release runbook — marketplace
refresh, per-scope plugin update, a read-back verification, then a workspace reconcile. This
release ships that sequence as one command, plus the one-time migration every pre-v1.7.0 adopter
needs and could not discover on their own (their tooling truthfully reports "already at the latest
version", forever, while pinned to a frozen tag).

- **`cli-update/`** — a new, thin npm package (`update-agentic-workspace`) depending on
  `create-agentic-workspace` at an exact, equal version; no shared module is vendored twice.
- **`cli/src/pluginRefresh.mjs`** — Phase 1 (marketplace refresh, migrating an adopter still
  tag-pinned from before v1.7.0, once per affected scope) and Phase 2 (plugin update in every scope
  that enables the plugin, verified by reading back the refreshed cache manifest rather than
  trusting the invoked CLI's own success line — the v1.4.1 scar, made mechanical). Every `claude`
  invocation this command makes is drawn from one frozen, closed six-subcommand allowlist.
- **`cli/src/cleanup.mjs`** — the destructive third phase, opt-in behind `--cleanup`: prune
  superseded plugin-cache versions (the live set is read from the platform's own state, never
  computed by sorting version strings — the newest directory on disk is not always the live one)
  and remove a stale or duplicate marketplace registration that no scope still enables. A flagless
  run previews every candidate and removes nothing at all.
- **`cli/src/update.mjs`** — the phase orchestrator: preview before the first mutation, the
  managed-file and permission-floor reconcile as the final phase, and a per-phase summary so a
  no-op run is distinguishable from a silent failure.
- **`docs/troubleshooting.md`** now points at the surgical `--cleanup` prune beside the existing
  blunt `rm -rf ~/.claude/plugins/cache/` recovery, which stays for the case nothing on disk can be
  trusted.
- **`cli/README.md`** now points at the sibling entry point and scopes its "never runs `claude`"
  claim to `create-agentic-workspace` specifically — the update command is the one exception to
  that posture, bounded by the closed allowlist above.

## v1.8.0 — 2026-09-01

### The retired session-context framework's name leaves the repo entirely

**The dependency was severed in v1.6.0; the name is now gone too.** This repo is public, and a plugin
install is a full `git clone` with no file-exclusion mechanism, so every literal occurrence reached
every adopter. There were **617 case-insensitive occurrences across 21 tracked files** at v1.7.0, and
**428 of them — 69% — were the removal machinery itself**: the zero-reference gate, its frozen
forbidden-token corpus, and the absence-proving tests. A forbidden-token gate cannot detect a token
without spelling it, so the guard was by construction the largest instance of what it banned.

It had also begun obstructing the cleanup it existed to enforce: because the gate required every
allowlist entry to cover a live occurrence, removing an allowlisted reference turned the suite red
until the guard itself was edited.

- **The gate and its fixtures are deleted** — the sweep module, its frozen sweep definition, and the
  module-absence proof that accompanied them. Retiring completed migration scaffolding at a
  release boundary is the industry lifecycle (Rust removes a future-incompatibility lint once the
  condition becomes unrepresentable; a ratchet at zero budget is inventory with a carrying cost).
- **`tests/test_infra_prose_grounding.py` is re-grounded, not deleted.** Its two token-absence sweeps
  and the overloaded-construct pin are removed; the **eleven positive prose invariants** that guard
  the *replacement* doctrine survive and still pass. 18 cases, down from 35.
- **Absence assertions became exactness assertions, which is strictly stronger.** The fleet
  machinery's selftest and tests now freeze the import graph and the record/`KNOWN_SAFE` key sets by
  exact equality instead of naming retired keys. The previous `issuperset` form was blind to extra
  keys; exact equality convicts a re-added retired key *and* any other unexpected one.
- **Ordinary "context" abbreviations are renamed** rather than pinned: the statusline pressure bar
  renders `tok` (same three-character width), plus the git-discipline and session-learnings helpers,
  a reviser callback parameter and two loop locals.
- **The changelog record is kept.** Descriptive references are reworded; proper identifiers are
  dropped rather than renamed, because renaming a published atom's name would be a falsehood. No
  released section or entry was removed.
- **No guard runs for adopters; CI keeps a one-line re-entry locator.** The deleted gate was 400+
  lines of test corpus that reached every adopter by construction. Its replacement is a single
  allowlist-free `git grep` step in `.github/workflows/ci.yml` that fails the build on any
  occurrence. Being a workflow, it still lands on disk in an adopter's plugin cache — an install is a
  full clone, as above — but it never executes there and is not part of the plugin surface — **contributors should know it exists**, because it reds a PR containing the retired
  substring anywhere outside that workflow file itself. The heavier "no dependency" assertion over
  manifests and the import graph is deliberately NOT built: nothing imports the retired framework,
  so such a detector would ship with an empty subject. An encoded or runtime-assembled token list
  was researched and **rejected** — no industry precedent exists, and every surveyed tool stores its
  forbidden literals in reviewable plaintext by design.

The `create-agentic-workspace` installer CLI is **unaffected** by this change: no install line, pin
or scaffold output moves, and an adopter upgrades with the usual single `plugin update`.

Security review: required and performed — the change touches `hooks/` and the shipped version ledger,
three arms of the security-path floor.

## v1.7.0

### The documented install line stops freezing the adopter's catalogue (feat-foundry-install-line-unpinning)

Every documented
marketplace-add install line (README, QUICKSTART, troubleshooting, the brownfield how-to, and the
release runbook itself) registers the marketplace **tagless** now — no version literal on the
source argument. A tag-pinned registration lands verbatim in an adopter's `settings.json` and every
subsequent upgrade command re-reads that frozen ref forever, truthfully reporting "already at the
latest version" even after a new release publishes: publishing was never delivery. The artifact pin
(`source.sha` in `.claude-plugin/marketplace.json`) is unchanged and remains the real supply-chain
control.

The release runbook (`skills/cut-release/SKILL.md`) is rewritten to match: the complete adopter
upgrade is now one command, run forever; an existing tag-pinned adopter gets a one-time migration
instead of a per-release re-point. The runbook also prescribes deferring the marketplace.json
catalogue bump (version + `source.ref`) out of the release commit **R** into the re-pin commit **R2**,
together with `source.sha`, so the default branch would never advertise a catalogue version its own
pinned commit does not carry.

**Known limitation — that deferral is not executable, and this release does not achieve it.**
`claude plugin tag --dry-run`, which the required `release-acceptance` check runs by way of
`scripts/foundry-release-acceptance.py`, refuses any tree whose `plugin.json` and
`.claude-plugin/marketplace.json` versions disagree: *"Version mismatch ... update the marketplace
entry ... before tagging."* The atom relaxed the two checks this repository owns (`test_manifests_agree`
and the cut-release preflight) so both tolerate the lag, but never exercised the sequence against the
platform's check — the one that actually gates the merge. So **R** must still bump the catalogue
version, and the R-to-R2 window stays open, including on this cut: the coherence check below
correctly reports `main` red for the span between the two merges. Under a tagless registration that
window is fail-unsafe, because `source.sha` outranks `source.ref`. Recorded for correction; the
runbook and the how-to still prescribe the unexecutable sequence.

The shipped-doc
install-pin checks are inverted to match (a version literal on the registration now fails, not
passes), each carrying a negative control proving the inversion discriminates.

**Scope note:** this does not, by itself, deliver the one-command upgrade for every onboarding
path. The `npx create-agentic-workspace` pre-session bootstrap and the existing-repo installer
still compose a pinned source in code; the sibling `feat-foundry-installer-unpinning` carries
that half.

### The default branch's catalogue is verified on every push (feat-foundry-main-catalogue-coherence)

Sibling work removes the `#vX.Y.Z` tag from this plugin's marketplace registration so adopters can
upgrade with one command. That move shifts catalogue resolution from the release **tag**'s
`.claude-plugin/marketplace.json` — graded at cut time by `tag_pin_coherence`
(`scripts/foundry-cut-release.py:306`) — to the **default branch**'s blob, which until now was
graded by nothing.

`scripts/foundry_main_catalogue.py` is the replacement control: a read-only, offline check over the
default branch's checked-in `.claude-plugin/marketplace.json` for the `plugins[]` entry named
`foundry`. It refuses unless `source.sha` is a full 40-character lowercase hex id, resolves to a
plain revision (never an annotated-tag object), is an ancestor of the default branch, and the
`.claude-plugin/plugin.json` checked in at that revision declares the same version the catalogue
advertises. Wired into `.github/workflows/ci.yml` as a step scoped to push-to-`main` only — never
on pull requests, because the check grades `refs/heads/main` and a pull-request checkout leaves a
detached HEAD with no such local ref, so it would fail closed on every PR. (An earlier draft of this
note justified the scoping by the release window in which the version bump lands ahead of its pin.
That window is real and still open — see the known limitation above — but it is not why the step is
push-scoped; the detached-HEAD reason is.)

This is a **detective** control, not a preventive one: it reports an incoherent default branch
after the fact; it does not stop one from being pushed.

**No `create-agentic-workspace` change in this entry** — this is a plugin-repo CI/governance-only
atom; the scaffold it writes is untouched.

Spec: `specs/features/foundry/governance/main-catalogue-coherence/` (workspace), authorized.

### Both installers register the marketplace tagless (feat-foundry-installer-unpinning)

**The two INSTALLERS that compose the marketplace registration stop freezing it, in code.**
`scripts/foundry-bootstrap.sh`'s default `toolchain-install` and the `npx create-agentic-workspace`
CLI (`cli/src/permissionFloor.mjs`) both used to compose an explicit release-tag ref onto the
registration unconditionally — `feat-foundry-installer-unpinning` closes the code-path half the
sibling docs-only change above could not: the shell installer's default plan now registers the
marketplace tagless (`--ref`/`--channel edge` still compose an exact ref, unchanged), and the npx
CLI's composed `extraKnownMarketplaces` entry drops `source.ref` entirely, restoring the shape its
own already-authorized `AC-BCL-4(b)` declares. `permissionFloor.mjs` carries, in writing, the
supersession of the prior PR #61 security-review Block that added `ref` in the first place — read
it before touching that file again. `cli/src/floorReconcile.mjs`'s pinned-state predicate is taught
that a tagless entry **naming this marketplace's own github source**, with `autoUpdate` absent or
`false`, is pinned (not unpinned) — a foreign, malformed, or auto-updating entry still withholds, so the allow-tier grant is not
silently withheld for every adopter as a side effect; its skew report is fixed to never claim
"pinned at null". `.github/workflows/btb-gates-base.yml`'s security-path alternation now covers all
three files. The artifact pin (`source.sha`) is unchanged throughout.

**Known residual (shell installer only).** `claude plugin marketplace add` exposes no `autoUpdate`
flag and writes no such key, and `toolchain-install` is constrained by a frozen invariant to need
only the `claude` binary — so it cannot write one either. The npx CLI writes the explicit `false`.
For a shell-installed adopter the platform default therefore governs whether the catalogue
re-resolves from the default branch unattended, and that default is not established anywhere in
this repository. Pass `--ref <tag>` if you want the old frozen-index behaviour.

## v1.6.0

**The retired session-context control-plane dependency is severed.** The framework no longer probes, requires, or
references an external environment control plane. Infra mutations run on standard `aws` / `tofu` /
`kubectl` over IaC against the AWS context the OPERATOR has already configured — and the operator's
IAM restrictions ARE the control, out of the framework's scope. It never acquires credentials,
establishes connectivity, or second-guesses that context.

Before this release, with the retired CLI absent from PATH the posture probe fail-closed to REFUSE, so
`id-apply` refused **every** infra mutation on any machine without that CLI.

The posture layer is **deleted, not replaced**: no prod-vs-non-prod branch, no guard state, no
break-glass, no runbook-generation outcome. `decide_apply` now derives the GitOps class itself from
the frozen change scope and routes on three outcomes — EXECUTE (the default path for a well-formed
direct change), VERIFY_ONLY (a **correctness** routing where the ArgoCD controller owns the path, not
a permission check), and REFUSE on five mechanically unresolvable inputs.

Shipped with a standing exit gate that proves the removal and keeps it proven, and with the
`cloud-cli-exec-guard` reclassified honestly as defense-in-depth rather than a security boundary.


### A zero-reference exit gate proves the retired session-context framework stays gone

**The removal program's exit criterion: an allowlist-aware whole-tracked-tree sweep, additive
and test-only, that computes RED on any reference to the retired framework anywhere in the repo and
GREEN over the shipped tree.** `tests/test_reference_sweep.py` + its fixture
`tests/fixtures/reference-sweep/retired-framework-sweep.yaml` add one pytest module and one
fixture; no shipped script, hook, schema, skill or workflow is edited.

- The catch-all token is a bare case-insensitive substring, deliberately not word-boundaried, so
  the gate has no false negatives at the file level. The discrimination a word boundary cannot do
  — telling an unrelated "context" abbreviation from a literal reference into the retired module,
  which are the same shape — is done by an occurrence-keyed allowlist instead, one reasoned entry
  per tolerated identifier.
- Per-element negative controls across five surface classes (a skill, a published doc, a pack
  file, a script, a hook) and per-site anti-blanket proofs across eight overloaded-abbreviation
  sites both compute RED on an injected synthetic reference — a green that was never red proves
  nothing.
- Every allowlist entry is proven load-bearing: it covers a real occurrence, and removing it alone
  turns the sweep RED naming that entry.
- A `--root <tree>` mode runs the identical frozen sweep over any checkout; the `agentic-handbook`
  checkout re-verifies at zero references.

### The apply gate loses its posture input and executes the change (feat-foundry-apply-gate-regrounding)

**`decide_apply` no longer probes an ambient control plane for a posture — the operator supplies a
correctly configured AWS context, and its IAM restrictions ARE the control.** The retired status-probe command
--json` probe left the shipped gate inert on any machine without that CLI (`REFUSE` on every
mutation); this atom subtracts the dependency rather than replacing it.

- **Signature change (AC-IDAGR-10):** `decide_apply(*, changed_paths, infra_binding)` — the `posture`
  parameter is gone, and `decide_apply` now derives the GitOps class ITSELF by calling
  `classify_gitops` (unchanged, AC-IDAGR-5) rather than accepting a class its caller asserts. No
  parameter offers a caller a way to supply or override the class.
- **Two outcomes, one refusal, five conditions (AC-IDAGR-2):** `GENERATE_RUNBOOK` and the break-glass
  `audited` flag are gone (AC-IDAGR-1), together with `RunbookPayload.executed_by`. A well-formed
  `direct` change EXECUTEs unconditionally — the default path; a `gitops` change routes to
  VERIFY_ONLY (a correctness routing — the ArgoCD controller owns reconciliation of that path, not a
  permission check); REFUSE fires on exactly five mechanically unresolvable inputs — the two added
  here being a malformed `infra_binding.gitops_paths` (an empty list is well-formed) and a
  `changed_paths` whose FORM is not a list/tuple/set of well-formed relative POSIX paths. The fifth
  came from the gating security review: an absolute, padded, backslash-separated, non-printable or
  non-normal member (including the `.` / `..` whole-repo and escaping spellings) matches no
  `gitops_paths` glob, so without it a wholly controller-managed change classified `direct` and
  EXECUTEd against a scope covering ArgoCD-owned manifests.
- **The shipped `aws-eks-karpenter` pack's `plan`/`apply` slots now carry a saved-plan sequence
  (AC-IDAGR-3, v0.5.3):** `plan` writes `-out=.foundry/infra.tfplan`; `apply` applies that exact same
  literal path, byte-identical, no placeholder token, no `-auto-approve` over a freshly computed plan.
  `.foundry/` is the repo-root runtime partition the shipped `.gitignore` already default-denies
  (AC-IDAGR-12) — load-bearing, because a saved plan carries every `TF_VAR_*`/state value in
  plaintext and must never be committed or published as a build artifact.
- **A rendered/logged `ApplyDecision` is secret-scrubbed (AC-IDAGR-11):** `foundry_id_apply.py` gains
  a local `render_decision()` helper (mirroring, not importing, the sibling redaction surfaces) that
  scrubs the rendered `command`/`verify`/`reason` strings; the EXECUTE branch's actually-run command
  stays the frozen `infra_binding.apply` bytes, unaltered.
- **Documentation corrected off the retired authority (AC-IDAGR-7/-8/-9):** the `apply` slot's two
  description sites (`schema/stack-profile.schema.json`, the pack comment) now state it is
  operator-authored, executed as written, and bounded by the operator's IAM context — never crediting
  the loader's read-role check, which never inspects `apply`. The loader's own commentary
  (`scripts/foundry-stack-profile.py`) now names itself the sole static floor on the read-only
  `plan`/`verify`/`policy` slots rather than deferring to the retired runtime guard. The gate module's
  own docstrings and comments are rewritten to describe only the surviving behaviour.
- **`skills/id-apply/SKILL.md` rewritten (AC-IDAGR-6):** the procedure resolves the profile → decides
  (the gate re-derives the class itself) → drives the branch; no session probe, no posture, no retired control plane.

**No external-authority claim survives, and this atom deletes nothing.** The retired posture module
is left untouched — it becomes unreferenced by this atom, and a sibling atom retires it separately so
the tree stays green at each step.

**No `create-agentic-workspace` change in this entry, and the scaffold it writes is untouched.**
Checked rather than assumed: the package bundles `cli/permission-floor.json` and its templates, not
`schema/acceptance-contract.schema.json`, so the new classification member does not reach a newly
scaffolded or reconciled workspace until that workspace's plugin is updated. Adopters get it from the
plugin, which is version-keyed, not from the wizard.

### The shipped prose is re-grounded on operator-supplied AWS context

**Every remaining reference to the retired session-context framework and its posture gate, across
26 files, is rewritten to describe the reality the sibling code atoms ship: the framework EXECUTES
standard `tofu`/`kubectl`/`aws` against the AWS context the operator has already configured, and
that context's IAM restrictions are the control — outside the framework's scope.** There is no
posture, no prod-vs-non-prod branch, no guard state, no break-glass, no runbook-for-guarded-prod.
Prose asserting any of those is deleted, not restated in new words.

- **`skills/id-apply/SKILL.md`:** states the operator-supplied AWS context, names `aws sso login` /
  `aws configure` / assume-role / VPN as things the framework never does, frames the GitOps-vs-direct
  routing as a correctness concern (the ArgoCD controller reconciles a GitOps path; a direct apply
  there would fight the controller), and states the saved-plan sequence (`-out=.foundry/infra.tfplan`
  → `apply .foundry/infra.tfplan`, the literal path in both renderings, no placeholder) as applying
  exactly what was planned.
- **`skills/id-promote/SKILL.md` rewritten (13 retired-token lines / 18 forbidden-phrase lines closed):** the
  frontmatter `description` and body now describe promotion as re-deriving the change scope and the
  GitOps class per environment and driving `id-apply`'s real two-input `decide_apply(changed_paths,
  infra_binding)` — never the removed `posture`/`blast_tier`/`high_blast_acked` arguments, never
  `GENERATE_RUNBOOK`. The EXECUTE branch runs against the AWS context the operator has configured for
  the target environment.
- **The four offline/read-only steps keep their offline claim, session framing dropped
  (`id-validate`, `id-test`, `id-simulate`, `id-architect`):** each still states the step runs
  entirely offline — no live cloud account, no cluster, no credentials, no mutating command.
  `id-validate`/`id-test` keep the frozen `## Offline (no-guarded-exec) mode` heading verbatim
  (`AC-IDOFF-1`); `id-test`'s now-dead no-posture-path section is
  deleted outright, not reworded.
- **`docs/glossary.md`:** the retired-framework term entry is replaced by a `guarded-exec wrapper` /
  `cloud_cli_exec_guard` entry — adopter-supplied, config-gated and fail-INERT, defense-in-depth, not
  a security boundary; the operator's IAM restrictions are the control.
- **`hooks/foundry-cloud-cli-exec-guard.sh` reclassified, not weakened:** its header now states
  plainly it is adopter-configured defense-in-depth against the framework's own mistakes and
  explicitly not a security boundary, sharpening the existing "one tier weaker than the MAC systems"
  hedge rather than inventing a new downgrade. Its matcher, activation rule, and block behaviour are
  byte-unchanged. The doc example wrapper is now the vendor-neutral `exec-wrapper` placeholder
  (`tests/test_hooks_guards.py`'s fixtures updated to match, guard suite unchanged in behaviour).
- **`skills/infra-sandboxed-apply/SKILL.md` (dormant design intent, no live implementation):**
  `decide_sandbox_apply`'s described signature loses the `posture` input and its REFUSE branch no
  longer routes to the deleted `GENERATE_RUNBOOK` enum; it states the REFUSE outcome directly.
- **Eleven files carry a pure deletion** (`id-drift`, `id-rollback`, `id-discover`, `id-plan`,
  `id-verify`, `id-import`, `id-implement`, `id-baseline`, `id-sync`, `fleet`,
  `agents/infra-engineer.md`): the session/posture/guard clause is struck with nothing invented in
  its place — the step simply runs the command.
- **New `tests/test_infra_prose_grounding.py`** carries every criterion above (35 tests) plus the
  false-positive allowlist's survival proof (the statusline pressure bar, the git-discipline block helper, the session-learnings locals,
  the `spec-audit.js` reviser param, the tier-preflight local, the stack-profile loop local, and the
  and the context-lifecycle skills all remained verbatim — none of these were ever references to the
  retired framework).

### Retirement is expressible — `classification: remove` (ER #120, ER #121)

**An atom that retires an artifact used to become unamendable the moment it succeeded.** Two
independent freeze floors refused it, and closing either alone left the atom stuck behind the other.
Both were reproduced against v1.5.0 before the fix.

- **`system_grounding.classification` gains a fourth member, `remove`** — declared in both the module
  constant and the schema enum, which `test_classification_vocabulary_set_equality_read_as_json`
  holds set-equal. Reading the schema as JSON rather than through `jsonschema.validate` is
  deliberate: the JSON-Schema floor returns early when that library is unimportable, so a one-sided
  edit is invisible in exactly the environment where it goes undetected — and it fails permissive.
- **`remove` is FAIL-CLOSED ON ABSENCE, not a never-failing value.** Declared-removed-but-still-present
  is refused, naming the honest alternative. The two-state problem is solved by the existing
  lifecycle rather than a phase field: declare `exists` at first authorization, amend to `remove` at
  re-authorization once the removal has landed. The amendment is the transition, hash-covered and
  operator-reviewed. This is what keeps `remove` a member of the consistency floor instead of a
  per-artifact opt-out from it — a distinction four downstream consumers of that floor's verdicts
  inherit. Verified: a bogus removal classifies STALE in `foundry_grounding_conformance`, not grounded.
- **New `removal_grounding_errors(data, repo_root)`** supplies the same fail-closed predicate for
  UNGROUNDED kinds, where the snapshot carries no dimension — **but it is NOT YET WIRED, and that is
  said plainly here rather than discovered later.** No caller invokes it: `foundry-authorize.py` is
  outside this atom's authorized scope, so the call site is a follow-up. The PR-diff security review
  found this and it is recorded rather than papered over. What that means concretely: an ungrounded
  `remove` declaration naming no live artifact is currently accepted on trust. What it does **not**
  mean is a widened scope — every `allowed_paths` entry admitted by the retirement tolerance has
  already been **evaluated by** `_allowed_path_exists` and found ABSENT, and that evaluation is the
  byte-identical `os.path.exists` call the missing check would make. (Said that way deliberately:
  "already passed `_allowed_path_exists`" reads as "passed the check", which inverts the argument —
  the entry reached the tolerance precisely *because* the predicate returned False.) The gap is
  therefore confined to `remove` declarations not mirrored in `allowed_paths`.
- **The removal predicate applies the cross-dimension collision catch for ANY kind**, mirroring
  `net-new`. Gating it on grounded kinds (an earlier draft did) left
  `{kind: resource, identifier: "sessions", classification: remove}` silent while `sessions` is a
  live table — a per-artifact opt-out reached through `kind` rather than `classification`, and
  `resource` is exactly the kind the path tolerance keys on. Also found by the diff security review.
- **`allowed_paths` grounding admits a retired path** when the same contract declares it removed
  (ER #121). Bounded three ways: `kind: resource` only (the schema now declares that
  identifier-to-path correspondence rather than leaving it inferred), literal paths only — never a
  glob, which would admit an unbounded subtree on one line — and withheld when another artifact
  declares the same identifier alive.
- **The zero-match diagnostic no longer asserts a cause it has not established.** It said "a stale
  path prefix or typo", which this floor cannot distinguish from a deliberate removal, and offered
  the wrong remedy for the latter. It now carries a named `observed:` part and a named `routes:` part
  enumerating all three ways out, asserted structurally so a reword cannot reintroduce the claim.

**Two stale comments corrected while in the file.** `allowed_paths` was described as "the one contract
field the merge gate actually enforces set-containment against (CHECK-4)"; there is no bespoke merge
gate — it was removed in v0.24.0 and the sentence outlived it. `allowed_paths` is today a
*permission-granting* input three authorize-time floors consult to relax themselves, which is the
opposite polarity and is why the retirement tolerance is bounded to literal paths. The
`allowed_paths_grounding_errors` docstring also now records that AC-APG-2's "any other entry fails
closed" is narrowed by the third admission path.

**Correction to ER #120's own account, recorded because it makes the defect worse rather than better:**
the ER states every classification is refused after a landed removal. Measured, `net-new` was
*accepted* — so the framework left exactly one open path, and that path recorded a deleted artifact as
one that does not yet exist, then froze the falsehood into the contract hash.

Spec: `specs/features/foundry/gate-integrity/retirement-grounding/` (workspace), authorized
`auth_seq 1`, `spec_sha256=6102dc33b4534b64…`.

### `/foundry:fleet` stops probing the retired control plane on every run — the infra discriminator is re-sourced from the stack-profile lock

**The one unmocked production probe of the retired session-context control plane in the whole workspace is gone.**
`derive_all` invoked a probe helper on every `/foundry:fleet` invocation, which shelled out to the
retired status command. All three imports of the retired posture module — `derive_infra`, the probe helper,
and (the one a reader scanning `derive_*` functions misses) the module's own `--selftest` — are gone,
and the helper itself is deleted.

- **The `infra` discriminator is re-sourced from the committed stack-profile lock**
  (`resolve_lock(project_dir)` + `profile_kind: infra`) instead of a live session probe. This is a
  genuine, disclosed meaning change: `infra` is now **project-scoped, not session-scoped** — every
  session row in an infra-profile project reads `infra: true`, not only the invoking session's own
  row, because the source no longer varies by session. Nothing downstream gates on it; it is a display
  discriminator.
- **The posture field and `break_glass` are REMOVED, not renamed.** With the posture concept itself retired,
  a renamed field would be permanently null and, under the roster's own default-deny rule, permanently
  rendered as attention on every row forever. Removed from the machinery record, the `KNOWN_SAFE`
  default-deny table, `is_field_clear`'s signature (no longer accepts a `break_glass` keyword), the
  roster's `deny()` helper, and both roster selftest fixtures.
- **`resolve_lock` raises `StackProfileError` on every failure it detects, including a tampered content
  sha256 pin — the call site now catches it and distinguishes two failure states** rather than letting
  either escape: no lock at the project dir ⇒ `infra` false, `source_unavailable`; a lock present but
  unresolvable ⇒ `infra` false, `degraded`, carrying the resolver's own message. Both name the lock.
  `derive_all` resolves the discriminator exactly once, before its per-session loop — the same position
  the removed probe resolved at — so an unwrapped call there would have taken the whole roster down
  on a single tampered pin rather than degrading one field.
- **First pytest coverage for `scripts/foundry-fleet-session-machinery.py`** (`tests/test_fleet_session_
  machinery.py`) — the two fleet modules had none before this atom, which is how the live status probe
  exec survived unnoticed in a production path this long. The module's own `--selftest` still runs
  clean (`FLEET-SESSION-MACHINERY-SELFTEST-GREEN`) but is not treated as a substitute.
- `blast_radius` stays inert (no live call site supplies `blast_tier`); this atom neither fixes nor
  worsens that.

Spec: `specs/features/foundry/fleet/infra-discriminator-regrounding/` (workspace), authorized
`auth_seq 1`.

### The retired posture module is deleted

**The framework's last hard dependency on the retired CLI is gone.** The posture module
shelled out to the retired status command and, with that CLI absent from `PATH`, fail-closed to `REFUSE` — refusing
every infra mutation on any machine without that binary. The two sibling atoms above severed every
consumer first; this atom deletes the now-unreferenced module itself.

- **The retired posture module and its dedicated test file are deleted outright.**
- **The `not_invoked` entry naming the deleted script is removed from both permission-floor mirrors**
  (`cli/permission-floor.json`, `docs/permission-floor.json`), which remain byte-identical.
- `docs/glossary.md` needed no edit — the prose-decoupling atom above already removed the retired-framework entry.
- A whole-tree, no-allowlist sweep confirms the module's identifier now has zero occurrences in
  either spelling anywhere in the shipped tree.

Spec: the posture-retirement atom in the workspace corpus, authorized
`auth_seq 1`.

## v1.5.0 — 2026-08-14

**`create-agentic-workspace` 0.4.2 → 0.5.0.** The wizard's own code is unchanged, but what it *writes*
moves by one line: `cli/permission-floor.json` is the package's bundled map, so a newly scaffolded or
reconciled workspace receives one additional `allow` rule (for the read-only module below). The tarball
therefore bumps for its own sake as well as for the marketplace pin it embeds — an unpinned
`npx create-agentic-workspace` would otherwise keep scaffolding the previous floor. An earlier draft of
this entry claimed the wizard was untouched; a later one claimed the package did not move. Both were
wrong in the same direction, and the release suite is what settled it.

### The autonomous driver gets a clock, and "is there work?" stops being a judgement

`/foundry:mode-autonomous` already declared itself a wrap over native `/loop` + the release-wave
Workflow + the native floor. It was correctly designed and it ran **once in thirty days across seven
products**, because it said *what* to compose and never said when to wake, what a tick does, or when
there is nothing to do. The 30-day mining found the binding constraint and it was not correctness:
**37 of 52 resumes were the agent stopping silently after finishing work**, and ~37% of operator turns
were the operator being the runtime — scheduler, clock, merge button. Correction ran 1.7-3.8%.

This adds the missing half as a delta to that skill plus one resolution module, and it adds **no gate,
no ledger and no tracker**. `scripts/foundry_command_deck.py` composes three shipped derivations rather
than forking them — `load_release` (slug-only, containment-checked resolution), `derive_run_state`
(per-atom authorization re-derived through `foundry_authz`, plus the dependency gate) and
`compute_wave_plan` (wave grouping *including* declared-write-path overlap, so two atoms that touch the
same tree never start concurrently). The genuinely new part is small: the in-flight listing, the wave
barrier applied to a ready-set, and four predicates.

**The two questions the driver got wrong are now computed, not judged.** `ready_set()` answers "what may
I start" and `is_idle()` answers "is there anything to do" — idle **iff** the ready-set is empty AND no
worker is running, driven over all four corners. An agent asked to judge whether a quiet tick is really
quiet gets it wrong in both directions: halting while work waits, or inventing work to look busy. The
first draft of this criterion was invocation-scoped and therefore *authorized the silent halt it was
written to abolish* — an atom becoming ready on a later tick had no starting obligation, and the idle
rule then mandated stop-forever-with-work-waiting. Caught at review; the criterion is now
tick-independent by construction, with no cursor and no seen-set anywhere in the module.

**The auto-merge grant is RESTORED** (operator decision 2026-08-13), superseding the withdrawal
language in the skill, and bounded by the shipped `foundry-git-discipline.sh` clause rather than by
anything new: `--admin` stays blocked outright and a plain merge needs every check passing. Landing
evidence must be an **affirmative** success conclusion from the forge for the head commit — an absent,
empty, pending, `neutral` or `skipped` conclusion is not evidence, and neither is anything the worker
reports about its own work. The first draft constrained only where the evidence came *from*, which made
it satisfiable by `neutral` — weaker than the hook beside it.

**Escalation is a closed two-member set**, which is the atom's whole reason for existing: dispatched
workers cost 0.22-0.54 operator interventions per 100 turns and **decks cost 10.66-12.07**, recorded as
*"the deck became the inbox."* Of the three standard ambient-agent HITL patterns the driver keeps
**notify** and does not offer **question** or **review** as standing surfaces.

Also carried, each from an observed failure in one 12-hour run: merged is not applied (an atom with a
live surface is not complete while `deploy-status` reports it stale or not rolled); hand the operator
exactly one self-contained command with its preconditions verified first, naming which guard refused it;
and verify the outcome against the world rather than against a report of success.

**Six atoms were planned and five were dropped before any code** — two retracted at review as custom
gate engines, which no mainstream delivery tool ships and which this repo deliberately does not, two
because the capability already ships (the native Task graph; `deploy-status`' STALE/NOT-ROLLED cross-check), and one
with the operator's no-new-gate-machinery decision. The records are in `.foundry/decisions/`.

**Found while building, and worth a separate look:** **10 of the workspace's 15 release manifests are
refused by `foundry_release.load_release`** — every ADL-era one carries top-level fields the validator
rejects. The loader is real and used (5 do load), so this is corpus drift rather than a dead schema.
This release's own manifest was fixed; the other nine were not touched.

**Security review:** the diff adds one read-only derivation module, its tests, prose in a skill, **and a
permission-floor `allow` entry with its digest pin, its bundled CLI mirror, and the differential corpus**
— named here because the map edit is the part of this change most worth a reviewer's eye.
`security-path-base` flags it on `^skills/`, by design — a SKILL.md is prompt-level executable surface.
The module writes nothing (asserted by a byte-level before/after over the whole fixture tree), the
merge-grant restoration is bounded by the existing hook and adds no new merge path, and manifest
free-text is neutralized wherever it is rendered or forwarded because the native Task tools bypass
PreToolUse.

## v1.4.2 — 2026-08-12

### A `create-agentic-workspace` release; the plugin tree is unchanged

This moves **`create-agentic-workspace` 0.4.1 → 0.4.2**. The plugin's own `scripts/`, `skills/`
and `hooks/` are identical to v1.4.1 — an adopter who runs only `claude plugin update` gains
nothing here. The version still moves because the tarball ledger keys the published CLI version off
`plugin_version`, and the marketplace pin is what carries the new CLI to `npx`. Said plainly rather
than implying a plugin-side change that is not in the diff.

Both fixes were found by *using* v1.4.1 — the first by reconciling this repo's own workspace, the
second by trying to run the test suite before pushing that fix.

**The reconcile's advisory report contradicted the write it had just made.** `--reconcile-floor`
classified permission drift *above* the write phase and printed the advisory report *below* it, so a
run that had just added 42 `allow` + 16 `ask` rules ended with:

```
permission-floor reconcile: added allow=42, ask=16, deny=0

Permission-floor report (advisory, 58 finding(s)):
  [ask-absent]   {"class":"ask-absent","rule":"Bash(claude plugin tag:*)"}
  [allow-absent] {"class":"allow-absent","rule":"…foundry-audit-prepare.py:*)"}
  … 56 more, every one of them just written
```

An operator cannot tell that from a write that silently failed — the output was convincing enough
that the write got re-verified against `git show HEAD:` before it was believed. The create path had
the same defect whenever it wrote `settings.json` itself.

The union classification's only consumer is that report, so it now runs where it is consumed: after
the write, describing the state the operator is left in. The reconcile's *own* classification stays
above the write and over the tracked file alone — consent has to be informed by what *will* be
written, which is a different question from what remains after. Verified against byte-identical
fixtures: 58 stale findings → 0, with identical `settings.json` written either way. Display-only.
`cli/test/run-orchestration.test.mjs` adds the first coverage of `runCli`'s ordering — every
primitive involved was already unit-tested and correct, and the defect lived in the sequence, which
nothing exercised. (#112)

**The test suite could not pass on a self-hosting checkout.** Two tests asserted `DOCTOR-GREEN` with
the doctor's project dir at `REPO_ROOT`. The `control-plane` probe walks *ancestors* of that dir for
a `foundry-project.json` naming the repo in `repos{}` — so the verdict depended on where the clone
sat on disk: green on a standalone CI checkout, permanently red on one nested in the workspace that
hosts it, which is how the framework is actually developed. Measured at `2 failed, 1640 passed`.

Green in CI and red on the maintainer's machine is the worst way for an assertion to be wrong: it
quietly retires "run the suite before you push", because once two failures are expected a real
regression arrives camouflaged among them. Both now run from a neutral temp project dir with
`CLAUDE_PLUGIN_ROOT` still the real tree — which is what `foundry-release-acceptance.py`'s own
AC-RELACC-3 doctor check has always done, so this converges the tests on the shipped gate's answer
rather than inventing one. A grep-shaped guard fails any future test that reintroduces the pattern.
No coverage lost — the hosted-repo detection has its own hermetic test. (#113)

**Security review:** not applicable — neither diff touches `.claude-plugin/`, `hooks/`, `skills/`,
`agents/`, dependency manifests, or any auth/secret/token surface; `security-path-base` reported
clean on both PRs without a review label.

## v1.4.1 — 2026-08-12

### Everything here was found by USING v1.4.0, not by reviewing it

Two of the four are in `create-agentic-workspace` (0.4.0 → 0.4.1) and reached it the only way they
could have: by running the tool against a real adopter workspace rather than reading it.


**The trust hand-off named a consent ceremony that does not fire.** `bootstrap-cli` R8 has carried
this as unverified since before the feature existed: does adding rules to an *already-trusted*
workspace re-prompt, silently activate, or require re-trust? It was dormant while the CLI never
mutated a trusted workspace's allow set. `--reconcile-floor` made it the main path, and the first
live run answered it — **silently activates**. 42 version-wildcarded `allow` rules took effect on
session restart with no dialog at all.

That made the CLI's own closing text false on exactly that path: *"the trust dialog is the consent
ceremony, and the `allow` rules take effect only after you accept it."* Consent did happen — the
rules were listed and confirmed — but at the CLI, not where the CLI said. The text is now
conditional: the scaffold path is unchanged, because there the dialog genuinely is the grant; the
reconcile path states that the workspace is already trusted and that the preview and confirmation
were the only consent moment there will be.

**Consent was asked before the plan existed** (ER #95). `runCli`'s `print()` pushed into an array
that `bin` wrote *after* the run returned, while readline prompts wrote in real time — so
`Write the workspace as previewed above? [y/N]` was asked against a blank screen, and the preview
scrolled past afterwards. The unseen material is exactly the security-relevant part: the
machine-scope writes **outside the target root**, and the 62 permission rules. Output now streams as
it is produced; the preview and the reconcile plan both precede the confirmation. This was cosmetic
until R8 was answered — now that prompt is the only consent surface on the reconcile path, which is
what moved it from an annoyance to a defect.

**A required merge check had a SIGPIPE race in its own version assertion** (ER #83).
`shell-parse-bash32` failed intermittently *before parsing a single file*: `bash --version | head -1`
under `set -euo pipefail`, where `head` closes the read end while the container is still writing. The
pipe is gone; the assertion it guards is unchanged.

**The doctor collapsed 58 findings into one row.** `check_permission_floor` semicolon-joined the
module's line list into a single ~4,000-character string — in the one probe whose entire job is
operator visibility, and v1.4.0's two new drift classes made it worse. Findings are now one per
line, indented under the check: widest line 226 characters.

## v1.4.0 — 2026-08-12

### `create-agentic-workspace --reconcile-floor` converges an existing workspace

`feat-foundry-adoption-permission-floor-reconcile`. The wizard already computed the whole answer for
an existing workspace and refused to act on it: it ships the floor as a bundled constant, reads the
target's effective rules, names every missing rule — then writes nothing, because settings.json goes
through the whole-file never-clobber plan (exists and differs, so `drifted`, so untouched). Five of
seven adopter handbooks are missing the entire floor as a result.

The new opt-in flag adds the rules the classifier named. Nothing is removed, nothing reordered, no
other top-level key touched. The delta is recomputed every run, so a second run is silent and no
ledger records what the filesystem already answers.

Three things worth knowing before using it:

- **The pin travels with the grants.** Every bundled `allow` rule is wildcarded across the plugin
  cache and is bounded only by the pinned marketplace `ref` the create path writes in the same file,
  in the same write. Six of seven handbooks carry no marketplace entry at all, so the reconcile
  **adds the pin when it is absent**, and **withholds the whole allow tier** when an entry exists but
  is unpinned — `ask` and `deny` still apply, being strengthening rather than granting.
- **The write set is the tracked file; the report is the union.** A floor rule carried only in the
  untracked `settings.local.json` reads as covered, so the tracked file would stay incomplete while
  the report said converged — and the repo would ship to every other clone and to CI without it.
- **It requires an explicit `--yes` when there is no terminal.** Yes-mode is otherwise inferred from
  a non-TTY stdin, which also waives the `--existing` basename ceremony; a piped invocation would
  have mutated the permission floor unattended.

The write is this CLI's first to a path that already exists, so none of the existing anti-clobber
machinery applied to it: `applyPlan` opens `O_EXCL` and refuses by definition, `confinedJoin` passes
an **in-root** symlink (and `.claude/settings.json` linked to `.claude/foundry-operators.json`
resolves inside the root — the file whose key membership alone mints an authorizer), and
truncate-then-write would lose the operator's permissions block and install pin on an interrupt. One
mechanism answers all three: confinement join, **link-level** stat, temp-in-`.claude`, rename.

Drift classification also moved above the write phase and above the `--dry-run` return. It had sat
after `applyPlan`, which meant `--dry-run` never classified the floor at all.


### The drift classifier could not see a third of the floor

`feat-foundry-onboarding-floor-drift-classification`. Both implementations of the permission-floor
drift comparison — `cli/src/permissionFloor.mjs` and `scripts/foundry_permission_floor.py` — checked
`ask` entries only for being **shadowed**, never for being **absent**. There was no `ask-absent`
class. Measured against a real adopter workspace, the tools reported 46 findings and were silent
about 16 more: every ceremony rule gone, with `/foundry:doctor` calling that dimension clean.

Three additions to the vocabulary, in both implementations:

- **`ask-absent`** — the missing third. An exact mirror of `deny-missing` against the `ask` tier.
- **`tier-conflict`** — every absence test was tier-scoped, consulting only the effective tier the
  map declares. A rule the operator deliberately placed in *another* tier reported absent while
  plainly present, which would invite a consumer to add a second copy. Now reported and the false
  absence suppressed. Which of two tiers governs is a precedence question this tooling deliberately
  does not model, so the double declaration is surfaced rather than resolved.
- **map shape validation** — the Node loader parsed and returned, with no `schema_version` and no
  tier check; the Python twin already refused an out-of-enum tier, so the two disagreed on what
  counted as a loadable map. Both now refuse.

Both new classes are **informational**: an absent `allow` means more prompting, never less, and an
absent `ask` is the same, so neither reaches the session-start banner. The actionable set is
unchanged.

`create-agentic-workspace` is unchanged in behaviour by this entry — it gains no flag and writes no
new file. What changes is what its drift report can SEE, which is the precondition for the
convergence write specified separately.

### The two implementations did not actually agree

They had always claimed to agree "by construction" on the shared subsumption rule. That claim was a
comment. It is now a **differential fixture corpus** (`tests/fixtures/floor-drift-corpus.json`): every
case runs through both classifiers and the two must name the identical rule set per class.

It found a real divergence on the first comparison. Python canonicalized rules — dropping a leading
interpreter word, expanding `~`/`$HOME`, folding away the `plugins/cache/<mp>/foundry/<ver>/`
segment — and Node compared literal strings. For the input that actually occurs in the wild, the
absolute version-resolved path the harness writes on ask-to-allow persist, the two disagreed:

```
effective  Bash(/Users/<u>/.claude/plugins/cache/agentic-foundry/foundry/1.3.1/scripts/foundry-doctor.py:*)
map        Bash(~/.claude/plugins/cache/*/foundry/*/scripts/foundry-doctor.py)
           python: covered        node: absent
```

So the CLI reported 42 rules absent that the doctor could see were covered. The fold is now ported
to Node, along with the **deny direction**, which refuses the fold and requires exact reach
equality — Node had been reusing the allow-direction relation there, strictly more permissive than
the twin. Blanket detection is likewise derived from the fold rather than from an enumerated set of
three spellings.

## v1.3.1 — 2026-08-08

### Two gate defects the v1.3.0 cut hit live

Both were found while shipping v1.3.0 and fixed directly rather than filed — this repo's own
workspace is the control plane, not an adopter, so an enhancement request here is a request to
oneself. Neither changes the CLI; `create-agentic-workspace` is unchanged since 0.3.0, and its
tarball bumps to 0.3.1 only because the plugin pin it embeds moved.

**`contract_sha256` was unstable across a RE-authorization.** The hasher read the contract-proper
region as-is while the writer canonicalized it to exactly one trailing newline. `authorize()`
records the first and asserts it equals the second, so any contract whose region ended in more than
one newline refused with `internal: contract_sha256 unstable across freeze` — a message that was
right that something was broken and wrong about where. A first authorization was immune (no
sentinel yet, so the whole file is already canonical); only re-authorization exposed it. Both sides
now canonicalize. Verified before changing anything: across 352 acceptance contracts, zero hashes
move, so no existing frozen authorization is invalidated.

Two Risks from the security review are closed rather than disclosed. Trailing newlines are **not**
universally semantics-free in YAML — a keep-chomped block scalar (`|+`, `>+`) makes trailing blank
lines part of the value, so two contracts with genuinely different frozen values could have shared
one signature. `validate_contract_bytes` now refuses chomping indicators outright, which restores
the premise as a fact about the format rather than an assumption about its authors. And the
post-freeze assertion had become unfailable now that both sides canonicalize — worse, it was blind
to the exact writer-side regression its own error message named. It now asserts a byte-level
property that can actually fail.

**The publish plan told the operator to push a branch that cannot be pushed.** It emitted a bare
`push origin main`, which a repo whose main carries branch protection with `enforce_admins` refuses
for everyone. During the v1.3.0 cut the tag pushed and main did not, leaving the release published
upstream while its commits were not on the branch. The plan now names the exact refusal text and
carries a commented fallback that routes through a PR — including the non-obvious consequence that
a squash landing rewrites the release commits, so the tag's commit will not be an ancestor of main.
That is harmless, because an install resolves the catalogue at the ref and the plugin at
`source.sha`, never via the branch — but an operator who does not know it will try to "fix" it.

**Security review disposition:** routed (the diff touches the front-authorization integrity core).
No Block; both Risks above closed in the same PR.

## v1.3.0 — 2026-08-08

### The front door now explains itself

`npx create-agentic-workspace` is the first thing a new adopter runs, and it asked questions it
never explained. The enumerated prompt rendered as `Stage mode (lean/scale): ` — the alphabet of
legal answers and nothing else — and its default was **structurally unreachable**: the suffix was a
mutually-exclusive ternary (`choices ? … : default ? … : ''`), so the one record declaring both a
choice list and a default could never display the default. Pressing Enter selected `lean` correctly
and silently. A real adopter stalled mid-wizard and had to read the CLI's own source to answer.

Every question now carries a `description` stating what the answer DOES, rendered above the prompt
and as an indented continuation in `--help`; enumerated questions get one line per choice; and the
suffix is additive — `Stage mode (lean/scale) [lean]: `. Booleans render `(y/n) [n]` rather than
`[false]`, and an empty-string default renders `[blank]` rather than nothing, so the reader can see
that Enter is both safe and pre-selected.

The `--gh-account` prompt got the most attention, because it is the only answer that writes outside
the project. It previously said just "blank to skip" while a non-blank answer wrote two global git
`includeIf` entries plus a per-account identity file. The first draft of the fix disclosed those
artifacts in config-key syntax and was **misread by this project's own operator as changing their
global identity** — which it does not do; the rule lives in the global file but fires only inside
the target folder. Copy that names the file without naming the scope of its effect is worse than no
copy. The shipped text leads with "your global git identity is NOT changed", and a mutation control
asserts that dropping that sentence while keeping the artifact list turns the suite red.

All eight prompt strings were reviewed and approved by the operator one record at a time.

Realizes ER #88 via `feat-foundry-wizard-prompt-disclosure` (13 ACs, 16 checkpoints, auth_seq=2).
Machine-scope writes were already disclosed in the pre-write preview; the residual gap was consent
ORDERING — the prompt is answered before the preview exists — so the disclosure moved to the prompt
rather than being duplicated into the preview.

**Security review disposition:** not routed. The atom touches no auth, secrets, or supply-chain
surface; `cli/src/identity.mjs`, the permission-floor writer, and `cli/package.json` are all
contract-denied and unmodified. The change is prompt text and its rendering.

**Known-unrelated:** two pre-existing `cli/test/core.test.mjs` failures in `scaffold.mjs`
path-confinement reproduce on a clean checkout of `main` on macOS and are untouched here —
`scaffold.mjs` is contract-denied for this atom.

## v1.2.2 — 2026-08-07

### Why this release exists: a shipped fix that never reached anyone

`create-agentic-workspace@0.2.1` was published from `v1.2.1` (2026-08-05). **PR #75 — the fix for
step 0 leaving a reader in a non-git directory without saying so — merged the day after.** So the
defect was fixed on `main` and every stranger following the README kept hitting it, because the
published artifact predated the fix. Found by running the clean-machine install pass in a fresh
container against published materials only: the scaffolded directory was not a git repo and the
handoff never mentioned `git init`. This is the same failure shape as the v1.0.0 re-pin — the branch
was checked and called done while the published artifact was wrong — and it is the reason the
clean-machine pass exists as a control rather than a formality. `cli/package.json` is bumped to
**0.2.2** so `npm-publish.yml` actually republishes; it skips any version already on the registry.

### The fork-rewritable gate definition is closed for both metadata gates

`btb-gates.yml` runs on `pull_request`, which GitHub evaluates **from the PR's merge ref** — so a
fork author's edits to the gate file took effect in the run grading their own PR, and a fork could
rewrite `security-path` to print PASS. `spec-link` and `security-path` now live in
`.github/workflows/btb-gates-base.yml` as `spec-link-base` / `security-path-base` on
`pull_request_target`, whose definition comes from the default branch (#77, #79). Both are
checkout-free and consume only API metadata, which is what makes that trigger safe; the workflow
contains no `uses:` at all and declares `contents: read` + `pull-requests: read`.

**Not closed, and deliberately not claimed as closed:** `shell-parse-bash32` checks out fork code,
so it stays on `pull_request` and remains fork-rewritable by design — moving it would trade a
gate-definition hole for a code-execution hole. Its residual control is a practice, bounded to a
correctness failure rather than a privilege escalation. `test_the_gate_jobs_are_split_by_trigger`
asserts the split structurally (trigger per file, job set per file, no `uses:` in the privileged
workflow, least-privilege permissions) and was mutation-checked both ways.

### The release gate could not see that a tag was missing upstream

`tag_pin_coherence` proves the pin is coherent in the **local** object store; nothing hermetic could
see that the tag was absent upstream or resolved elsewhere. That blindness published twice in one
day when the F3 reset deleted tags while every local check stayed green. Adds
`published_tag_coherence` + `--verify-published`, appended to the publish plan **after both pushes**
(#76). It cannot be folded into `--verify-tag`, which by design runs before the push. Fail-closed: a
transport failure refuses rather than passing.

### Docs that were wrong on a public repo

`docs/merge-floor.md` shipped a copy-pasteable `foundry_tier_preflight` invocation naming the
pre-split contexts — an adopter following it would have configured required checks that never
report. Both it and `README.md` also still documented **`Lane: light` in the PR body** as a way to
claim the light lane; that path was removed in the tier-A hardening precisely because a PR body is
author-written, so a fork could self-declare the lane. The docs were advertising a removed bypass.
Now label-only, with the guarantee sized honestly: discretionary, but no longer *self*-asserted by
an outside contributor.

### The `source.sha` decision, recorded (#80)

Kept, and re-grounded on the vendor docs rather than on the prior framing: **`sha` outranks `ref`**
at install time, and since Claude Code v2.1.141 a deleted ref does not block an install whose sha
resolves. So the catalogue resolves at the ref and the plugin installs at the sha, which makes
"`source.sha` names the tag's first parent" structural rather than a caveat — a commit cannot contain
its own hash. Written up in `skills/cut-release/SKILL.md` § *The install pin*, including a residual
documented as **DO NOT FIX**: the installed tree carries an earlier sha in its own manifest, which is
inert and unfixable by construction.

### Also

- **Tier-A hardening** (#71): the merge-floor tier is derived from live protection state instead of a
  hardcoded literal that was true when written and false the moment the repo went public; the review
  label is bound to `(head, base)`.
- **npm attestation check** (#74): polls for registry propagation instead of failing a correct publish
  on a race.

### Security review

Every atom in this release carried a security review. #71, #77, #79 and #80 each touched
`.github/` or `skills/` and cleared `security-path` with a head-bound review label. No Blocks
outstanding.

## v1.2.1 — 2026-08-06

### `create-agentic-workspace` publishes to npm over OIDC trusted publishing

- **What this release is for.** The pre-session bootstrap CLI had never been published, so
  `npx create-agentic-workspace` — **step 0** of the documented setup path — 404'd for every reader.
  This is a patch release whose purpose is to fire `.github/workflows/npm-publish.yml` and publish
  `create-agentic-workspace@0.2.1`. The plugin content is unchanged from v1.2.0 apart from the
  publish machinery and this bump.
- **Trusted publishing, not a stored token.** The bootstrap-CLI spec's R9 obligation said
  `npm publish --provenance`, which implies a long-lived `NPM_TOKEN` in repository secrets. npm's
  OIDC trusted publishing went GA 2025-07-31 and is now the recommended path: no credential to leak
  or rotate, the repository→package binding enforced by npm rather than by whoever holds a token,
  and provenance generated automatically (the flag is redundant). R9's *intent* — an attested
  publish, verified — ships in full; its literal 2024 shape deliberately does not.
- **The attestation is verified, not inferred.** After publishing, the workflow asserts the registry
  reports a non-empty `dist.attestations` for the version just published and exits non-zero when it
  is absent. R9 named `npm audit signatures`, which verifies *installed dependencies* — this package
  has none, so that check would have been vacuous.
- **Ref-binding, twice.** npm's trusted publishing binds owner + repo + workflow + environment but
  **not a ref**. The workflow conditions the publish on `github.ref_type == 'tag'`; the
  `npm-publish` GitHub Environment independently admits `v*` tags only. Without either, a
  `workflow_dispatch` on any branch could publish unreviewed code with a *truthful* attestation.
- **Security review:** performed at spec time and again on the PR diff (2026-08-04); no Blocks. Three
  findings fixed pre-merge — `$GITHUB_OUTPUT` injection via unvalidated manifest name/version, the
  vacuous `npm audit signatures` check, and the missing ref condition.
- **The bootstrap, recorded not hidden.** npm cannot configure a trusted publisher for a package
  that does not yet exist, and has no PyPI-style pre-registration (`npm/cli#8544` is open) — so the
  first publish cannot use OIDC. The name was claimed on 2026-08-06 by a `0.0.0` placeholder
  carrying no real code, published from an **interactive passkey-authenticated session**; no
  automation token was created or stored. That placeholder is **deprecated on the registry**
  (verified). Two consequences worth stating: npm sets `latest` on a package's *first* publish
  regardless of `--tag`, so `latest` pointed at the placeholder until this release moved it; and
  `0.0.0` is permanent, so an explicit pin to it resolves an unattested artifact forever.
  Follow-up: set the package's npm publishing access to **require the trusted publisher**, which
  disables token publishes for good.

## v1.2.0 — 2026-08-04

### `--verify-tag` accepts GitHub's port-443 SSH host (fix, found by dogfooding the cut)

- **The defect, one release after its own atom shipped.** `feat-foundry-verify-tag-ssh-alias-resolution`
  taught the tag-pin coherence check to resolve ssh-config **host aliases** through `ssh -G`, then
  compared the result against the single literal `github.com`. So it resolved the alias correctly and
  then rejected the answer: an operator whose `~/.ssh/config` points at **`ssh.github.com`** — GitHub's
  own SSH endpoint on port 443, and the configuration GitHub's documentation *recommends* for networks
  that block port 22 — had every coherent release convicted as `TAG-PIN-INCOHERENT`. Found by running
  the check on this very release cut.
- **The fix** compares against a **closed two-element set** `{github.com, ssh.github.com}`, verified
  against GitHub's own "Using SSH over the HTTPS port" documentation (2026-08-04). Deliberately not a
  `.github.com` suffix match — that would admit an attacker-shaped `evil.github.com` if DNS or ssh
  config were ever hostile, and confirming the pin was verified against the repo an install actually
  fetches from is the whole purpose of the check. A negative control asserts three lookalike hosts stay
  refused, and the positive case reddens against the pre-fix comparison.

**The onboarding wave.** Twelve atoms realizing
`intake/er-onboarding-wizard-and-permission-floor.md` end to end, plus the light-lane fixes that
rode with them. The wave's organizing finding is structural: **`/foundry:init` can never scaffold
its own permission floor** — a model editing its own confinement is classifier-denied (confirmed
live). So the floor must exist *before a session does*, which makes the Claude session a **step**
rather than the entry point, and introduces a pre-session bootstrap CLI as phase 0. Every atom below
went through the factory: front-authorized, implemented on its own branch, security-reviewed where
it touched a flagged path, and merged through the floor.


### `/foundry:init` stops prescribing settings writes it cannot perform (feat-foundry-init-slimming, AC-INS-1..8)

- **The bug:** an empirical classifier sweep confirmed a model editing its own confinement is
  hard-denied — so `/foundry:init`'s `.claude/settings.json` writes (plugin/marketplace
  enablement, the git-identity jail bootstrap, the `statusLine` wiring, the native Bash sandbox
  enable) were prescribing a write an agent session can never actually perform. Every adopter who
  reached those steps met a denial where the skill promised a write.
- **The fix:** all four steps become delimited, machine-checkable **VERIFY-ONLY** regions — read
  the artifact, report the verdict, and either **REFUSE** with a pointer (where
  `[[feat-foundry-bootstrap-cli]]` actually owns the write: plugin enablement, commit identity) or
  **REPORT-ONLY** + `no shipped writer` (the four artifacts nothing ships writes: the `statusLine`/
  `subagentStatusLine` wiring, the sandbox enable, the `gh` jail's authentication, and the
  `GH_CONFIG_DIR` session-env carrier). A new closing step 14, the **hello-loop**, walks the
  operator through one toy atom end-to-end (intake → spec-review → authorize → dispatch → merge,
  with the one-keypress authorize ask explained as it fires) and cleans up after itself. A new
  `## What init verifies — and what it no longer does` section states the skill's own honest
  tiering, including that init **still writes** three artifacts
  (`.claude/foundry-operators.json`, `.foundry/stack-profile.lock`, the `.gitignore` managed
  block) — it is not "purely conversational".
- **Strictly narrowing.** Steps 1, 3, 5, 6, 7, 8, 9, 10 and 13 keep their numbers and stay
  byte-identical; `scripts/foundry-bootstrap.sh` and both statusline wrapper scripts ship
  unchanged. `docs/QUICKSTART.md` gains a `## Before your first session` section naming the
  pre-session bootstrap's writes, both `gh`-jail security caveats, and a per-artifact coverage
  table for the four artifacts nothing ships owns; `docs/troubleshooting.md` gains a matching
  symptom-first entry. New `tests/test_init_slimming.py` (region/tiering/step locks plus a
  negative control per lock class) and three new `tests/test_docs_claims.py` cases.

### Docs-truth reconciliation: an overstated enforcement claim, a misattributed env var, and the spec-path shape (feat-foundry-docs-truth-reconciliation, AC-DTR-1..5)

- **`skills/id-sync/SKILL.md` no longer claims never-force-sync is MACHINE-enforced** — nothing in
  the tree observes a `--force`/`--prune` past the realization read (`force`/`prune` do not occur in
  `foundry_realization.py`). The frontmatter `description`, the `never-force-sync-blindly` heading,
  and its anti-pattern bullet now say plainly it is **not machine-enforced**, and name what actually
  is: `derive_realization_verdict` computes a **machine-derived** verdict over evidence `id-sync`
  itself **records** — an observation + incident trigger, **not a merge block** — matching the house
  form `skills/id-promote/SKILL.md` and `skills/id-impact/SKILL.md` already ship.
- **`docs/troubleshooting.md` no longer credits `FOUNDRY_PLUGINS_DIR` with moving the plugin cache.**
  It is read in exactly one shipped file, `scripts/foundry-fleet-doctor.py`, only to choose where
  **fleet-doctor** looks up `installed_plugins.json`. The false parenthetical beside
  `rm -rf ~/.claude/plugins/cache/` is removed (the command itself is unchanged); `docs/QUICKSTART.md`
  gains the missing honest note in its "Where things live" table.
- **Eleven spec-path mentions across `README.md`, `docs/QUICKSTART.md`, and two how-to guides now
  carry the `<product>` segment** the shipped `derive_area` projection actually indexes
  (`specs/features/<product>/<domain>/<capability>/`), matching the shape `context/feat-spec-template.md`
  and `skills/intake/SKILL.md` already document.
- **`tests/test_docs_claims.py`** gains structural (never substring-in-joined-text) pins for both
  corrections plus the path shape, each with a negative control proving the check convicts a
  reintroduced claim, a de-scoped env-var mention, a shortened path, and a slice-scoping bypass —
  and an additive-only guard proving every pre-existing case in the module is still defined with a
  byte-identical body.

### `release-wave.js` honours the Workflow-runtime script contract (feat-foundry-release-wave-workflow-syntax, AC-RWS-1..4)

- **Fixed a Workflow-runtime SyntaxError.** The native Workflow runtime extracts `export const
  meta` and wraps the REMAINING script body in an async function, which makes an `export`
  **declaration** inside that body a SyntaxError (top-level `return` statements stay legal — they
  become ordinary function returns once wrapped). `workflows/release-wave.js` carried four stray
  `export function` declarations (`normalizeEvidence`, `dedupKey`, `consolidateFindings`,
  `assembleReviewResult`, inside the RFH-PURE block) alongside `export const meta`; the sibling
  `workflows/spec-audit.js` has zero and runs fine. The fix drops the `export` keyword from those
  four declarations — file-private now, called only from inside the same file — with every body,
  parameter list, comment, and in-file call site byte-identical to the merge base.
- **Two new locks in `tests/test_workflow_export_shape.py`.** A **wrapper-simulation oracle**
  reproduces the runtime's own transform (excises the meta declaration by a balanced-brace scan
  over an elided view, embeds the remainder in `(async () => { ... })`) and checks the transformed
  artifact under a real parser (`node --check --input-type=module`), asserting on exit status
  only — `workflows/spec-audit.js` is the untouched positive control, proving the oracle tests the
  export rule and not merely rejecting every workflow file. A **Node-free structural sole-export
  scan** computes the same elided view, collects every whole-word `export` token by position, and
  asserts the collected list is exactly `[export const meta]` — six negative controls (four must
  FAIL, two must PASS, including a quote-bearing regex-literal control) prove the scan is not
  vacuous. The oracle's two cases are `pytest.mark.skipif`-gated on `node` per function; the
  structural scan is never skip-gated, so it runs on a Node-less machine too.
- **`ci.yml:41`'s existing `node --check` step is unchanged and is not the fix** — it exits 0 on
  the defective file today (measured), which is exactly why the wrapper-simulation oracle exists as
  a separate, stronger lock. `tests/test_review_fanout.py`'s docstrings are reconciled to no
  longer describe the four functions as *exported* (they are file-private after this atom); its
  own assertions and RFH-PURE-block extraction are unchanged.

### The governed-repo attach flow ships: attach-existing and create-new (feat-foundry-wizard-attach-repo-flow, AC-WAF-1..9)

- **`scripts/foundry_repo_attach.py` is the write half of the registry** — the `mr register` /
  `nx import` shape, as a reusable flow module the pre-session bootstrap CLI hosts. Every field
  (source, local path, registry key, role, description, default branch) is collected in a pinned
  order, defaulted where derivable, and **confirmed before anything is written**; every field
  carries a flag twin so a fully-flagged `--yes` run completes with zero stdin reads, and a
  required value with no flag is a named refusal under `--yes` — never a silent default.
- **Validation runs before any byte is written**, decided by the **shipped**
  `schema/foundry-project.schema.json` and the **loaded** `foundry-prepublication-leak-scan.py`
  admitted-remote-form predicate — never a re-implementation — over the whole prospective document
  under a **monotonicity** rule (a pre-existing schema defect is reported, never blocking; any
  *new* error refuses). This flow's own narrowings: a floored + never-auto-transformed registry
  key, a refused password-bearing source (the manifest is a **committed** file), C0/C1 refused in
  `path` **and** `description`, confinement against every governed root (duplicate path, nested
  inside, or containing an existing repo), and a `tracked` target.
- **The preview shows the exact row, the exact gitignore bytes, and the reconcile plan** before
  the write, rendered through the registry's own sink; a declined confirmation and `--dry-run`
  each leave both files byte-identical.
- **The pair lands atomically, gitignore line first, row second** — temp-in-target-directory +
  `fsync` of file and directory, mode preserved, symlink target refused, a pre-image hash
  re-verified immediately before every rename so a concurrent writer is reported, never clobbered.
  Rollback (triggered by a reconcile failure outcome or an exception, never by a `finding` alone)
  mirrors the order, restoring the row first; a completed clone and a `gh repo create`d repository
  are the two **uncompensated** effects, always named as an orphan/undeclared checkout in the
  report, never deleted.
- **`create-new` confirms the creation first**, then runs `gh repo create` (create-then-view,
  never `--clone`), binds the row from `gh`'s **structured** `repo view --json` output rather than
  the typed argument, and falls through into the same attach path — one implementation writes the
  pair. `gh` is the only network-capable subprocess this module spawns.
- **Reconcile happens only through the sibling's imported callable** (`foundry_repo_fleet.reconcile`,
  called by identity), entered only after the pair is durably on disk, with `rows` equal to exactly
  the new row **read back from the manifest file on disk** — never the in-memory values this flow
  collected. This module executes no `git` command of its own.
- `tests/test_repo_attach.py` drives the real module over materialized `tmp_path` workspaces:
  local bare-repo remotes, a fake `gh` on PATH with captured argv and scripted `--json` output,
  fault-injected atomic-replace/TOCTOU scenarios, and byte-level before/after comparison of both
  files.

### The pre-session bootstrap CLI: `npx create-agentic-workspace` (feat-foundry-bootstrap-cli, AC-BCL-1..11)

- **`create-agentic-workspace` ships as a new, zero-dependency npm package rooted at `cli/`** — the
  ER's finding is structural: `/foundry:init` can never scaffold its own permission floor (a model
  editing its own confinement is classifier-denied), so the floor must be written **before a
  session exists**, in the operator's own terminal. `npx create-agentic-workspace` walks
  name/dir → greenfield-vs-existing → git/GitHub identity → stage mode → the permission
  conversation, previews every file **and** every capability it will declare before the first
  byte, then writes and stops.
- **It declares; it never grants.** The plugin's reviewed three-tier `docs/permission-floor.json`
  is emitted verbatim (bundled as a byte-identical mirror, `cli/permission-floor.json`) into the
  new workspace's committed `.claude/settings.json`, alongside `extraKnownMarketplaces` and
  `enabledPlugins` single-sourced from `cli/package.json`'s `foundry` pin block. The marketplace
  entry is a pinned literal (`autoUpdate: false` — the floor's rules are version-wildcarded, so
  auto-update would be a standing grant over unseen plugin versions); the written file's top-level
  key set is closed, and `statusLine`/`sandbox`/`hooks`/`apiKeyHelper`/`env`/`mcpServers`/
  `additionalDirectories` are never emitted at any nesting level. The CLI never runs `claude`,
  never accepts the workspace trust dialog, and never writes under `$HOME/.claude/`; the exit line
  explains the trust hand-off and directs the operator to `/foundry:doctor`'s permission-floor
  check afterward — the one adopter-side check that compares against the **installed plugin's own**
  copy of the map rather than the npm tarball's.
- **Absorbs `foundry-bootstrap.sh`'s out-of-session identity wiring**, proved differentially equal
  to the shipped script across four artifacts (global git config listing, the per-account include
  file's path and bytes, the repo-local `useConfigOnly` line, and the `.claude/gh-identity`
  marker). The optional `gh api user` probe is bounded (one call, timeout, its own `GH_CONFIG_DIR`
  jail, five token/host variables stripped), discards on any login mismatch, and persists no probe
  output beyond the confirmed name/email.
- **Scaffolds a closed, seven-file, schema-valid workspace seed**, and re-running is a
  **reconcile-with-drift-report** (the `terraform init` posture): a managed path that is absent is
  created, one that matches is left untouched, and one that exists and differs is reported
  `drifted` and **never overwritten** — never-clobber is unconditional, on every path and in every
  mode, including `--existing` against a foreign, hand-authored `.claude/settings.json` (reported
  drifted, never merged). The permission-drift half of the report reuses
  `[[feat-foundry-doctor-permission-floor-check]]`'s eight-class vocabulary and its `covers()`
  subsumption relation, kept in agreement with `tests/test_permission_floor_map.py::_subsumes` on
  a shared row table so the two matchers cannot diverge silently.
- **Supply-chain posture, stated honestly.** Zero third-party dependencies, `scripts` closed to
  `{test}` (no lifecycle hook of any kind), every `import`/dynamic `import()` a `node:` builtin or
  a relative path (statically decidable), and no network module anywhere in the import closure —
  the only reachable egress is the one bounded `gh` probe. Every control here is a **build-time**
  check over the source tree; it does not attest the published tarball. `tests/test_bootstrap_cli.py`
  drives the package's own `node --test` suite as a subprocess (no CI workflow edit) plus seventeen
  mutation negative controls, one per invariant class.

**Security review (PR #61) — remediation disposition.** A separate-context floor-#3 review returned
2 Blocks + 6 Risks over the new supply-chain surface. **Both Blocks fixed:**

- **B1 — the marketplace pin floated.** The emitted `extraKnownMarketplaces` entry carried
  `{source, repo}` and no `ref`, so an adopter's *first* marketplace resolution followed the default
  branch — and because the floor's allow rules are version-wildcarded
  (`cache/*/foundry/*/scripts/…`), that turned the operator's single trust acceptance into a standing
  grant over whatever code the resolution delivered. It is the same floating-pin defect
  `autoUpdate: false` guards the *later* fetches against, left open on the first, and strictly weaker
  than the pinned manual path `docs/QUICKSTART.md` already documented. The entry now carries
  `ref: v<plugin_version>`, single-sourced from the `foundry` pin block per AC-BCL-4(b) (contract
  v1.2). Worth recording *why* CI was green: three assertions pinned the **unpinned** shape, so the
  checkpoint passed precisely *because* the fix was missing, and applying it turned them red. Two new
  controls (`o`, `o2`) now redden on a dropped ref and on a wrong one.
- **B2 — a dangling symlink at a managed path escaped confinement.** `fileBytesEqual` tested with
  `existsSync`, which *follows* symlinks and so reported `false` for a dangling one; the row was
  classified `create` and `writeFileSync` followed the link, landing the write outside the
  physically-resolved target root — reachable at `$HOME/.claude/settings.json`, which no trust dialog
  gates. On that one path the CLI stopped declaring and started granting. Now `lstat`-based, so any
  existing entry (dangling symlink included) is `drifted`: reported, never written; the write itself
  additionally uses `O_EXCL`. New control `p` asserts at the outcome level that nothing appears at the
  link target — and was verified by mutation to fail against the pre-fix code.

**Risks fixed:** R1 — the `gh` binary name is a string literal again, not `env.FOUNDRY_TEST_GH_BIN || 'gh'`
(that seam let anything controlling the environment choose the spawned binary, `claude` included, making
the "child-process set is closed to `{git, gh}`" claim false; it was also dead code). R4 — the assertion
guarding "reads no plugin-cache path" was **vacuous**: a three-arm `or` whose last arm matched the
substring `map.`, which the guarded file always contains. Replaced with a structural check that
balanced-brace-extracts the cache-walking function and requires every `fs.*` call inside it to be
read-only, plus a pin on the reader set; the underlying stat calls are now `throwIfNoEntry`-guarded so a
broken symlink in the operator's plugin cache can no longer turn a completed scaffold into a stack trace.
R5 — the scaffolded `.gitignore` now ignores `/.claude/settings.local.json`, so a fresh workspace cannot
commit ungated local grants (the managed block stays byte-identical to the shipped applier; the line is
added outside it). **Residuals (recorded, not fixed):** R2 — an identity-path refusal still occurs after
`applyPlan`, so exit 1 there leaves a scaffolded tree with git identity unwired; R3 — the per-account
`gh` config jail directory is created by the parent and is not in the preview's enumerated
machine-scope writes; R6 — the `.claude/gh-identity` marker is written outside the plan/preview
machinery and overwrites a differing pre-existing marker.

### `--verify-tag` resolves ssh-config host aliases before the `source.repo` cross-check (feat-foundry-verify-tag-ssh-alias-resolution, AC-VTA-1..5)

- **The bug:** `tag_pin_coherence`'s `source.repo` cross-check stripped one of exactly three literal
  prefixes (`git@github.com:`, `https://github.com/`, `ssh://git@github.com/`) before comparing to
  the marketplace pin. An operator whose `origin` remote uses an ssh-config **host alias**
  (`git@personal-github:owner/repo` — the shape the shipped identity-isolation practice produces)
  matched none of the three, so a perfectly coherent release printed `TAG-PIN-INCOHERENT`.
- **The fix:** for an **ssh-shaped origin** (transport `ssh`, host passing a conservative
  `^[A-Za-z0-9._-]+$` charset gate), the host is resolved through `ssh -G <host>` — OpenSSH's own
  config resolver, which prints the effective configuration and exits, contacting nothing — and
  equivalence becomes *(resolved host, owner/repo path)* instead of the literal prefix strip. A
  genuinely different repository still refuses (the negative control). The `https` path is
  untouched and keeps today's case-sensitive prefix strip byte-for-byte (scoping this atom strictly
  to the ssh-alias defect, not a general case-insensitivity widening).
- **Strictly non-weakening / fail-closed.** Every resolver failure — `ssh` absent, non-zero exit,
  timeout, no `hostname` line, an empty-valued `hostname`, or a charset-violating host (never handed
  to `ssh` at all, closing an argv-injection shape by construction) — falls back to the shipped
  strict comparison. The new resolve timeout is a named module constant,
  `SSH_RESOLVE_TIMEOUT_SECONDS = 30`, mirroring the module's existing `git()` helper timeout and
  bound by reference so a test can prove the real, unmocked `subprocess.run` path actually enforces
  it against a deliberately slow fake `ssh`. A dependency-injected `resolver=` keyword-only seam on
  `tag_pin_coherence` (defaulting to the real `ssh -G` implementation) follows the module's existing
  `acceptance_fn` / `er_state_fn` / `suite_runner` convention; `main()`'s `--verify-tag` CLI path
  injects no resolver, mirroring the existing never-inject guard on the `cut_release` path.
- **No network call is added**, verified structurally over the module's parsed AST rather than over
  source-text substrings: the literal argv-head strings passed to `subprocess.run` are unchanged
  except for the one new `ssh` head, no call carries a truthy `shell=`, and no network-capable
  symbol (`socket`, `urllib`, `http`, `requests`, `ssl`, `asyncio.open_connection`) is referenced.
- **`tests/test_tag_pin_coherence.py`** carries the first coverage of the `source.repo` cross-check
  in either direction (it had zero coverage before this atom), including the real, unmocked `ssh -G`
  timeout case and a `HOME`-scoped `~/.ssh/config` end-to-end case that reports NOT-RUN rather than
  passing vacuously when the local OpenSSH build or environment cannot support it.

### `/foundry:doctor` gains a permission-floor drift check (feat-foundry-doctor-permission-floor-check, AC-DPF-1..8)

- **The doctor's new `permission-floor` probe** (the seventh probe, taking the shipped tree from
  six to seven) compares the workspace's EFFECTIVE permission configuration — the union of
  `permissions.{allow,ask,deny}` read from BOTH `.claude/settings.json` **and**
  `.claude/settings.local.json`, origin-tracked — against the shipped `docs/permission-floor.json`
  and reports drift in eight ranked classes: `blanket-allow`, `ask-shadowed-ceremony`,
  `ask-shadowed`, `deny-missing`, `settings-unreadable`, `stale-plugin-path`, and the informational
  `allow-absent` / `unclassified`. This discharges R6 of the permission-floor map: the harness's
  ask-to-allow persist option writes an `allow` into `.claude/settings.local.json` with no second
  trust dialog, so the **permission-floor probe** is what notices when a front-authorization
  ceremony has been silently shadowed.
- **Report-only, never auto-fixed, and never RED on a mismatch** — a mismatch is a new fourth
  doctor outcome, `advisory`, distinct from `ok`/`skip`/failure. RED (the only outcome that fails
  the operator-invoked run) fires only on a schema-invalid `docs/permission-floor.json`.
  `--session-start` stays fail-open and now also prints its `WARNING:` banner on an advisory-only
  run, filtered to the actionable finding classes plus one informational count line.
- **Read-only and non-disclosing.** `scripts/foundry_permission_floor.py` is a pure comparison
  module: only `permissions.{allow,ask,deny}` are ever parsed, retained, or emitted; every other
  settings key (`env`, `apiKeyHelper`, etc.) is untouched. An unreducible (`unclassified`) rule is
  reported by tool-name prefix, origin file, and count — never by its body.
- **`tests/test_permission_floor_check.py`** is the live seam: a materialized negative-control
  fixture per finding class (including the live workspace's own shadow spelling), a fail-open
  control per failure mode, and a cross-atom conformance table proving this module's `covers()`
  agrees with the sibling map suite's `_subsumes()` on a shared table of rules.

**Security review (PR #60) — remediation disposition.** A separate-context review pass returned
0 Blocks + 9 Risks. **Applied:** R1 — the `blanket-allow` finding line now renders the
folded/canonicalized rule, not the raw settings-file text, closing a path where an arbitrary
free-text prefix ahead of the plugins/cache segment (which the fold otherwise discards) would
have been emitted verbatim; R2 — `_tool_prefix` no longer falls back to the entire rule body when
a rule carries no `(`, so a paren-less secret-shaped rule (an AWS-key-ID-shaped bare token, say)
can no longer be reported as a "tool prefix" — it renders `?` — and `_TOOL_PREFIX_VALID_RE`'s `$`
anchor is now `\Z` so a trailing newline can't smuggle a body past the validator; R3 — the
doctor's three probe-exception detail strings (module-unimportable, floor-malformed, probe-crashed)
now pass through a length-cap + control/ANSI-neutralize floor before they're returned, mirroring
the AC-ROST-5 pattern already applied to every settings-derived string; R4 — the zero-width/bidi
render floor is widened, superset-only, to also neutralize the Arabic Letter Mark (U+061C) and the
Unicode line/paragraph separators (U+2028/U+2029), leaving AC-DPF-8's own enumerated set intact;
R9 — the `ghp_`-prefixed test fixture is shortened to 20 characters after the prefix so it stays
credential-shaped without exact-matching the `ghp_[0-9A-Za-z]{36}` GitHub-PAT scanner rule (which
would otherwise fire gitleaks/trufflehog and this repo's own prepublication leak scan at the
GO-PUBLIC flip); the other credential-shaped fixtures the review named (`sk-…` bodies) were
checked and do not exact-match a canonical scanner pattern (none are the 48-char OpenAI-key
length), so they are unchanged. **Residuals (accepted, not fixed):** R5 — the session-start
cadence's fail-open guarantee rests on each probe's individually enumerated `try/except` rather
than one structural top-level `try/except` wrapping the whole render; R6 — the first import of
`foundry_permission_floor`/`foundry-doctor` writes a `__pycache__` bytecode file into the plugin
tree, a pre-existing shape of the lazy-import loader this atom reuses, not introduced by it; R7 —
the glob-expansion cap (`_GLOB_EXPANSION_CAP = 256`) bounds the reported output, not the `glob.glob`
work itself — a plugin-cache tree with a very large fan-out still pays the full expansion cost
before the cap is applied; R8 — the `--session-start` payload is bounded per-class (50 lines) and
per-line (200 chars) but not by total bytes, so a maximally adversarial settings file could still
push the payload to roughly 60 KB; a tighter total-byte budget is deferred. None of the residuals
are exploitable beyond a denial-of-legibility/availability nuisance at the advisory-only,
fail-open probe this already is.

### The plugin now ships a reviewed permission-floor declaration (feat-foundry-permission-floor-map, AC-PFM-1..7)

- **`docs/permission-floor.json` is the canonical three-tier allow/ask/deny map** of every command
  shape the plugin's own workflow instructs — one `{rule, tier, rationale}` entry per invocable
  `scripts/` shape (`*.py` plus every owner-executable `*.sh`/extensionless file), closed-world
  complete and verified at test time against the shipped tree so a new un-tiered script fails
  closed. The ceremonies (`foundry-authorize.py`, `foundry-decommission.py record`/`gate-check`,
  `foundry_release.py accept`, `foundry-upstream-submit.py`, `foundry-cut-release.py`,
  `foundry-project-sync.py`, `foundry_tier_preflight.py`, `foundry-doctor.py --heal`,
  `foundry-stack-profile.py --relock`, `foundry-bootstrap.sh`, `claude plugin tag`) are pinned
  `ask`; the absolute anti-patterns (`gh pr merge --admin`, `git push --force`,
  `tofu destroy -auto-approve`, `docker system prune`) are pinned `deny`.
- **The map grants nothing by itself.** It is inert data — no hook, gate, authorization path, or
  runtime reads it in this atom. Consent stays at the platform's workspace-trust dialog. The `generated_for_plugin_version` updates at each release cut alongside the two manifests (convention; the field ships equal to the cut version).
  pre-session bootstrap CLI that applies it and the doctor drift check that watches it are
  separate, out-of-scope atoms.
- **`tests/test_permission_floor_map.py`** derives its ground truth from the shipped tree at run
  time, asserts schema well-formedness, closed-world coverage (with reverse-direction and
  not-commanded truth checks), the pinned ceremony/anti-pattern rules, rule syntax, and the
  no-silent-subsumption rule across tiers — with five materialized negative-control fixtures
  proving each fail-closed direction actually fires.

### Harness-denial fallback discipline across the seven ceremony-instructing skills (feat-foundry-gate-denial-fallback, AC-GDF-1..5)

- **When the harness denies a ceremony command, the model now has an instruction, not a guess.**
  `docs/harness-denial-fallback.md` ships one canonical, delimited clause with three limbs: **(a)**
  hand the denied invocation back byte-identical (modulo the leading in-session `!`), never
  freeform-composed and never lifted from a spec or PR body, naming any override/exception flag
  (`--yes`, `--skip-audit-reason`, `--reauth-after-impl`, `--admin`, `-auto-approve`) in plain
  language above the block; **(b)** STOP — never retry the call, never route around it via another
  tool or credential, explicitly excluding a verb's own documented degraded path
  (`UPSTREAM-SUBMIT-LABEL-DEGRADED`, `cut-release`'s `REFUSED`/`GATED`); **(c)** name the durable
  fix — `.claude/settings.json` and the native trust dialog, since chat confirmation is never a
  consent channel.
- **Single-sourced, pointer-checked.** Each of the seven ceremony-instructing skills
  (`authorize`, `authorize-release`, `cut-release`, `decommission-gate`, `release`,
  `upstream-submit`, `id-apply`) carries a one-line pointer (path + `STOP` + a
  harness/permission-denial trigger word) rather than a copy, so voices and lengths stay native to
  each skill while a three-way set equality (enumerated seven ⟷ the clause's own roster ⟷ the
  on-disk pointers) convicts a half-done addition.
- **No un-negated retry instruction survives the checked text.** A sentence-scoped negation check
  over the delimited region plus every pointer line fails the build if any retry/route-around/
  bypass wording appears without a `never`/`do not`/`must not` earlier in its own sentence — with a
  single named exemption for the `**Resuming after a real grant.**` paragraph, where the accurate
  rule (re-running is correct once state changed through a real consent channel) is expressible.
- `tests/test_gate_denial_fallback.py` + `tests/support_gate_denial_fallback.py` assert AC-GDF-1..3
  over the real tree and run a 5-case mutation negative control (`pointer-removed`, `limb-dropped`,
  `limb-a-literal-dropped`, `retry-instruction`, `enumeration-desynced`) proving the suite is not
  unconditionally green.

**Security review:** not flagged — prose-only atom (a doc + skill-instruction text + two new test
files); `hooks/**`, `scripts/**`, `schema/**` and `.github/workflows/**` are contract-denied, so no
gate decision, permission rule, hook, or CI check is touched. The discipline reinforces the harness
denial (limb (b) forbids verbatim retry and tool substitution) rather than working around it.

### The governed-repo registry is formalized: a tightened manifest schema + a read-only registry-integrity report (feat-foundry-repo-registry-formalization, AC-RRF-1..7)

- **`.claude/foundry-project.json` `repos{}` now matches the repo/meta/vcstool manifest
  consensus.** `schema/foundry-project.schema.json` requires `path` on every `repos.<key>`
  record (a pathless record used to validate, then fail only at dispatch), and adds four
  optional fields — `remote`, `default_branch`, `role` (closed to `product`/`handbook`/
  `infra`/`app`/`workspace`), `description` — each carrying its own non-empty schema
  description, since this atom ships no separate how-to prose. Everything else stays
  additive: no other `required` key, `additionalProperties: true` unchanged at all three
  levels, `packages.<key>.role` still an unconstrained string. A JSON-Schema shape floor
  (no leading `-`, no C0/C1 control character) guards `path`/`remote`/`default_branch`
  against the one place they reach an argv position and a terminal — never a URL validator.
  The tightening reaches `scripts/foundry-config.py check`/`adopt` with **no code change** to
  that script, which remains the schema's sole consumer.
- **New: `scripts/foundry_repo_registry.py`, a read-only registry-integrity report.** Per
  `repos.<key>` entry it answers, and never repairs: is the path `present`, `not-cloned`
  (declared `remote`, no checkout yet) or `dangling` (no `remote`, nothing there) — the
  headline gap this atom closes, since today both absent-path cases look identical; is it
  paired with a root-anchored `.gitignore` rule via `git check-ignore -v --no-index`, and is
  it already swept into the control plane's own index via `git ls-files` (`tracked` outranks
  every other pairing state); and does the checkout's `origin` match the declared `remote`,
  read via `git config --get remote.origin.url` — the raw, unrewritten value, never `git
  remote get-url`, which would let a checkout's own `url.*.insteadOf` forge a match. Path
  resolution is physical (symlinks followed), mirroring `scripts/foundry-wt`'s `cd && pwd -P`
  confinement. `--json` emits a `{degraded, degraded_reason, rows}` envelope for Wave-2 verbs
  to consume; a manifest with no `repos{}` is the named `no-repos` outcome (exit 2), never a
  silent clean exit. The exit code is advisory (tri-state, `terraform plan
  -detailed-exitcode`-style) and consumed by no gate. Every emitted field — repo key, remote,
  discovered origin, paths, remedy text — passes through one sanitizing sink at stdout/
  stderr/`--json`/exception-path alike: userinfo redacted to a fixed `***`, every C0/C1
  control character and ANSI CSI escape neutralized.
- **The `role` closed set is this atom's one non-additive element**, order-pinned behind the
  named prerequisite atom `handbook-manifest-role-migration` (moves the handbook's prose
  `role` values into the new `description` field first) — see the spec's Residual R1.
  `scripts/foundry-doctor.py` and the sibling validator `feat-foundry-control-plane-preflight`
  are untouched; this atom surfaces drift, it never convicts it.

**Security review:** not flagged — the report is read-only (writes nothing, fetches nothing,
edits no `.gitignore`/manifest/git state), every git invocation is a fixed, `--`-guarded argv
drawn from a closed config-read-only plumbing set, and the credential-bearing surface (a
remote URL's userinfo) is redacted at a single emission sink covering every output channel
including the exception path (spec AC-RRF-7).

### The multi-repo control-plane how-to is complete, and its enforcement claims are derived (feat-foundry-control-plane-docs, AC-CPD-1..4)

- **`docs/how-to/multi-repo-control-plane.md`** now carries the full pattern in six placed
  sections: gitignored-siblings-never-submodules, the `repos{}` registry (an example validated
  against the shipped `schema/foundry-project.schema.json`, with the fields no shipped code
  reads marked inert inline), the gitignore⟷registry pairing rule and clone-before-register
  ordering (both failure directions — dangle and spill), the session rule (`--add-dir`, the
  session's blast radius, and the `permissions.deny` remedy), and the `target_repo` ⟷
  build-provenance binding.
- **A closed, honestly-derived `enforced` roster replaces prose promises.** Every
  `machine-enforced` / `not-enforced-today` / `practice` label is derived by exercising the
  shipped code, never asserted from memory: the hook's live `bind-check` invocation, the
  authorize seam's degrade-and-freeze-anyway behaviour (executed against a hermetic fixture
  workspace), the doctor's `repos{}` validation (now machine-enforced —
  `feat-foundry-control-plane-preflight` landed first, per the merge-order precondition), and
  `target_repo`'s place inside the hash-covered contract region. A pinned semantic-class
  denylist convicts any restatement of `SETUP.md`'s inaccurate "fails closed at authorization"
  claim.
- **`tests/test_doc_claims.py`** gains four new `COVERED_CLAIMS` entries — one per mechanism,
  each with its own `make_mutated` and negative-control conviction — plus the closed-roster and
  practice-label-co-occurrence assertions. **`tests/test_docs_claims.py`** gains the structural
  presence-and-linking + section-placement lock.

### `/foundry:repos` — the governed-repo fleet verbs over the registry (feat-foundry-workspace-repo-verbs, AC-WRV-1..12)

- **The control plane's repos{} registry now has verbs that act, not just report.**
  `scripts/foundry_repo_fleet.py` ships `sync` (idempotent reconcile: clone every `not-cloned` row
  that declares a `remote`, fetch every `present` row whose `origin` is exactly `match`, report
  everything else untouched), `status` (one line per entry: present · origin · branch ·
  ahead/behind · dirty), `foreach` (shell-free argv fan-out over the present repos,
  fail-collecting, child output captured and sanitized per line), and `validate` (the manifest ⟷
  reality ⟷ gitignore round trip in **both** directions — including the reverse direction,
  `undeclared-checkout`, that `feat-foundry-repo-registry-formalization` deferred by name).
- **Clone and fetch are the entire mutation vocabulary.** No checkout, reset, merge, rebase, pull,
  push, clean, stash, branch, remote or submodule command, and no `--force`/`--force-sync`, exists
  in this tool — an existing checkout is never rewritten; drift is surfaced, never fixed. Every git
  child, network-capable or not, carries the corpus's reviewed hardening set (`credential.helper=`,
  `core.askPass=` with the askpass env removed, `core.fsmonitor=`,
  `core.sshCommand=ssh -o BatchMode=yes` with `GIT_SSH_COMMAND` removed, `protocol.allow=never`
  admitting only https/ssh/file, submodule recursion off) under the subtractive sink environment —
  verified by absent observable side effects (a planted hostile `core.fsmonitor`/`core.sshCommand`
  does not fire), per the standard `feat-foundry-leak-scan-ls-remote-sink` shipped.
- **The boundary re-validates every row before any socket opens.** The admitted-remote-form
  predicate is *loaded* from `scripts/foundry-prepublication-leak-scan.py`
  (`url_is_allowed_form`/`_is_local_path_escape_hatch`) rather than re-implemented; the fetch
  invocation names the **declared** remote, never the checkout's configured origin; and the
  reconcile logic is exposed as an importable callable (`reconcile(root, rows, *, timeout=None)`)
  the Wave-3 wizard's attach flow will call through the same code path.
- **New skill `/foundry:repos`**, disambiguated from `/foundry:fleet` (the session roster, which
  governs no repository). No promise that a cloned tree is inert — a cloned repo's `CLAUDE.md`,
  `.claude/**` and `.mcp.json` become discoverable configuration inside a Claude Code root, stated
  in both `--help` and the skill.

**Security review:** the review confirmed the boundary re-validation is the sole runtime control
(the schema shape floor is advisory only), the hardening set + sink environment are proven by
absent side effects, submodule recursion is off on clone and fetch, and every emitted string
(including captured `foreach` child output) passes through the registry module's single sink.

**Security review (PR #59) — remediation disposition.** A separate-context review pass returned
1 Block + 9 Risks. **Block B1** — `skills/repos/SKILL.md`'s blanket "every git child is
hardened" claim overstated the boundary — is **fixed**: qualified to "every git child this tool
itself invokes", with a stated residual that `foreach` children run under the ambient environment
minus only `GIT_DIR`/`GIT_WORK_TREE` (AC-WRV-5, as authorized) and get **no** `-c` hardening at
all, so a governed checkout's own `.git/config` can direct a `foreach -- git …` child; approving
that at the ask tier is trusting the checkouts' own configs, not this tool's hardening. **Applied:**
R3 — `sync`'s fetch leg now refuses (zero git spawns) a `present`/`match` row whose resolved path
carries no `.git` entry, re-derived at the boundary independently of the row's own claim; R6 —
`sync --timeout` now defaults to a declared `DEFAULT_SYNC_TIMEOUT_SECONDS = 600.0` rather than
unbounded, `--timeout` still overrides (the AC-WRV-10-pinned `reconcile()` callable's own
`timeout=None` default is untouched); R9 — the per-row `except` handler's fallback
`declared_remote` lookup is now `isinstance(row, dict)`-guarded like its neighbors; R1 —
`_sink_env()` now also removes `GIT_ALLOW_PROTOCOL` as an explicit additional over-removal (the
spec-pinned `SINK_ENV_REMOVED_VARS` tuple itself is unchanged, pending a Terminology amendment);
R8 — the module's own `sys.path` bootstrap is now a guarded, idempotent `append` rather than an
unconditional `insert(0, …)`; R4 — `--end-of-options` now guards the revision-range positional on
`git rev-list --left-right --count`, verified locally (git 2.43.0) to leave its output
byte-identical. The sibling `git rev-parse --abbrev-ref <branch>@{upstream}` call is **left
unguarded**: the same treatment there makes `rev-parse` echo a spurious `--end-of-options` line
ahead of the resolved ref in this git's non-`--verify` mode, corrupting the parsed upstream value
— a functional rejection, recorded as a residual rather than mis-applied. **Residuals recorded**
(spec-amendment / follow-on-atom territory, not implemented here): R2 (`protocol.file.allow=always`
→ `user` — the 11-entry hardening set is spec-pinned member-for-member); R5 (AC-WRV-11's
`core.sshCommand` side-effect plant plus the vacuous `foreach` leg of the same marker run); R7
(`envelope()`'s double-sanitize path escapes a preserved newline to the literal `\x0a` — an
assert-on-wrong-layer risk); B1's mechanical half (spawning `foreach` children under `_sink_env()`
needs its own AC-WRV-5 spec amendment before it can change — today's unhardened `foreach` children
are what the authorized contract specifies); and the `git rev-parse …@{upstream}` leg of R4 above.


## v1.1.0 — 2026-08-02

### The project's own `boot_command` now wins certification's boot-recipe resolution (feat-foundry-boot-recipe-precedence, AC-BRP-1..8)

- **`certify-local` is reachable from a clean install.** `repos.<key>.boot_command` in
  `.claude/foundry-project.json` — already accepted by the schema but never read — is now the
  **first-precedence** boot recipe: declare it and certification boots from it directly, no
  `.foundry/stack-profile.lock` required. The active stack profile's
  `app_exercise_binding.boot` remains the fallback, byte-for-byte unchanged, when the project
  declares nothing usable. The precedence key follows the release's resolved venue exactly the
  way `foundry_release._resolve_repo` resolves it: the literal `workspace` key for the
  merge-gate sentinel / single-repo self-host default, `self_host_code_repo` when set, or the
  explicit `target_repo` key.
- **A malformed manifest degrades, never breaks.** An unreadable/invalid
  `.claude/foundry-project.json` falls through to the profile path instead of raising, but is
  reported on its own line — distinct from "declared nothing" — so a typo'd `boot_command` is
  never silently indistinguishable from an absent one.
- **The refusal names only reachable remedies.** With neither source yielding a recipe,
  `certify-local` always names declaring `boot_command` (the one an adopter can always act on),
  and names "activate a different stack profile" only when a lock already exists — until the
  sibling atom (`feat-foundry-stack-profile-lock-create`) ships, nothing creates a lock, so
  naming that remedy unconditionally would send the reader nowhere.
- `docs/troubleshooting.md`'s "No boot recipe" entry is reconciled to the shipped precedence,
  and its v1 known-limitation note is narrowed (not deleted): the profile path itself still
  requires a lock nothing yet creates.

**Security review:** not flagged — no auth, secrets, or supply-chain path in scope (this changes
which already-executed declaration is consulted first, not whether a command is executed).

### Stack-profile lock creation (feat-foundry-stack-profile-lock-create, AC-SPLC-1..8)

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

**Security review:** floor holds (2026-08-02 pass over PRs 50-53) — trusted-resolve guardrails shared with relock; validate-before-write; TOFU pack-trust residual recorded.

- **Control-plane preflight.** `/foundry:doctor` gains a sixth probe, `control-plane`: it catches
  a session started in the wrong root — rooted directly IN a repo an ancestor
  `.claude/foundry-project.json` already names as hosted (`repos{}`), rooted BELOW a control
  plane without being its root, or carrying a dangling `repos{}` path in its own manifest. The
  bounded ancestor walk (`scripts/foundry_control_plane.py`, new — shared by the doctor AND
  `/foundry:init`, which now runs it as its scripted first step, before any write) deliberately
  crosses ancestor `.git` and filesystem-mount boundaries rather than stopping at them, since the
  hosted repo it must see past always carries its own `.git`. This is a MISTAKE-CATCHER for the
  operator, not a floor: `--session-start` still fails open (a warning only), and the
  operator-invoked exit code is the only enforcement — see
  `specs/features/foundry/adoption/control-plane-preflight/feat-foundry-control-plane-preflight.md`
  for the full contract, including the narrow residual (unreachable only when the plugin was
  enabled strictly per-project, never the common user-wide install).

- **Substance gate no longer counts synthetic local-command records as user turns; the reflection
  cadence wording is now honest.** The Stop-hook substance gate's limb (c) ("genuine user turns")
  was counting Claude Code's own local-slash-command transcript records (`<command-name>…`,
  `<local-command-stdout>…`) as real turns — they carry `type: "user"` but no `isMeta` flag, so a
  session opened with `/model`, `/config`, `/help`, … pre-loaded 2 phantom turns and the
  once-per-session reflection fired after the operator's very first real message, presenting as a
  session-*start* error box rather than the intended mid-session checkpoint. The classifier now
  applies an ordered two-signal rule: a structural human-authorship signal
  (`origin.kind == "human"` or a `promptSource` field) counts and DOMINATES; otherwise a
  leading-tag match on one of the five local-command record shapes excludes; otherwise the entry
  counts, exactly as before — an unrecognized or older transcript shape still fails toward
  injecting (the substance gate's one-directional conservatism is unchanged). Separately, the
  reflection's cadence wording was dishonest ("Before this session ends…" / "routine
  end-of-session learnings capture") for a `Stop` hook that cannot know which idle is the session's
  last — every emitter (the primary JSON path and the python-failure fallback) now says plainly
  that the reflection runs **once per session, at the first qualifying idle**.
  (`feat-foundry-learnings-substance-gate-synthetic-turns`, auth_seq=1)

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
