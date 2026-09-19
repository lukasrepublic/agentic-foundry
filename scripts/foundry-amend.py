#!/usr/bin/env python3
"""foundry-amend — the mechanical half of /foundry:amend: one-step re-freeze of a living spec
(feat-foundry-authorization-amend-verb).

A spec is a living document: implementation reality routinely changes an authorized spec's
normative region or its contract. This CLI re-freezes that change in one step — recomputing the
hashes, bumping `auth_seq`, and recording the amendment — WITHOUT an operator step, UNLESS the
change widens a boundary or the contract is security-flagged, in which case it refuses and routes
to `/foundry:authorize`.

  foundry-amend.py --spec <spec.md> --contract <acceptance-contract.yaml> \
      [--ref HEAD] [--repo-root <path>] [--why "<free text>"]

The freeze logic is NEVER re-implemented here: this CLI applies the same freeze floors as
`/foundry:authorize` by calling `foundry_authz.validate_spec_contract` before any write, and
performs the actual freeze-write by calling `foundry_authz.authorize` (the SAME function
/foundry:authorize calls) — `auth_seq`/`supersedes` are derived server-side there from the prior
`authorized:` trailer, never accepted as CLI input, so they cannot be forged by the caller.

Widening classifier (AC-AMND-1):
  (a) the spec's normative-region text (diff printed for operator legibility only — not itself
      a widening signal).
  (b) the contract's BOUNDARY fields: scope, checkpoints[].intended, checkpoints[].ack,
      system_grounding, preconditions, build_gates, post_apply_checks, mandatory_review,
      requires_capabilities.
  (c) an IDENTIFIER TOKEN introduced or changed inside a checkpoint's locator/expect.value: a
      12-digit AWS account id, an `arn:`-prefixed token, a hostname/URL, or a token equal to a
      system_grounding.artifacts[].identifier value.
  (d) a CHECKPOINT-RIGOR REDUCTION: expect.value lowered, expect.op weakened, expect.baseline
      pre-change→none, or surface repointed.
A contract whose (candidate) mandatory_review names a `security` review ALWAYS routes to
/foundry:authorize (AC-AMND-3), independent of whether mandatory_review itself changed.

"Previous" state is resolved via `git show <ref>:<path>` (default ref: HEAD) — amend diffs the
working tree (the agent's in-place edit) against the last commit, which is expected to hold the
last-frozen state (the file `/foundry:authorize` or a prior `/foundry:amend` last wrote and the
operator/agent committed). There is no other durable record of "what changed" to diff against:
the frozen `authorized:` trailer stores only hashes, never the prior field values, by design
(scripts/foundry_authz.py — the freeze-write is never handed the old content to keep around).

AC-AMND-6: this CLI never imports or consults `foundry_audit_ledger`/the §8 audit-ledger row — a
non-widening amend completes with no audit-ledger row required or consulted.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foundry_contract as fc          # noqa: E402
import foundry_authz as az             # noqa: E402
import foundry_audit_log as al         # noqa: E402


class AmendError(Exception):
    pass


# --------------------------------------------------------------------------- #
# AC-AMND-1(b) — boundary fields (top-level; compared structurally, not just by
# presence/absence). `requires_capabilities` is R1 (not yet schema-present) — treated
# as absent-vs-absent (no diff) until it exists, per the spec's Clarifications.
# --------------------------------------------------------------------------- #
BOUNDARY_FIELDS = [
    "scope", "system_grounding", "preconditions", "build_gates",
    "post_apply_checks", "mandatory_review", "requires_capabilities",
]

# AC-AMND-1(d) — a fixed strictness ranking for the closed `expect.op` enum
# (non-empty/count_gte/equals/matches). A change to a STRICTLY LOWER rank is a
# weakening; ops off this ranking (future extension) never compare as weaker/stronger
# of each other, they only convict via a change AT ALL (handled by BOUNDARY diff, not
# this ranking) if they are otherwise unrecognized — see `_op_weakened`.
_OP_RANK = {"non-empty": 0, "matches": 1, "equals": 2, "count_gte": 3}

# AC-AMND-1(c) — the fixed, testable four identifier-token classes. Applied to the NEW
# value only (Design/notes): an identifier already present and unchanged is not a widening.
_ACCOUNT_ID_RE = re.compile(r"(?<!\d)\d{12}(?!\d)")
_ARN_RE = re.compile(r"\barn:[^\s\"',]+")
_HOSTNAME_URL_RE = re.compile(
    r"(?:https?://[^\s\"']+)"
    r"|(?:\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b)"
)


# --------------------------------------------------------------------------- #
# git-backed "previous state" resolution
# --------------------------------------------------------------------------- #
def git_show(repo_root: str, relpath: str, ref: str = "HEAD") -> "bytes | None":
    """The bytes of `relpath` at `ref`, or None when unresolvable (no repo, no such
    ref, or the file did not exist at that ref) — the caller treats None as fail-closed,
    never as "no prior content, so nothing changed"."""
    proc = subprocess.run(["git", "-C", repo_root, "show", f"{ref}:{relpath}"],
                           capture_output=True)
    if proc.returncode != 0:
        return None
    return proc.stdout


def _relpath(repo_root: str, path: str) -> str:
    return os.path.relpath(os.path.abspath(path), os.path.abspath(repo_root))


def _write_temp(raw: bytes, suffix: str) -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as fh:
        fh.write(raw)
    return path


def old_normative_bytes(old_spec_raw: "bytes | None") -> "bytes | None":
    """AC-AMND-1(a): the normative-region bytes of the PREVIOUS spec content, computed
    by the SAME `foundry_contract.spec_normative_bytes` /foundry:authorize's hash uses
    (never re-derived) — routed through a throwaway temp file since that helper reads a
    path, not bytes."""
    if old_spec_raw is None:
        return None
    tmp = _write_temp(old_spec_raw, ".md")
    try:
        return fc.spec_normative_bytes(tmp)
    finally:
        os.remove(tmp)


def old_contract_data(old_contract_raw: "bytes | None") -> "dict | None":
    """The parsed dict of the PREVIOUS contract content, via `foundry_contract.load_contract`
    (never re-derived) routed through a throwaway temp file."""
    if old_contract_raw is None:
        return None
    tmp = _write_temp(old_contract_raw, ".yaml")
    try:
        return fc.load_contract(tmp)
    finally:
        os.remove(tmp)


def unified_diff_text(old_bytes: "bytes | None", new_bytes: bytes) -> str:
    old_lines = (old_bytes or b"").decode("utf-8", errors="replace").splitlines(keepends=True)
    new_lines = new_bytes.decode("utf-8", errors="replace").splitlines(keepends=True)
    return "".join(difflib.unified_diff(
        old_lines, new_lines, fromfile="normative(prev)", tofile="normative(current)"))


# --------------------------------------------------------------------------- #
# AC-AMND-1(b)/(c)/(d) — the widening classifier
# --------------------------------------------------------------------------- #
def _leaf_diff(old, new, path: str) -> list:
    """Deep-diff two boundary-field values, reporting the deepest DOTTED path that
    differs (e.g. "scope.allowed_paths") — recurses through nested mappings, bottoms
    out (reports the whole sub-path) at the first non-mapping mismatch (a list or
    scalar), exactly matching the Scenario's `widened_fields: ["scope.allowed_paths"]`."""
    if old == new:
        return []
    if isinstance(old, dict) and isinstance(new, dict):
        out = []
        for k in sorted(set(old) | set(new)):
            out.extend(_leaf_diff(old.get(k), new.get(k), f"{path}.{k}"))
        return out
    return [path]


