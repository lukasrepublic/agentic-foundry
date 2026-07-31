#!/usr/bin/env python3
"""foundry_project_tracking — the PURE, OFFLINE projection MODEL (PTM, ER #112 atom 1 of 3;
feat-foundry-project-tracking-projection-model).

ER #112 wants a native ONE-WAY projection of file-based atoms into a GitHub org Project — the spec
files stay the single source of truth, the Project is a *generated view* (like the derived
`specs/lifecycle/` view). This module is ONLY the pure, offline MODEL layer: read the adopter's
`.claude/foundry-project.json` `github_projects` block, and for each atom in the corpus derive its
**projected item** — the idempotency identity (a stable issue-body marker), the derived **Status**
(Draft / Authorized / In Progress · In Review / Done), and the derived fields **Area / Control /
Priority**. It performs NO network I/O: no `requests`/`urllib`/`http`/GraphQL client, no `import
socket`; every GitHub-event input (PR open/merged state) is *passed in* to a pure function. The model
never calls GitHub and never writes to a spec or contract file (AC-PTM-5, the read-only corpus floor).

The GraphQL projector CLI (PTC, `scripts/foundry-project-sync.py`) and the RTM report / doctor drop-in
/ PR-cut hook / CI workflow (PTR) are separate downstream atoms that IMPORT this model — they are out
of scope here (see the spec's "Out of scope / non-goals").

Config-gated, default OFF. The capability is inert unless `.claude/foundry-project.json` (resolved via
CLAUDE_PROJECT_DIR — the adopter corpus workspace) carries a `github_projects` block with `enabled:
true`. Absent config, or `enabled` anything other than `true`, => the model reports INACTIVE and
attempts no atom derivation (AC-PTM-1).

Threat model — TRUSTED OPERATOR (memory `staged-security-threat-model`). This atom reads config and
corpus files and returns derived data structures; it holds no token, opens no socket, mutates nothing.

  python scripts/foundry_project_tracking.py                 # print the projected corpus as JSON
  python scripts/foundry_project_tracking.py --root DIR       # override the resolved adopter root
  python scripts/foundry_project_tracking.py --selftest       # AC-PTM-1..6 (isolated temp fixtures)
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

import yaml

# ------------------------------------------------------------------------------------------------- #
# Root resolution — mirrors the repo's existing producer convention (foundry-distill._resolve_root):
# --root override -> CLAUDE_PROJECT_DIR -> `git --git-common-dir` parent -> cwd.
# ------------------------------------------------------------------------------------------------- #

def _resolve_root(root_arg=None):
    if root_arg:
        return os.path.abspath(root_arg)
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return os.path.abspath(env)
    try:
        cdir = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, check=False,
        ).stdout.strip()
        if cdir:
            return os.path.abspath(os.path.dirname(os.path.abspath(cdir)))
    except Exception:
        pass
    return os.getcwd()


# ------------------------------------------------------------------------------------------------- #
# AC-PTM-1 — config parse + INACTIVE gate.
# ------------------------------------------------------------------------------------------------- #

INACTIVE = {"active": False}


def read_config(root_arg=None):
    """Read `.claude/foundry-project.json`'s `github_projects` block (resolved via CLAUDE_PROJECT_DIR
    / --root, mirroring every other foundry-project.json consumer's resolution).

    Returns {"active": False, "reason": <str>} when the file is absent/unreadable/invalid, the
    `github_projects` block is absent, or its `enabled` is not exactly `True` — in ANY of those cases
    the caller MUST attempt no atom derivation (AC-PTM-1). Returns {"active": True, org,
    project_number, field_map, auth_env, issues_repo} otherwise. `issues_repo` is parsed + EXPOSED
    only (no default computed at the model layer — an absent value stays parseable/None; the
    "defaults to the adopter's primary repo" resolution, and the sub-issue hierarchy edges that depend
    on it, are PTC's downstream concern per the spec's Clarifications)."""
    root = _resolve_root(root_arg)
    path = os.path.join(root, ".claude", "foundry-project.json")
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except FileNotFoundError:
        return {"active": False, "reason": "no .claude/foundry-project.json"}
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        return {"active": False, "reason": f"foundry-project.json unreadable/invalid: {e}"}
    if not isinstance(doc, dict):
        return {"active": False, "reason": "foundry-project.json root is not an object"}
    gp = doc.get("github_projects")
    if not isinstance(gp, dict) or gp.get("enabled") is not True:
        return {"active": False, "reason": "github_projects block absent or enabled != true"}
    field_map = gp.get("field_map")
    return {
        "active": True,
        "org": gp.get("org"),
        "project_number": gp.get("project_number"),
        "field_map": field_map if isinstance(field_map, dict) else {},
        "auth_env": gp.get("auth_env"),
        "issues_repo": gp.get("issues_repo"),  # parse + expose only; absent -> None, still parseable
    }


# ------------------------------------------------------------------------------------------------- #
# AC-PTM-2 — stable idempotency marker.
# ------------------------------------------------------------------------------------------------- #

_MARKER_TMPL = "<!-- foundry-atom: {atom_id} -->"


def atom_id_of(spec_path):
    """The atom-id: the spec file's basename with the `.md` suffix removed."""
    base = os.path.basename(spec_path)
    return base[:-3] if base.endswith(".md") else base


