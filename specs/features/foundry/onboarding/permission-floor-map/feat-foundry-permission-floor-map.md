# Foundry ships its canonical permission floor as one reviewed data file (feat-foundry-permission-floor-map)

> **Human-readable intent.** The 2026-08-01 classifier-exposure sweep (ER
> `intake/er-onboarding-wizard-and-permission-floor.md`) found that **the shipped plugin writes no permission
> configuration at all**, so a foundry user meets the auto-mode classifier one denial at a time — and the
> in-session `/foundry:init` cannot fix it, because a model editing its own confinement is itself a blocked
> shape (confirmed live: the `.claude/settings.json` write was denied). The floor must therefore be
> *declared* by the plugin and *applied* before a session exists.
>
> **This atom ships the declaration only.** One reviewed data file, `docs/permission-floor.json`, carries the
> three-tier map of every command shape the plugin's workflow instructs — **allow** (the advisory/read-only
> scripts, the state-writers, the dev loop, the read-only infra CLI forms), **ask** (the ceremonies:
> authorize, decommission `record`/`gate-check`, release `accept`, upstream-submit, `claude plugin tag`,
> cut-release, project-sync, tier-preflight, the two pinned write flags, the bootstrap prelude), **deny**
> (the absolute anti-patterns: `gh pr merge --admin`, force-push, `tofu destroy -auto-approve`,
> `docker system prune`) — one entry per command shape, each `{rule, tier, rationale}`.
> The consumers are out of scope here and named in the ER: the pre-session bootstrap CLI (ER atom 1) writes
> the workspace settings from this map, and the doctor drift check (ER atom 4) watches it. This atom ships
> **the map plus the test that keeps it complete, syntactically valid, and honest.**

## Prior art / industry grounding

- **Policy-as-committed-data is the platform's own model.** Claude Code's permission system is already a
  declarative `permissions.{allow,ask,deny}` document in `settings.json` — so the floor's natural shape is
  *data*, not code: one reviewed file, many consumers, reviewed as a diff. This is the standard
  policy-as-data pattern (OPA/conftest rule bundles, `CODEOWNERS`, `.gitignore`) that this workspace already
  runs for version pinning (`docs/standing-versions.lock.yaml`). No novel machinery is introduced.
- **A plugin cannot ship permissions — the consent gate is the platform's.** Verified against the Claude Code
  permissions/plugins docs during the ER sweep: committed project **`allow`** rules take effect **only after
  the user accepts the workspace trust dialog, which lists exactly those rules**, while `deny`/`ask` (which
  only restrict) apply immediately. There is no plugin-side grant. Hence: the plugin declares, the workspace
  applies, the platform's dialog takes consent. This is the VS Code **Workspace Trust** manifest pattern and
  Claude Code's own plugin "will install" prompt — scaffolders *declare*; they never self-grant.
- **Bash permission rules are prefix matches, and string matching is fragile.** The permissions doc's own
  caveat, already recorded in `[[feat-foundry-native-bash-sandbox]]`. Three consequences are designed in, not
  papered over: an `ask` rule cannot key on a non-leading flag (so the ask is taken at the coarser, fail-safe
  granularity); a broad prefix rule silently **subsumes** every narrower rule under it, so a split tier is
  expressible only through reach-disjoint forms (AC-PFM-6); and the `deny` tier is **belt-and-braces over the
  hook-enforced floor**, never the control.
- **The version-wildcarded plugin path** is the shape `[[feat-foundry-init-statusline-wrapper]]` proved for
  the cache layout (`~/.claude/plugins/cache/<marketplace>/foundry/<version>/`, read off
  `scripts/foundry-statusline-wrapper.sh` line 26) — carried here so a rule survives a plugin upgrade. That
  atom's glob is a *shell* glob; see R1.

## Security posture

**The map grants nothing by itself.** It is inert data in the plugin tree: no hook, gate, authorization path,
or runtime reads it today, and this atom wires none. Consent remains where the platform puts it — the
workspace trust dialog, which lists the rules it is granting. Nothing here bypasses, pre-accepts, or
substitutes for that dialog.

