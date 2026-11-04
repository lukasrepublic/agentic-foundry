# Version pinning policy (conftest / OPA). Enforces the SHAPE of a pin, not the number: the pinned
# values live in the project's version matrix and lock, which carry the research timestamp.
#
# Three drift surfaces:
#   (1) container images in workload manifests: an @sha256 digest or an exact 3-component version tag.
#   (2) Argo CD Application Helm targetRevision: an exact chart version, never a range, branch or latest.
#   (3) GitHub Actions `uses:` (steps and reusable workflows): a full 40-hex commit SHA, never a floating tag or branch.
#
# Every rule names a set of compliant subjects and a `deny` per non-compliant subject, so conftest fails
# on any violator regardless of how many subjects pass. Input: one document or an array of documents.
package governance.pinning

import rego.v1

_docs contains d if {
	is_object(input)
	not input.contents
	d := input
}

_docs contains d if {
	is_array(input)
	some item in input
	is_object(item)
	not item.contents
	d := item
}

_docs contains d if {
	is_array(input)
	some w in input
	is_object(w)
	is_object(w.contents)
	d := w.contents
}

_docs contains d if {
	is_array(input)
	some w in input
	is_object(w)
	is_array(w.contents)
	some item in w.contents
	d := item
}

is_digest_pinned(ref) if regex.match(`@sha256:[0-9a-f]{64}$`, ref)

# The tag is the segment after the last ":" that follows the last "/" (guards a host:port colon).
_tag(ref) := tag if {
	not is_digest_pinned(ref)
	parts := split(ref, "/")
	last := parts[count(parts) - 1]
	contains(last, ":")
	tp := split(last, ":")
	tag := tp[count(tp) - 1]
}

_tag(ref) := "" if {
	not is_digest_pinned(ref)
	parts := split(ref, "/")
	last := parts[count(parts) - 1]
	not contains(last, ":")
}

# An exact three-component version. Truncated rolling tags ("1", "1.27") and mutable aliases
# ("latest", "stable", "main") do not match. A non-semver immutable tag must pin by digest.
_semver_re := `^v?[0-9]+\.[0-9]+\.[0-9]+([._-].+)?$`

is_pinned_form(ref) if is_digest_pinned(ref)

is_pinned_form(ref) if {
	not is_digest_pinned(ref)
	tag := _tag(ref)
	tag != ""
	regex.match(_semver_re, tag)
}

# (1) Container images.
_container_images contains {"workload": name, "container": c.name, "image": c.image} if {
	some d in _docs
	d.kind in {"Deployment", "DaemonSet", "StatefulSet", "Job", "CronJob", "Pod", "ReplicaSet"}
	name := object.get(object.get(d, "metadata", {}), "name", "<unnamed>")
	some c in _pod_containers(d)
	c.image
}

_all_containers(spec) := array.concat(
	array.concat(object.get(spec, "containers", []), object.get(spec, "initContainers", [])),
	object.get(spec, "ephemeralContainers", []),
)

_pod_containers(d) := _all_containers(d.spec) if d.kind == "Pod"

_pod_containers(d) := _all_containers(tspec) if {
	d.kind in {"Deployment", "DaemonSet", "StatefulSet", "Job", "ReplicaSet"}
	tspec := object.get(object.get(d.spec, "template", {}), "spec", {})
}

_pod_containers(d) := _all_containers(tspec) if {
	d.kind == "CronJob"
	tspec := object.get(object.get(object.get(object.get(d.spec, "jobTemplate", {}), "spec", {}), "template", {}), "spec", {})
}

image_pinned contains x if {
	some x in _container_images
	is_pinned_form(x.image)
}

deny contains msg if {
	some x in _container_images
	not is_pinned_form(x.image)
	msg := sprintf("version-pinning: %s container %s image %q is not immutably pinned: need an @sha256 digest or an exact MAJOR.MINOR.PATCH tag (no :latest, floating alias or truncated tag).", [x.workload, x.container, x.image])
}

# (2) Helm targetRevision.
_helm_sources contains {"app": name, "chart": s.chart, "rev": rev} if {
	some d in _docs
	d.kind == "Application"
	name := object.get(object.get(d, "metadata", {}), "name", "<unnamed>")
	some s in _app_sources(d)
	object.get(s, "chart", "") != ""
	rev := object.get(s, "targetRevision", "")
}

_app_sources(d) := ss if {
	ss := object.get(d.spec, "sources", [])
	count(ss) > 0
}

# An absent or empty `sources` falls back to the single `source`.
_app_sources(d) := [d.spec.source] if {
	count(object.get(d.spec, "sources", [])) == 0
	object.get(d.spec, "source", false)
}

helm_rev_pinned contains x if {
	some x in _helm_sources
	regex.match(_semver_re, x.rev)
}

deny contains msg if {
	some x in _helm_sources
	not regex.match(_semver_re, x.rev)
	msg := sprintf("version-pinning: Application %s Helm source (chart %q) targetRevision %q is not an exact pin: use a 3-component chart version (a missing or empty targetRevision is denied; no range, :latest or branch).", [x.app, x.chart, x.rev])
}

# (3) GitHub Actions step `uses:` and job-level reusable-workflow `uses:`. Local actions (`uses: ./...`) have no external ref to pin.
_sha40_re := `@[0-9a-f]{40}$`

_workflow_uses contains {"job": jobname, "uses": step.uses} if {
	some d in _docs
	is_object(object.get(d, "jobs", false))
	some jobname, job in d.jobs
	some step in object.get(job, "steps", [])
	object.get(step, "uses", "") != ""
	not startswith(step.uses, "./")
}

# Job-level reusable workflows: jobs.<id>.uses.
_workflow_uses contains {"job": jobname, "uses": job.uses} if {
	some d in _docs
	is_object(object.get(d, "jobs", false))
	some jobname, job in d.jobs
	object.get(job, "uses", "") != ""
	not startswith(job.uses, "./")
}

action_pinned contains x if {
	some x in _workflow_uses
	regex.match(_sha40_re, x.uses)
}

deny contains msg if {
	some x in _workflow_uses
	not regex.match(_sha40_re, x.uses)
	msg := sprintf("version-pinning: workflow job %q step `uses: %s` is not SHA-pinned: pin the action to a full 40-char commit SHA (a floating tag can be re-pointed).", [x.job, x.uses])
}
