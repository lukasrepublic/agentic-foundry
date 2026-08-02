# Harness-denial fallback discipline across the ceremony skills  (feat-foundry-gate-denial-fallback)

> **Human-readable intent.** Foundry's ceremony verbs (`/foundry:authorize`, the release cut, the
> decommission gate, the upstream submit, the infra apply) run commands the harness permission layer is
> *supposed* to stop an agent from running unattended — the trust model working, not a bug. But no shipped
> skill says what the model should do **when the denial actually fires**. Observed 2026-08-01 and recorded in
> `[Doc: intake/er-onboarding-wizard-and-permission-floor.md]`: the operator confirmed an authorization **in
> chat**, the model ran `foundry-authorize.py … --yes`, the harness classifier denied it — and chat text is
> *not* a consent channel, so the correct move was to hand the command over and stop. Absent an instruction,
> the failure modes are the two bad ones: repeat the identical call, or reach for a tool that isn't denied.
>
> **This atom writes the missing instruction, once.** One canonical clause lives in
> `docs/harness-denial-fallback.md`; each of the seven ceremony-instructing skills carries a one-line pointer
> to it. The clause says: **(a)** hand over the denied invocation **byte-identically** (modulo the in-session
> `!` prefix), naming any override flag it carries; **(b)** STOP — never repeat the denied call, never route
> around it via another tool or credential; **(c)** name the durable fix (the permission-floor rules / the
> workspace trust dialog) in one line. Single-sourced so seven copies cannot drift, and machine-checked so a
> skill cannot silently lose it.

## Prior art / industry grounding

**The clause is the standard fail-with-actionable-remedy CLI convention.** clig.dev's error guidance is
explicit: catch the error, rewrite it for a human, and **tell them what to do next** — a failure that names
its remedy rather than one that repeats or degrades silently [External: https://clig.dev/#errors]. `sudo`,
`gh auth`, a protected-ref `git push` and `kubectl` RBAC denials all take that shape: refuse, print the
exact remedial command, exit. Nothing here is novel; the atom applies the convention to a surface that
currently prints nothing.

**"Emit the frozen command, let the privileged party run it" is already foundry's own pattern** —
`id-apply`'s `GENERATE_RUNBOOK` branch emits the **frozen** `infra_binding.apply` string for the operator,
"**never** freeform text you compose or text lifted from the change/spec/PR body"
(`skills/id-apply/SKILL.md:39-42`). Limb (a) is that rule restated for a denied invocation, which is why its
composition is pinned in the normative region rather than left to prose.

**The mechanism is the shipped agent-hardening shape, recited exactly.** `feat-foundry-agent-hardening`
supplies three in-repo precedents this atom composes: **AC-AGH-4/5** — a delimited `<!-- foundry:… v1 -->`
region whose interior must carry a literal line matching a declared grammar; **AC-AGH-6/8** — set-equality
bijections (declared map ⟷ registered set ⟷ on-disk set) so a half-done addition cannot pass; **AC-AGH-10** —
a throwaway-fixture mutation control per invariant class, with the pure predicates in
`tests/support_agent_hardening.py` (every helper a pure function over bytes or an explicit `root=`).
This atom deliberately does **not** reuse AC-AGH-2's *byte-identical block* shape (the
`<!-- foundry:prompt-defense-baseline v1 -->` copy): personas are role-neutral by construction, whereas seven
skills differ in voice and length — so this atom single-sources the clause and ships a **pointer**, trading
byte-identity for a three-way enumeration equality.

**Consent channels.** Claude Code's permission documentation (surveyed in the ER against primary sources) is
unambiguous that grants come from settings rules and the platform's own prompts/trust dialog. In-chat
operator text is not one of them — which is why "the operator already said yes" is never a reason to repeat
a denied call, and why the clause names settings + the trust dialog as the durable fix.

## Security posture

**Prose-only atom; it changes no gate decision and grants nothing.** It edits documentation and skill
instruction text plus two new test files; `hooks/**`, `scripts/**`, `schema/**` and `.github/workflows/**`
are contract-denied, so no gate implementation, permission rule, hook, or CI check is touched. Front-
authorization, the merge floor, and the harness permission classifier are untouched and unweakened.

