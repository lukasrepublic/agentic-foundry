"""tests/test_doc_claims.py — anti-doc-rot CI (feat-foundry-anti-doc-rot-ci).

Derives ground truth from the shipped tree at run time and asserts the count-bearing and
capability-bearing claims in the operator-facing docs against it, riding the existing
`python3 -m pytest tests/ -q` invocation (no new workflow/job/step — AC-DRT-1). Fail-closed
throughout: no skip/xfail marker, no warn-only branch, no env-var escape (AC-DRT-2).

Spec: specs/features/foundry/quality/anti-doc-rot-ci/feat-foundry-anti-doc-rot-ci.md

Shape (spec "Design / notes"):
  - COVERED_CLAIMS  — the single registry. Each entry binds a stable claim id, the doc it
    covers, the count-bearing tokens (if any) it accounts for, its mutation class, and three
    callables: derive(root), check(root) (raises AssertionError naming the claim id), and
    make_mutated(tmp_path) (materializes a mutated copy for the negative control).
  - EXCLUDED_DOCS / EXPECTED_EXCLUSIONS — the exclusion mapping over the closed reason set,
    pinned against a literal expected copy (two independently hand-written literals; a diff to
    one without the other fails test_exclusion_mapping_is_pinned).
  - DOC_CORPUS — README.md, CONTRIBUTING.md, docs/**/*.md (recursive), minus EXCLUDED_DOCS.
  - CORE_LOOP_VERBS — the pinned verb set the README's catalog sentence excludes.

Honest residual (module-level, not per-entry): docs/DESIGN.md and docs/glossary.md are
excluded (reason `historical-record`) because their surviving count-bearing tokens are, without
exception, either (a) a historical LOC/ratio/audit-convergence figure describing an already-
deleted system with no independently recorded ground truth in the live tree, or (b) an external
industry citation — neither is "a claim about the shipped tree" (this atom's own displaced
condition). Every currently-checkable fact those two files repeat (e.g. the 14-AC/8,000-word
spec-size ceiling) is independently covered via its sibling occurrence in a non-excluded doc.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class MissingSourceError(AssertionError):
    """AC-DRT-3: a documentation file or derivation source is missing/unreadable/unparseable —
    convicts the suite (a subclass of AssertionError) rather than passing vacuously."""


def read_doc_text(root: Path, relpath: str) -> str:
    path = root / relpath
    if not path.exists():
        raise MissingSourceError(f"missing documentation/derivation source: {relpath}")
    text = path.read_text(encoding="utf-8")
    if text.strip() == "":
        raise MissingSourceError(f"source yields no parseable content: {relpath}")
    return text


def _materialize(root: Path, tmp_path_arg: Path, relpaths: list) -> Path:
    """Copy the given repo-relative files/dirs from `root` into a fresh `materialized/`
    subdirectory under `tmp_path_arg`, preserving repo-relative structure. Read-only over the
    real tree; every mutation applied by a registry entry happens in this copy."""
    dest = tmp_path_arg / "materialized"
    dest.mkdir(parents=True, exist_ok=True)
    for rel in relpaths:
        src = root / rel
        dst = dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        elif src.is_file():
            shutil.copy2(src, dst)
    return dest


# ---------------------------------------------------------------------------
# Terminology (normative — spec "Terminology (normative)")
# ---------------------------------------------------------------------------

REASONS = {"historical-record", "archived", "generated", "third-party"}

EXCLUDED_DOCS = {
    "docs/archive/**": "archived",
    "docs/DESIGN.md": "historical-record",
    "docs/glossary.md": "historical-record",
}

# Pinned literal expected copy of EXCLUDED_DOCS, hand-synchronized (not derived from it) — a
# reviewer sees any addition/removal of an exclusion as a two-place diff in this module.
EXPECTED_EXCLUSIONS = {
    "docs/archive/**": "archived",
    "docs/DESIGN.md": "historical-record",
    "docs/glossary.md": "historical-record",
}

CORE_LOOP_VERBS = frozenset(
    {"intake", "spec-review", "authorize", "dispatch", "certify-local", "release"}
)

UNDERSTATEMENT_FLOOR_RATIO = 0.5


def _doc_corpus(root: Path) -> list:
    corpus = []
    for name in ("README.md", "CONTRIBUTING.md"):
        if (root / name).exists():
            corpus.append(name)
    docs_dir = root / "docs"
    if docs_dir.exists():
        for path in sorted(docs_dir.rglob("*.md")):
            corpus.append(path.relative_to(root).as_posix())
    excluded = set()
    for pattern in EXCLUDED_DOCS:
        if pattern.endswith("/**"):
            prefix = pattern[:-3]
            excluded.update(r for r in corpus if r.startswith(prefix))
        else:
            excluded.add(pattern)
    return [r for r in corpus if r not in excluded]


DOC_CORPUS = _doc_corpus(REPO_ROOT)


# ---------------------------------------------------------------------------
# Count-bearing claim detector (spec Terminology — the six closed exclusions)
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])\d[\d,]*(?![A-Za-z0-9_])")
_VERSION_RE = re.compile(r"v?\d+(?:\.\d+)+")
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_CLOCK_RE = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?")
_ORDINAL_PREFIX_RE = re.compile(r"^(\s*)(\d+)(\.\s)")
_ATX_ORDINAL_RE = re.compile(r"^(#{1,6}\s+)(\d+)([.\s])")


def find_count_bearing_tokens(text: str) -> list:
    """Return [(line_no, token, line_text), ...] for every count-bearing token surviving the
    six closed exclusions: (i) code span/block, (ii) link target/repo path/URL, (iii) version
    tag, (iv) ISO date/clock time, (v) ordered-list ordinal/ATX heading numeral, (vi) HTML
    comment/YAML front matter."""
    hits = []
    in_fence = False
    in_frontmatter = False
    lines = text.split("\n")
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if i == 1 and stripped == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if stripped == "---":
                in_frontmatter = False
            continue
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if "<!--" in line:
            continue
        excluded_spans = []
        m = _ORDINAL_PREFIX_RE.match(line)
        if m:
            excluded_spans.append((m.start(2), m.end(2)))
        m = _ATX_ORDINAL_RE.match(line)
        if m:
            excluded_spans.append((m.start(2), m.end(2)))
        for regex in (_VERSION_RE, _ISO_DATE_RE, _CLOCK_RE):
            excluded_spans.extend((m.start(), m.end()) for m in regex.finditer(line))
        for m in _TOKEN_RE.finditer(line):
            start, end = m.start(), m.end()
            before = line[:start]
            if before.count("`") % 2 == 1:
                continue  # inside an inline code span
            bracket_before = before.rfind("](")
            if bracket_before != -1 and line.find(")", end) != -1 and bracket_before >= before.rfind(")"):
                continue  # inside a markdown link target
            if "http://" in before[-30:] or "https://" in before[-30:]:
                continue  # inside a bare URL
            if (start > 0 and line[start - 1] == "/") or (end < len(line) and line[end] == "/"):
                continue  # part of a repo-relative path
            if any(s <= start and end <= e for s, e in excluded_spans):
                continue
            hits.append((i, m.group(), line.strip()))
    return hits


# ---------------------------------------------------------------------------
# Shared derivations
# ---------------------------------------------------------------------------

def _shipped_skill_names(root: Path) -> set:
    skills_dir = root / "skills"
    if not skills_dir.exists():
        raise MissingSourceError("missing derivation source: skills/")
    return {p.name for p in skills_dir.iterdir() if (p / "SKILL.md").is_file()}


# ---- 1. skill-catalog-count (AC-DRT-7) ------------------------------------

def _derive_skill_catalog_count(root: Path) -> int:
    names = _shipped_skill_names(root)
    for verb in CORE_LOOP_VERBS:
        if verb not in names:
            raise AssertionError(
                f"[skill-catalog-count] pinned core-loop verb {verb!r} does not resolve to a "
                f"shipped skills/{verb}/SKILL.md"
            )
    return len(names) - len(CORE_LOOP_VERBS)


def _check_skill_catalog_count(root: Path) -> None:
    text = read_doc_text(root, "README.md")
    m = re.search(r"other ~(\d+) skills", text)
    if not m:
        raise MissingSourceError("README.md yields no parseable skill-catalog claim")
    claimed = int(m.group(1))
    derived = _derive_skill_catalog_count(root)
    if claimed != derived:
        raise AssertionError(
            f"[skill-catalog-count] README claims ~{claimed} skills, derived {derived} from "
            f"skills/*/SKILL.md minus {sorted(CORE_LOOP_VERBS)}"
        )


def _mutate_skill_catalog_count(tmp_path_arg: Path) -> Path:
    dest = _materialize(REPO_ROOT, tmp_path_arg, ["README.md", "skills"])
    synthetic = dest / "skills" / "zz-synthetic-doc-claims-probe"
    synthetic.mkdir(parents=True)
    (synthetic / "SKILL.md").write_text("---\nname: zz-synthetic\n---\n", encoding="utf-8")
    return dest


# ---- 2. agent-roster-terminology (AC-DRT-8) --------------------------------

_AGENT_REF_RE = re.compile(r"`([a-z][a-z0-9]*(?:-[a-z0-9]+)+)`")


def _agent_stems(root: Path) -> set:
    agents_dir = root / "agents"
    if not agents_dir.exists():
        raise MissingSourceError("missing derivation source: agents/")
    return {p.stem for p in agents_dir.glob("*.md")}


def _referenced_agent_names(text: str) -> set:
    names = set()
    for line in text.split("\n"):
        if "subagent" in line.lower():
            names.update(_AGENT_REF_RE.findall(line))
    return names


def _derive_agent_roster(root: Path) -> tuple:
    text = read_doc_text(root, "docs/TERMINOLOGY.md")
    referenced = _referenced_agent_names(text)
    stems = _agent_stems(root)
    return tuple(sorted(referenced - stems))


def _check_agent_roster(root: Path) -> None:
    unresolved = _derive_agent_roster(root)
    if unresolved:
        raise AssertionError(
            f"[agent-roster-terminology] docs/TERMINOLOGY.md names agent(s) with no "
            f"agents/<name>.md: {unresolved}"
        )


def _mutate_agent_roster(tmp_path_arg: Path) -> Path:
    dest = _materialize(REPO_ROOT, tmp_path_arg, ["docs/TERMINOLOGY.md", "agents"])
    target = dest / "agents" / "security-reviewer.md"
    target.rename(dest / "agents" / "security-reviewer-renamed.md")
    return dest


# ---- 3. doctor-probe-claims (AC-DRT-9) -------------------------------------

_PROBE_RUN_RE = re.compile(r'_run\(\s*"([a-z0-9-]+)"')
_QUICKSTART_PROBES_RE = re.compile(r"DOCTOR-GREEN \((\d+) probes: ([^)]+)\)")


def _derive_doctor_probe_ids(root: Path) -> list:
    text = read_doc_text(root, "scripts/foundry-doctor.py")
    ids = _PROBE_RUN_RE.findall(text)
    if not ids:
        raise MissingSourceError("scripts/foundry-doctor.py yields no parseable probe registrations")
    return ids


def _token_contains(a: str, b: str) -> bool:
    a, b = a.lower(), b.lower()
    return a in b or b in a


def _label_matches_identifier(label: str, identifier: str) -> bool:
    label_tokens = label.split()
    id_tokens = identifier.split("-")
    n, m = len(label_tokens), len(id_tokens)
    if n > m:
        return False
    for start in range(m - n + 1):
        if all(_token_contains(label_tokens[j], id_tokens[start + j]) for j in range(n)):
            return True
    return False


def _check_doctor_probe_claims(root: Path) -> None:
    text = read_doc_text(root, "docs/QUICKSTART.md")
    m = _QUICKSTART_PROBES_RE.search(text)
    if not m:
        raise MissingSourceError("docs/QUICKSTART.md yields no parseable doctor-probe claim")
    claimed_count = int(m.group(1))
    labels = [s.strip() for s in m.group(2).split(",")]
    ids = _derive_doctor_probe_ids(root)
    if claimed_count != len(ids):
        raise AssertionError(
            f"[doctor-probe-claims] QUICKSTART claims {claimed_count} probes, doctor registers "
            f"{len(ids)}: {ids}"
        )
    if len(labels) != len(ids):
        raise AssertionError(
            f"[doctor-probe-claims] QUICKSTART lists {len(labels)} labels {labels} against "
            f"{len(ids)} probe ids {ids}"
        )
    matched_ids = set()
    for label in labels:
        candidates = [pid for pid in ids if _label_matches_identifier(label, pid)]
        if len(candidates) != 1:
            raise AssertionError(
                f"[doctor-probe-claims] label {label!r} does not match exactly one probe id "
                f"(candidates={candidates}) among {ids}"
            )
        pid = candidates[0]
        if pid in matched_ids:
            raise AssertionError(f"[doctor-probe-claims] probe id {pid!r} matched by more than one label")
        matched_ids.add(pid)
    if matched_ids != set(ids):
        raise AssertionError(
            f"[doctor-probe-claims] not a bijection: unmatched probe id(s) {set(ids) - matched_ids}"
        )


def _mutate_doctor_probe_claims(tmp_path_arg: Path) -> Path:
    dest = _materialize(REPO_ROOT, tmp_path_arg, ["docs/QUICKSTART.md", "scripts/foundry-doctor.py"])
    p = dest / "scripts" / "foundry-doctor.py"
    p.write_text(p.read_text(encoding="utf-8").replace('"operator-registry"', '"acct-registry"'), encoding="utf-8")
    return dest


# ---- 4/5. verb-refs-readme / verb-refs-quickstart (AC-DRT-10) -------------

_VERB_REF_RE = re.compile(r"/foundry:([A-Za-z][A-Za-z0-9_-]*)")


def _referenced_verbs(text: str) -> list:
    return sorted(set(_VERB_REF_RE.findall(text)))


def _derive_verb_refs(root: Path, doc: str) -> tuple:
    text = read_doc_text(root, doc)
    verbs = _referenced_verbs(text)
    shipped = _shipped_skill_names(root)
    return tuple(v for v in verbs if v not in shipped)


def _check_verb_refs(root: Path, doc: str, claim_id: str) -> None:
    unresolved = _derive_verb_refs(root, doc)
    if unresolved:
        raise AssertionError(
            f"[{claim_id}] {doc} references /foundry:<verb> not resolving to "
            f"skills/<verb>/SKILL.md: {unresolved}"
        )


def _mutate_verb_refs(tmp_path_arg: Path, doc: str) -> Path:
    dest = _materialize(REPO_ROOT, tmp_path_arg, [doc, "skills"])
    (dest / "skills" / "release").rename(dest / "skills" / "release-renamed")
    return dest


# ---- 6. gate-tier-label (AC-DRT-11) ----------------------------------------

_CLAIMED_TIER_JOB_LIST_RE = re.compile(
    r"((?:`[a-z][a-z0-9-]*`(?:,\s*|\s+and\s+)?)+)\s+gate jobs? labels? (?:its|their)\s+tier"
)
_BACKTICK_NAME_RE = re.compile(r"`([a-z][a-z0-9-]*)`")
_EMIT_TIER_RE = re.compile(r"merge floor tier:\s*([A-Z])\b")
_JOB_BLOCK_RE = re.compile(r"^  ([a-z][a-z0-9-]*):\s*$", re.MULTILINE)
_TIER_TABLE_ROW_RE = re.compile(r"^\|\s*\*\*([A-Z])\*\*\s*\|", re.MULTILINE)


def _emitting_jobs_and_tiers(workflow_text: str) -> dict:
    job_starts = [(m.group(1), m.start()) for m in _JOB_BLOCK_RE.finditer(workflow_text)]
    job_starts.append((None, len(workflow_text)))
    emitting = {}
    for idx in range(len(job_starts) - 1):
        name, start = job_starts[idx]
        _, end = job_starts[idx + 1]
        block = workflow_text[start:end]
        m = _EMIT_TIER_RE.search(block)
        if m:
            emitting[name] = m.group(1)
    return emitting


# BOTH workflow files, because the gate jobs are split by trigger: the two metadata gates run on
# `pull_request_target` (definition taken from the base branch, so a fork cannot rewrite them) and
# `shell-parse-bash32` stays on `pull_request` because it checks out fork code. Reading only one
# file would let the README claim tier labels for jobs that no longer emit them — the exact
# doc-drift class this claim exists to catch.
_GATE_WORKFLOWS = (".github/workflows/btb-gates.yml", ".github/workflows/btb-gates-base.yml")


def _derive_gate_tier(root: Path) -> tuple:
    emitting = {}
    for rel in _GATE_WORKFLOWS:
        emitting.update(_emitting_jobs_and_tiers(read_doc_text(root, rel)))
    tier_table_text = read_doc_text(root, "docs/merge-floor.md")
    documented_tiers = set(_TIER_TABLE_ROW_RE.findall(tier_table_text))
    return emitting, documented_tiers


def _check_gate_tier(root: Path) -> None:
    text = read_doc_text(root, "README.md")
    m = _CLAIMED_TIER_JOB_LIST_RE.search(text)
    if not m:
        raise MissingSourceError("README.md yields no parseable gate-tier-label claim")
    claimed_jobs = set(_BACKTICK_NAME_RE.findall(m.group(1)))
    emitting, documented_tiers = _derive_gate_tier(root)
    emitting_jobs = set(emitting)
    if claimed_jobs != emitting_jobs:
        raise AssertionError(
            f"[gate-tier-label] README claims tier-label job(s) {claimed_jobs}, actual "
            f"emitting job(s) {emitting_jobs}"
        )
    for job, tier in emitting.items():
        if tier not in documented_tiers:
            raise AssertionError(
                f"[gate-tier-label] job {job!r} emits tier {tier!r}, not in docs/merge-floor.md's "
                f"tier table {documented_tiers}"
            )


def _mutate_gate_tier(tmp_path_arg: Path) -> Path:
    dest = _materialize(
        REPO_ROOT, tmp_path_arg, ["README.md", *_GATE_WORKFLOWS, "docs/merge-floor.md"]
    )
    # Mutate in the file that actually DEFINES spec-link now. Renaming in btb-gates.yml would be a
    # no-op mutation, which would make this claim's own mutation guard vacuous — a green that can
    # never go red.
    p = dest / ".github" / "workflows" / "btb-gates-base.yml"
    p.write_text(p.read_text(encoding="utf-8").replace("  spec-link-base:\n", "  spec-link-renamed:\n"), encoding="utf-8")
    return dest


# ---- 7. test-count-band (AC-DRT-12) ----------------------------------------

def _pytest_collected_count(tests_dir: Path) -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(tests_dir), "-q", "--collect-only"],
        capture_output=True,
        text=True,
        cwd=str(tests_dir.parent),
    )
    m = re.search(r"(\d+) tests? collected", proc.stdout)
    if m:
        return int(m.group(1))
    if "no tests collected" in proc.stdout:
        return 0
    raise MissingSourceError(
        f"pytest --collect-only over {tests_dir} yields no parseable collected count "
        f"(rc={proc.returncode}): {proc.stdout[-500:]}"
    )


def _derive_test_count(root: Path) -> int:
    tests_dir = root / "tests"
    if not tests_dir.exists():
        raise MissingSourceError("missing derivation source: tests/")
    return _pytest_collected_count(tests_dir)


def _check_test_count(root: Path) -> None:
    text = read_doc_text(root, "README.md")
    m = re.search(r"(\d+)\s+pytest tests", text)
    if not m:
        raise MissingSourceError("README.md yields no parseable pytest test-count claim")
    claimed = int(m.group(1))
    derived = _derive_test_count(root)
    floor = math.floor(UNDERSTATEMENT_FLOOR_RATIO * derived)
    if claimed > derived or claimed < floor:
        raise AssertionError(
            f"[test-count-band] README claims {claimed} tests, derived {derived} "
            f"(admissible band [{floor}, {derived}])"
        )


def _mutate_test_count(tmp_path_arg: Path) -> Path:
    # remove-unit: delete every collected test module (keeping conftest.py/fixtures so the
    # copy stays structurally valid) so the derived count drops far below README's claimed
    # floor — robust to the real suite's size growing over time, unlike deleting a fixed
    # handful of named files.
    dest = _materialize(REPO_ROOT, tmp_path_arg, ["README.md", "tests", "scripts"])
    for test_file in (dest / "tests").glob("test_*.py"):
        test_file.unlink()
    return dest


# ---- 8. documented-paths-stack-profiles (AC-DRT-13) ------------------------

_STACK_PROFILES_RE = re.compile(r"`packs/stack-profiles/`\s*\(([^;)]+)")


def _derive_stack_profiles(root: Path) -> set:
    base = root / "packs" / "stack-profiles"
    if not base.exists():
        raise MissingSourceError("missing derivation source: packs/stack-profiles/")
    return {p.name for p in base.iterdir() if p.is_dir()}


def _check_stack_profiles(root: Path) -> None:
    text = read_doc_text(root, "docs/QUICKSTART.md")
    m = _STACK_PROFILES_RE.search(text)
    if not m:
        raise MissingSourceError("docs/QUICKSTART.md yields no parseable stack-profiles claim")
    claimed = [s.strip() for s in m.group(1).split(",")]
    actual = _derive_stack_profiles(root)
    missing = [c for c in claimed if c not in actual]
    if missing:
        raise AssertionError(
            f"[documented-paths-stack-profiles] docs/QUICKSTART.md names stack profile(s) "
            f"absent from packs/stack-profiles/: {missing}"
        )


def _mutate_stack_profiles(tmp_path_arg: Path) -> Path:
    dest = _materialize(REPO_ROOT, tmp_path_arg, ["docs/QUICKSTART.md", "packs/stack-profiles"])
    shutil.rmtree(dest / "packs" / "stack-profiles" / "node-web")
    return dest


# ---- 9. documented-paths-git-discipline-hook (AC-DRT-13) -------------------

def _derive_git_discipline_hook_present(root: Path) -> bool:
    return (root / "hooks" / "foundry-git-discipline.sh").exists()


def _check_git_discipline_hook_path(root: Path) -> None:
    text = read_doc_text(root, "docs/merge-floor.md")
    if "hooks/foundry-git-discipline.sh" not in text:
        raise MissingSourceError("docs/merge-floor.md yields no parseable git-discipline-hook path claim")
    if not _derive_git_discipline_hook_present(root):
        raise AssertionError(
            "[documented-paths-git-discipline-hook] docs/merge-floor.md names "
            "hooks/foundry-git-discipline.sh, absent from the tree"
        )


def _mutate_git_discipline_hook_path(tmp_path_arg: Path) -> Path:
    dest = _materialize(REPO_ROOT, tmp_path_arg, ["docs/merge-floor.md", "hooks/foundry-git-discipline.sh"])
    (dest / "hooks" / "foundry-git-discipline.sh").unlink()
    return dest


# ---- 10. node-version-quickstart (completeness) ----------------------------

_NODE_VERSION_CI_RE = re.compile(r'node-version:\s*"(\d+)"')
_NODE_VERSION_DOC_RE = re.compile(r"\*\*node (\d+)\+\*\*")


def _derive_node_version(root: Path) -> str:
    text = read_doc_text(root, ".github/workflows/ci.yml")
    versions = set(_NODE_VERSION_CI_RE.findall(text))
    if not versions:
        raise MissingSourceError(".github/workflows/ci.yml yields no parseable node-version pin")
    if len(versions) > 1:
        raise AssertionError(f"[node-version-quickstart] ci.yml pins inconsistent node versions: {versions}")
    return next(iter(versions))


def _check_node_version(root: Path) -> None:
    text = read_doc_text(root, "docs/QUICKSTART.md")
    m = _NODE_VERSION_DOC_RE.search(text)
    if not m:
        raise MissingSourceError("docs/QUICKSTART.md yields no parseable node-version claim")
    claimed = m.group(1)
    derived = _derive_node_version(root)
    if claimed != derived:
        raise AssertionError(
            f"[node-version-quickstart] docs/QUICKSTART.md claims node {claimed}+, CI pins "
            f"node-version {derived}"
        )


def _mutate_node_version(tmp_path_arg: Path) -> Path:
    dest = _materialize(REPO_ROOT, tmp_path_arg, ["docs/QUICKSTART.md", ".github/workflows/ci.yml"])
    p = dest / ".github" / "workflows" / "ci.yml"
    p.write_text(p.read_text(encoding="utf-8").replace('node-version: "22"', 'node-version: "23"'), encoding="utf-8")
    return dest


# ---- 11. spec-size-ceiling-quickstart (completeness) -----------------------

_HARD_ACS_RE = re.compile(r"HARD_ACS\s*=\s*(\d+)")
_HARD_WORDS_RE = re.compile(r"HARD_WORDS\s*=\s*(\d+)")
_QUICKSTART_SIZE_RE = re.compile(r"(\d+) acceptance criteria / ([\d,]+) words")


def _derive_spec_size_ceiling(root: Path) -> tuple:
    text = read_doc_text(root, "scripts/foundry-spec-lint.py")
    ac_m = _HARD_ACS_RE.search(text)
    words_m = _HARD_WORDS_RE.search(text)
    if not ac_m or not words_m:
        raise MissingSourceError(
            "scripts/foundry-spec-lint.py yields no parseable HARD_ACS/HARD_WORDS constants"
        )
    return int(ac_m.group(1)), int(words_m.group(1))


def _check_spec_size_ceiling(root: Path) -> None:
    text = read_doc_text(root, "docs/QUICKSTART.md")
    m = _QUICKSTART_SIZE_RE.search(text)
    if not m:
        raise MissingSourceError("docs/QUICKSTART.md yields no parseable spec-size-ceiling claim")
    claimed = (int(m.group(1)), int(m.group(2).replace(",", "")))
    derived = _derive_spec_size_ceiling(root)
    if claimed != derived:
        raise AssertionError(
            f"[spec-size-ceiling-quickstart] docs/QUICKSTART.md claims {claimed[0]} ACs / "
            f"{claimed[1]} words, scripts/foundry-spec-lint.py pins {derived[0]}/{derived[1]}"
        )


def _mutate_spec_size_ceiling(tmp_path_arg: Path) -> Path:
    dest = _materialize(REPO_ROOT, tmp_path_arg, ["docs/QUICKSTART.md", "scripts/foundry-spec-lint.py"])
    p = dest / "scripts" / "foundry-spec-lint.py"
    p.write_text(p.read_text(encoding="utf-8").replace("HARD_WORDS = 8000", "HARD_WORDS = 7999"), encoding="utf-8")
    return dest


# ---- 12./13. retired (pre-v1 content pass) ---------------------------------
# subtraction-check-cli-count and subtraction-doctor-loc derived their ground truth from
# `git show v0.23.0:…` — impossible against a zero-history public tree, and the CONTRIBUTING
# claims they checked were removed with the narrative they kept accurate.


# ---- 14. terminology-section-refs (completeness) ---------------------------

_SECTION_REF_RE = re.compile(r"§(\d+)")
_NUMBERED_HEADING_RE = re.compile(r"^##\s+(\d+)\.", re.MULTILINE)


def _derive_terminology_headings(root: Path) -> set:
    text = read_doc_text(root, "docs/TERMINOLOGY.md")
    return set(_NUMBERED_HEADING_RE.findall(text))


def _check_terminology_section_refs(root: Path) -> None:
    text = read_doc_text(root, "docs/TERMINOLOGY.md")
    refs = set(_SECTION_REF_RE.findall(text))
    headings = _derive_terminology_headings(root)
    unresolved = refs - headings
    if unresolved:
        raise AssertionError(
            f"[terminology-section-refs] docs/TERMINOLOGY.md's §-reference(s) "
            f"{sorted(unresolved)} do not resolve to a '## N.' heading in the same document "
            f"(headings: {sorted(headings)})"
        )


def _mutate_terminology_section_refs(tmp_path_arg: Path) -> Path:
    dest = _materialize(REPO_ROOT, tmp_path_arg, ["docs/TERMINOLOGY.md"])
    p = dest / "docs" / "TERMINOLOGY.md"
    p.write_text(
        p.read_text(encoding="utf-8").replace("## 5. Library naming standard", "## 9. Library naming standard"),
        encoding="utf-8",
    )
    return dest


# ---- 15. terminology-illustrative-markers (completeness) -------------------
# "(1) the canonical verb-object" / "(2) 2-3 natural-language trigger phrases" / "PR #51" are
# illustrative prose (an inline enumeration + a fictional example PR number), not shipped-tree
# claims. Bound here as self-consistency/format checks over the doc's own live text (still
# read from the tree at call time, still mutation-convictable) rather than duplicated as a
# hardcoded expected value.

def _derive_terminology_markers(root: Path) -> dict:
    text = read_doc_text(root, "docs/TERMINOLOGY.md")
    idx1 = text.find("(1) the canonical verb-object")
    idx2 = text.find("(2) 2–3 natural-language trigger phrases")
    return {
        "sequential-parens": idx1 != -1 and idx2 != -1 and idx1 < idx2,
        "pr-example": bool(re.search(r"PR #\d+", text)),
    }


def _check_terminology_markers(root: Path) -> None:
    checks = _derive_terminology_markers(root)
    failed = [k for k, v in checks.items() if not v]
    if failed:
        raise AssertionError(
            f"[terminology-illustrative-markers] docs/TERMINOLOGY.md's illustrative marker(s) "
            f"no longer match expected form: {failed}"
        )


def _mutate_terminology_markers(tmp_path_arg: Path) -> Path:
    dest = _materialize(REPO_ROOT, tmp_path_arg, ["docs/TERMINOLOGY.md"])
    p = dest / "docs" / "TERMINOLOGY.md"
    p.write_text(
        p.read_text(encoding="utf-8").replace("(1) the canonical verb-object", "(9) the canonical verb-object"),
        encoding="utf-8",
    )
    return dest


# ---- 16. architecture-illustrative-marker (completeness) -------------------

def _derive_architecture_marker(root: Path) -> bool:
    text = read_doc_text(root, "docs/architecture.md")
    return bool(re.search(r"adopter #\d+", text))


def _check_architecture_marker(root: Path) -> None:
    if not _derive_architecture_marker(root):
        raise AssertionError(
            "[architecture-illustrative-marker] docs/architecture.md no longer carries the "
            "'adopter #<n>' illustrative marker"
        )


def _mutate_architecture_marker(tmp_path_arg: Path) -> Path:
    dest = _materialize(REPO_ROOT, tmp_path_arg, ["docs/architecture.md"])
    p = dest / "docs" / "architecture.md"
    p.write_text(p.read_text(encoding="utf-8").replace("adopter #0", "adopter zero"), encoding="utf-8")
    return dest


# ---- 17. comparison-year-marker (completeness) -----------------------------

def _derive_comparison_marker(root: Path) -> bool:
    text = read_doc_text(root, "docs/comparison.md")
    return bool(re.search(r"mid-\d{4}", text))


def _check_comparison_marker(root: Path) -> None:
    if not _derive_comparison_marker(root):
        raise AssertionError(
            "[comparison-year-marker] docs/comparison.md no longer carries the 'mid-<year>' "
            "currency marker"
        )


def _mutate_comparison_marker(tmp_path_arg: Path) -> Path:
    dest = _materialize(REPO_ROOT, tmp_path_arg, ["docs/comparison.md"])
    p = dest / "docs" / "comparison.md"
    p.write_text(p.read_text(encoding="utf-8").replace("mid-2026", "mid-year"), encoding="utf-8")
    return dest


# ---- 18. merge-floor-quickstart-step-ref (completeness) --------------------

_STEP_REF_RE = re.compile(r"step (\d+)\)")


def _derive_quickstart_steps(root: Path) -> set:
    text = read_doc_text(root, "docs/QUICKSTART.md")
    steps = set(_NUMBERED_HEADING_RE.findall(text))
    if not steps:
        raise MissingSourceError("docs/QUICKSTART.md yields no parseable numbered-step headings")
    return steps


def _check_merge_floor_step_ref(root: Path) -> None:
    text = read_doc_text(root, "docs/merge-floor.md")
    m = _STEP_REF_RE.search(text)
    if not m:
        raise MissingSourceError("docs/merge-floor.md yields no parseable QUICKSTART step reference")
    claimed_step = m.group(1)
    steps = _derive_quickstart_steps(root)
    if claimed_step not in steps:
        raise AssertionError(
            f"[merge-floor-quickstart-step-ref] docs/merge-floor.md references QUICKSTART step "
            f"{claimed_step}, no '## {claimed_step}.' heading found (steps: {sorted(steps)})"
        )


def _mutate_merge_floor_step_ref(tmp_path_arg: Path) -> Path:
    dest = _materialize(REPO_ROOT, tmp_path_arg, ["docs/merge-floor.md", "docs/QUICKSTART.md"])
    p = dest / "docs" / "QUICKSTART.md"
    p.write_text(
        p.read_text(encoding="utf-8").replace("## 1. Wire your repo (once)", "## 9. Wire your repo (once)"),
        encoding="utf-8",
    )
    return dest


# ---- 19. bootstrap-emitted-refs (AC-BHT-1/7/8/11/12/13, feat-foundry-bootstrap-handoff-truth) -----
# Every emitted reference scripts/foundry-bootstrap.sh writes to stdout/stderr -- by ANY mechanism
# (a printf/echo call, a heredoc, the `usage` rendering of its own header comment block, or a
# log/plan/die helper; Terminology) -- must resolve in the shipped plugin tree, or be declared
# external with a reason from the closed set. The extractor is a heuristic over string literals
# (spec Residuals): it scans exactly the three emission-bearing surfaces named above -- the
# header/usage block (lines 2..the `# BOOTSTRAP-USAGE-END` sentinel, mirroring usage()'s own `sed`
# range), single-line `log`/`plan`/`die`/`printf`/`echo` calls (the quoted string immediately
# following the keyword), and heredoc bodies (`cat <<DELIM ... DELIM`, any delimiter) -- rather
# than the whole script, so it does not choke on internal grep/sed pattern literals or ordinary
# design-note comments that never reach stdout. A handful of two-word English phrases that are
# structurally indistinguishable from a real two-segment path/repo-slug (e.g. "add/install",
# "user/prompt") are excluded by a small, hand-audited, reason-pinned denylist -- the disclosed,
# accepted narrowness (AC-BHT-13's floor bounds, but does not eliminate, this heuristic's blind
# spots). Two further named bounds (security-review finding, not yet closed by any checkpoint):
#   (i) a heredoc opener line followed by a redirection or pipe (e.g. `cat <<EOF | foo` or
#       `cat <<EOF > file`) is NOT recognized -- the opener regex requires the delimiter to be the
#       last token on its line -- so a reference inside such a heredoc's body is invisible to this
#       extractor. None of today's heredocs take that shape; a sibling atom adding one should read
#       this note before assuming coverage.
#   (ii) only the FIRST quoted argument immediately following `log`/`plan`/`die`/`printf`/`echo` is
#       captured. A bare, unquoted `echo docs/x.md` (no surrounding quotes) or a reference in a
#       call's SECOND-or-later quoted argument is invisible. Every call site in today's script
#       quotes its one message argument, so this is a latent gap, not a live miss.

EXTERNAL_REF_REASONS = frozenset({
    "adopter-workspace", "home-config", "template-repo", "remote-repo", "slash-verb",
})

# The declared-external-reference registry (Terminology, AC-BHT-1/AC-BHT-11): every emitted
# reference that does NOT resolve inside the shipped plugin tree, mapped to a reason from the
# closed set above. `.claude/foundry-operators.json` is declared here even though a literal file of
# that name happens to exist in THIS repo's own `.claude/` (this plugin self-hosts itself) --
# leaving it undeclared would make the claim pass by an accident of this repo's own dogfood
# config, not because the reference is a genuine plugin-tree artifact: the string the script emits
# names a path inside the ADOPTER's target, not a file this plugin ships to be read.
DECLARED_EXTERNAL_REFS = {
    ".claude/gh-identity": "adopter-workspace",         # written into the ADOPTER's target, not this tree
    ".claude/foundry-operator": "adopter-workspace",    # written into the ADOPTER's target, not this tree
    ".claude/foundry-operators.json": "adopter-workspace",  # ditto -- coincidentally also present in
                                                          # this self-hosting repo's OWN .claude/, which
                                                          # is not why it is declared here (see above)
    "/foundry:init": "slash-verb",
    "/foundry:doctor": "slash-verb",
    "lukasrepublic/agentic-handbook": "template-repo",  # the default workspace template's own repo
    "lukasrepublic/agentic-foundry": "remote-repo",      # this plugin's own repo (marketplace default)
}

# Pinned literal expected copy, hand-synchronized (not derived from DECLARED_EXTERNAL_REFS) — a
# reviewer sees any addition/removal as a two-place diff (AC-BHT-11), the same idiom as
# EXCLUDED_DOCS/EXPECTED_EXCLUSIONS above.
EXPECTED_EXTERNAL_REFS = {
    ".claude/gh-identity": "adopter-workspace",
    ".claude/foundry-operator": "adopter-workspace",
    ".claude/foundry-operators.json": "adopter-workspace",
    "/foundry:init": "slash-verb",
    "/foundry:doctor": "slash-verb",
    "lukasrepublic/agentic-handbook": "template-repo",
    "lukasrepublic/agentic-foundry": "remote-repo",
}

# The pinned floor of emitted references the extractor must find, spanning three distinct
# emission mechanisms (AC-BHT-13): the guidance emission (a heredoc), two `plan` helper calls, and
# the `usage` rendering of the script's own header comment block (the ONLY mechanism that emits
# `lukasrepublic/agentic-handbook` — an extractor hardcoded to one path cannot clear this floor).
# This is the RAW extracted set (before DECLARED_EXTERNAL_REFS is dropped) -- `.claude/foundry-
# operators.json` being declared external above does not remove it from what the extractor must
# still FIND; it only changes how the reference is treated once found.
EXPECTED_EMITTED_REF_FLOOR = frozenset({
    "docs/identity-isolation.md",
    ".claude/gh-identity",
    ".claude/foundry-operators.json",
    "lukasrepublic/agentic-handbook",
})

_BOOTSTRAP_HEADER_END_MARKER = "# BOOTSTRAP-USAGE-END"

# Structurally indistinguishable from a real path/repo-slug, but are ordinary English shorthand
# ("A/B" meaning "A or B") that never resolves anywhere. The extractor's only otherwise-unpinned
# escape hatch (a bare, reason-free entry could silence a future genuinely-dangling reference by
# simply matching its shape) -- so, the same idiom as EXCLUDED_DOCS/EXPECTED_EXCLUSIONS and
# DECLARED_EXTERNAL_REFS above: every entry carries a reason, and the whole mapping is pinned
# against a hand-written expected copy (test_prose_slash_denylist_is_pinned).
_KNOWN_PROSE_SLASH_DENYLIST = {
    "add/install": "verb shorthand ('the marketplace add/install operations'), not a path",
    "env/identity": "noun shorthand ('the machine/user env/identity split'), not a path",
    "machine/user": "scope shorthand ('machine/user scope'), not a path",
    "reference/symlink": "noun pair ('the conflicting reference/symlink'), not a path",
    "user/prompt": "noun pair ('gh api user/prompt'), not a path",
    "user.name/user.email": "two git-config KEYS joined by '/' for prose, not a path",
}

# Pinned literal expected copy, hand-synchronized (not derived from the denylist above).
_EXPECTED_PROSE_SLASH_DENYLIST = {
    "add/install": "verb shorthand ('the marketplace add/install operations'), not a path",
    "env/identity": "noun shorthand ('the machine/user env/identity split'), not a path",
    "machine/user": "scope shorthand ('machine/user scope'), not a path",
    "reference/symlink": "noun pair ('the conflicting reference/symlink'), not a path",
    "user/prompt": "noun pair ('gh api user/prompt'), not a path",
    "user.name/user.email": "two git-config KEYS joined by '/' for prose, not a path",
}

_REF_TOKEN_SPLIT_RE = re.compile(r"[^\s\"'`]+")
_REF_EXT_SHAPE_RE = re.compile(r"^[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)+\.[A-Za-z0-9]{1,10}$")
_REF_DOTPATH_SHAPE_RE = re.compile(r"^\.[A-Za-z0-9_\-]+(?:/[A-Za-z0-9_.\-]+)+$")
_REF_SLASHVERB_SHAPE_RE = re.compile(r"^/[A-Za-z][A-Za-z0-9:_\-]*$")
_REF_REPOSLUG_SHAPE_RE = re.compile(r"^[a-z0-9][a-z0-9\-]*/[a-z0-9][a-z0-9._\-]*$")


def _candidate_path_tokens(text: str) -> set:
    """A path-like token (spec Terminology): no whitespace, at least one '/' with a non-empty
    segment on each side; a token rooted at '~'/'$HOME' or produced by shell parameter expansion
    falls outside the definition. Restricted to four recognizable path shapes (has-extension,
    dotfile/dotdir, leading-slash verb, lowercase repo-slug) minus the reason-pinned prose
    denylist."""
    found = set()
    for raw in _REF_TOKEN_SPLIT_RE.findall(text):
        tok = raw.strip("'\"`,;()[]{}")
        tok = tok.rstrip(".,;:)")
        if not tok or "$" in tok or tok.startswith("~") or "/" not in tok:
            continue
        if tok in _KNOWN_PROSE_SLASH_DENYLIST:
            continue
        if (
            _REF_EXT_SHAPE_RE.match(tok)
            or _REF_DOTPATH_SHAPE_RE.match(tok)
            or _REF_SLASHVERB_SHAPE_RE.match(tok)
            or _REF_REPOSLUG_SHAPE_RE.match(tok)
        ):
            found.add(tok)
    return found


_REF_CALL_SITE_RE = re.compile(r"\b(?:log|plan|die|printf|echo)\s+(?:'[^']*'|\"[^\"]*\")")
_REF_HEREDOC_START_RE = re.compile(r"<<-?'?(\w+)'?\s*$")


def _extract_emitted_references(script_text: str) -> set:
    """Static extraction over the three emission-bearing surfaces (module docstring above; see
    also the two named bounds recorded there)."""
    lines = script_text.split("\n")
    refs = set()

    header_end = None
    for i, line in enumerate(lines):
        if line.startswith(_BOOTSTRAP_HEADER_END_MARKER):
            header_end = i
            break
    if header_end is not None:
        for line in lines[1:header_end]:  # mirrors usage()'s own `sed '2,/pat/p' | sed '$d'` range
            refs |= _candidate_path_tokens(line)

    for line in lines:
        if line.strip().startswith("#"):
            continue
        m = _REF_CALL_SITE_RE.search(line)
        if m:
            refs |= _candidate_path_tokens(m.group(0))

    i = 0
    while i < len(lines):
        m = _REF_HEREDOC_START_RE.search(lines[i])
        if m:
            delim = m.group(1)
            j = i + 1
            while j < len(lines) and lines[j] != delim:
                refs |= _candidate_path_tokens(lines[j])
                j += 1
            i = j
        i += 1

    return refs


def _all_bootstrap_emitted_references(root: Path) -> frozenset:
    text = read_doc_text(root, "scripts/foundry-bootstrap.sh")
    return frozenset(_extract_emitted_references(text))


def _resolves_in_tree(root: Path, ref: str) -> bool:
    """A reference resolves ONLY relative to `root`. `pathlib.Path.__truediv__` DISCARDS the left
    operand when the right operand looks absolute (`Path("/a") / "/b" == Path("/b")`), so a naive
    `(root / ref).exists()` on an undeclared absolute-looking reference (e.g. a future `/usr/...`
    literal) would silently resolve against the HOST filesystem instead of the plugin tree -- a
    vacuous pass exactly backwards from the spec's fail-closed rule that a genuine absolute
    filesystem path matches no member of the closed external-reason set and so must fail until one
    is declared. Every reference reaching this function has already had the slash-verb (also
    leading-`/`) members of DECLARED_EXTERNAL_REFS dropped by the caller, so a `/`-leading ref
    still arriving here is, by construction, undeclared -- refuse it outright rather than let
    pathlib's join semantics decide."""
    if ref.startswith("/"):
        return False
    return (root / ref).exists()


