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
      requires_capabilities, AND `target_repo` (round-2 hardening, RISK R1 — STRICTER than the
      frozen AC-AMND-1(b) list: `target_repo` names WHERE an atom's code lands in a multi-repo
      adopter, and a silent change there is a venue change no non-widening path should permit).
  (c) an IDENTIFIER TOKEN introduced or changed inside a checkpoint's locator/expect.value: a
      12-digit AWS account id, an `arn:`-prefixed token (case-insensitive), an IPv4 literal, a
      hostname/URL, or a token equal to a system_grounding.artifacts[].identifier value. A
      checkpoint whose ac_id is ABSENT from the baseline (new OR renamed-away-from) is evaluated
      against an EMPTY baseline — any identifier token in its locator/expect.value is, by
      definition, introduced (round-2 hardening, BLOCK S2).
  (d) a CHECKPOINT-RIGOR REDUCTION: expect.value lowered (or a `matches` regex value changed AT
      ALL — direction unknowable), expect.op weakened (or changed to/from an op outside the fixed
      ranking — direction unknowable), expect.baseline pre-change→none, or surface repointed OR
      DELETED.
A contract whose (candidate) mandatory_review names a `security` review ALWAYS routes to
/foundry:authorize (AC-AMND-3), independent of whether mandatory_review itself changed.

"Previous" state (round-2 hardening, BLOCK S1): the frozen `authorized:` trailer stores only
`spec_sha256`/`contract_sha256` — never the prior field values — so there is no durable record of
"what changed" except git history. This CLI does NOT trust `git show <ref>:<path>` at face value
(a caller could commit an already-widened contract, or point `--ref` at an arbitrary commit, and a
naive show-at-ref diff would silently compare the widened state against itself). Instead it walks
`git log <ref> -- <contract>` from `--ref` (default HEAD) backwards and uses the FIRST commit whose
(spec, contract) blobs BOTH hash to the values the working tree's `authorized:` trailer actually
signed — the exact state that was frozen — refusing closed if none matches. `operator_id` /
`merge_autonomy_mode` for the re-freeze are then read from THAT verified baseline commit's own
trailer (RISK R2), never from the mutable working-tree trailer (which sits below the hash sentinel
and could be hand-edited without moving any hash).

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
# presence/absence). `requires_capabilities` is R1-the-spec's-own (not yet
# schema-present) — treated as absent-vs-absent (no diff) until it exists, per the
# spec's Clarifications. `target_repo` is a ROUND-2 ADDITION (RISK R1) — stricter
# than the frozen AC-AMND-1(b) list; see the module docstring.
# --------------------------------------------------------------------------- #
BOUNDARY_FIELDS = [
    "scope", "system_grounding", "preconditions", "build_gates",
    "post_apply_checks", "mandatory_review", "requires_capabilities",
    "target_repo",
]

# AC-AMND-1(d) — a fixed strictness ranking for the closed `expect.op` enum
# (non-empty < matches < equals < count_gte). A change to a STRICTLY LOWER rank is a
# weakening. `expect.op` is a per-checkpoint RIGOR field (d) — it is NOT one of the
# top-level BOUNDARY_FIELDS (b) above, and there is no boundary-diff fallback that
# would otherwise catch an op change. An op change where either side falls OUTSIDE
# this ranking (a future schema extension) has an UNKNOWABLE direction and is
# convicted unconditionally by `compute_diff` below, never silently passed through.
_OP_RANK = {"non-empty": 0, "matches": 1, "equals": 2, "count_gte": 3}

# AC-AMND-1(c) — the fixed, testable identifier-token classes. Applied to the NEW
# value only (Design/notes): an identifier already present and unchanged is not a
# widening. RISK R3 (round-2 hardening):
#   - `arn:` matched case-insensitively (an upper/mixed-case ARN is still an ARN).
#   - an IPv4 literal is folded into the `hostname-url` class (an IP is host-shaped).
#   - a BARE (non-URL) hostname-shaped token ending in a common file extension is
#     EXCLUDED from the `hostname-url` class — `foundry_checks/module.py` should not
#     false-positive as a two-label hostname. An explicit `http(s)://` URL is NEVER
#     excluded by extension (a URL ending `.html`/`.json`/etc. is still a URL).
#   - NOT covered (accepted residuals — documented in skills/amend/SKILL.md):
#     a bare SINGLE-LABEL host (no dot: "prod-db", "localhost") and unicode
#     homoglyph/confusable substitutions in a hostname.
_ACCOUNT_ID_RE = re.compile(r"(?<!\d)\d{12}(?!\d)")
_ARN_RE = re.compile(r"\barn:[^\s\"',]+", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s\"']+", re.IGNORECASE)
_BARE_HOST_OR_IPV4_RE = re.compile(
    r"\b(?:(?:\d{1,3}\.){3}\d{1,3}"
    r"|(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,})\b"
)
_HOSTNAME_FILE_EXT_DENYLIST = {
    "md", "py", "yaml", "yml", "json", "sh", "txt", "js", "ts", "html", "toml", "lock",
}


