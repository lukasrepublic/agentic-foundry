# The governed-repo attach flow: attach-existing / create-new  (feat-foundry-wizard-attach-repo-flow)

> **Human-readable intent.** `[[feat-foundry-repo-registry-formalization]]` defines what a governed-repo
> row *is* (`{path, remote, default_branch, role, description}`, a root-anchored gitignore line beside
> it) and reports on it; `[[feat-foundry-workspace-repo-verbs]]` reconciles reality to it and exposes
> that reconcile as the importable callable `foundry_repo_fleet.reconcile`. **Neither writes a row.**
> Today the only way a repo enters the control plane is an operator hand-editing JSON and remembering
> the paired `.gitignore` line — the exact pairing this workspace has already got wrong
> (`repos.ctxinfra`: no gitignore entry, and a path that does not exist).
>
> **This atom ships the write half, and only the write half**: the `attach-existing` and `create-new`
> flows — the `mr register` / `nx import` shape — as a **reusable flow module the pre-session bootstrap
> CLI hosts** (`[[feat-foundry-bootstrap-cli]]`, being specified in parallel; this atom ships no wizard
> chrome and no other wizard step). One code path: `create-new` runs `gh repo create` and then **falls
> through into** `attach-existing`. Every field is collected, defaulted and **confirmed**; every value is
> validated against the registry's own shape floor **before any byte is written**; the operator sees the
> exact row, the exact gitignore **bytes** and the reconcile plan **before** the write; the pair lands
> **atomically, in a pinned order whose only survivable half is the harmless one**; and only then does
> reconcile run, **through the sibling's importable callable**. This module executes **no git command of
> its own**, ever, and `gh repo create` runs only after its own explicit "create repo *X*" confirmation.
>
> **The order is the security property.** Never clone-first-record-after: a checkout that exists before
> its row is an undeclared checkout, invisible to every consumer until `validate` finds it. And the
> pairing is both-or-neither because each half alone is a defect — a row without the gitignore line
> means the control plane's next `git add -A` sweeps the child's whole history into the parent (the
> spill), and a gitignore line without a row hides a checkout from every consumer that reads the
> manifest. Where the two-file write cannot be made truly transactional, the atom **says so and orders
> the writes so the reachable partial state is the inert one** (AC-WAF-4, R2) rather than claiming an
> atomicity no filesystem offers.

## Prior art / industry grounding

**The register-an-existing-repo flow is the category's, and two of its members are named in the ER.**
`intake/er-onboarding-wizard-and-permission-floor.md` (researched 2026-08-01, primary sources) records
the verdict — the wizard's attach flow is "the `mr register`/`nx import` shape", both branches end in
the same manifest-write-then-reconcile path — and supplies the operating rule from **ArgoCD's
registration UX**: *the wizard only ever writes manifest rows; a separate reconcile verb makes the
filesystem match; the manifest is the only truth.* That rule is this atom's spine, and it is why the
reconcile is an imported callable rather than logic living here.

- **myrepos `mr register`** (`https://myrepos.branchable.com/`) — registers an **already-existing**
  checkout into the committed `.mrconfig` by inferring its VCS and URL: the registration act is a
  *manifest edit*, not a checkout operation. This flow's `attach-existing` is that move plus the
  gitignore pairing this control plane needs — including its consequence, that registering a checkout
  whose origin does not match its declaration is a **declaration that stands with a finding beside it**,
  not a failure that un-registers the repo (AC-WAF-8).
- **`nx import`** (`https://nx.dev/nx-api/nx/documents/import`) — the guided *source → destination →
  confirm* interaction for bringing an outside repository under a workspace's governance.
  **Departure, stated:** `nx import` **absorbs** the source repo's history into the parent monorepo;
  this control plane never does — the child stays a plain independent clone under its own origin, per
  the gitignored-siblings consensus the ER re-confirmed (submodules and history-absorption both couple
  the parent to the child).
- **`gh repo create`** (`https://cli.github.com/manual/gh_repo_create`) — the create branch's one
  network write, including `--template`. **Departure, stated:** its `--clone` flag is **not** used —
  cloning is the reconcile callable's job, and using `--clone` would be exactly the
  clone-first-record-after order this atom forbids.
- **Wizard craft** — `create-next-app` / clig.dev (`https://clig.dev/`): a flag twin for every prompt
  plus `--yes` (non-interactive parity), recommended defaults pre-selected, **preview-before-write**.
  The two-phase *show the plan, then apply it* shape is `terraform plan` → `apply`; clig.dev's rule that
  a **destructive or irreversible** step gets its own confirmation is why `gh repo create` has one of
  its own (AC-WAF-6) rather than riding the pair's confirmation.
