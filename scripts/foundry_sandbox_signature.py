"""foundry_sandbox_signature — the ONE shared sandbox-denial signature classifier
(feat-foundry-doctor-selftest-sandbox-tolerance, ER #76 + ER #64, AC-DSST-3).

Under an OS/command sandbox (Claude Code native Bash sandbox, CI jails, seccomp/landlock),
hermetic selftests fail at scratch creation with EPERM ("Operation not permitted") — an
ENVIRONMENTAL refusal, not a logic failure. The doctor must render that as INCONCLUSIVE
(a distinct advisory row), never a false DOCTOR-RED indistinguishable from a genuine
safety-floor breach.

The signature is deliberately NARROW (fail-closed toward RED):
  - the explicit `SELFTEST-INCONCLUSIVE` token a scratch-guarded selftest emits, OR
  - a scratch-creation verb (mkdtemp / mktemp / mkdir / TemporaryDirectory) co-occurring
    with the OS-sandbox errno text `Operation not permitted` (EPERM).
It does NOT match `Permission denied` (EACCES) — our own selftests deliberately produce
that via chmod fixtures, and a genuinely failing chmod-fixture assertion must stay RED.
Callers apply this ONLY to a NONZERO selftest exit; a passing run is never reclassified.

Imported by scripts/foundry-doctor.py (check_gate_selftests) and by drop-in checks
(scripts/foundry_checks/*) — a single implementation, no drifting copies.
"""
from __future__ import annotations

import re

# The explicit token a scratch-guarded bash selftest prints before exiting 75 (EX_TEMPFAIL).
INCONCLUSIVE_TOKEN = "SELFTEST-INCONCLUSIVE"

# The advisory detail prefix every caller renders (operators grep for it).
INCONCLUSIVE_PREFIX = "INCONCLUSIVE"

# EX_TEMPFAIL — the sysexits code the scratch guard uses for "could not run (environment)".
EX_TEMPFAIL = 75

_SCRATCH_VERB = r"(?:mkdtemp|mktemp|mkdir|TemporaryDirectory)"
_EPERM_TEXT = r"Operation not permitted"
# Verb and errno within one line, either order (macOS: "mkdir: /x: Operation not permitted";
# glibc: "mktemp: mkdtemp failed on /x: Operation not permitted").
_SIG = re.compile(
    rf"(?:{_SCRATCH_VERB}[^\n]*{_EPERM_TEXT})|(?:{_EPERM_TEXT}[^\n]*{_SCRATCH_VERB})",
    re.IGNORECASE,
)


def is_sandbox_denial(output: str) -> bool:
    """True iff a FAILED selftest's combined output carries the sandbox scratch-denial
    signature. Callers must gate on a nonzero exit code themselves."""
    if not output:
        return False
    if INCONCLUSIVE_TOKEN in output:
        return True
    return bool(_SIG.search(output))


def inconclusive_detail(what: str) -> str:
    """The uniform advisory detail for an INCONCLUSIVE (status None) doctor row."""
    return (f"{INCONCLUSIVE_PREFIX} — {what} could not run: filesystem sandbox denied "
            "scratch creation (mkdtemp/mkdir EPERM); re-run with write access or outside "
            "the sandbox (not a logic failure)")
