# quality kit

Review, tests and done. Rules: `rules.yaml` (FG-QA-001..006). Floors: one separate-context review per PR
(FG-QA-001) and the Done-means re-run on merged main (FG-QA-006).

Check snippets in `checks/`: the quarantine record schema and its checker
(`python3 quarantine_check.py <file> [--today YYYY-MM-DD]`; exit 1 on a missing field or an expired entry),
a review checklist, and a coverage CI snippet.
