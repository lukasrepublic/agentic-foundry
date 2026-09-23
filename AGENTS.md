# AGENTS.md — the standing rules for coding agents working in this repo

Claude Code reads this file as the project's instructions when there is no `CLAUDE.md`
(since 2.1.278, 2026-09-18); other agent tools read it by convention. It holds only the
invariant rules of this repo, each verifiable against the tree. Orchestration and
session-specific prompts live elsewhere (`skills/`, the adopter's own workspace).

This repo is a **Claude Code plugin** (`foundry`): skills in `skills/*/SKILL.md`, personas
in `agents/`, hooks in `hooks/` (wired by `hooks/hooks.json`), Python tooling in `scripts/`,
tests in `tests/`, templates in `context/`, stack profiles in `packs/`.

## Ground rules

- **Run the tests**: `python3 -m pytest tests/ -q` (full suite, ~10 min measured 2026-09-21;
  the certify-fixture module boots a local `http.server`) with the pinned dev requirements
  (`requirements-dev.txt`; CI uses Python 3.12), and `python3 scripts/foundry-doctor.py`
  (must print `DOCTOR-GREEN` — run from this repo as the session root; run from a hosting
  workspace it reports the control-plane RED by design). Both must be green locally before
  the first push of a branch.
- **The hook's executable content is digest-pinned** in
  `tests/fixtures/release/cut-release-reconcile-sweep.yaml`; a deliberate change to
  `hooks/foundry-git-discipline.sh` advances the pin with a numbered ledger entry there.
- **Never weaken an assertion to make a test pass.** Fix the code or flag the test.
- **No claim beyond shipped enforcement**: docs and skill prose must never describe
  guarantees the code doesn't provide. When you change behavior, change the prose in the
  same PR.
- **Skill frontmatter is load-bearing**: every `skills/*/SKILL.md` must keep YAML-parseable
  frontmatter with `name` and `description`; the doctor checks all of them.
- **Versions are pinned**: GitHub Actions by 40-char SHA; dependency changes go through the
  standing-versions discipline — don't float anything.
- **Git discipline**: branch per change, PR-then-merge, no force-push to `main`. The repo's
  own PreToolUse hook (`hooks/foundry-git-discipline.sh`) refuses force-pushes to a protected
  branch, `gh pr merge --admin`, and any merge ahead of green checks — by design, not a bug
  to work around. Atoms PR into `release/<version>`; `main` receives one PR per release;
  `hotfix/<id>` → `main` is the one exception. See `docs/how-to/branching-and-cleanup.md`.
- **Text that names a guarded command goes through Write/Edit, never a Bash heredoc.** The
  hook scans the whole command string, heredoc bodies included, so `cat > f <<EOF` with a
  body mentioning a force-push or `--admin` is refused (docs, tests, commit bodies, PR
  bodies). An inline `-m "…"` is fine. See `docs/merge-floor.md`.
- **The retired-framework token fails CI.** `ci.yml` greps the whole tree, case-insensitive,
  for the three-letter name of the retired environment tool (the one that starts with `c`
  and ends with `x`); any occurrence in any file — code, comment, doc, fixture — is red.
- **PR trailers**: code-change PRs carry a `Spec:` trailer naming the authorizing spec (the
  `spec-link-base` gate in `.github/workflows/btb-gates-base.yml` checks it; docs-only diffs
  are exempt). Security-relevant changes (this hook, CI gate workflows, authorization,
  provenance) get an independent reviewer — self-review has missed real bugs here.

## Where to learn more

[README](README.md) → [docs/QUICKSTART.md](docs/QUICKSTART.md) →
[docs/architecture.md](docs/architecture.md) → [docs/glossary.md](docs/glossary.md) →
[CONTRIBUTING.md](CONTRIBUTING.md). The changelog is the authoritative history.
