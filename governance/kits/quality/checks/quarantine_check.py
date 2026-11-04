#!/usr/bin/env python3
"""Check a flaky-test quarantine record file against quarantine.schema.json.

Usage: quarantine_check.py <file.json|file.yaml> [--today YYYY-MM-DD]
Exit 0: every record is valid and unexpired (a record whose expiry is today is still valid).
Exit 1: a finding, one per line as "<test>: <field>: <problem>" (missing field, bad date, unknown key,
expiry before entry, or "expired on <date>").
Exit 2: the file cannot be read or parsed; one "refused: <type>: <detail>" line, no traceback.
Quarantine is not skip: this check never removes a test from the run.
"""
import argparse
import datetime
import json
import re
import sys
from pathlib import Path

REQUIRED = ("test", "owner", "issue", "entry", "expiry")
KNOWN = REQUIRED + ("retry_budget",)
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


class Refused(Exception):
    pass


def load(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise Refused(f"unreadable-file: {exc}")
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            raise Refused("pyyaml-unavailable: install PyYAML to read YAML records")
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise Refused(f"yaml-parse-error: {str(exc).splitlines()[0] if str(exc) else 'invalid YAML'}")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise Refused(f"json-parse-error: {exc}")


def as_date(v):
    """A date from a YYYY-MM-DD string or a YAML date; None when it is neither."""
    if isinstance(v, datetime.datetime):
        return None
    if isinstance(v, datetime.date):
        return v
    if isinstance(v, str) and DATE_RE.fullmatch(v):
        try:
            return datetime.date.fromisoformat(v)
        except ValueError:
            return None
    return None


def check(doc, today: datetime.date) -> list:
    """Findings as strings; empty means clean."""
    out = []
    recs = doc.get("quarantine") if isinstance(doc, dict) else None
    if not isinstance(recs, list):
        return ["quarantine: quarantine: missing list"]
    if isinstance(doc, dict):
        out += [f"quarantine: {k}: unknown field" for k in doc if k != "quarantine"]
    for i, r in enumerate(recs):
        if not isinstance(r, dict):
            out.append(f"#{i}: record: not a mapping")
            continue
        name = r["test"] if isinstance(r.get("test"), str) and r["test"].strip() else f"#{i}"
        for f in REQUIRED:
            v = r.get(f)
            if f in ("entry", "expiry"):
                if v is None or v == "":
                    out.append(f"{name}: {f}: missing")
                elif as_date(v) is None:
                    out.append(f"{name}: {f}: not a YYYY-MM-DD date")
            elif not isinstance(v, str) or not v.strip():
                out.append(f"{name}: {f}: missing")
        out += [f"{name}: {k}: unknown field" for k in r if k not in KNOWN]
        rb = r.get("retry_budget", 0)
        if isinstance(rb, bool) or rb not in (0, 1):
            out.append(f"{name}: retry_budget: must be 0 or 1")
        entry, expiry = as_date(r.get("entry")), as_date(r.get("expiry"))
        if entry and expiry:
            if expiry < entry:
                out.append(f"{name}: expiry: before entry")
            elif expiry < today:
                out.append(f"{name}: expiry: expired on {expiry.isoformat()}")
        elif expiry and expiry < today:
            out.append(f"{name}: expiry: expired on {expiry.isoformat()}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--today")
    a = ap.parse_args(argv)
    try:
        try:
            today = datetime.date.fromisoformat(a.today) if a.today else datetime.date.today()
        except ValueError:
            raise Refused(f"bad-today: {a.today!r} is not YYYY-MM-DD")
        doc = load(Path(a.file))
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    findings = check(doc, today)
    for f in findings:
        print(f)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
