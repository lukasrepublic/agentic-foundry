# Staging certification checklist — `<release-id>`

<!-- Emitted by `/foundry:certify-staging <release>`
(`skills/certify-staging/SKILL.md`), filled from the release manifest + the `staging` binding in
`.claude/foundry-project.json` + a `/foundry:deploy-status` observation. This is a CHECKLIST the
operator works through against the real staging environment — it is NOT re-executed automatically
end-to-end, and it STOPS before promotion: promotion itself stays CD-owned and operator-gated
(the constitution's "operator sign-off is the terminal gate" principle, §I.5). Fill every `<…>`
placeholder from live data; never hand this template to the operator with placeholders unresolved. -->

- **Release:** `<release-id>` — `<release description, from release.yaml>`
- **Staging binding:** `<staging.base_url from .claude/foundry-project.json>`
  (deploy target: `<staging.deploy_target, if declared>`)
- **Certify-local evidence:** `<link/reference to the certify-local run this checklist follows —
  date, verdict, operator, per CONSTITUTION.md §V's "certify locally -> operator acceptance ->
  staging" ordering>`

## 1. Deploy observation (`/foundry:deploy-status`)

Run the kept deploy-status surface (`skills/deploy-status/SKILL.md`) against the staging target —
observe-only, never a trigger:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-deploy-status.py" \
  --config "$CLAUDE_PROJECT_DIR/.foundry/deploy-targets.yaml"
```

- Sync status: `<Synced | OutOfSync>`
- Health: `<Healthy | Degraded | Progressing>`
- Deployed-artifact identity vs expected merged commit: `<ROLLED | STALE/NOT-ROLLED, cause if any>`

A `STALE/NOT-ROLLED`, `Degraded`, or `OutOfSync` staging app is a checklist BLOCKER — route it as
an incident (per `deploy-status`'s Anti-patterns) rather than proceeding past this section.

## 2. Journeys re-run against staging baseURL

Every atom's tagged journey from the release manifest, to be exercised against
`<staging.base_url>` (manually, or via the app's own `npx playwright test --grep <tag> --project
<staging-project>` if the app's Playwright config carries a staging project/baseURL — Foundry
does not assume or inject one; that binding is the implementer's own, same as `app_exercise_binding`
locally):

| Atom | Journey tag(s) | Result on staging | Notes |
|---|---|---|---|
| `<atom-id>` | `<tag, ...>` | `<pass / fail / not run>` | `<...>` |

(repeat one row per atom in the release manifest's `atoms[]`, in manifest order)

## 3. Rollback readiness

- Rollback path confirmed (`/foundry:revert` or the CD pipeline's own rollback): `<yes/no + how>`
- Prior known-good staging deploy identity recorded: `<commit/tag>`
- Blast radius of THIS release if it must be rolled back: `<low/medium/high — why>`

## 4. Sign-off line

> Certified on staging: `<date>`, by `<operator>`. Verdict: `<accepted | rejected>`.
> `<one-line note>`

This checklist STOPS here. Promotion beyond staging is CD-owned and operator-gated — this skill
never triggers it (see `skills/certify-staging/SKILL.md`'s Anti-patterns).
