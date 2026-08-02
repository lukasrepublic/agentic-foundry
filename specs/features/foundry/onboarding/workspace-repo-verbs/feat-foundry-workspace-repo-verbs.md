# The governed-repo fleet verbs: sync / status / foreach / validate  (feat-foundry-workspace-repo-verbs)

> **Human-readable intent.** `[[feat-foundry-repo-registry-formalization]]` formalizes the manifest and
> ships a read-only report that **declares and reports** — presence, gitignore pairing, origin match.
> Nothing yet **acts**: a `not-cloned` row is still cloned by hand, a fleet is still visited repo by
> repo to ask "what branch, how far behind, is it dirty", and a checkout on disk that the manifest
> never declared is noticed by nothing at all.
>
> **This atom ships the fleet verbs over that registry** — the triad the repo-of-repos category
> converged on, plus the round-trip validator: **`sync`** (idempotent reconcile: clone the absent rows
> that declare a remote, fetch the present ones whose origin matches, **report** diverged / dirty /
> origin-mismatch without touching them), **`status`** (one line per entry: present · origin · branch ·
> ahead/behind · dirty), **`foreach`** (fail-collecting fan-out over the present repos), **`validate`**
> (the manifest ⟷ reality ⟷ gitignore round-trip, **including the reverse direction the registry atom
> deferred by name**: a checkout under the workspace root with no `repos{}` entry).
>
> **Two properties are load-bearing, and both are normative rather than prose.** Every mutation this
> atom can perform is `git clone` or `git fetch` — no checkout, reset, merge, rebase, pull, push or
> clean exists in its vocabulary, so an existing tree is never rewritten and drift is **surfaced, never
> fixed** (the `id-drift` posture). And clone/fetch are this control plane's **first network egress**,
> bounded to remotes an operator declared in a committed manifest, re-validated at the boundary, and
> carrying the corpus's own reviewed git-hardening standard on every child.
>
> The wizard's attach flow (Wave 3, `wizard-attach-repo-flow`) must reconcile through **the same code
> path**, so the reconcile logic is an importable callable and the CLI is a renderer over it. The CLI is
> this atom's surface; the wizard is not.

## Prior art / industry grounding

**The verb set is the category's, not an invention.** The ER
(`intake/er-onboarding-wizard-and-permission-floor.md`, researched 2026-08-01) records the verdict —
the control plane needs "the universal triad + validate" — but it records it at *atom* granularity and
carries **none** of the subcommand-level facts the departures below turn on. Those are therefore cited
here from primary sources directly (consulted 2026-08-02):

- **Google `repo`** — Repo command reference, `https://source.android.com/docs/setup/reference/repo`
  (upstream `git-repo`, `https://gerrit.googlesource.com/git-repo`, `HEAD`). `repo sync` downloads new
  changes **and updates the working files** of each project; `repo sync --force-sync` will overwrite an
  existing git directory; `repo forall [<project>…] -c <command>` evaluates the command **through
  `/bin/sh`**, passing later arguments as shell positional parameters. `repo status` is the per-project
  one-liner; `manifest.xml` carries `name` / `path` / `remote` / `revision`.
- **vcstool** — `https://github.com/dirk-thomas/vcstool` (README, `master`). `vcs import` materializes a
  `.repos` file, `vcs pull` runs the underlying VCS pull in each repo, `vcs status` reports, and
  **`vcs export` reconstructs a `.repos` file *from* the tree** — the named prior art for the reverse
  direction that `[[feat-foundry-repo-registry-formalization]]`'s *Out of scope* defers to this atom.
- **`meta`** (`https://github.com/mateodelnorte/meta`) — `meta git update` / `meta exec`; **myrepos**
  (`https://myrepos.branchable.com/`) — `mr update` / `mr status` / `mr run`: the same three moves over
  a committed manifest. **`git submodule foreach`** — git-submodule(1),
  `https://git-scm.com/docs/git-submodule`: "Evaluates an arbitrary **shell command** in each checked
  out submodule."
- **Posture and exit contract** — ArgoCD's registration rule (the manifest is the only truth;
  declaring and reconciling are separate acts; drift is surfaced before anything is made to match),
  and the advisory tri-state (0 clean / 1 error / 2 findings) of `terraform plan -detailed-exitcode`
  (`https://developer.hashicorp.com/terraform/cli/commands/plan`), already the shipped convention in
  `scripts/foundry-config.py`. **Fail-collecting fan-out** is `make -k`'s and `xargs`'s documented
  shape; clig.dev (`https://clig.dev/`) is the argument-handling source, and the corpus's own
  `--`-guarded, `shell=False` rule is pinned by **AC-RRF-6(vi)**.

**Three deliberate departures from the category, each stated.** (1) `repo sync` updates each project's
**working files** (and `--force-sync` will overwrite a git directory); `vcs pull` runs the underlying
pull. This atom **never updates an existing tree** — `fetch` moves remote-tracking refs and nothing
else, and a diverged or dirty child is a reported row. The control plane governs repos that evolve
under their own origins and their own gates; advancing one from here would move code the merge floor
never saw. (2) `repo forall -c` and `git submodule foreach` run their command **through a shell**. This
atom runs argv with no shell at all, trading `cd x && git log | head` ergonomics for the removal of a
shell-injection surface fed by a committed file. (3) vcstool's per-entry VCS `type` is not carried, for
the reason the sibling recorded: this control plane is git-only.

