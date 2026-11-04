# Implement under data-pipeline

Load `conventions.md` first. Work in the layer the contract names and respect the allowed edges (orchestration -> io, transform, domain).

1. **domain**: define the dataset schema and contract first; test with tiny literal frames.
2. **transform**: write a pure function; add a unit test plus a golden-output snapshot for critical transforms.
3. **io**: read and write by partition only; enforce the schema on both sides; make the write idempotent (overwrite the partition or merge on the natural key).
4. **orchestration**: wire the task, set retries and alerts, and declare lineage, owner and classification.

Add data-quality expectations as tests, run the profile's `static_validation` and `test_recipe` commands, and tick the touched items in `blueprints/readiness-checklist.md`. Never edit `standing-versions/` pins inside an atom.
