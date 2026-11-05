# claude-plugin conventions

A Claude Code plugin: stdlib-first Python scripts, a Node CLI, hooks and skills. There is no domain layer and no `src/` package; the layers below are the repository's top-level directories. Dependencies point inward only.

## Layers

- **scripts** -- the Python modules and entry points (`scripts/`) that implement behaviour: pure functions first, thin `main()` wrappers last. Stdlib-only where possible; the only third-party imports are the pinned PyYAML and jsonschema. Imports nothing from `hooks` or `skills`.
- **hooks** -- shell/Python hook entry points (`hooks/`) wired in `hooks/hooks.json`. Each reads the event from stdin, calls into `scripts`, and returns a decision. May import `scripts`.
- **skills** -- the model-facing instructions (`skills/*/SKILL.md`, agents, commands). Prose and frontmatter, not code; they may call `scripts` and rely on `hooks`.

Allowed edges, in prose: skills -> scripts, hooks; hooks -> scripts; scripts -> nothing in this repo's other layers.

## Conventions that matter for this type

- **Hooks fail closed.** A malformed event, a missing dependency or an internal error blocks (or denies) rather than allows; every hook has a test driving it with fixture stdin.
- **Scripts are stdlib-only** apart from the two pinned libraries; no network access at import time and no hidden state outside `.foundry/`.
- **Permission floor tiering.** Anything that widens what an agent may do is tiered in the permission floor map and needs the operator; a plugin change never loosens the floor to make a check pass.
- **No retired context-engine namespace token** anywhere in shipped files (a grep gate enforces it), and no private names or secrets.
- Behavioural assertions live in `tests/`; the doctor stays a fixed thin probe.

Pins live in `standing-versions/manifest.yaml`.
