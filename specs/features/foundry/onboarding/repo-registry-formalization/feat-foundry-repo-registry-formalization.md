# Formalize the governed-repo registry: manifest schema + registry-integrity report  (feat-foundry-repo-registry-formalization)

> **Human-readable intent.** `.claude/foundry-project.json` `repos{}` is already the control plane's
> **manifest** — the one committed file that declares which repos the plane governs and where they live.
> Every consumer resolves against it: `scripts/foundry-wt` maps a `target_repo` key to an on-disk path,
> the worktree hooks dispatch into that path, `/foundry:authorize` grounds five floors against it.
> But the manifest is under-specified against its own industry category, and the survey of the shipped
> plugin (0.28.0) makes the gaps concrete:
>
> - **`path` is optional.** `schema/foundry-project.schema.json` declares `repos.<key>` with
>   `additionalProperties: true` and no `required` — a record with no `path` validates, then fails at
>   dispatch (`foundry-wt` exits 3, "unknown repo key").
> - **Clone provenance has no field.** There is no `remote` and no `default_branch`; where a repo came
>   from lives in `"//"` prose comments (this workspace records `lukasrepublic/ctxinfra (private,
>   default branch develop)` that way).
> - **A typo and a not-yet-cloned repo are indistinguishable.** Both present as "path is not there."
>   Nothing can tell an operator *"clone it from here"* apart from *"this entry is garbage."*
> - **Nothing pairs the manifest row with the gitignore entry**, though the pairing is load-bearing:
>   a hosted repo that is not root-anchored-ignored gets swept into the control plane's own history.
>   Live in this workspace: `repos.ctxinfra` has **no** `.gitignore` entry at all, and the three that
>   do sit under a hand-written comment explaining why they must stay root-anchored.
> - **`role` is used two incompatible ways in the first-party corpora today** —
>   `docs/how-to/multi-repo-control-plane.md` writes `"role": "product"` / `"role": "infra"` (a
>   vocabulary), while the handbook's seeded manifest writes a full prose sentence into the same key.
>   A field that means both is a field no consumer can branch on.
>
> **This atom formalizes the registry and nothing else.** It (1) tightens the manifest schema —
> `path` required, four optional fields added (`remote`, `default_branch`, `role`, `description`),
> `role` given a closed vocabulary, everything else still additive — and (2) ships a **read-only
> registry-integrity report** that answers, per entry: does the path exist, is it paired with a
> root-anchored gitignore rule (and not already swept into the index), and does the checkout's
> `origin` match the declared `remote` — with **not-yet-cloned** and **dangling** reported as distinct
> states. The `role` narrowing is this atom's **single non-additive element**, and it is order-pinned
> behind the companion atom `handbook-manifest-role-migration` (Residuals R1).
>
> **It surfaces; it never fixes.** No clone, no gitignore edit, no manifest write. The sibling
> validator `[[feat-foundry-control-plane-preflight]]` owns *convicting* a dangling path at doctor
> level (AC-CPP-1) and is not touched or duplicated here: that atom decides the doctor's verdict, this
> one owns the **schema** and the **per-entry integrity report** the reconcile verbs will later consume.

## Prior art / industry grounding

