# Pattern: scale-to-zero for rare privileged jobs (C3)

Run a rare privileged job (for example a release signer) and any non-production workload on a dedicated node
class that scales to zero when idle. Do not share it with general build runners.

1. Taint the node class (`dedicated=privileged-job:NoSchedule`). Only the job tolerates it.
2. Set the node class minimum to zero, with a capacity limit and a short empty-node expiry.
3. Give the job a node selector and a toleration for that class. Nothing else gets them.
4. Price the idle state. At zero nodes the idle cost is the control plane only (FG-COST-001 priced ask).
5. Accept cold start: the first job after idle waits for a node. State this in the priced ask.

Template: `scale-to-zero-nodeclass.yaml` (Karpenter NodePool). Adapt names; keep the taint and the zero floor.
