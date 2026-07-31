#!/usr/bin/env python3
"""foundry_pr_review — record/present for the pr-reviewer governance subagent (UL-0018, Phase 1).

The general-code-review analog of foundry_security_review's recording half. The review JUDGMENT is the
`pr-reviewer` agent (a read-only separate-context pass); this library records its result so "a code review
happened" is auditable and exposes a presence read:

  1. record_review(...) — appends a `pr-review` entry to the shared trail via foundry_audit_log
     (fail-closed, read-back-confirmed). Verdict is DERIVED from findings (`block` if ANY Block finding,
     else `pass`); REFUSES (raises) to record a `pass` when a Block finding is present.
  2. pr_review_present(spec_sha256, contract_sha256) — returns (ok, reason): ok=True iff a passing
     `pr-review` entry exists for that (spec, contract).

Distinct from security-review by the `action` discriminator ("pr-review" vs "security-review") — both share
the audit trail but their presence reads never cross-satisfy.

Threat model — TRUSTED OPERATOR (memory `staged-security-threat-model`): an advisory mistake-catcher FOR the
operator; no SoD / forgery defenses. Records reference location + rationale only.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import foundry_audit_log as audit  # noqa: E402  (record-before-action append + path resolution)

ACTION = "pr-review"


def _derive_verdict(findings) -> str:
    return "block" if any(f.get("severity") == "Block" for f in (findings or [])) else "pass"


def record_review(spec_ref, spec_sha256, contract_sha256, operator_id, findings, *, adopter_root):
    """Append a `pr-review` entry to the shared audit trail (fail-closed, read-back-confirmed via
    foundry_audit_log). Verdict derived from findings — `block` if ANY Block finding present, else `pass`.
    REFUSES (raises ValueError) to write `pass` when a Block finding is present (fail-closed coupling in
    code, not operator discipline). Returns the assigned record_id."""
    findings = list(findings or [])
    verdict = _derive_verdict(findings)
    if verdict == "pass" and any(f.get("severity") == "Block" for f in findings):
        raise ValueError("refusing to record verdict=pass with a Block finding present")
    record = {
        "action": ACTION,
        "spec_ref": spec_ref,
        "spec_sha256": spec_sha256,
        "contract_sha256": contract_sha256,
        "operator_id": operator_id,
        "verdict": verdict,
        "findings": findings,
    }
    return audit.append_record(record, repo_root=adopter_root)


def _iter_records(adopter_root):
    """Read the audit trail directly (foundry_audit_log has no reader). Yields parsed dict records;
    silently skips unparseable lines. Returns nothing if the trail does not exist yet."""
    import json
    path = audit.audit_log_path(adopter_root)
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except (ValueError, TypeError):
                continue


def pr_review_present(spec_sha256, contract_sha256, *, adopter_root):
    """Presence read. Returns (ok, reason): ok=True iff a `pr-review` entry with verdict=pass exists for
    that (spec_sha256, contract_sha256); else (False, reason). A missing/empty trail → (False, reason)
    (graceful, never raises)."""
    saw_subject = False
    saw_block = False
    for rec in _iter_records(adopter_root):
        if rec.get("action") != ACTION:
            continue
        if rec.get("spec_sha256") != spec_sha256 or rec.get("contract_sha256") != contract_sha256:
            continue
        saw_subject = True
        if rec.get("verdict") == "pass":
            return True, "a passing pr-review is recorded for this (spec, contract)"
        if rec.get("verdict") == "block":
            saw_block = True
    if saw_block:
        return False, "only block-verdict pr-review entries recorded for this (spec, contract)"
    if saw_subject:
        return False, "a pr-review entry exists but with no pass verdict for this (spec, contract)"
    return False, "no pr-review recorded for this (spec, contract)"
