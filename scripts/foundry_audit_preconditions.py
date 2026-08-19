#!/usr/bin/env python3
"""foundry_audit_preconditions — the deterministic G-2/G-3/G-4 gate the §8 audit entrypoint runs
BEFORE any LLM round (feat-foundry-audit-preconditions, AC-APC-1..6).

The retrospective found the audit engine's waste concentrated in rounds a deterministic check
would have refused for ~zero tokens: 7 specs re-audited on byte-identical `spec_sha256`
(window-sticker 3x), a phantom atom (FEAT-0324) carried through 2 audit rounds + 3 authorize
ceremonies AFTER its spec was marked `status: superseded`, and specs hard-wiring cross-spec
references to superseded/never-authorized siblings that no gate resolved. This module is the
precondition family that closes those three gaps:

  G-2 dedupe        — an identical `spec_sha256` already has a recorded terminus row in the audit
                       ledger -> SKIP (zero LLM rounds), recording a `dedupe-skip` ledger event.
                       `--force` bypasses G-2 ONLY.
  G-3 liveness       — `status: superseded` -> REFUSE, always. "Release-dropped" (absent from an
                       EXPLICITLY invoked `--release <id>`'s manifest) -> REFUSE, but ONLY when a
                       release context is given, and NEVER for a release whose state is
                       `completed` (completed membership can never kill a live subject) or when
                       the atom is carried by no release at all (vacuously live). No bypass.
  G-4 reference      — every FEAT/AC-id reference in the subject spec's `<!-- normative -->`
      closure          region must resolve to a live (non-superseded, authorized) corpus atom ->
                       REFUSE, naming every dangling reference. No bypass.

Execution order (`run_preconditions`) is **G-3 -> G-4 -> G-2**, REFUSE-dominates-SKIP: `spec_sha256`
hashes only the normative region (§22.4b), so `status: superseded` (frontmatter) and a manifest
drop change NEITHER the hash G-2 keys on NOR the G-4 reference set — a subject that was audited
BEFORE going dead must still refuse on re-invocation, never silently dedupe-skip past the liveness
fact. G-2 therefore runs LAST, only once G-3/G-4 have both cleared.

THREAT MODEL — TRUSTED OPERATOR; non-security-flagged. This gate can only SKIP or REFUSE a run —
it never weakens one. AC-APC-4 (fail-closed TOWARD AUDITING): any error reading the gate's OWN
inputs (an unreadable ledger, an unparseable release manifest, an unreadable spec/corpus) disables
SKIP/REFUSE for that heuristic ALONE and lets the audit proceed — the gate may only ever save
cost, never silently suppress a needed audit. G-0 grounding (Pass-0 system-grounding) and G-1 size
(the spec-size gate already wired into `foundry-audit-prepare.py`) are shipped elsewhere; this
module does not duplicate them.
"""
from __future__ import annotations

import os
import re
import sys

import yaml

# Bootstrap THIS module's own directory onto sys.path (mirrors foundry-audit-prepare.py /
# foundry_audit_ledger.py's own bootstrap) so the sibling imports resolve regardless of HOW this
# module was loaded (a plain `import` with scripts/ already on sys.path, OR a drop-in check's
# `importlib.util.spec_from_file_location` load, which does NOT put scripts/ on sys.path for us).
_HERE_DIR = os.path.dirname(os.path.abspath(__file__))
if _HERE_DIR not in sys.path:
    sys.path.insert(0, _HERE_DIR)

import foundry_audit_ledger as al   # noqa: E402
import foundry_contract as fc       # noqa: E402
import foundry_release as fr        # noqa: E402

# ── the deterministic reason-line prefixes (AC-APC-5) ──────────────────────────────────────────
ACTIONS = ("proceed", "skip", "refuse")


