# The pre-session bootstrap wizard: `npx create-agentic-workspace`  (feat-foundry-bootstrap-cli)

> **Human-readable intent.** The ER's headline finding is structural, not cosmetic: **the in-session
> `/foundry:init` can never scaffold its own permission floor**, because a model editing its own confinement
> is a shape the auto-mode classifier hard-denies (confirmed live 2026-08-01 — the `.claude/settings.json`
> write was blocked). So the floor must exist **before a session exists**, and the only place with no
> classifier is the operator's own terminal.
>
> **This atom ships that terminal step**: a zero-dependency Node CLI, `npx create-agentic-workspace`, that
> walks the operator through name/dir → greenfield-vs-existing → git/GitHub identity → stage mode → **the
> permission conversation**, previews *every file it will write and every capability it will declare*, writes
> the workspace, and stops. It writes **declarations**, never grants: the plugin's reviewed three-tier
> `docs/permission-floor.json` is emitted verbatim into the workspace's committed `.claude/settings.json`
> alongside `extraKnownMarketplaces` + `enabledPlugins`, and the CLI **never runs `claude`, never accepts the
> trust dialog, and never pre-grants anything**. The dialog is the consent ceremony; the exit line explains
> what accepting it grants (`cd <dir> && claude` → trust → `/foundry:init`) and points at `/foundry:doctor`'s
> floor check, the one adopter-side verification that does not travel through npm.
>
> Three properties are normative rather than prose: **flag/prompt parity** (a flag twin per question; `--yes`
> never prompts), **preview-before-write** (files *and* capabilities, before the first byte), and **idempotent
> re-run as reconcile-with-drift-report** — a second run leaves bytes, inode and mtime unchanged, an edited
> managed file is *reported*, never overwritten, and **never-clobber holds unconditionally, on every path and
> in every mode**. The permission half of that report reuses
> `[[feat-foundry-doctor-permission-floor-check]]`'s vocabulary and subsumption table, so the workspace's
> three matchers cannot diverge silently.

## Prior art / industry grounding

- **The packaging is the category default, and the ER already fixed it.** `npm create-*` invoked through
  `npx`, with all work at **explicit invocation time and none at install time**, is the consensus for a
  Node-adjacent audience (ER *Prior art*, two 2026-08-01 sweeps): `create-next-app`, `create-vite`,
  `create-t3-app`, OpenSpec's `openspec init`, cc-sdd's `npx cc-sdd@latest`. Spec-Kit's `specify init` is the
  same shape over `uvx` because it is Python-implemented. `curl|sh` is reserved for compiled binaries. Nothing
  novel is introduced here.
- **The ban on install-time execution is an ecosystem consensus with mechanisms behind it**, not a preference:
  npm ships `--ignore-scripts`, pnpm gates build scripts behind an explicit allow-list, and the repeated
  supply-chain incidents in this class all execute through `postinstall`. This corpus already votes that way —
  `docs/standing-versions.md` §3a pins the Playwright runner with **`npm ci --ignore-scripts`** as "the
  enforcement, not the rego". AC-BCL-1 therefore closes the `scripts` key to `{test}` rather than merely
  omitting `postinstall`.
- **Wizard craft** (create-next-app / create-t3-app / clig.dev, `https://clig.dev/`): a flag twin for every
  prompt plus `--yes` (never *require* a prompt — a CLI that only works interactively is unscriptable),
  recommended defaults pre-selected, and a preview before writing. `terraform init` supplies the re-run
  posture: reconcile what is missing, **report** what diverged, change nothing that was edited. That is the
  same *surfaced-never-fixed* posture `[[feat-foundry-workspace-repo-verbs]]` and the `id-drift` gate hold.
- **The trust moment is the platform's, verbatim-verified in the ER against primary docs.** Committed project
  `permissions.allow` rules take effect **only after** the workspace trust dialog, which lists exactly those
  rules; `deny`/`ask` restrict only and apply immediately; project-declared marketplaces/plugins raise a native
  install prompt on trust. This is the VS Code **Workspace Trust** manifest pattern. A scaffolder therefore
  *declares*; it never self-grants — and `[[feat-foundry-permission-floor-map]]`'s Security posture already
  records the one posture change that declaration makes (ceremony `ask` becomes a one-keypress prompt).
