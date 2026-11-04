# claude-plugin test strategy

What each test lane proves for this blueprint; the exact commands live in `stack-profile.yaml` (`test_recipe`, `static_validation`).

- Unit: `python3 -m pytest tests/ -q` over scripts, hooks and skill contracts.
- Integration: `python3 scripts/foundry-doctor.py` -- the fixed thin probe; behavioural assertions belong in `tests/`, never in the doctor.
- E2E: `node --test cli/test/` for the node CLI (install/update orchestration).
- Build: `claude plugin validate .` for the manifest, hooks and skills.
- Hook tests drive the hook binary with fixture stdin and assert the decision (fail closed on malformed input).
- Dogfood: the plugin is detected and governed by this very blueprint (`tests/test_blueprints.py`).
