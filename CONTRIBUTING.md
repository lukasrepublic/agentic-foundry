# Contributing to Agentic Foundry

Thanks for helping build a trustworthy LLM code factory. Contributions are welcome —
issues, discussions, and PRs.

## The non-negotiable floor

These invariants are what make Foundry trustworthy; a PR that weakens them will not be
merged without a design change that justifies it:

1. **The pytest suite + doctor stay green.** One real pytest suite (`tests/`) carries the
   behavioral assertions; `scripts/foundry-doctor.py` is a thin probe (manifest/hooks/
   skills-frontmatter/stack-profile-lock/operator-registry) and is never the place a new
   assertion goes. Run before you push:
   ```bash
   pip install -r requirements-dev.txt
   python3 -m pytest tests/ -q
   python3 scripts/foundry-acceptance-contract-validate.py --selftest
   python3 scripts/foundry-build-citation-graph.py --selftest
   python3 scripts/foundry-graph-mcp.py --selftest
   python3 scripts/foundry-doctor.py            # DOCTOR-GREEN
   node --check workflows/*.js
   ```
   See "Testing" below for where a new behavioral test belongs.
2. **Fail-closed by default.** Gates block on missing/ambiguous/INDETERMINATE input;
   they never "skip to pass". New checks must default to BLOCK.
3. **Front-authorization is unconditional** — no skip path in the factory flow. Review
   skips are operator-only and recorded.
4. **No claim beyond shipped enforcement.** Docs and skill prose never describe a
   guarantee the code doesn't provide; the merge floor's tier labeling
   (docs/merge-floor.md) is honest by construction — keep it that way.
5. **Native-primitive-first.** Before adding a `[foundry]` primitive, check whether a
   native Claude Code primitive already does it — adopt it with a thin seam; don't
   reinvent. New skills carry a `Prompt grammar` section and a frontmatter `name` +
   `description`.

## Testing

Real tests live in `tests/` (pytest, pinned in `requirements-dev.txt`). A new atom that ships
load-bearing behavior in a `scripts/foundry_*.py` module gets a `tests/test_<module>.py` (or a
test class added to the closest existing one) that imports the module directly and asserts on
its computed output over `tmp_path` fixtures — never a standalone `--selftest` CLI dropped
elsewhere (one hand-rolled test harness per atom, re-discovered by a registry, is the
anti-pattern this rule exists to keep out).
`scripts/foundry-doctor.py` stays a thin, fixed-checklist probe; it is never the place a new
behavioral assertion goes. A shipped script that already carries its own hermetic `--selftest`
(e.g. `foundry-decommission.py`, `foundry-bootstrap.sh`, the `hooks/*.sh` selftests)
keeps it — `tests/` may wrap it with a subprocess call rather than re-implementing it.

## Workflow

- Branch, implement, **add/extend a test** for the behavior (see Testing above), run the floor above.
- For security-relevant changes (the git-discipline hook, CI gate workflows,
  authorization, provenance), an **independent review** (a fresh reviewer, not the
  author) is expected — self-review has repeatedly missed real bugs here.
- Open a PR with a clear description + the test output; code-change PRs carry a `Spec:`
  trailer naming the authorizing spec. CI runs the floor.
- **Dogfood rule: features to Foundry go through Foundry** — non-trivial changes get a
  spec + review + authorization through the tool itself. The git history doubling as a
  worked example is a feature.
- **AI-generated code is welcome** when it arrives tested, reviewed by you, and with the
  model credited in the PR description — the same bar as any other code, stated openly.

## Releasing (version discipline)

`claude plugin update` is **version-keyed**: adopters receive a change only when the
`version` in `.claude-plugin/plugin.json` is bumped. A fix left under a perpetual
"Unreleased" is invisible to every existing install — they stay pinned to whatever
snapshot they first installed (this is how a fresh adopter once came up `DOCTOR-RED` on a
pre-fix snapshot). So:

- **Bump `.claude-plugin/plugin.json` `version` (SemVer) on every meaningful change**, and
  land the notes under a dated `## vX.Y.Z` section in `CHANGELOG.md` (not a standing
  "Unreleased"). Patch for fixes, minor for additive features.
- After releasing, adopters pull via `claude plugin update foundry@<marketplace>` (or
  uninstall + reinstall if the cache is wedged).

## Traceability ids in skill prose

Skill/agent prose carries parenthetical anchors like `(feat-foundry-…, AC-XXX-n)` — the spec
atom + acceptance-criteria ids that authorized that behavior. Foundry is self-hosted, so the
plugin's own authorizing specs live in the maintainer's private workspace and do not ship
here; the ids are provenance stamps, not resolvable links (see docs/glossary.md). Keep them
when editing — they are how a change is traced back to its authorization.

## Scope

Foundry is the generic framework; your project is the example. Never bake a
project-specific name into a primitive — Foundry owns the `FOUNDRY_*` env namespace and
adopters map their own vars onto it.

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). Be excellent to
each other.