- **Templates travel in the tarball, not over the wire.** `create-next-app` and `create-vite` bundle their
  templates inside the published package; degit-style pulls (and Spec-Kit's release-asset download) trade
  offline capability and version-pinning for freshness. Given the ER's *no network at scaffold time* bound and
  this workspace's pinning discipline, the bundled form is adopted (Clarifications).
- **Single-sourcing by committed mirror + a byte-identity test** is this corpus's own shape for data that must
  exist in two places (`docs/standing-versions.md` ⟷ `docs/standing-versions.lock.yaml`, kept byte-synced by
  rule; `[[feat-foundry-gate-denial-fallback]]`'s three-way enumeration equality). AC-BCL-5 applies it to the
  permission map and the runtime-gitignore block rather than inventing a build step.

## Security posture

**The CLI's whole job is to *declare* a confinement, so its own confinement is the load-bearing claim.** Three
bounds are asserted at the outcome level, not by enumerating forbidden calls: every write lands inside the
physically-resolved target root, **except** the three enumerated machine-scope artifacts of AC-BCL-7 (the two
global `includeIf` keys and the per-account include file at its pinned path — the same machine scope
`scripts/foundry-bootstrap.sh` already writes, absorbed out of the session); the child-process set is closed to
`git` and `gh`, so **`claude` is never spawned and no trust is ever self-accepted**; and the import closure
carries no network module, so the only egress that can exist is the single bounded `gh api user` identity
probe, which is optional, timeout-bounded, scoped to its own `GH_CONFIG_DIR` jail, and degrades to a prompt.

**Every control above is a BUILD-TIME control, and none of them survives a publish compromise.** AC-BCL-1's
no-lifecycle-script and import-closure checks, AC-BCL-5's byte-identity mirror, AC-BCL-9's confinement and
no-network checks all run in the plugin repo's CI over the **source tree**. They are assertions about what was
reviewed and packed; they are **not** attestations about the tarball an adopter's `npx` actually downloads. An
attacker who compromises the npm publish path — a stolen token, a hijacked release job — ships a package that
these tests never see, and that package writes the adopter's permission floor. Stated plainly so no reader
mistakes an in-tarball assertion for a supply-chain guarantee. **Two adopter-side checks are independent of the
tarball, and both are partial:**

1. **The platform's trust dialog** gates every `allow` rule, and it is the operator's own eyes on the grant
   list. Its two blind spots are named rather than implied: **(i) a *dropped* rule is dialog-invisible** — the
   dialog lists what a workspace grants, not what it fails to deny, so a tampered tarball that silently omits
   `deny` and `ask` entries presents a *shorter, more innocuous* list, which is the wrong direction for a human
   check; and **(ii) the list is long** — the shipped map's `allow` tier alone is 40 rows of 56 entries
   (inspected 2026-08-02), which is a wall an operator realistically skims rather than audits.
2. **`[[feat-foundry-doctor-permission-floor-check]]`'s floor probe**, which compares the workspace's written
   settings against **the cache-installed plugin's own `docs/permission-floor.json`** — an artifact delivered
   over the marketplace/plugin channel that **did not travel through npm**. A tampered CLI and an untampered
   plugin therefore disagree, and the disagreement is reported in the probe's own finding vocabulary. This is
   the reason AC-BCL-4(e) makes the exit line *direct* the operator to run it after the first session opens:
   the cross-channel comparison is the only independent check the adopter gets, and it is worth nothing if
   nobody runs it. It is advisory, not blocking (R5, R9).

R9 assigns the missing half — `npm publish --provenance` and an `npm audit signatures` verification step — to
the release cut, which owns publishing and is the only place that can attest the artifact.

**It writes an operator's own new workspace, and refuses anything else.** A non-empty target is refused unless
`--existing` is given *and* confirmed (typed interactively), and template materialization refuses any path
whose physical resolution escapes the target root. **Never-clobber is unconditional** (AC-BCL-9): there is no
path through the CLI — no mode, no flag combination — on which a file that exists and differs is written. A
pre-existing `.claude/settings.json` the CLI did not author is reported `drifted` and is **never merged**;
merging a foreign settings file would be floor-widening by an unreviewed union, so the drift report is the
whole remedy.

**No telemetry, stated plainly and normatively** (AC-BCL-9): the CLI collects nothing, transmits nothing, and
has no opt-out to offer because there is nothing to opt out of. **No credential is read, derived, or written**
— the identity step handles a name, an email and an account slug, and the `gh` probe reads whatever
authentication the operator's own `gh` already holds without ever seeing it.

**Nothing here grants.** `docs/permission-floor.json`, `scripts/**`, `hooks/**`, `schema/**`, `skills/**`,
`.claude-plugin/**` and `.github/workflows/**` are contract-denied: the map is *mirrored*, never edited; no
gate, hook, CI check or authorization path is touched — **no wiring re-pin, no meta-regress, no new exec
seam**. The one posture consequence is inherited and already reviewed: applying the map's `ask` tier makes
`foundry-authorize.py` reachable behind one operator keypress, which is the ER's class-C decision recorded in
`[[feat-foundry-permission-floor-map]]`'s Security posture and R6. R5 records that this atom's new surfaces do
not route to the floor-#3 security-path CI check and inherits the tracked follow-on.

<!-- normative -->
## Terminology (normative)

- **the package** — the npm package rooted at `cli/`, named `create-agentic-workspace`.
- **the bundled map** — `cli/permission-floor.json`, the package's committed copy of the plugin's
  `docs/permission-floor.json`.
- **the target root** — the workspace directory the operator named, resolved **physically** (symlinks
  followed).
- **a managed path** — a path the CLI creates or converges, drawn from the closed set AC-BCL-6 declares.
- **a machine-scope write** — a write whose physical resolution lies outside the target root.

## Acceptance criteria

- **AC-BCL-1** *(Invariant — ubiquitous)* **(the package is dependency-free and executes nothing at install
  time):** `cli/package.json` SHALL declare `name` `create-agentic-workspace`, `type` `module`, a `bin` entry
  mapping `create-agentic-workspace` to `bin/create-agentic-workspace.mjs`, `engines.node` exactly
  `">=22.0.0"`, and a `files` array that enumerates every path the CLI reads at run time. Its `dependencies`,
  `devDependencies`, `optionalDependencies` and `peerDependencies` keys SHALL each be absent or empty, and its
  `scripts` object SHALL contain keys only from the closed set `{test}` — so no `preinstall`, `install`,
  `postinstall`, `prepare`, `prepack` or `prepublish` hook exists to run. Every `import` statement and dynamic
  `import()` specifier in every `cli/**/*.mjs` file SHALL be either a `node:`-prefixed built-in or a relative
  path beginning `./` or `../`; no bare specifier SHALL appear; and **any `import()` whose specifier is not a
  string literal SHALL be forbidden**, so the closure is decidable by static inspection of the source rather
  than only by running the CLI. `cli/package.json` SHALL additionally carry a `foundry` object whose four pin
  keys are each **equal to the value at the named path** in `.claude-plugin/marketplace.json` —
  `marketplace_name` to the manifest's top-level `name`, `marketplace_repo` to `plugins[0].source.repo`,
  `plugin_name` to `plugins[0].name`, `plugin_version` to `plugins[0].version` — and `pins_researched`, an
  ISO-8601 `YYYY-MM-DD` date. *(Checkpoints: `-k package_manifest_has_no_lifecycle_scripts_and_no_deps`,
  `-k every_import_is_a_node_builtin_or_relative`, `-k the_plugin_pin_block_matches_the_marketplace_manifest`.)*

- **AC-BCL-2** *(Invariant — ubiquitous)* **(flag/prompt parity, and `--yes` never prompts):** The wizard's
  questions SHALL be declared **once**, as a single table in `cli/src/**` whose records each carry an `id`, a
  long `flag`, a prompt string and either a declared default or the marker that it is required. The argv parser
  SHALL derive its accepted long flags from that table and from no second list, and `--help` SHALL render one
  line per record. A record MAY additionally carry a **`choices`** array; when it does, a value supplied by
  flag or by prompt that is not a member of that array SHALL cause a **refusal** naming the record and its
  permitted values, and SHALL write nothing — so a closed-vocabulary answer (stage mode, greenfield/existing)
  is validated in one place rather than at each use site. The three sets — table flags, argv-accepted flags,
  `--help` lines — SHALL be **equal**. When
  `--yes` is given, or when stdin is not a TTY, the CLI SHALL emit **zero** prompts: every unanswered question
  SHALL take its declared default, and any required question left unanswered SHALL cause a refusal that names
  the missing flag and writes nothing. *(Checkpoints: `-k prompts_and_flags_are_a_bijection`,
  `-k yes_mode_completes_without_a_prompt`, `-k yes_mode_refuses_naming_the_missing_flag`.)*

- **AC-BCL-3** *(Requirement — event)* **(preview covers files AND capabilities, before the first byte):**
  When the CLI reaches its write phase, it SHALL first print, before any byte is written or any git config is
  touched: (i) every managed path with its action — one of `create`, `unchanged`, `drifted` (AC-BCL-8's
  vocabulary); (ii) every machine-scope write it will make, each named with its scope; and (iii) **every
  capability it will declare** — one line per bundled-map entry, carrying that entry's `rule`, its `tier`, and
  its `rationale` rendered as the plain-language reason, grouped by tier. It SHALL then require an affirmative
  confirmation unless `--yes` was given. When `--dry-run` is given the CLI SHALL print that same preview and
  perform **no write and no side effect**: every file under the target root SHALL be byte-identical afterwards
  (per-file SHA-256), no path SHALL be added or removed, `git config --global --list` SHALL be byte-identical
  before and after, and **zero** child processes SHALL be spawned. That dry-run proof SHALL be taken over a
  **populated** target — one already scaffolded and then edited, so the preview reports at least one `create`
  row **and** at least one `drifted` row — and not only over an empty directory, since an empty directory
  cannot distinguish "wrote nothing" from "had nothing to overwrite". *(Checkpoints:
  `-k preview_lists_every_file_and_every_capability`, `-k dry_run_writes_nothing_and_spawns_nothing`.)*

- **AC-BCL-4** *(Invariant — ubiquitous)* **(declarations only — the map is emitted verbatim, and the trust
  gate is left to the platform):** The `.claude/settings.json` the CLI writes SHALL satisfy all five:
  **(a)** `permissions.allow`, `permissions.ask` and `permissions.deny` SHALL each equal, **as a set**, the set
  of `entries[].rule` values in the bundled map whose `tier` is `allow`, `ask` and `deny` respectively — no
  rule invented, none dropped, none re-tiered;
  **(b)** `extraKnownMarketplaces` SHALL carry **exactly one** key, `<marketplace_name>`, whose value SHALL be
  exactly the object `{"source": {"source": "github", "repo": <marketplace_repo>}, "autoUpdate": false}` — the
  `source` object's key set closed to `{source, repo}` (**no `installLocation`**), `source.source` equal to the
  literal `github` and to no other value (`git`, `directory`, `url` and any other spelling forbidden), and
  `autoUpdate` the boolean `false` and never `true` or absent. `enabledPlugins` SHALL carry exactly the one key
  `<plugin_name>@<marketplace_name>` mapped to `true`. `<marketplace_name>` and `<marketplace_repo>` SHALL be
  read from `cli/package.json`'s `foundry` block (AC-BCL-1's pin paths) and from no hand-copied literal;
  **(c)** the file SHALL parse as JSON, and its **top-level key set SHALL be exactly**
  `{permissions, extraKnownMarketplaces, enabledPlugins}`, plus zero or more keys whose names begin `//` and
  each of which appears in the **closed, enumerated comment-key list the templates declare** (so a comment key
  is a named, reviewed addition and not a wildcard). `permissions`' own key set SHALL be closed to exactly
  `{allow, ask, deny}` — so there is no `permissions.defaultMode`. The CLI SHALL emit **no** `statusLine`,
  `hooks`, `sandbox`, `apiKeyHelper`, `env`, `mcpServers` or `additionalDirectories` key at **any** nesting
  level of the written file. This is a deliberate narrowing of the ER's atom-1 absorption list, which named
  "git identity, settings, statusLine, sandbox": **`statusLine` and `sandbox` are not emitted at all** — not
  here and not relocated to another writer — because both are grants-adjacent configuration the trust model
  says the operator should choose in-session, and neither is needed for a floor to exist. **The ER's
  absorption list is amended accordingly** (Out of scope), and the in-session slimming atom inherits the open
  question of where, if anywhere, they land;
  **(d)** the CLI SHALL write **no** path under `$HOME/.claude/`, SHALL never write `.claude/settings.local.json`,
  and SHALL spawn child processes only from the closed set `{git, gh}` — so `claude` is never executed and no
  trust is ever self-accepted; and
  **(e)** the run's final output SHALL carry the trust hand-off, containing the case-sensitive literals
  `trust dialog`, `cd `, `claude`, `/foundry:init`, and a sentence stating that the `allow` rules take effect
  **only after** the operator accepts that dialog, which lists them. It SHALL **additionally** contain the
  literal `/foundry:doctor` in a sentence directing the operator to run the permission-floor check once the
  session is open, stating that it compares the written floor against the **installed plugin's own** copy of
  the map — the one independent, non-npm-delivered check available to the adopter (Security posture).
  *(Checkpoints: `-k settings_permissions_are_a_bijection_onto_the_map`,
  `-k marketplace_and_plugin_are_single_sourced`, `-k the_marketplace_entry_is_the_pinned_literal`,
  `-k settings_json_key_set_is_closed`, `-k the_cli_never_spawns_claude_or_writes_home_claude`,
  `-k the_exit_line_explains_the_trust_gate`.)*

- **AC-BCL-5** *(Invariant — ubiquitous)* **(bundled, single-sourced, offline):** `cli/permission-floor.json`
  SHALL be **byte-identical** to `docs/permission-floor.json`. The workspace `.gitignore` template under
  `cli/templates/**` SHALL contain a `FOUNDRY-RUNTIME-GITIGNORE-BEGIN` / `FOUNDRY-RUNTIME-GITIGNORE-END`
  managed block whose interior lines are **byte-identical** to `scripts/foundry-runtime.gitignore`, so the
  scaffolded runtime partition and the shipped applier cannot diverge. Every path the CLI opens for reading at
  run time SHALL resolve inside the package directory and SHALL be matched by `cli/package.json`'s `files`
  array; the CLI SHALL read no path under a plugin cache and SHALL download nothing.
  *(Checkpoints: `-k the_bundled_map_mirrors_the_shipped_map_byte_for_byte`,
  `-k the_gitignore_template_carries_the_shipped_runtime_block`, `-k every_runtime_asset_is_packaged`.)*

- **AC-BCL-6** *(Requirement — event)* **(the scaffold is a closed, schema-valid set):** When the CLI writes a
  greenfield workspace, the set of paths it creates under the target root SHALL be **exactly**: `CLAUDE.md`,
  `.gitignore`, `.claude/settings.json`, `.claude/foundry-project.json`, `specs/features/README.md`,
  `specs/lifecycle/README.md`, and `.foundry/README.md`. `.claude/foundry-project.json` SHALL validate against
  the **shipped** `schema/foundry-project.schema.json` read from the tree at test time (never a copy), SHALL
  carry `schema_version` as the integer `1` (the schema requires an integer ≥ 1; pinning the seed's value here
  spares a reader from inferring it), and SHALL seed exactly one `repos` entry, keyed `workspace`, carrying a
  non-empty string `path` of `.` and a `role` of `workspace` — both of which the shipped schema now
  *constrains*: `repos.<key>` requires `path`, and `role` is a closed enum whose members include `workspace`
  (verified against the shipped schema 2026-08-02), so this seed is validated by the schema rather than merely
  tolerated by it. `CLAUDE.md` SHALL carry a single stage-mode declaration line whose value is exactly
  the mode the operator selected (`lean` or `scale`) and SHALL contain no second mode declaration, and it SHALL
  carry the atomic-spec convention line naming `specs/features/`, a delimited `<!-- normative -->` region and
  `acceptance-contract.yaml`. `.gitignore` SHALL be root-anchored: every ignore line it writes for a governed
  path SHALL contain a `/` at a position other than its last character, per git's own pattern rule.
  *(Checkpoints: `-k the_created_path_set_is_exactly_the_declared_set`,
  `-k the_seeded_manifest_validates_against_the_shipped_schema`,
  `-k claude_md_carries_one_stage_mode_line`, `-k claude_md_carries_the_atomic_spec_convention_line`,
  `-k gitignore_lines_are_root_anchored`.)*

- **AC-BCL-7** *(Requirement — event)* **(the out-of-session identity half, proved equal to the shipped
  script):** When and only when `--gh-account` is supplied, the CLI SHALL first validate the account value
  against the closed charset `^[A-Za-z0-9._-]+$`, additionally refusing the values `.` and `..`, and SHALL
  **refuse** — writing nothing, naming the flag — any value outside it. It SHALL then wire commit-identity
  isolation: write the per-account include file at the **pinned path** `$HOME/.config/git/identity-<slug>`,
  carrying `user.name`/`user.email`; set — never append — the **narrow** global binding as exactly the two
  `includeif.gitdir:<canon>.git.path` and `includeif.gitdir:<canon>.git/.path` entries (`gitdir/i:` on Darwin),
  where `<canon>` is the physically-resolved target root with a trailing `/`; set repo-local
  `useConfigOnly=true` in the target; and write the `.claude/gh-identity` account marker. **Every** git-config
  write SHALL be made by invoking `git config` with the value in an **argv position** — the CLI SHALL never
  compose, template or in-place-edit git configuration text — so git's own escaping and locking apply.

  Conformance SHALL be proved **differentially**: over a throwaway `HOME` and a throwaway target repo, **all
  four** of the following produced by the CLI SHALL be **equal** to those produced by
  `bash scripts/foundry-bootstrap.sh` for the same account, author and target — (i) the `git config --global
  --list` output, (ii) the include file's **path** and its bytes, (iii) the repo-local `.git/config`'s
  `user.useConfigOnly` line, and (iv) the `.claude/gh-identity` marker file's content — so no artifact of this
  absorption escapes the proof and none can drift from the reviewed implementation it absorbs.

  When no author is supplied by flag, the CLI MAY make **at most one** `gh api user` invocation, bounded by a
  declared timeout and run with `GH_CONFIG_DIR` set to the declared jail `$HOME/.config/gh-<slug>` and with
  `GH_TOKEN`, `GITHUB_TOKEN`, `GH_ENTERPRISE_TOKEN`, `GITHUB_ENTERPRISE_TOKEN` and `GH_HOST` removed from the
  child environment (each outranks the jail's own configuration or re-points the call at another host — the
  same five the shipped script strips). It SHALL
  **compare the returned `login` against the declared account** (ASCII case-insensitive) and, on mismatch,
  discard the probe **whole** — never a partial adopt — and fall back to the prompt (or, under `--yes`, refuse
  per AC-BCL-2). It SHALL retain **only** the name and email fields; **no** probe output, in whole or in part,
  SHALL be written to any file, logged, or printed beyond the name/email the operator confirms. If the probe
  fails, times out, or `gh` is absent, the CLI SHALL degrade the same way and SHALL make no further network
  call. When `--gh-account` is absent, **no** machine-scope write SHALL occur and `git config --global --list`
  SHALL be byte-identical before and after the run.
  *(Checkpoints: `-k identity_wiring_matches_the_shipped_bootstrap_byte_for_byte` — which asserts all four
  artifacts including the `.claude/gh-identity` marker — `-k the_gh_probe_is_bounded_and_degrades` — which
  asserts the login cross-check, the `GH_CONFIG_DIR` scoping and that no probe output is persisted — and
  `-k no_account_means_no_machine_scope_write`.)*

- **AC-BCL-8** *(Requirement — event)* **(re-run is reconcile-with-drift-report; nothing edited is
  overwritten):** When the CLI runs a second time over a target it already scaffolded and nothing has changed,
  it SHALL **write nothing** and add or remove **zero paths**, report every managed path as `unchanged`, and
  exit `0`. "Writes nothing" is asserted as the **checkable** claim (the vaguer "zero bytes" is not directly
  observable): for every path under the target root, its bytes, its **inode number** and its **mtime** SHALL be
  unchanged across the second run — so a rewrite with identical content is caught rather than passing a
  content-only compare. (Inode identity is asserted on the platforms CI runs; R6 bounds the rest.) When a
  managed path's bytes differ from what the CLI would write, it SHALL report that path as `drifted`, SHALL
  leave it **byte-identical**, and a managed path that is absent SHALL be reported `create` and created.

  The **exit-code matrix is total**: the CLI SHALL exit `0` whenever no managed path is reported `drifted` —
  **regardless** of how many are reported `create` or `unchanged`, so a partial-scaffold reconcile that writes
  only missing files is a success; it SHALL exit `2` whenever **at least one** managed path is reported
  `drifted`; and it SHALL exit `1` on any refusal (AC-BCL-2's missing required flag or out-of-`choices` value,
  AC-BCL-7's malformed account slug, AC-BCL-9's non-empty target without `--existing` or refused traversing
  path), having written nothing. **Permission-floor findings are advisory and SHALL NOT change the exit code**,
  matching `[[feat-foundry-doctor-permission-floor-check]]`'s advisory posture. The run SHALL report
  **permission-floor drift** between the
  bundled map and the target's `.claude/settings.json` (unioned with `.claude/settings.local.json`, origin
  tracked) using **exactly** the class names and severity rank
  `[[feat-foundry-doctor-permission-floor-check]]` AC-DPF-8 fixes — `blanket-allow`,
  `ask-shadowed-ceremony`, `ask-shadowed`, `deny-missing`, `settings-unreadable`, `stale-plugin-path`,
  `allow-absent`, `unclassified` — and SHALL emit no other class name. The `covers` relation implementing that
  comparison SHALL agree with `tests/test_permission_floor_map.py::_subsumes` on **every** row below; if that
  sibling cannot be imported the check SHALL **fail**, never skip.

  | # | A (the broad rule) | B (the map entry) | expected |
  |---|---|---|---|
  | 1 | `Bash(a/b:*)` | `Bash(a/b/c:*)` | true |
  | 2 | `Bash(a/b/c:*)` | `Bash(a/b:*)` | false |
  | 3 | `Bash(a/b:*)` | `Bash(a/b)` | true |
  | 4 | `Bash(a/bc:*)` | `Bash(a/b:*)` | false |
  | 5 | `Bash(a/b:*)` | `Bash(a/bc:*)` | true |
  | 6 | `Bash(a/b:*)` | `Bash(a/b:*)` | true |
  | 7 | `Bash(~/.claude/plugins/cache/*/foundry/*/scripts/:*)` | `Bash(~/.claude/plugins/cache/*/foundry/*/scripts/foundry-authorize.py:*)` | true |
  | 8 | `Bash(gh pr merge --admin:*)` | `Bash(gh pr merge:*)` | false |

  *(Checkpoints: `-k a_second_run_writes_nothing`, `-k an_edited_managed_file_is_reported_not_overwritten`,
  `-k drift_classes_are_exactly_the_dpf_vocabulary`, `-k covers_agrees_with_the_map_suite_on_the_shared_table`.)*

