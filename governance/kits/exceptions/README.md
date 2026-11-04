# exceptions kit

Deviations, baselines and amendments. Rules: `rules.yaml` (FG-EXC-001..004). Adopter exceptions live
in `governance/local/exceptions.yaml`; `scripts/foundry_governance.py exceptions` lists and validates
them, and `check` fails on an expired one. Baselines live in `governance/local/baselines/`.
Shipped check snippets go in `checks/` (none yet).
