# data-pipeline test strategy

What each test lane proves for this blueprint; the exact commands live in `stack-profile.yaml` (`test_recipe`).

- Unit: transforms as pure functions over small frames.
- Integration: run a pipeline end-to-end over a seeded fixture dataset.
- Data-quality: expectations run as tests (schema, nulls, uniqueness, referential).
- Regression: golden output snapshots for critical transforms.