class PreconditionResult:
    """The outcome of one gate (or the combined `run_preconditions`). `action` in {proceed, skip,
    refuse}; `gate` names which heuristic produced it (`G-2`/`G-3`/`G-4`); `reason_tag` is the
    fine-grained discriminator (`dedupe`/`superseded`/`release-dropped`/`dangling-reference`);
    `error` is True iff this outcome IS a gate-input-error passthrough (AC-APC-4) — the gate could
    not evaluate itself and fell back to `proceed` rather than silently suppressing the audit."""

    def __init__(self, action, *, gate=None, reason_tag=None, detail="", error=False):
        if action not in ACTIONS:
            raise ValueError(f"action must be one of {ACTIONS}, got {action!r}")
        self.action = action
        self.gate = gate
        self.reason_tag = reason_tag
        self.detail = detail
        self.error = error

    def reason_line(self):
        """A SINGLE deterministic, machine-parseable line (AC-APC-5) — `AUDIT-PRECONDITION-<ACTION>
        <gate> <reason_tag>: <detail>`. Any embedded newline in `detail` is collapsed to a space so
        the guarantee ("a single line") holds regardless of what a caller stuffs into detail."""
        bits = [f"AUDIT-PRECONDITION-{self.action.upper()}"]
        if self.gate:
            bits.append(self.gate)
        if self.reason_tag:
            bits.append(self.reason_tag)
        head = " ".join(bits)
        detail = " ".join(str(self.detail).splitlines())
        return f"{head}: {detail}" if detail else head

    def __repr__(self):
        return f"<PreconditionResult {self.reason_line()!r}>"


def _project_dir(project_dir=None):
    return project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def _relpath(path, project_dir=None):
    """`path` relative to the project dir, forward-slashed — matches the form `spec_ref`/
    `contract_ref` are stored in (release manifests, contracts)."""
    pd = os.path.abspath(_project_dir(project_dir))
    ap = os.path.abspath(path)
    try:
        rel = os.path.relpath(ap, pd)
    except ValueError:
        return ap
    return rel.replace(os.sep, "/")


# ── G-2 — identical-sha dedupe (AC-APC-1) ────────────────────────────────────────────────────────

def _read_ledger_rows(ledger_path):
    """All parseable JSONL rows. A per-line JSON error is tolerated (skip that line — mirrors
    foundry_audit_ledger's own tolerant read); an OS-level read error (unreadable file, or the path
    being a directory) PROPAGATES — that is the "unreadable ledger" AC-APC-4 names explicitly.
    A ledger path that does NOT EXIST AT ALL is the ordinary "no audits recorded yet" case (never
    an error) — distinguished from "exists but is a directory/unreadable" via `os.path.exists`
    (NOT `os.path.isfile`, which would silently treat an existing-but-broken path as empty)."""
    if not os.path.exists(ledger_path):
        return []
    rows = []
    import json
    with open(ledger_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict):
                rows.append(r)
    return rows


def gate_dedupe(spec_path, *, project_dir=None, force=False, operator=None):
    """G-2 (AC-APC-1). Returns a PreconditionResult. `force=True` bypasses G-2 ONLY (AC-APC-5) —
    every other gate still runs. A hit records a NEW `dedupe-skip` ledger row (AC-APC-1); if that
    write itself fails, the gate does NOT skip (fail-closed toward auditing, AC-APC-4) — recording
    is part of what AC-APC-1 requires a skip to mean."""
    if force:
        return PreconditionResult("proceed", gate="G-2", reason_tag="force",
                                  detail="--force bypasses G-2 dedupe")
    try:
        spec_sha = fc.spec_sha256(spec_path)
    except OSError as e:
        return PreconditionResult("proceed", gate="G-2", reason_tag="gate-input-error",
                                  detail=f"spec unreadable: {e} (dedupe disabled, audit proceeds)",
                                  error=True)
    ledger_p = al.ledger_path(project_dir)
    try:
        rows = _read_ledger_rows(ledger_p)
    except OSError as e:
        return PreconditionResult("proceed", gate="G-2", reason_tag="gate-input-error",
                                  detail=f"ledger unreadable ({ledger_p}): {e} "
                                         f"(dedupe disabled, audit proceeds)", error=True)
    hit = None
    for r in rows:
        if r.get("spec_sha256") == spec_sha:
            hit = r  # keep last -> most recent
    if hit is None:
        return PreconditionResult("proceed", gate="G-2",
                                  detail=f"no recorded terminus at spec_sha256={spec_sha[:16]}…")

    op = al.resolve_operator_for_ledger(operator, project_dir)
    try:
        row = al.record_audit_v2(spec_ref=_relpath(spec_path, project_dir), spec_sha256=spec_sha,
                                 rounds=0, operator=op, tier="unspecified", verdict="dedupe-skip",
                                 findings={"new": 0, "resolved": 0, "open": 0},
                                 project_dir=project_dir)
    except al.LedgerWriteError as e:
        return PreconditionResult("proceed", gate="G-2", reason_tag="gate-input-error",
                                  detail=f"could not record dedupe-skip event: {e} "
                                         f"(dedupe disabled, audit proceeds)", error=True)
    return PreconditionResult(
        "skip", gate="G-2", reason_tag="dedupe",
        detail=f"identical spec_sha256={spec_sha[:16]}… already has a recorded terminus "
               f"(verdict={hit.get('verdict')!r}, run_id={hit.get('run_id')!r}, "
               f"ts={hit.get('ts')!r}) in {ledger_p} — recorded dedupe-skip run_id="
               f"{row['run_id']!r}")