def _derive_bootstrap_emitted_refs(root: Path) -> frozenset:
    """Returns resolution STATUS, never the raw extracted list (spec Design/notes) — so
    test_derivations_are_live's real-vs-mutated comparison is sensitive to the doc's presence,
    not merely to whether the script text changed (it doesn't, under this claim's mutation)."""
    all_refs = _all_bootstrap_emitted_references(root)
    remaining = all_refs - set(DECLARED_EXTERNAL_REFS)
    return frozenset(f"{ref}:{'ok' if _resolves_in_tree(root, ref) else 'missing'}" for ref in remaining)


def _check_bootstrap_emitted_refs(root: Path) -> None:
    all_refs = _all_bootstrap_emitted_references(root)  # MissingSourceError if unreadable
    remaining = all_refs - set(DECLARED_EXTERNAL_REFS)
    missing = sorted(ref for ref in remaining if not _resolves_in_tree(root, ref))
    if missing:
        raise AssertionError(
            f"[bootstrap-emitted-refs] emitted reference(s) do not resolve in the shipped plugin "
            f"tree: {missing}"
        )


def _mutate_bootstrap_emitted_refs(tmp_path_arg: Path) -> Path:
    """Materializes the script PLUS every plugin-tree reference that currently resolves (computed
    from REPO_ROOT at mutation time, so the copy list cannot rot), then deletes
    docs/identity-isolation.md from the copy — exactly one reference then goes missing, so the
    negative control convicts on the injected drift rather than on materialization noise."""
    all_refs = _all_bootstrap_emitted_references(REPO_ROOT)
    remaining = all_refs - set(DECLARED_EXTERNAL_REFS)
    resolving = [ref for ref in remaining if _resolves_in_tree(REPO_ROOT, ref)]
    dest = _materialize(REPO_ROOT, tmp_path_arg, ["scripts/foundry-bootstrap.sh"] + resolving)
    target = dest / "docs" / "identity-isolation.md"
    if target.exists():
        target.unlink()
    return dest