- **The gitignore line is constructed by git's own documented rules, not by a heuristic** —
  `https://git-scm.com/docs/gitignore`: a `/` at the start or middle anchors the pattern to the file's
  directory, a trailing `/` restricts the match to a directory, `#` and `!` are special at line start,
  `*`, `?` and `[…]` are wildcards anywhere, `\` escapes the next character, and a trailing space is
  stripped unless escaped. AC-WAF-2/-3/-4 and the Terminology entry apply exactly those rules.
- **Atomic-replace + durability is the standard recipe, cited** — write a temp file in the **target's own
  directory**, `fsync` it, `rename(2)` over the target (POSIX's atomic replace:
  `https://pubs.opengroup.org/onlinepubs/9699919799/functions/rename.html`), then `fsync` the containing
  **directory** so the rename itself is durable (`https://lwn.net/Articles/457667/`, "Ensuring data
  reaches disk" — the recipe git, SQLite and every serious config writer use).
- **Both-or-neither across two files has no filesystem primitive**, so the pair is made transactional
  the standard way: per-file atomic replace plus a **compensating action** — the saga pattern
  (Garcia-Molina & Salem, *Sagas*, SIGMOD 1987; `https://microservices.io/patterns/data/saga.html`),
  which also supplies the bound that an **irreversible** effect is never compensated (hence AC-WAF-5's
  two uncompensated effects). The stronger alternative, a **write-ahead intent journal** replayed on next
  start (the WAL/two-phase-commit-with-recovery shape), was considered and **not** taken; see
  *Clarifications*.

**Nothing here is a new mechanism.** The shape floor, the admitted remote forms, the redaction sink and
the reconcile are all *consumed* from the two authorized siblings — this atom's contribution is the
ordering, the confirmation and the atomicity.

## Security posture

**This flow IS the consent moment, and that is the point.** `[[feat-foundry-workspace-repo-verbs]]`
resolves its first-clone question by declaring that **write access to `.claude/foundry-project.json` is
egress authority** — a committed row is the operator's standing consent to reach that remote. That
assumption is only reviewable if something makes the write deliberate, and this atom is that something:
every field is shown and confirmed, the whole row plus its gitignore bytes plus the reconcile plan are
previewed before the write, and the flow states in plain language that writing the row is what
authorizes the clone. AC-WAF-3 is therefore a security criterion, not an ergonomics one.

**Two network writes exist in this atom's whole surface, and only one is its own.** The reconcile's
`git clone`/`git fetch` belong to the callable, bounded by AC-WRV-3's boundary re-validation, AC-WRV-11's
hardening set and AC-WRV-12's egress bound — none of which this atom can weaken (`foundry_repo_fleet.py`
is contract-denied). `gh repo create` is the single network write this atom makes itself: fixed argv, no
shell, `--`-guarded, **argument-floored before invocation**, gated behind its own explicit
"create repo `<owner>/<name>`" confirmation, short-circuited entirely under `--dry-run`, invoked only on
the explicit create branch, and it writes nothing locally.

**A credential must never be able to enter the manifest through this flow.** The manifest is a
**committed** file: a remote carrying `https://user:token@host/…` would put a live secret into git
history, where redaction at the emission boundary does not help. So a source whose userinfo carries a
password component is a **named refusal** (AC-WAF-2d) — not a double-confirm — and a userinfo with a
single component (which may itself be a token) draws a named advisory at the preview stating that the
manifest is committed. The flow reads, stores and passes no credential of its own.

**No trust self-acceptance.** The callable's AC-WRV-10 contract states that it re-validates **every**
row regardless of what the caller claims, and that its returned values are **unsanitized** — this flow
is not a trusted caller and does not act like one: it validates before writing (AC-WAF-2) and renders
every returned value through the inherited sink (AC-WAF-3). Nothing here marks a row as pre-approved,
and no path skips the callable's own re-validation. Its own additions to the floor (AC-WAF-2 b, d, e, f,
g, h) are **narrowings** — a value this flow refuses is never a value the siblings would have admitted
being widened, only a smaller admitted set at the one surface that writes.

<!-- normative -->
## Terminology (normative)

- **the shape floor** — the value constraints `[[feat-foundry-repo-registry-formalization]]` expresses in
  `schema/foundry-project.schema.json`: `path` required and a non-empty string; `role` in the closed set
  `product`, `handbook`, `infra`, `app`, `workspace`; `remote`, `default_branch`, `description` strings;
  no empty `path`/`remote`/`default_branch`; no leading `-` on `path`/`remote`/`default_branch`; no C0/C1
  control character (`U+0000`–`U+001F`, `U+007F`–`U+009F`) in `remote`/`default_branch`.
- **an admitted remote form** — as `[[feat-foundry-workspace-repo-verbs]]` defines it: the value for
  which the corpus's reviewed `url_is_allowed_form` / `_is_local_path_escape_hatch` predicate returns
  true, decided by **loading** that predicate from `scripts/foundry-prepublication-leak-scan.py`. That
  predicate admits a local path only when it is **absolute** and resolves to an existing directory; a
  relative path is not an admitted form.