# ── G-3 — subject liveness (AC-APC-2) ────────────────────────────────────────────────────────────

# Mirrors foundry_release.py's `_probe_superseded` heuristic exactly (single source of the
# "what does `status: superseded` mean" question): a YAML frontmatter `status: superseded` key, OR
# a body line matching this bold-tolerant, case-insensitive regex.
_SUPERSEDED_BODY_RE = re.compile(r"^\*{0,2}[Ss]tatus:?\*{0,2}\s*superseded\b", re.M)


def _spec_superseded(spec_path):
    """(superseded_bool, error_or_None). error is set (and superseded meaningless) iff the spec
    itself is unreadable — the caller treats that as a gate-input error (AC-APC-4)."""
    try:
        with open(spec_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        return False, f"spec unreadable: {e}"
    fm_superseded = False
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            try:
                fm = yaml.safe_load(text[3:end]) or {}
                if isinstance(fm, dict) and fm.get("status") == "superseded":
                    fm_superseded = True
            except yaml.YAMLError:
                pass
    return (fm_superseded or bool(_SUPERSEDED_BODY_RE.search(text))), None


def gate_liveness(spec_path, *, project_dir=None, release_id=None):
    """G-3 (AC-APC-2). `status: superseded` always refuses. Release-dropped is evaluated SOLELY
    against the explicit `release_id` context (never a scan of every manifest under
    `.foundry/releases/`): absent release_id -> vacuously live; release_id given but the release's
    `state == completed` -> live regardless of membership (completed membership never kills); else
    membership in that one release's manifest decides."""
    superseded, err = _spec_superseded(spec_path)
    if err:
        return PreconditionResult("proceed", gate="G-3", reason_tag="gate-input-error",
                                  detail=f"{err} (liveness disabled, audit proceeds)", error=True)
    if superseded:
        return PreconditionResult("refuse", gate="G-3", reason_tag="superseded",
                                  detail=f"spec {spec_path} carries status: superseded")

    if not release_id:
        return PreconditionResult(
            "proceed", gate="G-3",
            detail="no --release context given; an atom carried by no release is vacuously live")

    try:
        rel = fr.load_release(release_id, project_dir=project_dir)
    except fr.ReleaseError as e:
        return PreconditionResult("proceed", gate="G-3", reason_tag="gate-input-error",
                                  detail=f"release manifest {release_id!r} unparseable: {e} "
                                         f"(release-dropped check disabled, audit proceeds)",
                                  error=True)

    spec_rel = _relpath(spec_path, project_dir)
    member = any(a.spec_ref == spec_rel for a in rel.atoms)
    if member:
        return PreconditionResult("proceed", gate="G-3",
                                  detail=f"spec is a member of release {release_id!r}")
    if rel.state == "completed":
        return PreconditionResult(
            "proceed", gate="G-3",
            detail=f"spec absent from completed release {release_id!r}'s manifest — "
                   f"completed membership never kills a live subject")
    return PreconditionResult(
        "refuse", gate="G-3", reason_tag="release-dropped",
        detail=f"spec {spec_rel} absent from release {release_id!r} (state={rel.state!r}) "
               f"manifest")


# ── G-4 — reference closure (AC-APC-3) ───────────────────────────────────────────────────────────

_NORM_OPEN, _NORM_CLOSE = "<!-- normative -->", "<!-- /normative -->"
# A corpus wiki ref is `[[feat-<slug>]]` — the `feat-` prefix is REQUIRED, and is what separates a
# citation from quoted link SYNTAX. Without it this pattern matched any `[[token]]`, so a spec that
# merely DOCUMENTS wikilink syntax (`[[wikilink]]`, `[[link]]`, `[[Target]]`) was read as citing
# three nonexistent atoms and fail-closed G-4 on its own prose — unavoidable for a corpus whose
# subject matter is a wiki, since such a spec cannot describe its product without writing the token.
# The PREFIX, not position, is the discriminator: measured across both live corpora, every
# `feat-`-prefixed token is a genuine reference and every non-prefixed one is syntax, zero overlap.
# Deliberately NOT a code-span exemption — real references are frequently backticked (e.g.
# `[[feat-foundry-leak-scan-ls-remote-sink]]`, a genuinely DANGLING ref this gate must keep
# catching), so masking code spans would trade a harmless false positive for a silent false
# negative. Every spec file in the corpus is named `feat-*.md`, so the prefix is invariant.
_WIKI_REF_RE = re.compile(r"\[\[(feat-[A-Za-z0-9_-]+)\]\]")
_ATOM_CITE_RE = re.compile(r"\[Atom:\s*([^\]]+)\]")
_AC_ID_RE = re.compile(r"\bAC(?:-[A-Z0-9]+)+-\d+\b")


def _normative_region(text):
    parts, i = [], 0
    while True:
        o = text.find(_NORM_OPEN, i)
        if o < 0:
            break
        c = text.find(_NORM_CLOSE, o + len(_NORM_OPEN))
        if c < 0:
            break
        parts.append(text[o + len(_NORM_OPEN):c])
        i = c + len(_NORM_CLOSE)
    return "".join(parts) if parts else text


def _own_ac_ids(spec_path):
    """The AC-IDs the subject spec ITSELF defines (its sibling contract's checkpoints) — excluded
    from reference extraction so a spec's own AC-IDs are never treated as cross-spec references."""
    contract_path = os.path.join(os.path.dirname(spec_path), "acceptance-contract.yaml")
    if not os.path.isfile(contract_path):
        return set()
    try:
        with open(contract_path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        return {aid for cp in (doc.get("checkpoints") or []) if isinstance(cp, dict)
                for aid in fc._ac_ids_of(cp)}
    except (OSError, yaml.YAMLError):
        return set()


def _extract_references(normative_text, own_ac_ids):
    """Cross-spec reference tokens in the normative region: `[[feat-slug]]` wiki refs, `[Atom:
    <path>]` citations (the shipped citation grammar), and bare `AC-<PREFIX>-<N>` mentions that are
    NOT the subject's own AC-ID definitions. Deduplicated, order-preserving.

    The corpus's own convention (every spec in this repo) is: a spec DEFINES its own AC-IDs as a
    BOLD definition-list bullet — `- **AC-APC-1** (Requirement): …` — and CITES another atom's
    AC-ID as a plain, non-bold inline mention — `…(AC-ALT-1)`, `feat-foundry-X, AC-ASG-4)`. A
    bold-wrapped AC-ID match is therefore a self-definition (excluded), never a cross-reference —
    this holds even when the subject has no sibling contract yet (a common pre-authorize DRAFT
    state, where `own_ac_ids` — the contract-checkpoints signal — is empty). `own_ac_ids` is kept
    as an ADDITIONAL exclusion signal when a contract IS present (defense in depth, not the sole
    mechanism)."""
    refs, seen = [], set()

    def _add(kind, token):
        key = (kind, token)
        if key not in seen:
            seen.add(key)
            refs.append(key)

    for m in _WIKI_REF_RE.finditer(normative_text):
        _add("feat-slug", m.group(1))
    for m in _ATOM_CITE_RE.finditer(normative_text):
        _add("path", m.group(1).strip())
    for m in _AC_ID_RE.finditer(normative_text):
        tok = m.group(0)
        s, e = m.span()
        is_bold_definition = (normative_text[max(0, s - 2):s] == "**"
                              and normative_text[e:e + 2] == "**")
        if is_bold_definition or tok in own_ac_ids:
            continue
        _add("ac-id", tok)
    return refs


def _walk_specs(project_dir):
    root = os.path.join(project_dir, "specs")
    if not os.path.isdir(root):
        return
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if fn.startswith("feat-") and fn.endswith(".md"):
                yield os.path.join(dirpath, fn)


def _build_corpus_index(project_dir):
    """{'by_slug': {slug: path}, 'by_relpath': {rel: path}, 'by_ac_id': {ac_id: path}} over every
    `feat-*.md` under `<project_dir>/specs/` (the direct spec-tree-scan fallback the design notes
    call for). Tolerant of a per-spec malformed sibling contract (skip that spec's AC-IDs, keep
    scanning) — an unreadable CORPUS ROOT itself is the caller's problem (propagates as OSError)."""
    idx = {"by_slug": {}, "by_relpath": {}, "by_ac_id": {}}
    for path in _walk_specs(project_dir):
        slug = os.path.basename(path)[:-3]
        idx["by_slug"][slug] = path
        idx["by_relpath"][_relpath(path, project_dir)] = path
        contract_path = os.path.join(os.path.dirname(path), "acceptance-contract.yaml")
        if os.path.isfile(contract_path):
            try:
                with open(contract_path, encoding="utf-8") as fh:
                    doc = yaml.safe_load(fh) or {}
                for cp in (doc.get("checkpoints") or []):
                    if not isinstance(cp, dict):
                        continue
                    for aid in fc._ac_ids_of(cp):
                        idx["by_ac_id"][aid] = path
            except (OSError, yaml.YAMLError):
                pass
    return idx


def _spec_authorized(spec_path):
    """True iff the sibling `acceptance-contract.yaml` carries a non-empty `authorized:` block
    (an `operator_id`). False (unauthorized) on an absent contract or an absent/empty
    `authorized:` block. A malformed-but-present contract is NOT treated as authorized (a broken
    contract cannot attest authorization) but IS a legitimate "unauthorized" verdict, not a gate
    error — only a wholly unreadable CORPUS scan (the caller's `_build_corpus_index`) is."""
    contract_path = os.path.join(os.path.dirname(spec_path), "acceptance-contract.yaml")
    if not os.path.isfile(contract_path):
        return False
    try:
        with open(contract_path, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return False
    blk = doc.get("authorized")
    return isinstance(blk, dict) and bool(blk.get("operator_id"))


def gate_reference_closure(spec_path, *, project_dir=None):
    """G-4 (AC-APC-3). Resolves every FEAT/AC-id reference in the subject spec's normative region
    against the corpus; REFUSES naming every reference that resolves to a superseded spec, a
    nonexistent spec, or an unauthorized-and-required dependency (the sole meaning of "required"
    here: it is cited from the subject's OWN normative region, which is by definition load-bearing
    for that subject's freeze)."""
    pd = _project_dir(project_dir)
    try:
        with open(spec_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        return PreconditionResult("proceed", gate="G-4", reason_tag="gate-input-error",
                                  detail=f"spec unreadable: {e} "
                                         f"(reference closure disabled, audit proceeds)", error=True)

    refs = _extract_references(_normative_region(text), _own_ac_ids(spec_path))
    if not refs:
        return PreconditionResult("proceed", gate="G-4",
                                  detail="no cross-spec references in the normative region")

    try:
        idx = _build_corpus_index(pd)
    except OSError as e:
        return PreconditionResult("proceed", gate="G-4", reason_tag="gate-input-error",
                                  detail=f"corpus scan failed: {e} "
                                         f"(reference closure disabled, audit proceeds)", error=True)

    dangling = []
    for kind, token in refs:
        target = None
        if kind == "feat-slug":
            target = idx["by_slug"].get(token)
        elif kind == "path":
            target = idx["by_relpath"].get(token)
            if target is None and os.path.isfile(os.path.join(pd, token)):
                target = os.path.join(pd, token)
        elif kind == "ac-id":
            target = idx["by_ac_id"].get(token)

        if target is None:
            dangling.append(f"{token} (nonexistent spec)")
            continue
        t_superseded, t_err = _spec_superseded(target)
        if t_err:
            # The referenced spec itself is unreadable -- a gate-input error for THIS reference,
            # not a defect of the reference; AC-APC-4 says errors disable SKIP/REFUSE, they never
            # manufacture one, so this reference is silently not added to `dangling`.
            continue
        if t_superseded:
            dangling.append(f"{token} (superseded spec {_relpath(target, pd)})")
            continue
        if not _spec_authorized(target):
            dangling.append(f"{token} (unauthorized-and-required dependency "
                            f"{_relpath(target, pd)})")

    if dangling:
        return PreconditionResult("refuse", gate="G-4", reason_tag="dangling-reference",
                                  detail="; ".join(dangling))
    return PreconditionResult(
        "proceed", gate="G-4",
        detail=f"all {len(refs)} cross-spec reference(s) resolve to live, authorized atoms")


# ── the combined entrypoint (run before any LLM round) ──────────────────────────────────────────

def run_preconditions(spec_path, *, project_dir=None, release_id=None, force=False, operator=None):
    """G-3 -> G-4 -> G-2, in that order. REFUSE dominates SKIP (a hard "this subject is dead/
    dangling" fact is always surfaced, even when its BYTE content happens to hash-match a prior
    terminus): `spec_sha256` hashes only the normative region (§22.4b), so `status: superseded`
    living in the frontmatter, or a manifest drop, changes NEITHER a spec's normative bytes NOR its
    G-4 reference set — a superseded/dropped/dangling subject that was audited BEFORE going dead
    must still refuse, never silently dedupe-skip past the liveness fact. G-2 dedupe therefore runs
    LAST, only once G-3/G-4 have both cleared. `force` bypasses ONLY G-2 (AC-APC-5) — G-3/G-4
    always run and have no bypass. Each gate independently fails closed toward auditing on its OWN
    input errors (AC-APC-4); an error in one gate never suppresses another gate's REFUSE."""
    r3 = gate_liveness(spec_path, project_dir=project_dir, release_id=release_id)
    if r3.action == "refuse":
        return r3
    r4 = gate_reference_closure(spec_path, project_dir=project_dir)
    if r4.action == "refuse":
        return r4
    r2 = gate_dedupe(spec_path, project_dir=project_dir, force=force, operator=operator)
    if r2.action == "skip":
        return r2
    return PreconditionResult(
        "proceed", gate="G-2/G-3/G-4",
        detail="no precondition fired (" + "; ".join(
            r.reason_line() for r in (r3, r4, r2)) + ")")


if __name__ == "__main__":
    # Minimal manual-invocation CLI (not the drop-in check entrypoint — see
    # scripts/foundry_checks/audit-preconditions.py for the selftest). Useful for a human/operator
    # to probe a single spec's precondition verdict without binding the full engine.
    import argparse
    ap = argparse.ArgumentParser(description="Evaluate the G-2/G-3/G-4 audit preconditions for a spec.")
    ap.add_argument("spec", help="path to the target spec (.md)")
    ap.add_argument("--release", default=None, help="explicit release-id context for G-3 release-dropped")
    ap.add_argument("--force", action="store_true", help="bypass G-2 dedupe only")
    args = ap.parse_args()
    result = run_preconditions(args.spec, release_id=args.release, force=args.force)
    print(result.reason_line())
    sys.exit(1 if result.action == "refuse" else 0)
