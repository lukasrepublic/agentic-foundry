# cli-tool conventions

A command-line tool (Python via uv, or Node). Its only surface is the CLI. Dependencies point inward only.

## Layers

- **domain** -- the tool's actual logic as plain functions and types. No argument parsing, no printing, no process exit, no environment reads except through parameters. Imports nothing from `commands`.
- **commands** -- argument parsing, option validation, output formatting, exit codes. May import `domain`. Each command is a thin function: parse, call domain, format.

Allowed edges, in prose: commands -> domain; domain -> nothing.

## Conventions that matter for this type

- **Exit codes** are part of the contract: 0 success, 1 runtime failure, 2 usage error (plus any documented tool-specific codes). Errors go to stderr, results to stdout.
- **`--json`** gives a stable, versioned machine-readable output on every command that prints results; human output may change, JSON output may not without a version bump.
- **No network or heavy work at import time.** `--help` and `--version` must be instant and offline; do all I/O inside command bodies.
- No interactive prompt without a non-interactive override (`--yes`, `--no-input`).

Tooling is locked (`uv sync --locked`, or the committed Node lockfile); pins live in `standing-versions/manifest.yaml`.
