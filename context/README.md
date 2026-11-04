# Foundry context kit — the canonical authoring templates

This directory is the **single source of truth** for foundry's spec-authoring methodology artifacts.
It ships **with the plugin**, so `claude plugin update` delivers template improvements to every adopter
(the plugin is a self-contained ecosystem — it does not resolve these from a sibling repo).

- `operating-model.md` — **the default path on one page**: a ticket with a runnable `Done means`, the
  paper guard, the Stop hook, local-green, the platform as the gate. Start here.
- `branch-discipline.md` — branch, worktree and release-branch rules.
- `permissions-template.yaml` — the starter standing-grants policy (`.foundry/permissions.yaml`).
- `feat-spec-template.md` — the atomic spec template for the **opt-in spec lane** (industry-grounded
  shape: Requirement/Invariant tagging, EARS phrasing, a `## Prior art / industry grounding` section, a
  delimited `<!-- normative -->` region, stable AC-IDs). Grounding: industry spec-shape research
  (Spec Kit / Kiro / EARS prior art).
- `glossary.md` — the Foundry methodology lexicon (extend with your project's domain terms).

Consumed by the `intake` and `authorize` skills (the opt-in spec lane). An adopter workspace references
THIS kit rather than carrying its own copy.