The repo-of-repos category has converged, and the convergence is the design here. **Google `repo`**
(`manifest.xml`: per-project `name`, `path`, `remote`, `revision`, `groups`), **vcstool** (`.repos`:
per-entry `type`, `url`, `version`), **`meta`** (`.meta`: a plain key→clone-URL map), and **myrepos**
(`.mrconfig`: per-repo `checkout` command) all ship the same spine: a **committed manifest declaring
`{path, remote, branch}` per repo, with children as plain independent clones — deliberately not
submodules**, which couple the parent's history to a child SHA. `repo`'s `groups` and Nx's closed
`projectType` supply the **classification-as-closed-vocabulary** precedent this atom applies to `role`.
The **standing-ref vs point-in-time-pin split** is also theirs (`repo`'s branch `revision` vs
`repo manifest -r`'s pinned snapshot): the branch-shaped `default_branch` belongs in the manifest, the
pin stays in each atom's `.foundry/build-provenance.yaml`.

**ArgoCD's registration UX supplies the operating rule** this atom's read-only posture comes from:
declaring a source and reconciling reality are separate acts, the manifest is the only truth, and
drift is *surfaced* before anything is made to match. `terraform plan -detailed-exitcode` supplies the
advisory tri-state exit contract (0 clean / 1 error / 2 findings) — already the shipped convention in
`scripts/foundry-config.py`, reused rather than re-invented. `git check-ignore -v` supplies the
gitignore oracle: git's own answer plus the matching `source:line:pattern`, so the report never
re-implements ignore-rule semantics. Git's own pattern rule ("a separator at the beginning or middle
of a pattern makes it relative to the `.gitignore`'s directory; otherwise it matches at any level") is
what "root-anchored" is defined against here — not a hand-rolled heuristic.

**Two deliberate departures from the category, both stated.** First, in `repo`/vcstool/`meta` the clone
source is effectively **mandatory** — those manifests exist in order to clone from, so an entry without
a URL is meaningless. Here `remote` stays **optional**, because this manifest's first job is dispatch
(key → path) and provenance is its second: an entry with undeclared provenance must **surface as a
reportable row** (`undeclared`, `dangling`) rather than be rejected at validation — the ArgoCD
surface-don't-reject posture again. Second, vcstool's per-entry `type: git|hg|svn` is **not** carried:
Foundry's control plane is **git-only** (every consumer runs git plumbing), so a VCS-type field would
be a field with one legal value.

The research record for this decomposition is the ER
(`intake/er-onboarding-wizard-and-permission-floor.md`, researched 2026-08-01, primary sources), whose
verdict is that the existing nested-gitignored-siblings pattern **is** the consensus and the only gap
is that Foundry's manifest is thinner than the category's.

## Security posture

**Read-only, no new trust surface.** The report writes no file and mutates no git state. Its entire git
vocabulary is config-read-only plumbing — `git check-ignore`, `git config --get`, `git ls-files`,
`git rev-parse` — pinned as a closed set in AC-RRF-6(vi), each invoked as a fixed argv with `--` before
every manifest-derived value, and it never fetches, so there is no network egress and no credential use.
It reads `.claude/foundry-project.json` and each declared path; resolution is **physical** (symlinks
followed, AC-RRF-3), and a path that escapes the physical workspace root is reported out-of-tree with
its gitignore check skipped rather than walked.

**The audited object is untrusted input.** Every value the report handles — repo keys, paths, declared
remotes, and the origin URL read out of a child checkout's own config — is attacker-influenceable in the
threat model where a governed child repo is hostile. Three consequences are normative here: the origin
oracle is `git config --get remote.origin.url`, the **raw** configured value, never `git remote get-url`,
which expands `url.*.insteadOf` **from the audited checkout's own config** and would let that checkout
redirect the comparison into a forged `match` (AC-RRF-5; the in-repo precedent is `_remote_url_raw` in
`scripts/foundry-prepublication-leak-scan.py:591-602`, with the regression fixture
`tests/test_prepublication_leak_scan.py::test_insteadof_redirect_does_not_redirect_the_probe`); the
schema carries a **shape floor** that rejects an argv-hostile value before it is ever read (AC-RRF-2);
and every emitted string passes through **one sanitizing sink** (AC-RRF-7).

**Credential non-disclosure is a first-class requirement (AC-RRF-7).** Remote URLs are the one place in
this manifest where a secret can legitimately sit — `https://<token>@github.com/o/r.git` is a real shape
— and this report prints remotes to a terminal, to `--json`, and (on failures) to stderr. Userinfo is
therefore redacted at the single emission boundary, in every channel, for the declared remote and the
discovered origin alike, and for **every** row regardless of `path_status` — a `not-cloned` row prints
the declared remote too.

**No gate reads any of this.** `scripts/foundry-config.py` is the schema's *only* consumer in the
plugin (verified: it is the sole file naming `foundry-project.schema.json`), so the tightening's entire
blast radius is that advisory drift-checker's exit code plus its pre-write `adopt` refusal. The
authorization floor, the merge floor, and the doctor's verdict are untouched — `scripts/foundry-wt`,
`scripts/foundry-authorize.py`, `scripts/foundry-doctor.py`, `hooks/**` and `.github/workflows/**` are
all denied scope.

<!-- normative -->
## Acceptance criteria

- **AC-RRF-1** *(Invariant — ubiquitous)* **(the extension is additive and self-documenting):** The
  extended `schema/foundry-project.schema.json` SHALL validate, without error, every repo record that
  carries a string `path` and carries no `role` key — including records carrying only the fields the
  pre-change schema named, and records carrying arbitrary unknown properties. No property other than
  `path` SHALL be added to any `required` list anywhere in the schema; `additionalProperties: true`
  SHALL remain in force at the manifest root, inside each `repos.<key>` record, and inside each
  `packages.<key>` record; and `packages.<key>.role` SHALL keep its existing unconstrained `string`
  type. Each newly declared property SHALL carry a non-empty `description`, and the `role` property's
  description SHALL define every value in its closed set — the schema is the only surface on which an
  adopter can discover an optional field, since this atom ships no how-to prose.

- **AC-RRF-2** *(Requirement — unwanted)* **(the schema convicts a malformed repo record):** If a
  `repos.<key>` record omits `path`, or carries a `path` that is not a string, or carries a `role`
  whose value is outside the closed set `product`, `handbook`, `infra`, `app`, `workspace`, or carries
  a non-string `remote`, `default_branch` or `description`, then schema validation SHALL fail with an
  error whose location identifies the offending `repos.<key>` — so the existing
  `scripts/foundry-config.py check` reports it, and its `adopt` refuses to baseline it, **with no code
  change to that script**. Empty strings SHALL be rejected for `path`, `remote` and `default_branch`
  (a blank path is the same defect as an absent one). Validation SHALL likewise fail when `path`,
  `remote` or `default_branch` begins with `-`, and when `remote` or `default_branch` contains a C0 or
  C1 control character (`U+0000`–`U+001F`, `U+007F`–`U+009F`) — a **shape floor** expressed as a
  JSON-Schema `pattern`, reusing the reviewed vocabulary of
  `scripts/foundry-prepublication-leak-scan.py` (its `_C0_C1_CONTROL_RE` class, and the anchored
  first-character rule of its `SCP_LIKE_AUTHORITY_RE` that rejects a dash-leading authority). The floor
  exists because these values reach an argv position and a terminal, not because the schema judges URL
  semantics — see *Out of scope*.

- **AC-RRF-3** *(Requirement — event)* **(the report distinguishes not-yet-cloned from dangling):**
  When the registry report runs over a manifest, it SHALL emit **exactly one row per `repos.<key>`
  entry**, classifying its declared path as exactly one of: **`present`** (resolves to an existing
  directory); **`not-cloned`** (does not resolve, **and** a non-empty `remote` is declared) — reported
  with the declared remote and the path to clone it to; **`dangling`** (does not resolve, and no
  `remote` is declared) — reported with the remedy that the entry needs a `remote` or removal; or
  **`outside-workspace`** (resolves outside the workspace root). Resolution SHALL be **physical**: the
  declared path and the workspace root are each resolved with symlinks followed, mirroring
  `scripts/foundry-wt`'s `cd … && pwd -P` confinement, so an entry whose *physical* resolution lies
  outside the *physical* root SHALL be `outside-workspace` even when its declared, lexical path appears
  to sit inside it. `not-cloned` and `dangling` SHALL be distinct row values and SHALL carry distinct
  remedy text, because they are distinct operator actions. Both are findings for exit-code purposes
  (AC-RRF-6); the distinction lives in the row, not in the exit code. Every remedy SHALL be rendered as
  **labelled fields** — the declared remote and the target path as separate, individually labelled
  values — rather than as a runnable command line; if any channel does emit a clone command line, every
  interpolated manifest value in it SHALL be shell-quoted.

- **AC-RRF-4** *(Requirement — event)* **(pairing and index membership are reported, never repaired):**
  When the report evaluates an entry whose declared path resolves to a **strict** subdirectory of the
  workspace root, it SHALL classify the pairing using `git check-ignore -v --no-index -- <path>` as the
  oracle — a **path computation, independent of whether the directory exists** — and, for an entry whose
  path is `present`, one `git ls-files -- <path>` probe, yielding exactly one of: **`ok`** (ignored by a
  pattern that is root-anchored — one containing a `/` at any position other than its last character,
  per git's own pattern rule — **and**, where the probe ran, `git ls-files` returned empty);
  **`unanchored`** (ignored, but only by a pattern with no such separator, which also matches a
  same-named directory at any depth) — reported with the matching `source:line:pattern`;
  **`unpaired`** (not ignored) — reported with the exact root-anchored line to add; **`tracked`**
  (`git ls-files -- <path>` returned a non-empty list) — reported with the **count** of tracked paths
  and the remedy that the entry must leave the control plane's index, because git's ignore rules do not
  apply to tracked paths, so the pairing is defeated whatever the rule says; or **`unknown`** (the
  oracle was unavailable — this value is an instance of AC-RRF-6(iv)'s degraded rule, not a second rule,
  and is verified there). `tracked` SHALL take precedence over `ok`, `unanchored` and `unpaired`, and
  SHALL be a finding. The self-entry whose path resolves to the workspace root itself, and any entry
  classified `outside-workspace`, SHALL be reported `n/a`, never `unpaired`. The report SHALL make no
  edit to any `.gitignore`, any manifest, or any git state — an instance of AC-RRF-6(i), restated here
  because the ignore oracle is the one check with a plausible auto-fix.

- **AC-RRF-5** *(Requirement — event)* **(the origin oracle is the raw configured value, and the match
  is normalized):** When an entry declares a non-empty `remote` and its path resolves to a git checkout,
  the report SHALL obtain that checkout's origin URL by running `git config --get remote.origin.url`,
  whose result is the **literal stored value**, and SHALL compare it against the declared `remote`,
  classifying the row **`match`** or **`mismatch`** and printing both URLs (through AC-RRF-7's sink) on
  a mismatch. The oracle SHALL be `git config --get` rather than `git remote get-url` because the latter
  expands `url.<base>.insteadOf` **from the audited checkout's own configuration** (documented git
  behaviour), which would let the audited object redirect the comparison and forge a `match`; the
  in-repo precedent is `_remote_url_raw` in `scripts/foundry-prepublication-leak-scan.py:591-602`.
  Accordingly, when the checkout's own config carries a `url.<base>.insteadOf` entry that rewrites its
  origin, the report SHALL classify the row from the **unrewritten** configured value — i.e. an origin
  identical to the declared `remote` SHALL classify `match`, and a differing one `mismatch`, regardless
  of any `insteadOf` rewrite present. Comparison SHALL be normalized so that the scp-form
  (`git@host:owner/repo.git`), the `ssh://` form and the `https://` form of the same repo compare equal:
  host compared case-insensitively, any userinfo and port ignored, and a leading `/`, a trailing `/` and
  one trailing `.git` stripped from the path. An entry with no declared `remote` SHALL be `undeclared`,
  and a path that resolves but is not a git checkout SHALL be `not-a-checkout`; neither is a mismatch.

- **AC-RRF-6** *(Invariant — ubiquitous)* **(a read-only advisory with a typed contract and a degraded
  oracle):** The entrypoint `scripts/foundry_repo_registry.py` SHALL be independently invocable as a
  CLI over an explicit `--root`, and SHALL satisfy all six: (i) **it writes nothing** — no file under
  the resolved root is created, modified or deleted by any invocation; (ii) its exit code is
  **advisory** and follows the shipped tri-state convention — `0` when every row is clean, `2` when any
  row carries a finding, `1` when the manifest is absent, unreadable or unparseable — and both the
  module docstring and `--help` SHALL state that no gate consumes this exit code; (iii) `--json` emits a
  single JSON **object** `{degraded, degraded_reason, rows}` — never a bare array — where `rows` is one
  object per entry carrying at least the keys `key`, `path`, `path_status`, `gitignore` and `origin`
  with the values AC-RRF-3/-4/-5 define, so a later reconcile verb consumes structure rather than parsed
  prose and cannot read a degraded run as a complete one; (iv) when the oracle is unavailable — `git`
  not on `PATH`, or the root is not a git work tree — the affected checks SHALL report `unknown`, these
  SHALL be excluded from the finding count, and both channels SHALL state that the run was degraded
  (`degraded: true` with a non-empty `degraded_reason`); (v) when the manifest parses but its `repos`
  key is absent, empty, or not an object, the report SHALL emit the named outcome **`no-repos`** in both
  channels with `rows: []` and exit `2`, since exit `0` is reserved for a run in which every declared
  entry was checked and found clean, and a report that checked nothing SHALL be distinguishable from
  one that checked everything; (vi) every git invocation SHALL be a fixed argv executed without a
  shell, SHALL place `--` before every manifest-derived value, and SHALL be drawn only from the closed,
  config-read-only plumbing set `git check-ignore -v --no-index -- <path>`,
  `git config --get remote.origin.url`, `git ls-files -- <path>` and `git rev-parse` — a set in which
  no member fetches, writes a ref, writes an object, or mutates configuration.

- **AC-RRF-7** *(Invariant — ubiquitous)* **(one emission sink: credentials redacted, control sequences
  neutralized):** Every string value the report emits SHALL reach stdout, stderr and `--json` only by
  passing through **one** sanitizing function applied at the emission boundary — including on error and
  uncaught-exception paths, where the report SHALL emit a sanitized error line in place of a raw
  traceback — and SHALL be applied to every emitted field: the repo key, the declared remote, the
  discovered origin, paths, remedy text, `degraded_reason`, and `source:line:pattern`, for **every** row
  regardless of `path_status` (a `not-cloned` row prints its declared remote too). Within each field
  value the sink SHALL (a) replace the userinfo component of each remote form the corpus admits —
  `https://[user[:pass]@]host/…`, `ssh://[user[:pass]@]host/…`, and the scp-form
  `[user[:pass]@]host:path` — with the fixed literal `***`, a constant that is neither derived from nor
  length-matched to the value it replaces; and (b) replace every C0 and C1 control character
  (`U+0000`–`U+001F`, `U+007F`–`U+009F`) and every ANSI CSI escape sequence with a printable escape, so
  that no field value can repaint the terminal, move the cursor, or open a line the report did not
  itself produce (the report's own record and line separators are unaffected, being structure rather
  than field content). For a clean run, a findings run and an error run alike, and for each of the three
  remote forms, the raw userinfo SHALL be absent from **both** stdout and stderr.
<!-- /normative -->

## Design / notes

- **A new module, not an extension of the drift checker.** `scripts/foundry-config.py` answers "has
  this config changed, and is it well-formed"; the registry report answers "does the manifest match
  reality." Folding the second into the first would entangle two exit-code contracts and force an edit
  to the one script whose docstring pins "NEITHER VERB EVER WRITES A MANAGED CONFIG FILE." Keeping it
  out of scope also makes AC-RRF-2's strongest claim structural: the tightened schema must flow into
  the existing checker **with no code change**, which is only provable if that file is denied scope.
- **`kind` is untouched.** `kind` carries free-form tech shape (`single-app`, `plugin-repo`,
  `infra-repo`) and stays unconstrained. `role` carries the governance vocabulary. Merging them would
  break every shipped manifest for no gain.
- **`--no-index` on the ignore oracle, and a separate probe for the index.** `--no-index` makes the
  answer the *rule*, not the index state, so an entry whose directory is not yet cloned still gets a
  real pairing answer — exactly the `not-cloned` case an adopter is most likely to get wrong. But that
  same flag is what makes an ignore-only report blind to the failure that actually leaks: a child
  already **tracked** in the control plane's history, where ignore rules have no effect at all. Hence
  the one extra `git ls-files -- <path>` probe per `present` entry (AC-RRF-4) and the distinct `tracked`
  row. The probe is cheap, read-only, and answers the question the workspace has already been bitten
  by; `ok` therefore means "root-anchored rule **and** not in the index", not "a rule exists".
- **Path normalization.** A declared `./my-app` (the form the shipped how-to example uses) and `my-app`
  are the same entry; both normalize before the gitignore and existence checks. Normalization is
  lexical *then* physical (AC-RRF-3): the lexical form drives the `.gitignore` pattern the remedy line
  suggests, the physical form drives confinement.
- **Why `no-repos` is exit 2, not exit 0 or 1.** Exit `1` is "I could not read the manifest"; exit `0`
  is a positive claim that every declared entry was checked. A manifest with no `repos` key supports
  neither claim, and the failure mode that matters is a Wave-2 verb branching on the exit code alone and
  concluding "registry clean" from a run that examined nothing. The named outcome plus exit `2` makes
  that impossible without a special case in the consumer.
- **One sink, not per-call-site redaction (AC-RRF-7).** Redaction scattered across call sites fails the
  moment a new print is added — which is why the sink is normative at the *emission boundary* and why
  stderr and the exception path are named explicitly: those are precisely the paths a per-call-site
  discipline forgets.

## Clarifications

- **Q: `role` is already in use as free prose in the handbook's seeded manifest — does closing the set
  break it?** **Yes, deliberately, and the migration is a named dependency.** Verified: the foundry
  how-to on `main` uses `role: "product"` / `"infra"` (vocabulary), while `agentic-handbook`'s
  `.claude/foundry-project.json` and its `docs/SETUP.md` / `docs/control-plane.md` examples put a prose
  sentence in the same key. Narrowing a field's domain is a breaking change under every schema-evolution
  convention, so the two standard remedies are a version bump or a new field. Neither is worth its cost
  here: the adopter population is the two first-party workspaces (the repos are pre-launch and private),
  the prose has a proper home in the **new `description` field**, and shipping a field that means two
  things is the defect. Resolved: close the set now, leave `schema_version` at 1, and **pin the
  sequencing** — the prose migration is a named prerequisite atom, `handbook-manifest-role-migration`
  (`target_repo: agentic-handbook`), and the `role` narrowing ships only in a plugin release that the
  handbook template migration already precedes. R1 states the binding order; the Changelog names
  `role` as the atom's single non-additive element so a release manager reads it without opening the
  ACs.
- **Q: `product` vs `app` — which does a single-app product use?** Either; the distinction is
  descriptive today. `product` is the repo that ships the product, `app` a runnable
  application/service surface. **No consumer branches on `role` in this atom** — the closed set exists
  so that the Wave-2 verbs and the wizard *can* — so a debatable label is cosmetic here, not a defect.
- **Q: Should the report convict, i.e. change the doctor's verdict?** **No — resolved.** Conviction at
  doctor level is `[[feat-foundry-control-plane-preflight]]`'s AC-CPP-1, pending authorization. This
  atom reports; duplicating that AC would create two owners for one verdict.

## Out of scope / non-goals

- **The bootstrap wizard and its attach-repo flow** (`onboarding-bootstrap-cli`,
  `wizard-attach-repo-flow`) — Wave 3 of the ER's decomposition. This atom writes the schema the wizard
  will write *into*, and no wizard code.
- **The `sync` / `status` / `foreach` / `validate` verbs** (`workspace-repo-verbs`) — Wave 2, per the
  ER. Nothing here clones, fetches or reconciles; `--json` exists so those verbs can consume this
  report instead of re-deriving it.
- **Changing the doctor's verdict on a dangling path** — owned by the sibling validator; see
  *Clarifications*.
- **Shipping or editing `docs/how-to/multi-repo-control-plane.md`** — the registry chapter belongs with
  the ER's `control-plane-pattern-docs` atom, which aligns both corpora at once, and that file is
  already in the pending preflight atom's write scope. Two atoms editing one doc concurrently is a
  merge conflict, not a spec. AC-RRF-1 makes the schema self-documenting so this atom ships no
  undocumented field.
- **Index and worktree state beyond the one tracked probe.** AC-RRF-4's `git ls-files -- <path>` probe
  answers exactly one question — is this child already inside the control plane's index — and nothing
  else. Ahead/behind counts, dirty worktrees, stashes and submodule state belong to the Wave-2 `status`
  verb.
- **URL semantics — shape floor yes, URL semantics no.** AC-RRF-2's `pattern` rejects an argv- and
  terminal-hostile *shape* (a leading `-`, a C0/C1 control character) and takes no view whatsoever on
  scheme, host, reachability or well-formedness: a remote may legitimately be a local path, an ssh
  alias or a `git://` URL. The schema requires a non-empty, shape-safe string; the report does the only
  semantic check there is (does it match the checkout's origin).
- **Reverse-direction discovery — a checkout on disk with no manifest entry.** This atom reads the
  manifest and asks reality about each entry; it never enumerates the workspace root looking for
  un-declared checkouts (the vcstool `export` analog, which reconstructs a manifest *from* a tree).
  That direction is deferred, by name, to the Wave-2 `validate` verb in `workspace-repo-verbs`, whose
  ER charter is the manifest ⟷ reality ⟷ gitignore round-trip. Named here so the asymmetry is a
  decision rather than an oversight.

## Residuals

- **R1 — The handbook migration is a NAMED PREREQUISITE ATOM, and the order is binding.** The companion
  atom is **`handbook-manifest-role-migration`** (`target_repo: agentic-handbook`): it moves the prose
  out of `role` into the new `description` field in `agentic-handbook`'s seeded
  `.claude/foundry-project.json` (one live `repos.workspace.role` prose value), in its `docs/SETUP.md`
  and `docs/control-plane.md` examples, and in its `CLAUDE.md` `path`/`kind`/`role` line. **Binding
  order: the `role` narrowing ships only in a plugin release that the handbook migration already
  precedes** — otherwise a workspace created from the template reports schema findings in
  `foundry-config.py check` (exit 2, advisory) and is **refused** by `foundry-config.py adopt` on its
  first day. `target_repo: agentic-foundry` means this atom cannot make that edit, so the order is
  operator-held sequencing, not a checkpoint. Related and pre-existing, to be swept by the same
  migration: the handbook's seeded `repos` map also holds two *string* comment values (`"/"` and
  `"//hosted-repo-example"`) where the **pre-change** schema already requires an object — that manifest
  fails validation today, before this atom's tightening, and the tightening neither causes nor worsens
  it. **This residual has not been reflected into the ER's atom list**
  (`intake/er-onboarding-wizard-and-permission-floor.md` §"Atoms this decomposes into", which stops at
  #9) — that edit is outside this atom's spec directory and is flagged for the operator.
- **R2 — The closed `role` set is a guess about future consumers.** Nothing branches on it yet, so its
  five values are validated only against the two first-party corpora. Adding a value later is additive
  and cheap; removing one is not. Bound: the set is exactly the ER's, plus `workspace` for the
  self-entry a **freshly seeded** manifest carries. That self-entry is a *convention with zero live
  consumers*, not a guaranteed row: `scripts/foundry-wt` hardcodes the `workspace` key to the resolved
  root before it reads `repos{}` at all, and this workspace's own flagship manifest — the most-exercised
  one in the corpus — has **no** `repos.workspace` entry. Any later consumer that assumes the self-entry
  exists is assuming something the corpus does not support.
- **R3 — The gitignore oracle is exercised on a case-sensitive filesystem.** A macOS/Windows case-fold
  difference in an ignore pattern is not exercised by the suite; git itself is the oracle, so the
  untested platform behaves as git does rather than as a hand-rolled matcher would.
- **R4 — `origin` is the only remote compared.** A checkout whose upstream is named something other
  than `origin` reports `not-a-checkout`-adjacent noise rather than a mismatch. Bound: `origin` is the
  name every clone path in the corpus produces; multi-remote support belongs with the Wave-2 verbs.
- **R5 — Narrowing after launch will need the version lever.** This atom spends the pre-launch window
  to narrow `role` without a `schema_version` bump. Once the plugin is public, the same move requires
  a bump; recorded so the precedent is not read as a general licence.
- **R6 — `git config --get` reads the merged config, not only the checkout's.** A global
  `remote.origin.url` in the operator's own `~/.gitconfig` would be returned for a checkout that has
  none locally. This is not the threat AC-RRF-5 addresses (the audited object cannot set the operator's
  global config), and `--local` was not pinned because the cited in-repo precedent uses plain `--get`;
  recorded so a later hardening pass has the fork already stated.

## Changelog

- v1.0 Draft. Formalize `repos{}` against the repo/meta/vcstool manifest consensus: `path` required,
  `remote` / `default_branch` / `role` (closed set) / `description` added as optional, additive
  compatibility preserved; plus a read-only `scripts/foundry_repo_registry.py` reporting per-entry
  presence (distinguishing not-cloned from dangling), root-anchored gitignore pairing, and
  origin-match with credential redaction. Realizes the ER's repo-nesting-half atom #6
  (`intake/er-onboarding-wizard-and-permission-floor.md`, 2026-08-01). The sibling validator
  `[[feat-foundry-control-plane-preflight]]` is cited, not modified or duplicated.
- v1.1 Remediation round (4-lens review, 2026-08-02). **Non-additive elements: exactly one — the `role`
  closed set**, order-pinned behind the named prerequisite atom `handbook-manifest-role-migration`
  (R1); everything else in the schema change remains additive. **Blocks resolved:** the origin oracle is
  pinned to `git config --get remote.origin.url` (raw value; `git remote get-url` expands the audited
  checkout's own `url.*.insteadOf`), citing `_remote_url_raw` and mirroring its regression fixture;
  redaction is lifted out of AC-RRF-5 into a new **AC-RRF-7** — one emission sink covering stdout,
  stderr, `--json` and the exception path, over all three remote forms, for every row. **Risks folded
  in:** a JSON-Schema shape floor on `path`/`remote`/`default_branch` (AC-RRF-2), physical
  symlink-resolved path confinement and fields-not-command-lines rendering (AC-RRF-3), a `git ls-files`
  tracked probe as a distinct finding (AC-RRF-4), and a `{degraded, degraded_reason, rows}` envelope,
  a named `no-repos` outcome, and pinned `--`-guarded read-only git argv (AC-RRF-6 (iii), (v), (vi)).
  Corrections: R2 rescoped (the flagship manifest has no self-entry; `foundry-wt` hardcodes the key),
  reverse-direction discovery named as deferred to Wave 2, prior art extended with the optional-`remote`
  and git-only departures.
