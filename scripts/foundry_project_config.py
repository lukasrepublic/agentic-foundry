"""foundry_project_config.py — adopter-configurable governance-record path resolver
(ER #111, atom GRP: feat-foundry-governance-configurable-record-paths, AC-GRP-1/2/5).

Decision records and their disconfirming-evidence trail are first-class governance/audit
artifacts (ADR convention, ISO/IEC 27001 cl. 9.3, SOC 2 CC1) that should live on the ADOPTER
side of the vendored/owned boundary — not hard-coded into the plugin's disposable `.foundry/`
dotdir. This module resolves the two governance-record output locations from
`.claude/foundry-project.json`'s `governance` block (`decisions_path` / `research_path`),
mirroring `foundry-doctor.py`'s `_multi_repo_status` reader idiom (resolve the adopter
inventory file relative to `CLAUDE_PROJECT_DIR`; absent/unreadable never raises — it degrades
to the default), and DEFAULTS to `.foundry/decisions` / `.foundry/research` when the
`governance` key or either sub-key is absent — byte-identical to the pre-atom behavior
(AC-GRP-1/2).

PATH CONFINEMENT (AC-GRP-5): a configured value that is absolute, or that escapes the project
root via a `..` path component, is REJECTED — a warning is emitted on stderr and the
corresponding `.foundry/…` default is used instead. The configured value is never accepted
verbatim; only a same-tree-relative, `..`-free path is honored. "Any owned path" means any path
*within* the adopter's project tree (per the spec's Clarifications).

stdlib-only, dependency-light (per the design note) — no third-party imports.
"""
import json
import os
import sys

DEFAULT_DECISIONS_PATH = os.path.join(".foundry", "decisions")
DEFAULT_RESEARCH_PATH = os.path.join(".foundry", "research")


def _project_dir(project_dir=None):
    """Resolve the adopter project root: an explicit override, else CLAUDE_PROJECT_DIR, else cwd."""
    return project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def _load_project_config(project_dir=None):
    """Read `.claude/foundry-project.json` under the resolved project root. Mirrors
    `foundry-doctor.py`'s `_multi_repo_status` reader idiom: an absent or unreadable inventory
    file degrades to `{}` (never raises) — the caller falls back to the governance defaults."""
    root = _project_dir(project_dir)
    inv = os.path.join(root, ".claude", "foundry-project.json")
    if not os.path.isfile(inv):
        return {}
    try:
        with open(inv, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _confine(value, default, label, warn=True):
    """AC-GRP-5 path confinement. Returns a project-root-relative, `..`-free path derived from
    `value`, or `default` when `value` is absent/blank/rejected. Rejects (with a stderr warning
    when `warn`) an ABSOLUTE value or one that escapes the project root via a leading `..`
    component after normalization — the configured value is never accepted verbatim in either
    case; only the confinement-cleared, `os.path.normpath`-normalized form is returned."""
    if not isinstance(value, str) or not value.strip():
        return default
    v = value.strip()
    reject_reason = None
    if os.path.isabs(v):
        reject_reason = "absolute path is not allowed (must be project-root-relative)"
    else:
        norm = os.path.normpath(v)
        parts = norm.split(os.sep)
        if norm == os.pardir or parts[0] == os.pardir:
            reject_reason = "escapes the project root via a '..' component"
    if reject_reason:
        if warn:
            print(
                f"WARNING: governance.{label} = {value!r} rejected ({reject_reason}); "
                f"falling back to default {default!r}",
                file=sys.stderr,
            )
        return default
    return os.path.normpath(v)


def resolve_governance_paths(project_dir=None, warn=True):
    """Returns `(decisions_path, research_path)` — the two adopter-configurable governance-record
    output locations, resolved from `.claude/foundry-project.json`'s `governance` block
    (`decisions_path` / `research_path`), confined to the project root (AC-GRP-5), and defaulting
    to `.foundry/decisions` / `.foundry/research` when the `governance` key or either sub-key is
    absent (AC-GRP-1/2 — byte-identical back-compat)."""
    cfg = _load_project_config(project_dir)
    gov = cfg.get("governance")
    gov = gov if isinstance(gov, dict) else {}
    decisions = _confine(gov.get("decisions_path"), DEFAULT_DECISIONS_PATH, "decisions_path", warn)
    research = _confine(gov.get("research_path"), DEFAULT_RESEARCH_PATH, "research_path", warn)
    return decisions, research


def resolve_decisions_path(project_dir=None, warn=True):
    """The resolved `governance.decisions_path` (default `.foundry/decisions`)."""
    return resolve_governance_paths(project_dir, warn)[0]


def resolve_research_path(project_dir=None, warn=True):
    """The resolved `governance.research_path` (default `.foundry/research`)."""
    return resolve_governance_paths(project_dir, warn)[1]


if __name__ == "__main__":
    dp, rp = resolve_governance_paths()
    print(f"decisions_path: {dp}")
    print(f"research_path: {rp}")