**Declaring `foundry-authorize.py` at `ask` is a deliberate change to floor #1's *harness* posture, and is
stated here as such rather than framed as a neutral transcription.** Today `foundry-authorize.py … --yes` is
**hard-refused by the auto-mode classifier** (confirmed empirically 2026-08-01: the dry-run runs, adding
`--yes` is denied) — an agent simply cannot self-authorize. Once a workspace applies an `ask` rule for that
script and the operator accepts the trust dialog, the same command becomes reachable behind **one keypress**.
That is the ER's explicit decision, quoted: *"A native one-keypress prompt IS the operator signature the trust
model wants; a deny would make even legitimate operator-driven runs impossible"* (ER class C). The floor's
**substance** is unchanged — the signature is still the operator's, and `authorize` still refuses a spec that
fails its own checks — but the **cost of a wrong keypress falls**, and the operator should confirm that trade
knowingly at the authorize gate rather than discover it later. R6 records the decay path that makes this
sharper.

The contract denies `hooks/**`, `scripts/foundry-authorize.py`, `scripts/foundry_authz.py`, `schema/**`, and
`.github/workflows/**`, so no gate wiring changes: **no wiring re-pin, no meta-regress, no new exec seam.**
Two deliberate asymmetries: the **ask** tier is coarsened wherever prefix-only matching cannot isolate a
non-leading flag (`--yes`, `--skip-audit-reason`, `--create`) — coarsening yields *more* prompts, never
fewer, which is the fail-safe direction; and the **deny** tier is a declared second line behind the hooks
that actually enforce force-push and destructive-op refusal (R3), claimed as belt-and-braces and never as
coverage. The file names no credential, host, token, or account; introducing one would be a review finding.
R7 records that this atom's own diff does **not** route to the floor-#3 security-path CI check, and why that
is a tracked follow-on rather than a change made here.

<!-- normative -->
## Acceptance criteria

- **AC-PFM-1** *(Invariant — ubiquitous)* **(the map is a well-formed, provenance-carrying three-tier data
  file):** The plugin SHALL ship `docs/permission-floor.json` as a JSON object with exactly the top-level keys
  `schema_version` (integer `1`), `plugin_root_glob` (whose value SHALL be exactly the string
  `~/.claude/plugins/cache/*/foundry/*`), `generated_for_plugin_version` (a non-empty single-line string
  naming the plugin version the map was authored against), `entries` (non-empty array), and `not_invoked`
  (array). Every `entries` element SHALL be an object with exactly the keys `rule`, `tier`, `rationale`,
  where `rule` and `rationale` are non-empty single-line strings (no newline, no leading/trailing
  whitespace) and `tier` is a member of the closed set `allow` | `ask` | `deny` (exact lowercase; no other
  value, no absent value, no null). Every `rule` value SHALL be unique across `entries`. Every `not_invoked`
  element SHALL be an object with exactly the keys `script` (a bare basename, no `/`) and `rationale`
  (non-empty, single-line), and every `not_invoked[].script` value SHALL be unique across `not_invoked`.
  *(Checkpoints: pytest `-k map_schema_is_wellformed`, `-k plugin_root_glob_is_the_pinned_cache_shape`.)*

