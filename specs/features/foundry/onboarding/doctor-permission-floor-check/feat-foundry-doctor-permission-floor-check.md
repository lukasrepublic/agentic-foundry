# The doctor watches the permission floor for drift, advisory-only (feat-foundry-doctor-permission-floor-check)

> **Human-readable intent.** `[[feat-foundry-permission-floor-map]]` (authorized) ships
> `docs/permission-floor.json` — the plugin's canonical three-tier allow/ask/deny map — as inert data, and
> assigns the watching to this atom. Its **R6** is the sharp edge: the harness ask-prompt offers a persist
> option, and accepting it writes an `allow` rule into **`.claude/settings.local.json`** with no second trust
> dialog, silently converting a declared ceremony `ask` (`foundry-authorize.py` included) into a standing
> local grant that never appears in the reviewed `settings.json` diff. R6's named follow-on is quoted
> verbatim: the drift check *"SHALL read `.claude/settings.local.json` in addition to `.claude/settings.json`,
> and report any local `allow` that shadows a declared `ask` as drift — otherwise the drift check watches the
> one file the decay does not touch."* This workspace already exhibits it, verified 2026-08-02 in
> `.claude/settings.local.json`: `permissions.allow` carries
> `Bash(python3 /home/…/agentic-foundry/foundry/*/scripts/*)` while `permissions.ask` carries
> `Bash(python3 /home/…/agentic-foundry/foundry/*/scripts/foundry-authorize.py *)` — the shadow, live, in the
> flagship control plane.
>
> **This atom adds the doctor's seventh probe** (`permission-floor` — the shipped tree registers **six**:
> `manifest`, `hooks`, `skills-frontmatter`, `stack-profile-lock`, `operator-registry`, `control-plane`). It
> compares the workspace's **effective** permission configuration — both settings files, unioned,
> origin-tracked — against the shipped map and reports, in a fixed severity rank: a **blanket** local grant
> that swallows the whole map, a **ceremony ask** shadowed by a broader **allow**, any other shadowed **ask**,
> a missing **deny**, a settings file that exists but will not parse, a plugin-path glob that no longer
> resolves, and (informationally) an absent **allow** plus an `unclassified` bucket. It is **report-only and
> never auto-fixed**: an agent writing `.claude/settings*.json` is hard-denied by the classifier — which is
> precisely why this is a doctor probe that hands the operator a diff, not a reconciler. A **mismatch is
> ADVISORY, not RED**, because local divergence can be a deliberate operator choice; only a **malformed floor
> file** reddens the operator-invoked run.

## Prior art / industry grounding

- **Read-only status verb + surfaced-never-reconciled drift is the consensus shape.** `terraform plan`
  reports declared-vs-actual divergence and never applies it; ArgoCD marks an app `OutOfSync` and leaves the
  sync to a separate, explicitly-invoked action; `kubectl diff` prints and exits; `gh auth status` reports
  which config a tool actually resolved and stops. The ER adopts the same posture for the repo registry
  (*"drift is surfaced, never auto-reconciled — matching the id-drift posture"*), and this atom is that
  posture applied to the permission floor. No novel machinery.
- **A flat exit code on a difference is NOT the industry universal, and this spec does not claim it is.**
  `kubectl diff` exits **1** when differences are found, and `gh auth status` exits **non-zero** when a host
  is not authenticated — both signal through the exit code. `terraform plan` exits 0 by default and signals a
  diff only under the opt-in `-detailed-exitcode`. The grounding for *this* probe's never-redden choice is
  therefore narrower and named honestly: **ArgoCD's status-field model** (`OutOfSync` is a reported condition
  on the object, not a process failure) **plus the doctor's own shipped convention** —
  `[[feat-foundry-control-plane-preflight]]` AC-CPP-7 (`--session-start` prints a `WARNING:` carrying the
  actionable payload and still exits 0) and `skills/doctor/SKILL.md`'s *"a mistake-catcher for the operator,
  not a merge gate"*. It is a deliberate local choice inside a mixed field, not an inherited consensus.
- **The render floor for untrusted strings is the plugin's own shipped convention.** `[[feat-foundry-fleet-roster]]`
  **AC-ROST-5** and `[[feat-foundry-fleet-session-registry]]` **AC-SREG-5** fix it: every externally-sourced
  rendered string is **secret-scrubbed THEN control/ANSI/newline-neutralized across C0 (`0x00–0x1F`), C1
  (`0x80–0x9F`) and `0x7F` plus ANSI/CSI/OSC introducers** (shipped as `_CTRL_RE` in
  `scripts/foundry-fleet-roster.py` and `scripts/foundry-fleet-session-machinery.py`). Settings-file rule
  bodies are exactly that class of string, and AC-DPF-2/-8 reuse the shipped floor rather than inventing one.
- **The subsumption rule is not re-invented — and the reuse is now *proved*, not asserted.** The map's
  **AC-PFM-6** defines prefix subsumption modulo a trailing `:*`; its implementation is the private
  `_subsumes()` in `tests/test_permission_floor_map.py`. Because that lives in a test module, no import
  relationship can make the two matchers agree "by construction", so AC-DPF-5 makes agreement a **cross-atom
  conformance obligation**: one shared fixture table, both implementations, identical verdicts asserted.
  Drift is caught by regression, not claimed by prose.

## Security posture

