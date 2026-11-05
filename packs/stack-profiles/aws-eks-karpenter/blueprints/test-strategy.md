# aws-eks-karpenter test strategy

What each test lane proves for this blueprint; the exact commands live in `stack-profile.yaml` (`test_recipe`).

- Static: `tofu validate`, `tofu fmt -check`, conftest over plan and rendered manifests.
- Policy: rego unit tests with should-flag / should-not-flag fixtures.
- Plan review: saved-plan diff classified by blast radius.
- Post-apply: read-only verify slot (kubectl dry-run, argocd app get).
