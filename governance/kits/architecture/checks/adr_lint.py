#!/usr/bin/env python3
"""ADR lint for MADR files docs/decisions/NNNN-title.md.

  adr_lint.py [DIR]      (default docs/decisions)

Fails (exit 1, one line per finding naming the file) on:
  - a missing or unknown `status` in the front matter
  - a number used by more than one file
  - a `superseded-by` link that does not resolve to another ADR
  - an accepted ADR without `confirmation.check_id`
Exit 2 when DIR is not a directory. 0000-template.md is skipped.
"""
import re
import sys
from pathlib import Path

import yaml

NAME_RE = re.compile(r"^(\d{4})-.+\.md$")
STATUSES = {"proposed", "accepted", "rejected", "deprecated", "superseded"}


def front_matter(text):
    m = re.match(r"\A---\n(.*?)\n---\s*(\n|\Z)", text, re.S)
    if not m:
        return None
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def raw_superseded_by(text):
    """Read superseded-by as written: YAML would turn 0010 into the octal integer 8."""
    m = re.search(r"^superseded-by:[ \t]*[\"']?([^\s\"'#]*)", text.split("\n---", 1)[0], re.M)
    return m.group(1) if m else ""


def lint(directory):
    d = Path(directory)
    files = sorted(p for p in d.glob("*.md") if NAME_RE.match(p.name) and not p.name.startswith("0000-"))
    findings, by_num = [], {}
    for p in files:
        by_num.setdefault(NAME_RE.match(p.name).group(1), []).append(p)
    for num, ps in sorted(by_num.items()):
        if len(ps) > 1:
            for p in ps:
                others = ", ".join(q.name for q in ps if q is not p)
                findings.append(f"{p.name}: number {num} reused (also {others})")
    for p in files:
        text = p.read_text(encoding="utf-8")
        fm = front_matter(text)
        if fm is None:
            findings.append(f"{p.name}: missing front matter")
            continue
        status = fm.get("status")
        if not status:
            findings.append(f"{p.name}: missing status")
            continue
        if str(status) not in STATUSES:
            findings.append(f"{p.name}: unknown status {status!r}")
        link = raw_superseded_by(text)
        if status == "superseded" or link:
            target = {q.name for q in files if q is not p and (q.name == link or q.name.startswith(link + "-"))} if link else set()
            if not link:
                findings.append(f"{p.name}: superseded without superseded-by")
            elif not target:
                findings.append(f"{p.name}: broken superseded-by {link}")
        if status == "accepted":
            conf = fm.get("confirmation")
            if not (isinstance(conf, dict) and conf.get("check_id")):
                findings.append(f"{p.name}: accepted without confirmation.check_id")
    return findings


def main(argv):
    target = argv[0] if argv else "docs/decisions"
    if not Path(target).is_dir():
        print(f"adr-lint: not a directory: {target}", file=sys.stderr)
        return 2
    findings = lint(target)
    for f in findings:
        print(f)
    if not findings:
        print("adr-lint: clean")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
