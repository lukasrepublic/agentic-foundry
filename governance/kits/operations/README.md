# operations kit

Reliability and change. Rules: `rules.yaml` (FG-OPS-001..008). Floors: production changes announced before and
reported after with a correlation id (FG-OPS-004), and the open-change audit (FG-OPS-005).

The pillar map is a scorecard: a report, not a gate. Snippets: OpenSLO validate, pint. Templates: error-budget
policy and a production-readiness checklist.

## Planned

Shipped later: `scripts/foundry_message_log.py` (gv-09) and `scripts/foundry_prod_step.py` (gv-13). The rollout
lens that FG-OPS-008 names is added to `agents/pr-reviewer.md` by gv-16.
