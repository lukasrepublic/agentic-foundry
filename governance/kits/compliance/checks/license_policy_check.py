#!/usr/bin/env python3
"""FG-LIC-001 check: evaluate a package list against a license policy. Deny unknown.

Usage: license_policy_check.py POLICY.yaml PACKAGES.json [--today YYYY-MM-DD]
PACKAGES.json: a list of {"name": str, "license": SPDX expression}. Exit 0 pass, 1 violations, 2 bad input.
An expression with OR passes when any alternative is allowed; AND needs all. Unknown or empty denies.
"""
import datetime
import json
import re
import sys

import yaml


def _allowed_ids(policy):
    ids = set()
    for group in (policy.get("allow") or {}).values():
        ids.update(group or [])
    return ids


def _ok(expr, allowed, denied):
    expr = (expr or "").strip()
    if not expr:
        return False
    ors = [p for p in re.split(r"\s+OR\s+", expr.strip("() ")) if p]
    for alt in ors:
        ands = [a.strip("() ") for a in re.split(r"\s+AND\s+", alt)]
        if all(a in allowed and a not in denied for a in ands):
            return True
    return False


def evaluate(policy, packages, today=None):
    today = today or datetime.date.today()
    allowed, denied = _allowed_ids(policy), set(policy.get("deny") or [])
    excs = {}
    for e in policy.get("exceptions") or []:
        exp = e.get("expires")
        exp = exp if isinstance(exp, datetime.date) else datetime.date.fromisoformat(str(exp))
        if exp >= today and e.get("owner") and e.get("reason"):
            excs[(e["package"], e["license"])] = True
    out = []
    for p in packages:
        lic = p.get("license")
        if (p.get("name"), lic) in excs or _ok(lic, allowed, denied):
            continue
        out.append(f"{p.get('name')}: license {lic!r} is not allowed (deny unknown)")
    return out


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    today = None
    if "--today" in argv:
        today = datetime.date.fromisoformat(argv[argv.index("--today") + 1])
        args = [a for a in args if a != argv[argv.index("--today") + 1]]
    if len(args) != 2:
        print("usage: license_policy_check.py POLICY.yaml PACKAGES.json", file=sys.stderr)
        return 2
    try:
        policy = yaml.safe_load(open(args[0], encoding="utf-8"))
        packages = json.load(open(args[1], encoding="utf-8"))
        bad = evaluate(policy, packages, today)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    for line in bad:
        print(line)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
