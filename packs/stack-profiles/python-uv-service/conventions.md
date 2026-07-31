# python-uv-service — project shape, pin discipline, and live-seam conventions

The Python ASGI web-service stack profile (`id: python-uv-service`), consensus mid-2026 Python
tooling per the spec's "Prior art / industry grounding": **`uv`** for the environment, dependency,
and interpreter management; **`ruff`** for format + lint; **`mypy`** for typecheck; **`pytest`** +
**`pytest-cov`** for the test recipe; a plain `uvicorn` boot for the ASGI app (portable across
FastAPI/Starlette). These are the *genuine* commands a mid-2026 `uv`-first Python service uses —
loaded into the generic worker, never generated.

> **Threat model — TRUSTED OPERATOR.** This profile is operator-curated machinery, authorized at
> the normal `/foundry:authorize` gate. These conventions are an **advisory mistake-catcher FOR the
> trusted operator**, not a defense AGAINST them.

## Project shape

- **`pyproject.toml` + `src/` layout.** The official Python packaging tutorial's own scaffold:
  `src/<package>/` with the project configured in `pyproject.toml`. This profile's `typecheck`
  (`mypy src`) and coverage (`--cov=src`) slots assume exactly this layout.
- **Test directories.** `tests/unit`, `tests/integration`, `tests/e2e` must exist in the adopter's
  project for the three `test_recipe` slots to resolve a path — a precondition this profile states,
  not enforces (matching how the shipped `node-web` profile directs its integration runner at a
  fixed directory).
- **A `/healthz` handler.** The live-seam boot target (below) assumes the ASGI app exposes a
  `GET /healthz` route returning a `200` with JSON body `{"status": "ok"}`.

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

## Live seam (app_exercise_binding)

The profile's `app_exercise_binding` is the generic analog of `make dev`: `boot` starts the ASGI app
under `uvicorn` bound to `127.0.0.1:8000`, and its single `api` surface exercises `GET
http://127.0.0.1:8000/healthz`, asserting a `200` JSON body `{status: ok}` — so the SDLC step-8
live-seam walk and `/foundry:certify-local` have a stack-faithful, resolvable boot target.
