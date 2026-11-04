# aws-eks-karpenter readiness checklist

Gate before a change built under this blueprint is declared production-ready. The `governance` block of `stack-profile.yaml` selects the kits and levels that enforce it.

- [ ] Every apply goes through a saved plan; blast-radius tier reviewed before apply.
- [ ] No long-lived AWS keys: IRSA / Pod Identity, least-privilege IAM.
- [ ] Policy-as-code (conftest/OPA) gates the plan and rendered manifests.
- [ ] Karpenter NodePool limits and disruption budgets set; consolidation reviewed.
- [ ] Runbook for HIGH blast-radius changes and a tested rollback path.
- [ ] Cost guardrails and tagging in place.
