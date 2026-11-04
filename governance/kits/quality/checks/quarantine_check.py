#!/usr/bin/env python3
"""Check a flaky-test quarantine record file against quarantine.schema.json.

Usage: quarantine_check.py <file.json|file.yaml> [--today YYYY-MM-DD]
Exit 0: every record is valid and unexpired. Exit 1: a missing or invalid field (the field is named)
or an expired entry (reported as "expired"). Exit 2: the file cannot be read.
Quarantine is not skip: this check never removes a test from the run.
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REQUIRED = ("test", "owner", "issue", "entry", "expiry")


def load(path: Path):
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        import yaml
        return yaml.safe_load(text)
    return json.loads(text)


def check(doc, today: datetime.date) -> list:
    """Findings as strings; empty means clean."""
    out = []
    recs = doc.get("quarantine") if isinstance(doc, dict) else None
    if not isinstance(recs, list):
        return ["quarantine: missing list"]
    for i, r in enumerate(recs):
        name = r.get("test", f"#{i}") if isinstance(r, dict) else f"#{i}"
        if not isinstance(r, dict):
            out.append(f"{name}: record: not a mapping")
            continue
        for f in REQUIRED:
            if not isinstance(r.get(f), str) or not r[f].strip():
                out.append(f"{name}: {f}: missing")
        if r.get("retry_budget", 0) not in (0, 1) or isinstance(r.get("retry_budget"), bool):
            out.append(f"{name}: retry_budget: must be 0 or 1")
        exp = r.get("expiry")
        if isinstance(exp, str) and exp.strip():
            try:
                if datetime.date.fromisoformat(exp) < today:
                    out.append(f"{name}: expiry: expired on {exp}")
            except ValueError:
                out.append(f"{name}: expiry: not a YYYY-MM-DD date")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--today")
    a = ap.parse_args(argv)
    try:
        today = datetime.date.fromisoformat(a.today) if a.today else datetime.date.today()
        doc = load(Path(a.file))
    except (OSError, ValueError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    findings = check(doc, today)
    for f in findings:
        print(f)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