def atom_marker(atom_id):
    """The stable idempotency identity: `<!-- foundry-atom: <atom-id> -->`. A pure function of
    `atom_id` only — re-deriving the same atom yields a byte-identical marker (AC-PTM-2); this is the
    sole source of atom<->issue identity (no side table)."""
    return _MARKER_TMPL.format(atom_id=atom_id)


# ------------------------------------------------------------------------------------------------- #
# AC-PTM-3 — Status union derivation (PURE: file-truth (in) union event-truth (passed IN)).
# ------------------------------------------------------------------------------------------------- #

STATUS_DRAFT = "Draft"
STATUS_AUTHORIZED = "Authorized"
STATUS_IN_PROGRESS_REVIEW = "In Progress · In Review"
STATUS_DONE = "Done"


def read_file_truth(contract_path):
    """file-truth reader (read-only): {"authorized": bool} — whether the sibling
    acceptance-contract.yaml carries a signed `authorized:` trailer (operator_id + auth_seq present).
    Absent/unreadable/malformed contract -> {"authorized": False} (never raises; the atom is simply
    Draft). This is I/O; `derive_status` itself stays PURE and never opens a file."""
    if not contract_path or not os.path.isfile(contract_path):
        return {"authorized": False}
    try:
        with open(contract_path, encoding="utf-8") as f:
            data = yaml.safe_load(f.read()) or {}
    except Exception:
        return {"authorized": False}
    trailer = data.get("authorized") if isinstance(data, dict) else None
    signed = (
        isinstance(trailer, dict)
        and bool(trailer.get("operator_id"))
        and trailer.get("auth_seq") is not None
    )
    return {"authorized": signed}


def derive_status(file_truth, event_truth=None):
    """PURE Status union: file_truth ∪ event_truth -> exactly one of the pinned table's four labels.
    Issues NO GitHub call, opens NO file — every input is a plain dict passed IN by the caller.

      Done                    <- event_truth.pr_merged is True (PR merged to the default branch)
      In Progress · In Review <- event_truth.pr_open is True (an open PR references the atom-issue)
      Authorized               <- file_truth.authorized is True, no open/merged PR
      Draft                    <- otherwise (spec exists, no signed `authorized:` trailer)
    """
    event_truth = event_truth or {}
    authorized = bool(file_truth.get("authorized")) if file_truth else False
    if event_truth.get("pr_merged"):
        return STATUS_DONE
    if event_truth.get("pr_open"):
        return STATUS_IN_PROGRESS_REVIEW
    if authorized:
        return STATUS_AUTHORIZED
    return STATUS_DRAFT


# ------------------------------------------------------------------------------------------------- #
# AC-PTM-4 — field derivation: Area <- <domain> path segment; Control <- checkpoints[].ac_id set;
# Priority/Class <- the spec's intent block IF a structured source exists, else UNSET (never invented).
# ------------------------------------------------------------------------------------------------- #

