# cli-tool readiness checklist

Gate before a change built under this blueprint is declared production-ready. The `governance` block of `stack-profile.yaml` selects the kits and levels that enforce it.

- [ ] `--help` and `--version` work; every command documents its flags and exit codes.
- [ ] Exit codes are stable and documented; errors go to stderr, results to stdout.
- [ ] Machine-readable output mode (`--json`) is stable and versioned.
- [ ] No interactive prompt without a non-interactive override.
- [ ] Installable from a pinned, locked toolchain; release artefacts are reproducible.