- **AC-PFM-2** *(Invariant — ubiquitous)* **(closed-world catalogue coverage — a new un-tiered invocable
  script fails):** Ground truth SHALL be derived **at test time from the shipped tree** as the non-recursive
  set of basenames of files directly under `scripts/` that are invocable shapes: every `*.py` file, plus
  every other regular file carrying the owner-execute bit (today the seven `*.sh` files and the extensionless
  `foundry-wt`). A file that is neither `*.py` nor owner-executable is data, not a command, and is outside
  the closed world (today `foundry-runtime.gitignore`, `foundry_floor_controls.json`). Against that ground
  truth the map SHALL satisfy, for every basename `B`: exactly one of (a) `B` is **tiered** — at least one
  `entries` element whose `rule` contains the literal `scripts/` immediately followed by `B` and then by a
  rule delimiter (`:`, a space, or the rule body's end), so a basename can never be matched as the prefix of
  a longer one — or (b) `B` is **excluded**: `B` appears as a `not_invoked[].script`. Never both, never
  neither. Where `B` is tiered by more than one entry, those entries SHALL either all carry the same `tier`
  **or** be pairwise reach-disjoint in the sense of AC-PFM-6, so reading `B`'s tier never requires
  rule-precedence reasoning. Two reverse-direction obligations SHALL hold: every `not_invoked[].script`, and
  every basename named after `scripts/` inside a `rule`, SHALL correspond to a file present in the shipped
  tree (no dangling entry survives a script's deletion or rename); and no `not_invoked[].script` basename
  SHALL appear in **command position** in any `skills/**/SKILL.md` fenced code block or any non-comment line
  of `hooks/**` — where command position means the first word of a command (allowing an optional leading
  interpreter word `bash`/`sh`/`python`/`python3` and an optional directory prefix), commands being
  delimited by line start, `|`, `&&`, `||`, `;`, `$(`, or a backtick. A basename occurring only as an
  argument (an `install`/`cp` source path) or only in prose is not in command position and does not violate
  this. *(Checkpoints: `-k every_script_is_tiered_exactly_once`, `-k tiers_per_script_are_coherent`,
  `-k no_dangling_map_entries`, `-k not_invoked_scripts_are_never_commanded`.)*

- **AC-PFM-3** *(Invariant — ubiquitous)* **(the ceremonies and the absolute anti-patterns are pinned
  verbatim):** Writing `G` for the literal `~/.claude/plugins/cache/*/foundry/*`, `entries` SHALL contain,
  each as an exact string match with `tier: "ask"`, the rules `Bash(G/scripts/foundry-authorize.py:*)`,
  `Bash(G/scripts/foundry-decommission.py record:*)`, `Bash(G/scripts/foundry-decommission.py gate-check:*)`,
  `Bash(G/scripts/foundry_release.py accept:*)`, `Bash(G/scripts/foundry-upstream-submit.py:*)`,
  `Bash(G/scripts/foundry-cut-release.py:*)`, `Bash(G/scripts/foundry-project-sync.py:*)`,
  `Bash(G/scripts/foundry_tier_preflight.py:*)`, `Bash(G/scripts/foundry-doctor.py --heal:*)`,
  `Bash(G/scripts/foundry-stack-profile.py --relock:*)`, `Bash(G/scripts/foundry-bootstrap.sh:*)`, and
  `Bash(claude plugin tag:*)`; and, each with `tier: "deny"`, the rules `Bash(gh pr merge --admin:*)`,
  `Bash(git push --force:*)`, `Bash(tofu destroy -auto-approve:*)`, and `Bash(docker system prune:*)`. Each
  rule pinned in this criterion SHALL appear in `entries` exactly once, carrying exactly the tier named for
  it here. Additionally, every `entries` element whose rule body begins with the literal `git push` SHALL
  carry at least one further literal token after `push` (a flag, remote, or refspec form) before the body's
  end, so a bare `Bash(git push:*)` — which would swallow the cut-release PUBLISH ceremony's tag and
  default-branch pushes (R8) — cannot appear at any tier. *(Checkpoint: `-k ceremony_and_denial_rules_are_pinned`.)*

- **AC-PFM-4** *(Invariant — ubiquitous)* **(every rule is a syntactically valid, prefix-anchored `Bash()`
  rule):** Every `entries[].rule` SHALL match `^Bash\([^()]+\)$` — exactly one opening and one closing
  parenthesis, at the string's ends, with no nested parenthesis — and its body SHALL carry no leading or
  trailing whitespace. Every body SHALL be **prefix-anchored**: either a literal exact command string, or a
  string whose final two characters are `:*`. The character `*` SHALL occur in a body **only** (i) within the
  leading `plugin_root_glob` prefix, or (ii) as that final `:*` — no mid-argument wildcard, which the
  platform's prefix matching would silently never match. Every rule naming a plugin script SHALL begin with
  the file's own declared `plugin_root_glob` value followed by `/scripts/`, so the cache-layout prefix is
  single-sourced in one field. *(Checkpoint: `-k rules_are_prefix_anchored_bash_rules`.)*

- **AC-PFM-5** *(Requirement — event)* **(the validation test is the live seam, with a negative control per
  fail-closed direction):** When `python3 -m pytest tests/test_permission_floor_map.py -q` runs in the plugin
  repo, the test module SHALL assert AC-PFM-1 through AC-PFM-4 and AC-PFM-6 in separately named tests; SHALL
  derive the AC-PFM-2 ground truth by enumerating the tree at run time (never from a hand-copied script
  list); SHALL expose its coverage and subsumption checks as functions of an explicit `(tree_root,
  parsed_map)` pair so they can be driven over a throwaway tree; and SHALL include **negative controls**
  asserting that the relevant check FAILS on each of five materialized fixtures: (a) a tree copy carrying one
  extra, un-tiered `scripts/*.py` file; (b) the same with an un-tiered `scripts/*.sh` file; (c) the same with
  an un-tiered extensionless owner-executable file; (d) a map naming a `scripts/` basename absent from the
  tree (the reverse-direction/dangling check); and (e) a map carrying a blanket
  `Bash(<plugin_root_glob>/scripts/:*)` allow alongside any `ask` pin beneath it (the subsumption check).
  Without these the corresponding assertions are vacuous. *(Checkpoints:
  `-k coverage_assertion_is_not_vacuous`, `-k negative_controls_all_fire`, plus the whole-module green run.)*

- **AC-PFM-6** *(Invariant — ubiquitous)* **(no rule silently subsumes a different tier, and no rule grants
  the scripts directory wholesale):** For any two distinct `entries` elements A and B whose bodies are `bA`
  and `bB`, where `bA` ends in `:*`: let `sA` be `bA` without that trailing `:*`, and `sB` be `bB` without
  its own trailing `:*` if present. If `sB` begins with `sA`, then B's `tier` SHALL equal A's `tier` — a
  broad prefix rule and everything its reach swallows SHALL agree, so a split tier is expressible only
  through reach-disjoint forms (an exact rule, or a disjoint leading subcommand/flag form). Independently,
  every rule body containing the literal `/scripts/` SHALL name a concrete script: the text between
  `/scripts/` and the next space, `:`, or body end SHALL be a non-empty basename belonging to the AC-PFM-2
  ground-truth set. *(Checkpoints: `-k no_rule_subsumes_a_different_tier`,
  `-k scripts_dir_rules_name_a_concrete_script`.)*

- **AC-PFM-7** *(Invariant — ubiquitous)* **(the repo's own health gate is unchanged):** `python3
  scripts/foundry-doctor.py` SHALL report `DOCTOR-GREEN` on the plugin repo with this atom applied — this
  atom adds a data file and a test; it wires no hook, changes no gate, and touches no schema.
<!-- /normative -->

## Design / notes

- **One entry per command shape, verbose by design.** The allow tier is one explicit entry per invocable
  script rather than a class-wide wildcard, so a reviewer reads a line and a rationale per command shape and
  AC-PFM-2's closed world can bind. AC-PFM-6's second half makes that structural: a bare
  `Bash(<glob>/scripts/:*)` is not a legal rule in this map at any tier.
- **Split tiering, and what it costs.** The ER wants `foundry-doctor.py --heal` and
  `foundry-stack-profile.py --relock` at `ask` while the scripts themselves stay `allow`. Both flags are
  verified **leading** options (neither script takes a positional: `foundry-doctor.py` declares
  `--session-start`/`--heal`/`--repo`; `foundry-stack-profile.py` declares `--validate`/`--load`/`--relock`),
  so they are prefix-keyable. But a broad `Bash(<glob>/scripts/foundry-doctor.py:*)` allow would **subsume**
  the `--heal` ask and silently win, which AC-PFM-6 now forbids. The expressible shape is therefore a set of
  reach-disjoint forms: an **exact** rule for the bare invocation plus one prefix rule per allowed leading
  flag, with `--heal` / `--relock` as their own `ask` entries. The deliberate consequence is that an
  unenumerated flag form matches no rule and falls through to a prompt — the fail-safe direction, and the
  reason no blanket per-script allow is used for these two.
- **`foundry-project-sync.py` is `ask`, diverging from the ER's class-B placement.** The ER listed it under
  class B (allow, with an explicit rule). Verified against the shipped script: its non-dry-run path issues
  authenticated GraphQL **write** mutations against a remote (`createIssue`, `updateIssue`,
  `addProjectV2ItemById`, `updateProjectV2ItemFieldValue`, `addSubIssue`, plus a REST milestone create).
  A `--dry-run` allow beneath a script-wide `ask` is exactly the subsumption AC-PFM-6 forbids, and inverting
  it (dry-run allow + everything-else ask) is inexpressible with prefix rules, so the whole script is `ask`.
  This is the spec review's re-tiering, recorded rather than silently absorbed.
- **`foundry_tier_preflight.py` is `ask`** — its `--apply` path POSTs repository rulesets with an
  Administration (write) token, i.e. it writes the merge floor itself. Verified: `--apply` is a declared flag
  on the shipped CLI, and `^scripts/foundry_tier_preflight` is already an anchored arm of the btb-gates
  security-path pattern.
- **`foundry-cut-release.py` is `ask`, and the honest reason.** Verified: the script *never* runs `git tag`,
  `git push`, or `gh`, and mutates no tree — it emits a verdict and the operator executes the emitted
  commands. The `ask` is therefore not a mutation guard; it is the ER's class-C ceremony marker at the
  release-cut moment, taken at the coarser fail-safe tier. The commands it emits are R8.
- **The eight non-`.py` invocable shapes, each verified.** `foundry-bootstrap.sh` → **ask** (it writes
  **global** git config: `git config --global --unset-all` / `--get-regexp` / two `includeIf.*.path` writes
  — machine-scope state, the same class the ER flags as a class-C shape). `foundry-apply-runtime-gitignore.sh`
  → **allow** (idempotent managed-block writer confined to `<repo-root>/.gitignore`, refuses on any
  malformed state; invoked in command position by `skills/init/SKILL.md`). `foundry-wt` → **allow**
  (worktree wrapper, workspace-root-confined by construction, consumed by the WorktreeCreate hook and the
  dispatch skill). `foundry-statusline.sh` and `foundry-subagent-statusline.sh` → **allow** (read-only,
  fail-open renderers that print and exit 0). `foundry-statusline-wrapper.sh` and
  `foundry-subagent-statusline-wrapper.sh` → **not_invoked** (the workflow `install`s them into the adopter
  repo — they appear only in argument position inside init's prescribed block — and the *platform's*
  `statusLine`/`subagentStatusLine` key runs the installed copy, never an agent Bash call).
  `foundry-direnv-lib.sh` → **not_invoked** (a direnv global-lib file, sourced by direnv, definitions only —
  it is never a command).
- **`not_invoked` is a deliberate classification, not an escape hatch — and now it is checked.** The
  `scripts/` tree mixes CLIs with imported library modules (`foundry_authz.py`, `foundry_contract.py`,
  `foundry_graph.py`, `foundry_id_apply.py`, …). A module that is never a command is recorded with a one-line
  reason, and AC-PFM-2's disjointness means "forgot to classify" is indistinguishable from "failed the test".
  The new command-position cross-check closes the other half: a script the workflow actually instructs cannot
  be parked in `not_invoked` to dodge a tier decision. Note the check's live consequence —
  `foundry_run_metrics.py` **is** invoked in command position by `hooks/foundry-run-metrics.sh`
  (`python3 "$_SCRIPTS_DIR/foundry_run_metrics.py" --posttooluse`), so despite its underscore-library naming
  it must be **tiered**, not excluded. R11 bounds the check's known blind spot.
- **`foundry_id_apply.py` is a library, not a CLI.** Verified: the module declares no `argparse`, no
  `main()`, and no `if __name__ == "__main__"` block; it is loaded via `tests/conftest.py`'s `load_module`
  and exposes `classify_gitops`/`decide_apply` as pure functions. It is therefore `not_invoked`, with the
  same rationale as its library siblings — **not** an `ask` pin. The real id-apply EXECUTE ceremony is R5.
- **Why the ask tier is coarsened at three points.** `foundry-authorize.py --yes` / `--skip-audit-reason`
  and `foundry-upstream-submit.py --create` are ceremonies keyed on flags that arrive *after* other
  arguments; a prefix rule cannot reach them, so the whole script is `ask`. `claude plugin tag --push` takes
  a tree path between the verb and the flag, so the ask is taken at `claude plugin tag:*` (a dry-run also
  prompts — accepted). `foundry-decommission.py record|gate-check` and `foundry_release.py accept` are
  leading subcommands and *are* prefix-keyable, so they are pinned at that precision.
- **Non-script entries are permitted and unbound.** Class-D dev-loop and read-only infra forms (`pytest`,
  `gh pr create`, `gh pr checks`, feature-branch `git push`, read-only `tofu`/`kubectl`/`argocd`) are
  ordinary entries; AC-PFM-2's closed-world obligation binds only the `scripts/` ground-truth set, so adding
  or refining a third-party CLI form never trips the coverage test. AC-PFM-3's `git push` shaping clause and
  R8 are the one constraint on that freedom.
- **Source.** ER `intake/er-onboarding-wizard-and-permission-floor.md` (2026-08-01 classifier sweep, class
  A–E catalogue, and the two web sweeps behind the trust-moment finding). Every flag, subcommand, cache-path
  segment, and library-vs-CLI claim in AC-PFM-1/-3 and in these notes was re-verified against the shipped
  `agentic-foundry` tree during the v1.1 remediation, not quoted from memory.

## Out of scope / non-goals

- **The pre-session bootstrap CLI that consumes the map** (ER atom 1) and the **doctor permission-floor drift
  check** (ER atom 4) — both named in the ER, both separate atoms. This atom ships data and its validation.
- **Writing, merging, or applying any `.claude/settings*.json`** — in this workspace or an adopter's. Nothing
  here is applied by anything; the interim hand-paste the ER records stays a hand-paste.
- **`/foundry:init` slimming** (ER atom 3) and the **gate-denial fallback discipline** (ER atom 5).
- **Changing what any script does, its flags, or its ceremony status.** The map *records* the tiering the ER
  decided (and, where the review re-tiered, records that too); it does not create, relax, or tighten a gate.
- **Widening the btb-gates security-path pattern** so this map's own path routes to floor #3 (R7) — that
  edits `.github/workflows/**`, which this contract denies, and is its own light-lane change.
- **Exhaustively enumerating every third-party CLI form a user might run.** The map carries the classes the
  ER names; completeness is asserted only over the `scripts/` ground-truth set.
- **Proving that a rule actually matches a live command string.** See R1/R2 — that is observed at the seam
  that applies the map, which this atom does not build.

## Residuals ledger

- **R1 — A mid-path `*` in a `Bash()` rule is not documented as a prefix-match wildcard (epistemic).** The
  marketplace/version wildcard in `plugin_root_glob` is the only shape that survives a plugin upgrade, and
  the proven precedent (`[[feat-foundry-init-statusline-wrapper]]`) is a *shell* glob, not a permission rule.
  **Bound:** the map grants nothing, so a non-matching rule is inert, not unsafe; AC-PFM-4 pins the rule
  *shape* and deliberately not its matching semantics, because this atom has no way to execute; and
  `plugin_root_glob` is single-sourced, so a correction after the consuming atom observes real matching is a
  one-line edit to one field.
- **R2 — Invocation-spelling coupling (epistemic).** A rule beginning `~/.claude/…` is a different string
  from the same command written `$HOME/.claude/…`, as an absolute path, or via a `CLAUDE_PLUGIN_ROOT`
  expansion. Same bound as R1; the consuming CLI atom owns reconciling the spelling the workflow actually
  emits.
- **R3 — The deny tier's reach is prefix-bounded (design, accepted).** A prefix rule cannot see a non-leading
  `--admin`, cannot tell a protected branch from a feature branch on `git push --force`, and does not cover
  the `terraform` twin of `tofu destroy -auto-approve`. **Bound:** force-push and destructive-op refusal are
  hook-enforced today; the deny tier is declared as a harness-level second line, exactly as the ER framed it
  ("already hook-blocked; settings deny makes it harness-enforced too"). Per CLAUDE.md, outcome-level
  controls plus the operator's terminal test pass are what carry this — enumerating every evasion inside a
  data file would be anti-gaming machinery priced above its value.
- **R4 — The test enforces completeness, syntax, and coherence — never the correctness of a tier
  (epistemic).** Nothing machine-derives that `foundry-wt` *belongs* in allow rather than ask; AC-PFM-2 only
  proves the decision was *made*, AC-PFM-6 only proves the decisions do not contradict each other, and
  AC-PFM-3 only pins the ones the operator and the review named. Tier judgement rests on the spec review and
  the operator's sign-off, and is stated here rather than implied as coverage.
- **R5 — The id-apply EXECUTE ceremony is outside this map's closed world (scope, accepted).** `id-apply`'s
  mutation is not a `scripts/*.py` invocation at all: `foundry_id_apply.py` is a pure decision library, and
  the command actually run on the EXECUTE branch is the **stack profile's frozen `infra_binding.apply`
  string** — operator-authored and audited at profile-authorize time, arbitrary in shape, and unknown to the
  plugin tree. No `Bash()` rule over `scripts/` can express it, and a rule broad enough to cover any possible
  binding string would be a blanket grant. **Bound:** the ceremony is gated where it lives — the id-apply
  skill's own posture gate, which refuses fail-closed on a missing/ambiguous posture, restricts EXECUTE to
  non-prod/break-glass non-GitOps, and emits a runbook for the operator otherwise. The map records the
  boundary rather than pretending to cover it. A future map may add an `infra_binding` tier convention; that
  belongs with the consuming CLI, which is the only component that sees a resolved profile.
- **R6 — The `ask` → `allow` decay path is real and unmeasured (design, accepted, and the sharpest edge on
  the posture change above).** The harness's ask-prompt offers a persist option; accepting it writes an
  `allow` rule into `.claude/settings.local.json` — a *local, gitignored-by-convention* file — with **no
  second trust dialog**. One operator keypress in a hurry therefore converts a declared ceremony `ask` into a
  standing local grant, invisible in the reviewed `settings.json` diff, for `foundry-authorize.py` as much as
  for anything else. **Bound:** nothing this atom ships can prevent that (the map is inert data and the
  prompt is the platform's), and the operator's terminal test pass remains the outer control. **Named
  follow-on:** the doctor permission-floor drift check (ER atom 4) SHALL read `.claude/settings.local.json`
  in addition to `.claude/settings.json`, and report any local `allow` that shadows a declared `ask` as
  drift — otherwise the drift check watches the one file the decay does not touch. Recorded here so that
  requirement is inherited rather than rediscovered.
- **R7 — This atom's diff does not route to the floor-#3 security-path CI check (process gap, tracked).**
  Verified against `.github/workflows/btb-gates.yml`: the `security-path` job's pattern is
  `(auth|secret|credential|token|provenance|signing|\.rego$)|^\.github/|^hooks/|^\.claude-plugin/|(^|/)(standing-versions|profile-version-ledger)|<dependency manifests>|^skills/|^agents/|^rulesets/|^scripts/foundry_tier_preflight`.
  `docs/permission-floor.json` matches **no** arm, so a future edit to the permission floor — the file that
  declares what an agent may run without a prompt — would not mechanically demand a security review.
  **Bound:** this atom's own review is happening (the map is a security artifact and was reviewed as one),
  and the map grants nothing until a consumer applies it. **Named follow-on:** a light-lane change adding an
  anchored `^docs/permission-floor` arm (OR-append only, widen-only, with negative-control rows in
  `tests/fixtures/btb-gates/security-path-matrix.yaml`, mirroring the AC-SCW-13 / AC-TARC-15 discipline).
  It is **not** done here because the contract denies `.github/workflows/**` and because that file matches
  `^\.github/` itself, so the widening must ride its own security-reviewed PR.
- **R8 — The cut-release PUBLISH ceremony is inexpressible as a script rule (design, accepted).** The
  release's actual mutations are raw `git tag -a` + `git push` of the tag and the default branch, executed by
  the operator from the verdict `foundry-cut-release.py` emits. No `scripts/`-shaped rule reaches them, and
  the class-D dev-loop `git push` allow the ER contemplates would swallow them if written bare — which is why
  AC-PFM-3 forbids a bare `Bash(git push:*)` at any tier and the D-tier push rules must be shaped (flag or
  explicit remote/refspec forms) to exclude tag and default-branch pushes. **Bound, stated honestly:** a
  prefix rule *cannot* distinguish a feature-branch push from a `main` push, so this shaping is partial. The
  hook that backs it is `hooks/foundry-git-discipline.sh`, and its coverage is **force-intent only** —
  verified: a push with no `--force`/`-f`/`--force-with-lease` and no leading-`+` refspec is explicitly
  skipped (`if not force_intent: continue`), so a *non-force* `git push origin main` is blocked by neither
  the hook nor any prefix rule. What holds that line is branch protection plus the operator's terminal test
  pass, not this map.
- **R9 — The closed world assumes a flat `scripts/` directory (epistemic, low).** Ground truth is
  enumerated non-recursively, and every rule body is shaped `<glob>/scripts/<basename>`. If `scripts/` ever
  gains a subdirectory of invocable files, they are silently outside the closed world — the coverage test
  stays green while the map is incomplete. **Bound:** the tree is flat today (verified); the failure mode is
  a missing grant (a prompt), not a missing restriction; and the fix is a one-line change to the enumerator
  plus the AC-PFM-6 basename rule when it happens.
- **R10 — Trust-dialog re-prompt semantics for a *changed* allow set are unverified (epistemic).** The
  documented behaviour is that committed `allow` rules take effect only after the workspace trust dialog is
  accepted. What is **not** verified is whether adding rules to an already-trusted workspace re-prompts with
  the new list, silently activates them, or requires an explicit re-trust — which decides whether a plugin
  upgrade that widens the floor is consented to or merely inherited. **Bound:** unverifiable here (this atom
  cannot execute and ships no consumer). **Assigned:** the consuming pre-session bootstrap CLI (ER atom 1)
  owns observing this at its own live seam and recording the answer; `generated_for_plugin_version` exists in
  the schema so that atom can detect a floor that widened under an already-trusted workspace.
- **R11 — The `not_invoked` command-position check is a lexical heuristic (epistemic, fail-safe).** It reads
  the shipped instruction text, so indirection defeats it: `hooks/foundry-env-reap.sh` assigns
  `PER_WORKER_ENV_CHECK="${PLUGIN_ROOT}/scripts/foundry_env_isolation.py"` and invokes the *variable*, so
  that basename never appears in command position and could be parked in `not_invoked` despite being run.
  **Bound and direction:** a false negative leaves a genuinely-invoked script **un-granted**, so the cost is
  a prompt, never a silent grant; the check's purpose is to stop `not_invoked` being used to dodge a tier
  decision on an obviously-instructed command, and it does that. Tier judgement itself remains R4's.

## Changelog

- v1.1 Remediation against the 2026-08-02 four-lens spec review (one round), every claim re-verified against
  the shipped `agentic-foundry` tree. **Blocks resolved:** `foundry_id_apply.py` moved from AC-PFM-3's ask
  pins to `not_invoked` (verified: no `argparse`/`main()`/`__main__`) with R5 recording the real
  `infra_binding.apply` EXECUTE ceremony as outside the closed world; `not_invoked` gains a uniqueness rule
  and a command-position truth check (AC-PFM-2) so it cannot be used to dodge a tier; **new AC-PFM-6** adds
  the subsumption rule (no prefix rule may swallow a different tier) and forbids a bare
  `<glob>/scripts/:*` rule; AC-PFM-2's ground truth widens from `scripts/*.py` to every invocable shape
  (`*.py` plus owner-executable files — the seven `*.sh` and `foundry-wt`), each of the eight non-`.py`
  shapes individually verified and tiered; the Security posture now states plainly that declaring
  `authorize` at `ask` changes floor #1's harness posture from classifier hard-block to one-keypress, citing
  the ER decision, with R6 recording the `ask`→`allow` decay through `settings.local.json` and the drift
  check's inherited requirement; R7 records the btb-gates security-path routing gap and its tracked
  follow-on. **Risks folded in:** split tiering permitted only through reach-disjoint forms, with
  `foundry-doctor.py --heal` and `foundry-stack-profile.py --relock` pinned to `ask` (leading flags verified
  prefix-keyable); `foundry_tier_preflight.py`, `foundry-cut-release.py`, `foundry-project-sync.py` added to
  the ask pins (the last diverging from the ER's class-B placement, recorded); R8 pins the `git push`
  shaping rule and names the git-discipline hook's force-only coverage; `plugin_root_glob`'s exact value
  pinned inline and `generated_for_plugin_version` added to the schema, with R9/R10/R11 recording the
  flat-directory assumption, the unverified re-prompt semantics, and the heuristic's blind spot. **Rubric:**
  the DOCTOR-GREEN regression split out as **AC-PFM-7**, AC-PFM-2's tier-coherence subclause given its own
  checkpoint, AC-PFM-3's closing negative-shall rephrased positively, and every AC now names its contract
  `pytest -k` substrings. AC-IDs 1–5 unchanged; 6 and 7 are new.
- v1.0 Draft. Ship `docs/permission-floor.json` — the canonical three-tier allow/ask/deny map of every
  command shape the plugin's workflow instructs, from the ER's class A–E classifier-exposure catalogue —
  plus `tests/test_permission_floor_map.py`, which derives its ground truth from the shipped tree so a new
  un-tiered script fails closed. Data only: no hook, no gate, no settings write; consent stays at the
  platform trust dialog. Realizes ER atom 2 of
  `intake/er-onboarding-wizard-and-permission-floor.md`.