**The discipline REINFORCES the denial rather than working around it.** Limb (b) is the load-bearing half:
it forbids the two bypass shapes an unguided model reaches for (verbatim repeat, tool substitution) and
routes consent back to the only channels that carry it. **Limb (a) is an injection-resistant hand-off, and
that property is normative, not a prose claim** — AC-GDF-1 requires the emitted block to be *byte-identical*
to the denied invocation modulo the leading `!`, forbids composing it freeform or lifting it from a spec or
PR body, and requires any override/exception flag to be named in plain language above the block so a
`--yes`-bearing command can never be handed over silently. Limb (c) is a pointer, not an action: nothing
here instructs the model to edit its own confinement, which the ER records as itself classifier-denied by
design. No credential, token, or secret is read, emitted, or referenced.

<!-- normative -->
## Acceptance criteria

- **AC-GDF-1** *(Invariant — ubiquitous)* **(one canonical clause; three limbs; limb (a)'s composition
  pinned):** The plugin SHALL ship `docs/harness-denial-fallback.md` containing **exactly one** region
  delimited by the literal opening line `<!-- foundry:harness-denial-fallback v1 -->` and the literal closing
  line `<!-- /foundry:harness-denial-fallback -->`, and that region SHALL contain, **in this order**, three
  line-leading labelled limbs whose labels are the literals `**(a) Emit the exact ready-to-run command.**`,
  `**(b) Stop.**` and `**(c) Name the durable fix.**`. Each limb's text — the bytes from its own label up to
  the next label or the closing delimiter — SHALL state the rule given below for it **and** SHALL contain its
  required literals, all matched case-sensitively as substrings:
  - limb **(a)** — *the hand-off is transcribed, never authored*: the emitted block SHALL be **byte-identical
    to the invocation the harness denied**, modulo the leading in-session `!` — **no flag added, removed, or
    re-valued** — and SHALL never be composed freeform nor lifted from a spec or PR body; and when the denied
    invocation carries an override/exception flag (`--yes`, `--skip-audit-reason`, `--reauth-after-impl`,
    `--admin`, `-auto-approve`) the clause SHALL direct the model to **name that flag in plain language on a
    line above the block**. Required literals: the 3-byte sequence `` `!` `` (backtick, exclamation mark,
    backtick), `in-session`, `byte-identical`, `never freeform`, `spec or PR body`, `name the override flag`.
  - limb **(b)** — *stop, and what "route around" does and does not mean*: the ban SHALL target achieving the
    denied effect **via another tool or credential**, and SHALL explicitly EXCLUDE a **documented degraded
    path** the skill itself publishes — naming both shipped instances: `upstream-submit`'s
    `UPSTREAM-SUBMIT-LABEL-DEGRADED` degradation and `cut-release`'s `REFUSED`/`GATED` outcome (a verb's own
    gate refusal is the opposite polarity of a harness denial, and following its documented path is correct).
    Required literals: `never retry`, `never route around`, `another tool or credential`,
    `documented degraded path`, `UPSTREAM-SUBMIT-LABEL-DEGRADED`, `GATED`.
  - limb **(c)**: the literals `.claude/settings.json` and `trust dialog`.

  The same file SHALL carry a `## Skills that carry this clause` section whose markdown list items name the
  ceremony skills' `skills/<name>/SKILL.md` paths. That section's presence and parseability are asserted by
  **AC-GDF-2's** named test (`test_ac_gdf_2_every_ceremony_skill_carries_the_pointer`), which round-trips it
  as one of the three sets — an unparseable or missing section makes that test RED; AC-GDF-1's own test does
  not re-assert it.

