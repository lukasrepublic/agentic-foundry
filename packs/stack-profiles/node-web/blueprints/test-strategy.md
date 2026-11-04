# node-web test strategy

What each test lane proves for this blueprint; the exact commands live in `stack-profile.yaml` (`test_recipe`).

- Unit: vitest over the domain and data layers.
- Integration: vitest against api routes with a real datastore.
- E2E: Playwright over the primary journeys against the booted app.
- Visual/a11y: automated accessibility scan in the e2e lane.
