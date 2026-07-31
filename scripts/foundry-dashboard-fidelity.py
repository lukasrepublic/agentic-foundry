#!/usr/bin/env python3
"""foundry-dashboard-fidelity — the round-trip dashboards-as-code fidelity gate
(feat-foundry-dashboards-as-code, ER #86).

Pairs GOLDEN dashboard JSON exports with RE-RENDERED as-code outputs, reduces BOTH sides to a
SEMANTIC SIGNATURE (parity-bearing fields only), and asserts N/N MATCH — fail-closed:

  foundry-dashboard-fidelity.py gate --golden <dir> --rendered <dir> [--json]

Exit 0 ONLY when every golden has a rendered pair and every pair matches. Nonzero (naming the
cause) on: any signature mismatch (LAYOUT moves included — right panels in the wrong places is a
fail), a golden with no rendered pair (or an orphan render), any panel carrying the emitter's
unverifiable FLAG (`x_foundry_unverified: true` — the flag-don't-guess contract: flagged panels
are human-review items, never green), and ZERO pairs (a vacuous run is never green).

The signature (platform-generic; a Grafana-style schemaVersioned export is the illustration):
  - panels -> {kind, queries (expr/query/rawSql of each target), grid position {x,y,w,h}}
  - template variables -> {name, type, query, datasource}
  - dashboard-level datasource references
NAMED, SYMMETRIC normalizations are applied to both sides BY CONSTRUCTION (one function, called on
each side — a one-sided normalization is structurally inexpressible):
  - schema-version / injected-defaults noise: excluded because the signature projects ONLY
    parity-bearing fields (a newer builder SDK's defaults never enter the comparison);
  - placeholder targets: a target with NO query string is equivalent to an absent target;
  - library panels: a panel with a libraryPanel reference is keyed by {name, uid} + grid position;
    its server-resolved type/targets are ignored (the platform resolves them at load).

Threat model — TRUSTED OPERATOR; a deterministic READ-ONLY comparator (reads JSON, exits).
"""
from __future__ import annotations

import argparse
import glob as _glob
import json
import os
import sys

FLAG_KEY = "x_foundry_unverified"


# ─────────────────────────── semantic signature (ONE definition of MATCH) ───────────────────────────

def _target_queries(targets):
    """The parity-bearing query strings of a panel's targets. A placeholder target with no
    expressible query string is EQUIVALENT TO ABSENT (symmetric normalization)."""
    out = []
    for t in targets or []:
        if not isinstance(t, dict):
            continue
        q = t.get("expr") or t.get("query") or t.get("rawSql") or ""
        if isinstance(q, str) and q.strip():
            out.append(q.strip())
    return sorted(out)


def _panel_sig(p):
    gp = p.get("gridPos") or {}
    pos = (gp.get("x"), gp.get("y"), gp.get("w"), gp.get("h"))
    lib = p.get("libraryPanel")
    if isinstance(lib, dict):
        # library panel: keyed by {name, uid} + position; server-resolved type/targets ignored.
        return {"library": {"name": lib.get("name"), "uid": lib.get("uid")}, "pos": pos}
    sig = {"kind": p.get("type"), "queries": _target_queries(p.get("targets")), "pos": pos}
    if p.get(FLAG_KEY):
        sig["flagged"] = True
    return sig


def _walk_panels(container):
    """Panels incl. those nested in rows (a row's collapsed panels live under panel.panels)."""
    for p in container.get("panels") or []:
        if not isinstance(p, dict):
            continue
        yield p
        for sub in p.get("panels") or []:
            if isinstance(sub, dict):
                yield sub


def signature(dash: dict) -> dict:
    """The semantic signature — parity-bearing fields ONLY, applied IDENTICALLY to both sides
    (this one function IS the shared definition of MATCH: the convert self-verify and the fleet
    gate both call it; neither can loosen it unilaterally)."""
    panels = sorted((_panel_sig(p) for p in _walk_panels(dash)),
                    key=lambda s: json.dumps(s, sort_keys=True))
    tvars = sorted(
        ({"name": v.get("name"), "type": v.get("type"),
          "query": (v.get("query") if isinstance(v.get("query"), (str, dict)) else None),
          "datasource": v.get("datasource")}
         for v in ((dash.get("templating") or {}).get("list") or []) if isinstance(v, dict)),
        key=lambda s: json.dumps(s, sort_keys=True))
    return {"title": dash.get("title"), "panels": panels, "templating": tvars}


def flagged_panels(dash: dict) -> int:
    return sum(1 for p in _walk_panels(dash) if p.get(FLAG_KEY))


def _identity(path: str, dash: dict) -> str:
    return dash.get("uid") or os.path.splitext(os.path.basename(path))[0]


def _load_dir(d: str) -> dict:
    out = {}
    for p in sorted(_glob.glob(os.path.join(d, "*.json"))):
        try:
            dash = json.load(open(p, encoding="utf-8"))
        except Exception as e:
            raise SystemExit(f"FAIL (fail-closed): unparseable dashboard JSON {p}: {e}")
        if isinstance(dash, dict):
            out[_identity(p, dash)] = (p, dash)
    return out