**Read-only over the settings files, proved at the outcome level.** The probe consumes **only**
`permissions.{allow, ask, deny}` from `.claude/settings.json` and `.claude/settings.local.json`. Every other
key — `env`, `apiKeyHelper`, `enabledPlugins`, hooks, MCP blocks — is neither parsed into the model nor
retained nor printed: an adopter's `env` block is a plausible home for credential material, and the probe's
report must never become its exfiltration path. Read-only is asserted as an **outcome** (a fixture tree
byte-identical after the run, an audit-hook witness, no `subprocess`/network import), not as an enumeration of
forbidden call shapes an implementation could route around (AC-DPF-2).

**Non-disclosure is VALUE-scoped, which is the sharp version.** A settings file's rule bodies are attacker- or
accident-controlled text. The narrow rule is: *no settings-derived string reaches output except a canonicalized
rule that actually covered a map entry, plus the file path and tier key that locate it.* An **`unclassified`**
rule — one the matcher could not reduce — is therefore reported by **tool-name prefix, origin file, tier key
and count only, never by its body**, because an unreducible body is precisely the one nobody vetted. Every
emitted line is sanitized to the AC-ROST-5 floor (extended to zero-width/bidi) and length-capped.

**It grants nothing and gates nothing.** No hook, schema, workflow, or authorization path is touched
(`hooks/**`, `schema/**`, `.github/workflows/**`, `scripts/foundry-authorize.py`, `scripts/foundry_authz.py`,
`skills/**` are contract-denied, as are `scripts/foundry-release-acceptance.py` — whose `[XX` scrape is a
*constraint on* this atom, not a target — and `tests/test_permission_floor_map.py`, which AC-DPF-5 imports for
conformance and never edits): **no wiring re-pin, no meta-regress, no new exec seam.** R6 records that this
atom's own two new surfaces do not route to the floor-#3 security-path CI check, and names the tracked
follow-on that fixes it.

<!-- normative -->
## Acceptance criteria

- **AC-DPF-1** *(Invariant — ubiquitous)* **(the probe is registered, and `advisory` is a representationally
  distinct, non-failing outcome):** `scripts/foundry-doctor.py` SHALL register one additional probe whose
  registration matches the literal form `_run("permission-floor",` (the shape
  `tests/test_doc_claims.py::_derive_doctor_probe_ids` parses). Its result-rendering SHALL support a third
  non-failing outcome, **`advisory`**, carried by a **sentinel value that is not `True`, not `False` and not
  `None`** — the shipped renderer reads `ok is None` as `skip`, so reusing `None` would silently collapse an
  advisory into a skip — and dispatched on by an explicit branch that precedes the truthiness branch. The
  advisory mark SHALL be distinct from the marks rendered for `ok`, `skip` and failure, and SHALL NOT contain
  the substring `XX` (`scripts/foundry-release-acceptance.py::_check_doctor` scrapes `[XX` to collect
  failures). An advisory result SHALL NOT set the run's hard-fail flag and SHALL NOT cause `DOCTOR-RED`. Every
  pre-existing probe's registration literal, returned outcome value and rendered mark SHALL be unchanged, and
  no pre-existing probe SHALL return the advisory sentinel. *(Checkpoints:
  `-k probe_is_registered_and_advisory_is_a_distinct_outcome`, `-k existing_probes_are_unchanged`,
  `-k advisory_never_collapses_into_skip_or_ok`.)*

- **AC-DPF-2** *(Invariant — ubiquitous)* **(both settings files, read-only at the outcome level, with
  VALUE-scoped non-disclosure):** The comparison module `scripts/foundry_permission_floor.py` SHALL build the
  **effective** configuration as the union of `permissions.allow`, `permissions.ask` and `permissions.deny`
  read from `<project_dir>/.claude/settings.json` **and** `<project_dir>/.claude/settings.local.json`,
  retaining for every rule its **origin** (the file path and the tier key it came from). Four obligations
  bind:
  **(a) Bounded, exception-tolerant reads.** Before reading, each path SHALL be `stat`-ed and read only if it
  is a **regular file** (so a FIFO, device or symlink-to-non-regular is refused, not opened), and only if its
  size is at most **1 MiB**. **ANY** exception raised while resolving, `stat`-ing, opening, reading, decoding,
  JSON-parsing or shape-validating a settings file — without enumerating exception types — SHALL be caught and
  recorded as a `settings-unreadable` finding for that file, never propagated.
  **(b) Shape validation.** A file whose `permissions` value is present but not an object, or whose
  `permissions.allow`/`.ask`/`.deny` value is present but is not an array of strings (a string, dict, `null`,
  or an array containing a non-string element), SHALL yield `settings-unreadable` for that file. A **missing**
  `permissions` key, or a missing tier key, is an empty tier and is not a failure.
  **(c) VALUE-scoped non-disclosure.** No settings-derived string SHALL appear on stdout or stderr except:
  (i) a **canonicalized** rule that covers a map entry, on that entry's finding line; (ii) a settings file's
  own path; (iii) a tier key from the closed set `allow`|`ask`|`deny`. Every other settings-derived value —
  any unclassified rule's body, any value under any non-`permissions` key, any rule that covers nothing — SHALL
  be absent from output, and no non-`permissions` key or value SHALL be retained in the returned model.
  **(d) Read-only, asserted as an outcome.** For any probe run over a fixture tree: every file in that tree
  SHALL be byte-identical afterwards (per-file SHA-256, the two settings files explicitly among them), no path
  SHALL be added or removed, a `sys.addaudithook` witness installed for the duration of the call SHALL record
  **zero** write-class audit events (`open` with a write/append/update flag, `os.rename`, `os.remove`,
  `os.mkdir`, `shutil.*`) whose path argument resolves under that tree, and the module's import closure SHALL
  contain no `subprocess`, `socket`, `http`, `urllib` or `requests` import.
  *(Checkpoints: `-k effective_config_unions_both_settings_files`, `-k only_the_permissions_keys_are_consumed`,
  `-k no_settings_derived_string_escapes`, `-k the_module_never_writes`,
  `-k absent_or_unreadable_settings_are_tolerated`.)*

