# claude-plugin conventions

Layers (dependencies point inward only): scripts, hooks, skills. The allowed edges are the `architecture.allowed_dependencies` of `stack-profile.yaml`; the architecture kit enforces them.

- Tooling is `uv`-driven and locked (`uv sync --locked`); dev-tool pins live in `standing-versions/manifest.yaml`.
- Config and secrets come from the environment, never from code or fixtures.
- Each layer has its own tests; the domain layer performs no I/O.
- See `blueprints/readiness-checklist.md` and `blueprints/test-strategy.md` for the gates this blueprint adds.
