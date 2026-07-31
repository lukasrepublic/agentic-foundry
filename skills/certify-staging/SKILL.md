---
name: certify-staging
description: Emit the staging certification checklist for a release (/foundry:certify-staging <release>, introduced in the v0.25.0 certification realignment, CONSTITUTION.md §V factory-mode tail). Fills context/staging-checklist-template.md from the release manifest + the staging binding in .claude/foundry-project.json, observes deploy state via the kept /foundry:deploy-status surface, and STOPS — promotion stays CD-owned and operator-gated. REFUSES naming the missing prerequisite when no `staging` binding exists in .claude/foundry-project.json. Trigger after certify-local + operator acceptance, before any staging→production promotion decision.
---

# /foundry:certify-staging `<release>`

The release train's LAST certification step before promotion (CONSTITUTION.md §V `factory` mode:
… → certify locally → operator acceptance → **staging**). A thin procedure that emits a
checklist + an observation, then stops — it never promotes anything.

## When to trigger

- "certify `<release>` on staging", "/foundry:certify-staging `<release>`", after
  `/foundry:certify-local` is green and the operator has recorded acceptance
  (`/foundry:release accept …` — see `skills/release/SKILL.md`'s tail).
- NEVER to promote staging → production. Promotion is CD-owned and operator-gated (see
  `skills/deploy-status/SKILL.md`'s boundary) — this skill stops at the checklist + observation.

## Procedure

1. **Resolve the staging binding.** Read `.claude/foundry-project.json`. **REFUSE** ("no staging
   binding") if it has no `staging` key, or the key is present but missing `base_url`:
   ```json
   {
     "staging": {
       "base_url": "https://staging.example.com",
       "deploy_target": "<name matching an entry in .foundry/deploy-targets.yaml, optional>"
     }
   }
   ```
   This is adopter-config, the same convention `self_host_code_repo`/`repos` already use in this
   file — Foundry ships the checklist + the shape, never a hardcoded staging URL.
2. **Resolve the release manifest** (`scripts/foundry_release.py`'s `load_release`) for its
   description + per-atom `journeys[]` tags — the SAME manifest `/foundry:certify-local` reads.
3. **Observe deploy state** via the kept, unmodified `/foundry:deploy-status` surface
   (`skills/deploy-status/SKILL.md`) against the staging target — sync + health + the
   deployed-artifact identity cross-check. Read-only; never trigger a deploy or sync.
4. **Emit the checklist** — copy `context/staging-checklist-template.md` and fill every
   placeholder from steps 1–3: the staging binding, the deploy-status observation (§1), one row
   per atom naming its journey tags for a staging re-run (§2), rollback readiness (§3), and a
   blank sign-off line (§4) for the operator to complete.
5. **STOP.** Hand the filled checklist to the operator. This skill does not re-run the journey
   suite against staging itself (the app's own Playwright config would need a staging
   project/baseURL binding Foundry does not assume or inject — see the template's §2 note), does
   not record an acceptance verdict on the operator's behalf, and does not promote.

## Inputs / Outputs

- In: `<release-id>`, `.claude/foundry-project.json`'s `staging` binding,
  `.foundry/deploy-targets.yaml` (for the deploy-status observation),
  `context/staging-checklist-template.md`.
- Out: a filled staging checklist (the template above, no placeholders left unresolved) handed
  to the operator; no state is mutated (no promotion, no acceptance record, no manifest write).

## Anti-patterns

- **Promoting staging → production from this skill.** There is no promotion verb here — CD owns
  it, the operator gates it. This skill's job ends at the checklist + the sign-off line the
  operator fills themselves.
- **A vacuous checklist when there is no staging binding.** Refuse, name the missing
  `.claude/foundry-project.json` `staging` key, never emit a checklist with an invented URL.
- **Re-implementing `/foundry:deploy-status`.** Drive it as-is; never re-read ArgoCD/the
  gitops paths directly here.
- **Treating the emitted checklist as the sign-off.** The checklist's §4 sign-off line is the
  operator's own record, filled by them — same practice-not-gate posture as
  `/foundry:release accept` (the constitution's "operator sign-off is the terminal gate" principle, context/constitution-template.md §I.5).