- **AC-DPF-3** *(Invariant — ubiquitous)* **(canonicalization and the `covers` relation — the matcher's
  mechanics):** Comparison SHALL canonicalize each rule before matching. A rule participates only if it
  matches `^Bash\((.+)\)$`. Its **body** is folded thus: a leading interpreter token from the closed set
  `python3` | `python` | `bash` | `sh` is dropped when a further token follows; a leading `~/` or `$HOME/` is
  expanded; a plugin-cache path segment sequence `plugins/cache/<seg>/foundry/<seg>/` (each `<seg>` any single
  non-`/` segment, `*` included) is folded to one sentinel, so the map's `plugin_root_glob` spelling and a
  workspace's concrete or globbed spelling reduce to the same string. A folded body is a **prefix rule** — with
  *reach prefix* = the body without its marker — when it ends in `:*`, or ends in a `*` preceded by whitespace
  or `/`, **or is exactly `*`** (reach prefix the empty string); otherwise it is **exact** and its reach
  prefix is the whole body. An effective rule **E** **covers** a map entry **M** iff E is a prefix rule and
  M's reach prefix begins with E's reach prefix, or E is exact and their reach prefixes are equal — the
  AC-PFM-6 rule, reused (and proved equal to it by AC-DPF-5, not asserted). Two directional rules bind:
  **(a) Blanket detection.** A prefix rule whose reach prefix is empty, or reduces to empty after the
  interpreter drop, is a **blanket** rule: it covers every map entry. `Bash(*)`, `Bash(python3 *)` and
  `Bash(python3:*)` are each blanket.
  **(b) The deny direction refuses the fold.** For the `deny` comparison only, matching SHALL use the
  **identity** canonicalization — surrounding-whitespace normalization and the prefix-marker split, with **no**
  interpreter drop and **no** plugin-cache fold — and an effective `deny` rule covers a map `deny` entry only
  if their reach prefixes are **equal**. Prefix-broadening does not count as deny coverage. For `deny`, a false
  "covered" is silence on a missing protection, so the fold's fail-open direction is refused there; for
  `ask`/`allow` a fold error costs at most a noisy or missing line (R1).
  *(Checkpoints: `-k canonicalization_folds_the_live_spellings`, `-k blanket_grants_are_detected`,
  `-k deny_coverage_requires_exact_reach_equality`.)*

- **AC-DPF-8** *(Invariant — ubiquitous)* **(the report — classes, rank, origins, redaction, and the summary
  line):** *(New in v1.1; numbered 8 because AC-IDs are never renumbered. Logically it follows AC-DPF-3.)*
  The probe SHALL report exactly these finding classes, and SHALL order both its finding lines and its
  per-class counts in this fixed **severity rank**:
  1. **`blanket-allow`** — an effective `allow` rule that is blanket (AC-DPF-3(a)). Reported **once per
     blanket rule**, naming every map `ask` and `deny` entry within its reach; those entries SHALL NOT also
     produce individual `ask-shadowed` lines.
  2. **`ask-shadowed-ceremony`** — a **ceremony** map entry covered by an effective `allow` rule. The
     ceremony set SHALL be derived **structurally, with no schema change and no second source of truth**: a
     map entry whose `tier` is `ask` and whose rule body names a `scripts/` basename.
  3. **`ask-shadowed`** — any other map `ask` entry covered by an effective `allow` rule.
  4. **`deny-missing`** — a map `deny` entry covered by no effective `deny` rule under AC-DPF-3(b).
  5. **`settings-unreadable`** — a settings file that **exists** but failed AC-DPF-2(a)/(b). When any such
     finding exists the outcome SHALL NOT be `ok`, whatever else the comparison found. A genuinely **absent**
     settings file is not this class; it follows the absent-configuration path of AC-DPF-4.
  6. **`stale-plugin-path`** — the map's `plugin_root_glob` expands to no directory on disk, or expands but no
     expansion contains the `scripts/<basename>` a map rule names. The expands-to-nothing case is reported
     once, naming the glob, not once per entry.
  7. **`allow-absent`** *(informational)* — a map `allow` entry covered by no effective `allow` rule.
  8. **`unclassified`** *(informational)* — an effective rule that failed canonicalization (a non-`Bash(`
     tool rule, or an unparseable body). It SHALL be reported by **tool-name prefix** (the text before the
     first `(`, rendered as `?` where it is not `[A-Za-z0-9_-]{1,32}`), **origin file**, **tier key** and
     **count** — its **body SHALL NOT be emitted**. Nothing is silently dropped: every non-participating rule
     is counted here.
  Each finding line SHALL name its class, the map rule, that rule's covering/absent effective rule where one
  exists, that rule's origin file and tier key, and a one-line remedy. Where **more than one** effective rule
  covers a map entry, the entry SHALL be reported **once**, citing **every distinct** covering `(rule, file,
  tier key)` origin — so a rule present in both settings files lists both. The report SHALL open with a
  **summary line** carrying the per-class counts in rank order; whenever one or more ceremony entries are
  shadowed — whether by a blanket rule or a narrower one — the summary line SHALL **lead** with that fact and
  carry the remedy literal `the front-authorization prompt is not firing`. Every emitted line SHALL be
  neutralized to the AC-ROST-5 render floor (C0 `0x00–0x1F`, C1 `0x80–0x9F`, `0x7F`, ANSI/CSI/OSC introducers)
  **extended to zero-width and bidirectional-override code points** (`U+200B`–`U+200F`, `U+202A`–`U+202E`,
  `U+2066`–`U+2069`, `U+FEFF`), and SHALL be length-capped at 200 characters; at most 50 finding lines per
  class SHALL be emitted, with any remainder collapsed into a truncation count.
  *(Checkpoints: `-k the_finding_classes_are_reported_with_every_covering_origin`,
  `-k the_unclassified_bucket_redacts_rule_bodies`, `-k the_summary_line_ranks_ceremony_shadowing_first`.)*

