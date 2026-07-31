#!/usr/bin/env python3
"""foundry-deploy-status — observe-only deployed-artifact-identity cross-check (§13, UL-0001).

The `deploy-status` verb reports ArgoCD sync + health. That is blind to a `workflow_run`-gated
CD pipeline that SILENTLY SKIPS when its upstream CI false-reds: the merged commit never builds,
gitops is never written back, the old image keeps running — and ArgoCD reads "Synced + Healthy"
against the STALE image. This checker cross-checks the deployed gitops `image.tag` against the
EXPECTED merged-commit identity (target repo `main` HEAD / build-provenance SHA) and surfaces a
distinct STALE/NOT-ROLLED verdict, INDEPENDENT of sync/health, so a stale image cannot read GREEN.

Observe-only: this script READS + COMPARES + REPORTS. It contains no deploy-trigger / cluster-
mutation surface. The expected-SHA -> gitops-path mapping is ADOPTER-CONFIG (loaded from the
adopter workspace, e.g. $CLAUDE_PROJECT_DIR/.foundry/deploy-targets.yaml); no product/adopter
target ships in the plugin.

  foundry-deploy-status.py --config <deploy-targets.yaml>   # classify configured targets
  foundry-deploy-status.py --selftest                       # exit 0 green / 1 red

Exit: 0 = ok / all targets ROLLED (or selftest green); 2 = at least one STALE/NOT-ROLLED;
1 = selftest red / config error. Fail-closed throughout.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

# Verdict vocabulary — STALE/NOT-ROLLED is DISTINCT from Healthy and from a bare "drift".
ROLLED = "ROLLED"
STALE = "STALE/NOT-ROLLED"

CAUSE_CI_NOT_SUCCESS = "upstream-ci-not-success"   # workflow_run-gated build skipped
CAUSE_IDENTITY_DRIFT = "identity-drift"            # tags differ for another reason
CAUSE_NONE = "rolled"


def _norm(sha: str | None) -> str:
    """Normalize a git identity for comparison (lowercase, trimmed). Empty if absent."""
    return (sha or "").strip().lower()


def classify(expected_sha: str | None, gitops_image_tag: str | None,
             ci_status: str | None) -> tuple[str, str]:
    """Pure identity cross-check. Returns (verdict, cause).

    - expected_sha: the merged-commit identity that SHOULD be running (target repo `main`
      HEAD, or its build-provenance SHA).
    - gitops_image_tag: the identity actually deployed (the gitops `image.tag`).
    - ci_status: the upstream CI run status for expected_sha ("success" | other | None).

    The CI-not-success case is named explicitly (AC-DEPLOYSTALE-2): when the gated build never
    ran, the new image was never produced, so a stale tag is EXPECTED — but it is still
    STALE/NOT-ROLLED, never Healthy, and the cause is the skipped build, not generic drift.
    """
    exp = _norm(expected_sha)
    got = _norm(gitops_image_tag)
    ci_ok = _norm(ci_status) == "success"

    if not ci_ok:
        # The gated build was skipped/failed -> the expected image was never built/pushed.
        # Whatever is deployed is by definition not the merged commit.
        return STALE, CAUSE_CI_NOT_SUCCESS
    if not exp or not got:
        # Missing identity is fail-closed: cannot prove the merged commit rolled.
        return STALE, CAUSE_IDENTITY_DRIFT
    # Allow a short-SHA tag to match a full SHA (and vice versa) by prefix.
    if exp == got or exp.startswith(got) or got.startswith(exp):
        return ROLLED, CAUSE_NONE
    return STALE, CAUSE_IDENTITY_DRIFT


# --------------------------------- config ----------------------------------- #

def _default_config_path() -> str:
    root = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    return os.path.join(root, ".foundry", "deploy-targets.yaml")


def load_targets(config_path: str) -> list[dict]:
    """Load the ADOPTER-CONFIG target mapping. The plugin ships the loader + key shape only;
    the adopter fills concrete targets. A target is:
      {name, target_repo, expected_ref, gitops_image_tag_path, ci_workflow}
    Values are opaque to the plugin (no product specifics interpreted here)."""
    import yaml  # local import: only needed for the live (non-selftest) path
    if not os.path.exists(config_path):
        raise FileNotFoundError(
            f"no deploy-targets config at {config_path} — this mapping is adopter-config; "
            f"create .foundry/deploy-targets.yaml in the adopter workspace")
    with open(config_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    targets = data.get("targets")
    if not isinstance(targets, list):
        raise ValueError(f"{config_path}: expected a top-level `targets:` list")
    return targets


# --------------------------------- selftest --------------------------------- #

# GENERIC example tokens standing for "a product/adopter-specific name that must never ship in the
# plugin" — the self-scan asserts none appear in this module's real code (targets come from adopter
# config, not hardcoded). Deliberately non-proprietary: the CI leak gate is a plain grep that does
# NOT honor `# selfscan:exclude`, so real adopter names here would trip it (UL-0001 residual: teach
# CI to honor the marker — out of this atom's scope).
_PRODUCT_TOKENS = ("acme-corp", "example-tenant", "example-product-web")  # selfscan:exclude
# Write/trigger verbs that would violate the observe-only contract if present in this module.
_DEPLOY_TRIGGER_VERBS = (  # selfscan:exclude
    "argocd app sync", "argocd app set", "kubectl apply", "kubectl rollout",  # selfscan:exclude
    "gh workflow run", "argocd app create", "kubectl delete", "helm upgrade",  # selfscan:exclude
)

# Lines bearing this marker DEFINE the token/verb blocklists (they necessarily name the
# forbidden strings to scan for); the self-scan excludes them so the definitions don't
# self-trip the check.
_SELFSCAN_EXCLUDE = "selfscan:exclude"


def _scannable_source() -> str:
    """This module's source with blocklist-definition lines removed, lowercased — so the
    self-scan checks only 'real' code/prose, not the lists it scans with."""
    with open(os.path.abspath(__file__), encoding="utf-8") as fh:
        kept = [ln for ln in fh if _SELFSCAN_EXCLUDE not in ln]
    return "".join(kept).lower()


def _selftest() -> int:
    failures: list[str] = []
    lines: list[str] = ["foundry-deploy-status selftest"]

    # 1. classify() logic — the core identity cross-check (AC-DEPLOYSTALE-1 / -2).
    cases = [
        # (expected, gitops_tag, ci_status) -> (verdict, cause)
        ("abc123", "abc123", "success", (ROLLED, CAUSE_NONE)),
        ("abc1234567", "abc123", "success", (ROLLED, CAUSE_NONE)),      # short-tag prefix match
        ("abc123", "def456", "success", (STALE, CAUSE_IDENTITY_DRIFT)),  # AC-1 mismatch
        ("abc123", "def456", "failure", (STALE, CAUSE_CI_NOT_SUCCESS)),  # AC-2 gated-build skip
        ("abc123", "abc123", "failure", (STALE, CAUSE_CI_NOT_SUCCESS)),  # AC-2 even if tags look equal
        ("abc123", None, "success", (STALE, CAUSE_IDENTITY_DRIFT)),      # fail-closed on missing
    ]
    for exp, got, ci, want in cases:
        gotv = classify(exp, got, ci)
        ok = gotv == want
        if not ok:
            failures.append(f"classify({exp!r},{got!r},{ci!r}) = {gotv} != {want}")
    lines.append(f"  [{'ok ' if not failures else 'RED'}] classify: {len(cases)} cases")
    # Surface the verdict + cause tokens for the live-seam walk (AC-1 / AC-2 locators).
    lines.append(f"  verdict-vocabulary: {ROLLED} | {STALE}")
    lines.append(f"  detected-cause-on-gated-skip: {CAUSE_CI_NOT_SUCCESS}")

    scannable = _scannable_source()

    # 2. No product/adopter specifics shipped in the plugin (AC-DEPLOYSTALE-3).
    hard = [t for t in _PRODUCT_TOKENS if t in scannable]
    n_hard = len(hard)
    if n_hard:
        failures.append(f"product token(s) hardcoded in plugin: {hard}")
    lines.append(f"  [{'ok ' if not n_hard else 'RED'}] plugin-hardcoded-targets: {n_hard}")
    lines.append("  mapping-source: adopter-config (.foundry/deploy-targets.yaml)")

    # 3. Observe-only — no deploy-trigger / cluster-mutation verb in this module (AC-DEPLOYSTALE-4).
    triggers = [v for v in _DEPLOY_TRIGGER_VERBS if v in scannable]
    if triggers:
        failures.append(f"deploy-trigger verb(s) present (violates observe-only): {triggers}")
    lines.append(f"  [{'ok ' if not triggers else 'RED'}] observe-only: "
                 f"{'PASS' if not triggers else 'FAIL'} (no deploy-trigger surface)")

    lines.append("DEPLOY-STATUS-SELFTEST-" + ("GREEN" if not failures else "RED"))
    sys.stdout.write("\n".join(lines) + "\n")
    if failures:
        sys.stderr.write("selftest failures:\n  - " + "\n  - ".join(failures) + "\n")
        return 1
    return 0


# ----------------------------------- main ----------------------------------- #

def _run_live(config_path: str) -> int:
    """Classify each configured target. Observe-only: the adopter supplies the read commands
    that populate expected_sha / gitops_image_tag / ci_status per target; this checker does not
    execute deploys. (The read-adapter wiring is adopter-side; here we classify provided
    identities and refuse to proceed if a target is under-specified.)"""
    targets = load_targets(config_path)
    worst = 0
    print("foundry-deploy-status (observe-only)")
    for t in targets:
        name = t.get("name", "<unnamed>")
        verdict, cause = classify(
            t.get("expected_sha"), t.get("gitops_image_tag"), t.get("ci_status"))
        marker = "ok " if verdict == ROLLED else "!! "
        print(f"  [{marker}] {name}: {verdict}" + (f" (cause: {cause})" if verdict == STALE else ""))
        if verdict == STALE:
            worst = 2
    return worst


def main() -> int:
    ap = argparse.ArgumentParser(description="observe-only deployed-artifact-identity check")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--config", default=None,
                    help="adopter deploy-targets.yaml (default: $CLAUDE_PROJECT_DIR/.foundry/deploy-targets.yaml)")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    try:
        return _run_live(args.config or _default_config_path())
    except (FileNotFoundError, ValueError) as e:
        sys.stderr.write(f"deploy-status: {e}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