**The git-hardening standard is the corpus's own, adopted whole rather than re-derived.**
`[[feat-foundry-leak-scan-ls-remote-sink]]` (delivered) established, by execution rather than
inference, that a git invocation which touches an untrusted repository or an untrusted remote must
carry per-invocation `-c` overrides **and** a subtracted environment, and that the verification must
assert an **absent observable side effect** rather than the presence of an override string in argv —
an argv assertion stays green against an implementation that adds the flag and drops the anchoring.
Its shipped constants (`_LS_REMOTE_HARDENING_SET`, `_LS_REMOTE_SINK_ENV_REMOVED_VARS`,
`_HARDENED_GIT_CONFIG`) and its fixture shape (`tests/test_leak_scan_remote_forms.py`) are this atom's
consensus: **there is no design fork to research here, only a standard to apply at three more sinks.**
The credential half is `scripts/foundry-bootstrap.sh:251-271`'s reviewed reasoning verbatim —
`GIT_TERMINAL_PROMPT=0` bounds only the *terminal* prompt, so the clone there also carries
`-c credential.helper= -c core.askPass=`; that same comment records, as a residual, that it makes **no
claim about ssh's own prompt class**, which is why this atom adds OpenSSH's documented `BatchMode=yes`
(ssh_config(5), `https://man.openbsd.org/ssh_config#BatchMode`).

**The admitted remote forms are the corpus's reviewed allow-list, not a new one.**
`url_is_allowed_form` in `scripts/foundry-prepublication-leak-scan.py` (precedence order fixed by
`[[feat-foundry-leak-scan-scp-remote-form]]`: transport-helper syntax excluded **first and
generically**, then scheme dispatch, then the scp-like rule) admits `https://`, `ssh://` and
`[user@]host:path`, plus a narrow absolute-local-path escape hatch, and rejects everything else —
including `git://`, which is asserted rejected by the shipped fixture
`tests/test_leak_scan_remote_forms.py::test_scheme_dispatch_precedes_scp_rule`. This atom adopts that
set unchanged rather than widening it (see *Clarifications*).

## Security posture

**Network egress is this atom's new seam.** Before it, the control-plane scripts read files and ran
config-read-only git plumbing; `git clone` and `git fetch` are the first commands that open a socket.
AC-WRV-3, AC-WRV-11 and AC-WRV-12 bound it: an admitted remote form, declared in the committed
manifest, re-validated here, reached with submodule recursion off and the transport layer pinned.

**Every manifest value is untrusted input, and the schema floor is not a runtime control.**
`[[feat-foundry-repo-registry-formalization]]`'s AC-RRF-2 shape floor is enforced by
`scripts/foundry-config.py check`, which is **advisory and may never have been run** against the
manifest in front of this tool — nothing invokes it as a precondition. **AC-WRV-3's own re-validation
is therefore the sole runtime control**, and it re-checks shape, remote form and path confinement at
the boundary regardless of what the registry row claims. No manifest value reaches an option position:
clone and fetch are fixed argv with `--` before every manifest-derived positional, and commands that
run *inside* an existing checkout resolve the path physically and pass it as the child's `cwd`.

**Redaction is inherited, not re-implemented.** All output passes through the single sanitizing
emission sink `scripts/foundry_repo_registry.py` ships under AC-RRF-7. That module is **denied scope
here**, so the reuse is structural: this atom cannot weaken the sink, cannot fork the classifier, and
a second redaction implementation cannot drift from the first — the same move the sibling made by
denying `scripts/foundry-config.py`.

**No gate reads any of this.** The exit codes are advisory. `scripts/foundry-authorize.py`,
`scripts/foundry_authz.py`, `scripts/foundry-wt`, `schema/**`, `hooks/**` and `.github/workflows/**`
are contract-denied; the authorization floor, the merge floor and the dispatch resolver are untouched.

<!-- normative -->
## Terminology (normative)

- **an admitted remote form** — a value for which the corpus's reviewed predicate
  `url_is_allowed_form(v) or _is_local_path_escape_hatch(v)` — both defined in
  `scripts/foundry-prepublication-leak-scan.py`, composed there in `_resolve_configured_remote_url` —
  returns true: an `https://` URL, an `ssh://` URL, an scp-form `[user@]host:path` whose authority
  segment matches that module's ASCII-anchored pattern, or an **absolute** local filesystem path
  resolving to an existing directory. Every other value is **not** admitted — including `http://`,
  `file://`, `git://`, any relative path, and **any** transport-helper form `<name>::<rest>` (`ext::`,
  `fd::`, and every other name, excluded there by rule rather than by enumeration).
- **the hardening set** — the fixed, **non-manifest-derived** `-c` overrides, in this order:
  `credential.helper=`, `core.askPass=`, `core.fsmonitor=`, `core.sshCommand=ssh -o BatchMode=yes`,
  `protocol.ext.allow=never`, `protocol.allow=never`, `protocol.https.allow=always`,
  `protocol.ssh.allow=always`, `protocol.file.allow=always`,
  `fetch.recurseSubmodules=no`, `submodule.recurse=false`.