- **AC-BCL-9** *(Invariant — ubiquitous)* **(confined, network-free, telemetry-free, and refusing an existing
  tree):** Every write the CLI performs SHALL resolve **physically** inside the target root, except **exactly
  three** machine-scope artifacts, which SHALL be the only ones and which AC-BCL-7 enumerates: the global
  `includeif.gitdir:<canon>.git.path` key, the global `includeif.gitdir:<canon>.git/.path` key, and the
  per-account **include file** at the pinned path `$HOME/.config/git/identity-<slug>`. (This bounds writes the
  CLI itself performs; AC-BCL-7's bounded `gh` child runs under `GH_CONFIG_DIR=$HOME/.config/gh-<slug>`, so any
  state that child writes lands in that declared jail and nowhere else.) Template materialization SHALL join
  each template-relative path to the target root and SHALL refuse — writing nothing and naming the offending
  entry — any path whose physical resolution lies outside it, including an absolute path, a `..` segment, and a
  symlinked ancestor.

  **Never-clobber is unconditional.** No path that **exists and differs** from what the CLI would write SHALL
  ever be written — on **any** code path, in **any** mode, with or without `--existing`, with or without
  `--yes`, in a first run or a re-run. If the target directory exists and is non-empty, then the CLI SHALL
  refuse unless `--existing` is given; interactively it SHALL additionally require the operator to type the
  directory's basename. Under `--existing` the CLI's **written set** SHALL be restricted to managed paths that
  are **absent**: every managed path that exists SHALL be reported `unchanged` or `drifted` (AC-BCL-8's
  vocabulary) and left byte-identical, in interactive and `--yes` runs alike. A pre-existing
  `.claude/settings.json` the CLI did not author SHALL be reported `drifted` and SHALL **never** be merged,
  unioned, key-wise patched, or partially rewritten — merging a foreign settings file is floor-widening by an
  unreviewed union and is out of scope; the drift report (AC-BCL-8) is the remedy.

  The import closure of `cli/**` SHALL contain no `node:http`, `node:https`, `node:net`, `node:tls`,
  `node:dgram`, `node:dns`, `node:dns/promises` or `node:worker_threads` import and no `fetch` call, so the
  only reachable egress is AC-BCL-7's bounded `gh` probe. `cli/README.md` and `--help` SHALL each state in one
  sentence that the CLI collects and transmits nothing, carrying the case-sensitive literal `no telemetry`.
  *(Checkpoints: `-k writes_are_confined_to_the_target_root`, `-k a_traversing_template_path_is_refused`,
  `-k a_non_empty_target_is_refused_without_existing`, `-k an_existing_tree_is_never_clobbered_or_merged`,
  `-k the_import_closure_carries_no_network_module`, `-k the_no_telemetry_statement_is_present`.)*

