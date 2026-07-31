"""foundry_security_review — the UL-0018 security-reviewer library (v2.0, trusted-operator model).

Three things, all stdlib (no external binary, no gitleaks/trufflehog dependency to be absent):

  1. scan_candidate(...) — a deterministic, built-in secret/credential scanner: a pinned ruleset of
     high-signal patterns (PEM/private-key blocks, AWS/cloud keys, bearer/JWT/token formats) PLUS a
     base64/hex high-entropy heuristic with a pinned threshold + min-length window AND an allowlist
     of known non-secret high-entropy forms (git SHAs, *_sha256/content hashes, urandom().hex()
     nonce shapes) so foundry's own hashes/nonces are not Block-flagged. Every hit becomes a
     Block-severity finding with rotate_required=True referencing the secret by LOCATION + TYPE
     ONLY — the value is NEVER written into any finding, record, or log. A scan error fails LOUD
     (raises), never a silent "clean".

  2. record_review(...) — appends a `security-review` entry to .foundry/security-audit.jsonl via
     foundry_audit_log (fail-closed, read-back-confirmed). Derives verdict from findings (block if
     ANY Block finding present, else pass) and REFUSES to write a pass when a Block is present.

  3. review_present(...) — the floor-#3 presence read: ok=True iff a `security-review` entry with
     verdict=pass exists for the (spec_sha256, contract_sha256) subject.

Threat model: the session operator is the trusted root. This is an advisory assistant + mistake-
catcher, NOT a defense against the operator — no SoD, signing, or anti-forgery machinery (deferred
to the third-party / multi-operator trigger, D9). See feat-foundry-security-reviewer.md.
"""
from __future__ import annotations

import math
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import foundry_audit_log as audit  # noqa: E402  (record-before-action append + path resolution)

ACTION = "security-review"

# Files we never read as text for secret content: binaries, lockfiles, minified assets, and the
# audit trail itself (it stores location+type references, never values, but skip it regardless so a
# scan can never recurse on its own output). Extension-based; the trail is matched by basename.
_SKIP_EXTS = {
    # binaries / images / archives
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".tar", ".tgz", ".bz2", ".xz",
    ".7z", ".jar", ".war", ".class", ".so", ".dylib", ".dll", ".exe", ".bin", ".o", ".a", ".pyc",
    ".woff", ".woff2", ".ttf", ".eot", ".mp3", ".mp4", ".mov", ".wav", ".webp", ".bmp",
}
_SKIP_BASENAMES = {
    "security-audit.jsonl",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock",
    "Gemfile.lock", "composer.lock", "go.sum", "Pipfile.lock",
}
_SKIP_SUFFIXES = (".min.js", ".min.css", ".map")