# ---- 20-23. feat-foundry-control-plane-docs (AC-CPD-3) --------------------------------------
# Four claims, one per mechanism named in docs/how-to/multi-repo-control-plane.md's `enforced`
# section. Each derivation reads or EXECUTES the live seam (never a dispatch-table presence-grep
# alone) — spec Design/notes "why four claim ids, not one". Claims (control-plane-authorize-
# degrade) and (control-plane-doctor-validation) EXECUTE real scripts against a hermetic fixture
# workspace built fresh inside a real OS temp dir on every call (functionally the same throwaway-
# directory idiom `tmp_path` gives a test, since `derive`/`check` only receive `root` — Design/
# notes "the executed derivations must be hermetic"): CLAUDE_PROJECT_DIR and the subprocess cwd
# are BOTH pointed at that fixture, so the real repo's tree, `.foundry/security-audit.jsonl` and
# doctor state are never touched (Security posture disclosure).

_CP_HOWTO_DOC = "docs/how-to/multi-repo-control-plane.md"

_CP_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def _cp_section_body(text: str, keyword: str) -> str:
    """Same idiom as tests/test_docs_claims.py's own `_cp_section_body` (duplicated, not
    imported, so this module stays a self-contained registry — the existing house style here)."""
    matches = list(_CP_HEADING_RE.finditer(text))
    found = [i for i, m in enumerate(matches) if keyword.lower() in m.group(2).lower()]
    if len(found) != 1:
        raise AssertionError(
            f"heading keyword {keyword!r} must occur in exactly one heading; found {len(found)}"
        )
    i = found[0]
    level = len(matches[i].group(1))
    body_start = matches[i].end()
    body_end = len(text)
    for j in range(i + 1, len(matches)):
        if len(matches[j].group(1)) <= level:
            body_end = matches[j].start()
            break
    return text[body_start:body_end]