- **the sink environment** — the ambient environment with `GIT_TERMINAL_PROMPT=0` set and with
  `GIT_DIR`, `GIT_WORK_TREE`, `GIT_CONFIG_GLOBAL`, `GIT_CONFIG_SYSTEM`, `GIT_CONFIG_COUNT`,
  `GIT_CONFIG_PARAMETERS`, `GIT_SSH`, `GIT_SSH_COMMAND`, `GIT_ASKPASS` and `SSH_ASKPASS` **removed**
  (the tuple `[[feat-foundry-leak-scan-ls-remote-sink]]` pins as `_LS_REMOTE_SINK_ENV_REMOVED_VARS`).
- **a network-capable invocation** — any `git clone` or `git fetch` this tool executes.

## Acceptance criteria

- **AC-WRV-1** *(Invariant — ubiquitous)* **(clone and fetch are the entire mutation vocabulary; drift
  is surfaced, never fixed):** Across every invocation of every verb, the git commands
  `scripts/foundry_repo_fleet.py` executes SHALL be drawn only from the closed set `git clone`,
  `git fetch`, `git config --get`, `git rev-parse`, `git rev-list`, `git status --porcelain`,
  `git check-ignore` and `git ls-files`. No invocation SHALL run `checkout`, `switch`, `reset`,
  `merge`, `rebase`, `pull`, `push`, `clean`, `stash`, `branch`, `remote`, `submodule`, `gc`, or any
  command carrying `--force`, `-f`, `--hard` or `--force-sync`. Every invocation SHALL be a fixed argv
  executed **without a shell**, SHALL begin with the hardening set of AC-WRV-11 before its subcommand,
  and SHALL place `--` before every manifest-derived positional; no manifest-derived value SHALL appear
  in an option position, and a command that operates inside an existing checkout SHALL be run with the
  physically-resolved path as the child's `cwd`, never as `git -C <path>`. Consequently, when any verb
  runs over a checkout that is dirty, diverged from its upstream, or whose origin does not match the
  declared `remote`, that checkout's `HEAD` commit, index, working-tree bytes and git config SHALL be
  unchanged by the run, and the condition SHALL appear as a reported row.

- **AC-WRV-2** *(Requirement — event)* **(`sync` reconciles by the registry's rows, and fetches only a
  matching checkout, by its declared remote):** When `sync` runs, it SHALL consume the per-entry
  classification produced by `scripts/foundry_repo_registry.py` — which it SHALL NOT re-derive — and
  act by `path_status`: **`not-cloned`** → clone the declared `remote` to the declared path;
  **`present`**, a git checkout, **and** an `origin` classification of exactly **`match`** → fetch;
  every other row — **`dangling`**, **`outside-workspace`**, a present path that is not a git checkout,
  and **any** present row whose `origin` is not `match` (including `mismatch`, `undeclared` and
  `not-a-checkout`) → **no git command at all**, each reported as its own row in the sibling's
  vocabulary with a remedy and counted as a finding where the sibling counts one. The fetch invocation
  SHALL name the **declared** `remote` explicitly, as a positional after `--`, and SHALL NOT rely on
  the checkout's configured `origin` to select what is contacted.

- **AC-WRV-3** *(Requirement — unwanted)* **(the boundary re-validates every row and refuses before it
  reaches the network):** Before any network-capable invocation for a row, `sync` SHALL re-derive, from
  the row's own values and independently of any classification the row carries, all of: the `remote` is
  an admitted remote form, decided by **loading that predicate from
  `scripts/foundry-prepublication-leak-scan.py`** with the shipped
  `importlib.util.spec_from_file_location` device resolved from this module's own directory (never
  from `--root`), **never by re-implementing it**; the `remote` neither begins with `-` nor contains a
  C0 or C1 control character (`U+0000`–`U+001F`, `U+007F`–`U+009F`); and the target path's
  **physical** resolution — or
  that of its nearest existing physical ancestor — lies inside the physical workspace root. If any
  check fails, or the clone target already exists, then `sync` SHALL emit a named refusal row for that
  entry, SHALL execute **no** git command for it, and SHALL continue with the remaining rows; a refusal
  SHALL be a finding for exit-code purposes and SHALL never be reported as a successful clone. A
  `repos.<key>` **key** that begins with `-` or carries a control character SHALL NOT alter any decision
  above, SHALL never reach an argv option position, and SHALL be emitted only through AC-WRV-8's sink.
  For an accepted row the clone invocation SHALL be exactly `git <hardening set>
  clone --no-recurse-submodules -- <remote> <path>` under the sink environment.

