# Foundry context kit — the canonical authoring templates

This directory is the **single source of truth** for foundry's spec-authoring methodology artifacts.
It ships **with the plugin**, so `claude plugin update` delivers template improvements to every adopter
(the plugin is a self-contained ecosystem — it does not resolve these from a sibling repo).

- `feat-spec-template.md` — the atomic spec template (industry-grounded shape: Requirement/Invariant
  tagging, EARS phrasing, a `## Prior art / industry grounding` section, a delimited `<!-- normative -->`
  region, stable AC-IDs). Grounding: industry spec-shape research (Spec Kit / Kiro / EARS prior art).
- `acceptance-contract-template.yaml` — the sibling acceptance contract (scope + AC-bound checkpoints).
- `citation-grammar.md` — the `[Doc:]`/`[Atom:]`/`[Design-asset:]` citation grammar.
- `glossary.md` — the Foundry methodology lexicon (extend with your project's domain terms).
- `constitution-template.md` — an adopter-facing, generic template of the checked spec/engineering
  standard `/foundry:spec-review` reviews against. Copy it to your own workspace's
  `CONSTITUTION.md`, fill the placeholders, and cite it as the checked standard from your own specs
  and skills — do not edit this shipped copy in place.
- `charter-template.md` — the one-page noninteractive-mode unit of work (the lightweight lane's
  spec substitute; committed to the workspace before the build).
- `staging-checklist-template.md` — the staging certification checklist `/foundry:certify-staging`
  fills (deploy observation + journeys re-run rows + the operator sign-off line).

Consumed by the `spec-author` / `spec-reviewer` agents, the `extract-spec` / `intake` skills, and the
`docs/feat-spec.conformance.yaml` conformance check. An adopter workspace references THIS kit rather than
carrying its own copy.
