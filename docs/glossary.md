# Glossary

The vocabulary, in one place. Terms link to the doc that owns them.

- **Ticket** — the default unit of work: a GitHub issue with a runnable `## Definition of Done`
  command and a `## Referenced Artifacts` list. Assignment is the authorization. See
  [context/operating-model.md](../context/operating-model.md).
- **Atomic spec** — the unit of work on the opt-in spec lane: one capability, one spec file
  with stable AC-IDs, small enough to review honestly. Hard ceiling: 14 acceptance criteria / 8,000
  words (no override — oversize means decompose).
- **AC-ID** — a stable identifier for one acceptance criterion (`AC-EXPORT-3`). Checkpoints,
  reviews and tests key to it; it never renumbers.
- **Acceptance contract** — the YAML sibling of a spec: scope (`allowed_paths`) +
  observable checkpoints per AC-ID. Frozen (hashed + operator-signed) at authorization —
  the binding definition of done on the spec lane. See [QUICKSTART](QUICKSTART.md).
- **Authorization / front-authorization** — on the opt-in spec lane, the operator's explicit approval
  of a spec + contract *before* implementation; an unauthorized spec cannot reach
  `main` through the factory. On the default path the standing grant in
  `.claude/foundry-operators.json` is the authorization. "Approval is the spec merged to the workspace main; git history is the ledger."
- **Operator** — the human who authorizes (the standing grant, or a spec), merges, and signs off. Registered in
  `.claude/foundry-operators.json`. The tool's terminal authority, by design.
- **Workspace vs factory** — the workspace repo holds the WHAT (tickets, runbooks, governance);
  the plugin is the HOW (verbs, process). See [architecture.md](architecture.md).
- **Traceability ids (`feat-<slug>`, `AC-XXX-n`, gap ids)** — parenthetical anchors like
  `(feat-foundry-dispatch-on-native-workflow, AC-DNW-1..4)` cite the spec atoms and acceptance
  criteria that authorized a behavior. For plugin-internal behaviors those specs live in the
  maintainer's self-hosting workspace (Foundry is built with Foundry) and do not ship in this
  repo — treat the ids as provenance stamps, not links. Your own atoms' ids resolve in YOUR
  workspace the same way.
- **The both-modes floor** — the four never-relaxed invariants that hold in every mode:
  front-authorization, the merge floor, security review on sensitive paths, typed contracts +
  git discipline. "Both modes" is historical (attended/autonomous); the floor predates and
  outlives the mode names.
- **The merge floor** — the tiered enforcement between a PR and `main` (branch
  protection/CI + the git-discipline hook). Not a bespoke gate. See
  [merge-floor.md](merge-floor.md).
- **Tier A / Tier B** — server-enforced required checks vs always-reporting advisory
  checks (labeled as such), per your platform plan. See [merge-floor.md](merge-floor.md).
- **Paper guard** — the PreToolUse hook that refuses a write under `specs/`, `intake/`, `.foundry/`,
  `docs/`, `status-reports/`, `charters/`, or to a contract / manifest file unless the active
  ticket's `Referenced Artifacts` list names the path. Code, infra, tests and config are never gated.
- **Local-green** — `scripts/foundry-test.sh` ran the repository's own CI command and recorded
  success for HEAD; the git-discipline hook refuses a push and a non-draft PR until it exists.
- **Sign-off** — the operator's own test pass, the last step of delivery. A practice, deliberately
  never a machine gate.
- **Stack profile** — a versioned pack describing how a stack boots, tests, and verifies
  (`packs/stack-profiles/`). `/foundry:verify` reads it.
- **Stage mode (`lean` / `scale`)** — how much ceremony the workspace runs with; lean is
  the solo default, scale enforces the full gates.
- **Doctor** — the seven-probe health check (`/foundry:doctor`); `DOCTOR-GREEN` or a named
  failure, in under a second. See [QUICKSTART](QUICKSTART.md).
- **Intake** — the front door: fuzzy ask → interactive discovery → a ticket (or, on the opt-in spec
  lane, an atomic spec + contract).
- **"§8" / `btb-gates`** — internal code names that survive in shipped strings (`btb-gates.yml` as a
  workflow filename). Treat both as proper nouns — they carry no meaning beyond naming the thing
  that prints them.
- **`infra-delivery` (id-*)** — a documented step SEQUENCE: the procedure family the id-* skills
  form, walked by the operator/agent in order. "Step N" labels a skill's position in the sequence —
  there is no shipped workflow engine or state-machine file behind it.
- **Integration branch** — the release's own branch (`release/<version>`, cut from `main` at
  intake) that every ticket PR targets instead of `main`, so a per-merge deploy trigger fires
  once per release rather than once per atom. See
  [branching-and-cleanup.md](how-to/branching-and-cleanup.md).

## Agile terms (2.0)

One row for each agile term, with the term it replaces. The 1.x opt-in spec lane keeps its own "atomic spec".
Critical path, total float, claim/lease and `blocked_by` are unchanged: they are scheduling and coordination terms.

<!-- vocab-allow:start -->
| Agile term | Replaces | Meaning |
|---|---|---|
| **Epic** | programme | The whole body of work. One Epic has one Epic issue and one Release plan. |
| **Epic issue** | programme issue | The parent issue of an Epic. Its sub-issues are the Stories. |
| **Story** | atom | One ticket: a GitHub issue with a runnable Definition of Done. |
| **Batch** | batch (unchanged) | Stories that touch the same code, built in one pull request. |
| **Increment** | wave | A dependency-ordered group of Batches. It is not time-boxed. |
| **Release plan** | execution plan | The plan file `release-plan.yaml`: Increments, Batches, critical path, total float. |
| **WIP limit** | concurrency limit | The most Batches in progress at one time (`wip_limit`, `deck.max_builders`). |
| **Epic review** | close-out | The single final session of an Epic. It runs the Epic's Definition of Done. |
| **Definition of Ready check** | design-reviewer + ticket-reviewer gate | The refinement check before build (`ticket-reviewer`, `design-reviewer`). |
| **Acceptance Criteria** | `## Acceptance` | The ticket section of EARS bullets with stable `AC-<n>` ids. |
| **Definition of Done** | `## Done means` | The ticket section with the one fenced command that exits 0 when the work is done. |
| **Referenced Artifacts** | `## Paper allowed` | The ticket section that lists the documents the change may edit. |
| **Retrospective** | `## Learned` | The comment each Story ends with. The Retrospective distill turns it into proposed rule edits. |
<!-- vocab-allow:end -->

A Scrum Definition of Done is a team-wide quality standard. Here, the per-ticket Definition of Done is that standard made
executable for one item, and the Epic review runs the Epic's own Definition of Done.
