# cost kit

Rules FG-COST-001..004. Cost is a decision input (001 is a floor; the priced-ask check lives in
`scripts/foundry_blocker_check.py`). Tag enforcement is a conftest rego (`checks/required-tags.rego`). The cost
diff is a PR comment, never a gate (`checks/infracost-comment.sh`). Scale-to-zero: pattern doc and a K8s
template under `templates/`.
