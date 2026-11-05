#!/usr/bin/env python3
"""foundry_ci_budget_lint — a performance or request-count budget is a tool, not a gate (rule C2, gv-16).

    foundry_ci_budget_lint.py WORKFLOW_FILE_OR_DIR [...] [--required NAME ...] [--required-from FILE] [--json]

Reads CI workflow files and flags a job that measures a performance or request-count budget (Lighthouse CI, a bundle
size limit, a k6 threshold, a "performance budget" or "max requests" step) AND is wired as a required check. A budget
that blocks every merge on a noisy, environment-dependent number teaches people to retry and then to disable it; as a
non-required REPORT it informs the review without blocking. READ-ONLY: it parses YAML and prints findings.

Which jobs are required:
  * with `--required NAME` (repeatable) or `--required-from FILE` (a JSON list of check names, or a ruleset /
    branch-protection document that carries `required_status_checks` / `context` entries), a budget job is flagged
    exactly when its check name (the job's `name:`, else its id) is in that set;
  * with neither, the lint cannot see branch protection, so it flags a budget job that blocks by construction:
    one that does not set `continue-on-error: true`. A budget job marked `continue-on-error: true` passes.

Exit: 0 no budget gate found, 1 at least one found (each named with file, job and the matching text), 2 bad input.
Stdlib + PyYAML.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

BUDGET_RE = re.compile(
    r"(lighthouse|\blhci\b|size[-_ ]limit|bundle[-_ ]?size|bundlewatch|perf(?:ormance)?[-_ ]budget|request[-_ ]count|"
    r"max[-_ ]requests?|requests?[-_ ]budget|\bk6\b|--budget\b|web[-_ ]vitals|\bttfb\b|\blcp\b|load[-_ ]?test[-_ ]threshold)",
    re.I)


def _required_names(required, required_from) -> set | None:
    names = {str(x) for x in (required or [])}
    if required_from:
        doc = json.loads(Path(required_from).read_text(encoding="utf-8"))
        found = []

        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k == "context" and isinstance(v, str):
                        found.append(v)
                    elif k == "contexts" and isinstance(v, list):
                        found.extend(str(x) for x in v)
                    else:
                        walk(v)
            elif isinstance(o, list):
                for x in o:
                    walk(x)
        if isinstance(doc, list) and all(isinstance(x, str) for x in doc):
            found = list(doc)
        else:
            walk(doc)
        names |= set(found)
    return names if (required or required_from) else None


def _job_text(job: dict) -> list:
    """(label, text) pairs: the job's name and each step's name / run / uses / with."""
    out = [("job name", str(job.get("name") or ""))]
    for i, st in enumerate(job.get("steps") or []):
        if not isinstance(st, dict):
            continue
        for key in ("name", "run", "uses"):
            if st.get(key):
                out.append((f"step {i + 1} {key}", str(st[key])))
        if isinstance(st.get("with"), dict):
            out.append((f"step {i + 1} with", json.dumps(st["with"])))
    return out


def lint_file(path: Path, required: set | None) -> list:
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise ValueError(f"{path}: unreadable workflow ({e})")
    if not isinstance(doc, dict) or not isinstance(doc.get("jobs"), dict):
        return []
    findings = []
    for jid, job in doc["jobs"].items():
        if not isinstance(job, dict):
            continue
        hits = [(lab, txt) for lab, txt in [("job id", str(jid))] + _job_text(job) if BUDGET_RE.search(txt)]
        if not hits:
            continue
        check_name = str(job.get("name") or jid)
        if required is not None:
            is_required = check_name in required or str(jid) in required
            basis = "named in the required checks"
        else:
            coe = job.get("continue-on-error")
            is_required = not (coe is True or str(coe).lower() == "true")
            basis = "blocks by construction (no continue-on-error: true) and no required-check list was given"
        if is_required:
            lab, txt = sorted(hits, key=lambda h: h[0] == "job id")[0]      # a step or the job name before a bare id
            findings.append({"file": str(path), "job": str(jid), "check": check_name, "match": f"{lab}: {txt.strip()[:120]}",
                             "reason": f"a performance or request-count budget is wired as a required check ({basis}); "
                                       "make it a non-required report (rule C2: budgets are tools, not gates)"})
    return findings


def collect(paths) -> list:
    files = []
    for p in map(Path, paths):
        if p.is_dir():
            files += sorted(x for x in p.rglob("*") if x.suffix in (".yml", ".yaml") and x.is_file())
        elif p.is_file():
            files.append(p)
        else:
            raise ValueError(f"{p}: no such file or directory")
    return files


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="foundry_ci_budget_lint.py", description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--required", action="append", default=[], metavar="NAME")
    ap.add_argument("--required-from", metavar="FILE")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        required = _required_names(a.required, a.required_from)
        findings = []
        for f in collect(a.paths):
            findings += lint_file(f, required)
    except (OSError, ValueError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps({"findings": findings, "required_known": required is not None}, indent=2))
    elif findings:
        for f in findings:
            print(f"budget-as-gate: {f['file']} job {f['job']} ({f['check']}): {f['reason']} [{f['match']}]")
    else:
        print("ci-budget-lint-ok")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