# --------------------------------------------------------------------------- #
# git-backed "previous state" resolution (BLOCK S1)
# --------------------------------------------------------------------------- #
def git_show(repo_root: str, relpath: str, ref: str) -> "bytes | None":
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


def _spec_sha256_bytes(raw: bytes) -> str:
    """The SAME `foundry_contract.spec_sha256` /foundry:authorize's freeze hashes with
    (never re-derived), routed through a throwaway temp file since that helper reads a
    path, not bytes."""
    tmp = _write_temp(raw, ".md")
    try:
        return fc.spec_sha256(tmp)
    finally:
        os.remove(tmp)


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
    (never re-derived) routed through a throwaway temp file. Includes the historical
    `authorized:` block (RISK R2 reads operator_id/merge_autonomy_mode from it)."""
    if old_contract_raw is None:
        return None
    tmp = _write_temp(old_contract_raw, ".yaml")
    try:
        return fc.load_contract(tmp)
    finally:
        os.remove(tmp)


def resolve_baseline_commit(repo_root: str, contract_rel: str, spec_rel: str,
                             prior_block: dict, start_ref: str):
    """BLOCK S1: the diff baseline MUST be the exact state the frozen `authorized:`
    trailer signed — not merely "whatever `--ref` happens to point at". A caller could
    commit an already-widened contract and then amend against HEAD (HEAD IS the widened
    state — a naive show-at-HEAD diff would compare it against itself and silently
    re-freeze it), or pass `--ref` at an arbitrary older/wider commit.

    Walks `git log <start_ref> -- <contract_rel>` (start_ref is a STARTING POINT for the
    walk, never a trusted override) from `start_ref` backwards, and returns the FIRST
    commit whose (spec, contract) blobs BOTH hash to `prior_block`'s
    `spec_sha256`/`contract_sha256` — i.e. the exact content that was signed. Raises
    AmendError (fail-closed) when no such commit exists in that history.

    Returns (baseline_commit, old_spec_raw, old_contract_raw).
    """
    proc = subprocess.run(
        ["git", "-C", repo_root, "log", "--format=%H", start_ref, "--", contract_rel],
        capture_output=True, text=True)
    commits = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()] if proc.returncode == 0 else []
    if not commits:
        raise AmendError(
            f"no committed baseline matches the frozen trailer: `git log {start_ref} -- "
            f"{contract_rel}` in repo_root={repo_root!r} returned no commits (rc={proc.returncode}). "
            f"Commit the last-authorized baseline before amending."
        )
    target_spec_hash = prior_block.get("spec_sha256")
    target_contract_hash = prior_block.get("contract_sha256")
    for commit in commits:
        contract_raw = git_show(repo_root, contract_rel, commit)
        spec_raw = git_show(repo_root, spec_rel, commit)
        if contract_raw is None or spec_raw is None:
            continue
        if fc.contract_sha256_bytes(contract_raw) != target_contract_hash:
            continue
        if _spec_sha256_bytes(spec_raw) != target_spec_hash:
            continue
        return commit, spec_raw, contract_raw
    raise AmendError(
        f"no committed baseline matches the frozen trailer: walked {len(commits)} commit(s) "
        f"touching {contract_rel!r} from {start_ref!r} backwards and none reproduced BOTH "
        f"prior_block.spec_sha256={target_spec_hash!r} and "
        f"prior_block.contract_sha256={target_contract_hash!r}. Either the last-authorized "
        f"baseline was never committed, or --ref excludes the commit that would match — "
        f"amend refuses rather than diff against an unverified baseline."
    )


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


def _hostname_url_tokens(s: str) -> set:
    """RISK R3: URLs are never extension-filtered; a BARE hostname/IPv4-shaped token
    ending in a common file-extension is dropped (a filename, not a host)."""
    out = set(_URL_RE.findall(s))
    for m in _BARE_HOST_OR_IPV4_RE.findall(s):
        ext = m.rsplit(".", 1)[-1].lower()
        if ext in _HOSTNAME_FILE_EXT_DENYLIST:
            continue
        out.add(m)
    return out


def identifier_tokens(value, artifact_ids: set) -> dict:
    """AC-AMND-1(c): classify every identifier token found in `value` into the fixed
    classes. Returns {class_name: {token, ...}}; classes with no match are omitted."""
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
    hosts = _hostname_url_tokens(s)
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


def _identifier_widenings_against_empty_baseline(label: str, new_cp: dict, artifact_ids: set):
    """BLOCK S2: a checkpoint whose ac_id is ABSENT from the baseline (new, or the
    baseline's original ac_id was renamed away from) is NOT skipped outright. (c)
    still applies against an EMPTY baseline — any identifier token in the new
    locator/expect.value is, by construction, introduced. Surface/expect RIGOR (d) has
    no prior value to reduce FROM and is agent-owned (a genuinely new checkpoint is
    covered by AC-AMND-4's bijection floor instead) UNLESS an identifier token is
    present, in which case it is already caught here."""
    widened, summary = [], []
    new_exp = new_cp.get("expect") or {}
    for sub_field, new_val in (("locator", new_cp.get("locator")),
                                ("expect.value", new_exp.get("value"))):
        for cls, toks in identifier_tokens(new_val, artifact_ids).items():
            path = f"checkpoints[{label}].{sub_field}"
            widened.append(path)
            summary.append(
                f"{path} (checkpoint absent from the baseline — new or renamed) introduces a "
                f"{cls} identifier token {sorted(toks)} against an empty baseline — AC-AMND-1(c)"
            )
    return widened, summary


def compute_diff(old_data: dict, new_data: dict) -> "tuple[list, list]":
    """Return (widened_fields, diff_summary_lines) covering AC-AMND-1(b), (c), (d).
    (a) — the normative-region text diff — is computed separately by
    `unified_diff_text` (it is not itself a widening signal)."""
    widened = []
    summary = []

    for field in BOUNDARY_FIELDS:
        old_v, new_v = old_data.get(field), new_data.get(field)
        if field == "requires_capabilities" and old_v is None and new_v is None:
            continue  # not yet schema-present (R1 of the spec); absent-vs-absent is not a diff
        for path in _leaf_diff(old_v, new_v, field):
            widened.append(path)
            summary.append(f"{path} changed — AC-AMND-1(b)")

    old_cps, new_cps = _index_checkpoints(old_data), _index_checkpoints(new_data)
    artifact_ids = artifact_identifiers(new_data)

    for ac_id, new_cp in new_cps.items():
        label = ac_id if isinstance(ac_id, str) else "/".join(ac_id)
        old_cp = old_cps.get(ac_id)

        if old_cp is None:
            w, s = _identifier_widenings_against_empty_baseline(label, new_cp, artifact_ids)
            widened.extend(w)
            summary.extend(s)
            continue

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
        if old_op != new_op:
            if old_op in _OP_RANK and new_op in _OP_RANK:
                if _OP_RANK[new_op] < _OP_RANK[old_op]:
                    path = f"checkpoints[{label}].expect.op"
                    widened.append(path)
                    summary.append(f"{path} weakened {old_op}→{new_op} — AC-AMND-1(d)")
            else:
                # RISK R4: an op change involving an op OUTSIDE the fixed ranking
                # (a future schema extension) has an unknowable direction — convict.
                path = f"checkpoints[{label}].expect.op"
                widened.append(path)
                summary.append(
                    f"{path} changed {old_op!r}→{new_op!r} involving an op outside the "
                    f"fixed ranking {sorted(_OP_RANK)} — direction unknowable — AC-AMND-1(d)"
                )

        old_val, new_val = old_exp.get("value"), new_exp.get("value")
        if old_op == new_op == "matches" and old_val != new_val:
            # RISK R4: a `matches` regex operand's strictness direction is unknowable —
            # any change (looser, stricter, or merely different) convicts.
            path = f"checkpoints[{label}].expect.value"
            widened.append(path)
            summary.append(
                f"{path} matches-regex changed {old_val!r}→{new_val!r} — direction "
                f"unknowable — AC-AMND-1(d)"
            )
        else:
            old_num, new_num = _as_number(old_val), _as_number(new_val)
            if old_num is not None and new_num is not None and new_num < old_num:
                path = f"checkpoints[{label}].expect.value"
                widened.append(path)
                summary.append(f"{path} lowered {old_num}→{new_num} — AC-AMND-1(d)")

        old_surf, new_surf = old_cp.get("surface"), new_cp.get("surface")
        if old_surf is not None and old_surf != new_surf:
            # RISK R4: a surface CHANGE or DELETION both convict (new_surf may be None).
            path = f"checkpoints[{label}].surface"
            widened.append(path)
            if new_surf is None:
                summary.append(f"{path} deleted (was {old_surf!r}) — AC-AMND-1(d)")
            else:
                summary.append(f"{path} repointed {old_surf!r}→{new_surf!r} — AC-AMND-1(d)")

    # ROUND-2 SECURITY BLOCK: a checkpoint present in the baseline and ABSENT from the
    # candidate is a rigor reduction (AC-AMND-1(d)) — strictly stronger than deleting its
    # `surface`, which already convicts above. Bijection alone does not catch it when the AC
    # is deleted from the normative region too (normative text is not itself a signal).
    for ac_id in old_cps:
        if ac_id in new_cps:
            continue
        label = ac_id if isinstance(ac_id, str) else "/".join(ac_id)
        path = f"checkpoints[{label}]"
        widened.append(path)
        summary.append(f"{path} deleted — AC-AMND-1(d)")

    return widened, summary


# --------------------------------------------------------------------------- #
# The spec's `## Amendments` table (Residuals: not hash-covered; the tamper-evident
# copy is the security-audit record this CLI also writes).
# --------------------------------------------------------------------------- #
_SEP_RE = re.compile(r"^\|[-\s|]+\|\s*$", re.MULTILINE)
_NORMATIVE_CLOSE = "<!-- /normative -->"
_AMENDMENTS_HEADING = "## Amendments"
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
# v1.18.1: the heading is a LINE of its own after the last close marker — not the first occurrence
# anywhere, which a spec whose normative prose names "`## Amendments`" would match (the updater then
# appended a duplicate section on every run, and append_amendment_row could pick a table inside the
# normative region). Same line rule as foundry-audit-prepare.py's strip_amendments_section.
_HEADING_LINE_RE = re.compile(r"^## Amendments[ \t]*\r?$", re.MULTILINE)


def amendments_section_ok(spec_text: str) -> bool:
    """BLOCK C1(1): the `## Amendments` heading must appear AFTER the LAST
    `<!-- /normative -->` close-fence and OUTSIDE any ```-fenced code block (a spec
    could name "## Amendments" inside a documentation example without actually having
    the section). Masks fenced code blocks with same-length placeholder bytes so
    positions stay comparable to the unmasked text's fence offset."""
    close_idx = spec_text.rfind(_NORMATIVE_CLOSE)
    masked = _CODE_FENCE_RE.sub(lambda m: "\x00" * len(m.group(0)), spec_text)
    # No normative fence at all → the whole body is hashed (fallback elsewhere); the heading line may
    # sit anywhere. The SAME line rule as append_amendment_row, so this check never passes a spec
    # the append then refuses (v1.18.1 security review).
    return _HEADING_LINE_RE.search(masked, max(close_idx, 0)) is not None


def append_amendment_row(spec_path: str, date: str, what: str, why: str, auth_seq: int) -> None:
    text = open(spec_path, encoding="utf-8").read()
    close_idx = text.rfind(_NORMATIVE_CLOSE)
    masked = _CODE_FENCE_RE.sub(lambda m: "\x00" * len(m.group(0)), text)
    heading = _HEADING_LINE_RE.search(masked, max(close_idx, 0))
    idx = heading.start() if heading else -1
    if idx == -1:
        raise AmendError(f"{spec_path} has no `## Amendments` section — cannot record the amendment")
    # The separator must belong to THIS section: searched in the fence-masked text and bounded by the
    # next `#`/`##` heading line, so a table in a later section or a code example is never chosen.
    nxt = re.compile(r"^#{1,2} ", re.MULTILINE).search(masked, heading.end())
    m = _SEP_RE.search(masked, idx, nxt.start() if nxt else len(masked))
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
                     help="STARTING POINT (never a trusted override) for the backwards git-log "
                          "walk that resolves the verified baseline commit (default: HEAD)")
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

    # BLOCK S1 — resolve + VERIFY the baseline against the frozen trailer (fail-closed).
    try:
        baseline_commit, old_spec_raw, old_contract_raw = resolve_baseline_commit(
            repo_root, contract_rel, spec_rel, prior_block, args.ref)
    except AmendError as e:
        print(f"FAIL (fail-closed): {e}", file=sys.stderr)
        return 1

    old_data = old_contract_data(old_contract_raw)
    old_normative = old_normative_bytes(old_spec_raw)
    new_normative = fc.spec_normative_bytes(args.spec)

    print(f"baseline commit: {baseline_commit} (verified against the frozen trailer)")
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

    # BLOCK C1(1) — the Amendments-section shape check, BEFORE any write (including the
    # amend-intent record below): a spec with no (or wrongly-placed) `## Amendments`
    # section refuses closed, contract byte-identical.
    spec_text = open(args.spec, encoding="utf-8").read()
    if not amendments_section_ok(spec_text):
        print("FAIL: spec has no `## Amendments` section located AFTER <!-- /normative --> and "
              "outside any fenced code block — cannot record the amendment (BLOCK C1).",
              file=sys.stderr)
        return 1

    # RISK R2 — operator_id / merge_autonomy_mode come from the VERIFIED BASELINE
    # commit's own trailer, never from the mutable working-tree trailer (which sits
    # below the hash sentinel and could be hand-edited without moving any hash).
    baseline_block = old_data.get("authorized") if isinstance(old_data, dict) else None
    if not isinstance(baseline_block, dict):
        print("FAIL (fail-closed): the verified baseline commit's contract carries no "
              "`authorized:` trailer — cannot resolve operator_id/merge_autonomy_mode.",
              file=sys.stderr)
        return 1
    baseline_operator_id = baseline_block.get("operator_id")
    baseline_mode = baseline_block.get("merge_autonomy_mode", "lean")

    # The auth_seq az.authorize() will independently derive from the WORKING TREE's
    # current trailer (prior+1) — anticipated here (same fallback) only so the
    # Amendments row can name it before the freeze-write runs.
    anticipated_auth_seq = (
        prior_block["auth_seq"] + 1 if isinstance(prior_block.get("auth_seq"), int) else 1
    )

    # AC-AMND-2 / AC-AMND-6: record-before-action, THEN the Amendments row (BLOCK
    # C1(2)), THEN the freeze (BLOCK C1(3)), THEN completion (BLOCK C1(4)) — with NO
    # audit-ledger (§8) row required or consulted anywhere in this path.
    try:
        intent_id = al.append_record({
            "action": "amend-intent",
            "operator_id": baseline_operator_id,
            "spec_ref": contract_rel,
            "spec_sha256": cur_spec_hash,
            "contract_sha256": cur_contract_hash,
            "baseline_commit": baseline_commit,
            "baseline_spec_sha256": prior_block.get("spec_sha256"),
            "baseline_contract_sha256": prior_block.get("contract_sha256"),
        }, repo_root=repo_root)
    except al.AuditLogError as e:
        print(f"FAIL (fail-closed): could not write record-before-action: {e}", file=sys.stderr)
        return 1

    # BLOCK C1(2) — append the Amendments row FIRST (it lives outside the normative
    # region), then ASSERT the normative-region hash did not move; a mismatch means the
    # append landed inside the hashed region (a bug or a malformed section) — revert
    # and refuse. The contract is still untouched (az.authorize has not run yet).
    what = "; ".join(diff_summary_lines) if diff_summary_lines else "normative-region text updated"
    why = args.why or "reality changed during implementation (non-widening amendment)"
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    original_spec_bytes = open(args.spec, "rb").read()
    pre_append_spec_hash = cur_spec_hash
    append_amendment_row(args.spec, date, what, why, anticipated_auth_seq)
    post_append_spec_hash = fc.spec_sha256(args.spec)
    if post_append_spec_hash != pre_append_spec_hash:
        with open(args.spec, "wb") as fh:
            fh.write(original_spec_bytes)
        print("FAIL (fail-closed): appending the ## Amendments row moved the spec's "
              "normative-region hash (it must live entirely outside <!-- /normative -->) — "
              "reverted; contract unchanged.", file=sys.stderr)
        return 1

    # BLOCK C1(3) — the freeze-write.
    block = az.authorize(
        spec_path=args.spec,
        contract_path=args.contract,
        operator_id=baseline_operator_id,
        merge_autonomy_mode=baseline_mode,
        authorized_at=al.now_iso(),
    )

    # BLOCK C1(4) — completion.
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