def derive_area(spec_path):
    """Area <- the `<domain>` path segment of `specs/features/<product>/<domain>/<capability>/
    feat-<...>.md`. Returns None when the path does not follow that convention (never guesses)."""
    parts = [p for p in re.split(r"[\\/]", spec_path) if p]
    try:
        idx = parts.index("features")
        return parts[idx + 2]
    except (ValueError, IndexError):
        return None


def derive_control(contract_path):
    """Control <- the sorted set of the sibling acceptance-contract's `checkpoints[].ac_id` (the
    covered AC-IDs — the ER's `closes:` traceability linkage). Read-only; absent/unreadable contract
    -> [] (never raises the corpus walk)."""
    if not contract_path or not os.path.isfile(contract_path):
        return []
    try:
        with open(contract_path, encoding="utf-8") as f:
            data = yaml.safe_load(f.read()) or {}
    except Exception:
        return []
    checkpoints = data.get("checkpoints") if isinstance(data, dict) else None
    ids = set()
    for cp in (checkpoints or []):
        if not isinstance(cp, dict):
            continue
        v = cp.get("ac_id")
        if isinstance(v, str):
            ids.add(v)
        elif isinstance(v, list):
            ids.update(x for x in v if isinstance(x, str))
    return sorted(ids)


# The "intent block" is the spec's leading human-readable-intent blockquote (the `> **Human-readable
# intent.**` lines directly under the H1). No spec today carries a structured priority/class key there
# (verified in-repo; see the spec's Resolved Clarification + Residuals) — so this ALWAYS returns None
# (UNSET) in v1. If a structured source key is defined later this function extends additively without
# renumbering AC-PTM-4; it never invents a value from prose.
_INTENT_KEY_RE = re.compile(r"(?im)^\s*>\s*\**\s*(priority|class)\s*\**\s*:\s*(\S.*?)\s*$")


def _intent_block(spec_text):
    lines = spec_text.splitlines()
    block = []
    started = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(">"):
            started = True
            block.append(line)
        elif started:
            break
    return "\n".join(block)


def derive_priority(spec_text):
    """Priority/Class <- a structured `priority:`/`class:` key inside the spec's intent block, if
    present; otherwise UNSET (None) — NEVER guessed/invented from free prose (Residuals: v1-UNSET)."""
    if not spec_text:
        return None
    m = _INTENT_KEY_RE.search(_intent_block(spec_text))
    return m.group(2).strip() if m else None


def project_item(spec_path, contract_path=None, file_truth=None, event_truth=None, spec_text=None):
    """Assemble one atom's role-keyed projected item (role-keyed field values; mapping those roles to
    a Project's concrete field IDs is PTC's concern, not this model's)."""
    atom_id = atom_id_of(spec_path)
    ft = file_truth if file_truth is not None else read_file_truth(contract_path)
    return {
        "atom_id": atom_id,
        "marker": atom_marker(atom_id),
        "status": derive_status(ft, event_truth),
        "fields": {
            "area": derive_area(spec_path),
            "control": derive_control(contract_path),
            "priority": derive_priority(spec_text) if spec_text is not None else None,
        },
    }


# ------------------------------------------------------------------------------------------------- #
# Corpus discovery + AC-PTM-6 graceful degradation (no release.yaml / specs/lifecycle/ required).
# ------------------------------------------------------------------------------------------------- #

def discover_atoms(root):
    """Walk `specs/features/**/feat-*.md`, pairing each spec with its sibling
    `acceptance-contract.yaml` (None when absent). Requires NEITHER `release.yaml` NOR
    `specs/lifecycle/` to exist — those are consulted by other views, not this discovery walk
    (AC-PTM-6: absent -> the atoms actually found are still projected, no error). Returns a
    deterministically sorted list of (spec_path, contract_path_or_None)."""
    specs_dir = os.path.join(root, "specs", "features")
    found = []
    if not os.path.isdir(specs_dir):
        return found
    for dirpath, dirnames, filenames in os.walk(specs_dir):
        dirnames.sort()
        for fn in sorted(filenames):
            if fn.startswith("feat-") and fn.endswith(".md"):
                spec_path = os.path.join(dirpath, fn)
                contract_path = os.path.join(dirpath, "acceptance-contract.yaml")
                found.append((spec_path, contract_path if os.path.isfile(contract_path) else None))
    found.sort(key=lambda t: t[0])
    return found


