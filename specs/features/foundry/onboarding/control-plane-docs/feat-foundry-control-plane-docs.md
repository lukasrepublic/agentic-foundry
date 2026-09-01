# The plugin ships the control-plane pattern documentation, and its enforcement claims are derived from the tree  (feat-foundry-control-plane-docs)

> **Human-readable intent.** A plugin-only adopter — someone who ran `claude plugin install` and never
> cloned the workspace template — has no complete description of the pattern the factory is built for:
> one control-plane workspace hosting several code repos as **gitignored sibling clones named by a
> manifest**. The full treatment lives in the handbook (`docs/control-plane.md`, `docs/SETUP.md`), which
> that adopter does not have. The plugin's own `docs/how-to/multi-repo-control-plane.md` exists on `main`
> at v1.0.1 and covers the layout, the session rule and the dispatch flow — but it does not carry the
> registry field reference, the gitignore⟷manifest **pairing** rule, the clone-before-register
> **ordering** rule, the `--add-dir` question a nested-repo user actually asks, or — the load-bearing
> omission — an honest statement of **what is machine-enforced versus what is practice**.
>
> **The honesty half is why this atom is worth an AC.** A doc that overstates enforcement is worse than a
> missing doc: it tells the operator a boundary is held when nothing holds it. The shipped tree says
> (verified this session against `agentic-foundry` at v1.0.1):
> - **enforced** — `scripts/foundry-wt resolve` exits non-zero for an unknown `repos{}` key, for a `path`
>   that is not an existing directory, and for a path that escapes the workspace root;
>   `hooks/foundry-worktree-create.sh` **calls** `foundry-wt bind-check` on its redirect path and drops
>   the dispatch fail-closed — no worktree — when the manifest key disagrees with the atom's authorized
>   `target_repo`; and that `target_repo` sits inside the region `contract_sha256` covers, so moving it
>   invalidates the freeze.
> - **NOT enforced** — with a `target_repo` that does not resolve, `scripts/foundry-authorize.py` prints
>   `warn: … degraded` / `SKIPPED` for every venue-grounded floor (surface⊆scope, doctor-row baseline,
>   system-grounding, `allowed_paths` grounding, checkpoint-locator grounding) and **freezes anyway**,
>   incrementing `auth_seq`. The handbook's `SETUP.md` currently says such a contract "fails closed at
>   authorization". It does not. The plugin doc must not repeat that error.
> - **NOT validated today** — `scripts/foundry-doctor.py` contains no read of `repos{}` at all, so a
>   dangling entry stays `DOCTOR-GREEN`; and `--session-start` exits zero unconditionally (fail-open).
>   `[[feat-foundry-control-plane-preflight]]` changes the first half when it ships, which is exactly why
>   this atom's honesty claims are **derived by exercising the code** rather than written down: whichever
>   atom lands first, the doc that disagrees with the tree goes RED.
>
> The preflight contract already names this file in its `scope.allowed_paths` and grades it with the
> `AC-CPP-8` checkpoint, so the path is load-bearing for a sibling atom: this atom pins it.

## Prior art / industry grounding

**The repo-of-repos / meta-repo consensus.** Google `repo` (`manifest.xml`), the `meta` tool (`.meta`),
vcstool (`.repos`) and `myrepos` all converge on one shape: a **committed manifest in the parent declares
the repo set**, children are **plain independent clones at manifest-declared paths**, and the parent
**gitignores them — deliberately not submodules**, because a submodule pins the parent to a child commit
and entangles the two histories, which is the wrong coupling for a hub of independently released repos.
ArgoCD is cited for **one half only** — *the manifest is the only truth*, with reconciliation to the
filesystem a separate, explicit step a controller performs. Foundry ships **no reconcile verb** (ER
atom 7, Wave 2), so the doc's **clone-before-register** ordering is the manual stand-in for that
missing reconciler and deliberately runs *opposite* to declare-then-reconcile; it is **not** an
application of it. This is the ER's research verdict (2026-08-01, primary sources) and it matches what
Foundry already implements — so this atom **documents the standard pattern**, it does not invent one.

