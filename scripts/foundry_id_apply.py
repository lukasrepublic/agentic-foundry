#!/usr/bin/env python3
"""foundry_id_apply — the infra APPLY router: the one place the framework runs a mutating infra
command, and it runs the frozen `infra_binding.apply` string exactly as the operator authored it
(feat-foundry-apply-gate-regrounding).

`decide_apply` composes two inputs into a closed `ApplyDecision`:
  * the frozen change scope (`changed_paths`); AND
  * the profile's `infra_binding` — from which `decide_apply` ITSELF derives the GitOps CLASS by
    calling `classify_gitops(changed_paths=..., infra_binding=...)` — NOT a caller-supplied class
    (the §8 Critical: software-delivery never trusts a self-reported flag for a routing decision).
    The signature offers no parameter by which a caller can supply or override the class.

The table is TOTAL: a well-formed `direct` change EXECUTEs unconditionally — the DEFAULT path, with
no second condition anywhere; a well-formed `gitops` change routes to VERIFY_ONLY — a CORRECTNESS
routing (the ArgoCD controller owns reconciliation of that path and a direct apply would race it on
its next sync), never a permission check; and REFUSE fires on exactly FIVE mechanically unresolvable
inputs — an ambiguous/unclassifiable scope (including an EMPTY one), an out-of-set class value, a
missing/empty required `infra_binding` slot for the branch chosen, a malformed `gitops_paths`
declaration, or a `changed_paths` member whose FORM is not a well-formed relative POSIX path — never
on a judgement about the change's content.

`decide_apply` is PURE — decision only, no I/O, runs no command. The EXECUTE command is the FROZEN
`infra_binding.apply` string, operator-authored at profile-authorize time — never freeform text this
router could be tricked into emitting. The operator supplies a correctly configured AWS context whose
IAM restrictions ARE the control on what that command may do; this module never acquires credentials,
establishes connectivity, or verifies/re-derives/second-guesses the operator's context, and it probes
nothing ambient — the router's only inputs are the frozen change scope and the profile.
"""
import fnmatch
import posixpath
import re
from dataclasses import dataclass

# The closed ApplyAction enum — every branch of the total table resolves to exactly one of these.
EXECUTE = "EXECUTE"          # direct (non-GitOps): the framework runs the frozen infra_binding.apply.
VERIFY_ONLY = "VERIFY_ONLY"  # gitops: the ArgoCD controller mutates; the framework only verifies.
REFUSE = "REFUSE"            # the input's FORM was mechanically unresolvable — never a judgement call.

# The closed GitOps classification enum (re-derived by decide_apply itself; never caller-asserted).
GITOPS = "gitops"        # changed_paths non-empty AND all fall under a gitops_paths glob.
DIRECT = "direct"        # changed_paths non-empty AND none falls under a gitops_paths glob.
AMBIGUOUS = "ambiguous"  # mixed/unmatched — OR an EMPTY changed_paths (the vacuous-quantifier guard) → REFUSE.


@dataclass(frozen=True)
class RunbookPayload:
    """The runbook payload carried by an EXECUTE decision.

    `command` is EXACTLY the profile's frozen `infra_binding.apply` string — the same
    operator-authored command the EXECUTE branch runs, never freeform text this router composes or
    text lifted from a change, PR body or spec. `verify` is the DISTINCT read-only
    `infra_binding.verify` slot — the post-apply confirmation the framework runs read-only, never a
    second `plan` call."""
    command: str   # == infra_binding.apply (frozen; never freeform).
    verify: str    # == infra_binding.verify (distinct read-only slot; never .plan).


@dataclass(frozen=True)
class ApplyDecision:
    """The closed apply DECISION record: an `action` enum and the `runbook` payload it carries.

    `runbook` is None for VERIFY_ONLY and REFUSE (nothing is emitted/run). For EXECUTE it is a
    RunbookPayload whose `command` is the FROZEN `infra_binding.apply` and whose `verify` is the
    distinct read-only `infra_binding.verify` — never freeform. `reason` is a human-readable trace of
    which table row fired (audit-ledger evidence). The operator's AWS-context IAM restrictions bound
    what an EXECUTE command may do; this record carries no judgement about that and no field records
    one — there is nothing here for the framework to attest to beyond which row fired."""
    action: str
    runbook: "RunbookPayload | None" = None
    reason: str = ""