def project_corpus(root_arg=None, event_truth_by_atom=None):
    """The whole-corpus projection: {"active": False, "reason": ...} when INACTIVE (AC-PTM-1, no
    derivation attempted), else {"active": True, "items": [project_item(...), ...]}. Read-only: opens
    spec/contract files for reading ONLY, never writes (AC-PTM-5). Gracefully degrades when
    `release.yaml` / `specs/lifecycle/` are absent — it simply projects whatever `discover_atoms`
    finds (AC-PTM-6)."""
    cfg = read_config(root_arg)
    if not cfg.get("active"):
        return {"active": False, "reason": cfg.get("reason"), "items": []}
    root = _resolve_root(root_arg)
    event_truth_by_atom = event_truth_by_atom or {}
    items = []
    for spec_path, contract_path in discover_atoms(root):
        atom_id = atom_id_of(spec_path)
        try:
            with open(spec_path, encoding="utf-8") as f:
                spec_text = f.read()
        except Exception:
            spec_text = ""
        items.append(project_item(
            spec_path, contract_path,
            event_truth=event_truth_by_atom.get(atom_id),
            spec_text=spec_text,
        ))
    return {"active": True, "config": cfg, "items": items}


# ------------------------------------------------------------------------------------------------- #
# --selftest — AC-PTM-1..6 over throwaway temp/in-memory fixtures. Emits ONE
# `AC-PTM-N <label>: PASS` token per AC ONLY when its behavior actually computed PASS.
# ------------------------------------------------------------------------------------------------- #

_CONTRACT_TMPL = """spec_ref: {spec_ref}
spec_sha256: "{zeros}"
target_repo: agentic-foundry
scope:
  allowed_paths: []
checkpoints:
{checkpoints_yaml}
"""


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _checkpoints_yaml(ac_ids):
    lines = []
    for ac in ac_ids:
        lines.append(f'  - ac_id: {ac}\n    surface: "cli:x"\n    locator: "x"\n'
                     f'    expect: {{op: matches, value: "x", baseline: none}}')
    return "\n".join(lines)


def _mk_atom(root, product, domain, capability, atom_slug, ac_ids, authorized=False, intent_extra=""):
    """Materialize one fixture atom: spec + sibling acceptance-contract.yaml under
    specs/features/<product>/<domain>/<capability>/. Returns (spec_path, contract_path)."""
    d = os.path.join(root, "specs", "features", product, domain, capability)
    spec_path = os.path.join(d, f"feat-{atom_slug}.md")
    contract_path = os.path.join(d, "acceptance-contract.yaml")
    spec_text = (
        f"# {atom_slug} — fixture spec\n\n"
        f"> **Human-readable intent.** Fixture atom for the PTM selftest.\n"
        f"{intent_extra}\n\n"
        "<!-- normative -->\n## Acceptance criteria\n- **AC-X-1**: fixture.\n<!-- /normative -->\n"
    )
    _write(spec_path, spec_text)
    rel_spec = os.path.relpath(spec_path, root)
    contract_text = _CONTRACT_TMPL.format(
        spec_ref=rel_spec, zeros="0" * 64, checkpoints_yaml=_checkpoints_yaml(ac_ids),
    )
    if authorized:
        contract_text += (
            "# === FOUNDRY-AUTHORIZED-TRAILER (excluded from contract_sha256) ===\n"
            "authorized:\n  operator_id: op_test\n  authorized_at: 2026-07-07T00:00:00Z\n"
            "  auth_seq: 1\n  supersedes: null\n  spec_sha256: \"" + ("1" * 64) + "\"\n"
            "  contract_sha256: \"" + ("2" * 64) + "\"\n  merge_autonomy_mode: lean\n"
        )
    _write(contract_path, contract_text)
    return spec_path, contract_path


def _emit(token, label, ok):
    print(f"AC-PTM-{token} {label}: {'PASS' if ok else 'FAIL'}")
    return ok


