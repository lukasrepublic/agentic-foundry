#!/usr/bin/env python3
"""foundry-spec-lint — Phase 0 deterministic pre-lints for /foundry:spec-review
(feat-foundry-wave-plan security finding 6, PR #271).

A small, dependency-light CLI (stdlib + PyYAML, already a project dependency, no new third-party
deps) wrapping the two REUSED deterministic checks `/foundry:spec-review`'s Phase 0 drives — this
module never re-implements either check, it only wires them into one command a skill can invoke
directly instead of describing an ad-hoc import:

  1. **The BINDING size ceiling** (CONSTITUTION.md §12 — 14 ACs / 8,000 words, NO override) via
     `foundry-audit-prepare.py`'s `spec_size_metrics(spec_text)`. The ceiling is **HARDCODED here**
     (`HARD_ACS`/`HARD_WORDS` below), deliberately NOT resolved via that module's
     `load_size_thresholds()` — the adopter-tunable `.claude/foundry-project.json` `gates.audit_size`
     block exists to soft-tune the DORMANT §8 engine's warn/hard thresholds
     (feat-foundry-audit-tier-caps), a knob for machinery the operator may adjust per-project. THIS
     lint enforces the CONSTITUTION-bound floor from `/foundry:spec-review`'s Phase 0 — reading the
     adopter-tunable config here would let a project silently raise a ceiling CONSTITUTION.md §12
     declares binding with no override. There is no flag to relax it.
  2. **Reference-closure** via `foundry_audit_preconditions.py`'s `gate_reference_closure` (the
     retired engine's G-4 gate, kept as a cheap deterministic lint — see
     `docs/foundry/SPEC-AUDIT-ASSESSMENT.md` §2.6).

Prints ONE verdict line (plus, on failure, one finding line per problem) and exits 0 (clean) or 1
(a lint failure — oversize and/or a dangling reference). Never mutates anything.

CLI:
  foundry-spec-lint.py <spec> [--project-dir <dir>]
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import foundry_audit_preconditions as ap   # noqa: E402 — gate_reference_closure, reused not reimplemented

# The BINDING ceiling (CONSTITUTION.md §12) — HARDCODED, deliberately NOT the adopter-tunable
# `gates.audit_size` resolution (see the module docstring above). No override flag exists.
HARD_ACS = 14
HARD_WORDS = 8000


def _load_audit_prepare():
    """Import `foundry-audit-prepare.py` (a hyphenated filename, not import-able by bare `import`)
    for its `spec_size_metrics` — REUSED, never re-implemented here."""
    path = os.path.join(HERE, "foundry-audit-prepare.py")
    spec = importlib.util.spec_from_file_location("foundry_audit_prepare_for_lint", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def lint_spec(spec_path, *, project_dir=None):
    """Run both Phase-0 checks over `spec_path`. Returns `(ok, findings)` — `ok` is False iff
    either check fails; `findings` is a list of human-readable finding strings (empty iff `ok`)."""
    if not os.path.isfile(spec_path):
        return False, [f"spec not found: {spec_path}"]

    findings = []

    prep = _load_audit_prepare()
    with open(spec_path, encoding="utf-8") as f:
        spec_text = f.read()
    ac_count, word_count = prep.spec_size_metrics(spec_text)
    if ac_count > HARD_ACS or word_count > HARD_WORDS:
        findings.append(
            f"OVERSIZE: {ac_count} ACs / {word_count} words exceeds the BINDING ceiling "
            f"({HARD_ACS} ACs / {HARD_WORDS} words — CONSTITUTION.md §12, no override) — "
            f"decompose into smaller atoms, there is no --allow-oversize escape hatch here"
        )

    result = ap.gate_reference_closure(spec_path, project_dir=project_dir)
    if result.action == "refuse":
        findings.append(f"DANGLING-REFERENCE: {result.detail}")

    return (not findings), findings


def _nonconforming_warnings(spec_path):
    """(AC-SSC-9) The NONCONFORMING-AC-ID warning lines for `spec_path`, or `[]` on a missing/
    unreadable file (never raises — a CLI-level advisory, not part of `lint_spec`'s (ok, findings)
    arity, C6). ADVISORY ONLY: printed whether or not the lint otherwise passes, and never changes
    the AC count or the exit status determined by `lint_spec` alone."""
    if not os.path.isfile(spec_path):
        return []
    prep = _load_audit_prepare()
    try:
        with open(spec_path, encoding="utf-8") as f:
            spec_text = f.read()
    except OSError:
        return []
    tokens = prep.nonconforming_ac_id_tokens(spec_text)
    return [
        f"NONCONFORMING-AC-ID: {tok} — shaped like an AC ID but its suffix satisfies neither "
        f"grammar (not empty, not a single trailing lowercase letter) — not counted, not refused, "
        f"but not silently understood either"
        for tok in tokens
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Phase 0 deterministic pre-lints for /foundry:spec-review "
                    "(the binding size ceiling + reference-closure).")
    parser.add_argument("spec", help="path to the spec to lint")
    parser.add_argument("--project-dir", default=None,
                        help="else CLAUDE_PROJECT_DIR / cwd (for reference-closure's corpus scan)")
    args = parser.parse_args(argv)

    ok, findings = lint_spec(args.spec, project_dir=args.project_dir)

    # AC-SSC-9: printed WHETHER OR NOT the lint otherwise passes, and does not itself change `ok`
    # or `findings` — a nonconforming AC-ID-shaped token is disclosed, never refused (counting
    # parity with the authorize path is preserved; see the spec's prior-art family 1b).
    for warn_line in _nonconforming_warnings(args.spec):
        print(warn_line, file=sys.stderr)

    if ok:
        print(f"foundry-spec-lint: OK — {args.spec} clean (size ceiling + reference-closure)")
        return 0
    print(f"foundry-spec-lint: FAIL-CLOSED — {args.spec}:", file=sys.stderr)
    for line in findings:
        print(f"  - {line}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
