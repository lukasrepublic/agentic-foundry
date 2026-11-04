# conftest policy for FG-COST-002. Run on a Terraform plan:
#   terraform show -json plan.tfplan > plan.json && conftest test plan.json -p governance/kits/cost/checks
# A resource is taggable when its schema has a `tags` or `tags_all` attribute, i.e. the key is present in
# `after` or `after_unknown`. A null tags value counts as no tags; a non-map value is denied; an empty-string tag
# value does not count. Data sources are skipped.
# Scope: AWS-style `tags` / `tags_all` only. GCP and Azure labels are out of scope.
package main

import rego.v1

required_tags := {"owner", "environment", "cost-center"}

has_tag_attr(rc) if "tags" in object.keys(rc.change.after)

has_tag_attr(rc) if "tags_all" in object.keys(rc.change.after)

has_tag_attr(rc) if "tags" in object.keys(object.get(rc.change, "after_unknown", {}))

has_tag_attr(rc) if "tags_all" in object.keys(object.get(rc.change, "after_unknown", {}))

raw(rc, k) := object.get(rc.change.after, k, null)

# Tag keys set to a non-empty string in tags or tags_all (provider default_tags land in tags_all).
tag_keys(rc) := {k |
	some attr in {"tags", "tags_all"}
	v := raw(rc, attr)
	is_object(v)
	some k, val in v
	is_string(val)
	val != ""
}

bad_type(rc) if {
	some attr in {"tags", "tags_all"}
	v := raw(rc, attr)
	v != null
	not is_object(v)
}

deployable(rc) if {
	object.get(rc, "mode", "managed") != "data"
	some action in rc.change.actions
	action in {"create", "update"}
	rc.change.after != null
	has_tag_attr(rc)
}

deny contains msg if {
	some rc in input.resource_changes
	deployable(rc)
	bad_type(rc)
	msg := sprintf("FG-COST-002: %s has tags that are not a map", [rc.address])
}

deny contains msg if {
	some rc in input.resource_changes
	deployable(rc)
	missing := required_tags - tag_keys(rc)
	count(missing) > 0
	msg := sprintf("FG-COST-002: %s is missing cost tags: %v", [rc.address, sort(missing)])
}