def _binding_get(infra_binding, key):
    """Read a slot from infra_binding, tolerating a dict OR an attribute-bearing object. Returns None
    when absent — the caller fail-closes on a missing required slot."""
    if infra_binding is None:
        return None
    if isinstance(infra_binding, dict):
        return infra_binding.get(key)
    return getattr(infra_binding, key, None)


def _gitops_globs(infra_binding):
    """The profile's declared gitops_paths glob list (or an empty tuple). A non-list/absent value yields
    an empty set of globs → every non-empty change classifies `direct` (no false `gitops` route)."""
    globs = _binding_get(infra_binding, "gitops_paths")
    if not isinstance(globs, (list, tuple)):
        return tuple()
    return tuple(g for g in globs if isinstance(g, str) and g)


def _under_any_glob(path, globs):
    """True iff `path` matches any gitops glob. fnmatch with `*` spanning `/` mirrors the profile authors'
    glob intent (e.g. `clusters/**` / `manifests/*`); a leading-`./` is normalized off."""
    if not isinstance(path, str) or not path:
        return False
    p = path[2:] if path.startswith("./") else path
    return any(fnmatch.fnmatch(p, g) for g in globs)


def classify_gitops(*, changed_paths, infra_binding):
    """RE-DERIVE the GitOps class of a change from its FROZEN scope × the profile's gitops_paths globs —
    NOT a caller-supplied boolean (AC-IDAPP-1). Returns one of {"gitops","direct","ambiguous"}:

      * `gitops`    iff `changed_paths` is NON-EMPTY **and ALL** paths fall under a gitops glob;
      * `direct`    iff `changed_paths` is NON-EMPTY **and NONE** does;
      * `ambiguous` otherwise — a MIXED/unmatched set, OR an **EMPTY** `changed_paths`.

    The EMPTY → ambiguous guard is load-bearing (the re-audit High): "all" and "none" both go vacuously
    true on an empty set, so empty MUST resolve to ambiguous ⇒ REFUSE, never a silent EXECUTE/VERIFY
    route. Pure — no I/O.
    """
    # Normalize to a list of non-empty string paths; anything else is not a valid scope member.
    paths = [p for p in (changed_paths or []) if isinstance(p, str) and p.strip()]
    if not paths:
        return AMBIGUOUS  # empty / no valid paths → ambiguous (the vacuous-quantifier guard).

    globs = _gitops_globs(infra_binding)
    if not globs:
        # No gitops globs declared: a non-empty change has none-under-glob → `direct` (never `gitops`).
        return DIRECT

    matches = [_under_any_glob(p, globs) for p in paths]
    if all(matches):
        return GITOPS
    if not any(matches):
        return DIRECT
    return AMBIGUOUS  # mixed (some under a gitops glob, some not) → ambiguous → REFUSE.


def _refuse(reason):
    return ApplyDecision(action=REFUSE, runbook=None, reason=reason)


def _gitops_paths_declared_well_formed(infra_binding):
    """AC-IDAGR-2 condition (iv): `infra_binding.gitops_paths` must be a list whose members are all
    non-empty strings. An EMPTY list is WELL-FORMED — it declares "no GitOps paths". An absent key, a
    non-list value (a one-character YAML slip: a bare string instead of a list), or any non-string /
    empty member is malformed and REFUSEs.

    Checked here — BEFORE the class is derived — rather than inside `classify_gitops`, because
    `classify_gitops` is pinned unchanged (AC-IDAGR-5) and its `_gitops_globs` helper silently yields
    `()` for exactly this malformed input, after which `classify_gitops` returns `direct` for every
    non-empty change: a malformed `gitops_paths` would otherwise route a controller-managed change to
    a direct apply that races ArgoCD rather than refusing."""
    raw = _binding_get(infra_binding, "gitops_paths")
    if not isinstance(raw, list):
        return False
    return all(isinstance(g, str) and g for g in raw)