- **AC-DPF-4** *(Requirement — event)* **(the honest tier: a mismatch is advisory; only a malformed floor file
  reddens; session-start stays fail-open):** When the probe runs, its outcome SHALL be exactly: **`advisory`**
  when the floor file parses and one or more findings exist (including the case where **neither** settings file
  exists, reported as a single finding naming the absent configuration with the pre-session bootstrap CLI as
  the remedy); **`ok`** when the floor file parses and no finding exists; **`skip`** when
  `docs/permission-floor.json` is absent from the plugin tree (it is shipped by the map atom and denied to this
  one, so its absence is not-applicable); and **RED** — the only outcome that sets the run's hard-fail flag and
  makes the operator-invoked `scripts/foundry-doctor.py` exit non-zero — **only** on a **malformed floor**,
  defined as a schema-validation failure of `docs/permission-floor.json`: unparseable JSON, size above 1 MiB,
  missing `plugin_root_glob` or `entries`, an entry lacking `rule`/`tier`, a `tier` outside `allow`|`ask`|`deny`,
  or a `plugin_root_glob` failing the validation below.
  **`plugin_root_glob` SHALL be validated before it is expanded**: it SHALL begin with the literal
  `~/.claude/plugins/cache/` (the value AC-PFM-1 pins), SHALL contain no `..` path segment, SHALL contain no
  `**`, and SHALL contain at most two `*` characters; its expansion SHALL be enumerated non-recursively and
  SHALL be capped at **256** matches. A violation of any of these is a malformed floor (RED), not a
  `stale-plugin-path` finding.
  Under `--session-start` the process SHALL exit 0 in **every** case above, including the malformed-floor case.
  The session-start rendering SHALL be **extended, never weakened**: today the `WARNING:` banner prints only
  when the run hard-fails, so a run whose only signal is advisory would print nothing; the banner SHALL
  therefore also print when the run carries one or more advisory findings, with the hard-fail behaviour
  unchanged. Under `--session-start` the payload SHALL be filtered to the **actionable** classes (rank 1–6)
  plus exactly **one** count line covering the informational classes (`allow-absent`, `unclassified`); the full
  payload is operator-invoked only.
  *(Checkpoints: `-k outcome_tiering_is_advisory_except_a_malformed_floor`, `-k session_start_stays_fail_open`,
  `-k session_start_prints_only_the_actionable_lines`, `-k plugin_root_glob_is_validated_before_expansion`,
  plus a `DOCTOR-GREEN` regression on this repo.)*

- **AC-DPF-5** *(Requirement — event)* **(the test module is the live seam: a negative control per class, a
  fail-open control per failure mode, and cross-atom matcher conformance):** When `python3 -m pytest
  tests/test_permission_floor_check.py -q` runs in the plugin repo, the module SHALL assert AC-DPF-1 through
  AC-DPF-4, AC-DPF-7 and AC-DPF-8 in separately named tests; SHALL drive every comparison as a pure function of
  an explicit `(plugin_root, project_dir)` or `(parsed_map, effective_rules)` pair over throwaway fixture
  trees, never the real tree implicitly; and SHALL include the three control sets below.
  **(a) Class negative controls** — materialized fixtures asserting the named class is reported (or the check
  FAILS): (a) a `settings.local.json` whose `allow` shadows a map ceremony `ask` — spelled as the live
  workspace spells it, with a `python3` interpreter token, an absolute home path, a globbed version segment and
  a trailing bare `*`; (b) a missing map `deny`; (c) an absent map `allow`; (d) a `plugin_root_glob` expanding
  to no directory; (e) a malformed floor file (RED); (f) a clean configuration (no findings, outcome `ok`);
  **(g) `Bash(*)`, (h) `Bash(python3 *)`, (i) `Bash(python3:*)`** — each reported `blanket-allow` and each
  naming the ceremony entries it swallows; (j) a rule body carrying a credential-shaped literal **and** a
  second rule body carrying an ANSI/CSI escape, both landing in `unclassified`, asserting neither body nor any
  escape byte reaches stdout or stderr; (k) the same map entry covered by a rule in **both** settings files,
  asserting one finding line citing both origins. Without (a) the shadow check is vacuous against the very
  spelling it exists to catch; without (j) the redaction rule is unproved.
  **(b) Fail-open controls** — for each of: a JSON document nested past the interpreter's recursion limit;
  `permissions.allow` as a string, as a dict, and as `null`; a tier array containing an integer element; a
  settings path raising `PermissionError`; a settings path that is a FIFO and one that is a symlink to a
  non-regular file; a settings file whose bytes are not valid UTF-8; **and the malformed-floor case, which is a
  MANDATORY input to `session_start_stays_fail_open`** — the test SHALL invoke the doctor with
  `--session-start` and assert **exit code 0** and completion within **5 seconds** of wall clock.
  **(c) Cross-atom matcher conformance.** The module SHALL import `_subsumes` from the sibling suite
  `tests/test_permission_floor_map.py` and, for **every** row of the shared table below, assert that
  `_subsumes(A, B)` **and** this module's `covers(A, B)` both equal the expected verdict. If the sibling module
  cannot be imported the test SHALL **fail**, never skip.

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

  Every row's `A` is a `:*`-terminated prefix rule and every `B` is map-expressible (exact or `:*`-terminated)
  — the sub-relation both implementations must share. Rows outside that vocabulary (an **exact** `A`, or the
  bare-`*` / ` *` / `/*` / interpreter / `~`-expanded spellings this atom adds in AC-DPF-3) are **this atom's
  declared extension** and SHALL be asserted against this module only, in a separately named test.
  *(Checkpoints: `-k negative_controls_all_fire`, `-k fail_open_negative_controls_all_exit_zero`,
  `-k covers_agrees_with_the_map_suite_on_the_shared_table`, plus the whole-module green run.)*