def _selftest_ptm1():
    """AC-PTM-1: absent config, disabled config, and a fully-populated enabled config (incl.
    issues_repo absent-still-parseable)."""
    ok = True
    tmp = tempfile.mkdtemp(prefix="ptm-1-")
    try:
        # (a) no .claude/foundry-project.json at all -> INACTIVE.
        cfg_absent = read_config(tmp)
        ok = ok and cfg_absent.get("active") is False

        # (b) present but enabled:false -> INACTIVE, and project_corpus derives NOTHING.
        _mk_atom(tmp, "foundry", "some-domain", "some-cap", "disabled-atom", ["AC-X-1"])
        _write(os.path.join(tmp, ".claude", "foundry-project.json"),
               json.dumps({"github_projects": {"enabled": False, "org": "acme"}}))
        cfg_disabled = read_config(tmp)
        corpus_disabled = project_corpus(tmp)
        ok = ok and cfg_disabled.get("active") is False
        ok = ok and corpus_disabled.get("active") is False and corpus_disabled.get("items") == []

        # (c) enabled:true, full field set, issues_repo ABSENT (still parseable -> None, not an error).
        _write(os.path.join(tmp, ".claude", "foundry-project.json"), json.dumps({
            "github_projects": {
                "enabled": True, "org": "acme", "project_number": 7,
                "field_map": {"status": "Status", "area": "Area"},
                "auth_env": "FOUNDRY_PROJECT_TOKEN",
            }
        }))
        cfg_active = read_config(tmp)
        ok = ok and cfg_active.get("active") is True
        ok = ok and cfg_active.get("org") == "acme" and cfg_active.get("project_number") == 7
        ok = ok and cfg_active.get("field_map") == {"status": "Status", "area": "Area"}
        ok = ok and cfg_active.get("auth_env") == "FOUNDRY_PROJECT_TOKEN"
        ok = ok and cfg_active.get("issues_repo") is None  # absent stays parseable, no synthesized value

        # (d) issues_repo PRESENT -> parsed + exposed verbatim.
        _write(os.path.join(tmp, ".claude", "foundry-project.json"), json.dumps({
            "github_projects": {"enabled": True, "issues_repo": "acme/widgets"}
        }))
        ok = ok and read_config(tmp).get("issues_repo") == "acme/widgets"
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return ok


def _selftest_ptm2():
    """AC-PTM-2: the marker is a pure function of atom_id — byte-identical on re-derivation, distinct
    per distinct atom, and matches the pinned template for the real ER #112 spec basename."""
    ok = True
    real_id = "feat-foundry-project-tracking-projection-model"
    ok = ok and atom_marker(real_id) == "<!-- foundry-atom: feat-foundry-project-tracking-projection-model -->"
    # re-derivation is byte-identical.
    ok = ok and atom_marker(real_id) == atom_marker(real_id)
    # atom_id_of strips exactly the .md suffix from the basename (path-independent).
    ok = ok and atom_id_of("/a/b/c/feat-foo-bar.md") == "feat-foo-bar"
    ok = ok and atom_marker(atom_id_of("/a/b/c/feat-foo-bar.md")) == "<!-- foundry-atom: feat-foo-bar -->"
    # distinct atoms -> distinct markers.
    ok = ok and atom_marker("feat-a") != atom_marker("feat-b")
    return ok


