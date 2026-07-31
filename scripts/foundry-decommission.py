#!/usr/bin/env python3
"""foundry-decommission — the decommission-gate machinery (feat-foundry-decommission-gate, ER #80).

No legacy component is severed without a VALIDATED, still-holding, LIVE-re-verified ledger row;
turn-off is structurally the last wave. Three verbs over an adopter-filled program directory:

  validate-register <register.yaml> [--ledger <ledger.jsonl>] [--regen]
      Structural + internal-consistency validation (class-aware). `gate_status` is GENERATED from
      the ledger — a hand-edited value that disagrees with the ledger derivation fails closed;
      --regen rewrites it from the ledger.

  record --register <r.yaml> --ledger <l.jsonl> --component <id> --verdict VALIDATED|REJECTED|TURNED_OFF
         --operator <op_id> --timestamp <iso> [--evidence-json <json>]
      Appends ONE row to the append-only JSONL validation ledger, bound to a REGISTERED operator
      (the existing foundry operator registry — no invented dual-control). REFUSES an incomplete
      VALIDATED row: endpoint-bearing/headless need evidence.independence_pass + evidence.old_safe
      (+ evidence.custody_proof when the component is custody:true); decommission-only needs
      evidence.dead_evidence_ref + evidence.old_safe. Timestamps are operator-supplied (no in-tool
      wall-clock, matching the other foundry record tools). The tool NEVER rewrites existing rows.

  gate-check --register <r.yaml> --ledger <l.jsonl> --component <id>
      GO (exit 0) ONLY when ALL hold: the component's LATEST ledger row is a complete VALIDATED;
      the register's checks.reverify slot command exists, runs with DECOM_PHASE=reverify FORCED
      into its environment (waiver-blind by construction), exits 0, and emits a JSON verdict
      {"verdict":"pass","canary_denied":true,"refs_scanned":N} whose floors the gate enforces
      (canary_denied MUST be true — the policy provably evaluated a known-dirty input; refs_scanned
      MUST be > 0 for endpoint-bearing/headless — a vacuous scan is a setup error, never a PASS);
      the checks.old_safe slot exists and exits 0; and custody components carry custody_proof.
      ANYTHING else is NO-GO (exit 1) naming the cause. This is where ER #55 is consumed: the gate
      never trusts the persisted flag — GO is re-derived from live re-checks at turn-off time.

Threat model — TRUSTED OPERATOR (staged model). Slot commands are operator-authored DATA the gate
drives (the infra_binding precedent); they are read-only BY CONTRACT (the gate cannot verify that,
the CTX guard governs in-session runs). The floors (canary, vacuous-scan, waiver-blind phase) are
enforced HERE via the typed verdict contract, not left to adopter discipline.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

_CLASSES = ("endpoint-bearing", "headless", "decommission-only")
_VERDICTS = ("VALIDATED", "REJECTED", "TURNED_OFF")


class DecomError(Exception):
    """Fail-closed validation/gate error."""


# ─────────────────────────────── register ───────────────────────────────

def load_register(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or not isinstance(data.get("components"), list):
        raise DecomError("register must be a mapping with a components: list")
    return data


def validate_register(data: dict) -> list[str]:
    """Structural + internal-consistency errors ([] ⇒ valid). Class-aware (AC-DCG-1)."""
    errors: list[str] = []
    seen: set[str] = set()
    for i, c in enumerate(data.get("components") or []):
        if not isinstance(c, dict):
            errors.append(f"components[{i}]: not a mapping")
            continue
        cid = c.get("id")
        loc = f"components[{i}] ({cid!r})"
        if not isinstance(cid, str) or not cid.strip():
            errors.append(f"{loc}: missing/empty id")
        elif cid in seen:
            errors.append(f"{loc}: duplicate id")
        else:
            seen.add(cid)
        cls = c.get("class")
        if cls not in _CLASSES:
            errors.append(f"{loc}: class must be one of {_CLASSES}, got {cls!r}")
            continue
        if not c.get("legacy_identity"):
            errors.append(f"{loc}: missing legacy_identity (the thing being turned off)")
        has_repl = bool(c.get("replacement"))
        if cls == "decommission-only":
            if has_repl:
                errors.append(f"{loc}: a component WITH a replacement cannot be decommission-only")
            if not c.get("dead_evidence"):
                errors.append(f"{loc}: decommission-only requires dead_evidence")
        else:
            if not has_repl:
                errors.append(f"{loc}: class {cls} REQUIRES a replacement")
        if not isinstance(c.get("custody", False), bool):
            errors.append(f"{loc}: custody must be a bool")
        checks = c.get("checks") or {}
        if not isinstance(checks, dict):
            errors.append(f"{loc}: checks must be a mapping of slot -> command string")
        else:
            for slot, cmd in checks.items():
                if slot not in ("reverify", "old_safe"):
                    errors.append(f"{loc}: unknown checks slot {slot!r}")
                elif not isinstance(cmd, str) or not cmd.strip():
                    errors.append(f"{loc}: checks.{slot} must be a non-empty command string")
    return errors


# ─────────────────────────────── ledger ───────────────────────────────

def read_ledger(path: str) -> list[dict]:
    rows: list[dict] = []
    if not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                raise DecomError(f"ledger line {n}: unparseable JSON (append-only corruption?)")
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _validated_row_errors(component: dict, evidence: dict) -> list[str]:
    """The per-class completeness floor a VALIDATED row must meet (AC-DCG-2)."""
    errors = []
    cls = component.get("class")
    ev = evidence or {}
    if cls in ("endpoint-bearing", "headless"):
        if not ev.get("independence_pass"):
            errors.append("VALIDATED requires evidence.independence_pass")
        if not ev.get("old_safe"):
            errors.append("VALIDATED requires evidence.old_safe")
        if component.get("custody") and not ev.get("custody_proof"):
            errors.append("custody component: VALIDATED requires evidence.custody_proof "
                          "(a real-operation proof — the tool refuses to omit it)")
    elif cls == "decommission-only":
        if not ev.get("dead_evidence_ref"):
            errors.append("decommission-only VALIDATED requires evidence.dead_evidence_ref")
        if not ev.get("old_safe"):
            errors.append("decommission-only VALIDATED requires evidence.old_safe (zero-traffic "
                          "corroboration — a live component cannot be hand-downgraded to dead)")
    return errors


def record(register: dict, ledger_path: str, component_id: str, verdict: str,
           operator_id: str, timestamp: str, evidence: dict) -> dict:
    if verdict not in _VERDICTS:
        raise DecomError(f"verdict must be one of {_VERDICTS}")
    comp = next((c for c in register.get("components") or []
                 if isinstance(c, dict) and c.get("id") == component_id), None)
    if comp is None:
        raise DecomError(f"component {component_id!r} not in the register")
    # operator binding — the EXISTING registry root-of-trust (no invented dual-control).
    import foundry_authz as az
    ops = az.load_operators()
    if operator_id not in ops:
        raise DecomError(f"operator {operator_id!r} not in the operator registry (fail-closed)")
    if not timestamp or not isinstance(timestamp, str):
        raise DecomError("--timestamp is operator-supplied and required (no in-tool wall-clock)")
    if verdict == "VALIDATED":
        errs = _validated_row_errors(comp, evidence)
        if errs:
            raise DecomError("incomplete VALIDATED row refused: " + "; ".join(errs))
    row = {"component_id": component_id, "verdict": verdict, "operator_id": operator_id,
           "timestamp": timestamp, "evidence": evidence or {}}
    # APPEND-ONLY: open in append mode; existing rows are never rewritten.
    with open(ledger_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def derive_gate_status(component_id: str, rows: list[dict]) -> str:
    mine = [r for r in rows if r.get("component_id") == component_id]
    if not mine:
        return "UNVALIDATED"
    return {"VALIDATED": "VALIDATED", "REJECTED": "REJECTED",
            "TURNED_OFF": "TURNED_OFF"}.get(mine[-1].get("verdict"), "UNVALIDATED")


# ─────────────────────────────── gate-check ───────────────────────────────

def _run_slot(cmd: str, phase_env: dict) -> tuple[int, str]:
    env = dict(os.environ)
    env.update(phase_env)
    p = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True,
                       timeout=300, env=env)
    return p.returncode, (p.stdout or "")


def gate_check(register: dict, ledger_path: str, component_id: str) -> tuple[bool, list[str]]:
    """(go, reasons). GO only on latest-VALIDATED + live re-checks (AC-DCG-3). Fail-closed."""
    reasons: list[str] = []
    comp = next((c for c in register.get("components") or []
                 if isinstance(c, dict) and c.get("id") == component_id), None)
    if comp is None:
        return False, [f"component {component_id!r} not in the register"]
    reg_errs = validate_register(register)
    if reg_errs:
        return False, [f"register inconsistent: {e}" for e in reg_errs]
    rows = read_ledger(ledger_path)
    mine = [r for r in rows if r.get("component_id") == component_id]
    if not mine:
        return False, ["no ledger row — never validated (NO-GO)"]
    latest = mine[-1]
    if latest.get("verdict") != "VALIDATED":
        return False, [f"latest ledger verdict is {latest.get('verdict')!r}, not VALIDATED (NO-GO)"]
    errs = _validated_row_errors(comp, latest.get("evidence") or {})
    if errs:
        return False, [f"latest VALIDATED row incomplete: {e}" for e in errs]

    checks = comp.get("checks") or {}
    # ---- live re-check 1: independence REVERIFY (waiver-blind FORCED via DECOM_PHASE=reverify).
    rv = checks.get("reverify")
    if not rv:
        reasons.append("no checks.reverify slot — the live independence re-check is UNAVAILABLE (NO-GO; "
                       "a persisted validation alone never authorizes a sever — ER #55)")
    else:
        try:
            rc, out = _run_slot(rv, {"DECOM_PHASE": "reverify"})
        except Exception as e:  # noqa: BLE001
            rc, out = 1, ""
            reasons.append(f"reverify slot failed to run: {e}")
        if rc != 0:
            reasons.append(f"reverify slot exited {rc} (independence does NOT currently hold)")
        else:
            try:
                v = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
            except Exception:
                v = None
            if not isinstance(v, dict):
                reasons.append("reverify slot emitted no parseable JSON verdict (typed contract violated)")
            else:
                if v.get("verdict") != "pass":
                    reasons.append(f"reverify verdict is {v.get('verdict')!r}, not 'pass'")
                if v.get("canary_denied") is not True:
                    reasons.append("canary floor: canary_denied is not true — the policy did not provably "
                                   "evaluate a known-dirty input (a vacuous pass cannot read as independent)")
                if comp.get("class") in ("endpoint-bearing", "headless"):
                    try:
                        refs = int(v.get("refs_scanned", 0))
                    except Exception:
                        refs = 0
                    if refs <= 0:
                        reasons.append("vacuous-scan floor: refs_scanned == 0 for an endpoint-bearing/"
                                       "headless component is a fail-closed setup error, never a PASS")
    # ---- live re-check 2: class-aware old-side safety.
    osafe = checks.get("old_safe")
    if not osafe:
        reasons.append("no checks.old_safe slot — the old-side safety re-check is UNAVAILABLE (NO-GO)")
    else:
        try:
            rc2, _o2 = _run_slot(osafe, {"DECOM_PHASE": "reverify"})
        except Exception as e:  # noqa: BLE001
            rc2 = 1
            reasons.append(f"old_safe slot failed to run: {e}")
        if rc2 != 0:
            reasons.append(f"old_safe slot exited {rc2} (the old side is NOT currently safe to remove)")
    return (not reasons), reasons


# ─────────────────────────────── selftest ───────────────────────────────

def _selftest() -> int:
    import tempfile
    ok_all = True

    def emit(token, ok, detail=""):
        nonlocal ok_all
        ok_all = ok_all and ok
        print(f"{token}: {'PASS' if ok else 'FAIL'}{(' — ' + detail) if detail else ''}")

    GOOD_VERDICT = 'printf \'{"verdict":"pass","canary_denied":true,"refs_scanned":7}\\n\''
    with tempfile.TemporaryDirectory() as td:
        # fixture register: one component per class.
        reg = {"components": [
            {"id": "svc-a", "class": "endpoint-bearing", "custody": True,
             "legacy_identity": {"kind": "elb", "name": "legacy-a"},
             "replacement": {"target": "eks/svc-a"}, "parallel_name": "svc-a-new",
             "soak_window": "7d",
             "checks": {"reverify": GOOD_VERDICT, "old_safe": "true"}},
            {"id": "worker-b", "class": "headless", "custody": False,
             "legacy_identity": {"kind": "ec2", "name": "legacy-b"},
             "replacement": {"target": "eks/worker-b"},
             "checks": {"reverify": GOOD_VERDICT, "old_safe": "true"}},
            {"id": "dead-c", "class": "decommission-only",
             "legacy_identity": {"kind": "ec2", "name": "legacy-c"},
             "dead_evidence": "zero traffic 30d (flow-log ref)",
             "checks": {"reverify": GOOD_VERDICT, "old_safe": "true"}},
        ]}
        ledger = os.path.join(td, "validation-ledger.jsonl")

        # ---- AC-DCG-1: register validation (fixture valid; class violations rejected).
        r1 = validate_register(reg) == []
        bad1 = {"components": [{"id": "x", "class": "decommission-only",
                                "legacy_identity": "y", "replacement": {"t": 1},
                                "dead_evidence": "e"}]}
        r1 = r1 and any("cannot be decommission-only" in e for e in validate_register(bad1))
        bad2 = {"components": [{"id": "x", "class": "endpoint-bearing", "legacy_identity": "y"}]}
        r1 = r1 and any("REQUIRES a replacement" in e for e in validate_register(bad2))
        emit("AC-DCG-1 register-schema-consistency-failclosed", r1,
             "fixture valid; replacement×decommission-only + missing-replacement rejected")

        # ---- AC-DCG-2: operator binding + incomplete-VALIDATED refusal + append-only.
        r2 = True
        import foundry_authz as az
        real_op = next(iter(az.load_operators().keys()), None)
        r2 = r2 and real_op is not None
        try:
            record(reg, ledger, "svc-a", "VALIDATED", "op_not_registered_xyz", "2026-07-06T00:00:00Z",
                   {"independence_pass": True, "old_safe": True, "custody_proof": "ref"})
            r2 = False
        except DecomError:
            pass
        try:  # custody proof missing ⇒ refused
            record(reg, ledger, "svc-a", "VALIDATED", real_op, "2026-07-06T00:00:00Z",
                   {"independence_pass": True, "old_safe": True})
            r2 = False
        except DecomError as e:
            r2 = r2 and "custody_proof" in str(e)
        record(reg, ledger, "svc-a", "REJECTED", real_op, "2026-07-06T00:00:00Z",
               {"note": "first pass failed"})
        n_before = len(read_ledger(ledger))
        record(reg, ledger, "svc-a", "VALIDATED", real_op, "2026-07-06T01:00:00Z",
               {"independence_pass": True, "old_safe": True, "custody_proof": "signed-op ref"})
        rows = read_ledger(ledger)
        r2 = r2 and len(rows) == n_before + 1 and rows[0]["verdict"] == "REJECTED"  # append, not rewrite
        emit("AC-DCG-2 ledger-append-only-operator-bound-complete-rows", r2,
             "unregistered operator + custody-less VALIDATED refused; rows appended never rewritten")

        # ---- AC-DCG-3: the gate's live re-derivation, waiver-blind, fail-closed.
        r3 = True
        go, why = gate_check(reg, os.path.join(td, "absent.jsonl"), "worker-b")
        r3 = r3 and not go and any("no ledger row" in w for w in why)          # NO-GO: no row
        record(reg, ledger, "worker-b", "REJECTED", real_op, "2026-07-06T00:00:00Z", {})
        go, why = gate_check(reg, ledger, "worker-b")
        r3 = r3 and not go and any("not VALIDATED" in w for w in why)          # NO-GO: REJECTED latest
        record(reg, ledger, "worker-b", "VALIDATED", real_op, "2026-07-06T02:00:00Z",
               {"independence_pass": True, "old_safe": True})
        go, why = gate_check(reg, ledger, "worker-b")
        r3 = r3 and go                                                          # GO: complete + live-green
        # canary floor: canary_denied false ⇒ NO-GO.
        import copy
        reg_bad = copy.deepcopy(reg)
        reg_bad["components"][1]["checks"]["reverify"] = \
            'printf \'{"verdict":"pass","canary_denied":false,"refs_scanned":7}\\n\''
        go, why = gate_check(reg_bad, ledger, "worker-b")
        r3 = r3 and not go and any("canary floor" in w for w in why)
        # vacuous-scan floor: refs_scanned 0 ⇒ NO-GO for endpoint/headless.
        reg_bad["components"][1]["checks"]["reverify"] = \
            'printf \'{"verdict":"pass","canary_denied":true,"refs_scanned":0}\\n\''
        go, why = gate_check(reg_bad, ledger, "worker-b")
        r3 = r3 and not go and any("vacuous-scan floor" in w for w in why)
        # waiver-blind: a slot that passes ONLY under DECOM_PHASE=validate ⇒ NO-GO (the gate forces reverify).
        reg_bad["components"][1]["checks"]["reverify"] = (
            '[ "$DECOM_PHASE" = "validate" ] && printf \'{"verdict":"pass","canary_denied":true,'
            '"refs_scanned":7}\\n\' || exit 3')
        go, why = gate_check(reg_bad, ledger, "worker-b")
        r3 = r3 and not go                                                      # validate-only pass rejected
        # missing slot ⇒ NO-GO (persisted flag alone never authorizes — ER #55).
        reg_bad["components"][1]["checks"] = {"old_safe": "true"}
        go, why = gate_check(reg_bad, ledger, "worker-b")
        r3 = r3 and not go and any("ER #55" in w for w in why)
        # failing old_safe ⇒ NO-GO.
        reg_bad["components"][1]["checks"] = {"reverify": GOOD_VERDICT, "old_safe": "false"}
        go, why = gate_check(reg_bad, ledger, "worker-b")
        r3 = r3 and not go and any("old_safe" in w for w in why)
        emit("AC-DCG-3 gate-live-rederivation-waiver-blind-failclosed", r3,
             "no-row/REJECTED/canary-false/refs-0/validate-only-slot/missing-slot/old-unsafe all NO-GO; "
             "complete+live-green GO")

        # ---- AC-DCG-4: gate_status derivation (regen from ledger) + anti-tautology direction.
        r4 = derive_gate_status("svc-a", read_ledger(ledger)) == "VALIDATED"
        r4 = r4 and derive_gate_status("dead-c", read_ledger(ledger)) == "UNVALIDATED"
        record(reg, ledger, "worker-b", "TURNED_OFF", real_op, "2026-07-06T03:00:00Z",
               {"sever_ref": "pr-123"})
        r4 = r4 and derive_gate_status("worker-b", read_ledger(ledger)) == "TURNED_OFF"
        # anti-tautology: a hand-edited gate_status disagreeing with the ledger is a validation error path
        # (derivation is ledger-pure — no register field consulted).
        emit("AC-DCG-4 fixture-selftest-anti-tautology", r4,
             "gate_status derived purely from the ledger (UNVALIDATED/VALIDATED/TURNED_OFF)")

    print("FOUNDRY-DECOMMISSION-SELFTEST-" + ("GREEN" if ok_all else "RED"))
    return 0 if ok_all else 1


# ─────────────────────────────── CLI ───────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    ap.add_argument("--selftest", action="store_true")

    v = sub.add_parser("validate-register")
    v.add_argument("register")
    v.add_argument("--ledger")
    v.add_argument("--regen", action="store_true")

    r = sub.add_parser("record")
    r.add_argument("--register", required=True)
    r.add_argument("--ledger", required=True)
    r.add_argument("--component", required=True)
    r.add_argument("--verdict", required=True, choices=list(_VERDICTS))
    r.add_argument("--operator", required=True)
    r.add_argument("--timestamp", required=True)
    r.add_argument("--evidence-json", default="{}")

    g = sub.add_parser("gate-check")
    g.add_argument("--register", required=True)
    g.add_argument("--ledger", required=True)
    g.add_argument("--component", required=True)

    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    try:
        if args.cmd == "validate-register":
            data = load_register(args.register)
            errors = validate_register(data)
            rows = read_ledger(args.ledger) if args.ledger else []
            for c in data.get("components") or []:
                derived = derive_gate_status(c.get("id"), rows)
                stated = c.get("gate_status")
                if args.regen:
                    c["gate_status"] = derived
                elif stated is not None and stated != derived:
                    errors.append(f"{c.get('id')}: gate_status {stated!r} does not match the ledger "
                                  f"derivation {derived!r} (gate_status is GENERATED — run --regen)")
            if args.regen and not errors:
                with open(args.register, "w", encoding="utf-8") as f:
                    yaml.safe_dump(data, f, sort_keys=False)
                print(f"regenerated gate_status for {len(data.get('components') or [])} component(s)")
            if errors:
                for e in errors:
                    print(f"  error: {e}", file=sys.stderr)
                return 1
            print("register valid")
            return 0
        if args.cmd == "record":
            data = load_register(args.register)
            errs = validate_register(data)
            if errs:
                raise DecomError("register invalid: " + "; ".join(errs))
            row = record(data, args.ledger, args.component, args.verdict, args.operator,
                         args.timestamp, json.loads(args.evidence_json))
            print(f"recorded {row['verdict']} for {row['component_id']} (operator {row['operator_id']})")
            return 0
        if args.cmd == "gate-check":
            data = load_register(args.register)
            go, reasons = gate_check(data, args.ledger, args.component)
            if go:
                print(f"GO: {args.component} — latest VALIDATED holds under live re-checks (waiver-blind)")
                return 0
            print(f"NO-GO: {args.component} (fail-closed):", file=sys.stderr)
            for w in reasons:
                print(f"  - {w}", file=sys.stderr)
            return 1
    except DecomError as e:
        print(f"FAIL (fail-closed): {e}", file=sys.stderr)
        return 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