**Docs as a shipped, tested artifact — the lint-against-source branch.** The how-to genre (Diátaxis)
plus the docs-as-code consensus that documentation claims are **checked in CI** is the reason the
honesty section is a *test*, not a promise. That consensus has two branches: **executing examples
embedded in the doc** (Rust doctests, Python `doctest`) and **linting documentation against the source
it describes** (link checkers, generated-reference diffing, the wider "docs tests in CI" practice).
This atom is squarely the second — it executes no example embedded in the doc; it derives facts from
the shipped tree and convicts the prose. The local instance of that branch already ships:
`[[feat-foundry-anti-doc-rot-ci]]`
built `tests/test_doc_claims.py` as a `COVERED_CLAIMS` registry where every claim carries `derive(root)`
(ground truth from the tree), `check(root)` (raises naming the claim id) and `make_mutated(tmp)` (a
mandatory negative control). This atom **extends that registry** rather than writing a parallel checker —
building a second derive-check harness beside a shipped one would be novelty with no gap to justify it.

**Session scope for nested repos.** Claude Code resolves its corpus from the session's project directory
and treats directories nested under the project root as already in scope; `--add-dir` exists for
directories *outside* that root. Documenting "start at the root, no `--add-dir`" is therefore describing
the platform's own model, not a Foundry convention.

## Security posture

Pure documentation plus two test modules. **No network, no credential handling, no gate decision
changes**: `scripts/**`, `hooks/**`, `schema/**`, `skills/**` and `.github/workflows/**` are all denied
by this atom's scope, so no enforcement surface can move under cover of a docs change. The tests read
tracked files and materialize copies under pytest's `tmp_path` for the negative control, which is the
idiom `tests/test_doc_claims.py` already ships.

**Disclosed: two derivations now EXECUTE shipped scripts** (AC-CPD-3(2) runs `foundry-authorize.py`
through a real freeze; AC-CPD-3(3) runs `foundry-doctor.py --session-start`). This is a *test-side* exec
seam, not a new product seam, and it carries one real hazard: an authorize run that resolved the real
workspace would write an `authorized:` trailer and a `.foundry/security-audit.jsonl` line into the live
tree. The control is hermeticity — both runs SHALL point `CLAUDE_PROJECT_DIR` **and** the working
directory at a `tmp_path` fixture workspace with its own operator registry, and freeze only a fixture
contract (Design / notes). **This does not weaken front-authorization**: the fixture freeze is a
throwaway file under `tmp_path` that no dispatch, gate or merge path can ever read, and no real spec
becomes authorized by it.

The security-relevant risk here is **the doc itself**: an overclaiming enforcement statement induces the
operator to trust a boundary nothing holds — the same class of defect as a gate that reports PASS without
running. AC-CPD-3 is the control for that, and it is deliberately two-directional (a claim that
*understates* a shipped check is also convicted). Example remotes/paths in the doc are placeholders; no
real remote, token or host name is introduced. Threat model: a cooperating operator on a non-adversarial
corpus.

## Clarifications

- **Resolved — the file already exists.** The ER records "the plugin ships no multi-repo how-to … which
  does not exist". That survey was taken against the **installed plugin cache at v0.28.0**, whose `docs/`
  tree carries no `how-to/` directory. On `agentic-foundry` `main` at **v1.0.1** the file is present,
  linked from `docs/README.md` and `llms.txt`. This atom is therefore a **completion and a lock**, not a
  creation; AC-CPD-1 pins the path a sibling contract already references.
- **Resolved — the `hosted repo` keyword collided with a shipped heading.** The shipped doc already
  carries `## Run Claude from the control plane, never from a hosted repo`, whose heading text contains
  `hosted repo`. Pinning that keyword for AC-CPD-2(3) would have matched the **session** section (and
  broken the "exactly once" rule), so the pairing/ordering subject pins **`pairing rule`** instead. The
  session subject keeps keyword `session`, which no shipped heading currently contains — the implementer
  renames that heading so exactly one heading carries it.