def _index_checkpoints(data: dict) -> dict:
    out = {}
    for cp in (data.get("checkpoints") or []):
        if not isinstance(cp, dict):
            continue
        acid = cp.get("ac_id")
        key = tuple(acid) if isinstance(acid, list) else acid
        out[key] = cp
    return out


def artifact_identifiers(data: dict) -> set:
    sg = data.get("system_grounding") or {}
    return {
        art.get("identifier") for art in (sg.get("artifacts") or [])
        if isinstance(art, dict) and isinstance(art.get("identifier"), str)
    }


def identifier_tokens(value, artifact_ids: set) -> dict:
    """AC-AMND-1(c): classify every identifier token found in `value` into the four
    fixed classes. Returns {class_name: {token, ...}}; classes with no match are
    omitted."""
    if value is None:
        return {}
    s = value if isinstance(value, str) else str(value)
    out = {}
    acct = set(_ACCOUNT_ID_RE.findall(s))
    if acct:
        out["aws-account-id"] = acct
    arns = set(_ARN_RE.findall(s))
    if arns:
        out["arn"] = arns
    hosts = set(_HOSTNAME_URL_RE.findall(s))
    if hosts:
        out["hostname-url"] = hosts
    matched = {a for a in artifact_ids if a and a in s}
    if matched:
        out["artifact-identifier"] = matched
    return out


