# conftest policy for FG-COST-002. Run on a Terraform plan:
#   terraform show -json plan.tfplan > plan.json && conftest test plan.json -p governance/kits/cost/checks
# A resource is taggable when its schema has a `tags` or `tags_all` attribute, i.e. the key is present in
# `after` or `after_unknown`. A null tags value counts as no tags. Data sources are skipped.
package main

import rego.v1

required_tags := {"owner", "environment", "cost-center"}

has_tag_attr(rc) if "tags" in object.keys(rc.change.after)

has_tag_attr(rc) if "tags_all" in object.keys(rc.change.after)

has_tag_attr(rc) if "tags" in object.keys(object.get(rc.change, "after_unknown", {}))

has_tag_attr(rc) if "tags_all" in object.keys(object.get(rc.change, "after_unknown", {}))

# Keys set on the resource: tags and tags_all (null treated as {}). Provider default_tags land in tags_all.
tag_keys(rc) := keys if {
	t := object.get(rc.change.after, "tags", {})
	ta := object.get(rc.change.after, "tags_all", {})
	keys := {k | some k, _ in object.union(nonnull(t), nonnull(ta))}
}

nonnull(x) := {} if x == null

nonnull(x) := x if x != null

deny contains msg if {
	some rc in input.resource_changes
	object.get(rc, "mode", "managed") != "data"
	some action in rc.change.actions
	action in {"create", "update"}
	rc.change.after != null
	has_tag_attr(rc)
	missing := required_tags - tag_keys(rc)
	count(missing) > 0
	msg := sprintf("FG-COST-002: %s is missing cost tags: %v", [rc.address, sort(missing)])
}