- **Resolved — merge order is a precondition, not an assumption.**
  `[[feat-foundry-control-plane-preflight]]` is already authorized (`auth_seq: 1`) and lands first;
  AC-CPD-4 therefore names `tests/test_control_plane_preflight.py` directly rather than guarding the
  command with a file-existence test, which would be exactly the conditional-escape shape this repo's
  fail-closed discipline bans. **Operator confirmation needed** before authorize: if the sequencing
  changes, AC-CPD-4's sibling-regression command (and its `CPD-4-SIBLING-OK` checkpoint) must be
  dropped in the same edit.
- **Resolved — two test modules, not one.** The repo ships both `tests/test_docs_claims.py` (the GP-3
  assertion lock: install pins, roster locks, relative-link resolution) and `tests/test_doc_claims.py`
  (the anti-doc-rot claim registry with derive/check/mutate). Structural presence and coverage assertions
  belong in the former; the derived enforcement-claims oracle belongs in the latter, where the mandatory
  negative control lives. Both are in scope; nothing else is.

<!-- normative -->
## Acceptance criteria

- **AC-CPD-1** *(Invariant — ubiquitous)* **(the doc ships at the path a sibling contract names):** The
  plugin SHALL ship `docs/how-to/multi-repo-control-plane.md` — the exact repo-relative path the
  `[[feat-foundry-control-plane-preflight]]` contract's `AC-CPP-8` checkpoint names — as a **git-tracked**
  file (an untracked file does not ship in the packaged plugin), reachable by a resolving relative link
  from **both** `docs/README.md` and `llms.txt`. A named test SHALL assert tracking via the repo's own
  `git ls-files` output and assert both links resolve to that path on disk.

- **AC-CPD-2** *(Invariant — ubiquitous)* **(the doc carries the pattern, in placed sections):** The doc
  SHALL carry the six subjects below, each as its **own ATX section** whose heading text contains the
  pinned keyword (case-insensitive), and each stating its subject and carrying its pinned literal
  token(s) **within that section's own body**:
  1. keyword **`pattern`** — the control plane hosts each code repo as an **independent, gitignored
     sibling clone** at a manifest-declared path, and this is **deliberately not git submodules** because
     a submodule pins the parent to a child commit and entangles the two histories, while a control plane
     wants children evolving under their own origins. Token: `submodule`.
  2. keyword **`registry`** — `.claude/foundry-project.json` `repos{}` is the manifest; each key is the
     **dispatch key** a contract's `target_repo` names; `path` is the field the resolver reads. The
     section's example JSON SHALL be a document that **validates against the shipped
     `schema/foundry-project.schema.json`** (so it carries that schema's one required member,
     `schema_version`), and every field the example shows that **no shipped code reads** SHALL be
     marked inert **inline, inside the example itself**. The section SHALL state that the key
     `workspace` is resolved by `foundry-wt` **itself** and needs no `repos{}` row — replacing the
     shipped doc's "a fresh workspace seeds only the `workspace` self-entry", a claim about the
     separately-shipped workspace template that a plugin-only adopter's tree cannot ground. Tokens:
     `repos{}`, `path`, `foundry-wt`, `schema_version`.
  3. keyword **`pairing rule`** — the **pairing rule** (every hosted repo has **both** a root-anchored
     `.gitignore` entry **and** a `repos{}` row; either alone is a defect) and the **ordering rule**
     (gitignore, then clone, then register). The section SHALL give **both** failure directions: a
     `repos{}` row without a clone is a dangling entry indistinguishable downstream from a
     not-yet-cloned repo (the DANGLE direction), and a clone without its `.gitignore` entry first
     **spills** the hosted repo's working tree — its secrets and runtime state — into the control
     plane's own history on the next `git add -A` (the SPILL direction). Tokens: `.gitignore`,
     `repos{}`, `spill`.
  4. keyword **`session`** — start Claude Code at the **control-plane root**, never inside a hosted
     repo; a repo nested under that root is **already in session scope**, so `--add-dir` is neither
     needed for a nested child nor a remedy for a session started in the wrong place. The section
     SHALL state the consequence honestly: a control-plane-root session therefore holds read/write
     reach over **every** hosted repo's working tree — its **blast radius** — and the control plane's
     `.gitignore` *hides* those edits from control-plane `git status`; a contract's `target_repo` and
     `scope.allowed_paths` bind the **jailed worker**, not the root session. The remedy the section
     SHALL point at is a `permissions.deny` rule in `.claude/settings.json` for hosted paths the
     operator wants out of reach. Tokens: `--add-dir`, `blast radius`, `permissions.deny`.
  5. keyword **`target_repo`** — the acceptance contract's `target_repo` names a `repos{}` key, and
     each built atom writes `.foundry/build-provenance.yaml` in the code repo pinning the
     control-plane commit it was authorized against. The re-authorization consequence of that binding
     is **an enforcement claim**: it is stated in the `enforced` section under the `target_repo
     freeze` label and derived by AC-CPD-3(4), not asserted here. Tokens: `target_repo`,
     `.foundry/build-provenance.yaml`.
  6. keyword **`enforced`** — the section AC-CPD-3 governs.

  A named test SHALL parse the doc into heading-delimited sections and assert each keyword heading exists
  exactly once and each pinned token occurs **inside its own section's body**, never merely somewhere in
  the file.