def _changed_paths_well_formed(changed_paths):
    """Condition (v): every member of `changed_paths` must be REPO-RELATIVE and free of surrounding
    whitespace — the form `classify_gitops`'s globs are written against.

    Why this is a REFUSE and not a normalization: `_under_any_glob` strips only a single leading
    `./`, so an absolute path (`/repo/gitops/apps/root.yaml`), a leading/trailing-whitespace path, or
    a doubled separator matches NO `gitops_paths` glob. A change that is entirely controller-managed
    would then classify `direct` and, with the posture layer gone, EXECUTE the frozen apply with no
    second condition. Silently rewriting a caller's paths would be guessing at intent; refusing on
    the FORM is the same class of mechanical unresolvability as the other four conditions and adds no
    policy judgement. `classify_gitops` is pinned unchanged (AC-IDAGR-5), so the check lives here.
    An EMPTY/None `changed_paths` is NOT rejected here — it is the vacuous-quantifier guard's job,
    handled as condition (i) via `classify_gitops` returning AMBIGUOUS.

    A POSITIVE-form assertion, not a denylist of three spellings. An earlier draft `continue`d on a
    non-string member and let `""` through, on the reasoning that `classify_gitops` filters both. That
    reasoning was WRONG, and the review caught it: the filter drops the member SILENTLY, so
    `[b"clusters/prod/app.yaml", "infra/vpc.tf"]` classifies `direct` on the surviving member alone and
    EXECUTEs against a scope that included a controller-managed manifest — the very defect this check
    exists to close. Anything not a well-formed relative POSIX path now refuses.

    `posixpath.normpath` collapses `././`, `//` and `a/../b` in one predicate, so the check is not a
    guess-list of spellings. `posixpath` (not `os.path`) is deliberate: the module imports nothing from
    the `os` family, and a non-vacuous witness in the test suite enforces that."""
    for p in (changed_paths or []):
        if not isinstance(p, str) or not p:
            return False                       # bytes / None / "" — never silently dropped.
        if p != p.strip() or "\\" in p:        # padded, or a Windows separator the globs never match.
            return False
        if not p.isprintable():
            # Zero-width and other Cf/format characters are NOT whitespace, so `.strip()` leaves them
            # and `normpath` preserves them — `​clusters/x.yaml` would match no glob and classify
            # `direct`. `isprintable()` is False for the whole Cf category, which closes that family.
            return False
        # Mirror `_under_any_glob`: ONE leading `./` is normalized off there, so it is a well-formed
        # form here too and must not be refused. Everything beyond that must already be normal.
        probe = p[2:] if p.startswith("./") else p
        if not probe or probe.startswith(("/", "../")) or probe != posixpath.normpath(probe):
            return False                       # absolute, escaping, or non-normal (`././`, `//`, `a/../b`).
    return True