- **the registry key floor** — a `repos.<key>` matching `^[A-Za-z0-9][A-Za-z0-9._-]*$` and not equal to
  the reserved key `workspace` (the self-entry convention the registry sibling records).
- **the gitignore line** — for a collected local path `P` (lexical, relative to the workspace root, any
  `./` prefix stripped, no `..` segment), the single line whose bytes are `/` + `escape(P)` + `/` +
  `U+000A`, where `escape` prefixes `\` to each occurrence of `\`, `*`, `?`, `[`, `]`, `!`, `#` and to a
  trailing space, and never to `/` — one leading `/` (root-anchoring), exactly one trailing `/`
  (directory-only), every wildcard and comment metacharacter inert. Escaping a metacharacter git treats
  as special only at line start is deliberate: over-escaping is inert, under-escaping changes what the
  pattern matches.
- **the pair** — one `repos.<key>` object in `.claude/foundry-project.json` and one **gitignore line**
  in the workspace root's `.gitignore` naming the same declared path.
- **the sink** — the single sanitizing emission function `scripts/foundry_repo_registry.py` ships.
- **the registry report** — the per-entry classification that same module produces, whose `gitignore`
  value **`tracked`** means the target is already inside the control plane's git index.
- **the reconcile callable** — `foundry_repo_fleet.reconcile`, the importable function object
  `[[feat-foundry-workspace-repo-verbs]]` ships: `reconcile(root, rows, *, timeout=None) -> dict`,
  returning the typed envelope whose rows carry `action` (one of `clone`, `fetch`, `skip`, `refuse`),
  `result` (one of `ok`, `failed`, `timeout`, `spawn-failed`, `n/a`), a boolean `finding` and `detail`,
  all **unsanitized** by that contract.
- **a reconcile failure outcome** — a returned row whose `action` is `refuse`, **or** whose `result` is
  one of `failed`, `timeout`, `spawn-failed`. A row that is merely a `finding` is **not** one.

## Acceptance criteria

- **AC-WAF-1** *(Requirement — event)* **(attach-existing collects a complete, defaulted, confirmed row,
  and every prompt has a flag twin):** When the attach-existing flow runs, it SHALL collect, in this
  order: the **source** — a remote URL in an **admitted remote form**, or an **absolute** local
  filesystem path resolving to an existing directory; the **local path**, defaulted to the final path
  segment of the source with one trailing `.git` stripped, presented as an overridable default and
  **explicitly confirmed**; the **registry key**, defaulted to that same derived name when it satisfies
  **the registry key floor** and otherwise offered with **no** default — the flow SHALL NOT transform a
  non-conforming derived name into a conforming key; the **role**, offered as the shape floor's closed
  set; a **one-line description**; and the **default branch**. When the source is a local path, the value
  written to the row's `remote` SHALL be that absolute path **verbatim** — the exact string the
  admitted-form predicate accepted — so the boundary re-validation sees the bytes this flow validated.
  Each field SHALL have a flag twin, and a fully-flagged invocation with `--yes` SHALL complete without
  reading from stdin; under `--yes` a required value that no flag supplied SHALL be a named refusal,
  never a silently defaulted or prompted value.

- **AC-WAF-2** *(Requirement — unwanted)* **(if any collected value fails the floor, nothing is
  written):** If any of the following holds — (a) the **prospective document** (the current manifest with
  the new row inserted) fails validation against the shipped `schema/foundry-project.schema.json` with
  any error the **pre-image** document does not also carry; (b) the registry key fails **the registry key
  floor** or is already present; (c) the source is neither an **admitted remote form** nor an absolute
  path resolving to an existing directory; (d) the source's userinfo carries a password component (a `:`
  within the userinfo of any admitted form); (e) `path` or `description` contains a C0 or C1 control
  character (`U+0000`–`U+001F`, `U+007F`–`U+009F`) — a path containing `U+000A` is this refusal, **never**
  two gitignore lines; (f) the physical resolution of the target path, or of its nearest existing
  physical ancestor, lies outside the physical workspace root, or equals, lies inside, or contains the
  physical resolution of any declared `repos.<key>` path other than an entry whose path is the workspace
  root itself; (g) the target path equals the declared path of an existing row; (h) **the registry
  report** classifies the target `tracked`; or (i) on the create branch an argv floor of AC-WAF-6 fails —
  then the flow SHALL emit a named refusal identifying the offending field, **rendered through the
  sink**, SHALL write **no** byte to `.claude/foundry-project.json` or `.gitignore`, and SHALL invoke
  neither `gh` nor **the reconcile callable**. The floor SHALL be decided by **validating against the
  shipped `schema/foundry-project.schema.json`** and by **loading** the admitted-form predicate from
  `scripts/foundry-prepublication-leak-scan.py` — in both cases the shipped artifact, **never a
  re-implementation** — so this flow cannot drift from, or widen, the floor its siblings enforce; (b),
  (d), (e), (f), (g) and (h) are **narrowings this flow adds**, never relaxations. A schema error the
  pre-image already carries SHALL NOT block the write and SHALL instead be reported as a named
  pre-existing-defect line in the preview.

