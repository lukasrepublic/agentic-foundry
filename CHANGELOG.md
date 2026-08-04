# Changelog

All notable changes to Agentic Foundry are documented here (SemVer).

> **Release discipline.** `claude plugin update` is **version-keyed** — adopters only
> receive changes when the plugin `version` in `.claude-plugin/plugin.json` is bumped. Every
> meaningful change therefore bumps the version and lands under a dated release section below.
> Every release is itself specced, authorized, floor-gated, and certified through the tool
> (Foundry is built with Foundry), and each section records its security-review disposition.

## Unreleased

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