- **AC-GDF-2** *(Invariant — ubiquitous)* **(every ceremony skill carries a triggered pointer, bijectively):**
  Each of the **seven** ceremony-instructing skills — `skills/authorize/SKILL.md`,
  `skills/authorize-release/SKILL.md`, `skills/cut-release/SKILL.md`, `skills/decommission-gate/SKILL.md`,
  `skills/release/SKILL.md`, `skills/upstream-submit/SKILL.md`, `skills/id-apply/SKILL.md` — SHALL contain at
  least one **pointer line**: a single line carrying all three of the literal path
  `docs/harness-denial-fallback.md`, the literal `STOP` (both case-sensitive), and a **trigger literal**,
  matched case-insensitively, from the set { `harness denial`, `permission denial` }. The trigger literal
  binds the pointer to the harness-denial event specifically, so it cannot be read as covering a
  skill-documented gate refusal (`REFUSED`/`GATED`, `UPSTREAM-SUBMIT-LABEL-DEGRADED`). The three sets SHALL be
  **equal**: the seven paths enumerated above, the paths listed in the clause file's `## Skills that carry
  this clause` section, and the set of `skills/*/SKILL.md` files on disk that contain a pointer line. A skill
  listed but not pointing, or pointing but not listed, SHALL fail.

- **AC-GDF-3** *(Requirement — unwanted)* **(no un-negated retry instruction survives the checked text):**
  The **checked text** is the AC-GDF-1 delimited region plus every AC-GDF-2 pointer line. It SHALL be
  segmented into **paragraphs** (maximal runs of consecutive non-blank lines) and each paragraph into
  **sentences** (the paragraph's lines joined with single spaces, then split after each `.`, `!` or `?` that
  is followed by whitespace or ends the paragraph). If any case-insensitive, word-boundary-anchored
  occurrence of a **retry token** — `retry`, `retries`, `retried`, `retrying`, `re-run`, `re-runs`, `re-ran`,
  `re-running`, `rerun`, `reran`, `rerunning`, `run it again`, `try again`, `attempt again`, `route around`,
  `work around`, `workaround`, `circumvent`, `sidestep`, `bypass`, `bypasses`, `bypassed`, `bypassing` —
  appears in the checked text and is **not exempt**, then the check SHALL fail, naming the file, the line and
  the token. An occurrence is **exempt** iff **either**:
  - **(i) sentence-scoped negation** — a **negator** from the set { `never`, `do not`, `must not` }, matched
    case-insensitively, begins at a lower character offset within the **same sentence** (so
    "Never retry, re-run, or route around a denied call." exempts all three tokens); **or**
  - **(ii) the bounded resumption exemption** — the occurrence lies inside the **resumption block**: the
    paragraph whose first line's first non-whitespace bytes are the literal
    `**Resuming after a real grant.**` (so the accurate rule — re-running is correct once the state changed
    through a real consent channel — is expressible without defeating the check elsewhere).

  The guarantee this criterion delivers is **scoped to the enumerated token set and these two exemptions**;
  the broader intent ("no wording anywhere instructs retrying a denied command") remains review-borne
  (R4/R5).

- **AC-GDF-4** *(Requirement — event)* **(pytest is a real oracle, with a per-class negative control):** When
  `python3 -m pytest tests/test_gate_denial_fallback.py -q` runs, it SHALL assert AC-GDF-1..3 against the
  **real repository tree** in module-level functions named `test_ac_gdf_<n>_<slug>` (the node id is
  load-bearing — the acceptance contract selects on it), with the segmentation and predicate logic in
  `tests/support_gate_denial_fallback.py` as pure functions over bytes or over an explicit `root=` directory
  (never the real tree implicitly — the shipped `tests/support_agent_hardening.py` convention), **and** SHALL
  run a parametrized negative control `test_ac_gdf_4_mutated_fixture_is_red` over a throwaway temp fixture
  copied from the real clause file plus the real seven skills and mutated in exactly one of **five** ways per
  case — `pointer-removed` (drop the pointer line from one skill), `limb-dropped` (delete limb **(b)** from
  the region), `limb-a-literal-dropped` (delete one required limb-**(a)** literal), `retry-instruction`
  (insert a sentence carrying a retry token with no negator earlier in that sentence, outside the resumption
  block), `enumeration-desynced` (remove one skill from the clause's list) — each of which SHALL make the
  corresponding AC-GDF-1/2/3 check **fail**, naming the offending file. This establishes that the suite
  convicts each **check class** (pointer bijection, limb order, limb literals, the retry rule, enumeration
  sync) and is therefore **not unconditionally green**; it does not establish that every individual required
  literal is independently load-bearing (R5).

- **AC-GDF-5** *(Invariant — ubiquitous)* **(the tree stays healthy):** `/foundry:doctor` over the changed
  tree SHALL report `DOCTOR-GREEN` on its default verdict.
<!-- /normative -->

## Design / notes

- **Pointer, not a copied paragraph — but the pointer carries `STOP` and its trigger.** Copy-paste divergence
  across seven files is this shape's failure mode, so the clause is single-sourced and enumeration-checked
  rather than duplicated byte-identically into skills whose voices and lengths differ (the deliberate
  departure from AC-AGH-2). A bare path would degrade badly if the model never opens the file mid-ceremony,
  so one inline word carries the load-bearing half at the point of use — and the trigger literal keeps that
  word bound to a *harness* denial rather than to a verb's own refusal.
- **Why negation-anchoring, not a banned-phrase list — and why the anchor is sentence-scoped.** The clause
  must *say* "never retry" and "never route around", so a plain banned-substring scan would convict its own
  correct wording. An *immediately-preceding-`never `* anchor was worse: it convicts correct English
  ("Never retry, re-run, or route around…" fails on the 2nd and 3rd tokens). Sentence-scoped negation plus
  one labelled resumption block is the same assertion made decidable while leaving accurate prose writable.
- **Scope of the seven.** The ceremony/trust-action tier the ER's classifier sweep classes as **ask**
  (operator-signature-bearing): authorization (single + batch), the release surface and its cut, the
  decommission gate, the upstream submit, the infra apply gate. Advisory/read-only verbs are excluded — they
  are never denied, so a clause there is noise.
- **The eighth candidate, deliberately excluded: `mode-autonomous`'s bare `gh pr merge`.** It is a real
  denial surface, and it is named here rather than silently omitted. It is out of scope for two reasons:
  (i) the refusal comes from `hooks/foundry-git-discipline.sh`, whose `block()` already self-explains at the
  point of refusal — the clause would duplicate a message the operator already gets; and (ii) the hook's two
  refusal modes have **mixed semantics** — `--admin` is an outright block (clause-shaped), but a
  checks-pending block is legitimately resolved by waiting for CI and invoking the merge again once green,
  which limb (b)'s verbatim "never retry" would wrongly forbid. A surface with mixed polarity is unfit for a
  clause whose value is its bluntness.
- **Source.** `[Doc: intake/er-onboarding-wizard-and-permission-floor.md]` — its *Continuous* bullet ("every
  gate skill carries the fallback discipline — on a harness denial, emit the exact copy-paste/`!` block and
  stop; never retry, never route around") and atom 5 of its decomposition.

## Out of scope / non-goals

- **Writing, shipping, or changing any permission rule** — the allow/ask/deny map, `.claude/settings.json`,
  the trust manifest. Those are the ER's sibling atoms (`permission-floor-map`, `onboarding-bootstrap-cli`,
  `doctor-permission-floor-check`); this atom only *points at* the durable fix.
- **Detecting denials** — harness-reported; no detector, hook, or parser is added and no exit code changes.
- **Weakening, retrying, or auto-recovering from a denial** — deliberately impossible under limb (b).
- **Gate refusals a verb documents itself** (`REFUSED`/`GATED`, `UPSTREAM-SUBMIT-LABEL-DEGRADED`) — opposite
  polarity; limb (b) explicitly excludes them and this atom changes neither.
- **Non-ceremony skills, agents, commands, hooks, and gate/CI machinery** — the clause is scoped to the
  enumerated seven; `hooks/**`, `scripts/**`, `schema/**`, `.github/workflows/**` are contract-denied.

## Residuals ledger

- **R1 — Instruction prose, not a mechanical control (design), in two modes.** *(a) The repeat-call mode is
  benign:* the *control* is the harness classifier, which this atom never touches, so a model that ignores
  the clause and repeats the denied call gets denied again — the worst outcome is the pre-atom status quo (a
  stalled ceremony, a futile repeat), never a bypass. *(b) The route-around mode is NOT equally benign* and
  is the residual that matters: substituting a non-denied tool can in principle reach the same effect (e.g.
  hand-writing an `authorized:` block instead of running the denied CLI, or reaching for `gh` when a `git`
  invocation is denied). **Bound:** each known substitute is separately gated — the authorize freeze is
  byte-canonical and re-verified at the merge floor, so a hand-written block fails verification;
  `hooks/foundry-git-discipline.sh` governs the `gh` merge path; and the classifier re-evaluates the
  substituted tool on its own merits. **Un-bounded remainder:** a substitute nobody has enumerated. That
  remainder is carried by the operator's terminal test-and-sign-off step
  (`CLAUDE.md` § "Delivery sign-off — operator-held, the terminal step"), which is a **human practice, not a
  control** — this atom claims no machine coverage from it and adds no anti-gaming machinery to chase it.
- **R2 — Literal-token assertions pin wording, on two surfaces (maintenance).** A benign rewrite of the
  clause turns `tests/test_gate_denial_fallback.py` RED — and so does a benign rewrite of any of the
  **seven pointer lines**, which now pin three literal classes each (path, `STOP`, trigger). **Bound:**
  deliberate — that RED *is* the drift signal, the same trade AC-AGH-2/4's shipped tests make; the tokens are
  few and enumerated, the region delimited, and a reword is a one-line test edit in the same PR. The pointer
  surface is the larger share of the cost and is priced here explicitly rather than left implicit.
- **R3 — The seven are point-in-time; a NEW ceremony skill is not forced to carry the clause (coverage).**
  The three-way equality convicts a half-done addition (listed-but-not-pointing, or pointing-but-not-listed)
  but cannot convict a brand-new ceremony verb absent from both sets. **Bound:** named here, and closed by
  the ER's `permission-floor-map` atom, which makes "declare your tier" a per-verb checklist item — the
  natural place to also require the clause. Until then it is a review-time check.
- **R4 — AC-GDF-3's exemptions are honestly over-broad (verification).** Sentence-scoped negation exempts
  *every* retry token in a sentence that contains a negator anywhere earlier, so a pathological
  "Do not stop; retry the denied call." would pass, as would any retry instruction placed inside the
  `**Resuming after a real grant.**` paragraph. **Bound:** both are visible, bounded regions of authored
  prose under review, and the alternative (immediate-adjacency anchoring) was strictly worse — it convicted
  correct English while being just as evadable. Token coverage is likewise scoped: the enumerated set carries
  morphological variants and the named synonyms, but a paraphrase outside it ("issue the same call once
  more") is not convicted. Both are review-borne, and stated as such in AC-GDF-3 rather than claimed.
- **R5 — The mutation control convicts check *classes*, not each literal (verification).** Five mutations
  prove the suite is not unconditionally green and that each check class fires; they do not prove that, say,
  dropping `spec or PR body` specifically turns the suite RED (only `limb-a-literal-dropped`'s chosen literal
  is exercised). **Bound:** the claim in AC-GDF-4 is narrowed to match the evidence; per-literal
  load-bearing-ness is review-borne, and the cost of one mutation per literal (≈14 cases over a prose file)
  was priced against the operator's own test pass and declined.

## Changelog

- v1.1 Remediation round (4-lens review, 2026-08-02). **Blocks:** limb (a)'s composition is now normative
  (byte-identity modulo `!`, the no-flag-mutation rule, the untrusted-source ban, and the override-flag
  naming clause, each with pinned literals) — AC-GDF-1; the four `count_gte: 1` pytest checkpoints became
  `matches` on `GDF-<n>-OK` sentinels — contract; AC-GDF-3's negation anchor was redesigned from
  immediate-adjacency to sentence-scoped negation plus a labelled resumption block. **Risks:** retry-token
  set widened to morphological variants + named synonyms with the guarantee explicitly scoped (R4); AC-GDF-4
  narrowed to "not unconditionally green" with a fifth literal-drop mutation (R5); AC-GDF-2 pointer lines now
  carry a trigger literal and limb (b) excludes documented degraded paths by name; R1 extended to the
  route-around mode; `mode-autonomous`'s `gh pr merge` excluded with reasons; prior art re-cited to
  AC-AGH-4/5/6/8/10 (not the byte-identity block) and R2 priced for the pointer surface; `DOCTOR-GREEN` split
  out as AC-GDF-5.
- v1.0 Draft. Codify the harness-denial fallback discipline as one canonical clause
  (`docs/harness-denial-fallback.md`, delimited + limbed) referenced by the seven ceremony-instructing
  skills, with a negation-anchored no-retry rule and a mutation-based pytest oracle. Realizes atom 5 of
  `[Doc: intake/er-onboarding-wizard-and-permission-floor.md]`.