def decide_apply(*, changed_paths, infra_binding):
    """Map the FROZEN change scope × the profile's `infra_binding` onto a closed `ApplyDecision` by a
    TOTAL, fail-closed table (AC-IDAGR-2). `decide_apply` derives the GitOps class itself
    (AC-IDAGR-10) — `changed_paths` and `infra_binding` are the only inputs; there is no parameter by
    which a caller can supply or override the class.

    The table:
      (iv) `infra_binding.gitops_paths` absent / not a list of non-empty strings  → REFUSE (checked
           FIRST, before the class is derived — an empty list is well-formed and passes this check).
      (i)  the derived class == "ambiguous" (including an EMPTY changed_paths)    → REFUSE.
      (ii) the derived class is outside {"gitops","direct"}                        → REFUSE.
      (b)  class == "gitops"   → VERIFY_ONLY (the ArgoCD controller realizes it; the framework issues
           only the read-only `infra_binding.verify` — a correctness routing, not a permission).
      (c)  class == "direct"   → EXECUTE (runs the frozen infra_binding.apply). This is the DEFAULT
           path: reachable for ANY well-formed direct change, with no further condition.
      (iii) either branch's required `infra_binding` slot (`verify`, or `apply` + `verify` for direct)
           is missing/empty                                                        → REFUSE.

    The EXECUTE runbook `command` is EXACTLY the frozen `infra_binding.apply` (never freeform);
    `verify` is the DISTINCT read-only `infra_binding.verify` slot. Pure — decision only, no I/O, runs
    no command; acquires no credential and establishes no connectivity.
    """
    # (iv) a malformed gitops_paths declaration refuses before the class is derived.
    if not _gitops_paths_declared_well_formed(infra_binding):
        return _refuse(
            "infra_binding.gitops_paths is absent or not a list of non-empty strings "
            "(an empty list is well-formed) → fail-closed REFUSE"
        )

    # (v) a malformed PATH FORM refuses before the class is derived — an absolute or whitespace-padded
    #     member silently matches no glob, which would flip a controller-managed change to `direct`.
    if not _changed_paths_well_formed(changed_paths):
        return _refuse(
            "changed_paths contains a member that is not repo-relative and stripped "
            "(absolute, whitespace-padded, or containing '//') → fail-closed REFUSE"
        )

    gitops_class = classify_gitops(changed_paths=changed_paths, infra_binding=infra_binding)

    # (i) an unclassifiable change (mixed/unmatched, OR empty changed_paths) never routes to a mutation.
    if gitops_class == AMBIGUOUS:
        return _refuse("gitops_class ambiguous (mixed/unmatched/empty changed_paths) → fail-closed REFUSE")

    # (ii) guard against an out-of-set gitops_class value (only gitops/direct survive past (i)).
    if gitops_class not in (GITOPS, DIRECT):
        return _refuse(f"unrecognized gitops_class {gitops_class!r} → fail-closed REFUSE")

    # (b) gitops → VERIFY_ONLY. The ArgoCD controller owns reconciliation of this path; a direct apply
    #     would race it on its next sync. This is a CORRECTNESS routing, not a restriction.
    if gitops_class == GITOPS:
        verify_cmd = _binding_get(infra_binding, "verify")
        if not isinstance(verify_cmd, str) or not verify_cmd.strip():
            return _refuse("VERIFY_ONLY needs infra_binding.verify, missing/empty → fail-closed REFUSE")
        return ApplyDecision(
            action=VERIFY_ONLY,
            runbook=None,
            reason=(
                "gitops change: the ArgoCD controller owns reconciliation of this path and a direct "
                "apply would race it on its next sync → VERIFY_ONLY (a correctness routing)"
            ),
        )

    # (c) direct → EXECUTE. The default path — reachable unconditionally for any well-formed direct
    #     change. Both the apply and verify slots are required to compose the runbook.
    apply_cmd = _binding_get(infra_binding, "apply")
    verify_cmd = _binding_get(infra_binding, "verify")
    if not isinstance(apply_cmd, str) or not apply_cmd.strip():
        return _refuse("direct change needs frozen infra_binding.apply, missing/empty → fail-closed REFUSE")
    if not isinstance(verify_cmd, str) or not verify_cmd.strip():
        return _refuse("direct change needs infra_binding.verify, missing/empty → fail-closed REFUSE")

    return ApplyDecision(
        action=EXECUTE,
        runbook=RunbookPayload(command=apply_cmd, verify=verify_cmd),
        reason="direct change → EXECUTE (the default path): the framework runs the frozen infra_binding.apply",
    )


# ─────────────────────────────────────────────────────────── AC-IDAGR-11: render/scrub (local only) ── #
# MIRRORS the shape of scripts/foundry-fleet-session-machinery.py:43-55 (_SECRET_RE) — NOT imported;
# that file is out of this atom's scope and the gate carries no cross-surface import. The scrub applies
# ONLY to the rendered/logged copy; the EXECUTE branch's runnable command stays the frozen
# infra_binding.apply bytes, unaltered, because a redacted command is not a runnable one.
_SECRET_RE = re.compile(
    r"(?i)(?:sk-[a-z0-9]{16,}|ghp_[a-z0-9]{20,}|gh[opusr]_[a-z0-9]{20,}"
    r"|aws_secret[^\s]*|AKIA[0-9A-Z]{16}|[A-Za-z0-9+/]{40,}={0,2}"
    r"|(?:password|secret|token|api[_-]?key)\s*[:=]\s*\S+)"
)


def _scrub(value):
    """Secret-scrub a single rendered string. Non-strings pass through unchanged."""
    if not isinstance(value, str):
        return value
    return _SECRET_RE.sub("«redacted»", value)


def render_decision(decision):
    """Render an `ApplyDecision` for DISPLAY, a log line, or audit-ledger evidence (AC-IDAGR-11). The
    returned dict's `command` / `verify` / `reason` strings are secret-scrubbed copies — NEVER the
    bytes `decide_apply` returned in `decision.runbook.command`, which the EXECUTE branch runs
    verbatim. Call this ONLY for the rendered/logged form; never substitute its output for
    `decision.runbook.command` when running the command."""
    runbook = decision.runbook
    return {
        "action": decision.action,
        "reason": _scrub(decision.reason),
        "command": _scrub(runbook.command) if runbook is not None else None,
        "verify": _scrub(runbook.verify) if runbook is not None else None,
    }