- **AC-WAF-3** *(Requirement — event)* **(the operator sees exactly what will be written, rendered
  through the inherited sink, before it is written):** When validation passes, the flow SHALL emit a
  preview carrying (a) the **exact** `repos.<key>` object as it will be serialized, (b) **the gitignore
  line** as the exact byte sequence that will be appended — or, when that exact line is already present,
  a named statement that no line will be added — (c) the **reconcile plan** — the action **the
  reconcile callable** will take for this row, in its own `clone` / `fetch` / `skip` / `refuse`
  vocabulary — and (d) every pre-existing-defect line AC-WAF-2 produced; and SHALL require an explicit
  affirmative confirmation before any write. Wherever the sink's rendering differs from the bytes that
  would be written — a redacted userinfo component, an escaped control character — the preview SHALL
  state **at that value** that the unredacted, unescaped value is what would be written; and a source
  whose userinfo carries a single component with no password SHALL carry a named advisory that
  `.claude/foundry-project.json` is a **committed** file. A declined confirmation and a `--dry-run`
  invocation SHALL each leave `.claude/foundry-project.json` and `.gitignore` byte-identical, invoke
  neither `gh` nor the callable, and exit with a named non-writing outcome. Every string this flow emits
  to stdout, stderr or `--json` — the preview, AC-WAF-2's refusals, the reconcile report, AC-WAF-5's
  rollback report, and the error and uncaught-exception paths, where a sanitized line SHALL replace a raw
  traceback — SHALL pass through **the sink**, imported rather than re-implemented, so that a remote's
  userinfo never reaches a channel raw (the callable returns unsanitized values precisely because
  rendering is the caller's duty).

- **AC-WAF-4** *(Invariant — ubiquitous)* **(the pair lands atomically, in the order that makes the only
  reachable partial state the harmless one):** A run of either flow SHALL leave the workspace in exactly
  one of three states: **the pair present**; **neither present**; or — reachable only when the process
  dies between the two `rename(2)` calls, or inside the rollback — the **benign half**: the gitignore
  line present with no row. The **dangerous half** — a row with no root-anchored gitignore line, which is
  the spill — SHALL NOT be reachable. The write order is therefore pinned: **the gitignore line lands
  first, the manifest row second**, and the rollback mirrors it, **removing the row first**. Each file
  SHALL be replaced atomically: a temp file created in the **target file's own directory**, written,
  `fsync`ed, its mode copied from the file it replaces, `rename(2)`d over it, and the containing
  directory `fsync`ed after the rename; a target path that is a symbolic link SHALL be a named refusal,
  never a followed write. The pre-image of each file SHALL be re-read and hashed **immediately before the
  write** — never a copy cached at preview time — and that hash SHALL be re-verified immediately before
  the rename; a mismatch SHALL be a named refusal that writes nothing, so a concurrent writer is never
  clobbered. The `.gitignore` append SHALL first normalize the file's trailing newline (a file not ending
  in `U+000A` gains one before the append, and the result ends with exactly one), SHALL be a **no-op**
  when the exact line is already present, and SHALL add at most one line. The manifest write SHALL
  preserve every other key, every `"//"` comment entry and the file's existing formatting, altering only
  the added row.

- **AC-WAF-5** *(Requirement — unwanted)* **(rollback compensates the pair and nothing else, and names
  what it cannot compensate):** If the flow raises, or **the reconcile callable** returns **a reconcile
  failure outcome**, then the flow SHALL restore `.claude/foundry-project.json` and `.gitignore` to their
  **byte-identical** pre-write contents in the mirrored order (the row removed first), SHALL precede each
  restore by re-verifying that the on-disk file still hashes to what this flow itself wrote — a mismatch
  SHALL abort that file's restore with a named report line rather than overwrite a concurrent writer —
  and SHALL report the failure with its cause **through the sink**. The rollback SHALL be bounded to
  exactly those two edits. It SHALL NOT delete, move or modify a **clone that completed**, and it SHALL
  NOT delete a repository `gh repo create` created: these are the flow's **two uncompensated effects**,
  each of which SHALL be **named in the report** with its remedy — the clone is now an undeclared
  checkout, the created repository is an orphan with no local row. A created repository SHALL likewise be
  named as an orphan when the operator declines the confirmation, or any refusal fires, **after**
  `gh repo create` has succeeded. The rollback SHALL NOT alter any other `repos.<key>` entry, any other
  `.gitignore` line, any git state, or any file the flow did not itself write.

