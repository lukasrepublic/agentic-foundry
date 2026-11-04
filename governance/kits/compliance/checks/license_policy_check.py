#!/usr/bin/env python3
"""FG-LIC-001 check: evaluate a package list against a license policy. Deny unknown.

Usage: license_policy_check.py POLICY.yaml PACKAGES.json [--today YYYY-MM-DD] [--allow-empty]
PACKAGES.json: a list of {"name": str, "license": SPDX expression}.
Exit 0 pass, 1 violations, 2 refused (bad input, unparsed expression, empty package list without --allow-empty).

Expressions: `A OR B` passes when any alternative is allowed; `A AND B` needs all; AND binds tighter than OR.
`X WITH Y` is one unit and must itself be listed in the policy. Parentheses are refused (`unparsed-expression`),
never guessed at. Unknown or empty licenses are denied. Needs PyYAML.
"""
import argparse
import datetime
import json
import re
import sys

import yaml


class Refused(ValueError):
    pass


def _list(v, what):
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise Refused(f"{what}-not-a-list-of-strings")
    return v


def _allowed_ids(policy):
    allow = policy.get("allow") or {}
    if not isinstance(allow, dict):
        raise Refused("allow-not-a-mapping")
    ids = set()
    for name, group in allow.items():
        ids.update(_list(group, f"allow.{name}"))
    return ids


def _ok(expr, allowed, denied):
    if not isinstance(expr, str) or not expr.strip():
        return False
    if "(" in expr or ")" in expr:
        raise Refused(f"unparsed-expression: {expr!r}")
    for alt in re.split(r"\s+OR\s+", expr.strip()):
        units = [u.strip() for u in re.split(r"\s+AND\s+", alt)]
        if all(u and u in allowed and u not in denied for u in units):
            return True
    return False


def evaluate(policy, packages, today=None):
    if not isinstance(policy, dict):
        raise Refused("policy-not-a-mapping")
    if not isinstance(packages, list):
        raise Refused("packages-not-a-list")
    today = today or datetime.date.today()
    allowed, denied = _allowed_ids(policy), set(_list(policy.get("deny"), "deny"))
    excs = set()
    for e in policy.get("exceptions") or []:
        exp = e.get("expires")
        exp = exp if isinstance(exp, datetime.date) else datetime.date.fromisoformat(str(exp))
        if exp >= today and e.get("owner") and e.get("reason"):
            excs.add((e["package"], e["license"]))
    out = []
    for p in packages:
        if not isinstance(p, dict):
            raise Refused("package-entry-not-a-mapping")
        lic = p.get("license")
        if (p.get("name"), lic) in excs or _ok(lic, allowed, denied):
            continue
        out.append(f"{p.get('name')}: license {lic!r} is not allowed (deny unknown)")
    return out


def _date(s):
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: {s!r}")


def main(argv):
    ap = argparse.ArgumentParser(prog="license_policy_check.py")
    ap.add_argument("policy")
    ap.add_argument("packages")
    ap.add_argument("--today", type=_date)
    ap.add_argument("--allow-empty", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    try:
        with open(a.policy, encoding="utf-8") as f:
            policy = yaml.safe_load(f)
        with open(a.packages, encoding="utf-8") as f:
            packages = json.load(f)
        if isinstance(packages, list) and not packages and not a.allow_empty:
            raise Refused("empty-package-list (use --allow-empty if intended)")
        bad = evaluate(policy, packages, a.today)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    for line in bad:
        print(line)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