- **AC-WRV-4** *(Requirement — event)* **(`status` is one honest line per entry):** When `status` runs,
  it SHALL emit **exactly one row per `repos.<key>` entry**, carrying the fields `present`,
  `origin` (the sibling's `match` / `mismatch` / `undeclared` / `not-a-checkout` vocabulary), `branch`,
  `ahead_behind` and `dirty`. `branch` SHALL be the value of `git rev-parse --abbrev-ref HEAD`, with a
  detached HEAD reported as the named value **`detached`**; `ahead_behind` SHALL be derived from
  `git rev-list --left-right --count` against the branch's tracked upstream, with **no** tracked
  upstream reported as the named value **`no-upstream`** and **never** as `0/0`; `dirty` SHALL be true
  exactly when `git status --porcelain` is non-empty. For an entry that is not `present`, or where the
  oracle is unavailable, every field it cannot derive SHALL carry the named value **`unknown`** — no
  field SHALL ever be rendered blank, absent, or defaulted to a clean value.

- **AC-WRV-5** *(Requirement — event)* **(`foreach` fans out over present repos, fail-collects, and
  owns its children's output):** When `foreach` runs with a command supplied after `--`, it SHALL
  execute that command as **argv with no shell**, once per entry whose `path_status` is `present`, with
  the entry's physically-resolved path as the child's `cwd` and with `GIT_DIR` and `GIT_WORK_TREE`
  removed from the child's environment, and SHALL run it for **no** other entry. A command argument
  containing shell metacharacters (`;`, `&&`, `|`, `$(…)`, backticks) SHALL be passed to the child as a
  single literal argument. Each child's stdout and stderr SHALL be **captured** — never inherited onto
  this tool's own descriptors — and emitted only through AC-WRV-8's sink, sanitized **per line**: the
  captured bytes SHALL be decoded, split on `U+000A`, each line passed through the sink, and re-joined
  with `U+000A`, so line structure is preserved while no line's content can repaint the terminal. A
  child SHALL be bounded by a per-child timeout with a declared default, overridable by flag; a child
  that exceeds it SHALL be terminated and reported with the named result **`timeout`**, and a child
  that cannot be spawned at all (an `OSError` — an absent executable, a permission denial) SHALL be
  reported with the named result **`spawn-failed`**. Neither SHALL abort the fan-out: every remaining
  entry SHALL still run, each entry's result SHALL be reported, and the verb SHALL exit `2` when any
  child exited non-zero, timed out or failed to spawn, and `0` when all succeeded.

- **AC-WRV-6** *(Requirement — event)* **(`validate` closes the round-trip in both directions):** When
  `validate` runs, it SHALL report (a) the **forward** direction — the manifest ⟷ reality ⟷ gitignore
  findings exactly as `scripts/foundry_repo_registry.py` classifies them (`dangling`, `unpaired`,
  `unanchored`, `tracked`, `mismatch`, `outside-workspace`), with that module's row vocabulary
  preserved and not re-derived — and (b) the **reverse** direction the sibling deferred: every
  directory beneath the physical workspace root that contains a `.git` entry (file or directory), is
  not the root itself, and whose physical path is not the declared path of any `repos.<key>` entry,
  SHALL be reported as the named row value **`undeclared-checkout`**, carrying its path relative to the
  root, its **discovered origin** read with `git config --get remote.origin.url` — the raw configured
  value, never `git remote get-url`, which the discovered checkout's own `url.*.insteadOf` would
  expand — reported as the named value `undeclared` when unset, and the remedy that it needs a
  manifest row plus a root-anchored gitignore line. The reverse scan SHALL descend to a bounded depth
  (a declared default, overridable by flag), SHALL NOT descend into a discovered checkout, SHALL NOT
  traverse a symbolic link, and SHALL physically resolve each candidate and confirm it lies inside the
  physical root **before** reporting it. It SHALL exclude **exactly the set** `{.git, node_modules,
  .worktrees}` — `.git` because it is the marker directory itself and not a container of governed
  checkouts; `.worktrees` because `scripts/foundry-wt` creates foundry's own dispatch worktrees there
  (`<workspace>/.worktrees/<key>/<agent>/<task>`), which are worktrees of already-governed repos by
  construction; `node_modules` because it is a package-manager tree whose contents are never governed
  repos and whose depth makes traversal a cost rather than a finding. A directory outside that set
  SHALL be descended into. `validate` SHALL exit `2` when any finding in either direction is present
  and `0` when neither direction yields one.

- **AC-WRV-7** *(Invariant — ubiquitous)* **(a typed envelope and an advisory tri-state):**
  `scripts/foundry_repo_fleet.py` SHALL be independently invocable as a CLI over an explicit `--root`,
  and for **every** verb `--json` SHALL emit a single JSON **object** whose top-level keys are exactly
  `degraded`, `degraded_reason` and `rows` — the same envelope object
  `[[feat-foundry-repo-registry-formalization]]` froze for its own report, never a bare array — where
  each row carries at least `key` plus that verb's fields. When any oracle is unavailable, or the underlying registry report is itself degraded,
  `degraded` SHALL be `true` with a non-empty `degraded_reason`, the affected fields SHALL be `unknown`,
  and those SHALL be excluded from the finding count. Exit codes SHALL follow the shipped advisory
  tri-state — `0` clean, `2` findings, `1` when the manifest is absent, unreadable or unparseable — and
  both the module docstring and `--help` SHALL state that no gate consumes them.

- **AC-WRV-8** *(Invariant — ubiquitous)* **(one inherited emission sink):** Every string this tool
  emits to stdout, stderr or `--json` — including on the error and uncaught-exception paths, where a
  sanitized line SHALL replace a raw traceback — SHALL pass through the **single** sanitizing
  emission-boundary sink `scripts/foundry_repo_registry.py` ships, **imported rather than
  re-implemented** (asserted as that module's own function object), and
  applied to every field: repo key, declared remote, discovered origin, path, branch, remedy text,
  `degraded_reason`, and every captured child output line. For each of the three remote forms the
  corpus admits (`https://` userinfo, `ssh://` userinfo, and the scp-form), raw userinfo SHALL be
  absent from stdout, stderr and `--json` alike, on a clean run, a findings run and an error run.

- **AC-WRV-9** *(Invariant — ubiquitous)* **(the verb is discoverable, documented, and the tree stays
  healthy):** The plugin SHALL ship `skills/repos/SKILL.md` whose frontmatter `name` is `repos`, whose
  body names each of `sync`, `status`, `foreach` and `validate`, and whose `description` distinguishes
  it in one clause from the shipped `/foundry:fleet` verb — **the session roster**, which renders one
  row per active Claude Code session and governs no repository — so the two fleet-shaped verbs cannot
  be confused. `docs/VERBS-QUICK-REF.md` SHALL list `/foundry:repos`, and `README.md`'s derived
  `other ~N skills` claim SHALL be updated in the same change, so the shipped bijection between
  `skills/*/SKILL.md` and that reference and the shipped skill-count derivation both hold.
  `docs/how-to/multi-repo-control-plane.md` SHALL carry a verbs section naming the four verbs and
  stating the surfaced-never-fixed rule, and that section SHALL introduce no new label into the
  document's `enforced` claim roster, which `[[feat-foundry-control-plane-docs]]` pins closed.
  `CHANGELOG.md`'s **topmost release section** — the text between the first `## v…` heading and the
  next one — SHALL name `/foundry:repos`, because the plugin is version-keyed and an adopter receives
  only what a release section carries. `/foundry:doctor` over the changed tree SHALL report
  `DOCTOR-GREEN` on its default verdict.

- **AC-WRV-10** *(Invariant — ubiquitous)* **(one reconcile seam, re-validating, that the wizard can
  call):** The reconcile logic of AC-WRV-2/-3 SHALL be exposed by `scripts/foundry_repo_fleet.py` — a
  module name that is a valid Python identifier, so the Wave-3 wizard can `import` it — as the callable
  **`reconcile(root: str, rows: list[dict], *, timeout: float | None = None) -> dict`**, which SHALL
  perform no argv parsing and write to no stream. It SHALL return the AC-WRV-7 envelope, each row
  carrying at least `key`, `path`, `remote`, `action` (one of `clone`, `fetch`, `skip`, `refuse`),
  `result` (one of `ok`, `failed`, `timeout`, `spawn-failed`, `n/a`), `finding` (boolean) and `detail`.
  The callable SHALL resolve `root` **physically itself** and SHALL require a manifest present at that
  root, refusing (a degraded envelope, no git command) when there is none, so no caller can drive it
  against an arbitrary directory; and it SHALL apply AC-WRV-3's re-validation to **every** row's path
  and remote regardless of what that row claims. Its returned values SHALL be the **unsanitized**
  structured values — sanitizing them would corrupt data a caller must act on — and both its docstring
  and this criterion SHALL state that every consumer rendering them, the Wave-3 wizard included, SHALL
  render through AC-WRV-8's sink. The `sync` CLI path SHALL be a renderer over this callable's return
  value, so the wizard reconciles through the same code.

- **AC-WRV-11** *(Invariant — ubiquitous)* **(every git child carries the corpus's hardening standard,
  proven by absent side effects):** Every git invocation this tool makes — network-capable or not, and
  including every command run inside a governed checkout or a discovered `undeclared-checkout` — SHALL
  carry the **hardening set** as its leading `-c` overrides and SHALL run under the **sink
  environment**, both per the Terminology. Conformance SHALL be verified by **absent observable side
  effects**, not by argv strings alone: with a hostile `core.fsmonitor` and a hostile `core.sshCommand`
  planted in a governed checkout's own `.git/config`, a full `sync` + `status` + `foreach` + `validate`
  run SHALL leave the marker each would create **absent**, while a positive control demonstrates the
  same planted hook fires for an unhardened invocation. This criterion is the reason
  `GIT_TERMINAL_PROMPT=0` is **not** claimed as the credential property: the terminal prompt is one
  class only, and the property claimed here is the conjunction of `credential.helper=` (no configured
  helper attaches an ambient token), `core.askPass=` with `GIT_ASKPASS`/`SSH_ASKPASS` removed (no
  askpass program is consulted), and `core.sshCommand=ssh -o BatchMode=yes` with `GIT_SSH_COMMAND`
  removed (ssh's own passphrase and host-key prompts fail closed instead of blocking).

- **AC-WRV-12** *(Invariant — ubiquitous)* **(bounded egress, no submodule amplification, and no
  inertness promise about what is cloned):** Network egress SHALL occur only as a network-capable
  invocation, only for an entry declared in the manifest, and only to that entry's declared `remote`
  after AC-WRV-3's re-validation. Submodule recursion SHALL be disabled on **every** network-capable
  invocation — `--no-recurse-submodules` on `clone` and on `fetch`, plus the hardening set's
  `fetch.recurseSubmodules=no` and `submodule.recurse=false` — so no declared remote can be amplified
  into a fetch of an undeclared one; and the hardening set's `protocol.allow=never` with exactly
  `protocol.https.allow`, `protocol.ssh.allow` and `protocol.file.allow` set to `always` SHALL bound
  the transport layer to the admitted remote forms, so a redirect or an `insteadOf` rewrite into any
  other transport is refused by git rather than followed. This atom makes **no claim that a cloned tree
  is inert**: a cloned repository's content is untrusted, and inside a Claude Code workspace root its
  `CLAUDE.md`, `.claude/**` and `.mcp.json` become discoverable configuration for sessions rooted
  there. Both `--help` and `skills/repos/SKILL.md` SHALL state that non-promise in those terms.
<!-- /normative -->

## Design / notes

- **Why `cwd=` rather than `git -C`.** `-C <path>` puts a manifest-derived value in an option position
  ahead of the subcommand, where no `--` can guard it. Resolving physically and setting the child's
  `cwd` removes the value from argv entirely — a strictly smaller surface for the same behaviour.
- **Why a non-`match` origin never gets a fetch.** A checkout whose origin is not the declared remote
  is not the repo the manifest governs; fetching into it would move refs in a tree the control plane
  cannot vouch for, on the strength of a row that is already a finding. The rule widens to `undeclared`
  and `not-a-checkout` for a mechanical reason too: AC-WRV-2 requires the fetch to **name** the declared
  remote, so a row that declares none has nothing to fetch. It also subsumes a laxity in AC-RRF-5's
  comparison, which ignores userinfo and port when normalizing — two remotes differing only by port
  compare `match`, but since the fetch contacts the **declared** value rather than the checkout's
  configured one, the comparison's laxity cannot route a fetch anywhere the manifest did not name.
- **Why the module is `foundry_repo_fleet.py`, not the operator's original `foundry-repo-fleet.py`.** A
  hyphen is not a Python identifier: `import foundry-repo-fleet` is a syntax error, so AC-WRV-10's
  wizard seam would need a path-loading shim in production code. The corpus already draws this line —
  importable modules are underscored (`foundry_repo_registry.py`, `foundry_authz.py`, imported by name
  after a `sys.path` bootstrap in `foundry-authorize.py` and `foundry_audit_ledger.py`), hyphenated
  files are entrypoints only. The verb an operator types is unchanged: `/foundry:repos`.
- **The skill is new, and it is `repos`, not a `fleet-` sibling.** No shipped skill owns the
  governed-repo registry. `/foundry:fleet` is the **session roster** (`foundry-fleet-roster.py`, one
  row per active Claude Code session) — a different subject sharing the word; and
  `scripts/foundry-fleet-doctor.py`, the adopter-health sweep, is referenced by no skill, doc or test,
  so it is **unwired** and constrains nothing. Extending `fleet` would fuse two vocabularies in one
  verb; `fleet-repos` would read as its sub-verb. `skills/repos/` with sub-verb dispatch follows the
  shipped `/foundry:context <snapshot|resume|list|status>` convention, and AC-WRV-9 requires the
  description to draw the boundary.

## Clarifications

- **Q: Should `sync` fast-forward a clean, behind child, as `repo sync` does?** **No — resolved.** It
  is the one place the category's default and this control plane's posture genuinely conflict, and the
  posture wins: a child repo is governed by its own gates, and advancing its working tree from the
  control plane moves code that no merge floor observed. `fetch` makes the delta visible; `status`
  reports it; the operator (or the child's own loop) moves it. Recorded as departure (1) in *Prior art*.
- **Q: Should a first clone of a not-yet-present remote require an interactive operator
  confirmation?** **No — resolved: the manifest IS the declared consent.** A `repos.<key>` row with a
  `remote` is a committed, reviewable statement that this workspace governs that repository, and every
  other consumer already treats the manifest that way. Stated plainly so the assumption is reviewable:
  **write access to `.claude/foundry-project.json` is egress authority** for this tool. A prompt adds
  no security — the same actor who can edit the manifest can answer it — and breaks the
  non-interactive posture every other control-plane script holds.
- **Q: Should `git://` be admitted, since a remote may legitimately be one?** **No — resolved.** The
  sibling schema's *Out of scope* cites `git://` as a reason the **schema** takes no view on URL
  semantics; but this atom is the runtime boundary, and the corpus's reviewed allow-list rejects
  `git://` (unauthenticated, unencrypted), asserted by a shipped fixture. Adopting that set unchanged
  is the consensus move; widening it is a separate atom with its own review. Consequence, stated: a
  row declaring `git://` validates against the schema and is **refused here** as a named refusal row.
- **Q: What does `sync` do about a `dangling` entry — the defect live in this very workspace?** Reports
  it and stops there. There is nothing to clone (no `remote`) and nothing to fix without editing the
  manifest, which this atom never does.

## Out of scope / non-goals

- **Writing the manifest.** No verb here adds, edits or removes a `repos.<key>` row or a `.gitignore`
  line — including `validate`'s `undeclared-checkout` findings, which name the remedy and stop. The
  manifest is written by the wizard's attach flow (`wizard-attach-repo-flow`, Wave 3) and by hand.
- **The schema and the registry classification** — `[[feat-foundry-repo-registry-formalization]]`'s,
  frozen; `schema/**` and `scripts/foundry_repo_registry.py` are contract-denied here.
- **Widening or re-deriving the admitted remote forms** — the predicate is *loaded* from
  `scripts/foundry-prepublication-leak-scan.py`, which is contract-denied here along with its suites,
  so this atom cannot weaken it and cannot fork it.
- **Convicting anything at doctor level.** `[[feat-foundry-control-plane-preflight]]` owns the doctor's
  verdict on a dangling path; these verbs are advisory and `scripts/foundry-doctor.py` is denied.
- **Updating existing working trees** — no fast-forward, no `--force-sync` analog, no conflict
  resolution, no branch creation. Departure (1) above.
- **Credential management, auth setup, and private-remote onboarding.** The tool inherits whatever git
  credential configuration the operator's environment already has and never prompts; a remote that
  needs a credential the environment lacks is a reported failure row.
- **Sandboxing what is cloned, and any promise about its content** — AC-WRV-12 states the non-promise;
  making a cloned tree inert (or excluded from session discovery) is a different, larger atom.
- **Parallel fan-out, remotes other than `origin`, and submodule-aware traversal** — see *Residuals*.

## Residuals

- **R1 — Merge order is a precondition, and the chain is transitive.**
  `[[feat-foundry-control-plane-preflight]]` (authorized, `auth_seq: 1`) →
  `[[feat-foundry-control-plane-docs]]` (authorized) → **this atom**: the docs atom rewrites
  `docs/how-to/multi-repo-control-plane.md`, locking its section structure and its `enforced` claim
  roster while satisfying the preflight atom's `AC-CPP-8` checkpoint over that same file, and this
  atom appends a verbs section to it. Separately, `[[feat-foundry-repo-registry-formalization]]` is
  authorized but **not yet on disk** — `scripts/foundry_repo_registry.py` does not exist and every AC
  here consumes it — so it lands first of all. Operator-held sequencing, not a checkpoint.
- **R2 — Three atoms write one doc file.** Bound by R1's ordering plus the regression checkpoint over
  the shipped docs suites, which convict a section-lock or roster violation; the residual risk is a
  merge conflict if any two are implemented concurrently, which the ordering exists to prevent.
- **R3 — The reverse scan is bounded, and both bounds can miss.** Depth-bounded traversal with a closed
  exclusion set will not find an undeclared checkout nested deeper than the bound or under an excluded
  name, and `.worktrees` is excluded wholesale — an undeclared repo placed there is invisible to
  `validate`. Not following symlinks adds a third miss: a checkout reachable only through a symlinked
  directory is not walked. Bound: the exclusion set is closed and declared normatively, the depth is
  flag-overridable, and every one of these failure modes is a **missed finding, never a false one**.
- **R4 — `origin` is the only remote, inherited from the sibling.** A child whose upstream is named
  something else reports `not-a-checkout`-adjacent noise rather than a mismatch, and never gets a
  fetch under AC-WRV-2's `match`-only rule. Same bound the sibling recorded.
- **R5 — Egress bounding is argv- and config-level, not sandbox-level.** AC-WRV-12's `protocol.*`
  pinning is enforced by git, not by a network sandbox: the tool can still reach any host the declared
  remote names, and a server-side redirect within an allowed protocol is still followed. Bound: the
  declared remote is the operator's own committed value, re-validated at the boundary; the sibling's
  oracle reads the raw configured origin so a redirect surfaces as `mismatch`; submodule recursion is
  off; and the remainder sits with the operator's terminal test-and-sign-off step (`CLAUDE.md`
  § "Delivery sign-off — operator-held, the terminal step") — a **practice, not a control**. No
  anti-gaming machinery is added to chase it.
- **R6 — Serial fan-out only.** `foreach` over a large fleet is as slow as the sum of its children.
  Bound: matches every surveyed prior-art default; `--jobs` is additive later.
- **R7 — `sync`'s idempotence claim is narrow, and it is exactly two things.** A second `sync`
  immediately following a first over an unchanged tree issues **zero** `git clone` invocations and
  creates **no** new path; it does **not** claim fetch-level convergence — fetches legitimately re-run
  and a remote that changed between runs legitimately produces different results. The property is
  asserted over the run, not over a cached flag.
- **R8 — Clone consumes unbounded disk inside the workspace.** No quota, no size flag, no `--depth`
  default: a declared remote of any size is cloned in full into the control plane's own tree. Bound:
  the remote set is the operator's own committed manifest (the consent question in *Clarifications*),
  and the failure mode is a full filesystem reported as a failed row, not a silent corruption.
  `--depth`/partial-clone support is additive later.
- **R9 — No git version floor is declared anywhere in the corpus.** Every key the hardening set pins
  (`protocol.allow`, `protocol.<name>.allow`, `submodule.recurse`, `fetch.recurseSubmodules`,
  `core.sshCommand`, `core.fsmonitor`) long predates **git 2.30**, the conservative floor this atom
  assumes — but `docs/standing-versions.md` carries **no git row**, and no script in the plugin asserts
  a git version. Recorded, not fixed: adding that row is a workspace-side edit outside this atom's
  `target_repo`, and it is flagged for the operator as a follow-on.
- **R10 — The sibling schema has no floor on `repos.<key>` names.** AC-RRF-2 shape-floors `path`,
  `remote` and `default_branch` values but not the key itself, so a hostile key (leading `-`, control
  characters, ANSI) validates. AC-WRV-3 handles it at this boundary and AC-WRV-8's sink renders it, but
  the durable fix is a `propertyNames` pattern on `repos` in `schema/foundry-project.schema.json` —
  which is contract-denied here. Flagged as a follow-on to the sibling atom.

## Changelog

- v1.0 Draft. Ship the control plane's fleet verbs — `sync` / `status` / `foreach` / `validate` — over
  the formalized registry, as a new fleet module plus a new `skills/repos/` verb: clone and fetch are
  the entire mutation vocabulary (drift surfaced, never fixed), egress is bounded to operator-declared
  remotes, `foreach` is shell-free fail-collecting argv, and `validate` closes the round-trip
  **including** the reverse direction (`undeclared-checkout`) that
  `[[feat-foundry-repo-registry-formalization]]` deferred by name. The registry classification, the
  `{degraded, degraded_reason, rows}` envelope and the redaction sink are consumed from that sibling
  unmodified. Realizes atom #7 of `intake/er-onboarding-wizard-and-permission-floor.md` (2026-08-01).
- v1.1 Remediation round (consolidated 4-lens review, 2026-08-02). **Security Blocks, all resolved by
  adopting `[[feat-foundry-leak-scan-ls-remote-sink]]`'s reviewed standard rather than a new
  mechanism:** (1) an **admitted remote form** allow-list is enforced at the boundary in AC-WRV-3 —
  the corpus's own `url_is_allowed_form` / `_is_local_path_escape_hatch` predicate, **loaded** from the
  leak-scan module via the shipped `spec_from_file_location` device rather than re-implemented (so the
  transport-helper exclusion stays by-rule and cannot fork), with `protocol.allow` pinning in the
  hardening set; (2) `sync` now fetches **only** a `present` checkout
  whose origin is exactly `match`, and the fetch **names the declared remote** positionally after `--`
  rather than trusting the checkout's configured origin (AC-WRV-2); (3) submodule recursion is
  disabled on **every** network-capable invocation, fetch included — moved out of the old AC-WRV-8
  "on clone" wording into **AC-WRV-12**; (4) the fixed, non-manifest-derived **hardening set** and the
  subtractive **sink environment** are now required by AC-WRV-1 and defined in a normative Terminology
  block, with conformance asserted on **absent observable side effects** (a planted hostile
  `core.fsmonitor`/`core.sshCommand` does not fire) per AC-LSH-3's standard — **new AC-WRV-11**;
  (5) the credential claim is reworded to the conjunction actually secured (`credential.helper=`,
  `core.askPass=` + askpass vars removed, `ssh -o BatchMode=yes`), never `GIT_TERMINAL_PROMPT=0` alone,
  citing `scripts/foundry-bootstrap.sh:251-271`; (6) `foreach` child output is **captured** and emitted
  only through the sink, sanitized per line on `U+000A` with line structure preserved (AC-WRV-5).
  **Steel-man Blocks:** the module is renamed `scripts/foundry_repo_fleet.py` (importable — the
  hyphen breaks the Wave-3 wizard's `import`; the corpus's underscore/hyphen line is cited); the
  reverse-scan exclusion set is closed as **exactly** `{.git, node_modules, .worktrees}` with each
  member's rationale corrected and grouped, plus a checkpoint that a non-listed directory **is**
  descended into; the idempotence checkpoint now proves first-run `clone_count >= 1` **and** second-run
  `== 0` over the same entries. **Rubric Block:** the CHANGELOG requirement is anchored in AC-WRV-9's
  normative text and its locator is anchored to the **topmost release section**, not a bare substring.
  **AC splits (IDs only — the checkpoints were already paired):** AC-WRV-7 → envelope (AC-WRV-7) +
  callable seam (**AC-WRV-10**, with the callable's name, parameter names/types and return shape pinned,
  its own re-validation of every row, physical root resolution, a required manifest, and the
  unsanitized-return/render-through-the-sink contract); AC-WRV-8 → redaction (AC-WRV-8) + egress
  (**AC-WRV-12**); AC-WRV-3 gains a second checkpoint separating the refusal path from the accepted
  argv. **Risks folded in:** per-child timeout and `spawn-failed` as named row results (AC-WRV-5); the
  reverse scan does not follow symlinks and physically confines candidates before reporting, and
  `undeclared-checkout` rows now carry the discovered origin (the `vcs export` analogy made true)
  (AC-WRV-6); a hostile `repos.<key>` key added to the refusal parametrization (AC-WRV-3) with the
  schema `propertyNames` follow-on recorded (R10); the cloned-tree **inertness non-promise** stated
  normatively (AC-WRV-12) and the git version floor recorded (R9); a socket-deny belt-and-braces
  assertion added to the egress checkpoint. **Corrections:** the ER carries no subcommand-level facts,
  so *Prior art* now cites primary docs (URL + ref, consulted 2026-08-02) for `repo forall -c`'s shell
  evaluation, `repo sync`'s working-file update, `vcs export`, and `git submodule foreach`;
  `/foundry:fleet` is the **session roster** (and `foundry-fleet-doctor.py` is unwired) in both
  *Design/notes* and AC-WRV-9's disambiguation clause; the shell-free convention cites **AC-RRF-6(vi)**
  by ID; the schema shape floor is stated as **advisory**, with AC-WRV-3's re-check named the sole
  runtime control; R1 names the full transitive chain; R7 is tightened to the two things it claims;
  and the `git://` question, the first-clone consent question (the manifest **is** the declared
  consent) and the AC-RRF-5 port/userinfo laxity are each resolved in text. **Duplication trimmed:**
  the `cwd=`-not-`-C` rationale, the mismatch-skips-fetch rationale, and the
  registry-consumed-not-re-derived note each appear once now.
