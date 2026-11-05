# Scale to zero for occasional privileged jobs

Operating-model rule C3. A rare privileged job, such as a release signer, runs on its own node class that is tainted and
scales to zero when idle. It does not share a node with the general build runners.

The reason is blast radius and cost. A signer that shares a node with build runners shares its kernel, its disk and its
network with code that anyone can open a pull request against. A node class that exists only while the job runs also costs
nothing while it is idle.

## The pattern and the template

The pattern is in `governance/kits/cost/templates/scale-to-zero-pattern.md`. Read it first; this page does not copy it.
The template is `governance/kits/cost/templates/scale-to-zero-nodeclass.yaml` (Karpenter node pools and one node class).
Adapt the names and keep the taints and the empty-node removal. The kit's rules are in `governance/kits/cost/rules.yaml`,
and the cost rule that asks for a priced ask is `FG-COST-001`.

## What to do

1. Give the privileged job its own node class. Give non-production workloads another. They never share one.
2. Taint each class, and let only the owning workload tolerate its taint.
3. Add an admission policy. A taint does not restrict access: anyone who can create a pod can add a toleration. The
   template `governance/kits/cost/templates/restrict-privileged-toleration.kyverno.yaml` limits the toleration to the
   signer namespace and service account. Its test cases are in `governance/kits/cost/templates/kyverno-tests/`.
4. Make namespace access control the real boundary. Anyone who can create pods in the signer namespace can name the signer
   service account, so grant that right to nobody else.
5. Scale to zero by removing empty nodes after a short delay. There is no minimum size to keep at zero.
6. Price the idle state in a priced ask, and state the cold start: the first job after an idle period waits for a node.

## What is enforced

Nothing here is a machine gate. The pattern is a document and a template, and the rule is applied in review. The policy
test for the admission template runs when `kyverno` is installed; the plugin CI installs none of the policy tools, so those
tests skip there and are proven only on a machine that has them.

The pin rules for the Karpenter version and the machine image follow the stack kit
(`governance/kits/stack/templates/version-matrix.md`). Do not take a version from this page.