def _as_number(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return v


def mandatory_review_names_security(data: dict) -> bool:
    """Clarifications: the schema-valid, hash-covered security flag is a
    `mandatory_review` entry with `review: security` — there is no top-level
    `security:` key. Checked against the CANDIDATE (current) contract, independent of
    whether mandatory_review itself changed in this amendment (AC-AMND-3: a
    security-flagged atom always routes to /foundry:authorize)."""
    for item in (data.get("mandatory_review") or []):
        if isinstance(item, dict) and item.get("review") == "security":
            return True
    return False


def compute_diff(old_data: dict, new_data: dict) -> "tuple[list, list]":
    """Return (widened_fields, diff_summary_lines) covering AC-AMND-1(b), (c), (d).
    (a) — the normative-region text diff — is computed separately by
    `unified_diff_text` (it is not itself a widening signal)."""
    widened = []
    summary = []

    for field in BOUNDARY_FIELDS:
        old_v, new_v = old_data.get(field), new_data.get(field)
        if field == "requires_capabilities" and old_v is None and new_v is None:
            continue  # not yet schema-present (R1); absent-vs-absent is not a diff
        for path in _leaf_diff(old_v, new_v, field):
            widened.append(path)
            summary.append(f"{path} changed — AC-AMND-1(b)")

    old_cps, new_cps = _index_checkpoints(old_data), _index_checkpoints(new_data)
    artifact_ids = artifact_identifiers(new_data)

    for ac_id, new_cp in new_cps.items():
        old_cp = old_cps.get(ac_id)
        if old_cp is None:
            continue  # a brand-new checkpoint; covered by AC-AMND-4's bijection floor
        label = ac_id if isinstance(ac_id, str) else "/".join(ac_id)

        for sub_field in ("intended", "ack"):
            if old_cp.get(sub_field) != new_cp.get(sub_field):
                path = f"checkpoints[{label}].{sub_field}"
                widened.append(path)
                summary.append(f"{path} changed — AC-AMND-1(b)")

        old_exp, new_exp = old_cp.get("expect") or {}, new_cp.get("expect") or {}
        for sub_field, old_val, new_val in (
            ("locator", old_cp.get("locator"), new_cp.get("locator")),
            ("expect.value", old_exp.get("value"), new_exp.get("value")),
        ):
            old_tok = identifier_tokens(old_val, artifact_ids)
            new_tok = identifier_tokens(new_val, artifact_ids)
            for cls, toks in new_tok.items():
                introduced = toks - old_tok.get(cls, set())
                if introduced:
                    path = f"checkpoints[{label}].{sub_field}"
                    widened.append(path)
                    summary.append(
                        f"{path} introduces a new {cls} identifier token "
                        f"{sorted(introduced)} — AC-AMND-1(c)"
                    )

        if old_exp.get("baseline") == "pre-change" and new_exp.get("baseline") == "none":
            path = f"checkpoints[{label}].expect.baseline"
            widened.append(path)
            summary.append(f"{path} weakened pre-change→none — AC-AMND-1(d)")

        old_op, new_op = old_exp.get("op"), new_exp.get("op")
        if (old_op != new_op and old_op in _OP_RANK and new_op in _OP_RANK
                and _OP_RANK[new_op] < _OP_RANK[old_op]):
            path = f"checkpoints[{label}].expect.op"
            widened.append(path)
            summary.append(f"{path} weakened {old_op}→{new_op} — AC-AMND-1(d)")

        old_num, new_num = _as_number(old_exp.get("value")), _as_number(new_exp.get("value"))
        if old_num is not None and new_num is not None and new_num < old_num:
            path = f"checkpoints[{label}].expect.value"
            widened.append(path)
            summary.append(f"{path} lowered {old_num}→{new_num} — AC-AMND-1(d)")

        old_surf, new_surf = old_cp.get("surface"), new_cp.get("surface")
        if old_surf is not None and new_surf is not None and old_surf != new_surf:
            path = f"checkpoints[{label}].surface"
            widened.append(path)
            summary.append(f"{path} repointed {old_surf!r}→{new_surf!r} — AC-AMND-1(d)")

    return widened, summary


# --------------------------------------------------------------------------- #
# The spec's `## Amendments` table (Residuals: not hash-covered; the tamper-evident
# copy is the security-audit record this CLI also writes).
# --------------------------------------------------------------------------- #
_SEP_RE = re.compile(r"^\|[-\s|]+\|\s*$", re.MULTILINE)


def append_amendment_row(spec_path: str, date: str, what: str, why: str, auth_seq: int) -> None:
    text = open(spec_path, encoding="utf-8").read()
    marker = "## Amendments"
    idx = text.find(marker)
    if idx == -1:
        raise AmendError(f"{spec_path} has no `## Amendments` section — cannot record the amendment")
    m = _SEP_RE.search(text, idx)
    if not m:
        raise AmendError(f"{spec_path}'s `## Amendments` section has no table header separator row")
    pos = m.end()
    if text[pos:pos + 1] == "\n":
        pos += 1
    while text[pos:pos + 1] == "|":  # advance past any existing rows to append at table end
        nl = text.find("\n", pos)
        pos = (nl + 1) if nl != -1 else len(text)
    what_cell = what.replace("|", "\\|")
    why_cell = why.replace("|", "\\|")
    row = f"| {date} | {what_cell} | {why_cell} | {auth_seq} |\n"
    open(spec_path, "w", encoding="utf-8").write(text[:pos] + row + text[pos:])


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="/foundry:amend — one-step re-freeze of a living spec (no operator step "
                     "unless the amendment widens a boundary or the contract is security-flagged).")
    ap.add_argument("--spec", required=True, help="path to the amended spec")
    ap.add_argument("--contract", required=True, help="path to the amended acceptance-contract.yaml")
    ap.add_argument("--ref", default="HEAD",
                     help="git ref holding the previously-frozen spec/contract (default: HEAD)")
    ap.add_argument("--repo-root", default=None,
                     help="repo root for git/audit-trail resolution (default: $CLAUDE_PROJECT_DIR or cwd)")
    ap.add_argument("--why", default=None,
                     help="free-text 'why reality required it' for the ## Amendments row "
                          "(default: a generic note; the diff itself is the record of record)")
    return ap


