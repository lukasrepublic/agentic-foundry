# Pattern: scale-to-zero for rare privileged jobs and non-prod (C3)

Run a rare privileged job (for example a release signer) and non-production workloads on dedicated node classes
that scale to zero when idle. Do not share them with general build runners.

1. Separate classes. The privileged job and non-prod each get their OWN node class; they never share one.
2. Taint each class (`dedicated=privileged-job:NoSchedule`, `dedicated=non-prod:NoSchedule`). Only the owning
   workload tolerates its taint.
3. A taint does not restrict access: anyone who can create a pod can add a toleration. An admission policy
   (Kyverno, Gatekeeper or a ValidatingAdmissionPolicy) MUST limit the privileged toleration to the signer
   namespace and service account. Template: `restrict-privileged-toleration.kyverno.yaml`.
4. Scale to zero means no nodes exist when idle: the pool sets a capacity limit and removes empty nodes after a
   short delay (`consolidationPolicy: WhenEmpty`). There is no minimum-size setting to keep at zero.
5. Price the idle state. With no nodes, the idle cost is the control plane only (FG-COST-001 priced ask).
6. Accept cold start: the first job after idle waits for a node. State this in the priced ask.

Template: `scale-to-zero-nodeclass.yaml` (Karpenter NodePools plus the EC2NodeClass). Adapt names; keep the taints
and the empty-node removal. Pin the Karpenter version and the AMI per the stack kit's version-matrix template
(`governance/kits/stack/templates/version-matrix.md`).