def _cp_enforced_section(root: Path) -> str:
    text = read_doc_text(root, _CP_HOWTO_DOC)
    return _cp_section_body(text, "enforced")


def _import_module_from_path(path: Path, unique_name: str):
    if not path.is_file():
        raise MissingSourceError(f"missing derivation source: {path}")
    spec = importlib.util.spec_from_file_location(unique_name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- 20. control-plane-dispatch-enforcement --------------------------------------------------
# Reads the hook's LIVE bind-check invocation (the hook is what actually calls it; a dispatch
# table alone proves only that the subcommand exists) + foundry-wt's dispatched subcommands.

_CP_HOOK_BIND_CHECK_LINE = '"$WTBIN" bind-check "$tr" "$cpath"'
_CP_WT_BIND_CHECK_DISPATCH = "bind-check) shift; cmd_bind_check"


def _derive_dispatch_enforcement(root: Path) -> dict:
    hook_text = read_doc_text(root, "hooks/foundry-worktree-create.sh")
    wt_text = read_doc_text(root, "scripts/foundry-wt")
    return {
        "hook_calls_bind_check": _CP_HOOK_BIND_CHECK_LINE in hook_text,
        "wt_dispatches_bind_check": _CP_WT_BIND_CHECK_DISPATCH in wt_text,
    }


def _check_dispatch_enforcement(root: Path) -> None:
    derived = _derive_dispatch_enforcement(root)
    if not (derived["hook_calls_bind_check"] and derived["wt_dispatches_bind_check"]):
        raise AssertionError(
            f"[control-plane-dispatch-enforcement] the hook no longer calls bind-check on its "
            f"redirect path, or foundry-wt no longer dispatches it (derived: {derived}) — the "
            f"'dispatch bind-check' / 'repo-key resolution' enforced-section labels are no "
            f"longer grounded"
        )
    section = _cp_enforced_section(root)
    if "dispatch bind-check" not in section or "repo-key resolution" not in section:
        raise AssertionError(
            "[control-plane-dispatch-enforcement] the doc's enforced section no longer names "
            "'dispatch bind-check' / 'repo-key resolution'"
        )


def _mutate_dispatch_enforcement(tmp_path_arg: Path) -> Path:
    dest = _materialize(
        REPO_ROOT, tmp_path_arg,
        [_CP_HOWTO_DOC, "hooks/foundry-worktree-create.sh", "scripts/foundry-wt"],
    )
    p = dest / "hooks" / "foundry-worktree-create.sh"
    text = p.read_text(encoding="utf-8")
    original = '    if ! "$WTBIN" bind-check "$tr" "$cpath" >/dev/null 2>&1; then\n'
    if original not in text:
        raise MissingSourceError(
            "hooks/foundry-worktree-create.sh: expected bind-check invocation line not found "
            "(mutation target moved)"
        )
    p.write_text(text.replace(original, '    if false; then\n'), encoding="utf-8")
    return dest


# ---- 21. control-plane-authorize-degrade -----------------------------------------------------
# EXECUTES scripts/foundry-authorize.py against a hermetic fixture with an unresolvable
# target_repo — derives the outcome triple (degrade/warn output, exit status, written auth_seq).

_CP_AUTHZ_DENYLIST = {
    r"fails? closed at authoriz": "restates SETUP.md's inaccurate 'fails closed at authorization' claim",
    r"\b(refus\w*|block\w*|reject\w*|prevent\w*)\b[^.\n]*\bauthoriz\w*\b"
    r"|\bauthoriz\w*\b[^.\n]*\b(refus\w*|block\w*|reject\w*|prevent\w*)\b":
        "implies the freeze is refused/blocked, contradicting the derived 'freezes anyway' fact",
}
_EXPECTED_CP_AUTHZ_DENYLIST = {
    r"fails? closed at authoriz": "restates SETUP.md's inaccurate 'fails closed at authorization' claim",
    r"\b(refus\w*|block\w*|reject\w*|prevent\w*)\b[^.\n]*\bauthoriz\w*\b"
    r"|\bauthoriz\w*\b[^.\n]*\b(refus\w*|block\w*|reject\w*|prevent\w*)\b":
        "implies the freeze is refused/blocked, contradicting the derived 'freezes anyway' fact",
}


def _run_authorize_degrade_fixture(script: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="doc-claims-authz-") as td:
        fixture = Path(td)
        (fixture / ".claude").mkdir(parents=True)
        (fixture / ".claude" / "foundry-operators.json").write_text(json.dumps({
            "schema_version": 1,
            "operators": {"op_doc_claims": {"name": "T", "github": "t", "added_at": "2026-01-01"}},
        }), encoding="utf-8")
        # target_repo names a key ABSENT from repos{} -> the venue root never resolves.
        (fixture / ".claude" / "foundry-project.json").write_text(
            json.dumps({"schema_version": 1, "repos": {}}), encoding="utf-8",
        )
        specs_dir = fixture / "specs"
        specs_dir.mkdir()
        spec_path = specs_dir / "doc-claims-probe.md"
        contract_path = specs_dir / "doc-claims-probe.yaml"
        spec_path.write_text(
            "# Doc-claims probe (feat-doc-claims-probe)\n\n"
            "<!-- normative -->\n## Acceptance criteria\n\n"
            "- **AC-DCP-1**: the probe surface returns hi.\n"
            "<!-- /normative -->\n",
            encoding="utf-8",
        )
        contract_path.write_text(
            "spec_ref: specs/doc-claims-probe.md\n"
            'spec_sha256: "' + "0" * 64 + '"\n'
            "target_repo: unresolvable-ghost-key\n"
            "scope:\n  allowed_paths: [\"src/**\"]\n"
            "checkpoints:\n"
            "  - ac_id: AC-DCP-1\n"
            "    surface: \"cli:test\"\n"
            "    locator: \"echo hi\"\n"
            "    expect: {op: matches, value: \"hi\", baseline: pre-change}\n",
            encoding="utf-8",
        )
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(fixture)
        env["FOUNDRY_OPERATOR"] = "op_doc_claims"
        proc = subprocess.run(
            [sys.executable, str(script), "--spec", str(spec_path), "--contract", str(contract_path),
             "--operator", "op_doc_claims", "--mode", "lean",
             "--skip-audit-reason", "hermetic doc-claims fixture (AC-CPD-3(2))", "--yes"],
            cwd=str(fixture), capture_output=True, text=True, timeout=60, env=env,
        )
        out = proc.stdout + proc.stderr
        auth_seq = None
        try:
            doc = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
            auth_seq = ((doc or {}).get("authorized") or {}).get("auth_seq")
        except Exception:
            auth_seq = None
        return {
            "exit_code": proc.returncode,
            "has_warn_token": "warn:" in out,
            "has_degrade_token": "degrad" in out.lower(),
            "has_skipped_token": "SKIPPED" in out,
            "auth_seq": auth_seq,
        }


def _derive_authorize_degrade(root: Path) -> dict:
    script = root / "scripts" / "foundry-authorize.py"
    if not script.is_file():
        raise MissingSourceError(f"missing derivation source: scripts/foundry-authorize.py under {root}")
    return _run_authorize_degrade_fixture(script)


def _check_authorize_degrade(root: Path) -> None:
    derived = _derive_authorize_degrade(root)
    if derived["exit_code"] != 0:
        raise AssertionError(
            f"[control-plane-authorize-degrade] authorize exited {derived['exit_code']} over the "
            f"hermetic fixture (want 0 — the venue-degrade freeze no longer proceeds): {derived}"
        )
    if not (derived["has_warn_token"] and derived["has_degrade_token"] and derived["has_skipped_token"]):
        raise AssertionError(
            f"[control-plane-authorize-degrade] authorize did not emit the expected degrade/warn/"
            f"SKIPPED output over the hermetic fixture: {derived}"
        )
    if not (isinstance(derived["auth_seq"], int) and derived["auth_seq"] >= 1):
        raise AssertionError(
            f"[control-plane-authorize-degrade] authorize wrote no positive auth_seq trailer over "
            f"the hermetic fixture: {derived}"
        )
    section = _cp_enforced_section(root)
    if "authorization venue floors" not in section:
        raise AssertionError(
            "[control-plane-authorize-degrade] the doc's enforced section no longer names "
            "'authorization venue floors'"
        )
    for tok in ("degrade", "warn", "auth_seq"):
        if tok not in section:
            raise AssertionError(
                f"[control-plane-authorize-degrade] the doc's enforced section is missing the "
                f"literal token {tok!r}"
            )
    if "proceeds" not in section:
        raise AssertionError(
            "[control-plane-authorize-degrade] derivation shows the freeze PROCEEDS (exit 0, "
            "auth_seq written) but the doc's enforced section carries no 'proceeds' wording"
        )
    for pattern, reason in _CP_AUTHZ_DENYLIST.items():
        if re.search(pattern, section, re.I):
            raise AssertionError(
                f"[control-plane-authorize-degrade] the doc's enforced section matches the "
                f"paraphrase denylist ({pattern!r}: {reason})"
            )


def _mutate_authorize_degrade(tmp_path_arg: Path) -> Path:
    dest = _materialize(REPO_ROOT, tmp_path_arg, [_CP_HOWTO_DOC, "scripts"])
    p = dest / "scripts" / "foundry-authorize.py"
    text = p.read_text(encoding="utf-8")
    original = (
        '    if _venue_root is None:\n'
        '        print(f"  warn: surface⊆scope check degraded — venue root for target_repo {_tr!r} not "\n'
        '              "resolvable/cloned (ER #77 existence check skipped)")\n'
    )
    if original not in text:
        raise MissingSourceError(
            "scripts/foundry-authorize.py: expected venue-degrade branch text not found "
            "(mutation target moved)"
        )
    mutated = '    if _venue_root is None:\n        return 1  # doc-claims negative control: harden the degrade\n'
    p.write_text(text.replace(original, mutated), encoding="utf-8")
    return dest


# ---- 22. control-plane-doctor-validation -----------------------------------------------------
# Derives (a)/(b) whether the doctor's `checks` list actually WIRES the control-plane probe (a
# dispatch-table presence-grep would prove only that the FUNCTION exists, not that it runs), and
# (c) — by EXECUTING foundry-doctor.py --session-start against a deliberately broken fixture —
# that the advisory cadence exits zero regardless. (c) always runs, in BOTH regimes (spec
# AC-CPD-3(3): "the fail-open half of that compound claim SHALL be derived in both regimes").

_CP_DOCTOR_WIRING_LINE = '_run("control-plane", check_control_plane, project_dir=project_dir)'


def _run_doctor_session_start_fixture(script: Path, plugin_root: Path) -> int:
    with tempfile.TemporaryDirectory(prefix="doc-claims-doctor-") as td:
        fixture = Path(td)
        (fixture / ".claude").mkdir(parents=True)
        (fixture / ".claude" / "foundry-operators.json").write_text(json.dumps({
            "schema_version": 1,
            "operators": {"op_doc_claims": {"name": "T", "github": "t", "added_at": "2026-01-01"}},
        }), encoding="utf-8")
        # deliberately broken: a repos{} entry whose path does not exist (AC-CPP-1 dangling case).
        (fixture / ".claude" / "foundry-project.json").write_text(json.dumps({
            "schema_version": 1,
            "repos": {"ghost": {"path": "does-not-exist-anywhere"}},
        }), encoding="utf-8")
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(fixture)
        env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)
        proc = subprocess.run(
            [sys.executable, str(script), "--session-start"],
            cwd=str(fixture), capture_output=True, text=True, timeout=30, env=env,
        )
        return proc.returncode