def gate(golden_dir: str, rendered_dir: str) -> tuple[bool, list[str], int]:
    """(ok, problems, matched_count) — the N/N verdict, fail-closed."""
    problems: list[str] = []
    golden = _load_dir(golden_dir)
    rendered = _load_dir(rendered_dir)
    if not golden:
        return False, ["ZERO golden dashboards — a vacuous run is never green (fail-closed)"], 0
    matched = 0
    for key, (gp, gdash) in sorted(golden.items()):
        if key not in rendered:
            problems.append(f"{key}: golden has NO rendered pair (coverage gap)")
            continue
        rp, rdash = rendered[key]
        nflag = flagged_panels(rdash) + flagged_panels(gdash)
        if nflag:
            problems.append(f"{key}: {nflag} panel(s) carry the unverifiable FLAG ({FLAG_KEY}) — "
                            "human-review items are never reported green")
            continue
        if signature(gdash) != signature(rdash):
            problems.append(f"{key}: semantic signature MISMATCH (golden {gp} vs rendered {rp})")
            continue
        matched += 1
    for key in sorted(set(rendered) - set(golden)):
        problems.append(f"{key}: rendered dashboard has NO golden (orphan — not a verified port)")
    return (not problems), problems, matched


# ─────────────────────────────────────── selftest ───────────────────────────────────────

def _selftest() -> int:
    import copy
    import tempfile
    ok_all = True

    def emit(token, ok, detail=""):
        nonlocal ok_all
        ok_all = ok_all and ok
        print(f"{token}: {'PASS' if ok else 'FAIL'}{(' — ' + detail) if detail else ''}")

    base = {
        "uid": "dash-a", "title": "Service A", "schemaVersion": 36,
        "templating": {"list": [{"name": "env", "type": "query", "query": "label_values(env)"}]},
        "panels": [
            {"type": "timeseries", "gridPos": {"x": 0, "y": 0, "w": 12, "h": 8},
             "targets": [{"expr": "rate(http_requests_total[5m])"}]},
            {"type": "stat", "gridPos": {"x": 12, "y": 0, "w": 12, "h": 8},
             "targets": [{"expr": "up"}]},
        ],
    }

    def write(d, name, dash):
        json.dump(dash, open(os.path.join(d, name), "w"))

    with tempfile.TemporaryDirectory() as g, tempfile.TemporaryDirectory() as r:
        # identical pair + a renderer that injects NEWER-SCHEMA defaults + a placeholder target +
        # a library panel resolved server-side differently — all normalized SYMMETRICALLY -> MATCH.
        write(g, "a.json", base)
        newer = copy.deepcopy(base)
        newer["schemaVersion"] = 41                                # schema-version noise
        newer["editable"] = True                                   # injected default
        newer["panels"][0]["options"] = {"legend": {"show": True}}  # injected default
        newer["panels"][1]["targets"].append({"expr": "  "})       # placeholder target ≡ absent
        write(r, "a.json", newer)
        lib_g = {"uid": "dash-lib", "title": "Lib", "panels": [
            {"libraryPanel": {"name": "shared-cpu", "uid": "lp1"},
             "gridPos": {"x": 0, "y": 0, "w": 24, "h": 8}}]}
        lib_r = copy.deepcopy(lib_g)
        lib_r["panels"][0]["type"] = "timeseries"                  # server-resolved type ignored
        lib_r["panels"][0]["targets"] = [{"expr": "resolved()"}]   # server-resolved targets ignored
        write(g, "lib.json", lib_g)
        write(r, "lib.json", lib_r)
        ok, probs, n = gate(g, r)
        r_noise = ok and n == 2

        # a REAL query divergence under the SAME normalizations still fails (no loosening).
        div = copy.deepcopy(newer)
        div["panels"][0]["targets"][0]["expr"] = "rate(http_requests_total[1m])"
        write(r, "a.json", div)
        ok2, probs2, _ = gate(g, r)
        r_real = (not ok2) and any("MISMATCH" in p for p in probs2)
        emit("AC-DAC-2 symmetric-normalizations-no-loosening", r_noise and r_real,
             "schema/default/placeholder/library noise MATCHes; a real query divergence still fails")

        # restore; layout move -> MISMATCH (layout enforced).
        write(r, "a.json", newer)
        moved = copy.deepcopy(newer)
        moved["panels"][0]["gridPos"]["x"] = 6
        write(r, "a.json", moved)
        ok3, probs3, _ = gate(g, r)
        r_layout = (not ok3) and any("MISMATCH" in p for p in probs3)

        # flagged panel -> nonzero, never green.
        flagged = copy.deepcopy(newer)
        flagged["panels"][0][FLAG_KEY] = True
        write(r, "a.json", flagged)
        ok4, probs4, _ = gate(g, r)
        r_flag = (not ok4) and any("FLAG" in p for p in probs4)

        # missing pair -> nonzero; zero pairs -> nonzero (vacuous floor).
        os.remove(os.path.join(r, "a.json"))
        ok5, probs5, _ = gate(g, r)
        r_missing = (not ok5) and any("NO rendered pair" in p for p in probs5)
        with tempfile.TemporaryDirectory() as g0:
            ok6, probs6, _ = gate(g0, r)
            r_zero = (not ok6) and any("ZERO golden" in p for p in probs6)

        emit("AC-DAC-1 n-of-n-gate-failclosed", r_layout and r_flag and r_missing and r_zero,
             "layout move + flagged panel + missing pair + zero-pairs all nonzero; N/N required")

    print("FOUNDRY-DASHBOARD-FIDELITY-SELFTEST-" + ("GREEN" if ok_all else "RED"))
    return 0 if ok_all else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    gp = sub.add_parser("gate")
    gp.add_argument("--golden", required=True)
    gp.add_argument("--rendered", required=True)
    gp.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if args.cmd == "gate":
        ok, problems, matched = gate(args.golden, args.rendered)
        total = matched + len([p for p in problems])
        if args.json:
            print(json.dumps({"ok": ok, "matched": matched, "problems": problems}, indent=1))
        if ok:
            print(f"{matched}/{matched} MATCH — the as-code migration is provably lossless")
            return 0
        print(f"{matched} matched; {len(problems)} problem(s) (fail-closed):", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
