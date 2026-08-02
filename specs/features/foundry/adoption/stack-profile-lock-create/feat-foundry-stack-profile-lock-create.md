# Stack-profile lock creation — the missing half of the lock lifecycle (feat-foundry-stack-profile-lock-create)

> **Human-readable intent.** Foundry ships four stack profiles (`aws-eks-karpenter`, `node-web`,
> `python-uv-lib`, `python-uv-service`) and **no way to adopt one**. `write_lock()` in
> `scripts/foundry-stack-profile.py` has exactly one caller, `relock_lock()`, which refuses when no
> lock exists ("nothing to relock"). The CLI offers `--validate`, `--load` and `--relock` — there is
> no create.
>
> The consequence is that a fresh adopter cannot reach anything gated on an active profile:
> `/foundry:verify` (SKIPs with no lock), the `id-*` lane's `infra_binding`, and — until the sibling
> atom lands — `/foundry:certify-local`. Four shipped profiles are dead weight.
>
> The industry shape is unambiguous: **creation belongs to `init`, refresh is the flagged
> variant.** Terraform *"automatically creates or updates the dependency lock file each time you
> run `terraform init`"*; `poetry install` and `bundle lock` create when absent; `init -upgrade`,
> `poetry update` and `bundle lock --update` are the explicit refreshes. Foundry shipped only the
> refresh. This atom adds the create.
>
> **Threat model — TRUSTED OPERATOR; supply-chain-adjacent. SECURITY-REVIEW REQUIRED** (it writes
> a lock that subsequently governs which shipped profile's commands `/foundry:verify` executes, so
> the create path must carry the same trusted-resolve guardrails `relock` already enforces).

<!-- normative -->
## Acceptance criteria

- **AC-SPLC-1** *(Requirement)*: WHEN `foundry-stack-profile.py --lock <id>[,<id>…]` is invoked and
  no `.foundry/stack-profile.lock` exists, THEN it SHALL resolve each named id against the trusted
  `packs/stack-profiles/` tree and atomically write a lock via `write_lock()` — never a second
  writer. The per-entry field set SHALL **NOT** be enumerated by this criterion. It SHALL be
  produced by the **same code path `relock_lock()` uses**, so that every digest `resolve_lock()`
  verifies is present, and every digest it does not verify is absent. Enumerating the shape here is
  the defect this wording exists to prevent: a draft that named only
  `{version, sha256, blueprints_sha256}` would omit `standing_versions_sha256`, which
  `resolve_lock()` verifies (`scripts/foundry-stack-profile.py:497`) and which **three of the four
  shipped profiles declare** — `aws-eks-karpenter`, `python-uv-lib`, `python-uv-service` — so the
  enumerated shape would fail AC-SPLC-6's round-trip for three quarters of the shipped catalogue.