def _derive_doctor_validation(root: Path) -> dict:
    script = root / "scripts" / "foundry-doctor.py"
    if not script.is_file():
        raise MissingSourceError(f"missing derivation source: scripts/foundry-doctor.py under {root}")
    doctor_text = read_doc_text(root, "scripts/foundry-doctor.py")
    return {
        "control_plane_wired": _CP_DOCTOR_WIRING_LINE in doctor_text,
        "session_start_exit_zero": _run_doctor_session_start_fixture(script, root) == 0,
    }


import hashlib as _hashlib

def _foundry_state_digest():
    h=_hashlib.sha256()
    d=REPO_ROOT / ".foundry"
    if d.is_dir():
        for p in sorted(d.rglob("*")):
            if p.is_file():
                h.update(str(p.relative_to(d)).encode()); h.update(p.read_bytes())
    return h.hexdigest()

import pytest as _pytest

@_pytest.fixture(autouse=True)
def _exec_seam_hermeticity_tripwire():
    """Security review 2026-08-02 (R1): the derived claims EXECUTE foundry-authorize.py and
    foundry-doctor.py against fixtures; if a future root-resolution refactor made either write
    into the live tree, every pytest run would silently mutate .foundry/. Fail loudly instead."""
    before=_foundry_state_digest()
    yield
    after=_foundry_state_digest()
    assert before == after, ".foundry/ mutated during a doc-claims test — the exec seam escaped its fixture"