- **AC-DPF-6** *(Invariant — ubiquitous)* **(the derived documentation claims stay true):**
  `docs/QUICKSTART.md`'s `DOCTOR-GREEN (<N> probes: <labels>)` claim SHALL satisfy
  `tests/test_doc_claims.py::test_doctor_probe_claims` with this atom applied: `<N>` equal to the number of
  probe registrations derived from the shipped `scripts/foundry-doctor.py` at test time — **read from the tree
  at implementation time, never assumed** — and `<labels>` a bijection onto the registered probe ids, including
  exactly one label matching `permission-floor` and no other id. `docs/troubleshooting.md` SHALL carry a
  section for this probe stating that a mismatch is **advisory** (never RED, never auto-fixed), naming
  `.claude/settings.local.json` as the file the ask→allow decay writes to, and giving the remedy; required
  literals, case-sensitive: `permission-floor`, `advisory`, `.claude/settings.local.json`. `CHANGELOG.md` SHALL
  carry an entry containing the literal `permission-floor probe` — the bare `permission-floor` is not
  sufficient, since the map atom's own entry already carries it inside `docs/permission-floor.json`, which would
  make the checkpoint's pre-change baseline vacuous. *(Checkpoints: the doc-claims test,
  `-k troubleshooting_documents_the_advisory_tier`, and a `CHANGELOG.md` content match.)*

