# aws-eks-karpenter — Karpenter high-blast policy pack (feat-foundry-aws-eks-karpenter-profile v2).
#
# The GATING layer of the profile's policy-as-code: the high-blast Karpenter NodePool risk rules expressed as
# a conftest Rego pack, invoked READ-ONLY via infra_binding.policy
# (`conftest test -o json --policy packs/stack-profiles/aws-eks-karpenter/policy <rendered-manifests>`).
# Operator-curated CONTENT (TRUSTED OPERATOR threat model, memory staged-security-threat-model): the rules are
# git-reviewed, version-controlled risk markers, NOT a defense against a hostile pack-author. Efficacy is
# root-of-trust-bounded (a permissive/no-op rule body greens the structural check) — identical posture to a
# permissive blast_radius rule.
#
# Convention (ADR [Decision: 2026-06-20-v2a-policy-as-code-shapes] C2):
#   - package `main` so conftest's default namespace + the parser's `data.main.deny` / `data.main.warn`
#     queries resolve.
#   - The rule KIND is the gating discriminator (C1 `gating`):
#       * `warn` + `# METADATA custom.severity: high`  ⇒ gating:warn  ⇒ ACKABLE (a frozen {rule,resource} ack
#         clears it) — used for the high-blast NodePool changes the operator MAY legitimately authorize
#         (AMI / amiSelectorTerms, expireAfter, consolidationPolicy/disruption, capacity-type).
#       * `deny`  ⇒ gating:deny ⇒ a HARD, un-ackable FAIL — reserved for a change that must NEVER pass.
#   - EVERY gating rule returns a structured object {msg, severity, resource, rule} (never a bare string);
#     `severity` is `rego.metadata.rule().custom.severity`; `resource` is a NON-EMPTY canonical
#     `<kind>/<namespace>/<name>` (cluster-scoped Karpenter NodePool/EC2NodeClass: `<kind>//<name>`).
#   - The conftest `-o json` output (per-file {warnings[],failures[],…}, each entry {msg, metadata}) is the
#     parse_policy_findings structured contract — metadata carries `rule`/`resource`/`severity`.
#
# The v1 blast_radius advisory tier + the loader's >=1-HIGH invariant STAY untouched (Correction C); the
# Karpenter HIGH risk lives in BOTH tiers (advisory blast_radius + this gating pack).
package main

import rego.v1

# ---------------------------------------------------------------------------------------------------------
# Helpers — identity + Karpenter-kind selection over the rendered manifests conftest feeds as `input`.
# ---------------------------------------------------------------------------------------------------------

# `manifests` — THE shared input-shape normalization helper (feat-foundry-karpenter-policy-pack-inert-input-shape,
# AC-KPP-1/-2). EVERY gating rule below iterates THIS set; none of them touches `input` directly.
#
# THE BUG THIS CLOSES (empirically verified with real conftest + OPA 1.15.2, not asserted): every rule used to
# open with `some obj in input`, which assumes `input` is an ARRAY of manifests. The SHIPPED invocation
# (stack-profile.yaml:117) is `conftest test -o json --policy <pack> <rendered-manifests>` with NO `--combine`,
# and conftest WITHOUT --combine evaluates each YAML document SEPARATELY — so `input` is a single manifest
# OBJECT (`{apiVersion, kind, metadata, spec}`). `some obj in input` therefore iterated that object's VALUES
# (the strings "karpenter.sh/v1" / "NodePool", the metadata/spec sub-objects); none has a `.kind`, so
# `is_nodepool(obj)` was NEVER true and EVERY rule body died at its first expression. The pack emitted nothing
# on every manifest — a textbook vacuous pass: a gate that is green because it is dead. `--combine` is NOT the
# fix (it yields `[{path, contents}]`, whose `.kind` is equally undefined) ⇒ the fix is here, in the traversal.
#
# The four shapes normalized (AC-KPP-2), all yielding the SAME gating results:
#   1. a single manifest OBJECT              — the SHIPPED shape (conftest, per-document, no --combine)
#   2. an ARRAY of manifests                 — e.g. a JSON list input
#   3. a Kubernetes `kind: List` envelope    — {kind: "List", items: [...]}
#   4. conftest `--combine`'s wrapper list   — [{path, contents}], contents = one doc OBJECT or an ARRAY of docs

# The candidate top-level documents carried by `input`, per shape.

# Shape 1 — the SHIPPED shape: `input` IS the manifest document itself.
_input_docs contains d if {
	is_object(input)
	not input.contents # not a --combine wrapper (shape 4 handles those)
	d := input
}

# Shape 2 — an ARRAY of manifest documents.
_input_docs contains d if {
	is_array(input)
	some item in input
	is_object(item)
	not item.contents # not a --combine wrapper (shape 4 handles those)
	d := item
}

# Shape 4 — conftest `--combine`: [{path, contents}] where `contents` is a single document OBJECT.
_input_docs contains d if {
	is_array(input)
	some w in input
	is_object(w)
	is_object(w.contents)
	d := w.contents
}

# Shape 4 — conftest `--combine`: [{path, contents}] where `contents` is an ARRAY of documents (multi-doc file).
_input_docs contains d if {
	is_array(input)
	some w in input
	is_object(w)
	is_array(w.contents)
	some item in w.contents
	is_object(item)
	d := item
}

# Shape 3 — a `kind: List` envelope is EXPANDED to its items; every other document passes through as itself.
# `object.get` keeps a document with no `kind` from silently dropping out of the set.
manifests contains m if {
	some d in _input_docs
	object.get(d, "kind", "") != "List"
	m := d
}

manifests contains m if {
	some d in _input_docs
	object.get(d, "kind", "") == "List"
	some item in object.get(d, "items", [])
	is_object(item)
	m := item
}

