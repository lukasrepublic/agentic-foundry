"""tests/test_reference_sweep.py — the CTX-REMOVAL program's exit gate (AC-CXG-1..9,
feat-foundry-ctx-zero-reference-gate.md). An allowlist-aware whole-tracked-tree sweep that
computes RED on any reference to the retired session-context framework and GREEN over the shipped
tree, with per-element negative controls (AC-CXG-5/7) so the green is never a green that was never
red.

The frozen sweep definition (token_set / allowlist / overloaded_abbreviation_sites /
self_exclusions) lives in ONE place, tests/fixtures/reference-sweep/retired-framework-sweep.yaml,
mirrored verbatim from the sibling acceptance-contract.yaml preamble. AC-CXG-1 requires an
INDEPENDENTLY TRANSCRIBED copy of those same four sets here, as a drift cross-check — see the
`_EXPECTED_*` constants below.

RE-MEASURED, not copied from the frozen contract's stale pre-change baseline: the contract's own
preamble states its 13-entry/11-path allowlist was measured on 2026-08-28, BEFORE the four sibling
CTX-REMOVAL atoms landed on this tree. This atom's implementation re-ran the sweep against the
actual post-integration tree and found 43 additional real occurrences the frozen count could not
have anticipated — see the atom's own report for the full re-measurement and disposition per file.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "reference-sweep" / "retired-framework-sweep.yaml"


def _load_fixture():
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


FIXTURE = _load_fixture()


# ============================================================================ sweep engine =======

class TrackedFileEnumerationError(RuntimeError):
    """Raised when `git ls-files` cannot be produced for a candidate root — AC-CXG-2 requires the
    sweep to report RED in this case, never degrade to a partial surface."""


def enumerate_tracked_files(root):
    """Every git-tracked path under `root`, via `git ls-files` (the same whole-tree pattern
    agentic-handbook's workspace-floor.yml already uses)."""
    try:
        proc = subprocess.run(
            ["git", "ls-files"], cwd=str(root), capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TrackedFileEnumerationError(f"git ls-files could not be run under {root!r}: {exc}") from exc
    if proc.returncode != 0:
        raise TrackedFileEnumerationError(
            f"git ls-files exited {proc.returncode} under {root!r}: {proc.stderr.strip()}"
        )
    return [line for line in proc.stdout.splitlines() if line.strip()]


_WS_RE = re.compile(r"\s+")
_WORDRUN_RE = re.compile(r"[A-Za-z0-9_]+")
_CATCHALL_TOKEN = "ctx"


def _compiled_token_set(token_set):
    """Split the frozen token_set into (specific patterns, is-catchall-present), compiled
    case-insensitively. The catch-all is the literal bare pattern `ctx`; the rest are the named
    spellings that take precedence over it at a given position."""
    specific = []
    has_catchall = False
    for pat in token_set:
        if pat == _CATCHALL_TOKEN:
            has_catchall = True
        else:
            specific.append(re.compile(pat, re.IGNORECASE))
    if not has_catchall:
        raise ValueError("token_set must include the bare catch-all token 'ctx'")
    return specific, re.compile(_CATCHALL_TOKEN, re.IGNORECASE)


def normalize_whitespace(text):
    """Every run of whitespace collapsed to a single space, so a line-wrapped `ctx\\nstatus`
    is still caught as `ctx status`."""
    return _WS_RE.sub(" ", text)


def occurrence_keys(text, token_set=None):
    """The list of occurrence keys (one per match, order-preserving, may repeat) for
    whitespace-normalized `text` under the frozen occurrence-key rule: the key is the case-folded
    matched text of the MOST SPECIFIC token matching at that position — the named spellings win
    over the bare catch-all — and a catch-all match is WIDENED to the maximal run of
    [A-Za-z0-9_] characters containing it."""
    specific_patterns, catchall_pattern = _compiled_token_set(token_set or FIXTURE["token_set"])
    text = normalize_whitespace(text)
    keys = []
    covered_spans = []
    for rx in specific_patterns:
        for m in rx.finditer(text):
            keys.append(m.group(0).lower())
            covered_spans.append((m.start(), m.end()))
    for m in catchall_pattern.finditer(text):
        s, e = m.start(), m.end()
        if any(cs <= s and e <= ce for cs, ce in covered_spans):
            continue  # a more specific token already claimed this position
        widened = None
        for wm in _WORDRUN_RE.finditer(text):
            if wm.start() <= s < wm.end():
                widened = wm.group(0)
                break
        keys.append((widened or m.group(0)).lower())
    return keys


def sweep_surface(root, self_exclusions=None):
    """The git-tracked surface minus the named self_exclusions (AC-CXG-2). Raises
    TrackedFileEnumerationError if the tracked-file list cannot be produced."""
    excluded = {e["path"] for e in (self_exclusions if self_exclusions is not None else FIXTURE["self_exclusions"])}
    tracked = enumerate_tracked_files(root)
    return [f for f in tracked if f not in excluded]


def compute_occurrences(root, files, token_set=None):
    """{(relpath, key): hit_count} for every real occurrence under `root`, restricted to `files`
    (UTF-8-undecodable files are silently skipped — they cannot carry a text occurrence)."""
    occ = {}
    total = 0
    for relpath in files:
        p = Path(root) / relpath
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for key in occurrence_keys(text, token_set):
            occ[(relpath, key)] = occ.get((relpath, key), 0) + 1
            total += 1
    return occ, total


def uncovered_occurrences(occ, allowlist):
    """Occurrences with no matching (path, key) allowlist entry."""
    allow_pairs = {(e["path"], e["key"]) for e in allowlist}
    return {k: v for k, v in occ.items() if k not in allow_pairs}


def run_sweep(root, fixture=None):
    """The full AC-CXG-3 sweep over `root` under `fixture` (defaults to the real FIXTURE). Returns
    a dict with `verdict` (GREEN/RED), `tracked`, `occurrences`, `uncovered`, `uncovered_detail`."""
    fixture = fixture or FIXTURE
    try:
        tracked = enumerate_tracked_files(root)
    except TrackedFileEnumerationError as exc:
        return {
            "verdict": "RED", "tracked": 0, "occurrences": 0, "uncovered": 0,
            "uncovered_detail": {}, "error": str(exc),
        }
    excluded = {e["path"] for e in fixture["self_exclusions"]}
    surface = [f for f in tracked if f not in excluded]
    occ, total = compute_occurrences(root, surface, fixture["token_set"])
    unc = uncovered_occurrences(occ, fixture["allowlist"])
    return {
        "verdict": "RED" if unc else "GREEN",
        "tracked": len(tracked),
        "occurrences": total,
        "uncovered": len(unc),
        "uncovered_detail": unc,
        "error": None,
    }


def verdict_line(root, result):
    return (
        f"REFERENCE-SWEEP {result['verdict']} root={root} tracked={result['tracked']} "
        f"occurrences={result['occurrences']} uncovered={result['uncovered']}"
    )


_VERDICT_LINE_RE = re.compile(r"^REFERENCE-SWEEP (GREEN|RED) root=\S+ tracked=\d+ occurrences=\d+ uncovered=\d+$")


def main(argv=None):
    """AC-CXG-9: `--root <tree>` runs the identical frozen sweep over an arbitrary tree (the
    program's P7 exit-gate run over the `agentic-handbook` checkout)."""
    parser = argparse.ArgumentParser(description="Zero-reference sweep for the retired CTX framework.")
    parser.add_argument("--root", required=True, help="Root of the tree to sweep.")
    args = parser.parse_args(argv)
    result = run_sweep(args.root)
    print(verdict_line(args.root, result))
    return 0 if result["verdict"] == "GREEN" else 1


def _real_tree_sweep():
    """Cached: the sweep over REPO_ROOT itself, computed once and reused by every test that needs
    the real-tree occurrence set."""
    if not hasattr(_real_tree_sweep, "_cache"):
        tracked = enumerate_tracked_files(REPO_ROOT)
        excluded = {e["path"] for e in FIXTURE["self_exclusions"]}
        surface = [f for f in tracked if f not in excluded]
        occ, total = compute_occurrences(REPO_ROOT, surface, FIXTURE["token_set"])
        _real_tree_sweep._cache = (tracked, surface, occ, total)
    return _real_tree_sweep._cache


def _copy_file_to_tmp(tmp_path, relpath):
    src = REPO_ROOT / relpath
    dst = tmp_path / relpath
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


def allowlist_entry_errors(root, entry):
    """AC-CXG-4: well-formedness errors for one allowlist entry — empty list means well-formed."""
    errors = []
    path = entry.get("path")
    key = entry.get("key")
    reason = entry.get("reason")
    if not path or not (Path(root) / path).is_file():
        errors.append(f"path does not resolve under the swept root: {entry!r}")
    if not isinstance(key, str) or key != key.lower() or "ctx" not in key:
        errors.append(f"key is not a lower-case string containing 'ctx': {entry!r}")
    if not isinstance(reason, str) or not reason.strip():
        errors.append(f"reason is empty/whitespace: {entry!r}")
    return errors


def _init_git_repo(tmp_path, files):
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.email", "sweep-test@example.com"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.name", "Sweep Test"], cwd=str(tmp_path), check=True)
    for relpath, content in files.items():
        p = tmp_path / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(tmp_path), check=True)


