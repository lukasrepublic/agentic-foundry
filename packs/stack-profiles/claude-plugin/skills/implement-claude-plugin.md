# Implement under claude-plugin

Load `conventions.md` first. Find the layer the contract names (scripts, hooks or skills) and keep dependencies pointing inward.

1. **scripts**: write the pure function and its pytest test first; keep `main()` thin; stdlib-only apart from PyYAML and jsonschema.
2. **hooks**: parse the event defensively, fail closed on anything unexpected, and add a test that drives the hook with fixture stdin for both an allow and a deny case.
3. **skills**: keep frontmatter and trigger description accurate and the scope bounded; if the skill needs new behaviour, add it in `scripts` rather than in prose.
4. Anything touching the permission floor or a gate is operator-tiered; never relax one to make a check pass.

Run `python3 -m pytest tests/ -q`, `python3 scripts/foundry-doctor.py`, `node --test cli/test/` (when the CLI is touched) and `claude plugin validate .`, then tick the touched items in `blueprints/readiness-checklist.md`. Never edit `standing-versions/` pins inside an atom.
