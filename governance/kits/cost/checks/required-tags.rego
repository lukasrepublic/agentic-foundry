# conftest policy for FG-COST-002. Run on a Terraform plan:
#   terraform show -json plan.tfplan > plan.json && conftest test plan.json -p governance/kits/cost/checks
package main

import rego.v1

required_tags := {"owner", "environment", "cost-center"}

# Types that do not accept tags are skipped; extend via the adopter layer.
untaggable := {"aws_iam_policy_document", "random_id", "random_password", "null_resource"}

deny contains msg if {
	some rc in input.resource_changes
	rc.change.actions[_] in {"create", "update"}
	not rc.type in untaggable
	tags := object.get(rc.change.after, "tags_all", object.get(rc.change.after, "tags", {}))
	tags != null
	missing := required_tags - {k | some k, _ in tags}
	count(missing) > 0
	msg := sprintf("FG-COST-002: %s is missing cost tags: %v", [rc.address, sort(missing)])
}
