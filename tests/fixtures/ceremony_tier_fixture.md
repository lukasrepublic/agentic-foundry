# Fixture — ceremony-tier CLI behavioural checkpoint (feat-foundry-ceremony-tiering, AC-CTR-8)

> A deliberately **keyword-free** stub spec. It exists only to give the behavioural CLI
> checkpoint (`acceptance-contract.yaml`, AC-CTR-8) a committed `--spec` file to read: the CLI
> is run for real (`python3 scripts/foundry_ceremony_tier.py --files 3 --ambiguity low --spec
> tests/fixtures/ceremony_tier_fixture.md`) and the printed line is asserted against the
> anchored one-line form. Deliberately avoids the closed blast-radius keyword set the classifier
> matches (see the spec's normative table) so the checkpoint exercises the unflagged path.

## Summary

This is a small documentation fixture. It changes formatting on a handful of files and adds no
new public interface, no new dependency, and no design ambiguity worth naming.

<!-- normative -->
- **AC-FIX-1** (Requirement): When the fixture is read by the classifier CLI, the classifier
  SHALL find zero blast-radius keywords in its text.
<!-- /normative -->
