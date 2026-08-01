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

- **[Quickstart](QUICKSTART.md)** — zero to your first governed merge, six verbs, about ten minutes.
- **[The Acme Links tutorial](https://github.com/lukasrepublic/agentic-handbook/blob/main/docs/example-acme-links/README.md)**
  — the full guided build: an empty repo to a governed, live-proven merge in seven
  checkpointed steps, including watching the floor block a deliberately broken seam.

## How-to guides — one task each

- [Recover from a failed gate](how-to/recover-from-a-failed-gate.md)
- [Adopt Foundry on an existing codebase](how-to/adopt-on-an-existing-codebase.md) (brownfield)
- [Run author/approver separation with CODEOWNERS](how-to/team-review-with-codeowners.md)
- [Run a multi-repo control plane](how-to/multi-repo-control-plane.md) — **the mode Foundry is built for**: one workspace governing several code repos, and why you run Claude from the control plane rather than inside a repo
- [Cut a release](how-to/cut-a-release.md)
- [Migrate from Spec Kit](how-to/migrate-from-spec-kit.md) · [Migrate from OpenSpec](how-to/migrate-from-openspec.md)

## Reference — look it up

- **[Verb quick reference](VERBS-QUICK-REF.md)** — every `/foundry:*` verb, one line each,
  grouped by loop stage.
- **[Glossary](glossary.md)** — the vocabulary: atom, contract, floor, journey…
- **[Troubleshooting](troubleshooting.md)** — symptom-first runbooks. · **[FAQ](faq.md)**
- **[Terminology standard](TERMINOLOGY.md)** — the maintainer-facing naming standard every
  skill and agent conforms to.
- **Spec + contract formats** — the templates ship in [`context/`](../context/README.md).

## Explanation — why it's built this way

- **[Design & trust model](DESIGN.md)** — what the machine enforces, what stays human, and
  what Foundry deliberately does not guarantee.
- **[Architecture](architecture.md)** — the workspace ⟷ factory split and the wiring contract.
- **[The merge floor](merge-floor.md)** — the honest tier model and exact hook semantics.
- **[How it compares](comparison.md)** — including when NOT to use Foundry.
- [Identity isolation](identity-isolation.md) · [GitHub Projects projection](github-projects-projection.md)