- **AC-CPD-3** *(Invariant — ubiquitous)* **(the `enforced` section is a CLOSED roster whose every
  mechanism is derived from behaviour, convicting in both directions):** The `enforced` section SHALL
  present each mechanism it names as a **bolded label heading its own list item**, classified by a
  value from the closed set `machine-enforced` / `not-enforced-today` / `practice`, and SHALL name
  **no label outside** this roster:
  `repo-key resolution`, `dispatch bind-check`, `target_repo freeze` → **machine-enforced**;
  `authorization venue floors`, `doctor registry validation` → **not-enforced-today**;
  `pairing rule`, `clone-before-register ordering`, `session-root rule` → **practice**.
  `tests/test_doc_claims.py` SHALL carry that roster as a module-level label→classification mapping
  pinned against a hand-written expected copy (the two-place-diff idiom
  `DECLARED_EXTERNAL_REFS`/`EXPECTED_EXTERNAL_REFS` already ships), and a named test SHALL assert that
  the labels **parsed from the section's own body** equal the roster exactly — so a new qualitative
  overclaim cannot ship un-derived. The roster's **classifications** are current-regime values: where a
  derivation below flips one (AC-CPD-3(3)), the doc entry and the pinned mapping SHALL move **together**,
  and the claim convicts if only one moves. Each `machine-enforced` / `not-enforced-today` label SHALL be
  governed by one of the four claims below, each registered in `COVERED_CLAIMS` under **its own**
  `claim_id` with its own `derive`, `check` (raising `AssertionError` naming the claim id) and
  `make_mutated`, and each id added to the pinned `EXPECTED_CLAIM_IDS` roster. Every derivation below
  SHALL read or exercise the **live seam**, never a presence-grep over a dispatch table alone:
  1. **`repo-key resolution` + `dispatch bind-check`** — derive both the subcommands
     `scripts/foundry-wt` dispatches **and** the live invocation of `bind-check` inside
     `hooks/foundry-worktree-create.sh`'s redirect path (the hook is what actually calls it; a
     dispatch table proves only that the subcommand exists). While the hook invokes `bind-check`, the
     two labels SHALL be `machine-enforced` and the section SHALL state that an unknown `repos{}` key,
     a `path` that is not an existing directory, and a path escaping the workspace root all fail
     resolution, and that a dispatch whose manifest key disagrees with the atom's authorized
     `target_repo` fails `bind-check` **and no worktree is created**. If the hook no longer invokes it,
     or `foundry-wt` no longer dispatches it, the doc SHALL NOT name `dispatch bind-check` as enforced.
  2. **`authorization venue floors`** — derive by **executing** `scripts/foundry-authorize.py` against
     a hermetic fixture built under pytest's `tmp_path` (a fixture spec + contract whose `target_repo`
     names a manifest key that does not resolve, with `CLAUDE_PROJECT_DIR` and the working directory
     pointed at that fixture workspace so the real repo's tree and audit trail are never touched), and
     derive the **outcome triple**: the degrade/skip warnings the run emits, its exit status, and the
     `auth_seq` in the `authorized:` trailer the run leaves in the fixture contract. While that run
     emits venue-degrade warnings **and** exits zero **and** writes a trailer whose `auth_seq` is a
     positive integer, the entry SHALL state **both halves** — the venue-grounded floors *degrade to
     warnings* **and** *the freeze proceeds* — carrying the literal tokens `degrade`, `warn` and
     `auth_seq`. If instead the run exits non-zero or writes no trailer (authorization hardened), the
     "proceeds" wording SHALL be absent.
  3. **`doctor registry validation`** (and, with it, the `session-root rule` classification) — derive
     (a) whether `scripts/foundry-doctor.py` reads `repos{}`, (b) whether it runs any control-plane
     preflight probe, and (c) — by **executing** `foundry-doctor.py --session-start` against a
     deliberately broken fixture workspace — that the advisory cadence **exits zero regardless**.
     While (a) and (b) are false, the entry SHALL state that the doctor does not validate `repos{}`,
     that a dangling entry stays green, and that `--session-start` is fail-open; and `session-root
     rule` SHALL be classified `practice`. Once (a) or (b) becomes true — when
     `[[feat-foundry-control-plane-preflight]]` ships — the entry SHALL instead state that the
     **operator-invoked** doctor exits non-zero while `--session-start` still exits zero, the "does
     not validate" wording SHALL be absent, and `session-root rule` SHALL no longer read `practice`.
     The fail-open half of that compound claim SHALL be derived in **both** regimes.
  4. **`target_repo freeze`** — derive, by exercising `scripts/foundry_contract.py`'s contract-hash
     seam over a synthetic contract, that `contract_sha256` **changes when `target_repo` changes**
     (i.e. `target_repo` lies inside the hash-covered contract-proper region, above the trailer
     sentinel). While it does, the entry SHALL state that changing where code lands invalidates the
     frozen `contract_sha256` and requires **re-authorization**, and that the hook's `bind-check`
     reads that same frozen value. If `target_repo` ever falls outside the covered region, the claim
     SHALL convict.

  **The paraphrase ban is a semantic class, not one regex.** While derivation (2) holds, the
  **authorization entry's own body** SHALL match no member of a pinned regex denylist covering
  fail-closed-at-authorization phrasing — at minimum the case-insensitive `fails? closed at authoriz`
  **and** the verb class `refus|block|reject|prevent` occurring in the same sentence as `authoriz` —
  carried in `tests/test_doc_claims.py` as a reason-bearing mapping pinned against a hand-written
  expected copy (the shipped `_KNOWN_PROSE_SLASH_DENYLIST` idiom).

  **The practice labels get their own literal assertion.** A named test SHALL assert that each of the
  three rule names — `pairing rule`, `clone-before-register ordering`, `session-root rule` — literally
  co-occurs with the token `practice` inside the `enforced` section's own body.

