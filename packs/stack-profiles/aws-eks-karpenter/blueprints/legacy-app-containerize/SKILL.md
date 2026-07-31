---
id: legacy-app-containerize
covers: ["eol-runtime-containerization", "dockerfile-scaffold", "dependency-drift-preflight", "buildkit-operations"]
parametrizes_from: []
---

## When to trigger

- Containerizing an application pinned to an end-of-life runtime major (old Node.js/Python/Ruby)
  as-is for a lift-and-shift migration (runtime upgrade is a separate project).
- A legacy-app image build failing with parser errors in `.d.ts` files, engine-floor install
  errors, "no rule to make target" in a post-install hook, EACCES on first write, or a BuildKit
  GOAWAY during export.

Inputs: source repo; the pinned runtime + major (e.g. `node:16.20.1`); package manager + install
verb; the non-root runtime UID; runtime state paths; the registry immutability policy (tag == SHA).

## Pre-flight checks (run BEFORE the first build; each maps to a fleet-recurrent gotcha)

- **`lockfile-committed` — a lockfile exists AND is committed.** Install-at-build with no lockfile
  carries a latent transitive-version float: the image builds today and hard-breaks on the next
  rebuild when a `^`-ranged transitive floats. When the manifest and lockfile are OUT OF SYNC,
  make a **surgical single-entry lockfile edit** of only the drifted entry (when the two versions
  have identical dependency trees) — **never a fresh install**, which re-floats every transitive
  dep and reintroduces the stub-float and engine-floor gotchas.
- **`type-stub-pinned-to-runtime` — pin `@types/<runtime>`-style stubs to the RUNTIME's major**
  (e.g. `^16` stubs for a v16 runtime). An unpinned stub floats to a modern major whose `.d.ts`
  uses syntax the old compiler's PARSER rejects (parse errors, not type errors) — and
  **`--skipLibCheck` does NOT rescue it: it skips `.d.ts` type-CHECKING but still PARSES every
  `.d.ts`.**
- **`engine-floor-vs-runtime` — scan the resolved tree for `engines` floors above the pinned
  runtime.** A strict/frozen install ERRORS on the mismatch (the legacy environment only warned).
  Prescribe the ignore-engines flag **while keeping the lockfile frozen**, and track the EOL
  runtime as a pre-production residual — never unfreeze to dodge the error.
- **`build-file-in-deps-layer` — a post-install/native hook's referenced files are in the
  dependency-layer COPY set.** A hook running `make …` during install fails "no rule to make
  target" when the deps layer COPYed only manifest + lockfile + registry config — layer-ordering
  correctness, not a toolchain bug.
- **`workdir-writable-for-nonroot` — if the app writes runtime state under WORKDIR and runs
  non-root, `chown` the state path for the runtime UID** — a root-owned WORKDIR gives EACCES on
  the first write of a file-backed embedded store.

## The Dockerfile scaffold rules (multi-stage, annotated to the checks)

- **Deps layer COPY set: manifest + lockfile + registry config + any post-install build files**
  (the Makefile/codegen inputs), then the **pinned FROZEN install with ignore-engines as an
  opt-in ARG** (default off; enabling it is the engine-floor escape hatch, recorded).
- **App layer runs NON-ROOT with the state path made writable for the runtime UID**; the runtime
  base image is the pinned EOL tag (never `latest`, never a floating major).

## The BuildKit execution rule (operational, not a Dockerfile bug)

- **A `graceful_stop`/GOAWAY during image export is a TRANSIENT eviction — retry the job;
  serialize builds under the cache cap.** Under concurrent builds the in-cluster BuildKit pod gets
  evicted (cache EmptyDir overflow / autoscaler churn) and kills every in-flight build at export.
  Nothing was pushed, so there is **no immutable-tag conflict** — do not hunt a phantom Dockerfile
  defect and do not loosen registry immutability.

## Anti-patterns

- **A fresh install to "fix" a stale lockfile** — re-floats the whole tree; surgical edit only.
- **Reaching for `--skipLibCheck`** against a floated stub — it still parses; pin the stub.
- **Unfreezing the lockfile** to satisfy an engine floor — ignore-engines with the freeze intact.
- **Treating a GOAWAY as a Dockerfile bug** — retry and serialize; it is an eviction.
- **Running the legacy app as root** to dodge EACCES — chown the state path instead.
