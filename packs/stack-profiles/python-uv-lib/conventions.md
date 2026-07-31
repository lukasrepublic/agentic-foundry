# python-uv-lib — project shape, pin discipline, and the no-runtime-surface claim

The plain Python library stack profile (`id: python-uv-lib`), consensus mid-2026 Python tooling per
the spec's "Prior art / industry grounding": **`uv`** for the environment, dependency, and
interpreter management; **`ruff`** for format + lint; **`mypy`** for typecheck; **`pytest`** +
**`pytest-cov`** for the test recipe. The same `uv`-first battery as its `python-uv-service`
sibling — a library needs the identical static-validation + test discipline, it simply has nothing
to boot.

> **Threat model — TRUSTED OPERATOR.** This profile is operator-curated machinery, authorized at
> the normal `/foundry:authorize` gate. These conventions are an **advisory mistake-catcher FOR the
> trusted operator**, not a defense AGAINST them.

## Project shape

- **`pyproject.toml` + `src/` layout.** The official Python packaging tutorial's own scaffold:
  `src/<package>/` with the project configured in `pyproject.toml`. This profile's `typecheck`
  (`mypy src`) and coverage (`--cov=src`) slots assume exactly this layout.
- **Test directories.** `tests/unit`, `tests/integration`, `tests/e2e` must exist in the adopter's
  project for the three `test_recipe` slots to resolve a path — a precondition this profile states,
  not enforces.
- **The public `api` layer is the library's exported surface** (`architecture.layers`), not a
  network endpoint — this profile's `api` layer means "the package's public symbols", not a
  bootable service.

## Interpreter + dependency pinning

- **A committed `.python-version`.** `uv` reads this as the default Python version request and
  fetches/manages that interpreter itself — never the ambient system Python.
- **A committed `uv.lock`, and every dispatched command runs `--locked`.** `uv sync --locked` and
  `uv run --locked` error on a stale lockfile rather than silently re-resolving it — which is why
  every verify/test/boot slot in this pack carries `--locked`. Within a run the profile dispatches,
  the pins below are therefore enforced, not advisory. Outside it (an adopter typing a bare
  `uv run`, or CI that skips these recipes) nothing here can bind them — that boundary is real and
  is stated rather than papered over.
- **`requires-python` is a FLOOR, not the pinned interpreter.** `pyproject.toml`'s
  `requires-python` (`>=3.11`) is resolver-consumed library metadata; the exact interpreter line
  pinned in `.python-version` (`3.13`) is a separate artifact. Conflating the two is the classic way
  dev and CI drift onto different minors.
- **Exact dev-tool pins.** `ruff`, `mypy`, `pytest`, and `pytest-cov` are exact-pinned
  dev-dependency-group entries (`standing-versions/manifest.yaml`), resolved by `uv sync --locked`.
  `uv` itself is **never** a project dependency (never `pip install uv==`) — its pin is consumed as
  the CI setup action's `version:` input (or the standalone installer).
- **CI runs a `uv`-managed interpreter.** `uv python install` (honouring `.python-version`) then
  `uv sync --locked` then `uv run <tool>` — never the runner's ambient system Python. Pin the setup
  action itself to a 40-character commit SHA and request the pinned `uv` version explicitly; this
  profile does not assert a specific SHA (none was source-verified in the research pass).

## Determinism hazards

- **Plugin auto-activation.** `pytest` auto-activates any installed plugin — a stray transitive
  plugin can change a verdict. Bounded here by every test-time plugin being an exact-pinned dev
  dependency and CI syncing `--locked`; never install a pytest plugin outside the locked group.
- **`PYTHONHASHSEED` is deliberately NOT fixed.** Random string-hash seeding per process is itself a
  test of order-independence — pinning it by default would hide that class of bug. Do not add it to
  the recipe environment.
- **The alternative type checker.** The 2026 field's other mature checker is a supported swap for
  `mypy` in this profile's `typecheck` slot — not shipped as a variant here, but the swap is a
  same-shape substitution (`uv run <alt> src`) an adopter may make deliberately.

## Profile-kind claim: this pack asserts no runtime surface

This pack declares `profile_kind: library` **explicitly** — a claim the operator authors, never a
default and never inferred. It asserts that atoms built under this profile have no runtime surface
to boot: there is no `app_exercise_binding`, and none is expected.

That has one direct, load-bearing operator-facing consequence: **locking this pack makes
`/foundry:certify-local` REFUSE by design.** With no boot recipe to resolve, certification
terminates in its pre-dispatch `REFUSED (nothing dispatched): no boot recipe` class — no process is
launched, no verdict is emitted. That is the *correct* shape for a genuine library. It is emphatically
**not** the shape for an atom that has any bootable surface (an HTTP endpoint, a CLI entrypoint, a
worker process) — such an atom belongs on the `python-uv-service` pack instead, where the boot
recipe and the `/healthz` live-seam surface make certification a real, dispatched check rather than
a refusal. Locking a runtime-bearing atom to this pack does not fail loudly; it silently trades a
real certification run for a REFUSE that looks identical to a genuine library's. Choose the pack
deliberately.