# Canonical resource identity `<kind>/<namespace>/<name>`. Karpenter NodePool/EC2NodeClass are
# CLUSTER-SCOPED ⇒ an empty namespace segment (`<kind>//<name>`), per the ADR C2 cluster-scoped form.
resource_id(obj) := id if {
	kind := object.get(obj, "kind", "")
	ns := object.get(object.get(obj, "metadata", {}), "namespace", "")
	name := object.get(object.get(obj, "metadata", {}), "name", "")
	id := sprintf("%s/%s/%s", [kind, ns, name])
}

is_nodepool(obj) if obj.kind == "NodePool"

is_ec2nodeclass(obj) if obj.kind == "EC2NodeClass"

# ---------------------------------------------------------------------------------------------------------
# warn (ackable, severity:high) — the high-blast NodePool changes the operator MAY legitimately authorize.
# Each lands in conftest `warnings[]` ⇒ gating:warn ⇒ ACKABLE via a frozen {rule,resource} policy:high-blast-ack.
# ---------------------------------------------------------------------------------------------------------

# METADATA
# title: NodePool / EC2NodeClass AMI change is high-blast (Karpenter Drift → mass node replacement)
# custom:
#   severity: high
warn contains result if {
	some obj in manifests
	is_ec2nodeclass(obj)
	obj.spec.amiSelectorTerms
	result := {
		"msg": sprintf("EC2NodeClass %q sets amiSelectorTerms — an AMI change drives Karpenter Drift (mass node replacement); requires a disruption-budget/canary roll.", [obj.metadata.name]),
		"severity": rego.metadata.rule().custom.severity,
		"resource": resource_id(obj),
		"rule": "karpenter_ami_change",
	}
}

# METADATA
# title: NodePool expireAfter change is high-blast (forced node expiry → mass replacement)
# custom:
#   severity: high
warn contains result if {
	some obj in manifests
	is_nodepool(obj)
	obj.spec.template.spec.expireAfter
	result := {
		"msg": sprintf("NodePool %q sets spec.template.spec.expireAfter — forced node expiry replaces every node it owns; high-blast.", [obj.metadata.name]),
		"severity": rego.metadata.rule().custom.severity,
		"resource": resource_id(obj),
		"rule": "karpenter_expire_after",
	}
}

# METADATA
# title: NodePool consolidation/disruption change is high-blast (churn policy → node replacement)
# custom:
#   severity: high
warn contains result if {
	some obj in manifests
	is_nodepool(obj)
	obj.spec.disruption.consolidationPolicy
	result := {
		"msg": sprintf("NodePool %q sets spec.disruption.consolidationPolicy — a disruption/consolidation change drives node churn; high-blast.", [obj.metadata.name]),
		"severity": rego.metadata.rule().custom.severity,
		"resource": resource_id(obj),
		"rule": "karpenter_disruption_change",
	}
}

# METADATA
# title: NodePool capacity-type change is high-blast (spot/on-demand flip → mass replacement)
# custom:
#   severity: high
warn contains result if {
	some obj in manifests
	is_nodepool(obj)
	some req in obj.spec.template.spec.requirements
	req.key == "karpenter.sh/capacity-type"
	result := {
		"msg": sprintf("NodePool %q constrains karpenter.sh/capacity-type — a capacity-type (spot/on-demand) flip mass-replaces nodes; high-blast.", [obj.metadata.name]),
		"severity": rego.metadata.rule().custom.severity,
		"resource": resource_id(obj),
		"rule": "karpenter_capacity_type",
	}
}

# METADATA
# title: NodePool percentage disruption budget with no absolute-count ceiling is high-blast (ER #187, corrected ER #87 grounding)
# custom:
#   severity: high
warn contains result if {
	some obj in manifests
	is_nodepool(obj)
	some b in obj.spec.disruption.budgets
	regex.match("^[1-9][0-9]*%$", sprintf("%v", [b.nodes]))
	not _has_absolute_budget_entry(obj)
	result := {
		"msg": sprintf("NodePool %q sets a percentage disruption budget with no absolute-count ceiling — add an absolute companion entry (e.g. {nodes: \"5\"}); Karpenter min-combines budgets so the absolute bounds the blast radius.", [obj.metadata.name]),
		"severity": rego.metadata.rule().custom.severity,
		"resource": resource_id(obj),
		"rule": "karpenter_uncapped_percentage_budget",
	}
}

# IntOrString-safe: `nodes` may be authored as an unquoted int (`nodes: 5`) or a string (`nodes: "5"`); coerce
# via sprintf before matching so either form is recognised as a valid absolute-count ceiling companion.
_has_absolute_budget_entry(obj) if {
	some b in obj.spec.disruption.budgets
	regex.match("^[0-9]+$", sprintf("%v", [b.nodes]))
}

# ---------------------------------------------------------------------------------------------------------
# deny (hard, un-ackable FAIL) — a change that must NEVER pass. NodePool removal of ALL disruption budgets
# (an unbounded mass-replacement with no rollout guard) is a true never-allowed violation, not an ackable risk.
# ---------------------------------------------------------------------------------------------------------

# METADATA
# title: NodePool with consolidation enabled but NO disruption budget is a never-allowed unbounded roll
# custom:
#   severity: high
deny contains result if {
	some obj in manifests
	is_nodepool(obj)
	obj.spec.disruption.consolidationPolicy
	not obj.spec.disruption.budgets
	result := {
		"msg": sprintf("NodePool %q enables consolidation with NO spec.disruption.budgets — an unbounded mass roll with no rollout guard is never permitted.", [obj.metadata.name]),
		"severity": rego.metadata.rule().custom.severity,
		"resource": resource_id(obj),
		"rule": "karpenter_unbounded_disruption",
	}
}