- **AC-BCL-10** *(Requirement — event)* **(one CI entrypoint, and a negative control per invariant class):**
  When `python3 -m pytest tests/test_bootstrap_cli.py -q` runs in the plugin repo, that module SHALL execute
  the package's own `node --test cli/test/` suite as a subprocess and SHALL assert its success — so the shipped
  `python3 -m pytest tests/ -q` CI step runs the Node suite with **no workflow edit**. It SHALL **fail, never
  skip**, when `node` is absent or reports a major version below `cli/package.json`'s declared `engines.node`
  floor. Both suites together SHALL assert AC-BCL-1 through AC-BCL-9 in separately named tests, driving every
  scaffold over a throwaway target directory and a throwaway `HOME` (never the real tree or the real git
  config), and SHALL include **negative controls** asserting the corresponding check FAILS on each of these
  **fourteen** mutated fixtures: (a) one rule deleted from `cli/permission-floor.json`; (b) a `postinstall` key
  added to `cli/package.json`; (c) a bare (non-`node:`, non-relative) import added to a `cli/src` module;
  (d) a template entry whose path contains a `..` segment; (e) a managed file edited between two runs
  (asserting `drifted` and byte-identity, not overwrite); (f) one written rule re-tiered relative to the
  bundled map; (g) one row of the AC-BCL-8 subsumption table flipped; (h) a `hooks` key **and** a `statusLine`
  key added to the written settings object (AC-BCL-4(c)'s closed key set); (i) `extraKnownMarketplaces`'
  `autoUpdate` flipped to `true` (AC-BCL-4(b)'s pinned literal); (j) a target tree carrying a **foreign,
  hand-authored** `.claude/settings.json` run under `--existing --yes`, asserting it is reported `drifted`,
  left byte-identical, and **not merged** (AC-BCL-9); (k) one byte flipped in the CLI's identity wiring,
  asserting AC-BCL-7's differential comparison **reddens** rather than passing on a near-match; (l) an eighth
  file written under the target root, asserting AC-BCL-6's closed path set fires; (m) a flag accepted by the
  argv parser but absent from the question table, asserting AC-BCL-2's three-way parity fires; (n) one managed
  path omitted from the preview, asserting AC-BCL-3's preview coverage fires. Without these the corresponding
  assertions are vacuous. **Every AC that states an invariant over CLI behaviour — AC-BCL-1 through AC-BCL-9 —
  therefore carries at least one control, and none is exempt**; AC-BCL-10 is itself the meta-test and AC-BCL-11
  asserts document presence, both of which fail directly rather than needing a mutation.
  *(Checkpoints: `-k the_node_suite_runs_under_pytest_and_never_skips`, `-k negative_controls_all_fire`, plus
  the whole-module green run.)*

- **AC-BCL-11** *(Invariant — ubiquitous)* **(discoverable, changelogged, and the tree stays healthy):**
  `docs/QUICKSTART.md`'s install section SHALL carry the literal `npx create-agentic-workspace` ahead of its
  `claude plugin marketplace add` line, and the added text SHALL introduce **no new version literal** into that
  file. `README.md` SHALL name `create-agentic-workspace` on its install path. `CHANGELOG.md`'s **topmost
  release section** — the text between the first `## v…` heading and the next one — SHALL carry the literal
  `create-agentic-workspace`, because the plugin is version-keyed and an adopter receives only what a release
  section carries. `python3 -m pytest tests/test_doc_claims.py -q` SHALL be green, and `/foundry:doctor` over
  the changed tree SHALL report `DOCTOR-GREEN` on its default verdict.
  *(Checkpoints: `-k quickstart_leads_with_the_npx_line`, `-k readme_and_changelog_name_the_cli`, the
  doc-claims suite, and the `DOCTOR-GREEN` regression.)*
<!-- /normative -->

## Design / notes

- **Why the CLI ships in the plugin repo, and why one package rather than an npm workspace.** The CLI's whole
  payload is derived from plugin data (the map, the runtime-gitignore block, the marketplace/plugin pins), so
  co-locating makes each single-sourceable by a committed mirror plus a byte-identity test in the same PR; a
  separate repo would make every one a cross-repo pin. A root `package.json` declaring `workspaces: ["cli"]`
  was declined: with one package it buys nothing and would put a dependency manifest at the plugin root (which
  the marketplace packer and the btb-gates security-path pattern both read) for no gain. Publishing is
  `npm publish` from `cli/` — the release cut's job.
- **Why the map is mirrored, not copied at build time.** The tarball cannot reach `../docs/`, so the bundled
  map is either a committed mirror or a generated file. A `prepack` copy would put an unreviewed file in the
  tarball *and* reintroduce the lifecycle script AC-BCL-1 forbids. A committed mirror is reviewed as a diff
  and convicted by a byte-identity test; R1 prices it.
- **Why the identity half is proved differentially rather than re-specified.** The `includeIf` narrow binding
  is not simple — the Darwin `gitdir/i:` prefix, the two-entry narrow form, the same-key SET (never `--add`),
  the superseded-broad-rule migration — and is already reviewed and tested under
  `[[feat-foundry-commit-identity-isolation]]` and its gitdir-scope sibling. Re-deriving it in Node from prose
  is how the two drift; asserting **equal artifacts for equal inputs** makes drift a RED test rather than a
  review responsibility.
- **Why `autoUpdate: false` is pinned rather than left to the default.** `CLAUDE.md` § *Standing versions &
  drift control* rules 2–3 forbid a floating pin and make every version change a deliberate reviewed edit; an
  auto-updating marketplace is a floating pin by another name. More sharply, the floor's rules are
  **version-wildcarded** (`Bash(~/.claude/plugins/cache/*/foundry/*/scripts/…:*)` — AC-BCL-8's table row 7),
  so an allow granted once applies to **every future plugin version the cache receives**. With
  `autoUpdate: true` the operator's single trust acceptance becomes a standing grant over code they have not
  seen; with `false`, an update is an act they take. `installLocation` is omitted for the reason the key set
  is closed at all: an extra key is a surface the review never priced.
- **The zero-dependency rule costs prompt polish, and that is the trade taken.** `@clack/prompts` /
  `inquirer` are what a modern `create-*` wizard reaches for; this CLI uses `node:readline/promises` and plain
  writes instead, accepting a plainer terminal for a transitive supply-chain surface of exactly zero at the
  moment it writes an adopter's permission floor.
- **The drift report has two families and one vocabulary each.** File-level drift uses
  `create`/`unchanged`/`drifted` (the `terraform init` posture); permission-level drift borrows AC-DPF-8's
  eight classes verbatim so an operator who has seen the doctor's report reads the wizard's without relearning
  it, and so a third matcher cannot quietly disagree with the other two.
- **Source.** ER `intake/er-onboarding-wizard-and-permission-floor.md` — atom 1 of its decomposition, plus the
  Phase-0 wizard steps 1–7 and the class A–E classifier catalogue behind the map this CLI applies.

## Clarifications

- **Q: Bundled template, or a degit-style pull of `agentic-handbook`?** **Bundled — resolved.** Inspected
  2026-08-02: `agentic-handbook/context/` holds only a README pointing at the **plugin's** `context/` kit as
  canonical, while today's `scripts/foundry-bootstrap.sh` clones the whole handbook (unpinned default branch),
  dragging the tutorial corpus into a fresh workspace and requiring network. What a new workspace needs is the
  seven files AC-BCL-6 closes, so there is little to duplicate: the handbook stays the *narrative* reference,
  the plugin's kit stays the authoring templates, the CLI bundles only the seed — which also satisfies the
  ER's no-network bound and pins the seed to the CLI release. R2 records the resulting cross-repo drift.
- **Q: Is a separate repo structurally required for the npm package?** **No.** Publishing from a subdirectory
  is ordinary npm practice, `cli/` carries its own manifest, and no plugin-side machinery reads or is confused
  by it (verified: no `package.json` exists anywhere in the plugin tree today, and
  `[[feat-foundry-permission-floor-map]]` AC-PFM-2's closed world enumerates `scripts/` non-recursively).
- **Q: Which test harness?** **Node's built-in `node:test`, driven from one pytest module by subprocess** —
  the lightest that reaches CI unchanged. Verified: the plugin ships no `package.json` and no Node harness,
  while `.github/workflows/ci.yml` already provisions Node 22 *and* runs `python3 -m pytest tests/ -q`, so a
  `tests/test_bootstrap_cli.py` shim makes the Node suite CI-runnable with **zero workflow edits** — which
  matters, because `.github/workflows/**` is contract-denied. A third-party runner (vitest, jest) would also
  breach AC-BCL-1's zero-dependency rule.
- **Q: Why `engines.node >= 22.0.0` against the `>=20` row in `docs/standing-versions.md`?** That row is
  scoped to the Playwright runner. Node 20 reached end-of-life 2026-04-30, so a floor of 20 ships an
  unmaintained runtime; `docs/QUICKSTART.md` already states **node 22+** and CI runs 22. The matrix row owed
  is a workspace-side edit outside this `target_repo` — see R3.
- **Q: Does the wizard also wire the `gh` identity jail (`.envrc`, the direnv global lib)?** **No —
  deliberately out of scope.** That half of `foundry-bootstrap.sh` installs into `$HOME/.config/direnv/lib/`
  under its own reviewed atom; duplicating a machine-scope installer in Node is unjustified. The wizard writes
  the workspace-side `.claude/gh-identity` marker and stops.

## Out of scope / non-goals

- **The attach-existing / create-new governed-repo flow** (ER atom 8, `wizard-attach-repo-flow`) — its own atom.
  It reconciles through `[[feat-foundry-workspace-repo-verbs]]`'s **AC-WRV-10** `reconcile(...)` seam; this atom
  seeds the manifest with the `workspace` self-entry only and writes no `repos` row for a governed repo.
- **Slimming `/foundry:init`** (ER atom 3) and **retiring or editing `scripts/foundry-bootstrap.sh`** — both are
  their own atoms; `scripts/**` is contract-denied here, so the shell script keeps working unchanged (R4).
- **Publishing to npm.** No registry credential, no `npm publish`, no release automation: the release cut owns
  the publish step, as it owns the plugin tag.
- **Editing `docs/permission-floor.json`, the schema, any hook, skill, gate or workflow.** The map is mirrored;
  the schema is *validated against*, never changed.
- **Reading user-level or enterprise settings**, and **auto-fixing any drift** — the report names the remedy and
  stops, matching the doctor probe's posture.
- **Merging into a foreign `.claude/settings.json`.** A settings file the CLI did not author is reported
  `drifted` and left alone (AC-BCL-9). A merge would widen an adopter's floor by an unreviewed union of two
  rule sets, which is exactly the outcome the atom exists to prevent; the drift report is the whole remedy.
- **Emitting `statusLine` or `sandbox` configuration — an amendment to the ER's atom-1 absorption list.** The
  ER assigns atom 1 "git identity, settings, statusLine, sandbox". This spec absorbs the first two and
  **drops the last two entirely**: they are **not emitted by this CLI and are not relocated to another
  writer** (AC-BCL-4(c) forbids the keys outright). Both are grants-adjacent configuration whose right moment
  is in-session, after the operator has trusted the workspace and can see what they change; writing them
  pre-session would put unreviewed configuration inside the same file that carries the floor. Whether they
  reappear at all is left open to the in-session slimming atom (ER atom 3); nothing here promises they will.
- **Windows-specific path and shell behaviour** beyond what Node's own path handling gives — see R6.

## Residuals

- **R1 — The bundled map is a committed duplicate (maintenance, convicted).** `cli/permission-floor.json` will
  go stale the moment `docs/permission-floor.json` changes. **Bound and intended:** AC-BCL-5's byte-identity
  test turns that into a RED build in the same PR, which is the loud, one-line, reviewed failure this corpus
  already accepts for `standing-versions.md` ⟷ its lock. The same applies to the runtime-gitignore block.
- **R2 — The bundled seed can drift from `agentic-handbook`'s own root files (coverage gap, cross-repo).** No
  test can span the two repos, so a change to the handbook's `CLAUDE.md` shape will not redden anything here.
  **Bound:** the seed is deliberately *minimal* (seven files, none of them handbook prose), and the handbook
  remains the narrative reference rather than a copied artifact. **Named follow-on for the operator:** a
  handbook-side atom that either adopts this seed as its source or asserts the shapes agree.
- **R3 — Two `docs/standing-versions.md` rows are owed and cannot be written here (process, tracked).** The CLI
  needs a **Node runtime floor `>=22.0.0`** row and a **`create-agentic-workspace` published-package** row, each
  with a research timestamp (researched 2026-08-02). That file lives in the *workspace*, not in `agentic-foundry`,
  so it is outside this atom's `target_repo` — the same shape `[[feat-foundry-workspace-repo-verbs]]` R9 records
  for the missing git row. **Bound in-repo:** the package has **zero** third-party dependencies, so there is no
  range to pin, and `cli/package.json` carries `foundry.pins_researched` (AC-BCL-1) so the artifact states its
  own staleness. Flagged for the operator.

  **A third item is a deliberate, recorded exception to rule 2 (no floating pins): the documented invocation
  `npx create-agentic-workspace` is unpinned**, resolving to the registry's `latest` dist-tag. **Grounding:**
  every prior-art `create-*` CLI documents the unpinned form (`create-next-app`, `create-vite`, `create-t3-app`,
  cc-sdd's `npx cc-sdd@latest`, Spec-Kit's `uvx` invocation), because a bootstrap command is the one place
  where the *newest* scaffold is what the user wants and there is no prior state to break; a pinned README
  line goes stale for a pre-adoption reader with nothing installed. **It is the launch pad, not a build
  input** — rule 2 exists so a *reproducible build* never drifts under us, and nothing this CLI emits is an
  input to another build. **Compensating surfaces, all in-artifact:** the preview shows every file and
  capability before writing (AC-BCL-3); the tarball's own version is pinned by `foundry.plugin_version` +
  `foundry.pins_researched`; `autoUpdate: false` plus the exact marketplace ref keep the *plugin* — the thing
  that actually executes later — pinned whichever CLI version scaffolded it; and the `/foundry:doctor` floor
  check (AC-BCL-4(e)) verifies the result against a non-npm artifact. Recorded so a future audit reads a
  priced exception, not an oversight.
- **R4 — Two implementations of the identity wiring exist until one is retired (duplication, bounded).**
  `scripts/foundry-bootstrap.sh` keeps its `--gh-account` path (contract-denied here). **Bound:** AC-BCL-7's
  differential equality makes divergence a RED test rather than a silent fork; retirement is the named
  bootstrap-slimming follow-on.
- **R5 — This atom's new surfaces do not route to the floor-#3 security-path CI check (process gap, inherited).**
  Verified against `.github/workflows/btb-gates.yml`: the `security-path` pattern's `<dependency manifests>` arm
  is the only plausible match for `cli/package.json`, and nothing matches `cli/src/**` — the code that decides
  what an adopter's workspace declares. **Bound:** this atom's own security review is happening, and the CLI
  grants nothing (the trust dialog does). **Named follow-on:** the light-lane widening already tracked by
  `[[feat-foundry-permission-floor-map]]` R7 SHALL additionally add an anchored `^cli/` arm, OR-append only,
  with negative-control rows — not done here because `.github/workflows/**` is contract-denied.
- **R6 — Platform coverage is asserted only where CI runs it (epistemic).** The suite runs on Linux; the Darwin
  `gitdir/i:` branch of AC-BCL-7 is exercised only by the shipped script's own tests, and Windows is untested
  entirely — a `create-*` CLI will nonetheless be run there. **Bound:** every path join goes through Node's own
  `path` module, the differential test pins the Linux branch exactly, and the failure mode on an untested
  platform is a visibly wrong config, not a silent grant. Stated rather than implied as coverage.
- **R7 — The `covers` matcher is a third implementation of a documented-fragile relation (epistemic).** The
  platform's Bash rules are prefix matches whose matching semantics this atom cannot execute
  (`[[feat-foundry-permission-floor-map]]` R1/R2). **Bound:** the shared table makes the three implementations
  agree on the vocabulary they share; the report grants and blocks nothing; and a fold error costs a noisy or
  missing advisory line. The outer control remains the operator's terminal test pass (`CLAUDE.md` § *Delivery
  sign-off*) — a **practice, not a control**, from which no coverage is claimed.
- **R8 — Trust-dialog re-prompt semantics for a *changed* allow set remain unverified, and this atom was
  assigned them (epistemic, partially discharged).** `[[feat-foundry-permission-floor-map]]` R10 assigns the
  observation to this CLI: whether adding rules to an **already-trusted** workspace re-prompts, silently
  activates, or requires re-trust. This atom carries the *detector* (the map's `generated_for_plugin_version`
  is bundled and the re-run reports floor drift) but **cannot execute the dialog**, so the answer is recorded at
  the live-seam walk and the operator's own test pass, not by a checkpoint. Named so it is not read as closed.
- **R9 — Nothing in this atom attests the published artifact; the supply-chain half belongs to the release cut
  (security gap, assigned).** Every control this spec asserts is a build-time check over the source tree
  (Security posture): a compromise of the npm publish path ships a package none of them ever saw, and that
  package writes an adopter's permission floor. **Bound, partially:** the two adopter-side checks are the
  trust dialog (blind to *dropped* `deny`/`ask` rules, and long enough to skim) and the `/foundry:doctor`
  floor check against the cache-installed plugin's own map, which did not travel through npm — the exit line
  directs the operator to run it (AC-BCL-4(e)). **Assigned follow-on, not done here (this atom does not
  publish, and `.github/workflows/**` is contract-denied):** the release cut SHALL publish with **`npm publish
  --provenance`** from a CI environment able to issue the attestation, and SHALL add an **`npm audit
  signatures`** verification step over the published package. Recorded as an open security residual so it is
  not read as covered by the checks above.

## Changelog

- v1.1 One remediation round against the consolidated four-lens review (2026-08-02). Every tree claim below
  was re-verified against `agentic-foundry` `main` at that date. No AC was added, split or renumbered — all
  nine Blocks fold into existing lettered clauses, keeping the count at eleven.

  **Security Blocks.** *(1)* **AC-BCL-4(c)** closes the written file's **top-level key set** to
  `{permissions, extraKnownMarketplaces, enabledPlugins}` (plus named `//`-comment keys from a closed
  template-declared list) and `permissions`' keys to `{allow, ask, deny}`, and forbids `statusLine`, `hooks`,
  `sandbox`, `apiKeyHelper`, `env`, `mcpServers`, `additionalDirectories` at **any** nesting level. **ER-scope
  delta, stated in the clause and in *Out of scope*:** `statusLine` + `sandbox` are dropped from the CLI and
  **land nowhere** — not emitted at all, per the trust model — so **the ER's atom-1 absorption list is
  amended**, their eventual home left open to ER atom 3. Control **(h)**. *(2)* **AC-BCL-4(b)** pins the
  literal `{"source": {"source": "github", "repo": <marketplace_repo>}, "autoUpdate": false}` —
  `installLocation` absent, every other `source.source` forbidden, `autoUpdate` never `true`. **AC-BCL-1's
  undefined "corresponding value" is fixed** by naming each pin's source path (`marketplace_name` ←
  top-level `name`; `marketplace_repo` ← `plugins[0].source.repo`; `plugin_name` ← `plugins[0].name`;
  `plugin_version` ← `plugins[0].version`). `autoUpdate: false` is grounded in `CLAUDE.md` § *Standing
  versions* rules 2–3 plus the floor's **version-wildcarded** cache rules, which make auto-update a standing
  grant over unseen plugin versions. New `-k the_marketplace_entry_is_the_pinned_literal`; control **(i)**.
  *(3)* **Supply-chain honesty rewrite.** *Security posture* + new **R9** state that every in-tarball control
  is **build-time** and does **not** survive a publish compromise, and name the two adopter-side checks with
  their limits — the trust dialog (**blind to dropped `deny`/`ask` rules**; 40 `allow` rows of 56 entries,
  inspected 2026-08-02) and the `/foundry:doctor` floor check against the **cache-installed plugin's own map,
  which did not travel through npm**. **AC-BCL-4(e)** now requires the exit line to carry `/foundry:doctor`
  and direct the operator to run it post-install; R9 assigns `npm publish --provenance` + `npm audit
  signatures` to the release cut. *(4)* **Never-clobber is unconditional** (**AC-BCL-9**): nothing that exists
  and differs is written on any path, in any mode; `--existing` gets its own written-set clause (**absent**
  managed paths only); a foreign `.claude/settings.json` is reported `drifted` and **never merged** (merge =
  floor-widening — now an explicit *Out of scope* bullet). Control **(j)**.

  **Steel-man Blocks.** *(5)* **AC-BCL-7's differential proof widens to four artifacts** — global config
  listing, include-file **path and bytes**, repo-local `.git/config` `user.useConfigOnly` line, and
  `.claude/gh-identity` content — all byte-equal to the shell baseline; the include-file path is **pinned**
  (`$HOME/.config/git/identity-<slug>`) and **AC-BCL-9 now enumerates three machine-scope artifacts, not
  two** (the include file was the escapee). *(6)* Controls **(k)** flipped-byte identity wiring and **(l)** an
  eighth stray file were added as directed; rather than record exemptions, cheap controls **(m)** (flag
  accepted but absent from the table) and **(n)** (managed path omitted from the preview) close AC-BCL-2 and
  AC-BCL-3, so **no behavioural AC is exempt** — fourteen controls. *(7)* **AC-BCL-1** forbids any `import()`
  whose specifier is not a string literal. *(8)* `node:dns` + `node:dns/promises` added to **AC-BCL-9's**
  banned set.

  **Rubric Block.** *(9)* Uncovered clauses gained `-k settings_json_key_set_is_closed` (AC-BCL-4(c), merged
  with control (h)), `-k claude_md_carries_the_atomic_spec_convention_line` and
  `-k gitignore_lines_are_root_anchored` (AC-BCL-6); `-k an_existing_tree_is_never_clobbered_or_merged`
  carries Block 4.

  **Risks folded in.** AC-BCL-7: the `gh api user` probe compares the returned `login` against the declared
  account (whole-discard on mismatch) **and** runs under the scoped `GH_CONFIG_DIR` with the five token/host
  variables stripped; only name+email are retained and **no probe output is persisted**; `--gh-account` is
  validated against `^[A-Za-z0-9._-]+$` (`.`/`..` refused); every git-config write goes through `git config`
  **argv**, never templated text. AC-BCL-3: the dry-run proof runs over a **populated, drifted** fixture.
  AC-BCL-8: **"zero bytes" is replaced with the checkable claim** — bytes **plus inode plus mtime** unchanged
  (choice stated; R6 bounds the platform) — and the **exit-code matrix is completed**: `0` unless some path is
  `drifted` (regardless of `create` rows) → `2`, refusals → `1`, floor findings **advisory and
  exit-code-neutral**. AC-BCL-2 gains the optional **`choices`** field with out-of-choices refusal. R3 records
  the **unpinned `npx` invocation** as a deliberate, prior-art-grounded exception to the no-floating-pins rule
  with its compensating surfaces; Design/notes names the **zero-dependency vs. `@clack/prompts` polish trade**;
  AC-BCL-6 pins `schema_version: 1`. **One directed change was declined with reason:** re-surfacing the
  imports checkpoint to `file:cli/**/*.mjs` would fail two authorize-time floors (ER #77 surface⊆scope and
  ER #179 allowed-path grounding both string-match a surface against `allowed_paths`, and it would leave
  `cli/bin/create-agentic-workspace.mjs` unsurfaced), so the exact entrypoint surface is kept and the
  `cli/**/*.mjs` **assertion scope** is recorded in the checkpoint's comment instead.

  **Two findings are REFUTED against the current tree, and are recorded rather than acted on.** Verified in
  `agentic-foundry` `main`'s `schema/foundry-project.schema.json` on 2026-08-02: `repos.<key>` carries
  `required: ["path"]`, and `role` is a **closed** `enum` of `["product", "handbook", "infra", "app",
  "workspace"]`. **(i)** The prior-art lens's Risk that "`role: workspace` may not survive the future enum" is
  **MOOT** — the enum shipped *with* `workspace` in it, and its own `description` names that member as "the
  self-entry a freshly seeded manifest may carry for the workspace root itself", i.e. this exact seed. **(ii)**
  The steel-man's **Risk 8** ("`role` is ungrounded against the `kind` convention") is **REFUTED**: `role` **is**
  the shipped governance discriminator — the schema's own description calls it a "Closed governance vocabulary
  for this repo's place in the control plane" and directs free-form prose to the sibling `description` field,
  which exists precisely as "the home for anything that used to be written into `role` before `role` closed".
  `kind` remains a separate, unconstrained descriptive field. AC-BCL-6 now says so inline, and the contract's
  "post-RRF" comment on the schema checkpoint was **correct as written** and is retained. **Structural record
  corrected:** the spec carries **ONE** delimited normative region (a single `<!-- normative -->` /
  `<!-- /normative -->` pair spanning AC-BCL-1 through AC-BCL-11), **not three**; any review note asserting
  three regions was reading the AC bullets, not the delimiters.

- v1.0 Draft. Ship the ER's Phase-0 pre-session wizard as `create-agentic-workspace` — a zero-dependency,
  no-lifecycle-script Node package under `cli/`, invoked `npx create-agentic-workspace` — that runs the wizard
  steps, previews every file **and** every capability before writing, emits the plugin's reviewed
  three-tier permission map verbatim into the new workspace's committed `.claude/settings.json` alongside
  `extraKnownMarketplaces`/`enabledPlugins`, absorbs `foundry-bootstrap.sh`'s out-of-session `includeIf`
  identity wiring under a differential-equality proof, scaffolds a seven-file schema-valid workspace, and
  reconciles idempotently with a drift report that reuses
  `[[feat-foundry-doctor-permission-floor-check]]`'s finding vocabulary and subsumption table. It never runs
  `claude`, never accepts trust, reaches no network beyond one bounded optional `gh api user` probe, and
  collects nothing. The Node suite reaches CI through one pytest shim, so no workflow is touched. Realizes
  atom 1 of `intake/er-onboarding-wizard-and-permission-floor.md` (2026-08-01).