- **AC-WAF-6** *(Requirement — event)* **(create-new confirms the creation first, then runs `gh` and the
  same code path):** When the create-new flow runs, it SHALL first require an explicit affirmative
  confirmation of a line naming what it is about to create — `create repo <owner>/<name>`, plus
  `from template <owner>/<name>` when a template is requested — carrying, **at that choice**, the
  cloned-tree inertness non-promise of AC-WAF-7; and a `--dry-run` invocation SHALL short-circuit
  **before** any `gh` invocation. It SHALL then invoke `gh repo create` as a fixed argv executed without
  a shell, with `--` before every operator-supplied positional **and** with each such value itself
  floored before invocation — the repository argument matching
  `^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)?$` and a `--template` value matching the
  two-segment `<owner>/<name>` form of that same class — optionally carrying `--template`, and
  **without** `--clone`. On success the flow SHALL take the created repository's canonical
  `<owner>/<name>` and clone URL from a **structured (`--json`) `gh` read**, never from the
  operator-typed argument, SHALL surface any divergence between the requested and the created identity as
  a named line requiring re-confirmation before the pair is written, and SHALL then continue into **the
  same** attach-existing code path as AC-WAF-1 with that URL as the source, so that exactly one
  implementation writes the pair. If `gh repo create` fails, is unavailable, or is unauthenticated, then
  the flow SHALL emit a named refusal and write nothing. `gh` SHALL be the **only** network-capable
  subprocess this flow spawns; every other network effect SHALL occur inside the reconcile callable.

- **AC-WAF-7** *(Invariant — ubiquitous)* **(the shipped surface states the authority it exercises):**
  `scripts/foundry_repo_attach.py` SHALL be importable by the bootstrap CLI (a module name that is a
  valid Python identifier) and independently invocable as a CLI over an explicit `--root`. Its module
  docstring and its `--help` SHALL each carry, **verbatim**, each of these three sentences, each on one
  line:
  - `Writing a repos.<key> row IS the standing authority to reach that remote.`
  - `Every field is confirmed before anything is written.`
  - `No claim is made that a cloned tree is inert: a cloned repository's CLAUDE.md, .claude/** and .mcp.json become discoverable configuration for sessions rooted in the workspace.`

  The third restates, at the surface that causes the clone, the non-promise
  `[[feat-foundry-workspace-repo-verbs]]` makes about cloned trees.

