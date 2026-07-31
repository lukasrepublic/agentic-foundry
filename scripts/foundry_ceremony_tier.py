#!/usr/bin/env python3
"""foundry_ceremony_tier — the deterministic blast-radius classifier for ceremony tiering
(feat-foundry-ceremony-tiering). Sizes an atom from three declared signals (`files`,
`new_contract_surface`, `ambiguity`), resolves a tier from the closed set
`{trivial, small, standard, large}` under the MAXIMUM rule (never averaged — the FIPS 199
high-water-mark posture), applies the one-way security-sensitivity override (a matched
security-surface path or a blast-radius keyword forces at least `standard`, always up, never
down), and returns the verb mask that tier runs. This module never relaxes a floor: every
returned mask marks `phase0_lints`, `front_authorization`, `merge_floor`, and
`security_review_routing` REQUIRED for every tier in the closed set (AC-CTR-6).

Public API: `classify(files, new_contract_surface, ambiguity, scope_paths=(), spec_text="",
override=None, override_reason=None)` -> `CeremonyTier`. Pure: no wall-clock, no network, no env
var, no filesystem read other than the caller-supplied `--spec`/`--contract` paths (AC-CTR-2).

CLI:
  foundry_ceremony_tier.py --files 3 --ambiguity low [--new-contract-surface]
    [--scope-path <p> ...] [--spec <path>] [--contract <path>]
    [--override <tier> --override-reason <text>]
  # tier: small — 3 files, no new contract, low ambiguity

Prints exactly ONE line to stdout and exits 0 on a normal (or accepted-override) classification
(AC-CTR-8/9); an inadmissible override refuses (AC-CTR-11): nothing is printed to stdout, one
line naming the refused override and its ground goes to stderr, and the process exits non-zero.
The declared-scope tripwire (AC-CTR-14), when `--contract` is supplied, is a separate advisory
stderr-only notice that never changes stdout or the exit code.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field

TIERS = ("trivial", "small", "standard", "large")
_TIER_RANK = {tier: rank for rank, tier in enumerate(TIERS)}

AMBIGUITY_LEVELS = ("none", "low", "medium", "high")

# AC-CTR-7: the verbatim operator skip token the `trivial` mask carries.
TRIVIAL_SKIP_TOKEN_TEMPLATE = "skip review; reason: <one-line>"

# AC-CTR-4 (source-of-truth #1): the path-pattern literal below MUST be equal, AS A STRING, to
# the `grep -E '...'` literal the shipped `security-path` CI gate applies in
# `.github/workflows/btb-gates.yml`. `tests/test_ceremony_tier.py` reads that workflow file and
# asserts the equality directly, so either side drifting fails CI. Do not hand-edit one without
# the other — this is a single source of truth split across two files by construction (see the
# spec's `## Clarifications` — disclosed single point of failure on the CI-side extraction).
SECURITY_PATH_PATTERN = (
    r"(auth|secret|credential|token|provenance|signing|\.rego$)|^\.github/|^hooks/|"
    r"^\.claude-plugin/|(^|/)(standing-versions|profile-version-ledger)|"
    r"(^|/)(requirements[^/]*\.txt|package(-lock)?\.json|pyproject\.toml|Pipfile[^/]*|"
    r"go\.(mod|sum)|Cargo\.(toml|lock))$|^skills/|^agents/|^rulesets/|^scripts/foundry_tier_preflight"
)
_SECURITY_PATH_RE = re.compile(SECURITY_PATH_PATTERN)

# AC-CTR-4 (source-of-truth #2): the closed blast-radius keyword set — the spec's own normative
# table (AC-CTR-3). `tests/test_ceremony_tier.py` carries its own transcribed literal copy and
# asserts set equality, so adding/removing a keyword here without the spec (and the test) is a
# drift the suite catches.
BLAST_RADIUS_KEYWORDS = (
    "auth",
    "authentication",
    "authorization",
    "secret",
    "credential",
    "token",
    "signing",
    "supply-chain",
    "privilege",
    "permission",
)
_KEYWORD_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in BLAST_RADIUS_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


class OverrideRefused(Exception):
    """Raised by `classify()` when an operator-supplied override is inadmissible (AC-CTR-11):
    an override below `standard` while the security trigger fired, or a reason that is empty,
    whitespace-only, or not a single line. `classified_tier` is the tier `classify()` retains."""

    def __init__(self, ground, refused_tier, classified_tier):
        self.ground = ground
        self.refused_tier = refused_tier
        self.classified_tier = classified_tier
        super().__init__(
            f"ceremony-tier override refused: {refused_tier!r} — {ground} "
            f"(classified tier retained: {classified_tier})"
        )


@dataclass(frozen=True)
class CeremonyTier:
    """The classifier's result record. `tier` is the FINAL (post-override, if any) tier the
    caller should act on; `classified_tier` is always the tier the three signals + the security
    trigger resolved, independent of any override. `mask`'s floor fields are `True` for every
    tier (AC-CTR-6); `mask['security_question']` is `True` iff the sensitivity trigger fired,
    independent of `tier`/`classified_tier`/any override (AC-CTR-3)."""

    tier: str
    classified_tier: str
    signals: dict = field(default_factory=dict)
    triggers: dict = field(default_factory=dict)
    mask: dict = field(default_factory=dict)
    line: str = ""
    override: dict = None


def _tier_from_files(files):
    if files <= 1:
        return "trivial"
    if files <= 5:
        return "small"
    if files <= 15:
        return "standard"
    return "large"


def _tier_from_contract_surface(new_contract_surface):
    return "standard" if new_contract_surface else "trivial"


def _tier_from_ambiguity(ambiguity):
    mapping = {"none": "trivial", "low": "small", "medium": "standard", "high": "large"}
    if ambiguity not in mapping:
        raise ValueError(f"ambiguity must be one of {AMBIGUITY_LEVELS!r}, got {ambiguity!r}")
    return mapping[ambiguity]


def _max_tier(tiers):
    return max(tiers, key=_TIER_RANK.get)


def _detect_security_trigger(scope_paths, spec_text):
    """Returns `(kind, matched)` — `kind` is `"path"`/`"keyword"`/`None`. Path patterns are
    checked before keywords (deterministic priority; the mask/line results are the same either
    way since only one match is named)."""
    for path in scope_paths:
        if _SECURITY_PATH_RE.search(path):
            return "path", path
    match = _KEYWORD_RE.search(spec_text or "")
    if match:
        return "keyword", match.group(1).lower()
    return None, None


def mask_for(tier, security_fired):
    """The verb mask for `tier` (the mask table in the spec's normative region), with the
    security-question flag set independently from the trigger (AC-CTR-3/AC-CTR-6)."""
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {TIERS!r}, got {tier!r}")
    mask = {
        # AC-CTR-6: the floor, required at every tier, no exceptions.
        "phase0_lints": True,
        "front_authorization": True,
        "merge_floor": True,
        "security_review_routing": True,
        # AC-CTR-3: orthogonal to the tier — a pure function of the trigger, nothing else.
        "security_question": bool(security_fired),
    }
    if tier == "trivial":
        mask.update(
            spec_review="skipped-pending-operator-token",
            phase1_questions=(),
            skip_token=TRIVIAL_SKIP_TOKEN_TEMPLATE,
            decomposition_check=False,
        )
    elif tier == "small":
        mask.update(
            spec_review="required",
            phase1_questions=("steel_man_adversarial",),
            skip_token=None,
            decomposition_check=False,
        )
    elif tier == "standard":
        mask.update(
            spec_review="required",
            phase1_questions=("prior_art", "steel_man_adversarial", "per_ac_rubric"),
            skip_token=None,
            decomposition_check=False,
        )
    else:  # large
        mask.update(
            spec_review="required",
            phase1_questions=("prior_art", "steel_man_adversarial", "per_ac_rubric"),
            skip_token=None,
            decomposition_check=True,
        )
    return mask


def _contract_phrase(new_contract_surface):
    return "new contract surface" if new_contract_surface else "no new contract"


def _render_line(files, new_contract_surface, ambiguity, tier, security_fired, security_match,
                  override_info=None):
    contract_phrase = _contract_phrase(new_contract_surface)
    if override_info:
        head = (
            f"tier: {override_info['adopted']} (classified {override_info['classified']} "
            f"→ adopted {override_info['adopted']}, override reason: "
            f"{override_info['reason']})"
        )
    else:
        head = f"tier: {tier}"
    line = f"{head} — {files} files, {contract_phrase}, {ambiguity} ambiguity"
    if security_fired:
        line += f" (security override: {security_match})"
    return line


def _validate_override_reason(override_reason):
    """Returns the stripped reason if admissible, else `None`."""
    if override_reason is None:
        return None
    stripped = override_reason.strip()
    if not stripped:
        return None
    if "\n" in stripped:
        return None
    return stripped


def classify(files, new_contract_surface, ambiguity, scope_paths=(), spec_text="",
             override=None, override_reason=None):
    """The classifier's pure function (AC-CTR-1/AC-CTR-2). Returns a `CeremonyTier`. Raises
    `OverrideRefused` (AC-CTR-11) when an override is supplied but inadmissible — the caller
    (the CLI) is responsible for translating that into a non-zero exit."""
    if not isinstance(files, int) or isinstance(files, bool) or files < 0:
        raise ValueError(f"files must be a non-negative int, got {files!r}")

    scope_paths = tuple(scope_paths)
    files_tier = _tier_from_files(files)
    contract_tier = _tier_from_contract_surface(new_contract_surface)
    ambiguity_tier = _tier_from_ambiguity(ambiguity)
    base_tier = _max_tier((files_tier, contract_tier, ambiguity_tier))

    security_kind, security_match = _detect_security_trigger(scope_paths, spec_text)
    security_fired = security_kind is not None

    classified_tier = base_tier
    if security_fired and _TIER_RANK[classified_tier] < _TIER_RANK["standard"]:
        classified_tier = "standard"

    signals = {
        "files": files,
        "files_tier": files_tier,
        "new_contract_surface": bool(new_contract_surface),
        "contract_tier": contract_tier,
        "ambiguity": ambiguity,
        "ambiguity_tier": ambiguity_tier,
        "base_tier": base_tier,
    }
    triggers = {
        "security_fired": security_fired,
        "security_kind": security_kind,
        "security_match": security_match,
    }

    adopted_tier = classified_tier
    override_info = None
    if override is not None:
        if override not in TIERS:
            raise ValueError(f"override must be one of {TIERS!r}, got {override!r}")
        reason = _validate_override_reason(override_reason)
        if reason is None:
            raise OverrideRefused(
                "override reason must be a single line with at least one non-whitespace "
                "character",
                override,
                classified_tier,
            )
        if security_fired and _TIER_RANK[override] < _TIER_RANK["standard"]:
            raise OverrideRefused(
                "security trigger fired — an override below standard is inadmissible",
                override,
                classified_tier,
            )
        adopted_tier = override
        override_info = {"classified": classified_tier, "adopted": override, "reason": reason}

    mask = mask_for(adopted_tier, security_fired)
    line = _render_line(
        files, new_contract_surface, ambiguity, adopted_tier, security_fired, security_match,
        override_info=override_info,
    )

    return CeremonyTier(
        tier=adopted_tier,
        classified_tier=classified_tier,
        signals=signals,
        triggers=triggers,
        mask=mask,
        line=line,
        override=override_info,
    )


def scope_tripwire(files, contract_path):
    """AC-CTR-14: an advisory-only notice (never mutates stdout/exit code). Returns the notice
    string when the declared `files` signal under-states the contract's `scope.allowed_paths`
    count by more than the 1-entry tolerance, else `None`. Silent (returns `None`) when
    `contract_path` is falsy or does not exist — `/foundry:intake` passes the sibling contract
    only when one already exists beside the spec."""
    if not contract_path or not os.path.isfile(contract_path):
        return None
    import yaml  # repo-pinned PyYAML (docs/standing-versions.md) — already a project dependency

    with open(contract_path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    allowed_paths = ((data.get("scope") or {}).get("allowed_paths") or [])
    n_allowed = len(allowed_paths)
    if files < (n_allowed - 1):
        return f"scope-signal mismatch: declared files={files}, allowed_paths={n_allowed}"
    return None


def _build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="foundry_ceremony_tier.py",
        description="Blast-radius-graduated ceremony classifier (feat-foundry-ceremony-tiering).",
    )
    parser.add_argument("--files", type=int, required=True, help="estimated files touched")
    parser.add_argument("--ambiguity", choices=AMBIGUITY_LEVELS, required=True)
    parser.add_argument("--new-contract-surface", action="store_true", dest="new_contract_surface")
    parser.add_argument("--scope-path", action="append", default=[], dest="scope_paths",
                         help="repeatable; a declared-scope path (checked against the "
                              "security-surface pattern)")
    parser.add_argument("--spec", help="path to the spec text (checked for blast-radius keywords)")
    parser.add_argument("--contract", help="path to the sibling acceptance-contract.yaml "
                                            "(the AC-CTR-14 scope tripwire)")
    parser.add_argument("--override", choices=TIERS, default=None)
    parser.add_argument("--override-reason", default=None, dest="override_reason")
    return parser


def main(argv=None):
    args = _build_arg_parser().parse_args(argv)

    spec_text = ""
    if args.spec:
        with open(args.spec, encoding="utf-8") as handle:
            spec_text = handle.read()

    try:
        record = classify(
            args.files,
            args.new_contract_surface,
            args.ambiguity,
            scope_paths=tuple(args.scope_paths),
            spec_text=spec_text,
            override=args.override,
            override_reason=args.override_reason,
        )
    except OverrideRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.contract:
        notice = scope_tripwire(args.files, args.contract)
        if notice:
            print(notice, file=sys.stderr)

    print(record.line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