def _check_doctor_validation(root: Path) -> None:
    derived = _derive_doctor_validation(root)
    section = _cp_enforced_section(root)
    if "doctor registry validation" not in section:
        raise AssertionError(
            "[control-plane-doctor-validation] the doc's enforced section no longer names "
            "'doctor registry validation'"
        )
    if not derived["session_start_exit_zero"]:
        raise AssertionError(
            f"[control-plane-doctor-validation] --session-start exited non-zero over a "
            f"deliberately broken fixture (want 0, fail-open in both regimes): {derived}"
        )
    if derived["control_plane_wired"]:
        # current regime (feat-foundry-control-plane-preflight has shipped): the doc must state
        # the doctor NOW validates repos{}, never the stale "does not validate" wording, and
        # 'session-root rule' must no longer read practice.
        if "does not validate" in section.lower():
            raise AssertionError(
                "[control-plane-doctor-validation] the doctor now validates repos{} (wired in "
                "its checks list) but the doc still carries the stale 'does not validate' wording"
            )
        m = re.search(r"\*\*session-root rule\*\*\s*—\s*([a-z-]+)", section, re.I)
        if not m or m.group(1).strip().lower() == "practice":
            raise AssertionError(
                "[control-plane-doctor-validation] the doctor now validates repos{} but "
                "'session-root rule' still reads 'practice' in the doc's enforced section"
            )
    else:
        # pre-flip regime (a mutated tree with the wiring removed): the doc (unchanged, still
        # describing the current shipped regime) must NOT match this mutated tree's facts.
        if "does not validate" not in section.lower():
            raise AssertionError(
                "[control-plane-doctor-validation] the doctor no longer validates repos{} in "
                "this tree, but the doc carries no 'does not validate' wording for it"
            )


def _mutate_doctor_validation(tmp_path_arg: Path) -> Path:
    dest = _materialize(REPO_ROOT, tmp_path_arg, [_CP_HOWTO_DOC, "scripts"])
    p = dest / "scripts" / "foundry-doctor.py"
    text = p.read_text(encoding="utf-8")
    original = '        _run("control-plane", check_control_plane, project_dir=project_dir),\n'
    if original not in text:
        raise MissingSourceError(
            "scripts/foundry-doctor.py: expected control-plane wiring line not found "
            "(mutation target moved)"
        )
    p.write_text(text.replace(original, ""), encoding="utf-8")
    return dest


# ---- 23. control-plane-target-repo-freeze ----------------------------------------------------
# Exercises foundry_contract.py's OWN contract-hash seam over a synthetic contract differing
# only in target_repo — derives whether contract_sha256 actually changes (i.e. target_repo lies
# inside the hash-covered contract-proper region, above the trailer sentinel).

_CP_TR_FREEZE_TEMPLATE = (
    b"spec_ref: specs/x.md\n"
    b'spec_sha256: "' + b"0" * 64 + b'"\n'
    b"target_repo: __TR__\n"
    b'scope:\n  allowed_paths: ["x/**"]\n'
    b"checkpoints:\n"
    b"  - ac_id: AC-X-1\n"
    b'    surface: "cli:test"\n'
    b'    locator: "echo hi"\n'
    b'    expect: {op: matches, value: "hi", baseline: pre-change}\n'
)


def _derive_target_repo_freeze(root: Path) -> bool:
    fc_path = root / "scripts" / "foundry_contract.py"
    mod = _import_module_from_path(fc_path, f"_doc_claims_fc_{uuid.uuid4().hex}")
    a = _CP_TR_FREEZE_TEMPLATE.replace(b"__TR__", b"app")
    b = _CP_TR_FREEZE_TEMPLATE.replace(b"__TR__", b"infra")
    return mod.contract_sha256_bytes(a) != mod.contract_sha256_bytes(b)


def _check_target_repo_freeze(root: Path) -> None:
    if not _derive_target_repo_freeze(root):
        raise AssertionError(
            "[control-plane-target-repo-freeze] contract_sha256 does not change when "
            "target_repo changes — target_repo has fallen outside the hash-covered "
            "contract-proper region"
        )
    section = _cp_enforced_section(root)
    m = re.search(r"\*\*target_repo freeze\*\*\s*—\s*([a-z-]+)", section, re.I)
    if not m or m.group(1).strip().lower() != "machine-enforced":
        raise AssertionError(
            "[control-plane-target-repo-freeze] the doc's enforced section does not classify "
            "'target_repo freeze' as machine-enforced"
        )


def _mutate_target_repo_freeze(tmp_path_arg: Path) -> Path:
    dest = _materialize(
        REPO_ROOT, tmp_path_arg,
        [_CP_HOWTO_DOC, "scripts/foundry_contract.py", "scripts/foundry_system_snapshot.py"],
    )
    p = dest / "scripts" / "foundry_contract.py"
    text = p.read_text(encoding="utf-8")
    # Pinned to the two EXECUTABLE lines, not the whole function. The target previously included
    # the docstring, so documenting the function moved the target and this control failed closed
    # with "mutation target moved" — correct behaviour, but it fires on prose edits that cannot
    # affect what is being controlled. Narrowing to the statements keeps the match exact (a real
    # change to how the hash is computed still moves it) without coupling the control to comments.
    original = (
        '    proper, _ = split_contract_bytes(raw)\n'
        '    return hashlib.sha256(canonicalize_proper(proper)).hexdigest()\n'
    )
    if original not in text:
        raise MissingSourceError(
            "scripts/foundry_contract.py: expected contract_sha256_bytes body not found "
            "(mutation target moved)"
        )
    mutated = (
        '    return hashlib.sha256(b"doc-claims-negative-control-constant").hexdigest()\n'
    )
    p.write_text(text.replace(original, mutated), encoding="utf-8")
    return dest