- **AC-WAF-8** *(Invariant — ubiquitous)* **(the write strictly precedes a reconcile that is the
  sibling's own callable, called with exactly the new row):** The flow SHALL execute **no** `git` command
  itself, so no checkout can come into existence before the row that declares it. Reconcile SHALL be
  performed **only** by calling **the reconcile callable** — the imported function object, not a
  re-implementation and not a subprocess of the sibling's CLI — SHALL be entered **only after** the pair
  is durably on disk (both renames returned and both directory `fsync`s completed), and SHALL be called
  with `rows` equal to **exactly one row**: the newly-written row **read back from the manifest on
  disk**, never the in-memory values the flow collected. AC-WAF-5's rollback SHALL be triggered by **a
  reconcile failure outcome** and by an exception, and SHALL NOT be triggered by a returned row's
  `finding` flag alone: a row whose `action` is `skip` and whose `result` is `ok` or `n/a` is a
  legitimate declaration, and its pair SHALL persist even when that row is a finding — the
  register-an-existing-checkout case.

- **AC-WAF-9** *(Invariant — ubiquitous)* **(the release names the flow and the tree stays healthy):**
  `CHANGELOG.md`'s **topmost release section** — the text between the first `## v…` heading and the next
  one — SHALL name the attach flow, because the plugin is version-keyed and an adopter receives only what
  the current section carries. `/foundry:doctor` over the changed tree SHALL report `DOCTOR-GREEN` on its
  default verdict.
<!-- /normative -->

## Design / notes

- **Why a Python module rather than logic inside the npm CLI.** AC-WRV-10 pins the reconcile seam as an
  **importable Python callable** (`foundry_repo_fleet.reconcile`, underscored for exactly this reason).
  Re-implementing reconcile in JavaScript would fork the one code path the ER's design depends on. The
  flow therefore ships as `scripts/foundry_repo_attach.py` in the plugin and the pre-session CLI *hosts*
  it; how the CLI invokes it is `[[feat-foundry-bootstrap-cli]]`'s seam, not this one's.
- **Why the preview shows the reconcile plan and not just the row.** The row is the consent artifact,
  but "what will happen next" is the thing an operator can actually judge — `clone` reaches a network,
  `skip` does not. Rendering the callable's own action vocabulary keeps the preview honest with the code
  that will run instead of restating it in prose.
- **Why `--clone` is not used on `gh repo create`.** It would produce a checkout before the row exists —
  the clone-first-record-after order this atom exists to forbid — and it would bypass every bound
  AC-WRV-3/-11/-12 place on the clone that the callable performs.
- **Why the gitignore line lands first.** The two halves are not symmetrically dangerous. A row without
  the ignore line is the **spill** (the parent's next `git add -A` absorbs the child). An ignore line
  without a row, before any clone has run, names a path that does not exist: inert, and swept up by the
  flow's own idempotent re-run. Ordering the writes so the survivable half is the inert one converts an
  unavoidable two-syscall window from a security defect into a cosmetic one.
- **Why the canonical identity comes from `gh`, not from what was typed.** GitHub may normalize an owner
  or name (case, a renamed org) — binding the row to what was *typed* would declare a remote that is not
  the repository that exists. Reading it back structurally makes the row match reality and makes any
  divergence a thing the operator is shown and must re-confirm.

## Clarifications

- **Q: Should the flow re-validate what the callable will re-validate anyway?** **Yes — resolved.** The
  callable's re-validation refuses *at the boundary*, after the row is written; validating first is what
  makes AC-WAF-2's "nothing is written" reachable at all. The two checks are deliberately redundant and
  neither is removable: this one prevents a bad row from ever being committed, that one prevents a bad
  row that arrived some other way from reaching a socket.
- **Q: Should a failed reconcile roll the row back, given the row is a legitimate declaration?**
  **Yes for a failure, no for a finding — resolved, and the line is drawn in AC-WAF-8.** A row whose
  reconcile *failed or was refused* is indistinguishable from the `dangling`/`not-cloned` defects the
  sibling atoms exist to convict, and leaving it behind makes the wizard a *producer* of that drift. But
  a row that reconciled to `skip` **with a finding** — the classic `mr register` case, an existing
  checkout whose origin does not match its declaration — is exactly what the operator asked for: the
  declaration stands and the finding is reported. Rolling that back would make the flow unable to
  register the very repos it exists to register. The one thing never undone is a completed clone or a
  created repository (AC-WAF-5).
- **Q: An intent journal, or a weakened-and-ordered atomicity claim?** **The ordered form — resolved.**
  A write-ahead intent journal replayed at next start would close the two-syscall window completely, but
  it adds a persistent state file, a recovery path, and a second class of stale-state defect, all to
  protect a window whose only reachable partial state (with the order pinned) is **inert**. The honest
  weakening plus the pinned order is the proportionate build; the residual is stated as R2 rather than
  papered over with an atomicity claim the filesystem does not support. Revisit only if a real
  interrupted-run defect is observed.
- **Q: A credential-bearing remote — refuse, or accept behind a second confirmation?** **Refuse —
  resolved (AC-WAF-2d).** The double-confirm was considered and rejected on one fact: the manifest is a
  **committed** file, so a token in a remote becomes a token in git history, where the emission sink's
  redaction is irrelevant. A refusal costs the operator one edit; the alternative costs a credential
  rotation. Blanket refusal of *all* userinfo was also rejected — it would reject the scp-form
  `git@host:owner/repo`, which is the corpus's most common ssh remote — so the cut is at the password
  component, with an advisory on the single-component case.
- **Q: What if the manifest is already schema-invalid before this flow touches it?** **Report and
  proceed, row-scoped — resolved (AC-WAF-2a).** A blanket refusal was rejected because the defect is
  real and live: `[[feat-foundry-repo-registry-formalization]]`'s R1 records that the handbook's own
  seeded manifest fails validation *today* (two string comment values where the schema requires an
  object). Refusing would make the primary onboarding path unusable on day one and push the operator
  back to hand-editing JSON — the exact practice this atom replaces. The rule is therefore
  **monotonicity**: validate the whole prospective document, refuse anything the pre-image did not
  already carry, and surface pre-existing defects in the preview so the write is never mistaken for a
  clean bill of health.

## Out of scope / non-goals

- **Every other wizard step** — welcome, workspace identity, git/GitHub identity, stage mode, the
  permission-floor declarations, the exit line — and the CLI's packaging, argument surface and prompt
  chrome: all `[[feat-foundry-bootstrap-cli]]`'s.
- **The `sync` / `status` / `foreach` / `validate` verbs and the reconcile implementation** —
  `[[feat-foundry-workspace-repo-verbs]]`'s, authorized; `scripts/foundry_repo_fleet.py` is
  contract-denied here and consumed unmodified.
- **The manifest schema, the classifier, the envelope and the redaction sink** —
  `[[feat-foundry-repo-registry-formalization]]`'s, authorized; `schema/**` and
  `scripts/foundry_repo_registry.py` are contract-denied — including the `propertyNames` constraint that
  would express the registry key floor in the schema itself (R7).
- **Any new `/foundry:` verb.** This is a flow module hosted by the CLI, not a typed verb, so no
  `skills/` directory, no `docs/VERBS-QUICK-REF.md` row and no README skill-count claim moves.
- **Editing `docs/how-to/multi-repo-control-plane.md`** — three atoms already write that file; the
  attach-flow section belongs with the wizard's user-facing docs in `[[feat-foundry-bootstrap-cli]]`.
- **Removing, editing or re-keying an existing row; detaching a repo.** This flow only adds.
- **Repairing a pre-existing schema defect, a `tracked` child, or an unanchored gitignore line.** Each is
  a refusal or a reported line here; fixing them is the operator's, with the sibling report's remedy.
- **Credential and `gh` authentication setup.** The flow inherits the environment's `gh` auth state and
  never prompts for a credential; an unauthenticated `gh` is a named refusal (AC-WAF-6).

## Residuals

- **R1 — Merge order is a precondition, and the chain is transitive.**
  `[[feat-foundry-repo-registry-formalization]]` → `[[feat-foundry-workspace-repo-verbs]]` → **this
  atom**: neither `scripts/foundry_repo_registry.py` nor `scripts/foundry_repo_fleet.py` exists on disk
  yet, and every AC here consumes one or both. `[[feat-foundry-bootstrap-cli]]` may land in either order
  — this module stands alone behind its own CLI entrypoint. Operator-held sequencing, not a checkpoint.
- **R2 — Atomicity is per-file plus compensation plus ordering, not a transaction, and AC-WAF-4 says so.**
  No filesystem primitive commits two files at once. A crash between the two `rename(2)` calls — or
  inside the rollback — can leave the **benign half** (a gitignore line with no row): a root-anchored
  ignore rule for a path that does not exist. That state is **inert but unreported**: the registry
  report is row-driven and `validate`'s reverse direction only reports directories containing `.git`, so
  nothing surfaces it. Bound: the window is two syscalls wide, the surviving half cannot cause the spill,
  and the flow's own re-run is idempotent (AC-WAF-4's no-op append) so re-running completes the pair. The
  intent-journal alternative is stated and declined in *Clarifications*.
- **R3 — Two effects survive a rollback, by design.** A **completed clone** and a **created GitHub
  repository** are both irreversible-in-spirit and are never compensated (AC-WAF-5); the workspace can
  therefore end a failed run with an undeclared checkout on disk, or the operator's account with an empty
  orphan repository, in both cases named in the report. `validate`'s `undeclared-checkout` finding
  (AC-WRV-6) catches the first; nothing in this corpus sees the second — it is reported once, at the
  moment it happens, and then only the operator remembers it. Deleting either would be a worse default.
- **R4 — The role vocabulary is offered, not reasoned about.** The flow presents the closed set and
  refuses outside it; it makes no recommendation and nothing branches on the value, inheriting the
  sibling's R2 exactly.
- **R5 — `gh` is an unpinned external dependency, and its standing-versions row is OWED BEFORE SHIP.**
  No version floor for `gh` is declared anywhere in the corpus (`docs/standing-versions.md` carries no
  `gh` row), and `gh repo create`'s flags — including the structured `--json` read AC-WAF-6 depends on — verified 2026-08-02 against the installed gh: `repo create` itself has NO `--json` flag, `repo view --json nameWithOwner,url` is the structured read, so create-then-view is the conforming shape —
  could change under us. Per `CLAUDE.md` § "Standing versions & drift control", introducing a dependency
  without its pin is a defect, so **adding the `gh` row (with its research timestamp) is a named pre-ship
  obligation on the operator**, in the workspace's own matrix. It is not a checkpoint here because
  `docs/standing-versions.md` lives in this workspace, outside this atom's `target_repo`. Bound until
  then: the invocation uses only long-stable flags, and any `gh` failure is a named refusal that writes
  nothing — the same gap `[[feat-foundry-workspace-repo-verbs]]` recorded for `git` (its R9).
- **R6 — The consent moment is a confirmation, not an authentication.** Anyone who can drive this flow
  can already edit the manifest by hand; the preview makes the grant *visible*, it does not gate it.
  The remaining assurance is the operator's own terminal test-and-sign-off step (`CLAUDE.md`
  § "Delivery sign-off — operator-held, the terminal step") — a **practice, not a control**. No
  anti-gaming machinery is added to chase it.
