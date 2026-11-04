# Weaver registry policy for FG-OBS-001: every custom attribute defined in this registry MUST sit under the owner
# prefix. Attributes taken from upstream semantic conventions are referenced with `ref`, not defined with `id`.
# Weaver runs this before resolution. Verify the violation shape against the pinned weaver version.
package before_resolution

import rego.v1

owner_prefix := "example" # replace with the owning team prefix

deny contains violation if {
	some group in input.groups
	some attr in group.attributes
	attr.id
	not startswith(attr.id, concat("", [owner_prefix, "."]))
	violation := {
		"id": "FG-OBS-001",
		"type": "semconv_attribute",
		"category": "custom_name_without_owner_prefix",
		"attr": attr.id,
	}
}