# ---------------------------------------------------------------------------
# COVERED_CLAIMS — the single registry (AC-DRT-4)
# ---------------------------------------------------------------------------

COVERED_CLAIMS = [
    {
        "claim_id": "skill-catalog-count",
        "doc": "README.md",
        "mutation_class": "add-unit",
        "tokens": frozenset({"61"}),
        "derive": _derive_skill_catalog_count,
        "check": _check_skill_catalog_count,
        "make_mutated": _mutate_skill_catalog_count,
    },
    {
        "claim_id": "agent-roster-terminology",
        "doc": "docs/TERMINOLOGY.md",
        "mutation_class": "rename-unit",
        "tokens": frozenset(),
        "derive": _derive_agent_roster,
        "check": _check_agent_roster,
        "make_mutated": _mutate_agent_roster,
    },
    {
        "claim_id": "doctor-probe-claims",
        "doc": "docs/QUICKSTART.md",
        "mutation_class": "rename-unit",
        "tokens": frozenset(),
        "derive": _derive_doctor_probe_ids,
        "check": _check_doctor_probe_claims,
        "make_mutated": _mutate_doctor_probe_claims,
    },
    {
        "claim_id": "verb-refs-readme",
        "doc": "README.md",
        "mutation_class": "rename-unit",
        "tokens": frozenset(),
        "derive": lambda root: _derive_verb_refs(root, "README.md"),
        "check": lambda root: _check_verb_refs(root, "README.md", "verb-refs-readme"),
        "make_mutated": lambda tmp_path_arg: _mutate_verb_refs(tmp_path_arg, "README.md"),
    },
    {
        "claim_id": "verb-refs-quickstart",
        "doc": "docs/QUICKSTART.md",
        "mutation_class": "rename-unit",
        "tokens": frozenset(),
        "derive": lambda root: _derive_verb_refs(root, "docs/QUICKSTART.md"),
        "check": lambda root: _check_verb_refs(root, "docs/QUICKSTART.md", "verb-refs-quickstart"),
        "make_mutated": lambda tmp_path_arg: _mutate_verb_refs(tmp_path_arg, "docs/QUICKSTART.md"),
    },
    {
        "claim_id": "gate-tier-label",
        "doc": "README.md",
        "mutation_class": "rename-unit",
        "tokens": frozenset(),
        "derive": _derive_gate_tier,
        "check": _check_gate_tier,
        "make_mutated": _mutate_gate_tier,
    },
    {
        "claim_id": "test-count-band",
        "doc": "README.md",
        "mutation_class": "remove-unit",
        "tokens": frozenset({"1000"}),
        "derive": _derive_test_count,
        "check": _check_test_count,
        "make_mutated": _mutate_test_count,
    },
    {
        "claim_id": "documented-paths-stack-profiles",
        "doc": "docs/QUICKSTART.md",
        "mutation_class": "remove-unit",
        "tokens": frozenset(),
        "derive": _derive_stack_profiles,
        "check": _check_stack_profiles,
        "make_mutated": _mutate_stack_profiles,
    },
    {
        "claim_id": "documented-paths-git-discipline-hook",
        "doc": "docs/merge-floor.md",
        "mutation_class": "remove-unit",
        "tokens": frozenset(),
        "derive": _derive_git_discipline_hook_present,
        "check": _check_git_discipline_hook_path,
        "make_mutated": _mutate_git_discipline_hook_path,
    },
    {
        "claim_id": "node-version-quickstart",
        "doc": "docs/QUICKSTART.md",
        "mutation_class": "rename-unit",
        "tokens": frozenset({"22"}),
        "derive": _derive_node_version,
        "check": _check_node_version,
        "make_mutated": _mutate_node_version,
    },
    {
        "claim_id": "spec-size-ceiling-quickstart",
        "doc": "docs/QUICKSTART.md",
        "mutation_class": "remove-unit",
        "tokens": frozenset({"14", "8,000"}),
        "derive": _derive_spec_size_ceiling,
        "check": _check_spec_size_ceiling,
        "make_mutated": _mutate_spec_size_ceiling,
    },
    # Two claims retired in the pre-v1 content pass ("subtraction-check-cli-count",
    # "subtraction-doctor-loc"): both derived their ground truth from `git show v0.23.0:…`,
    # which a zero-history public tree cannot satisfy, and both existed to keep a historical
    # narrative accurate that the shipped docs no longer tell. Their doc claims were removed
    # from CONTRIBUTING.md in the same pass.
    {
        "claim_id": "terminology-section-refs",
        "doc": "docs/TERMINOLOGY.md",
        "mutation_class": "rename-unit",
        "tokens": frozenset({"5", "3"}),
        "derive": _derive_terminology_headings,
        "check": _check_terminology_section_refs,
        "make_mutated": _mutate_terminology_section_refs,
    },
    {
        "claim_id": "terminology-illustrative-markers",
        "doc": "docs/TERMINOLOGY.md",
        "mutation_class": "rename-unit",
        "tokens": frozenset({"1", "2", "51"}),
        "derive": _derive_terminology_markers,
        "check": _check_terminology_markers,
        "make_mutated": _mutate_terminology_markers,
    },
    {
        "claim_id": "architecture-illustrative-marker",
        "doc": "docs/architecture.md",
        "mutation_class": "remove-unit",
        "tokens": frozenset({"0"}),
        "derive": _derive_architecture_marker,
        "check": _check_architecture_marker,
        "make_mutated": _mutate_architecture_marker,
    },
    {
        "claim_id": "comparison-year-marker",
        "doc": "docs/comparison.md",
        "mutation_class": "remove-unit",
        "tokens": frozenset({"2026"}),
        "derive": _derive_comparison_marker,
        "check": _check_comparison_marker,
        "make_mutated": _mutate_comparison_marker,
    },
    {
        "claim_id": "merge-floor-quickstart-step-ref",
        "doc": "docs/merge-floor.md",
        "mutation_class": "rename-unit",
        "tokens": frozenset({"1"}),
        "derive": _derive_quickstart_steps,
        "check": _check_merge_floor_step_ref,
        "make_mutated": _mutate_merge_floor_step_ref,
    },
    {
        "claim_id": "bootstrap-emitted-refs",
        "doc": "docs/identity-isolation.md",
        "mutation_class": "remove-unit",
        "tokens": frozenset(),
        "derive": _derive_bootstrap_emitted_refs,
        "check": _check_bootstrap_emitted_refs,
        "make_mutated": _mutate_bootstrap_emitted_refs,
    },
    {
        "claim_id": "control-plane-dispatch-enforcement",
        "doc": _CP_HOWTO_DOC,
        "mutation_class": "remove-unit",
        "tokens": frozenset(),
        "derive": _derive_dispatch_enforcement,
        "check": _check_dispatch_enforcement,
        "make_mutated": _mutate_dispatch_enforcement,
    },
    {
        "claim_id": "control-plane-authorize-degrade",
        "doc": _CP_HOWTO_DOC,
        "mutation_class": "remove-unit",
        "tokens": frozenset(),
        "derive": _derive_authorize_degrade,
        "check": _check_authorize_degrade,
        "make_mutated": _mutate_authorize_degrade,
    },
    {
        "claim_id": "control-plane-doctor-validation",
        "doc": _CP_HOWTO_DOC,
        "mutation_class": "remove-unit",
        "tokens": frozenset(),
        "derive": _derive_doctor_validation,
        "check": _check_doctor_validation,
        "make_mutated": _mutate_doctor_validation,
    },
    {
        "claim_id": "control-plane-target-repo-freeze",
        "doc": _CP_HOWTO_DOC,
        "mutation_class": "remove-unit",
        "tokens": frozenset(),
        "derive": _derive_target_repo_freeze,
        "check": _check_target_repo_freeze,
        "make_mutated": _mutate_target_repo_freeze,
    },
]

COVERED_CLAIMS_BY_ID = {e["claim_id"]: e for e in COVERED_CLAIMS}


# ---------------------------------------------------------------------------
# Tests (names bound by the contract — spec "Design / notes")
# ---------------------------------------------------------------------------

def test_module_collected_by_default_suite():
    """AC-DRT-1: this module lives in tests/, requiring no new workflow/job/step. The contract's
    own checkpoint independently confirms collection via `pytest --collect-only`."""
    assert Path(__file__).parent.name == "tests"
    assert Path(__file__).name == "test_doc_claims.py"


# AC-DRT-2: forbidden substrings are built via concatenation so the literal text never appears
# verbatim in this module's own source (which would trip this very test on itself).
_SKIP_MARK = "pytest" + ".mark." + "skip"
_XFAIL_MARK = "pytest" + ".mark." + "xfail"
_SKIP_CALL = "pytest" + "." + "skip("
_XFAIL_CALL = "pytest" + "." + "xfail("
_WARN_CALL = "warnings" + "." + "warn("
_ENV_ESCAPE = "os" + ".environ.get(\"FOUNDRY_DOC_CLAIMS"


def test_no_warn_only_escape():
    source = Path(__file__).read_text(encoding="utf-8")
    forbidden = [_SKIP_MARK, _XFAIL_MARK, _SKIP_CALL, _XFAIL_CALL, _WARN_CALL, _ENV_ESCAPE]
    hits = [f for f in forbidden if f in source]
    assert not hits, f"warn-only/skip escape substring(s) found in module source: {hits}"


def test_missing_source_fails_closed(tmp_path):
    doc_missing_root = _materialize(REPO_ROOT, tmp_path / "doc-missing", ["skills"])
    with pytest.raises(MissingSourceError, match="README.md"):
        read_doc_text(doc_missing_root, "README.md")

    source_missing_root = _materialize(REPO_ROOT, tmp_path / "source-missing", ["README.md"])
    with pytest.raises(MissingSourceError, match="skills"):
        COVERED_CLAIMS_BY_ID["skill-catalog-count"]["derive"](source_missing_root)


def test_registry_is_single_source():
    assert isinstance(COVERED_CLAIMS, list) and len(COVERED_CLAIMS) >= 14
    ids = [e["claim_id"] for e in COVERED_CLAIMS]
    assert len(ids) == len(set(ids)), "duplicate claim_id in COVERED_CLAIMS"
    required_keys = {"claim_id", "doc", "mutation_class", "tokens", "derive", "check", "make_mutated"}
    for entry in COVERED_CLAIMS:
        assert required_keys <= set(entry), f"{entry.get('claim_id')} missing required key(s)"
        assert entry["mutation_class"] in {"add-unit", "remove-unit", "rename-unit"}
        assert callable(entry["derive"]) and callable(entry["check"]) and callable(entry["make_mutated"])


def test_derivations_are_live(tmp_path):
    failures = []
    for entry in COVERED_CLAIMS:
        claim_id = entry["claim_id"]
        try:
            original = entry["derive"](REPO_ROOT)
        except MissingSourceError as exc:
            failures.append(f"{claim_id}: derive(real root) raised {exc}")
            continue
        mutated_root = entry["make_mutated"](tmp_path / claim_id)
        mutated = entry["derive"](mutated_root)
        if mutated == original:
            failures.append(
                f"{claim_id}: derive() unchanged after its declared {entry['mutation_class']} "
                f"mutation ({original!r})"
            )
    assert not failures, "\n".join(failures)