- **AC-SPLC-1b** *(Invariant)*: **One entry-builder, shared — not duplicated.** The per-entry
  construction SHALL be extracted into a single helper used by **both** `--lock` and
  `relock_lock()`. The existing hand-duplication between `relock_lock()` and `resolve_lock()` is
  already flagged in the source as a divergence hazard (*"if a NEW trusted-resolve guardrail is
  ever added to resolve_lock, add it here too, or relock could write a lock that then fails
  resolve_lock"*, `scripts/foundry-stack-profile.py:545-547`). Adding a **third** hand-copy is the
  concrete way this atom would ship the next occurrence of that hazard, and the digest omission
  above is proof the hazard is live rather than theoretical.

- **AC-SPLC-2** *(Invariant)*: The create path SHALL apply the **same trusted-resolve guardrails**
  `relock_lock()` enforces — schema-valid, present in `packs/`, `requires_core` satisfied by the
  running core, and no bundle leak into the core plugin `skills/` tree — refusing the whole
  operation if **any** named id fails. There SHALL be no id-set for which `--lock` writes a lock
  that `resolve_lock()` would then reject.

- **AC-SPLC-3** *(Invariant)*: **VALIDATE-BEFORE-WRITE.** Every named id SHALL be checked before
  any bytes are written; on any failure the command SHALL exit non-zero with **no lock file
  created** and no partial or `.tmp` residue left behind.

- **AC-SPLC-4** *(Requirement)*: WHEN `--lock` is invoked and a **parseable**
  `.foundry/stack-profile.lock` already exists, THEN it SHALL refuse without modifying it, naming
  `/foundry:relock` as the refresh path. Create and refresh stay separate operations; `--lock`
  never silently re-points an adopter's existing lock.

- **AC-SPLC-4b** *(Requirement)*: WHEN the lock file is **present but unparseable** (malformed
  JSON, or structurally invalid — not a mapping, or no `profiles` list), THEN `--lock` SHALL
  refuse with a message that names the file as **corrupt**, distinguishes that case from
  AC-SPLC-4's refuse-on-existing, and states the remedy: remove the file, then re-run `--lock`.
  Defining "already exists" as mere file presence — as the first draft did — **strands the
  adopter**: `--lock` refuses and points at `relock`, while `relock_lock()` cannot parse the file
  either (`load_lock` does a bare `json.load`, `scripts/foundry-stack-profile.py:442`), so both
  documented paths refuse and neither names a way out. `--lock` SHALL still **not** overwrite the
  corrupt file itself; deleting an operator's file to recover from a parse error is the kind of
  silent repair this floor exists to avoid.

- **AC-SPLC-5** *(Requirement)*: WHEN an unknown profile id is named, THEN the refusal SHALL list
  the ids that **are** available under `packs/stack-profiles/`, so a typo is self-correcting
  rather than a lookup.

- **AC-SPLC-6** *(Invariant)*: The lock `--lock` writes SHALL round-trip: `resolve_lock()` against
  the freshly written lock SHALL succeed and return exactly the named profiles, and
  `/foundry:doctor`'s `stack-profile-lock` check SHALL report `ok` against it. A create that
  produces a lock the doctor then reds is the defect this criterion exists to prevent.

- **AC-SPLC-7** *(Requirement)*: `/foundry:init` SHALL offer profile selection and, on the
  operator's choice, invoke this scripted create path — never hand-writing a lock in prose. WHEN
  the operator selects no profile, THEN init SHALL complete normally with no lock, which remains a
  valid `DOCTOR-GREEN` state.

- **AC-SPLC-8** *(Invariant)*: A workspace with **no** `.foundry/stack-profile.lock` SHALL remain
  fully supported and `DOCTOR-GREEN` — the lock is opt-in. This atom adds a way in; it does not
  make a profile mandatory.
<!-- /normative -->

## Prior art / industry grounding

Researched 2026-07-31 via the research-first discipline.

**Creation belongs to `init`; refresh is the flagged variant.** Terraform's dependency-lock
documentation states that [*"Terraform automatically creates or updates the dependency lock file
each time you run the `terraform init` command"*](https://developer.hashicorp.com/terraform/language/files/dependency-lock),
with no separate create command, and `terraform init -upgrade` as the explicit refresh. Poetry
creates `poetry.lock` on `poetry install`/`poetry lock` when absent and refreshes on
`poetry update`. [Bundler's `bundle lock`](https://bundler.io/man/bundle-lock.1.html) creates or
updates without installing, and *"if you run `bundle lock` with `--update` … bundler will ignore
any previously installed gems and resolve all dependencies again,"* whereas without the flag it
respects the existing lockfile. The uniform shape across all three: **the default operation
creates when absent and respects what exists; changing pinned versions requires an explicit
flag.**

**The declaration→lock direction.** In every tool the configuration declares intent
(`required_providers`, `pyproject.toml`, `Gemfile`) and the lock records the resolution. Foundry's
equivalent declaration is the operator's profile choice at init; this atom supplies the resolution
step that was missing, rather than inventing a new declaration file.

**Why refuse-on-existing rather than update-in-place (AC-SPLC-4).** Terraform folds both into one
verb because `init` is idempotent and re-run constantly. Foundry already ships a *separate*
refresh verb (`/foundry:relock`) with downgrade refusal and version-advance semantics; adding
update-in-place to `--lock` would duplicate it and create two paths that can disagree. Bundler's
respect-the-existing-lockfile default is the closer precedent, and keeping the operations
separate preserves `relock`'s guarantees.

## Out of scope

- **Changing what a stack profile contains** or adding new profiles to `packs/`. This atom makes
  the existing four adoptable; it does not author a fifth.
- **The boot-recipe precedence change** — that is the sibling atom
  (`feat-foundry-boot-recipe-precedence`), which makes certification reachable *without* a lock.
  The two are independent; neither blocks the other.
- Any change to `relock_lock()`'s refresh semantics, its downgrade refusal, or the
  `standing-versions` digest check. Untouched.
- Auto-selecting a profile by sniffing the repo. The operator names the profile; guessing a
  stack and then executing its commands is exactly the inference this floor avoids.

## Residuals

- `--lock` writing a lock the operator did not intend (a mistyped-but-real id, e.g.
  `python-uv-lib` for `python-uv-service`) is caught only by AC-SPLC-5's available-ids listing and
  the operator reading it. The two profiles' commands differ, so the error surfaces at the first
  `/foundry:verify` rather than silently. Stated, not hidden.
- The pre-existing hand-duplication between `relock_lock()` and `resolve_lock()` is **not** repaired
  by this atom — AC-SPLC-1b only forbids adding a third copy, by making `--lock` share `relock`'s
  entry-builder. The two remaining copies stay as they are, guarded by AC-SPLC-6's round-trip;
  consolidating them touches `resolve_lock`'s verification path and belongs in its own atom.
- A corrupt lock is *reported* (AC-SPLC-4b) but not *repaired*. The operator deletes the file. That
  is deliberate: automatic deletion of an unparseable file the operator may want to inspect trades
  a clear one-line remedy for a silent data loss.

## Changelog

- v1.1 Revision after single-pass review (2 Blocks against this spec).
  **(B8)** AC-SPLC-1 enumerated `{version, sha256, blueprints_sha256}` and omitted
  `standing_versions_sha256`, which `resolve_lock()` verifies and which **3 of the 4 shipped
  profiles declare** — building to the enumerated shape would have failed AC-SPLC-6's round-trip
  for three quarters of the catalogue. The criterion no longer enumerates a field set; it requires
  the entry to be built by the same code `relock_lock()` uses, and new **AC-SPLC-1b** mandates that
  shared helper rather than a third hand-copy.
  **(B9)** AC-SPLC-4 defined "already exists" as file presence, which strands an adopter holding a
  corrupt lock: `--lock` refuses and points at `relock`, and `relock` cannot parse it either. New
  **AC-SPLC-4b** splits the corrupt case out with its own message and a stated remedy.
- v1.0 Draft.