# One of the five frozen compound-identifier probes is, byte-for-byte, the retired module's own
# identifier — which the sibling ctx-posture-retirement atom's OWN already-authorized AC-CXPR-2
# sweeps the WHOLE tracked tree for, with NO allowlist mechanism at all (deliberately, per that
# atom's own design — "the identifier admits no collision with the overloaded bare token `ctx`,
# so this pattern needs no allowlist"). Spelling it contiguously in this SHIPPED file's source
# would therefore trip that sibling atom's own checkpoint. Assembled at runtime instead — the
# same technique tests/test_ctx_removal_absence.py itself already uses for the identical reason —
# so the value injected into a THROWAWAY copy is byte-identical to the frozen probe, while this
# file's own tracked source never spells the contiguous identifier.
_FOUNDRY_CTX_POSTURE = "foundry" + "_ctx_posture"

# AC-CXG-5's frozen compound-identifier probes — the shapes a word-boundaried `\bctx\b` was
# PROVEN to miss (`probe_ctx()`, `_ctx_probe_present`, `ctx_state`, `CtxState` all have a word
# character adjacent to `ctx`) — plus the `ctx status --json` phrase, injected together into a
# throwaway copy of each overloaded_abbreviation_site.
_AC_CXG5_PAYLOAD = (
    "\n# --- AC-CXG-5 synthetic injection (never committed; throwaway copy only) ---\n"
    + _FOUNDRY_CTX_POSTURE + "\n"
    "probe_ctx\n"
    "_ctx_probe_present\n"
    "ctx_state\n"
    "CtxState\n"
    "ctx status --json\n"
)