def test_no_unclassified_count_bearing_claim():
    declared = {}
    for entry in COVERED_CLAIMS:
        declared.setdefault(entry["doc"], set()).update(entry["tokens"])
    failures = []
    for relpath in DOC_CORPUS:
        text = read_doc_text(REPO_ROOT, relpath)
        found = {tok for (_, tok, _) in find_count_bearing_tokens(text)}
        missing = found - declared.get(relpath, set())
        if missing:
            failures.append(f"{relpath}: unclassified count-bearing token(s) {sorted(missing)}")
    assert not failures, "\n".join(failures)


def test_exclusion_mapping_is_pinned():
    assert EXCLUDED_DOCS == EXPECTED_EXCLUSIONS, (
        f"EXCLUDED_DOCS drifted from its pinned EXPECTED_EXCLUSIONS copy: "
        f"{EXCLUDED_DOCS} != {EXPECTED_EXCLUSIONS}"
    )
    assert set(EXCLUDED_DOCS.values()) <= REASONS


# Pinned literal expected copy of the claim registry's id set (AC-BHT-12) — hand-synchronized, not
# derived from COVERED_CLAIMS — so adding or losing a claim is a visible two-place diff and an
# unregistered claim can no longer drop silently out of the parametrized sweeps.
EXPECTED_CLAIM_IDS = frozenset({
    "skill-catalog-count",
    "agent-roster-terminology",
    "doctor-probe-claims",
    "verb-refs-readme",
    "verb-refs-quickstart",
    "gate-tier-label",
    "test-count-band",
    "documented-paths-stack-profiles",
    "documented-paths-git-discipline-hook",
    "node-version-quickstart",
    "spec-size-ceiling-quickstart",
    "terminology-section-refs",
    "terminology-illustrative-markers",
    "architecture-illustrative-marker",
    "comparison-year-marker",
    "merge-floor-quickstart-step-ref",
    "bootstrap-emitted-refs",
    "control-plane-dispatch-enforcement",
    "control-plane-authorize-degrade",
    "control-plane-doctor-validation",
    "control-plane-target-repo-freeze",
})


def test_registry_roster_is_pinned():
    actual_ids = {e["claim_id"] for e in COVERED_CLAIMS}
    assert actual_ids == EXPECTED_CLAIM_IDS, (
        f"COVERED_CLAIMS roster drifted from its pinned EXPECTED_CLAIM_IDS copy: "
        f"{actual_ids} != {EXPECTED_CLAIM_IDS}"
    )
    assert "bootstrap-emitted-refs" in EXPECTED_CLAIM_IDS


def test_external_refs_registry():
    assert DECLARED_EXTERNAL_REFS == EXPECTED_EXTERNAL_REFS, (
        f"DECLARED_EXTERNAL_REFS drifted from its pinned EXPECTED_EXTERNAL_REFS copy: "
        f"{DECLARED_EXTERNAL_REFS} != {EXPECTED_EXTERNAL_REFS}"
    )
    assert set(DECLARED_EXTERNAL_REFS.values()) <= EXTERNAL_REF_REASONS


def test_emitted_refs_coverage_floor():
    derived = _all_bootstrap_emitted_references(REPO_ROOT)
    missing = EXPECTED_EMITTED_REF_FLOOR - derived
    assert not missing, (
        f"the extractor's derived reference set is missing pinned floor member(s) {missing} "
        f"(derived: {sorted(derived)})"
    )


def test_bootstrap_emitted_refs_claim():
    COVERED_CLAIMS_BY_ID["bootstrap-emitted-refs"]["check"](REPO_ROOT)


def test_prose_slash_denylist_is_pinned():
    assert _KNOWN_PROSE_SLASH_DENYLIST == _EXPECTED_PROSE_SLASH_DENYLIST, (
        f"_KNOWN_PROSE_SLASH_DENYLIST drifted from its pinned _EXPECTED_PROSE_SLASH_DENYLIST copy: "
        f"{_KNOWN_PROSE_SLASH_DENYLIST} != {_EXPECTED_PROSE_SLASH_DENYLIST}"
    )
    assert all(reason.strip() for reason in _KNOWN_PROSE_SLASH_DENYLIST.values()), (
        "every _KNOWN_PROSE_SLASH_DENYLIST entry must carry a non-empty reason"
    )


def test_absolute_ref_never_resolves_against_host_filesystem():
    """Risk-4 regression: an undeclared absolute-looking reference must fail closed, never
    silently resolve via pathlib's absolute-discards-root join semantics."""
    assert _resolves_in_tree(REPO_ROOT, "/etc/hostname") is False
    assert _resolves_in_tree(REPO_ROOT, "/usr/bin/env") is False


def test_skill_catalog_count_claim():
    COVERED_CLAIMS_BY_ID["skill-catalog-count"]["check"](REPO_ROOT)


def test_agent_roster_claims():
    COVERED_CLAIMS_BY_ID["agent-roster-terminology"]["check"](REPO_ROOT)


def test_doctor_probe_claims():
    COVERED_CLAIMS_BY_ID["doctor-probe-claims"]["check"](REPO_ROOT)


def test_verb_references_resolve():
    COVERED_CLAIMS_BY_ID["verb-refs-readme"]["check"](REPO_ROOT)
    COVERED_CLAIMS_BY_ID["verb-refs-quickstart"]["check"](REPO_ROOT)


def test_gate_workflow_tier_label():
    COVERED_CLAIMS_BY_ID["gate-tier-label"]["check"](REPO_ROOT)


def test_test_count_claim_within_band():
    COVERED_CLAIMS_BY_ID["test-count-band"]["check"](REPO_ROOT)


def test_documented_paths_exist():
    COVERED_CLAIMS_BY_ID["documented-paths-stack-profiles"]["check"](REPO_ROOT)
    COVERED_CLAIMS_BY_ID["documented-paths-git-discipline-hook"]["check"](REPO_ROOT)


# ---------------------------------------------------------------------------
# feat-foundry-control-plane-docs (AC-CPD-3) — named checkpoint tests
# ---------------------------------------------------------------------------

def test_control_plane_dispatch_enforcement():
    COVERED_CLAIMS_BY_ID["control-plane-dispatch-enforcement"]["check"](REPO_ROOT)


def test_control_plane_authorize_degrade():
    COVERED_CLAIMS_BY_ID["control-plane-authorize-degrade"]["check"](REPO_ROOT)


def test_control_plane_doctor_validation():
    COVERED_CLAIMS_BY_ID["control-plane-doctor-validation"]["check"](REPO_ROOT)


def test_control_plane_target_repo_freeze():
    COVERED_CLAIMS_BY_ID["control-plane-target-repo-freeze"]["check"](REPO_ROOT)


# The CLOSED roster (AC-CPD-3 chapeau): the labels parsed from the `enforced` section's own body
# must equal this pinned label->classification mapping EXACTLY, so a new qualitative overclaim
# cannot ship un-derived. Pinned against a hand-written expected copy (the DECLARED_EXTERNAL_REFS/
# EXPECTED_EXTERNAL_REFS two-place-diff idiom above). Values are CURRENT-REGIME (spec AC-CPD-3
# chapeau: "the roster's classifications are current-regime values") — feat-foundry-control-plane-
# preflight has already shipped (merge-order precondition, Clarifications), so
# 'doctor registry validation' and 'session-root rule' are pinned at their POST-flip values
# (AC-CPD-3(3)) rather than the pre-flip values the spec's own illustrative chapeau text shows.
_CP_ENFORCED_ROSTER = {
    "repo-key resolution": "machine-enforced",
    "dispatch bind-check": "machine-enforced",
    "target_repo freeze": "machine-enforced",
    "authorization venue floors": "not-enforced-today",
    "doctor registry validation": "not-enforced-today",
    "pairing rule": "practice",
    "clone-before-register ordering": "practice",
    "session-root rule": "not-enforced-today",
}

# Pinned literal expected copy, hand-synchronized (not derived from _CP_ENFORCED_ROSTER above).
_EXPECTED_CP_ENFORCED_ROSTER = {
    "repo-key resolution": "machine-enforced",
    "dispatch bind-check": "machine-enforced",
    "target_repo freeze": "machine-enforced",
    "authorization venue floors": "not-enforced-today",
    "doctor registry validation": "not-enforced-today",
    "pairing rule": "practice",
    "clone-before-register ordering": "practice",
    "session-root rule": "not-enforced-today",
}

_CP_CLASSIFICATIONS = frozenset({"machine-enforced", "not-enforced-today", "practice"})
_CP_LABEL_LIST_ITEM_RE = re.compile(r"^-\s+\*\*([^*]+)\*\*\s*—\s*([a-z-]+)\.", re.MULTILINE)


def _parse_cp_enforced_labels(section: str) -> dict:
    return {label.strip(): cls.strip() for label, cls in _CP_LABEL_LIST_ITEM_RE.findall(section)}


def test_control_plane_enforced_roster_is_closed():
    assert _CP_ENFORCED_ROSTER == _EXPECTED_CP_ENFORCED_ROSTER, (
        f"_CP_ENFORCED_ROSTER drifted from its pinned _EXPECTED_CP_ENFORCED_ROSTER copy: "
        f"{_CP_ENFORCED_ROSTER} != {_EXPECTED_CP_ENFORCED_ROSTER}"
    )
    assert set(_CP_ENFORCED_ROSTER.values()) <= _CP_CLASSIFICATIONS

    section = _cp_enforced_section(REPO_ROOT)
    parsed = _parse_cp_enforced_labels(section)
    assert parsed == _CP_ENFORCED_ROSTER, (
        f"labels parsed from the doc's enforced section do not equal the pinned roster exactly: "
        f"parsed={parsed} pinned={_CP_ENFORCED_ROSTER}"
    )


def test_control_plane_practice_labels_co_occur():
    # Security review 2026-08-02 (R6): assert on the PARSED roster item, never a bare substring
    # over the joined section (the vacuous shape this workspace has been burned by).
    section = _cp_enforced_section(REPO_ROOT)
    items = dict(re.findall(r"\*\*([^*]+)\*\* \u2014 ([a-z-]+)", section))
    practice = [label for label, tier in _CP_ENFORCED_ROSTER.items() if tier == "practice"]
    assert practice, "roster lost its practice labels"
    for label in practice:
        assert items.get(label) == "practice", (
            f"practice label {label!r} does not parse as tier 'practice' in the enforced section"
        )

@pytest.mark.parametrize("claim_id", [e["claim_id"] for e in COVERED_CLAIMS])
def test_negative_control_convicts_injected_drift(claim_id, tmp_path):
    entry = COVERED_CLAIMS_BY_ID[claim_id]
    mutated_root = entry["make_mutated"](tmp_path)
    with pytest.raises(AssertionError) as exc_info:
        entry["check"](mutated_root)
    assert claim_id in str(exc_info.value), (
        f"{claim_id}: negative-control failure message does not name the claim id: {exc_info.value}"
    )
