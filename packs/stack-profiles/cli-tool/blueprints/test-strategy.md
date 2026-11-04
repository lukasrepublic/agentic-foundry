# cli-tool test strategy

What each test lane proves for this blueprint; the exact commands live in `stack-profile.yaml` (`test_recipe`).

- Unit: command functions over the domain layer.
- Integration: invoke the CLI as a subprocess against fixture directories.
- Golden files for `--help` and `--json` output; changes are reviewed deliberately.
- Exit-code matrix test: success, usage error, runtime error.