- **R7 — The registry key floor is enforced by this flow only.** Expressing it as a `propertyNames`
  constraint in `schema/foundry-project.schema.json` would enforce it for *every* writer, but `schema/**`
  is contract-denied here and the constraint is non-additive (it could convict an existing adopter key).
  **Deferred, by name, to a follow-on on `[[feat-foundry-repo-registry-formalization]]`**, recorded here
  and in *Out of scope*. Bound until then: a hostile key written by some other means is still defused at
  the boundary by AC-WRV-3, which keeps a key out of every argv option position.
- **R8 — Redirect blindness on the create branch.** The canonical `<owner>/<name>` AC-WAF-6 binds into
  the row is whatever `gh` reports, and `gh` follows GitHub's own renames and redirects. The flow can
  see *that* the identity diverged from what was requested (and makes the operator re-confirm) but
  cannot distinguish a benign org rename from an attacker-controlled redirect. Bound: the divergence is
  never silent, and the remote still passes the admitted-form floor and the callable's re-validation.
- **R9 — A row may be added to an already-invalid manifest.** AC-WAF-2's monotonicity rule permits the
  write when the prospective document carries no *new* schema error. The workspace can therefore end a
  successful run with a valid new row inside a document that still fails validation elsewhere — reported
  in the preview, and convicted independently by `foundry-config.py check`. The alternative (blanket
  refusal) is rejected in *Clarifications* with its live counterexample.

