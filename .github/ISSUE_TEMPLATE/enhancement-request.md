---
name: Enhancement request (adopter → framework)
about: A generalizable learning discovered while running Foundry in an adopter workspace, triaged for the core plugin or the handbook template.
title: "[ER] <generalized one-line problem>"
labels: enhancement-request
---

<!--
  This is the RECEIVING shape for `/foundry:upstream-submit`. Adopters: prefer that verb — it
  fail-closes against proprietary leakage, stamps provenance + a dedup key, and renders this
  shape for you. Filing by hand? Keep everything GENERALIZED (the mechanism, not your incident)
  and carry NO proprietary context (product/customer/internal names, operator ids, memory refs).

  An ER is a REQUEST and a new /foundry:intake source — NOT a contribution to `main`. If accepted,
  the maintainer runs it through intake → audit → authorize → build (front-authorization, no skip).
-->

## Generalized problem
<!-- The framework mechanism that fails or is missing — abstracted from any single product/stack.
     "Would a different product on a different stack hit this?" If no → it's adopter-local, not an ER. -->

## Evidence (sanitized)
<!-- What you observed, with all proprietary context removed. The shape of the failure, not the incident. -->

## Proposed mechanism
<!-- The framework-level change you propose (a verb/script/hook/skill change, or a template convention). -->

## Triage bucket
<!-- One of: `core-plugin` (a change in agentic-foundry) | `handbook-template` (a workspace-shape convention). -->

## Provenance (machine)
<!-- `/foundry:upstream-submit` fills this. Filing by hand, include what you can: -->
- adopter: <label>
- foundry-version: <version you are on>
- dedup-key: <ur-… ; the verb computes this over the normalized generalized problem>