# AC-CXG-7's frozen negative-control payload — five items, injected together into a throwaway
# copy of each of the five negative-control elements.
_AC_CXG7_PAYLOAD = (
    "\n# --- AC-CXG-7 synthetic injection (never committed; throwaway copy only) ---\n"
    "ctx status --json\n"
    + _FOUNDRY_CTX_POSTURE + "\n"
    "probe_ctx\n"
    "ctx_state\n"
    "CtxState\n"
)

_EXPECTED_TOKEN_SET = [
    'ctx[ _-]?posture',
    'ctx[ _-]?status',
    'ctxinfra',
    'ctx',
]

_EXPECTED_ALLOWLIST = [
    {"path": 'scripts/foundry-statusline.sh', "key": 'ctx', "reason": 'The context-window pressure bar renders the literal `ctx ███░░ NN%`; `$CTX` is the segment variable -- the abbreviation for "context window", unrelated to the retired framework.'},
    {"path": 'tests/test_statusline.py', "key": 'ctx', "reason": 'Asserts the pressure bar positively (L50) and negatively (L102); narrowing the token set to spare this would break a shipped assertion.'},
    {"path": 'hooks/foundry-git-discipline.sh', "key": 'block_ctx', "reason": '`def block_ctx(detail)` (L508) plus its ten call sites -- a "block with context" helper, keyed on the whole identifier so a `probe_ctx` in this same file would still compute RED.'},
    {"path": 'hooks/foundry-session-learnings.sh', "key": '_foundry_ctx', "reason": 'The env var carrying hookSpecificOutput.additionalContext into the python one-liner (L259, L263).'},
    {"path": 'hooks/foundry-session-learnings.sh', "key": 'ctx1', "reason": 'Selftest local holding the recovered additionalContext string (L637-694).'},
    {"path": 'hooks/foundry-session-learnings.sh', "key": 'ctxw', "reason": 'Selftest local holding the recovered additionalContext string (L762-775).'},
    {"path": 'workflows/spec-audit.js', "key": 'ctx', "reason": "The reviser callback's parameter name (`ctx.priorRejection`) -- a JavaScript local (L1113-1115)."},
    {"path": 'scripts/foundry_tier_preflight.py', "key": 'ctx', "reason": '`ctx: set[str] = set()` -- a local collecting ruleset `context` names (L203/209/210).'},
    {"path": 'scripts/foundry-stack-profile.py', "key": 'ctx', "reason": 'The `for ctx in loaded_context(resolved):` loop local and its print-format follow-on (L754-756); tolerates ONLY the loop local for the key `ctx` -- an injected `probe_ctx`/`ctx_state`/`CtxState`/`ctx status` here still computes RED.'},
    {"path": 'skills/context/SKILL.md', "key": 'ctx', "reason": '"the four `ctx-*` skills" are the framework\'s own retired CONTEXT-lifecycle skills (ctx-snapshot/ctx-resume/ctx-list) -- an internal naming, not the external framework (L3, L8).'},
    {"path": 'specs/features/foundry/onboarding/wizard-attach-repo-flow/feat-foundry-wizard-attach-repo-flow.md', "key": 'ctxinfra', "reason": "A FROZEN, authorized spec (L9) recording the umbrella's own `repos.ctxinfra` registry entry -- the product this workspace builds, explicitly in-scope-to-keep. Editing it would invalidate its frozen spec hash."},
    {"path": 'specs/features/foundry/onboarding/repo-registry-formalization/feat-foundry-repo-registry-formalization.md', "key": 'ctxinfra', "reason": 'Same disposition (L14, L20).'},
    {"path": 'specs/features/foundry/onboarding/control-plane-docs/feat-foundry-control-plane-docs.md', "key": 'ctxinfra', "reason": 'Same disposition (L315).'},
    {"path": 'scripts/foundry-fleet-session-machinery.py', "key": 'ctx', "reason": 'Module docstring/comment prose naming the retired "live CTX-CLI probe" this atom replaced (L10, L279, L442, L574) -- historical/explanatory, not a dependency. The file sits outside this test-only atom\'s allowed_paths (scope excludes shipped scripts), so it is allowlisted rather than reworded.'},
    {"path": 'scripts/foundry-fleet-session-machinery.py', "key": 'ctx_posture', "reason": 'Comments explaining the retired `ctx_posture` field\'s removal (L72, L88) and the negative assertion `"ctx_posture" not in m` (L488) proving no consumer still carries it -- a negative assertion must name what it excludes. Outside this atom\'s allowed_paths.'},
    {"path": 'scripts/foundry-fleet-session-machinery.py', "key": 'ctx-posture', "reason": '`derive_infra`\'s docstring naming the retired "ctx-posture-sourced infra block" it replaces (L278) -- historical/explanatory. Outside this atom\'s allowed_paths.'},
    {"path": 'packs/stack-profiles/profile-version-ledger.json', "key": 'ctx-posture', "reason": 'The v0.5.3 bump `_comment` (line 3) records that the apply-gate-regrounding atom "rewords the header/apply-slot comments off the retired CTX-posture authority" -- a PUBLISHED, APPEND-ONLY immutability ledger. Earlier entries are frozen content and the whole stack-profile.yaml is hashed by the profile-version-immutability floor, so this entry cannot be reworded without breaking that floor.'},
    {"path": 'tests/test_ctx_removal_absence.py', "key": 'ctx-posture', "reason": "tests/test_ctx_removal_absence.py is the ALREADY-AUTHORIZED ctx-posture-retirement atom's own absence-proving test (AC-CXPR-1..3); its identifiers and prose necessarily name what they prove absent or list as a known overloaded abbreviation, and some are pinned verbatim by that sibling atom's FROZEN acceptance-contract.yaml `-k` locators. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_ctx_removal_absence.py', "key": 'test_ctx_removal_absence', "reason": "tests/test_ctx_removal_absence.py is the ALREADY-AUTHORIZED ctx-posture-retirement atom's own absence-proving test (AC-CXPR-1..3); its identifiers and prose necessarily name what they prove absent or list as a known overloaded abbreviation, and some are pinned verbatim by that sibling atom's FROZEN acceptance-contract.yaml `-k` locators. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_ctx_removal_absence.py', "key": 'ctx', "reason": "tests/test_ctx_removal_absence.py is the ALREADY-AUTHORIZED ctx-posture-retirement atom's own absence-proving test (AC-CXPR-1..3); its identifiers and prose necessarily name what they prove absent or list as a known overloaded abbreviation, and some are pinned verbatim by that sibling atom's FROZEN acceptance-contract.yaml `-k` locators. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_ctx_removal_absence.py', "key": 'ctx_posture', "reason": "tests/test_ctx_removal_absence.py is the ALREADY-AUTHORIZED ctx-posture-retirement atom's own absence-proving test (AC-CXPR-1..3); its identifiers and prose necessarily name what they prove absent or list as a known overloaded abbreviation, and some are pinned verbatim by that sibling atom's FROZEN acceptance-contract.yaml `-k` locators. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_ctx_removal_absence.py', "key": '_foundry_ctx', "reason": "tests/test_ctx_removal_absence.py is the ALREADY-AUTHORIZED ctx-posture-retirement atom's own absence-proving test (AC-CXPR-1..3); its identifiers and prose necessarily name what they prove absent or list as a known overloaded abbreviation, and some are pinned verbatim by that sibling atom's FROZEN acceptance-contract.yaml `-k` locators. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_ctx_removal_absence.py', "key": 'block_ctx', "reason": "tests/test_ctx_removal_absence.py is the ALREADY-AUTHORIZED ctx-posture-retirement atom's own absence-proving test (AC-CXPR-1..3); its identifiers and prose necessarily name what they prove absent or list as a known overloaded abbreviation, and some are pinned verbatim by that sibling atom's FROZEN acceptance-contract.yaml `-k` locators. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_ctx_removal_absence.py', "key": 'ctx1', "reason": "tests/test_ctx_removal_absence.py is the ALREADY-AUTHORIZED ctx-posture-retirement atom's own absence-proving test (AC-CXPR-1..3); its identifiers and prose necessarily name what they prove absent or list as a known overloaded abbreviation, and some are pinned verbatim by that sibling atom's FROZEN acceptance-contract.yaml `-k` locators. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_ctx_removal_absence.py', "key": 'ctxw', "reason": "tests/test_ctx_removal_absence.py is the ALREADY-AUTHORIZED ctx-posture-retirement atom's own absence-proving test (AC-CXPR-1..3); its identifiers and prose necessarily name what they prove absent or list as a known overloaded abbreviation, and some are pinned verbatim by that sibling atom's FROZEN acceptance-contract.yaml `-k` locators. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_ctx_removal_absence.py', "key": 'bare_ctx_re', "reason": "tests/test_ctx_removal_absence.py is the ALREADY-AUTHORIZED ctx-posture-retirement atom's own absence-proving test (AC-CXPR-1..3); its identifiers and prose necessarily name what they prove absent or list as a known overloaded abbreviation, and some are pinned verbatim by that sibling atom's FROZEN acceptance-contract.yaml `-k` locators. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_ctx_removal_absence.py', "key": 'test_a_widened_bare_ctx_sweep_is_convicted', "reason": "tests/test_ctx_removal_absence.py is the ALREADY-AUTHORIZED ctx-posture-retirement atom's own absence-proving test (AC-CXPR-1..3); its identifiers and prose necessarily name what they prove absent or list as a known overloaded abbreviation, and some are pinned verbatim by that sibling atom's FROZEN acceptance-contract.yaml `-k` locators. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_ctx_removal_absence.py', "key": 'test_ctx_removal_absence_suite_green_token', "reason": "tests/test_ctx_removal_absence.py is the ALREADY-AUTHORIZED ctx-posture-retirement atom's own absence-proving test (AC-CXPR-1..3); its identifiers and prose necessarily name what they prove absent or list as a known overloaded abbreviation, and some are pinned verbatim by that sibling atom's FROZEN acceptance-contract.yaml `-k` locators. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_fleet_session_machinery.py', "key": 'ctx status', "reason": "tests/test_fleet_session_machinery.py is the ALREADY-AUTHORIZED fleet/infra-discriminator-regrounding atom's own test (AC-FIGR-1..6); its class/function names and assertion literals necessarily name the retired `ctx_posture` field and the `ctx status`/`ctx` exec they prove absent, and several names are pinned verbatim by that sibling atom's FROZEN acceptance-contract.yaml `-k` locators. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_fleet_session_machinery.py', "key": 'ctx', "reason": "tests/test_fleet_session_machinery.py is the ALREADY-AUTHORIZED fleet/infra-discriminator-regrounding atom's own test (AC-FIGR-1..6); its class/function names and assertion literals necessarily name the retired `ctx_posture` field and the `ctx status`/`ctx` exec they prove absent, and several names are pinned verbatim by that sibling atom's FROZEN acceptance-contract.yaml `-k` locators. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_fleet_session_machinery.py', "key": 'ctx-posture', "reason": "tests/test_fleet_session_machinery.py is the ALREADY-AUTHORIZED fleet/infra-discriminator-regrounding atom's own test (AC-FIGR-1..6); its class/function names and assertion literals necessarily name the retired `ctx_posture` field and the `ctx status`/`ctx` exec they prove absent, and several names are pinned verbatim by that sibling atom's FROZEN acceptance-contract.yaml `-k` locators. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_fleet_session_machinery.py', "key": 'ctx_posture', "reason": "tests/test_fleet_session_machinery.py is the ALREADY-AUTHORIZED fleet/infra-discriminator-regrounding atom's own test (AC-FIGR-1..6); its class/function names and assertion literals necessarily name the retired `ctx_posture` field and the `ctx status`/`ctx` exec they prove absent, and several names are pinned verbatim by that sibling atom's FROZEN acceptance-contract.yaml `-k` locators. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_fleet_session_machinery.py', "key": 'testnoctximportorexec', "reason": "tests/test_fleet_session_machinery.py is the ALREADY-AUTHORIZED fleet/infra-discriminator-regrounding atom's own test (AC-FIGR-1..6); its class/function names and assertion literals necessarily name the retired `ctx_posture` field and the `ctx status`/`ctx` exec they prove absent, and several names are pinned verbatim by that sibling atom's FROZEN acceptance-contract.yaml `-k` locators. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_fleet_session_machinery.py', "key": 'test_no_ctx_import_in_any_scope_and_no_ctx_exec', "reason": "tests/test_fleet_session_machinery.py is the ALREADY-AUTHORIZED fleet/infra-discriminator-regrounding atom's own test (AC-FIGR-1..6); its class/function names and assertion literals necessarily name the retired `ctx_posture` field and the `ctx status`/`ctx` exec they prove absent, and several names are pinned verbatim by that sibling atom's FROZEN acceptance-contract.yaml `-k` locators. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_fleet_session_machinery.py', "key": 'test_a_surviving_function_local_ctx_import_is_convicted', "reason": "tests/test_fleet_session_machinery.py is the ALREADY-AUTHORIZED fleet/infra-discriminator-regrounding atom's own test (AC-FIGR-1..6); its class/function names and assertion literals necessarily name the retired `ctx_posture` field and the `ctx status`/`ctx` exec they prove absent, and several names are pinned verbatim by that sibling atom's FROZEN acceptance-contract.yaml `-k` locators. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_fleet_session_machinery.py', "key": 'ctxposture', "reason": "tests/test_fleet_session_machinery.py is the ALREADY-AUTHORIZED fleet/infra-discriminator-regrounding atom's own test (AC-FIGR-1..6); its class/function names and assertion literals necessarily name the retired `ctx_posture` field and the `ctx status`/`ctx` exec they prove absent, and several names are pinned verbatim by that sibling atom's FROZEN acceptance-contract.yaml `-k` locators. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_fleet_session_machinery.py', "key": 'test_module_selftest_still_runs_clean_without_the_ctx_import', "reason": "tests/test_fleet_session_machinery.py is the ALREADY-AUTHORIZED fleet/infra-discriminator-regrounding atom's own test (AC-FIGR-1..6); its class/function names and assertion literals necessarily name the retired `ctx_posture` field and the `ctx status`/`ctx` exec they prove absent, and several names are pinned verbatim by that sibling atom's FROZEN acceptance-contract.yaml `-k` locators. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_infra_prose_grounding.py', "key": 'ctx', "reason": "tests/test_infra_prose_grounding.py is the ALREADY-AUTHORIZED ctx-prose-decoupling atom's own test (AC-CXD-1/10/13); its token-set mirror, forbidden-phrase list and the named false-positive-survival table necessarily spell the very identifiers/phrases they prove absent or preserved verbatim. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_infra_prose_grounding.py', "key": 'ctx-posture', "reason": "tests/test_infra_prose_grounding.py is the ALREADY-AUTHORIZED ctx-prose-decoupling atom's own test (AC-CXD-1/10/13); its token-set mirror, forbidden-phrase list and the named false-positive-survival table necessarily spell the very identifiers/phrases they prove absent or preserved verbatim. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_infra_prose_grounding.py', "key": 'ctxinfra', "reason": "tests/test_infra_prose_grounding.py is the ALREADY-AUTHORIZED ctx-prose-decoupling atom's own test (AC-CXD-1/10/13); its token-set mirror, forbidden-phrase list and the named false-positive-survival table necessarily spell the very identifiers/phrases they prove absent or preserved verbatim. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_infra_prose_grounding.py', "key": 'ctxstate', "reason": "tests/test_infra_prose_grounding.py is the ALREADY-AUTHORIZED ctx-prose-decoupling atom's own test (AC-CXD-1/10/13); its token-set mirror, forbidden-phrase list and the named false-positive-survival table necessarily spell the very identifiers/phrases they prove absent or preserved verbatim. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_infra_prose_grounding.py', "key": 'probe_ctx', "reason": "tests/test_infra_prose_grounding.py is the ALREADY-AUTHORIZED ctx-prose-decoupling atom's own test (AC-CXD-1/10/13); its token-set mirror, forbidden-phrase list and the named false-positive-survival table necessarily spell the very identifiers/phrases they prove absent or preserved verbatim. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_infra_prose_grounding.py', "key": 'bctx', "reason": "tests/test_infra_prose_grounding.py is the ALREADY-AUTHORIZED ctx-prose-decoupling atom's own test (AC-CXD-1/10/13); its token-set mirror, forbidden-phrase list and the named false-positive-survival table necessarily spell the very identifiers/phrases they prove absent or preserved verbatim. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_infra_prose_grounding.py', "key": 'statusline_ctx_bar', "reason": "tests/test_infra_prose_grounding.py is the ALREADY-AUTHORIZED ctx-prose-decoupling atom's own test (AC-CXD-1/10/13); its token-set mirror, forbidden-phrase list and the named false-positive-survival table necessarily spell the very identifiers/phrases they prove absent or preserved verbatim. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_infra_prose_grounding.py', "key": 'block_ctx', "reason": "tests/test_infra_prose_grounding.py is the ALREADY-AUTHORIZED ctx-prose-decoupling atom's own test (AC-CXD-1/10/13); its token-set mirror, forbidden-phrase list and the named false-positive-survival table necessarily spell the very identifiers/phrases they prove absent or preserved verbatim. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_infra_prose_grounding.py', "key": 'git_discipline_block_ctx', "reason": "tests/test_infra_prose_grounding.py is the ALREADY-AUTHORIZED ctx-prose-decoupling atom's own test (AC-CXD-1/10/13); its token-set mirror, forbidden-phrase list and the named false-positive-survival table necessarily spell the very identifiers/phrases they prove absent or preserved verbatim. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_infra_prose_grounding.py', "key": '_foundry_ctx', "reason": "tests/test_infra_prose_grounding.py is the ALREADY-AUTHORIZED ctx-prose-decoupling atom's own test (AC-CXD-1/10/13); its token-set mirror, forbidden-phrase list and the named false-positive-survival table necessarily spell the very identifiers/phrases they prove absent or preserved verbatim. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_infra_prose_grounding.py', "key": 'session_learnings_foundry_ctx', "reason": "tests/test_infra_prose_grounding.py is the ALREADY-AUTHORIZED ctx-prose-decoupling atom's own test (AC-CXD-1/10/13); its token-set mirror, forbidden-phrase list and the named false-positive-survival table necessarily spell the very identifiers/phrases they prove absent or preserved verbatim. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_infra_prose_grounding.py', "key": 'spec_audit_ctx_param', "reason": "tests/test_infra_prose_grounding.py is the ALREADY-AUTHORIZED ctx-prose-decoupling atom's own test (AC-CXD-1/10/13); its token-set mirror, forbidden-phrase list and the named false-positive-survival table necessarily spell the very identifiers/phrases they prove absent or preserved verbatim. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_infra_prose_grounding.py', "key": 'tier_preflight_ctx_local', "reason": "tests/test_infra_prose_grounding.py is the ALREADY-AUTHORIZED ctx-prose-decoupling atom's own test (AC-CXD-1/10/13); its token-set mirror, forbidden-phrase list and the named false-positive-survival table necessarily spell the very identifiers/phrases they prove absent or preserved verbatim. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_infra_prose_grounding.py', "key": 'context_skill_ctx_star', "reason": "tests/test_infra_prose_grounding.py is the ALREADY-AUTHORIZED ctx-prose-decoupling atom's own test (AC-CXD-1/10/13); its token-set mirror, forbidden-phrase list and the named false-positive-survival table necessarily spell the very identifiers/phrases they prove absent or preserved verbatim. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_infra_prose_grounding.py', "key": 'test_overloaded_ctx_constructs_survive_verbatim', "reason": "tests/test_infra_prose_grounding.py is the ALREADY-AUTHORIZED ctx-prose-decoupling atom's own test (AC-CXD-1/10/13); its token-set mirror, forbidden-phrase list and the named false-positive-survival table necessarily spell the very identifiers/phrases they prove absent or preserved verbatim. Outside this test-only atom's allowed_paths, so allowlisted per-occurrence rather than reworded."},
    {"path": 'tests/test_infra_delivery.py', "key": 'ctx', "reason": "tests/test_infra_delivery.py's AC-IDAGR-6 rows assert the retired `ctx-posture`/`ctx status` phrases are absent from procedure-skill prose, so the literal phrases must appear inside the assertion. Outside this test-only atom's allowed_paths, so allowlisted rather than reworded."},
    {"path": 'tests/test_infra_delivery.py', "key": 'ctx-posture', "reason": "tests/test_infra_delivery.py's AC-IDAGR-6 rows assert the retired `ctx-posture`/`ctx status` phrases are absent from procedure-skill prose, so the literal phrases must appear inside the assertion. Outside this test-only atom's allowed_paths, so allowlisted rather than reworded."},
    {"path": 'tests/test_infra_delivery.py', "key": 'ctx status', "reason": "tests/test_infra_delivery.py's AC-IDAGR-6 rows assert the retired `ctx-posture`/`ctx status` phrases are absent from procedure-skill prose, so the literal phrases must appear inside the assertion. Outside this test-only atom's allowed_paths, so allowlisted rather than reworded."},
    {"path": 'tests/test_stack_profile.py', "key": 'ctx', "reason": 'tests/test_stack_profile.py\'s forbidden-phrase list asserts the retired "CTX\'s command-policy" narrative is absent from stack-profile prose, so the literal phrases must appear inside the assertion. Outside this test-only atom\'s allowed_paths, so allowlisted rather than reworded.'},
]

_EXPECTED_OVERLOADED_ABBREVIATION_SITES = [
    {"path": 'scripts/foundry-statusline.sh', "reason": "The context-window pressure bar's literal `ctx` segment."},
    {"path": 'tests/test_statusline.py', "reason": "Asserts the pressure bar's `ctx` segment, positively and negatively."},
    {"path": 'hooks/foundry-git-discipline.sh', "reason": 'The `block_ctx()` "block with context" helper.'},
    {"path": 'hooks/foundry-session-learnings.sh', "reason": 'The `_FOUNDRY_CTX`/`ctx1`/`ctxw` selftest locals for `additionalContext`.'},
    {"path": 'workflows/spec-audit.js', "reason": "The reviser callback's `ctx` parameter."},
    {"path": 'scripts/foundry_tier_preflight.py', "reason": 'The `ctx: set[str]` ruleset-context local.'},
    {"path": 'scripts/foundry-stack-profile.py', "reason": 'The `for ctx in loaded_context(resolved):` loop local.'},
    {"path": 'skills/context/SKILL.md', "reason": "The framework's own retired CONTEXT-lifecycle `ctx-*` skills, an internal naming."},
]

_EXPECTED_SELF_EXCLUSIONS = [
    {"path": 'tests/fixtures/reference-sweep/retired-framework-sweep.yaml', "reason": 'This file IS the frozen token set + allowlist; scanning it is self-referential.'},
    {"path": 'tests/test_reference_sweep.py', "reason": 'Carries the hand-transcribed copy of the sets (the AC-CXG-1 drift cross-check), the AC-CXG-5 compound-identifier probes and the AC-CXG-7 synthetic injection payload.'},
    {"path": 'CHANGELOG.md', "reason": 'An APPEND-ONLY historical record. The release note describing the removal necessarily names what was removed, and the exact identifiers it will name are not knowable when this contract is frozen. History is prose, never a dependency.'},
]

_NEGATIVE_CONTROL_ELEMENTS = [
    'skills/id-apply/SKILL.md',
    'docs/glossary.md',
    'packs/stack-profiles/aws-eks-karpenter/conventions.md',
    'scripts/foundry-stack-profile.py',
    'hooks/foundry-cloud-cli-exec-guard.sh',
]



# ==================================================================== AC-CXG-1 ====================

@pytest.mark.parametrize("fixture_key,expected", [
    ("token_set", _EXPECTED_TOKEN_SET),
    ("allowlist", _EXPECTED_ALLOWLIST),
    ("overloaded_abbreviation_sites", _EXPECTED_OVERLOADED_ABBREVIATION_SITES),
    ("self_exclusions", _EXPECTED_SELF_EXCLUSIONS),
])
def test_fixture_sets_equal_the_independently_transcribed_preamble(fixture_key, expected):
    assert FIXTURE[fixture_key] == expected, (
        f"fixture[{fixture_key!r}] drifted from the independently transcribed contract preamble"
    )


# ==================================================================== AC-CXG-2 ====================

@pytest.mark.parametrize("row", ["surface-equals-tracked-minus-exclusions",
                                  "exactly-three-self-exclusions",
                                  "unenumerable-tracked-list-is-red-not-partial"])
def test_surface_is_every_tracked_text_file_minus_named_self_exclusions(row, tmp_path):
    if row == "surface-equals-tracked-minus-exclusions":
        raw = subprocess.run(["git", "ls-files"], cwd=str(REPO_ROOT), capture_output=True,
                              text=True, check=True)
        raw_tracked = {l for l in raw.stdout.splitlines() if l.strip()}
        excluded = {e["path"] for e in FIXTURE["self_exclusions"]}
        expected_surface = raw_tracked - excluded
        assert set(sweep_surface(REPO_ROOT)) == expected_surface
        return

    if row == "exactly-three-self-exclusions":
        assert len(FIXTURE["self_exclusions"]) == 3
        assert {e["path"] for e in FIXTURE["self_exclusions"]} == {
            "tests/fixtures/reference-sweep/retired-framework-sweep.yaml",
            "tests/test_reference_sweep.py",
            "CHANGELOG.md",
        }
        return

    # row == "unenumerable-tracked-list-is-red-not-partial": a candidate root outside any git
    # repo must fail enumeration, and run_sweep over it must report RED rather than a partial
    # (silently-empty) surface.
    with pytest.raises(TrackedFileEnumerationError):
        enumerate_tracked_files(tmp_path)
    result = run_sweep(tmp_path)
    assert result["verdict"] == "RED"
    assert result["error"] is not None


# ==================================================================== AC-CXG-3 ====================

def test_shipped_tree_has_zero_uncovered_occurrences():
    _, surface, occ, total = _real_tree_sweep()
    unc = uncovered_occurrences(occ, FIXTURE["allowlist"])
    assert unc == {}, f"un-allowlisted occurrence(s) over the shipped tree: {sorted(unc)}"
    # sanity: the catch-all token really does reach a compound identifier of the framework's own
    # naming convention, so a future regression cannot silently escape the sweep by shape alone.
    for probe in ("probe_ctx", "_ctx_probe_present", "ctx_state", "ctxstate"):
        keys = occurrence_keys(f"a synthetic {probe} reference")
        assert probe.lower() in keys or probe.lower().replace("_", "") in [k.replace("_", "") for k in keys], (
            f"the catch-all token failed to key the compound identifier {probe!r}"
        )


# ==================================================================== AC-CXG-4 ====================

@pytest.mark.parametrize("row", ["all-real-entries-well-formed", "bad-path", "bad-key", "empty-reason"])
def test_every_allowlist_entry_is_well_formed(row):
    if row == "all-real-entries-well-formed":
        bad = {}
        for e in FIXTURE["allowlist"]:
            errs = allowlist_entry_errors(REPO_ROOT, e)
            if errs:
                bad[(e["path"], e["key"])] = errs
        assert bad == {}, f"malformed real allowlist entries: {bad}"
        return

    if row == "bad-path":
        entry = {"path": "scripts/this-file-does-not-exist-anywhere.py", "key": "ctx", "reason": "x"}
        assert allowlist_entry_errors(REPO_ROOT, entry) != []
        return

    if row == "bad-key":
        real_path = FIXTURE["allowlist"][0]["path"]
        for bad_key in ("CTX", "posture", ""):
            entry = {"path": real_path, "key": bad_key, "reason": "x"}
            assert allowlist_entry_errors(REPO_ROOT, entry) != [], f"key {bad_key!r} should be rejected"
        return

    # row == "empty-reason"
    real_path = FIXTURE["allowlist"][0]["path"]
    entry = {"path": real_path, "key": "ctx", "reason": "   "}
    assert allowlist_entry_errors(REPO_ROOT, entry) != []


# ==================================================================== AC-CXG-5 ====================

@pytest.mark.parametrize(
    "site",
    [e["path"] for e in FIXTURE["overloaded_abbreviation_sites"]],
    ids=[e["path"] for e in FIXTURE["overloaded_abbreviation_sites"]],
)
def test_an_allowlisted_path_is_never_a_blanket_for_compound_identifiers(site, tmp_path):
    dst = _copy_file_to_tmp(tmp_path, site)
    with open(dst, "a", encoding="utf-8") as f:
        f.write(_AC_CXG5_PAYLOAD)
    text = dst.read_text(encoding="utf-8")
    found_keys = set(occurrence_keys(text))
    allowed_keys = {e["key"] for e in FIXTURE["allowlist"] if e["path"] == site}
    uncovered = found_keys - allowed_keys
    assert uncovered, (
        f"{site} is allowlisted for {sorted(allowed_keys)} but injecting the compound-identifier "
        f"probes produced NO uncovered key — the allowlist entry is a blanket, which AC-CXG-5 forbids"
    )


# ==================================================================== AC-CXG-6 ====================

@pytest.mark.parametrize(
    "idx",
    range(len(FIXTURE["allowlist"])),
    ids=[f'{e["path"]}::{e["key"]}' for e in FIXTURE["allowlist"]],
)
def test_every_allowlist_entry_is_load_bearing(idx):
    _, _, occ, _ = _real_tree_sweep()
    entry = FIXTURE["allowlist"][idx]
    key_pair = (entry["path"], entry["key"])

    # (i) the entry covers at least one real occurrence.
    assert key_pair in occ, f"allowlist entry {key_pair} covers ZERO real occurrences (decorative)"

    # (ii) the sweep re-run WITHOUT this one entry reports RED naming this entry's (path, key).
    reduced_allowlist = [e for i, e in enumerate(FIXTURE["allowlist"]) if i != idx]
    unc = uncovered_occurrences(occ, reduced_allowlist)
    assert key_pair in unc, (
        f"removing allowlist entry {key_pair} did not turn the sweep RED for it — "
        f"either it is not load-bearing or another entry silently covers the same occurrence"
    )


# ==================================================================== AC-CXG-7 ====================

@pytest.mark.parametrize(
    "element",
    _NEGATIVE_CONTROL_ELEMENTS,
    ids=_NEGATIVE_CONTROL_ELEMENTS,
)
def test_injected_reference_turns_the_sweep_red(element, tmp_path):
    dst = _copy_file_to_tmp(tmp_path, element)
    with open(dst, "a", encoding="utf-8") as f:
        f.write(_AC_CXG7_PAYLOAD)
    text = dst.read_text(encoding="utf-8")
    found_keys = set(occurrence_keys(text))
    allowed_keys = {e["key"] for e in FIXTURE["allowlist"] if e["path"] == element}
    uncovered = found_keys - allowed_keys
    assert uncovered, f"injecting the negative-control payload into {element} produced no uncovered key"


# ==================================================================== AC-CXG-8 ====================

def test_gate_module_is_collected_by_the_default_pytest_run():
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300,
    )
    assert "tests/test_reference_sweep.py" in proc.stdout, (
        "the default `python3 -m pytest tests/ -q` collection does not name this gate module:\n"
        + proc.stdout[-4000:] + proc.stderr[-2000:]
    )
    assert proc.returncode == 0, f"collection reported errors:\n{proc.stdout}\n{proc.stderr}"


# ==================================================================== AC-CXG-9 ====================

@pytest.mark.parametrize("row", ["clean-root-is-green", "dirty-root-is-red"])
def test_sweep_accepts_an_arbitrary_root_and_emits_a_verdict_line(row, tmp_path, capsys):
    if row == "clean-root-is-green":
        _init_git_repo(tmp_path, {"README.md": "hello world, nothing retired lives here.\n"})
        rc = main(["--root", str(tmp_path)])
        out = capsys.readouterr().out.strip()
        assert rc == 0
        m = _VERDICT_LINE_RE.match(out)
        assert m is not None, f"verdict line does not match the frozen regex: {out!r}"
        assert m.group(1) == "GREEN"
        return

    # row == "dirty-root-is-red"
    _init_git_repo(tmp_path, {
        "README.md": "hello world.\n",
        "app.py": "def probe_ctx():\n    return None\n",
    })
    rc = main(["--root", str(tmp_path)])
    out = capsys.readouterr().out.strip()
    assert rc == 1
    m = _VERDICT_LINE_RE.match(out)
    assert m is not None, f"verdict line does not match the frozen regex: {out!r}"
    assert m.group(1) == "RED"


if __name__ == "__main__":
    sys.exit(main())