- **AC-DPF-7** *(Invariant — ubiquitous)* **(the sibling map's closed world is preserved, and `entries` is
  provably untouched):** Because `scripts/foundry_permission_floor.py` is a new `*.py` file under `scripts/`,
  the authorized map's AC-PFM-2 closed world obliges it to be tiered XOR excluded. It is a pure imported
  library (no `argparse`, no `main()`, no `__main__` block, never in command position), so
  `docs/permission-floor.json` SHALL gain **exactly one** `not_invoked` element — `script:
  "foundry_permission_floor.py"` with a one-line rationale — and this atom SHALL add, remove, re-tier or
  reorder **no** `entries` element. That second half SHALL be proved by **snapshot equality, independently of
  the `not_invoked` addition**: the test module SHALL carry the SHA-256 of the merge-base `entries` array under
  a canonical serialization (`json.dumps(entries, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`)
  as a declared literal, and SHALL assert the shipped file's `entries` digest equals it. `python3 -m pytest
  tests/test_permission_floor_map.py -q` SHALL be green with this atom applied.
  *(Checkpoints: `-k the_map_records_the_new_module_as_not_invoked`, `-k the_map_entries_array_is_unchanged`,
  plus the map module's green run.)*
<!-- /normative -->

## Design / notes

- **Why canonicalization is the load-bearing half.** The map's R2 (invocation-spelling coupling) is not
  hypothetical: this workspace's live `ask` rule is
  `Bash(python3 /home/…/.claude/plugins/cache/agentic-foundry/foundry/*/scripts/foundry-authorize.py *)` while
  the map declares `Bash(~/.claude/plugins/cache/*/foundry/*/scripts/foundry-authorize.py:*)`. A naive string
  compare finds no relation, reports `allow-absent`, and **misses the shadow it exists to catch**. AC-DPF-3's
  fold is what makes the live case convict, and AC-DPF-5(a) pins that exact spelling as a fixture so the fold
  cannot regress into vacuity.
- **Why blanket grants get their own top rank.** The live `allow` rule
  `Bash(python3 /home/…/foundry/*/scripts/*)` is *not* blanket (its reach prefix is the scripts directory), but
  one keystroke's difference — `Bash(python3 *)` — is, and under the old draft it would have been reported as
  nothing at all: it is a prefix rule whose reach prefix is empty, so it covers everything and would have been
  folded into a wall of per-entry `ask-shadowed` lines or missed entirely. One finding naming every ceremony it
  swallows is the shape that actually reaches the operator.
- **Why the ceremony set is derived, not declared.** Adding a `ceremony: true` field to the map would create a
  second source of truth in a file this atom is otherwise forbidden to touch. "`ask` tier + names a `scripts/`
  basename" is exactly the map's own ceremony population (AC-PFM-3's pins) read structurally. Its known miss is
  the non-script ceremony `Bash(claude plugin tag:*)`, which degrades to plain `ask-shadowed` — R7.
- **Why the deny direction refuses the fold.** The two directions have opposite failure costs. A false
  "covered" on `allow` costs a missing informational line; a false "covered" on `deny` is *silence about a
  protection that is not there*. Requiring reach-prefix equality for deny buys fail-safe noise (a workspace
  with a **broader** deny than the map declares will still report `deny-missing`) and that trade is named in R1
  rather than hidden.
- **Why a mismatch never reddens.** Local divergence is frequently deliberate — an operator who widened one
  rule for a session, an adopter mid-migration, a workspace that has not run the bootstrap CLI. Reddening those
  trains the operator to ignore the doctor. The one genuinely broken state — a floor file the plugin ships that
  does not parse or whose glob is not the pinned shape — is a broken install, and reddens.
- **The advisory glyph is constrained by property, not pinned as a literal.** AC-DPF-1 requires distinctness
  and forbids the `XX` substring (a real coupling: `_check_doctor` scrapes `[XX`), but names no glyph — the
  same convention the shipped renderer already follows, where the marks `ok `/`skip`/`XX ` appear in contract
  locators as observed output and in no spec's normative region.
- **The release gate keeps working.** `foundry-release-acceptance._check_doctor` runs the candidate doctor
  against a **synthetic project dir** seeded only with an operator registry — no settings files at all. Under
  AC-DPF-4 that yields one advisory finding, not RED, and the advisory mark carries no `XX`, so the pre-cut
  acceptance gate stays green. This seam is part of why the no-configuration case is advisory rather than a
  failure, and it is stated rather than discovered later.
- **Probe count, verified 2026-08-02.** The shipped `scripts/foundry-doctor.py` registers **six** probes —
  `manifest`, `hooks`, `skills-frontmatter`, `stack-profile-lock`, `operator-registry`, `control-plane` — and
  `docs/QUICKSTART.md` already claims `6 probes`. This atom takes it to **seven**. AC-DPF-6 pins the
  *bijection*, not a literal number, because `tests/test_doc_claims.py` derives the number from the tree.
- **Build order.** AC-DPF-5(c) imports the sibling suite and AC-DPF-7 asserts against the shipped map, so
  `[[feat-foundry-permission-floor-map]]` lands first. That was already true (AC-DPF-7's map-suite run); the
  conformance table makes the dependency explicit rather than incidental.
- **Source.** ER `intake/er-onboarding-wizard-and-permission-floor.md` (atom 4 of its decomposition, plus the
  *Continuous* bullet: *"`/foundry:doctor` gains an advisory permission-floor drift check (rules present,
  plugin-path wildcard still resolving)"*), and `[[feat-foundry-permission-floor-map]]` **R6**, whose named
  follow-on this atom discharges.

## Clarifications

- **Q: The brief denied `docs/permission-floor.json`; why does AC-DPF-7 allow one line of it?**
  **Resolved, and flagged for the operator at authorize.** The map's AC-PFM-2 derives its ground truth from the
  shipped tree at test time, so the moment this atom adds `scripts/foundry_permission_floor.py` the authorized
  map's own suite goes RED unless the file is classified. The map atom cannot pre-add the row (its
  reverse-direction check forbids naming a script absent from the tree), so the classification can only land
  here. The widening is bounded to a single `not_invoked` element, and AC-DPF-7 now *proves* `entries` is
  untouched by digest rather than merely forbidding it. Rejected alternative: inlining the comparison into
  `scripts/foundry-doctor.py` (already tiered), which avoids the widening but puts a ~300-line pure matcher
  inside a probe file the brief asks to keep thin.
- **Q: Two review findings asked for different top ranks — `blanket-allow` top, and `ask-shadowed-ceremony`
  leading the summary. Which won?** **Both, unified.** `blanket-allow` is rank 1 because it is the maximal
  case (a blanket rule swallows every ceremony *and* everything else, so ranking a single ceremony shadow above
  it would bury the worse finding). The summary line's *lead clause* is orthogonal to the rank: AC-DPF-8 makes
  it fire whenever **any** ceremony entry is shadowed, by a blanket rule or a narrower one, so the operator
  reads "the front-authorization prompt is not firing" first in either case.
- **Q: Should the probe compare `generated_for_plugin_version` against the installed plugin version?**
  **No — out of scope.** That signal answers the map's **R10** (a floor that widened under an already-trusted
  workspace), which R10 assigns to the **bootstrap CLI** (ER atom 1), the only component that sees the trust
  moment. Adding it here would be an unassigned requirement.

## Out of scope / non-goals

- **Auto-reconciling, healing, or writing any `.claude/settings*.json`.** Report-only, forbidden by AC-DPF-2,
  and hard-denied to an agent by the classifier regardless.
- **The bootstrap CLI's write path** (ER atom 1, Wave 3) — it writes the declarations; this probe watches them.
  Also its R10 trust-re-prompt observation (see Clarifications).
- **Changing the map's content, tiers, schema, or any `entries` element** — AC-DPF-7's single `not_invoked` row
  is the whole of this atom's reach into that file, and its digest assertion is the proof.
- **Reading user-level (`~/.claude/settings.json`) or enterprise-level settings** — see R2.
- **Widening the btb-gates security-path pattern** so this atom's own surfaces route to floor #3 — that edits
  `.github/workflows/**`, which this contract denies. See R6.
- **Enforcing anything.** No exit-code change except the malformed-floor RED; no gate, hook, or CI check is
  added or altered; `--session-start` remains fail-open.

## Residuals ledger

- **R1 — Canonicalization is a heuristic over a documented-fragile matcher, and its two directions fail
  differently (epistemic).** The platform's Bash rules are prefix matches and its own docs caveat that string
  matching is fragile (map R1/R2). AC-DPF-3's fold handles the spellings observed in the live workspace and the
  map, but an unfolded spelling (a `CLAUDE_PLUGIN_ROOT` expansion, a symlinked cache path, an interpreter
  outside the closed set, a wrapper) can yield a **false `allow-absent`** (noise) or a **missed `ask-shadowed`**
  (silence on a real decay). **Direction, stated:** the fold is applied on the `ask`/`allow` directions, where a
  miss costs a line of text; it is **refused on the `deny` direction** (AC-DPF-3(b)), which requires reach-prefix
  equality — so `deny-missing` is deliberately noisy (a workspace whose deny is *broader* than the map still
  reports) rather than deliberately silent. **Bound:** the probe grants and blocks nothing; the `unclassified`
  bucket makes an unreducible rule visible rather than absent; and the operator's terminal test pass
  (`CLAUDE.md` § *Delivery sign-off*) is the outer control — a **practice, not a control**, from which no
  coverage is claimed.
- **R2 — Only project-level settings are read (scope, accepted).** Claude Code also resolves user-level
  `~/.claude/settings.json` and enterprise policy files. A grant living there is invisible, so an
  `allow-absent` may be false and an `ask-shadowed` may be missed. **Bound:** R6's assignment names the two
  project files and the decay path writes to `settings.local.json`; widening means reading files outside the
  workspace — a deliberate blast-radius increase this atom declines and names.
- **R3 — The probe proves declaration drift, never live matching behaviour (epistemic).** It compares two
  documents; it cannot observe whether the harness matched a rule against a real command, and in particular it
  makes **no claim about tier precedence** — a `blanket-allow` finding names the `ask`/`deny` entries inside its
  reach without asserting that the blanket rule defeats them at match time. The map's R1 records the same gap
  from the other side. **Bound:** the atom claims exactly what it computes — a declared-vs-effective diff.
- **R4 — Advisory by design means an ignored report changes nothing (design, accepted).** A workspace can sit
  in permanent drift with a green doctor. That is the deliberate trade: RED on mismatch reddens legitimate
  local divergence and the release-acceptance seam's synthetic project dir. Stated rather than implied as
  coverage.
- **R5 — The advisory outcome touches the doctor's shared result renderer and its session-start gate
  (maintenance, bounded).** AC-DPF-1 adds a fourth outcome value to a rendering path all probes flow through,
  and AC-DPF-4 extends the session-start banner condition from "hard-fail" to "hard-fail or advisory".
  **Bound:** the extension only **adds** output, and only when a probe returns the new sentinel, which no
  pre-existing probe does; AC-DPF-1's unchanged-probes checkpoint, the `DOCTOR-GREEN` regression, and
  AC-CPP-7's own fail-open assertions cover the shared path from three sides.
- **R6 — This atom's two new surfaces do not route to the floor-#3 security-path CI check (process gap,
  tracked).** Verified against `.github/workflows/btb-gates.yml`: the `security-path` job's pattern is
  `(auth|secret|credential|token|provenance|signing|\.rego$)|^\.github/|^hooks/|^\.claude-plugin/|(^|/)(standing-versions|profile-version-ledger)|<dependency manifests>|^skills/|^agents/|^rulesets/|^scripts/foundry_tier_preflight`.
  Neither `scripts/foundry_permission_floor.py` nor `docs/permission-floor.json` matches any arm, so a future
  edit to the matcher that decides what an agent may run without a prompt would not mechanically demand a
  security review. **Bound:** this atom's own security review is happening, and the probe grants nothing.
  **Named follow-on — the map atom's R7, extended:** that tracked light-lane change already adds an anchored
  `^docs/permission-floor` arm; it SHALL additionally add `^scripts/foundry_permission_floor`, OR-append only,
  widen-only, with negative-control rows in `tests/fixtures/btb-gates/security-path-matrix.yaml` (the
  AC-SCW-13 / AC-TARC-15 discipline). Not done here: the contract denies `.github/workflows/**`, and that file
  matches `^\.github/` itself, so the widening rides its own security-reviewed PR.
- **R7 — The structural ceremony derivation misses non-script ceremonies (design, accepted, fail-safe).**
  AC-DPF-8's ceremony set is "`ask` tier + names a `scripts/` basename", so the map's `Bash(claude plugin
  tag:*)` ask is *not* a ceremony under it and a shadow of it degrades to rank-3 `ask-shadowed`. **Bound:** the
  finding is still reported, only one rank lower; the alternative — a `ceremony` flag in the map — creates a
  second source of truth in a file this atom may not edit. Direction is fail-safe: under-ranking, never
  silence.
- **R8 — The `entries` digest is a tripwire that a legitimate future map edit must deliberately update
  (maintenance, accepted).** AC-DPF-7 pins the merge-base `entries` digest as a literal in this atom's test
  module, so any later `entries` change — by the map atom or anyone — turns that test RED until the digest is
  updated in the same reviewed diff. **Bound and intended:** that is the same deliberate-upgrade discipline
  `CLAUDE.md` § *Standing versions* applies to pins; the failure is loud, one-line, and reviewed, and it is the
  only mechanism available to a test that cannot reach git history.
- **R9 — The read-only audit-hook witness is scoped to the fixture tree (epistemic, bounded).**
  `sys.addaudithook` is process-global and cannot be removed, so the assertion filters to write-class events
  whose path resolves under the fixture tree; a write outside that tree would not be witnessed by this control.
  **Bound:** the byte-identity snapshot covers the tree from the other side, the no-`subprocess`/no-network
  import assertion bounds indirect writes, and the classifier hard-denies an agent settings write regardless.

## Changelog

- v1.1 One remediation round against the 2026-08-02 consolidated four-lens review, every tree claim
  re-verified against the shipped `agentic-foundry` (six probes, `_check_doctor`'s `[XX` scrape, the live
  `settings.local.json` shadow, the btb-gates pattern, the map suite's `_subsumes`). **Blocks resolved:**
  *(1)* the `unclassified` bucket now redacts rule bodies entirely — tool-name prefix + origin + tier key +
  count only — with AC-DPF-2(c) restating non-disclosure as VALUE-scoped ("no settings-derived string other
  than a canonicalized rule that covered a map entry"), AC-DPF-8 mandating AC-ROST-5 sanitization extended to
  zero-width/bidi plus a length cap, and AC-DPF-5(a)(j) driving a credential-shaped + ANSI-bearing fixture;
  *(2)* blanket grants are no longer silent — new **rank-1 `blanket-allow`** class (a prefix rule whose reach
  prefix reduces to empty), naming every map `ask`/`deny` it swallows, with fixtures (g) `Bash(*)`,
  (h) `Bash(python3 *)`, (i) `Bash(python3:*)`; *(3)* new **`settings-unreadable`** class for a file that
  exists but fails parse/shape validation, forcing `advisory` and never `ok`, with genuine absence keeping the
  old path; *(4)* the "agree by construction" claim **dropped** — AC-DPF-5(c) replaces it with a cross-atom
  conformance obligation running an 8-row shared table, spelled in the spec, against **both** the map suite's
  `_subsumes` and this module's `covers`, failing (never skipping) if the sibling cannot be imported;
  *(5)* AC-DPF-7 gains a real no-`entries`-touched proof (canonical-serialization SHA-256 snapshot,
  independent of the `not_invoked` addition), with R8 recording the tripwire cost; *(6)* AC-DPF-3 split —
  mechanics stay in AC-DPF-3, report contents/format move to **new AC-DPF-8** with named checkpoints for the
  `unclassified` bucket and the summary line. **Risks folded in:** *(7)* fixed severity rank with
  `ask-shadowed-ceremony` at rank 2, the ceremony set derived structurally (`ask` tier + `scripts/` basename,
  no schema change), the summary line leading with `the front-authorization prompt is not firing` whenever any
  ceremony is shadowed, and `--session-start` filtered to the actionable ranks plus one informational count
  line; *(8)* fail-open made provable — outcome-level exception tolerance, regular-file `stat` + 1 MiB cap
  before read, malformed-floor defined as schema-validation failure, and a fail-open control per failure mode
  (deep-recursion JSON, `permissions.allow` as string/dict/null, int elements, `PermissionError`, FIFO,
  symlink-to-non-regular, `UnicodeDecodeError`, and the mandatory malformed-floor-at-session-start) each
  asserting exit 0 and a 5-second wall-clock bound; *(9)* `plugin_root_glob` runtime validation (pinned cache
  prefix, no `..`, no `**`, ≤2 `*`, 256-match expansion cap) as malformed-floor RED; *(10)* read-only pinned at
  the outcome level (byte-identity snapshot with settings files explicitly hashed, `sys.addaudithook` witness,
  no `subprocess`/network import), mechanism enumeration dropped, R9 bounding the witness; *(11)* duplicate
  coverage reported once per map entry citing every distinct covering origin; *(12)* `advisory` carried by a
  sentinel that is not `True`/`False`/`None` (the shipped renderer reads `None` as `skip`), with a negative
  control proving it never collapses into `skip` or `ok`, and the mark forbidden from containing `XX`;
  *(13)* prior art corrected — `kubectl diff` and `gh auth status` **do** signal via exit codes, so the
  flat-exit grounding is narrowed to ArgoCD `OutOfSync` + the doctor's own AC-CPP-7 convention and named a
  local choice; the 5→6 probe narrative corrected to six shipped / seven with this atom; the deny fold
  direction named normatively (exact reach equality, no fold) and in R1. **Residuals:** R6 records the
  security-path CI routing gap and extends the map atom's R7 follow-on to `^scripts/foundry_permission_floor`;
  R7 names the non-script-ceremony blind spot; R8/R9 are new. AC-DPF-1 gains an existing-probes-unchanged
  checkpoint, AC-DPF-2 an absent-or-unreadable-tolerated checkpoint, and the contract's `baseline: none` rows
  each carry a one-line rationale. AC-IDs 1–7 unchanged; **AC-DPF-8 is new**.
- v1.0 Draft. Add the doctor's `permission-floor` probe: an advisory, read-only comparison of the workspace's
  effective permission configuration (`.claude/settings.json` **and** `.claude/settings.local.json`, unioned
  and origin-tracked) against the shipped `docs/permission-floor.json`, reporting `ask-shadowed`,
  `deny-missing`, `allow-absent` and `stale-plugin-path` findings plus an `unclassified` bucket, with the
  comparison logic as a pure module (`scripts/foundry_permission_floor.py`) and a fixture-driven negative
  control per class. Report-only and never RED on a mismatch; RED only on a malformed floor file; fail-open at
  `--session-start`. Discharges **R6** of `[[feat-foundry-permission-floor-map]]` and realizes ER atom 4 of
  `intake/er-onboarding-wizard-and-permission-floor.md`.
