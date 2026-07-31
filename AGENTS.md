# AGENTS.md — orientation for coding agents working in this repo

This repo is a **Claude Code plugin** (`foundry`): skills in `skills/*/SKILL.md`, personas
in `agents/`, hooks in `hooks/` (wired by `hooks/hooks.json`), Python tooling in `scripts/`,
tests in `tests/`, templates in `context/`, stack profiles in `packs/`.

## Ground rules

- **Run the tests**: `python3 -m pytest tests/ -q` (full suite, ~1 min; the certify-fixture
  module boots local servers) and `python3 scripts/foundry-doctor.py` (must print
  `DOCTOR-GREEN`). Both must be green before any PR.
- **Never weaken an assertion to make a test pass.** Fix the code or flag the test.
- **No claim beyond shipped enforcement**: docs and skill prose must never describe
  guarantees the code doesn't provide. When you change behavior, change the prose in the
  same PR.
- **Skill frontmatter is load-bearing**: every `skills/*/SKILL.md` must keep YAML-parseable
  frontmatter with `name` and `description`; the doctor checks all of them.
- **Versions are pinned**: GitHub Actions by 40-char SHA; dependency changes go through the
  standing-versions discipline — don't float anything.
- **Git discipline**: branch per change, PR-then-merge, no force-push to `main`. The
  repo's own PreToolUse hook will refuse `gh pr merge --admin` and any merge ahead of
  green checks — that's by design, not a bug to work around.
- **PR trailers**: code-change PRs carry a `Spec:` trailer naming the authorizing spec
  (the `spec-link` CI gate checks it; docs-only diffs are exempt).

## Where to learn more

[README](README.md) → [docs/QUICKSTART.md](docs/QUICKSTART.md) →
[docs/architecture.md](docs/architecture.md) → [docs/glossary.md](docs/glossary.md).
The changelog is the authoritative history.
