# Foundry docs

Four kinds of page, four kinds of question. Start where your question lives:

```
            LEARNING it                          USING it
   ┌───────────────────────────┐      ┌───────────────────────────────┐
   │ TUTORIALS                 │      │ HOW-TO GUIDES                 │
   │  "teach me, step by step" │      │  "I need to do X, now"        │
   ├───────────────────────────┤      ├───────────────────────────────┤
   │ EXPLANATION               │      │ REFERENCE                     │
   │  "help me understand why" │      │  "look something up"          │
   └───────────────────────────┘      └───────────────────────────────┘
            UNDERSTANDING it                     CHECKING it
```

## Tutorials — learning, step by step

- **[Quickstart](QUICKSTART.md)** — zero to your first merged ticket, three verbs, about ten minutes.
- **[The operating model](../context/operating-model.md)** — the whole default path on one page.
- **[The Acme Links tutorial](https://github.com/lukasrepublic/agentic-handbook/blob/main/docs/example-acme-links/README.md)**
  — the full guided build: an empty repo to a governed, live-proven merge in seven
  checkpointed steps, including watching the floor block a deliberately broken seam.

## How-to guides — one task each

- [Recover from a failed gate](how-to/recover-from-a-failed-gate.md)

- [Collect native usage per operator and outcome](how-to/usage-collection.md) — OTEL export to a collector, dispatch bindings, attribution, and the coverage report
- [Branching and cleanup](how-to/branching-and-cleanup.md) — tickets into `release/<version>`, one PR to `main`, the worktree/branch gc
- [Run author/approver separation with CODEOWNERS](how-to/team-review-with-codeowners.md)
- [Run a multi-repo control plane](how-to/multi-repo-control-plane.md) — **the mode Foundry is built for**: one workspace governing several code repos, and why you run Claude from the control plane rather than inside a repo
- [Cut a release](how-to/cut-a-release.md)
- [Plan a programme](how-to/execution-plan.md) — waves, batches, critical path and total float from the live programme issues
- [Use the shared message log](how-to/shared-message-log.md) — the append-only machine channel on `foundry/messages`, the issue-per-topic people channel, and the pairing audit
- [Finish a plugin upgrade](how-to/post-upgrade.md) — `npx update-agentic-workspace@latest` (the script half), then `/foundry:post-upgrade` (the judgement half)
- [Govern a workspace: intake and upgrade](how-to/governance-intake-and-upgrade.md) — `discover`, `select`, `declare`, `apply`, `baseline`, then `upgrade` (three-way, by pull request) and the upstream leak guard
- [Turn a standing grant into policy](how-to/standing-grants.md) — `.foundry/permissions.yaml` (seeded by the updater), compile, the capability preflight
- [Enable agent teams](how-to/agent-teams.md)
- [Never read a secret value](how-to/secrets-never-read.md) — rule D1: verify by metadata only; the secret-read guard, its add-only policy lists, and why only an IAM-level boundary prevents the read
- [Run the plugin's native evals](how-to/plugin-evals.md)
- [Scale to zero for occasional privileged jobs](how-to/scale-to-zero-privileged-jobs.md) — rule C3: a tainted node class that scales to zero, with the kit's pattern and template
- [CI budgets are tools, not gates](how-to/ci-budgets-are-tools.md) — rule C2: budgets report and never gate; the budget lint
- [The evidence and baseline rule](how-to/evidence-baseline-rule.md) — rule G1: no baseline before the first fully green run
- [Operator hand-off files](how-to/operator-handoff-files.md) — rule E3: where hand-off files go, durable instructions in the repo, PDFs beside the hand-off files
- [Governance hardening notes](how-to/governance-hardening-notes.md) — what the hardening pass closed, and which controls are boundaries and which are visibility controls

## Reference — look it up

- **[Verb quick reference](VERBS-QUICK-REF.md)** — every `/foundry:*` verb, one line each,
  grouped by stage.
- **[Foundry Governance](governance.md)** — the sixteen categories and their kits, floor against tunable, the per-ticket sections, the review lenses, exceptions and baselines, and upgrade.
- **[The operating-model policy](operating-model-policy.md)** — every key of `.foundry/operating-model.yaml`, floor or tunable, the primitive that acts on it, and the rule-to-criterion traceability table.
- **[Glossary](glossary.md)** — the vocabulary: ticket, contract, floor…
- **[Troubleshooting](troubleshooting.md)** — symptom-first runbooks. · **[FAQ](faq.md)**
- **[Terminology standard](TERMINOLOGY.md)** — the maintainer-facing naming standard every
  skill and agent conforms to.
- **Opt-in spec format** — the template ships in [`context/`](../context/README.md).

## Explanation — why it's built this way

- **[Design & trust model](DESIGN.md)** — what the machine enforces, what stays human, and
  what Foundry deliberately does not guarantee.
- **[Architecture](architecture.md)** — the workspace ⟷ factory split and the wiring contract.
- **[The merge floor](merge-floor.md)** — the honest tier model and exact hook semantics.
- **[How it compares](comparison.md)** — including when NOT to use Foundry.
- [Identity isolation](identity-isolation.md)
