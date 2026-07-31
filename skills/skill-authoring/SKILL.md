---
name: skill-authoring
description: The discipline for authoring foundry skills — information hierarchy / progressive disclosure, leading words, completion criteria, no-op pruning. A REFERENCE the author consults when writing or revising a skills/<name>/SKILL.md; advisory craft, it does NOT gate, approve, or merge.
---

# /foundry:skill-authoring — the discipline for writing foundry skills

A reference for authoring `skills/<name>/SKILL.md` files that are **predictable, lean, and
self-describing**. Foundry ships many skills; this codifies the discipline so they stay
consistent. Advisory craft: it disciplines *how* a skill is written; it never enforces or merges.

## When to trigger

- Authoring a new foundry skill, revising an existing one, or reviewing a skill for sprawl,
  over-claim, or a missing completion criterion.

## ADVISORY — this skill advises the trusted operator; it is NOT a gate

Reference guidance for the trusted operator + a mechanical mistake-catcher (the bloated skill, the
missing completion criterion). It **never enforces, approves, gates, or merges** — the merge floor
(floor #2) is the only machine-derived merge authority.

## Principle: information-hierarchy (progressive disclosure)

Order content by **immediacy**. The three-tier ordering is: an **`in-skill step`** first (the
load-bearing ordered actions the agent executes), then in-skill reference (definitions consulted on
demand), then an external pointer behind a link (progressive disclosure). Push rarely-needed detail
down a tier; never bloat the top with what the agent reaches for only sometimes. Push too much down
and you hide what the agent actually needs; push too little and the top goes top-heavy.

## Principle: leading-words

Compress a repeated multi-word concept into **`one token`** the model already holds from
pretraining — a `pretrained token` (foundry's are *atom*, *floor*, *walk*, *gate*, *seam*). Reuse
that single token everywhere the concept appears, so the agent reaches the same behaviour every time
the word appears (execution) and prompts/docs/code share one vocabulary (invocation). Refactor
duplicated multi-word phrasing into the single token.

## Principle: completion-criteria

End each procedure on a criterion that is **`checkable`** and **`exhaustive`** — a predicate the
agent can evaluate true/false, covering every exit, so a step cannot be declared done prematurely.
A fuzzy or partial criterion is the classic premature-completion failure mode; sharpen the criterion
first, and only split a step off if the criterion is irreducibly fuzzy.

## Principle: no-op-pruning

For every line, apply the **no-op test**: does this line **`change behaviour`**? If it does not
change behaviour — if the model already obeys it by default — delete the whole sentence, not just a
few words. Keep a single source of truth per fact; stale layers and duplication are maintenance cost
and token waste.

## Principle: terminology & triggers (conform to the standard)

Every skill conforms to the **Foundry Terminology Standard** (`docs/TERMINOLOGY.md`) — the canonical,
industry-grounded lexicon + trigger grammar. Two rules bind the author:

- **Use the standard terms.** A primitive is a `skill` / `agent` / `subagent` / `tool` / `hook` / `command`;
  a task-triggered, looping, model-invoked skill is a **playbook** (NOT a "micro-workflow" — that term is
  retired; `workflow` is reserved for the predefined-path macro layer). The coinages `floor` / `engine` /
  `atom` / `live-seam` / `exit-gate` are defined there — use them as defined.
- **The `description` is the trigger surface.** It is the only thing the model matches a request against, so
  it MUST carry **(1) the canonical `verb-object`** (e.g. `cut-release`, `authorize spec`) and **(2) 2–3
  natural-language trigger phrases** the operator might actually type ("cut the v0.7.0 release", "ship the
  release"). A **playbook** additionally declares its shape: TRIGGER / ENGINE / EXIT-CRITERIA / FLOORS-TOUCHED.