def _selftest_ptm3():
    """AC-PTM-3: the pinned Draft / Authorized / In Progress · In Review / Done table, driven purely
    by file-truth (read from a fixture acceptance-contract.yaml) unioned with passed-IN event-truth —
    the function itself issues no GitHub call."""
    ok = True
    tmp = tempfile.mkdtemp(prefix="ptm-3-")
    try:
        # distinct capability dirs: one capability dir <-> one sibling acceptance-contract.yaml
        # (the real repo convention) — colliding them would make the second write clobber the first.
        _, draft_contract = _mk_atom(tmp, "foundry", "d", "c-draft", "draft-atom", ["AC-X-1"], authorized=False)
        _, auth_contract = _mk_atom(tmp, "foundry", "d", "c-auth", "auth-atom", ["AC-X-1"], authorized=True)

        draft_ft = read_file_truth(draft_contract)
        auth_ft = read_file_truth(auth_contract)
        ok = ok and draft_ft == {"authorized": False}
        ok = ok and auth_ft == {"authorized": True}

        # Draft: spec exists, no signed trailer, no PR event.
        ok = ok and derive_status(draft_ft, {}) == STATUS_DRAFT
        # Authorized: signed trailer, no open/merged PR.
        ok = ok and derive_status(auth_ft, {}) == STATUS_AUTHORIZED
        ok = ok and derive_status(auth_ft, {"pr_open": False, "pr_merged": False}) == STATUS_AUTHORIZED
        # In Progress · In Review: an open PR references the atom-issue (scenario in the spec).
        ok = ok and derive_status(auth_ft, {"pr_open": True}) == STATUS_IN_PROGRESS_REVIEW
        # Done: PR merged to the default branch.
        ok = ok and derive_status(auth_ft, {"pr_merged": True}) == STATUS_DONE
        # merged dominates a stale pr_open=True (a merged PR is no longer "open").
        ok = ok and derive_status(auth_ft, {"pr_open": True, "pr_merged": True}) == STATUS_DONE
        # a Draft atom with an (impossible in practice, but defensively tested) open-PR event still
        # unions toward the event-truth per the pinned table (event-truth dominates file-truth).
        ok = ok and derive_status(draft_ft, {"pr_open": True}) == STATUS_IN_PROGRESS_REVIEW

        # anti-tautology: the four labels are pairwise distinct (a degenerate always-same-string
        # implementation cannot pass this).
        labels = {STATUS_DRAFT, STATUS_AUTHORIZED, STATUS_IN_PROGRESS_REVIEW, STATUS_DONE}
        ok = ok and len(labels) == 4
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return ok


def _selftest_ptm4():
    """AC-PTM-4: Area <- <domain> segment; Control <- checkpoints[].ac_id set; Priority/Class <-
    UNSET absent a structured intent-block source (v1; never invented)."""
    ok = True
    tmp = tempfile.mkdtemp(prefix="ptm-4-")
    try:
        spec_path, contract_path = _mk_atom(
            tmp, "foundry", "project-tracking", "projection-model", "widget-atom",
            ["AC-W-2", "AC-W-1", "AC-W-1"],  # duplicate + unsorted input -> sorted DEDUPED set expected
        )
        ok = ok and derive_area(spec_path) == "project-tracking"
        ok = ok and derive_control(contract_path) == ["AC-W-1", "AC-W-2"]

        # a spec path that doesn't follow the specs/features/<product>/<domain>/... convention ->
        # Area is None (never guessed).
        ok = ok and derive_area("/tmp/not-a-spec-tree/foo.md") is None

        # an absent contract -> Control is [] (never raises the corpus walk).
        ok = ok and derive_control(None) == []
        ok = ok and derive_control(os.path.join(tmp, "does-not-exist.yaml")) == []

        # Priority/Class: no structured source in today's specs -> UNSET (None), never invented.
        with open(spec_path, encoding="utf-8") as f:
            spec_text = f.read()
        ok = ok and derive_priority(spec_text) is None

        # a fixture WITH a structured `priority:` key inside the intent blockquote DOES surface it
        # (proves the extractor is not a vacuous always-None stub). Distinct capability dir — a
        # capability dir <-> one sibling acceptance-contract.yaml, colliding would clobber widget-atom's.
        spec_path2, _ = _mk_atom(
            tmp, "foundry", "project-tracking", "priority-model", "priority-atom",
            ["AC-W-1"], intent_extra="> priority: P1",
        )
        with open(spec_path2, encoding="utf-8") as f:
            spec_text2 = f.read()
        ok = ok and derive_priority(spec_text2) == "P1"

        # project_item assembles the role-keyed fields end-to-end.
        item = project_item(spec_path, contract_path, spec_text=spec_text)
        ok = ok and item["fields"]["area"] == "project-tracking"
        ok = ok and item["fields"]["control"] == ["AC-W-1", "AC-W-2"]
        ok = ok and item["fields"]["priority"] is None
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return ok