# ---------------------------------------------------------------------------------------------- #
# Pinned high-signal ruleset. Each (type, compiled-regex). Order is deterministic; the FIRST
# matching rule names the type. Patterns are deliberately conservative (high signal) — the entropy
# heuristic catches the long tail. We NEVER capture the value into the finding (location+type only).
# ---------------------------------------------------------------------------------------------- #
_RULES = [
    ("private-key-block",
     re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("aws-access-key-id",
     re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA)[0-9A-Z]{16}\b")),
    ("aws-secret-access-key",
     re.compile(r"(?i)aws.{0,20}?(?:secret|sk).{0,20}?['\"][0-9A-Za-z/+=]{40}['\"]")),
    ("gcp-service-account-key",
     re.compile(r'"type"\s*:\s*"service_account"')),
    ("google-api-key",
     re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("github-token",
     re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36,}\b")),
    ("slack-token",
     re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{10,}\b")),
    ("jwt",
     re.compile(r"\beyJ[0-9A-Za-z_\-]{10,}\.eyJ[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}\b")),
    ("bearer-token",
     re.compile(r"(?i)\bbearer\s+[0-9A-Za-z_\-\.=]{20,}")),
    ("generic-secret-assignment",
     re.compile(r"(?i)\b(?:api[_-]?key|secret|password|passwd|token|client[_-]?secret|"
                r"access[_-]?token|private[_-]?key)\b\s*[:=]\s*['\"][^'\"\s]{12,}['\"]")),
]

# ---------------------------------------------------------------------------------------------- #
# Entropy heuristic (the long tail) + allowlist of known non-secret high-entropy forms.
# ---------------------------------------------------------------------------------------------- #
_ENTROPY_MIN_LEN = 32          # min-length window: only consider tokens this long or longer
_ENTROPY_MAX_LEN = 256         # ignore absurdly long blobs (data, not a key)
_ENTROPY_THRESHOLD = 4.0       # Shannon bits/char; base64/hex secrets sit ~4.5–6, hex SHAs ~3.7–4
_TOKEN_RE = re.compile(r"[0-9A-Za-z+/=_\-]{%d,%d}" % (_ENTROPY_MIN_LEN, _ENTROPY_MAX_LEN))
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")

# Allowlist: foundry's own hashes/nonces must NOT be Block-flagged (else DOCTOR-RED on its own trail).
# A token is allowlisted when EITHER (a) the surrounding line marks it as a content hash / sha / nonce,
# OR (b) it is a pure-hex string of a known digest/sha/nonce length (git SHA-1 = 40, sha256 = 64,
# os.urandom(12).hex() = 24, urandom(16).hex() = 32). Pure hex of these lengths is the shape foundry's
# own record_id / content_sha256 / git SHAs take — high-signal NON-secret.
_ALLOWLIST_HEX_LENS = {24, 32, 40, 56, 64, 96, 128}
_ALLOWLIST_CONTEXT_RE = re.compile(
    r"(?i)(?:_sha256|sha256|sha1|content[_-]?hash|\bsha\b|commit|git[_-]?sha|\bnonce\b|"
    r"record_id|urandom|digest|checksum|fingerprint|source_signature|wiring[_-]?hash)")


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = Counter(s)
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _is_allowlisted_entropy(token: str, line: str) -> bool:
    """True iff this high-entropy token is a known non-secret form (git SHA / *_sha256 / content hash
    / urandom nonce shape) and should NOT be Block-flagged."""
    if _HEX_RE.match(token) and len(token) in _ALLOWLIST_HEX_LENS:
        return True
    if _ALLOWLIST_CONTEXT_RE.search(line):
        return True
    return False


def _finding(severity, category, location, rationale, *, rotate_required=False):
    f = {"severity": severity, "category": category, "location": location, "rationale": rationale}
    if rotate_required:
        f["rotate_required"] = True
    return f


def scan_text(text: str, path: str) -> list[dict]:
    """Scan one text blob. Returns a list of Block findings (location + type ONLY, never the value).
    Raises on a regex/scan error (fail LOUD — never a silent clean)."""
    findings: list[dict] = []
    lines = text.splitlines()
    for i, line in enumerate(lines, start=1):
        loc = f"{path}:{i}"
        # Pinned ruleset (first match wins per line, per type — but a line can match several types).
        matched_types = []
        for typ, rx in _RULES:
            if rx.search(line):
                matched_types.append(typ)
        for typ in matched_types:
            findings.append(_finding(
                "Block", "secret", loc,
                f"possible {typ} detected (value redacted) — rotate the credential and remove it "
                f"from the change; load secrets from the environment / a secrets manager instead",
                rotate_required=True))
        if matched_types:
            continue  # a ruleset hit already flags the line; don't double-report via entropy
        # Entropy heuristic for the long tail.
        for tok in _TOKEN_RE.findall(line):
            if _is_allowlisted_entropy(tok, line):
                continue
            if _shannon_entropy(tok) >= _ENTROPY_THRESHOLD:
                findings.append(_finding(
                    "Block", "secret", loc,
                    f"high-entropy string (entropy>={_ENTROPY_THRESHOLD}, len>={_ENTROPY_MIN_LEN}; "
                    f"value redacted) — likely an embedded credential/key; rotate and externalize "
                    f"if so",
                    rotate_required=True))
                break  # one entropy finding per line is enough
    return findings


def _should_skip(path: str) -> bool:
    base = os.path.basename(path)
    if base in _SKIP_BASENAMES:
        return True
    if any(base.endswith(suf) for suf in _SKIP_SUFFIXES):
        return True
    _, ext = os.path.splitext(base)
    return ext.lower() in _SKIP_EXTS


def _read_text_or_skip(path: str):
    """Return the file's text, or None if it should be skipped (binary/lockfile/minified/trail).
    Binary detection: a NUL byte in the head. Raises on an unexpected read error (fail LOUD)."""
    if _should_skip(path):
        return None
    with open(path, "rb") as fh:
        raw = fh.read()
    if b"\x00" in raw[:8192]:
        return None  # binary
    return raw.decode("utf-8", errors="replace")


def scan_candidate(changed_files, *, adopter_root=None) -> list[dict]:
    """Scan the candidate = the changed-file set vs the authorized base. `changed_files` is an
    iterable of paths (absolute, or relative to adopter_root). Reads the FULL post-change content of
    each changed text file (a secret can sit on an unchanged line of a changed file); skips
    binaries / lockfiles / minified assets / the audit trail. Returns all Block findings.

    Fail LOUD: any error reading/scanning a file raises (never a silent clean)."""
    root = adopter_root or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    findings: list[dict] = []
    for f in changed_files:
        abspath = f if os.path.isabs(f) else os.path.join(root, f)
        rel = os.path.relpath(abspath, root)
        if not os.path.isfile(abspath):
            continue  # deleted file in the changeset — nothing to scan
        text = _read_text_or_skip(abspath)
        if text is None:
            continue
        findings.extend(scan_text(text, rel))
    return findings


# ---------------------------------------------------------------------------------------------- #
# Recording + presence read.
# ---------------------------------------------------------------------------------------------- #
def _derive_verdict(findings) -> str:
    return "block" if any(f.get("severity") == "Block" for f in (findings or [])) else "pass"


def record_review(spec_ref, spec_sha256, contract_sha256, operator_id, findings, *, adopter_root):
    """Append a `security-review` entry to .foundry/security-audit.jsonl (fail-closed, read-back-
    confirmed via foundry_audit_log). Derives verdict from findings — `block` if ANY Block finding
    is present (incl. a secret-scan hit), else `pass`. REFUSES (raises ValueError) to write a pass
    when a Block finding is present (fail-closed coupling in code, not operator discipline). Returns
    the assigned record_id."""
    findings = list(findings or [])
    verdict = _derive_verdict(findings)
    if verdict == "pass" and any(f.get("severity") == "Block" for f in findings):
        # defensive: _derive_verdict already prevents this, but make the coupling explicit + loud.
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


def review_present(spec_sha256, contract_sha256, *, adopter_root):
    """Floor-#3 presence read. Returns (ok, reason): ok=True iff a `security-review` entry with
    verdict=pass exists for that (spec_sha256, contract_sha256); else (False, reason)."""
    saw_subject = False
    saw_block = False
    for rec in _iter_records(adopter_root):
        if rec.get("action") != ACTION:
            continue
        if rec.get("spec_sha256") != spec_sha256 or rec.get("contract_sha256") != contract_sha256:
            continue
        saw_subject = True
        if rec.get("verdict") == "pass":
            return True, "a passing security-review is recorded for this (spec, contract)"
        if rec.get("verdict") == "block":
            saw_block = True
    if saw_block:
        return False, "only block-verdict security-review entries recorded for this (spec, contract)"
    if saw_subject:
        return False, "no passing security-review recorded for this (spec, contract)"
    return False, "no security-review recorded for this (spec, contract)"
