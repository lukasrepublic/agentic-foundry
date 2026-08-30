# Glossary

The vocabulary, in one place. Terms link to the doc that owns them.

- **Atom / atomic spec** — the unit of work: one capability, one spec file with stable
  AC-IDs, small enough to review honestly. Hard ceiling: 14 acceptance criteria / 8,000
  words (no override — oversize means decompose).
- **AC-ID** — a stable identifier for one acceptance criterion (`AC-EXPORT-3`). Checkpoints,
  reviews, tests, and journeys key to it; it never renumbers.
- **Acceptance contract** — the YAML sibling of a spec: scope (`allowed_paths`) +
  observable checkpoints per AC-ID. Frozen (hashed + operator-signed) at authorization —
  the binding definition of done. See [QUICKSTART](QUICKSTART.md).
- **Authorization / front-authorization** — the operator's explicit approval of a spec +
  contract *before* implementation; in the factory flow an unauthorized spec cannot reach
  `main`. "Approval is the spec merged to the workspace main; git history is the ledger."
- **Operator** — the human who authorizes, accepts, and signs off. Registered in
  `.claude/foundry-operators.json`. The tool's terminal authority, by design.
- **Workspace vs factory** — the workspace repo holds the WHAT (specs, governance, corpus);
  the plugin is the HOW (verbs, process). See [architecture.md](architecture.md).
- **Wave / wave plan** — the parallel build schedule for a release: atoms grouped so no
  atom runs before its dependencies or beside a sibling touching overlapping paths.
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
- **Journey** — a tagged end-to-end Playwright test exercising an atom's ACs against the
  real running app (`@AC-EXPORT-3`). Ordinary Playwright specs, no custom format.
- **Certification (certify-local)** — deploying a release once locally and running every
  atom's journeys against that single instance; refuses (never passes vacuously) when
  journeys or a boot recipe are missing.
- **Acceptance (release accept)** — the operator's recorded verdict on a release. A
  practice note, deliberately never a machine gate.
- **Stack profile** — a versioned pack describing how a stack boots, tests, and verifies
  (`packs/stack-profiles/`). The certify/verify verbs read it.
- **Stage mode (`lean` / `scale`)** — how much ceremony the workspace runs with; lean is
  the solo default, scale enforces the full gates.
- **Doctor** — the five-probe health check (`/foundry:doctor`); `DOCTOR-GREEN` or a named
  failure, in under a second.
- **Intake** — the front door: fuzzy ask → interactive discovery → atomic spec + contract.
- **Spec-review** — the single-pass default review: deterministic pre-lints, three
  fresh-context reviewer questions, one remediation round, recorded content-bound.
- **Deep spec audit** — the multi-pass adversarial engine (`/foundry:audit`). Dormant,
  opt-in only — measured, multi-pass review does not out-find single-pass, so single-pass
  is the default.
- **Provenance (build-provenance)** — the record pinning a built atom to the exact
  workspace commit it was authorized against (multi-repo adopters).
- **"§8" / `btb-gates`** — internal code names that survive in shipped strings ("§8" in the
  deep-audit binder's output; `btb-gates.yml` as a workflow filename). Treat both as proper
  nouns — they carry no meaning beyond naming the thing that prints them.
- **Guarded-exec wrapper / `cloud_cli_exec_guard`** — an **adopter-supplied** wrapper convention
  the `hooks/foundry-cloud-cli-exec-guard.sh` hook matches bare `aws`/`kubectl`/`tofu` invocations
  against. The hook is **config-gated and fail-INERT**: with no wrapper declared, it blocks nothing.
  It is **defense-in-depth against the framework's own mistakes**, not a security boundary — the
  operator's **IAM restrictions on the AWS context they supply** are the control the framework
  actually relies on.
- **`software-delivery` / `infra-delivery` (sd-* / id-*)** — two documented step SEQUENCES:
  procedure families the sd-*/id-* skills form, walked by the operator/agent in order.
  "Step N" labels a skill's position in the sequence — there is no shipped workflow engine
  or state-machine file behind them.
