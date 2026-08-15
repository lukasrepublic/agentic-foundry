#!/usr/bin/env python3
"""foundry-authorize — the mechanical half of /foundry:authorize (§22.6/§22.1).

The SKILL (skills/authorize/SKILL.md) drives the operator-facing confirmation; this
CLI performs the validated, logged freeze-write. Without --yes it is a DRY RUN that
displays exactly what the operator is being asked to sign.

  foundry-authorize.py --spec <spec.md> --contract <acceptance-contract.yaml> \
      --operator <op_id> --mode regular|lean [--reauth-after-impl] [--yes]

Fail-closed: refuses unless the contract passes the freeze floors (1-4) AND the
operator_id resolves against .claude/foundry-operators.json. Records a
record-before-action entry to the §22.5a trail BEFORE writing, and a completion
entry after.

The admission-gate precondition module does not ship —
authorize now runs the front-authorization path directly, end-to-end, with no additional
gate in front of it.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foundry_contract as fc          # noqa: E402
import foundry_authz as az             # noqa: E402
import foundry_audit_log as al         # noqa: E402
import foundry_audit_ledger as ledger  # noqa: E402


def _print_checkpoints(contract_path: str) -> None:
    data = fc.load_contract(contract_path)
    print(f"  spec_ref: {data.get('spec_ref')}")
    # UL-0022: target_repo decides WHERE this atom's authorized code lands (the merge venue,
    # CHECK 4b). Display it prominently so the operator signs it explicitly — never blind.
    _tr = data.get("target_repo")
    print(f"  target_repo: {_tr!r}  ({'PRODUCT-REPO dispatch — code lands here, gate-bound to this repo' if _tr and _tr != 'workspace' else 'workspace (single-repo default)'})")
    print(f"  scope.allowed_paths: {(data.get('scope') or {}).get('allowed_paths')}")
    print(f"  scope.denied_paths : {(data.get('scope') or {}).get('denied_paths') or []}")
    print("  checkpoints (the live-seam PASS criteria you are signing):")
    for cp in data.get("checkpoints", []):
        exp = cp.get("expect", {})
        print(f"    - [{cp.get('ac_id')}] {cp.get('surface')} @ {cp.get('locator')}")
        print(f"        expect: {exp.get('op')} {exp.get('value','')} (baseline={exp.get('baseline')})")


def _front_authz_main() -> int:
    """The front-authorization entrypoint body (historically named `main()`, kept as a separate
    function so `main()` below can dispatch to it directly)."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--contract", required=True)
    ap.add_argument("--operator")
    ap.add_argument("--mode", required=True, choices=["regular", "lean"])
    ap.add_argument("--reauth-after-impl", action="store_true")
    ap.add_argument("--skip-audit-reason", default=None,
                    help="operator-only: proceed without a recorded §8 audit, logging this reason "
                         "to the §22.5a trail (UL-0006; the skip path is itself un-cryptographic — "
                         "see foundry_audit_ledger threat model)")
    ap.add_argument("--yes", action="store_true", help="perform the write (else dry-run)")
    args = ap.parse_args()

    for p, label in ((args.spec, "spec"), (args.contract, "contract")):
        if not os.path.exists(p):
            print(f"FAIL: {label} not found: {p}", file=sys.stderr)
            return 1

    # 1. Resolve + validate operator (fail-closed).
    try:
        operator_id = az.resolve_operator(args.operator)
    except az.AuthzError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1

    # 2. Contract must pass the freeze floors (with bijection via the spec).
    spec_ac_ids = az._spec_ac_ids(args.spec)
    ok, errors, warnings = fc.validate_contract(args.contract, spec_ac_ids)
    for w in warnings:
        print(f"  warn: {w}")
    if not ok:
        print("FAIL: contract does not pass freeze floors:", file=sys.stderr)
        for e in errors:
            print(f"  error: {e}", file=sys.stderr)
        return 1

    # 2.5 ER #77 (AC-CSSF-1/-2) — surface⊆scope consistency, fail-closed at freeze. Resolve the
    # contract's VENUE root (workspace single-repo default, or the .claude/foundry-project.json
    # repos{target_repo}.path clone); a NEW path-typed checkpoint surface (test:<p>/file:<p>, path-
    # shaped, absent from the venue) outside scope.allowed_paths is an internally-inconsistent
    # contract — the build could not create its own test surface within scope. An ABSENT multi-repo
    # clone degrades to a WARNING (never wedge authorization on a missing checkout).
    _cdata = fc.load_contract(args.contract)
    _ws_root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    _tr = _cdata.get("target_repo")
    _venue_root: "str | None" = _ws_root
    if _tr and _tr != "workspace":
        _venue_root = None
        try:
            import json as _json
            _pj = _json.load(open(os.path.join(_ws_root, ".claude", "foundry-project.json"),
                                  encoding="utf-8"))
            _rp = ((_pj.get("repos") or {}).get(_tr) or {}).get("path")
            if _rp:
                _cand = os.path.join(_ws_root, _rp)
                _venue_root = _cand if os.path.isdir(_cand) else None
        except Exception:
            _venue_root = None
    if _venue_root is None:
        print(f"  warn: surface⊆scope check degraded — venue root for target_repo {_tr!r} not "
              "resolvable/cloned (ER #77 existence check skipped)")
    _surf_errors = fc.surface_scope_errors(_cdata, _venue_root)
    if _surf_errors:
        print("FAIL (fail-closed): checkpoint surface(s) outside the atom's own scope (ER #77):",
              file=sys.stderr)
        for e in _surf_errors:
            print(f"  error: {e}", file=sys.stderr)
        return 1

    # 2.55 ER #178 fix 1 (AC-OCW-4) — doctor-row-baseline consistency, fail-closed at freeze. Reuses
    # the SAME _venue_root the 2.5 block above already resolved. Rejects freezing a
    # `cli:foundry-doctor` `[ok ] <x>` row at `baseline: pre-change` for a check <x> this atom's
    # own scope.allowed_paths EDITS and that pre-exists (the row is structurally green-on-both — it
    # can never be RED on the merge-base). An unresolvable venue root degrades to a DISCLOSED
    # warning-skip (mirroring the 2.5 / ER #77 precedent above) — never a false block.
    if _venue_root is None:
        print(f"  warn: doctor-row-baseline check degraded — venue root for target_repo {_tr!r} "
              "not resolvable/cloned (ER #178 existence check skipped)")
    _doc_errors = fc.doctor_row_baseline_errors(_cdata, _venue_root)
    if _doc_errors:
        print("FAIL (fail-closed): doctor-row checkpoint(s) frozen at baseline: pre-change for a "
              "pre-existing check (ER #178):", file=sys.stderr)
        for e in _doc_errors:
            print(f"  error: {e}", file=sys.stderr)
        return 1

    # 2.6 Atom C (#121, AC-SGC-3..8) — the system-grounding freeze floor. Resolve Atom A's live
    # system-state snapshot at the SAME venue root the 2.5 block above already computed
    # (_venue_root), then fail closed on either a broken-but-present grounding oracle
    # (GroundingSourceError, AC-SGC-7) or a declared/live contradiction (system_grounding_errors,
    # AC-SGC-3/-4/-5/-8). Delegates to `fc.resolve_grounding_snapshot` (not inlined) so the drop-in
    # check can drive the same fail-closed seam directly (AC-SGC-9(g)). Only reached once the
    # contract already passes freeze floors 1-4 + ER #77's surface⊆scope floor above.
    #
    # sec-review Risk-1: when _venue_root is None (a non-workspace target_repo whose clone is not
    # resolvable) the 2.5 block already degraded to a WARNING. We must NOT then resolve the grounding
    # snapshot against project_dir=None, which build_system_snapshot silently re-resolves to the
    # WORKSPACE root (CLAUDE_PROJECT_DIR/cwd) — that would validate a product-repo atom's block
    # against the wrong system's snapshot (silent under-enforcement when workspace grounding is
    # unconfigured, or false over-blocking when it is). Parallel 2.5: skip with a loud warning rather
    # than substitute a different source of truth. (For target_repo: workspace — the self-host case —
    # _venue_root is the workspace root, never None, so the floor runs normally.)
    if _venue_root is None:
        print("  warn: system-grounding floor SKIPPED — venue root for target_repo "
              f"{_tr!r} not resolvable/cloned; the block is NOT validated against the product repo "
              "and is NOT silently validated against the workspace root (sec-review Risk-1, "
              "parallels the 2.5 degrade)")
    else:
        try:
            _sg_snapshot = fc.resolve_grounding_snapshot(_venue_root)
        except fc.GroundingSourceError as e:
            print(f"FAIL (fail-closed): system-grounding source is present but broken (AC-SGC-7): {e}",
                  file=sys.stderr)
            return 1
        _sg_errors = fc.system_grounding_errors(_cdata, _sg_snapshot)
        if _sg_errors:
            print("FAIL (fail-closed): system_grounding block contradicts the live snapshot (AC-SGC-3/-4/-5):",
                  file=sys.stderr)
            for e in _sg_errors:
                print(f"  error: {e}", file=sys.stderr)
            return 1

    # 2.65 feat-foundry-gate-integrity-retirement-grounding-wiring (AC-RGW-1..8) — the UNGROUNDED-KIND
    # half of the retirement predicate. `system_grounding_errors` above fail-closes a `remove` for a
    # GROUNDED kind (table/column/module) against the live snapshot; it cannot do the same for
    # `kind: resource` — the kind every retired FILE uses, and therefore the kind ER #120/#121 were
    # actually about — because the snapshot carries no dimension for it. `removal_grounding_errors`
    # supplies that half against the venue root, and until this block existed it had NO CALLER: it
    # shipped implemented, tested and inert, while the schema told authors the check ran.
    #
    # SLOT IS PINNED HERE, between 2.6 and 2.7 (AC-RGW-8). Not cosmetic: placing it before 2.6 would
    # change which failure an operator sees when a broken grounding oracle and a bogus removal are both
    # present — altering an existing floor's observable precedence without altering its verdict.
    #
    # `_venue_root` is READ, never re-resolved (AC-RGW-3). The tidy-looking refactor — lifting the 2.5
    # resolution block into a helper — is forbidden by the spec, because any drift in its directory test
    # or exception handling flips the root to None and mass-degrades ALL the floors at once, which no
    # suite covers.
    #
    # Degrade follows 2.7/2.8 (warn AND still call), NOT 2.6 (skip entirely). 2.6 must skip because
    # `resolve_grounding_snapshot(None)` silently re-resolves to the workspace root; this predicate has
    # no such fallback and returns [] for None by construction. The warning names THIS check explicitly
    # (AC-RGW-4) — the several sibling degrade warnings already on this path would otherwise satisfy a
    # generic "a skip was disclosed" reading while this check's own skip stayed invisible.
    if _venue_root is None:
        print(f"  warn: retirement grounding degraded — venue root for target_repo {_tr!r} not "
              "resolvable/cloned; declared removals of UNGROUNDED kinds (resource/fk/queue/event) are "
              "NOT validated against the venue (AC-RGW-4)")
    _rgw_errors = fc.removal_grounding_errors(_cdata, _venue_root)
    if _rgw_errors:
        print("FAIL (fail-closed): system_grounding declares a removal that has not landed (AC-RGW-1):",
              file=sys.stderr)
        for e in _rgw_errors:
            print(f"  error: {e}", file=sys.stderr)
        return 1

    # 2.7 ER #179 (AC-APG-1..5) — scope.allowed_paths reality-grounding, fail-closed at freeze. Reuses
    # the SAME _venue_root the 2.5 block above already resolved. Classifies each allowed_paths entry
    # as EXISTS (literal/dir/glob match under the venue root) or checkpoint-named declared-new
    # (AC-APG-3 infer-from-checkpoint tolerance); anything else fails CLOSED naming the offending
    # entry — the stale-prefix/typo drift that merge-gate CHECK-4 previously caught only at merge
    # (after the build was complete) is now caught at the authorize front door. An unresolvable
    # venue root degrades to a DISCLOSED warning-skip (mirrors the 2.5/2.55/2.6 precedents above) —
    # never a false block (AC-APG-4).
    if _venue_root is None:
        print(f"  warn: allowed_paths grounding degraded — venue root for target_repo {_tr!r} not "
              "resolvable/cloned (ER #179 existence check skipped)")
    _apg_errors = fc.allowed_paths_grounding_errors(_cdata, _venue_root)
    if _apg_errors:
        print("FAIL (fail-closed): scope.allowed_paths entry(ies) ungroundable against the venue "
              "root (ER #179):", file=sys.stderr)
        for e in _apg_errors:
            print(f"  error: {e}", file=sys.stderr)
        return 1

    # 2.8 feat-foundry-walk-locator-executability (AC-WLE-4/-5) — checkpoint LOCATOR sanity,
    # fail-closed at freeze. Reuses the SAME _venue_root the 2.5 block above already resolved.
    # Grounds every `cli:*` checkpoint's locator: a LOCATOR-SCRIPT-REF (a shlex token containing '/',
    # non-absolute/non-traversing, ending .py/.sh) must either EXIST under the venue root or be
    # admitted by this atom's own scope.allowed_paths (declared-new); an unparseable locator convicts.
    # Catches the typo'd `python3 scripts/foundry_checks/<typo>.py --selftest` at the authorize front
    # door instead of at the walk — where a Python interpreter that cannot open its script exits 2,
    # NOT 127, so the sibling runtime executability rule deliberately cannot see it (the two halves
    # are complementary). Pure — executes NOTHING. An unresolvable venue root degrades to a DISCLOSED
    # warning-skip (mirrors the 2.5/2.55/2.6/2.7 precedents above) — never a false block (AC-WLE-5).
    if _venue_root is None:
        print(f"  warn: checkpoint locator grounding degraded — venue root for target_repo {_tr!r} "
              "not resolvable/cloned (AC-WLE-4 existence check skipped)")
    _loc_errors = fc.locator_grounding_errors(_cdata, _venue_root)
    if _loc_errors:
        print("FAIL (fail-closed): checkpoint locator(s) ungroundable against the venue root "
              "(walk-locator-executability, AC-WLE-4):", file=sys.stderr)
        for e in _loc_errors:
            print(f"  error: {e}", file=sys.stderr)
        return 1

    # 3. State check.
    state, notes = az.spec_state(args.spec, args.contract)
    print(f"state: {state}  ({'; '.join(notes)})")
    if state == az.AUTHORIZED and not args.reauth_after_impl:
        print("Already AUTHORIZED and hashes match — nothing to do (idempotent).")
        return 0

    # 3.5 §8 audit enforcement (UL-0006, AC-AUDITENF-1..3). Single-read spec hash: this SAME value
    # binds the audit-evidence lookup AND the freeze below (kills the recorder↔authorize↔freeze
    # TOCTOU). Fail-closed on --yes; a prominent warning on the dry-run. The skip path is
    # operator-only + logged (and, by the operator-chosen non-crypto design, not agent-proof — see
    # foundry_audit_ledger threat model).
    spec_hash = fc.spec_sha256(args.spec)
    audit_rec = ledger.find_audit(spec_hash)
    if audit_rec:
        print(f"§8 audit: recorded ({audit_rec.get('rounds')} round(s), "
              f"verdict={audit_rec.get('verdict')}) for this spec content.")
    elif args.skip_audit_reason:
        print(f"§8 audit: SKIPPED by operator — reason: {args.skip_audit_reason} "
              "(logged to the §22.5a trail).")
    else:
        msg = ("§8 adversarial spec-audit NOT recorded for this spec content "
               f"(spec_sha256={spec_hash[:16]}…). Run /foundry:audit (it records via "
               "foundry-audit-record.py), then re-run; or pass --skip-audit-reason \"<reason>\" "
               "(operator-only, logged).")
        if args.yes:
            print(f"FAIL (fail-closed): {msg}", file=sys.stderr)
            return 1
        print(f"\n⚠️  AUDIT-ENFORCEMENT (will BLOCK the --yes freeze): {msg}")

    # 4. Display what the operator signs.
    print(f"\noperator={operator_id}  mode={args.mode}"
          + ("  RE-AUTH-AFTER-IMPL" if args.reauth_after_impl else ""))
    _print_checkpoints(args.contract)

    if not args.yes:
        print("\nDRY RUN — no write. After operator confirmation, re-run with --yes.")
        return 0

    # 5. Record-before-action (fail-closed), then freeze, then completion record.
    pre_contract_hash = fc.contract_sha256(args.contract)
    # spec_hash already computed once in step 3.5 (single-read TOCTOU guard) — reused here.
    if not audit_rec and args.skip_audit_reason:
        try:
            al.append_record({
                "action": "authorize-audit-skip",
                "operator_id": operator_id,
                "spec_ref": args.spec,
                "spec_sha256": spec_hash,
                "reason": args.skip_audit_reason,
            })
        except al.AuditLogError as e:
            print(f"FAIL (fail-closed): could not log §8 audit skip to the §22.5a trail: {e}", file=sys.stderr)
            return 1
    try:
        intent_id = al.append_record({
            "action": "authorize-intent",
            "operator_id": operator_id,
            "spec_ref": args.contract,  # contract carries spec_ref; this is the contract path
            "spec_sha256": spec_hash,
            "contract_sha256": pre_contract_hash,
            "merge_autonomy_mode": args.mode,
            "reauth_after_impl": bool(args.reauth_after_impl),
        })
    except al.AuditLogError as e:
        print(f"FAIL (fail-closed): could not write §22.5a record-before-action: {e}", file=sys.stderr)
        return 1

    block = az.authorize(
        spec_path=args.spec,
        contract_path=args.contract,
        operator_id=operator_id,
        merge_autonomy_mode=args.mode,
        authorized_at=al.now_iso(),
        reauth_after_impl=args.reauth_after_impl,
    )

    al.append_record({
        "action": "authorize-complete",
        "intent_ref": intent_id,
        "operator_id": operator_id,
        "auth_seq": block["auth_seq"],
        "supersedes": block["supersedes"],
        "spec_sha256": block["spec_sha256"],
        "contract_sha256": block["contract_sha256"],
        "merge_autonomy_mode": block["merge_autonomy_mode"],
    })

    print(f"\nAUTHORIZED  auth_seq={block['auth_seq']}  "
          f"contract_sha256={block['contract_sha256'][:16]}…  spec_sha256={block['spec_sha256'][:16]}…")
    print("§22.5a trail updated (.foundry/security-audit.jsonl).")
    return 0


def main() -> int:
    """The admission-gate precondition does not ship — `main()`
    now dispatches directly to `_front_authz_main()` (the front-authorization entrypoint),
    unconditionally. authorize runs end-to-end without any additional gate in front of it."""
    return _front_authz_main()


if __name__ == "__main__":
    sys.exit(main())
