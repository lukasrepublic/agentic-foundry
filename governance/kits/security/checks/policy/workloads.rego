# Enforces FG-SEC-031. Run: conftest test -p governance/kits/security/checks/policy <manifest files>
# Every container (containers, initContainers, ephemeralContainers) must pin its image by digest and set
# securityContext.runAsNonRoot to true at container level. An empty securityContext is denied.
package main

import rego.v1

pod_spec := input.spec.template.spec if input.kind in {"Deployment", "StatefulSet", "DaemonSet", "Job", "ReplicaSet"}

pod_spec := input.spec if input.kind == "Pod"

pod_spec := input.spec.jobTemplate.spec.template.spec if input.kind == "CronJob"

all_containers contains c if some c in pod_spec.containers

all_containers contains c if some c in pod_spec.initContainers

all_containers contains c if some c in pod_spec.ephemeralContainers

deny contains msg if {
	some c in all_containers
	not contains(c.image, "@sha256:")
	msg := sprintf("FG-SEC-031: container %s image is not pinned to a digest", [c.name])
}

deny contains msg if {
	some c in all_containers
	not c.securityContext.runAsNonRoot == true
	msg := sprintf("FG-SEC-031: container %s must set securityContext.runAsNonRoot to true", [c.name])
}