## Changelog

- v1.0 Draft. Ship the governed-repo **attach-existing** / **create-new** flows as the reusable module
  the pre-session bootstrap CLI hosts: ordered, defaulted, confirmed field collection with flag twins;
  shape-floor + admitted-remote-form validation **before any write**; preview of the exact row, the
  exact root-anchored gitignore line and the reconcile plan; an **atomic both-or-neither** pair write
  that always precedes reconcile (never clone-first-record-after); reconcile **only** through
  `[[feat-foundry-workspace-repo-verbs]]`'s AC-WRV-10 callable, with no git command of this module's
  own; and a rollback bounded to the pair — never to a completed clone. `gh repo create` is the single
  network write this atom makes itself, and the create branch falls through into the attach path so one
  implementation writes the pair. Realizes atom #8 of
  `intake/er-onboarding-wizard-and-permission-floor.md` (2026-08-01).
- v1.1 Remediation round (consolidated 4-lens review, 2026-08-02). **Rubric splits (IDs are stable; no
  renumbering):** AC-WAF-4 keeps atomicity/durability and the new **AC-WAF-8** takes ordering + the
  reconcile seam; AC-WAF-7 keeps disclosure and the new **AC-WAF-9** takes the release/doctor
  housekeeping. **Security blocks resolved:** the gitignore line is **pinned as bytes** (leading `/`,
  one trailing `/`, git-metacharacter escaping per git's own documented rules) in a new Terminology
  entry, with the C0/C1 floor extended to `path` **and** `description` as a pre-write refusal (a newline
  in a path is a refusal, never two lines), a `tracked` target refused pre-write, and the preview
  stating where its rendering differs from the bytes (AC-WAF-2 e/h, AC-WAF-3). Atomicity is told
  honestly: the **benign half** is admitted as reachable, the write order is **pinned** (gitignore
  first, row second; rollback mirrors), and temp-in-target-directory + `fsync` of file **and**
  directory, mode preservation, symlink refusal, trailing-newline normalization, idempotent append and
  a **TOCTOU pre-image re-verify before every rename and every restore** are normative (AC-WAF-4/-5,
  R2). `repos.<key>` becomes a **collected field** with a derivation rule, a key floor, a flag twin and
  a reserved-key refusal (AC-WAF-1/-2b; the schema `propertyNames` follow-on is deferred by name in
  R7 and *Out of scope*). A **credential-bearing source is refused** rather than double-confirmed, with
  the alternative stated (AC-WAF-2d, *Clarifications*). **create-new consent is re-ordered**: an explicit
  `create repo <owner>/<name>` confirmation **before** `gh repo create`, `--dry-run` short-circuiting
  before any `gh` invocation, direct argv floors on the repository and `--template` arguments, the row
  bound from `gh`'s **structured** output with divergence re-confirmed, and the created repository named
  as the **second uncompensated effect** (AC-WAF-6, AC-WAF-5, R3, R8). **Reconcile row-scope pinned** to
  exactly the newly-written row read back from disk, with the rollback trigger pinned to **reconcile
  failure outcomes, not `finding`** — so the `mr register` skip-with-finding case persists its pair
  (AC-WAF-8, *Clarifications*). **Risks folded in:** local-path sources are **absolute-only** with the
  verbatim path written to `remote`; duplicate-path and nested-inside-any-governed-repo refusals;
  whole-prospective-document schema validation under a **monotonicity** rule with the
  pre-existing-invalid-manifest case decided and grounded in the registry sibling's R1 (AC-WAF-2a, R9);
  docstring/`--help` literals pinned verbatim (AC-WAF-7); the atomicity prior art given its POSIX
  `rename(2)`, `fsync`-durability, saga and gitignore-syntax citations plus the declined intent-journal
  alternative; the `gh` standing-versions row named as a **pre-ship obligation** (R5); local sink
  obligations added to AC-WAF-2 and AC-WAF-5; and the four plain-text bootstrap-CLI mentions upgraded to
  `[[feat-foundry-bootstrap-cli]]` (verified present).
