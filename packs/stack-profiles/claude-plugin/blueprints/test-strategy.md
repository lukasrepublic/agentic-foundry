# claude-plugin test strategy

What each test lane proves for this blueprint; the exact commands live in `stack-profile.yaml` (`test_recipe`).

- pytest over scripts, hooks and skill contracts (`python3 -m pytest tests/ -q`).
- Doctor: the fixed thin probe is the integration gate; behavioural assertions live in tests.
- Hook tests drive the hook binary with fixture stdin and assert the decision.
- Dogfood: the plugin is detected and governed by this very blueprint.