def main(argv=None) -> int:
    ap = build_arg_parser()
    args = ap.parse_args(argv)

    for p, label in ((args.spec, "spec"), (args.contract, "contract")):
        if not os.path.exists(p):
            print(f"FAIL: {label} not found: {p}", file=sys.stderr)
            return 1

    repo_root = args.repo_root or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()

    cur_data = fc.load_contract(args.contract)
    prior_block = cur_data.get("authorized")
    if not isinstance(prior_block, dict):
        print("FAIL: contract carries no `authorized:` trailer — /foundry:amend re-freezes an "
              "EXISTING authorization; run /foundry:authorize first.", file=sys.stderr)
        return 1

    cur_spec_hash = fc.spec_sha256(args.spec)
    cur_contract_hash = fc.contract_sha256(args.contract)
    if (prior_block.get("spec_sha256") == cur_spec_hash
            and prior_block.get("contract_sha256") == cur_contract_hash):
        print("no drift since last authorization — nothing to amend (idempotent).")
        return 0

    spec_rel, contract_rel = _relpath(repo_root, args.spec), _relpath(repo_root, args.contract)
    old_spec_raw = git_show(repo_root, spec_rel, args.ref)
    old_contract_raw = git_show(repo_root, contract_rel, args.ref)
    if old_spec_raw is None or old_contract_raw is None:
        print(f"FAIL: cannot resolve the previously-frozen spec/contract via "
              f"`git show {args.ref}:<path>` (repo_root={repo_root!r}) — /foundry:amend diffs "
              f"the working tree against the last commit; commit the last-authorized baseline "
              f"before amending.", file=sys.stderr)
        return 1

    old_data = old_contract_data(old_contract_raw)
    old_normative = old_normative_bytes(old_spec_raw)
    new_normative = fc.spec_normative_bytes(args.spec)

    print("=== normative-region diff (AC-AMND-1a) ===")
    diff_text = unified_diff_text(old_normative, new_normative)
    print(diff_text if diff_text else "(no textual change)")

    widened_fields, diff_summary_lines = compute_diff(old_data, cur_data)

    security_flagged = mandatory_review_names_security(cur_data)
    if security_flagged and "mandatory_review" not in widened_fields:
        widened_fields.append("mandatory_review")
        diff_summary_lines.append(
            "mandatory_review names a security review — always routes to /foundry:authorize")

    widened_fields = sorted(set(widened_fields))

    print("=== boundary/checkpoint diff (AC-AMND-1b/c/d) ===")
    for line in diff_summary_lines:
        print(f"  - {line}")
    if not diff_summary_lines:
        print("  (none)")

    if widened_fields:
        record = {
            "status": "needs-operator",
            "widened_fields": widened_fields,
            "diff_summary": "; ".join(diff_summary_lines),
            "remediation": f"/foundry:authorize {args.spec}",
        }
        print(json.dumps(record, sort_keys=True))
        return 1

    # AC-AMND-4: the SAME freeze floors + AC<->checkpoint bijection check as
    # /foundry:authorize, before any write. Never re-implemented: delegates to
    # foundry_authz.validate_spec_contract (which itself delegates the floor logic to
    # foundry_contract.validate_contract_bytes).
    ok, errors, warnings = az.validate_spec_contract(args.spec, args.contract)
    for w in warnings:
        print(f"  warn: {w}")
    if not ok:
        print("FAIL: contract does not pass freeze floors (AC-AMND-4):", file=sys.stderr)
        for e in errors:
            print(f"  error: {e}", file=sys.stderr)
        return 1

    # AC-AMND-2 / AC-AMND-6: record-before-action, freeze, amendments row, completion —
    # with NO audit-ledger (§8) row required or consulted anywhere in this path.
    try:
        intent_id = al.append_record({
            "action": "amend-intent",
            "operator_id": prior_block.get("operator_id"),
            "spec_ref": contract_rel,
            "spec_sha256": cur_spec_hash,
            "contract_sha256": cur_contract_hash,
        }, repo_root=repo_root)
    except al.AuditLogError as e:
        print(f"FAIL (fail-closed): could not write record-before-action: {e}", file=sys.stderr)
        return 1

    block = az.authorize(
        spec_path=args.spec,
        contract_path=args.contract,
        operator_id=prior_block.get("operator_id"),
        merge_autonomy_mode=prior_block.get("merge_autonomy_mode", "lean"),
        authorized_at=al.now_iso(),
    )

    what = "; ".join(diff_summary_lines) if diff_summary_lines else "normative-region text updated"
    why = args.why or "reality changed during implementation (non-widening amendment)"
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    append_amendment_row(args.spec, date, what, why, block["auth_seq"])

    al.append_record({
        "action": "amend-complete",
        "intent_ref": intent_id,
        "operator_id": block["operator_id"],
        "auth_seq": block["auth_seq"],
        "supersedes": block["supersedes"],
        "spec_sha256": block["spec_sha256"],
        "contract_sha256": block["contract_sha256"],
    }, repo_root=repo_root)

    print(f"AMENDED  auth_seq={block['auth_seq']}  "
          f"contract_sha256={block['contract_sha256'][:16]}…  "
          f"spec_sha256={block['spec_sha256'][:16] if block['spec_sha256'] else None}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