- **AC-CPD-4** *(Requirement — event)* **(the docs suites, the sibling's doc checkpoint and the doctor
  stay green):** When `python3 -m pytest tests/test_doc_claims.py tests/test_docs_claims.py -q` runs,
  every test SHALL pass, including the inherited floors this atom's edits are most able to break —
  `test_no_unclassified_count_bearing_claim` (so the doc introduces **no** digit-shaped count token that
  is not declared in a new claim's `tokens`), `test_registry_roster_is_pinned`,
  `test_derivations_are_live`, `test_negative_control_convicts_injected_drift`,
  `test_relative_doc_links_resolve` and `test_no_journey_narration_in_shipped_docs`. When
  `python3 -m pytest tests/test_control_plane_preflight.py -q -k residual_is_declared_narrowly` runs —
  `[[feat-foundry-control-plane-preflight]]`'s own `AC-CPP-8` checkpoint over **this same doc file**,
  reachable because that atom is authorized ahead of this one and lands first (R2) — it SHALL pass, so
  this atom's rewrite of the doc cannot break the sibling's residual assertion. When
  `python3 scripts/foundry-doctor.py` runs in the plugin repo, its final line SHALL remain
  `DOCTOR-GREEN`.
<!-- /normative -->

## Design / notes

- **Extend, do not duplicate.** AC-CPD-1/-2 are structural assertions and belong in
  `tests/test_docs_claims.py` beside `test_relative_doc_links_resolve`. AC-CPD-3's four claims are drift
  oracles and belong in `tests/test_doc_claims.py`, where `derive`/`check`/`make_mutated` plus
  `test_derivations_are_live` and the parametrized `test_negative_control_convicts_injected_drift` already
  supply the anti-tautology machinery for free.
- **Why four claim ids, not one.** `test_negative_control_convicts_injected_drift` is parametrized **per
  claim id** and `test_derivations_are_live` requires only that a claim's `derive()` output *differ*
  after its mutation. A single compound claim therefore clears both tests while leaving three of its four
  sub-derivations un-exercised. One id per mechanism forces one mutation and one conviction each.
- **Suggested mutations** (one per claim, each a materialized `tmp_path` copy): (1) delete the
  `bind-check` invocation line from the copy of `hooks/foundry-worktree-create.sh`; (2) in the copy of
  `scripts/foundry-authorize.py`, make the venue-degrade branch `return 1` instead of warning, so the
  derived outcome triple flips to non-zero/no-trailer; (3) in the copy of `scripts/foundry-doctor.py`,
  add a `repos{}` read, so the derived "does not validate" fact flips; (4) in the copy of
  `scripts/foundry_contract.py`, make `contract_sha256_bytes` hash a constant, so `target_repo` no longer
  changes the hash. Each `check()` then raises against its own mutated tree.
- **Naming keeps the `-k` selectors unambiguous.** Claim ids are hyphenated
  (`control-plane-dispatch-enforcement`), test function names use the underscore form
  (`test_control_plane_dispatch_enforcement`). Each contract `-k` selector then matches exactly one
  named test and never the parametrized negative-control ids, which carry the hyphenated claim id.
- **The executed derivations must be hermetic.** Claims (2) and (3) run real scripts. They SHALL run with
  `CLAUDE_PROJECT_DIR` and `cwd` set to a `tmp_path` fixture workspace carrying its own
  `.claude/foundry-operators.json` and `FOUNDRY_OPERATOR`, so no authorization trailer, audit-ledger line
  or doctor state is ever written into the real repo. `auth_seq` is computed from the contract's **own**
  prior trailer (`prior + 1`), not a global ledger, so a fixture contract yields a deterministic value.
- **Placement, not prose quality.** Asserting a pinned token inside its own parsed section is the
  strongest thing a test can say about prose; it is not a quality oracle (R1). The section headings are
  matched by **keyword**, not verbatim, so ordinary editorial rewording does not turn the suite red — but
  the `enforced` section's **labels** are pinned verbatim, deliberately: that roster is the closure.
- **Write counts as words.** `tests/test_doc_claims.py` convicts any digit-shaped token in `docs/**/*.md`
  that no registered claim declares. Spelling counts out (or omitting them — the enumerated lists carry
  the information) keeps the doc out of that registry obligation entirely.
- **Handbook alignment is a sibling, not a section.** The handbook's `docs/control-plane.md` is the
  fuller narrative and stays the template-side artifact; `SETUP.md`'s "fails closed at authorization"
  error is a defect in a **different repo** and is fixed by a handbook atom (see Out of scope). The
  plugin doc is written so a plugin-only adopter needs nothing else.
- **Source.** ER `intake/er-onboarding-wizard-and-permission-floor.md` (the repo-nesting half; atom 9,
  `control-plane-pattern-docs`), plus this session's verification against `agentic-foundry` v1.0.1.

## Out of scope / non-goals

- **Every handbook-side edit** — aligning `agentic-handbook/docs/control-plane.md` and fixing
  `docs/SETUP.md`'s "fails closed at authorization" self-contradiction. Different repo, different
  `target_repo`, separate atom. This atom neither depends on it nor blocks it.
- **The bootstrap wizard and its attach-repo flow** (ER atoms 1 and 8) — this atom documents the pattern a
  human follows by hand today.
- **Formalizing the registry fields** (`remote` / `default_branch` / `role`) and tightening
  `schema/foundry-project.schema.json` — the ER's `repo-registry-formalization` atom, not yet specced.
  The doc documents only fields the shipped schema and shipped code actually carry (R3).
- **The workspace repo verbs** (`sync` / `status` / `foreach` / `validate`) — ER atom 7.
- **Changing authorization's degradation behaviour.** This atom describes it honestly; hardening it is a
  separate atom, and AC-CPD-3(2) is written so that atom flips the doc's claim rather than orphaning it.
- **The preflight checks themselves** — `[[feat-foundry-control-plane-preflight]]` owns those.
- **This workspace's dangling `repos.<private-infra>` entry** — an operator finding recorded in the ER, not a
  documentation change.

## Residuals ledger

- **R1 — A placement test is not a quality oracle, and a token is gameable (epistemic).** The suite
  proves each required subject has a section and its pinned tokens, not that the section is well written
  or complete. The concrete gaming vector: a pinned token such as `blast radius`, `spill` or
  `schema_version` can be satisfied by a **stray occurrence** anywhere in the right section — a
  parenthetical, a code comment in the example — without the surrounding sentence actually making the
  claim. **Bound:** the tokens must sit in the correct *parsed* section (keyword-stuffing the wrong
  section fails), AC-CPD-3's roster + denylist convict a *false* enforcement claim regardless of
  wording, and per CLAUDE.md the operator's own terminal read at delivery sign-off is the quality
  judgement — a **practice, not a control**, and this atom draws compensating coverage from it
  deliberately.
- **R2 — This atom and `[[feat-foundry-control-plane-preflight]]` write the same file, and now collide
  on its physical content.** Both name `docs/how-to/multi-repo-control-plane.md` in scope; the sibling's
  `AC-CPP-8` asserts a *residual paragraph* inside it via `-k residual_is_declared_narrowly`. This atom
  does not merely edit a different part of the file: it **restructures the section this atom's own
  `enforced` roster and the sibling's residual paragraph both live in**, so a rewrite that drops or
  reflows that paragraph turns the sibling's checkpoint RED. **Bound:** the preflight atom is authorized
  ahead of this one and merges first (never a shared branch), AC-CPD-4 runs the sibling's checkpoint as
  this atom's own regression, and AC-CPD-3(3) is two-directional and derived, so the doctor claim is
  asserted against the tree as it then is. The loser of any race gets a RED test, never a silently stale
  sentence.
- **R3 — The registry section documents fields that will grow, and "inert" is asserted by eye.**
  `remote`, `default_branch` and a repo-record `role` are not named by the shipped schema and no
  shipped code reads them; the permissive schema (`additionalProperties: true` throughout) accepts them
  silently — `path` is the only field the resolver reads. AC-CPD-2(2) therefore requires the example's
  unread fields to be marked inert **inline**, but that marking is prose: no test derives the read/unread
  partition, so a field that *becomes* read would leave the label stale. **Bound:** the ER's
  `repo-registry-formalization` atom owns that partition and will require one follow-up edit here;
  recorded, not pre-written — a doc that describes unshipped fields is exactly the overclaim AC-CPD-3
  exists to stop.
- **R4 — Two derivations still read source text; two now execute it.** The subcommand set and the hook's
  `bind-check` invocation are read textually from `scripts/foundry-wt` and
  `hooks/foundry-worktree-create.sh`; a refactor to a different shape could make either unresolvable.
  Claims (2) and (3) instead *run* the real scripts, which is stronger but buys a slower,
  environment-sensitive test (a Python/PyYAML-less runner would fail it). **Bound:** the registry's
  `MissingSourceError` idiom fails **closed** and surfaces as a RED test naming the claim, never a
  silent pass; the executed claims are hermetic under `tmp_path` (Design / notes) so a failure is the
  finding, not collateral damage to the real tree.
- **R6 — Two `practice` classifications are pinned, not derived.** `pairing rule` and
  `clone-before-register ordering` are classified `practice` by the pinned roster alone: "no gate
  asserts this" is an unfalsifiable negative that no derivation over the tree can establish. Only
  `session-root rule` is derived (AC-CPD-3(3) flips it when the preflight probe ships). **Bound:** the
  closed-roster test still stops a *new* mechanism from appearing un-derived, and a future gate that
  did assert either rule would leave the doc **understating** — the benign direction, and one the
  operator's terminal read catches.
- **R5 — The ER's premise was surveyed against a stale cache.** "The plugin ships no multi-repo how-to"
  was true of the installed v0.28.0 plugin cache and false of `main` at v1.0.1. Recorded so review does
  not chase a phantom gap: the gap is the missing pairing/ordering/registry/`--add-dir` content and the
  absent honesty section, not the missing file.

## Changelog

- v1.1 Remediation round against the consolidated 4-lens review (2026-08-02). **Blocks, all adopted:**
  (1) every AC-CPD-3 derivation is now behavioural, not a presence-grep — (1) reads the hook's live
  `bind-check` invocation, (2) **executes** the authorize seam against a hermetic `tmp_path` fixture and
  derives the outcome triple (warnings + exit status + written `auth_seq`), so "warns AND proceeds" is
  derived in both halves, and (3) additionally executes `--session-start` to derive its unconditional
  exit-zero. (2) the single compound claim is split into **four** registered claim ids — one per
  mechanism, each with its own `make_mutated` and negative-control conviction (four, not three: finding
  7 promotes the `target_repo` freeze into the `enforced` section, so it needs its own derivation).
  (3) the `enforced` section becomes a **closed roster** of pinned labels with a closed classification
  set, asserted against a hand-written expected copy; the chapeau is narrowed to match; the three
  practice labels get their own literal co-occurrence assertion and checkpoint; AC-CPD-4 adds the
  sibling's `-k residual_is_declared_narrowly` regression and R2 names the physical-content collision.
  **Risks folded in:** the `hosted repo` keyword collided with a shipped heading → repointed to `pairing
  rule` + Clarification (4); the ArgoCD citation is reframed as manifest-is-truth only, with
  clone-before-register called the manual stand-in pending the Wave-2 reconcile verb (5); AC-CPD-2(4)
  gains the mandated blast-radius clause, the gitignore-hides-edits point, the jailed-worker-not-root-
  session distinction and the `permissions.deny` remedy (6); the `target_repo` re-authorization sentence
  is restated and classified inside `enforced`, derived from `contract_sha256` coverage (7); the registry
  example must validate against the shipped schema with inert fields marked inline, the false
  "seeds only the workspace self-entry" prose is replaced by the `foundry-wt` built-in fact, and the
  pairing rationale gains the SPILL direction (8); the paraphrase ban becomes a pinned semantic-class
  denylist scoped to the authorization entry, R1 names the stray-token gaming vector, and the doctest
  citation is tightened to lint-against-source (9). New residual R6 discloses the two `practice`
  classifications that remain pinned rather than derived.
- v1.0 Draft. Ship the control-plane pattern documentation a plugin-only adopter lacks: pin
  `docs/how-to/multi-repo-control-plane.md` at the path `[[feat-foundry-control-plane-preflight]]`'s
  contract already references (AC-CPD-1), require the six placed sections covering the never-submodules
  rationale, the `repos{}` registry, the gitignore⟷manifest pairing and clone-before-register ordering,
  the session shape including `--add-dir`, and the `target_repo` ⟷ build-provenance binding (AC-CPD-2),
  and make the enforcement claims a **derived** claim in `[[feat-foundry-anti-doc-rot-ci]]`'s
  `COVERED_CLAIMS` registry so an overclaim or an understatement fails CI in either direction (AC-CPD-3).
  Realizes ER atom 9 (`control-plane-pattern-docs`) of
  `intake/er-onboarding-wizard-and-permission-floor.md`.
