# Enforces FG-SEC-031. Run: conftest test -p governance/kits/security/checks/policy <manifest files>
package main

import rego.v1

workload_kinds := {"Pod", "Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"}

pod_spec := input.spec.template.spec if input.kind in {"Deployment", "StatefulSet", "DaemonSet", "Job"}

pod_spec := input.spec if input.kind == "Pod"

pod_spec := input.spec.jobTemplate.spec.template.spec if input.kind == "CronJob"

deny contains msg if {
	some c in pod_spec.containers
	not contains(c.image, "@sha256:")
	msg := sprintf("FG-SEC-031: container %s image is not pinned to a digest", [c.name])
}

deny contains msg if {
	some c in pod_spec.containers
	not c.securityContext
	msg := sprintf("FG-SEC-031: container %s has no securityContext", [c.name])
}
