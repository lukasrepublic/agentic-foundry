#!/usr/bin/env python3
"""foundry-audit-record — record that the §8 adversarial spec-audit ran for a spec (UL-0006).

Run at the END of /foundry:audit. Writes an evidence row bound to the spec's CURRENT content hash
to .foundry/audit-ledger.jsonl, so /foundry:authorize can fail-closed on an un-audited spec. See
foundry_audit_ledger for the (honest, non-crypto) threat model.

LEDGER v2 (feat-foundry-audit-ledger-taxonomy, AC-ALT-1..3): this CLI writes ONE schema-validated
v2 JSONL row per invocation — a FLAT verdict enum plus an orthogonal `--kill-reason` present IFF
`--verdict killed`, `ts`/`run_id`/`tier`/`rounds`/`findings`/`spec_ref`/`spec_sha256`/a
registry-bound `operator`. The bare string "plateau" is never written by this v2 code path
(AC-ALT-1): a legacy invocation that omits `--verdict` (still used by foundry-doctor.py's own
audit-enforcement selftest — `--rounds N --operator X`, no `--verdict`) and an EXPLICIT
`--verdict plateau` both map deterministically onto `plateau-clean` (the corpus's own convention:
plateau -> plateau-clean unless the atom was security-flagged, in which case call this with
`--verdict plateau-security` explicitly).

  foundry-audit-record.py --spec <path> --rounds N --operator <id>
      [--verdict converged|plateau-clean|plateau-security|needs-reground|needs-operator|
                 killed|dedupe-skip|refused|plateau(legacy, mapped)]
      [--kill-reason watchdog|limit|error]     # required iff --verdict killed
      [--tier <alias>]                         # audit-engine model tier this run executed under
      [--run-id <id>]                          # else best-effort session-derived / generated
      [--findings-new N] [--findings-resolved N] [--findings-open N]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foundry_contract as fc          # noqa: E402
import foundry_audit_ledger as al      # noqa: E402

# AC-ALT-1: the bare string "plateau" is never written by v2 code — both the omitted-flag legacy
# shape and an explicit `--verdict plateau` map onto plateau-clean deterministically.
_LEGACY_VERDICT_MAP = {"plateau": "plateau-clean"}
_DEFAULT_VERDICT = "plateau-clean"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--rounds", type=int, required=True,
                    help="number of §8 audit rounds run (>=0; 0 only for a terminus that never "
                         "ran a round, e.g. dedupe-skip/refused)")
    ap.add_argument("--operator", required=True)
    ap.add_argument("--verdict", default=None,
                    help="converged|plateau-clean|plateau-security|needs-reground|needs-operator|"
                         "killed|dedupe-skip|refused (legacy 'plateau' accepted, mapped to "
                         "plateau-clean; omitted also defaults to plateau-clean)")
    ap.add_argument("--kill-reason", default=None, choices=list(al.KILL_REASONS),
                    help="required iff --verdict killed; refused otherwise")
    ap.add_argument("--tier", default="unspecified",
                    help="audit-engine model tier/alias this run executed under")
    ap.add_argument("--run-id", default=None,
                    help="else best-effort session-derived / generated (see foundry_audit_ledger)")
    ap.add_argument("--findings-new", type=int, default=0)
    ap.add_argument("--findings-resolved", type=int, default=0)
    ap.add_argument("--findings-open", type=int, default=0)
    args = ap.parse_args()

    if not os.path.exists(args.spec):
        print(f"FAIL: spec not found: {args.spec}", file=sys.stderr)
        return 1
    if args.rounds < 0:
        print("FAIL: --rounds must be >= 0", file=sys.stderr)
        return 1

    if args.verdict is None:
        verdict = _DEFAULT_VERDICT
    elif args.verdict in _LEGACY_VERDICT_MAP:
        verdict = _LEGACY_VERDICT_MAP[args.verdict]
    elif args.verdict in al.V2_VERDICTS:
        verdict = args.verdict
    else:
        print(f"FAIL: --verdict {args.verdict!r} is not a recognized verdict "
              f"({list(al.V2_VERDICTS)})", file=sys.stderr)
        return 1

    if verdict == "killed" and not args.kill_reason:
        print("FAIL: --verdict killed requires --kill-reason (watchdog|limit|error)", file=sys.stderr)
        return 1
    if verdict != "killed" and args.kill_reason:
        print("FAIL: --kill-reason is only valid together with --verdict killed", file=sys.stderr)
        return 1

    operator_id = al.resolve_operator_for_ledger(args.operator)
    spec_hash = fc.spec_sha256(args.spec)
    findings = {"new": args.findings_new, "resolved": args.findings_resolved,
                "open": args.findings_open}
    try:
        row = al.record_audit_v2(spec_ref=args.spec, spec_sha256=spec_hash, rounds=args.rounds,
                                 operator=operator_id, tier=args.tier, verdict=verdict,
                                 findings=findings, kill_reason=args.kill_reason,
                                 run_id=args.run_id)
    except al.LedgerWriteError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1

    print(f"recorded §8 audit: {args.spec}  spec_sha256={spec_hash[:16]}…  "
          f"rounds={row['rounds']}  verdict={row['verdict']}  run_id={row['run_id']}  "
          f"→ {al.ledger_path()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
