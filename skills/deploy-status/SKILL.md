---
name: deploy-status
description: Observe-only deploy status (/foundry:deploy-status). Deploy is CD-owned (ArgoCD App-of-Apps, GitOps); Foundry OBSERVES sync + health AND cross-checks deployed-artifact identity vs the expected merged commit (STALE/NOT-ROLLED), it does not push deploys. Production deploy is operator-gated under BOTH modes. Trigger to check what's deployed / sync+health / whether the merged commit actually rolled, never to trigger a deploy.
---

# /foundry:deploy-status

Observe-only. Deploy = **CD** (ArgoCD App-of-Apps, GitOps from the infra
repo); Foundry does NOT own the deploy action — it observes. Production deploy is
**operator-gated under BOTH modes**; there is no autonomous prod-deploy.

## When to trigger

- "deploy status", "/foundry:deploy-status", "what's synced/healthy in `<env>`?".
- NEVER to initiate a deploy (that's the operator + CD).

## Procedure

0. **Cross-check deployed-artifact identity (STALE/NOT-ROLLED).** Before trusting
   sync+health, confirm the *merged commit actually rolled*. A `workflow_run`-gated CD
   pipeline **silently skips** when its upstream CI false-reds: the merged commit never
   builds, gitops is never written back, the old image keeps running — and ArgoCD then reads
   "Synced + Healthy" against the **stale** image. Run the identity check:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/foundry-deploy-status.py" \
     --config "$CLAUDE_PROJECT_DIR/.foundry/deploy-targets.yaml"
   ```
   It compares each target's deployed gitops `image.tag` against the **expected** merged-commit
   identity (the target repo's `main` HEAD / its build-provenance SHA) and reports:
   - **`ROLLED`** — deployed identity == expected, upstream CI `success`.
   - **`STALE/NOT-ROLLED`** — identity mismatch *or* the expected commit's CI was not `success`
     (the gated build skipped). This is reported **independently of** sync/health, so a
     Synced+Healthy stale image can never read GREEN. When the cause is the skipped build it is
     named `upstream-ci-not-success`, distinct from a bare identity drift.
   The expected-SHA→gitops-path mapping is **adopter-config** (`.foundry/deploy-targets.yaml`,
   shape in `deploy-targets.example.yaml`); no product specifics ship in the plugin.
1. **Observe** ArgoCD sync + health for the target env's apps (read-only):
   `argocd app list` / `argocd app get <app>` (or the `gh`/`kubectl` read equivalents).
   Report per-app sync status (Synced/OutOfSync) + health (Healthy/Degraded/Progressing).
2. **Correlate** a merged atom to its rollout (the marker's `model_pin`/`built_at` aid
   incident forensics) — but do NOT mutate cluster state.
3. **Escalate** a `STALE/NOT-ROLLED`, Degraded, or OutOfSync prod app by notifying the
   operator; resolution is forward-fix (re-trigger the build / merge) or
   `/foundry:revert` — never a silent cluster edit.

## Boundary

Infra provisioning is governed via the `<project>-infra` repo pattern; the autonomous
pipeline's deploy role is DISTINCT from (and cannot mutate) the governance/gate-config
lane. Foundry observes; it does not provision or deploy.

## Anti-patterns

- **Triggering or syncing a deploy** — observe-only; deploy is CD + operator-gated. The
  identity checker only reads + compares + reports; it has no deploy-trigger surface.
- **Reading "Synced + Healthy" as "the merged commit is live."** Sync/health describe the
  *running* image; only the step-0 identity cross-check proves the *merged* commit rolled.
- **Hardcoding adopter gitops paths into the plugin** — the expected-SHA→gitops-path mapping
  is adopter-config (`.foundry/deploy-targets.yaml`); the plugin ships the loader + key shape.
- **Mutating cluster state** to "fix" a Degraded/STALE app — route an incident instead.
- **An autonomous prod-deploy path** — prod deploy is operator-gated under both modes.