def _selftest_ptm5():
    """AC-PTM-5: the read-only corpus floor — a full project_corpus() derivation run over a fixture
    corpus leaves EVERY spec/contract file byte-unchanged (and creates no new file in the tree)."""
    ok = True
    tmp = tempfile.mkdtemp(prefix="ptm-5-")
    try:
        spec_path, contract_path = _mk_atom(
            tmp, "foundry", "ro-domain", "ro-cap", "readonly-atom", ["AC-R-1"], authorized=True,
        )
        _write(os.path.join(tmp, ".claude", "foundry-project.json"),
               json.dumps({"github_projects": {"enabled": True, "org": "acme"}}))

        def _snapshot(root):
            snap = {}
            for dirpath, _dirnames, filenames in os.walk(root):
                for fn in filenames:
                    p = os.path.join(dirpath, fn)
                    with open(p, "rb") as f:
                        snap[p] = f.read()
            return snap

        before = _snapshot(tmp)
        out = project_corpus(tmp, event_truth_by_atom={"feat-readonly-atom": {"pr_open": True}})
        after = _snapshot(tmp)

        ok = ok and out.get("active") is True and len(out.get("items", [])) == 1
        ok = ok and before == after                 # byte-identical: no write, no new/removed file
        # explicit spot-check on the two atom files (redundant with the snapshot, but a direct oracle).
        with open(spec_path, "rb") as f:
            ok = ok and f.read() == before[spec_path]
        with open(contract_path, "rb") as f:
            ok = ok and f.read() == before[contract_path]
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return ok


def _selftest_ptm6():
    """AC-PTM-6: graceful degradation — no release.yaml, no specs/lifecycle/ view anywhere in the
    fixture root; the model still projects the atoms it discovers and returns with no error."""
    ok = True
    tmp = tempfile.mkdtemp(prefix="ptm-6-")
    try:
        _mk_atom(tmp, "foundry", "deg-domain", "deg-cap", "degrade-atom-1", ["AC-D-1"])
        _mk_atom(tmp, "foundry", "deg-domain", "deg-cap2", "degrade-atom-2", ["AC-D-2"], authorized=True)
        _write(os.path.join(tmp, ".claude", "foundry-project.json"),
               json.dumps({"github_projects": {"enabled": True}}))

        # anti-dormancy: assert release.yaml / specs/lifecycle really are absent from this fixture (a
        # vacuously-true test would silently pass even with a broken degrade guard).
        ok = ok and not os.path.isfile(os.path.join(tmp, "release.yaml"))
        ok = ok and not os.path.isdir(os.path.join(tmp, "specs", "lifecycle"))

        try:
            out = project_corpus(tmp)
            raised = False
        except Exception:
            out = None
            raised = True
        ok = ok and not raised
        ok = ok and out is not None and out.get("active") is True
        ok = ok and len(out.get("items", [])) == 2
        ids = {it["atom_id"] for it in out["items"]}
        ok = ok and ids == {"feat-degrade-atom-1", "feat-degrade-atom-2"}
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return ok


def selftest():
    results = [
        _emit("1", "config-parse-inactive-gate", _selftest_ptm1()),
        _emit("2", "idempotent-atom-marker", _selftest_ptm2()),
        _emit("3", "status-union-derivation", _selftest_ptm3()),
        _emit("4", "field-derivation-area-control", _selftest_ptm4()),
        _emit("5", "readonly-corpus-floor", _selftest_ptm5()),
        _emit("6", "graceful-degrade-no-lifecycle", _selftest_ptm6()),
    ]
    return 0 if all(results) else 1


def main():
    ap = argparse.ArgumentParser(
        description="foundry_project_tracking — pure offline projection model (PTM, ER #112 atom 1 of 3)"
    )
    ap.add_argument("--root", help="override the resolved adopter root (default: CLAUDE_PROJECT_DIR)")
    ap.add_argument("--selftest", action="store_true", help="run AC-PTM-1..6 over temp fixtures")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    out = project_corpus(args.root)
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
