#!/usr/bin/env python3
"""foundry-acceptance-contract-validate — CLI over foundry_contract.

Usage:
  foundry-acceptance-contract-validate.py <contract.yaml> [--spec <spec.md>]
  foundry-acceptance-contract-validate.py --hash <contract.yaml>        # print contract_sha256
  foundry-acceptance-contract-validate.py --spec-hash <spec.md>         # print spec_sha256 (normative)
  foundry-acceptance-contract-validate.py --selftest                    # run fixtures, assert verdicts

Exit 0 = PASS / all selftests passed; exit 1 = FAIL / a selftest verdict mismatched
(fail-closed). The merge gate (0d) enforces freeze floor 5 (AUTO-HARD); this CLI
covers floors 1-4 + schema + byte-canonical hashing.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foundry_contract as fc  # noqa: E402
import foundry_authz as fa  # noqa: E402


def extract_spec_ac_ids(spec_path: str) -> list[str]:
    """AC-ACX-8 (single source of truth): reuse the definition-scoped, suffix-aware
    extractor (`foundry_authz._spec_ac_ids`) rather than re-declaring a second copy of
    the whole-file regex here — so a spec that authorizes cleanly cannot still
    false-reject through this pre-flight validator (`--spec` mode and `--selftest`,
    both wired into foundry-doctor + CI)."""
    return fa._spec_ac_ids(spec_path)


def cmd_validate(contract_path: str, spec_path: str | None) -> int:
    spec_ac_ids = extract_spec_ac_ids(spec_path) if spec_path else None
    ok, errors, warnings = fc.validate_contract(contract_path, spec_ac_ids)
    for w in warnings:
        print(f"  warn: {w}")
    if ok:
        print(f"PASS  {contract_path}  contract_sha256={fc.contract_sha256(contract_path)[:16]}…")
        if spec_path:
            print(f"      spec_ac_ids={spec_ac_ids}")
        return 0
    print(f"FAIL  {contract_path}")
    for e in errors:
        print(f"  error: {e}")
    return 1


def cmd_selftest() -> int:
    fixtures_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "test", "fixtures")
    manifest_path = os.path.join(fixtures_dir, "EXPECTATIONS.json")
    if not os.path.exists(manifest_path):
        print(f"SELFTEST FAIL: no fixtures manifest at {manifest_path}", file=sys.stderr)
        return 1
    with open(manifest_path) as fh:
        manifest = json.load(fh)

    failures = 0
    for entry in manifest["cases"]:
        cpath = os.path.join(fixtures_dir, entry["contract"])
        spath = os.path.join(fixtures_dir, entry["spec"]) if entry.get("spec") else None
        spec_ac_ids = extract_spec_ac_ids(spath) if spath else None
        ok, errors, _warnings = fc.validate_contract(cpath, spec_ac_ids)
        exp_ok = entry["expect_ok"]
        verdict_ok = (ok == exp_ok)
        # When a failure is expected, optionally assert a substring in the errors
        # (golden-artifact-fail-closed: a fixture that fails for the WRONG reason
        # is itself a defect).
        reason_ok = True
        if not exp_ok and entry.get("expect_error_substr"):
            reason_ok = any(entry["expect_error_substr"] in e for e in errors)
        passed = verdict_ok and reason_ok
        failures += 0 if passed else 1
        tag = "ok " if passed else "XX "
        print(f"  {tag} {entry['contract']:42s} expect_ok={exp_ok!s:5s} got_ok={ok!s:5s}"
              + ("" if reason_ok else f"  [reason mismatch: want “{entry.get('expect_error_substr')}”]"))
        if not passed and not exp_ok:
            for e in errors:
                print(f"        - {e}")
    total = len(manifest["cases"])
    print(f"\nSELFTEST: {total - failures}/{total} fixtures verdicts correct")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("contract", nargs="?", help="acceptance-contract.yaml to validate")
    ap.add_argument("--spec", help="spec path (enables bijection floor 3)")
    ap.add_argument("--hash", metavar="CONTRACT", help="print contract_sha256 and exit")
    ap.add_argument("--spec-hash", metavar="SPEC", help="print spec_sha256 (normative) and exit")
    ap.add_argument("--selftest", action="store_true", help="run fixtures, assert verdicts")
    args = ap.parse_args()

    if args.selftest:
        return cmd_selftest()
    if args.hash:
        print(fc.contract_sha256(args.hash))
        return 0
    if args.spec_hash:
        print(fc.spec_sha256(args.spec_hash))
        return 0
    if not args.contract:
        ap.print_help()
        return 2
    return cmd_validate(args.contract, args.spec)


if __name__ == "__main__":
    sys.exit(main())
