# Implement under cli-tool

Load `conventions.md` first. Put logic in `domain` and keep each command a thin parse-call-format function.

1. Add or change domain functions with unit tests that never touch argv or stdout.
2. Wire the command: validate options, map domain errors to the documented exit codes, write results to stdout and diagnostics to stderr.
3. If the command prints results, support `--json` and add or update its golden-file test; a changed `--help` golden is a deliberate, reviewed change.
4. Add a subprocess test that runs the real entry point and asserts the exit code.

Run the profile's `static_validation` and `test_recipe` commands, then tick the touched items in `blueprints/readiness-checklist.md`. Never edit `standing-versions/` pins inside an atom.
