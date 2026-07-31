#!/usr/bin/env python3
"""foundry_release — release shaping (RA) + evidence-derived closure (RD)  (Phase 2a, release-manager).

A release is foundry's default operating unit. Foundry ships the middle of the lifecycle (RB gate-in
`authorize-release` + RC wave `release-wave`/`mode-autonomous`); this module adds the ends:
  * RA shaping — a declarative `release.yaml` manifest (atom set + dependency DAG) + a
    `backlog → planned → active → completed` state machine whose forward transitions have RE-DERIVED
    preconditions (never taken on assertion).
  * RD closure — `active → completed` is **refused on operator assertion** and re-derived, fail-closed,
    from PRIMARY artifacts: an atom is CLOSED iff AUTHORIZED (recompute-match) AND MERGED-on-main.

The load-bearing principle (v1.1, post-§8): **`merged-on-main` subsumes the per-atom floor.** An atom cannot
reach the target repo's `main` without having passed the merge gate, which already ran CHECK 7 (the live-seam
walk verdict) at full strength — so closure does NOT re-implement a (weaker) walk check. CLOSED is re-derived
from (a) `foundry_authz.is_authorized` (cryptographic recompute-match of the live contract) and (b) the atom's
`contract_sha256` appearing in the target repo's branch history (content-based → squash/rebase-safe; bound to
the authorized contract, not an arbitrary commit).

Threat model — TRUSTED OPERATOR (memory `staged-security-threat-model`). Manifest validation is a mechanical
mistake-catcher; closure catches an honest "I think it's done" when an atom never actually merged. An operator
who deliberately forges git history / merges a decoy is the trusted root sabotaging themselves (out of model).
No signing/anti-tamper. Read-only over the floor: closure READS authorization + git state; it never issues or
modifies authorization, the merge gate, or provenance.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(HERE)

_SLUG = re.compile(r"^[a-z0-9-]+$")
STATES = ["backlog", "planned", "active", "completed"]
_LEGAL = {"backlog": "planned", "planned": "active", "active": "completed"}
_TOP_REQUIRED_FIELDS = {"id", "description", "state", "atoms"}
# (feat-*-certification): ADDITIVE optional top-level field — see `ACCEPT_VERDICTS`/
# `append_acceptance` below. Never required, never touched by the RA/RD state machine.
_TOP_OPTIONAL_FIELDS = {"acceptance"}
_ATOM_FIELDS = {"id", "spec_ref", "contract_ref", "depends_on"}
# (feat-foundry-wave-plan): ADDITIVE optional atom fields consumed by
# `scripts/foundry-wave-plan.py` — never required, never touched by the RA/RD state machine
# above. `paths` is the atom's declared write-path scope (globset strings, same shape as an
# acceptance-contract's `scope.allowed_paths`); `journeys` is a list of journey/AC tags. Absent
# on an atom == `[]` (backward-compatible with every manifest authored before this extension).
_ATOM_OPTIONAL_FIELDS = {"paths", "journeys"}

# (feat-*-certification, AC per charter 2026-07-27-phase5-certification): a CLOSED,
# 2-value, BOTH-TERMINAL verdict enum for the operator's own acceptance record — "refuses
# non-terminal" means exactly this: there is no `pending`/`in-review`/other in-between value to
# refuse into, only the two terminal dispositions.
ACCEPT_VERDICTS = {"accepted", "rejected"}
_ACCEPTANCE_FIELDS = {"date", "operator", "verdict", "note"}


class ReleaseError(Exception):
    """Fail-closed schema/loader/transition error — a malformed manifest, an illegal transition, or a
    refused closure raises this (never a silent partial load / silent state change)."""


class Atom:
    def __init__(self, id, spec_ref, contract_ref, depends_on, paths=None, journeys=None):
        self.id = id
        self.spec_ref = spec_ref
        self.contract_ref = contract_ref
        self.depends_on = depends_on
        self.paths = list(paths or [])          # declared write-path scope (optional)
        self.journeys = list(journeys or [])     # journey/AC tags (optional)


class Release:
    def __init__(self, id, description, state, atoms, order, acceptance=None):
        self.id = id
        self.description = description
        self.state = state
        self.atoms = atoms                       # list[Atom] in manifest order
        self.order = order                       # list[str] topologically sorted atom ids (wave order)
        self.by_id = {a.id: a for a in atoms}
        # the optional PRACTICE acceptance log — list[{date, operator, verdict, note}],
        # oldest first, never touched by the RA/RD state machine (see `append_acceptance`).
        self.acceptance = list(acceptance or [])

    def __repr__(self):
        return f"<Release {self.id} state={self.state} atoms={len(self.atoms)}>"


def _project_dir(project_dir=None):
    return project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def release_path(id, project_dir=None):
    return os.path.join(_project_dir(project_dir), ".foundry", "releases", id, "release.yaml")


# ── schema + dependency graph ────────────────────────────────────────────────────────────────────────

def _toposort(atoms):
    """Kahn topological sort over the depends_on edges (the RC wave execution order); raises ReleaseError
    on a cycle or a dangling edge."""
    ids = {a.id for a in atoms}
    for a in atoms:
        for d in a.depends_on:
            if d not in ids:
                raise ReleaseError(f"atom {a.id!r}: depends_on references unknown atom {d!r}")
    indeg = {a.id: 0 for a in atoms}
    adj = {a.id: [] for a in atoms}
    for a in atoms:
        for d in a.depends_on:                   # edge d -> a (d before a)
            adj[d].append(a.id)
            indeg[a.id] += 1
    queue = sorted([i for i, n in indeg.items() if n == 0])
    order = []
    while queue:
        n = queue.pop(0)
        order.append(n)
        for m in sorted(adj[n]):
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
        queue.sort()
    if len(order) != len(atoms):
        cyclic = sorted(set(indeg) - set(order))
        raise ReleaseError(f"release dependency graph has a cycle among atoms {cyclic}")
    return order


def _validate_acceptance(rid, raw):
    """validate the OPTIONAL top-level `acceptance:` list, fail-closed. Each entry is a
    CLOSED-field record {date, operator, verdict, note} — exactly those four keys, `verdict` in the
    closed 2-value terminal enum (`ACCEPT_VERDICTS`). This is a PRACTICE record (never a gate): no
    entry here participates in `transition`/`derive_closure`/any floor primitive."""
    if not isinstance(raw, list):
        raise ReleaseError(f"release {rid!r}: `acceptance` must be a list")
    out = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ReleaseError(f"release {rid!r}: acceptance[{i}] must be a mapping")
        ekeys = set(entry.keys())
        if ekeys != _ACCEPTANCE_FIELDS:
            raise ReleaseError(f"release {rid!r}: acceptance[{i}] fields must be exactly "
                               f"{sorted(_ACCEPTANCE_FIELDS)}, got {sorted(ekeys)}")
        if not isinstance(entry["date"], str) or not entry["date"].strip():
            raise ReleaseError(f"release {rid!r}: acceptance[{i}].date must be a non-empty string")
        if not isinstance(entry["operator"], str) or not entry["operator"].strip():
            raise ReleaseError(f"release {rid!r}: acceptance[{i}].operator must be a non-empty string")
        if entry["verdict"] not in ACCEPT_VERDICTS:
            raise ReleaseError(f"release {rid!r}: acceptance[{i}].verdict {entry['verdict']!r} is not "
                               f"terminal — must be one of {sorted(ACCEPT_VERDICTS)}")
        if not isinstance(entry["note"], str):
            raise ReleaseError(f"release {rid!r}: acceptance[{i}].note must be a string")
        out.append(dict(entry))
    return out


def _validate(doc, expected_id):
    if not isinstance(doc, dict):
        raise ReleaseError(f"release {expected_id!r}: top-level document is not a mapping — fail-closed")
    keys = set(doc.keys())
    missing = _TOP_REQUIRED_FIELDS - keys
    if missing:
        raise ReleaseError(f"release {expected_id!r}: missing required field(s) {sorted(missing)}")
    unknown = keys - _TOP_REQUIRED_FIELDS - _TOP_OPTIONAL_FIELDS
    if unknown:
        raise ReleaseError(f"release {expected_id!r}: unknown field(s) {sorted(unknown)} "
                           f"(allowed required: {sorted(_TOP_REQUIRED_FIELDS)}, optional: "
                           f"{sorted(_TOP_OPTIONAL_FIELDS)}; there is NO floor-gate field — the floor "
                           f"is core-owned)")
    rid = doc["id"]
    if not isinstance(rid, str) or not _SLUG.match(rid):
        raise ReleaseError(f"release {expected_id!r}: `id` {rid!r} is not a [a-z0-9-]+ slug")
    if expected_id is not None and rid != expected_id:
        raise ReleaseError(f"release id {rid!r} does not match its directory {expected_id!r}")
    if not isinstance(doc["description"], str) or not doc["description"].strip():
        raise ReleaseError(f"release {rid!r}: `description` must be a non-empty string")
    state = doc["state"]
    if state not in STATES:
        raise ReleaseError(f"release {rid!r}: `state` {state!r} not in {STATES}")
    atoms_raw = doc["atoms"]
    if not isinstance(atoms_raw, list) or not atoms_raw:
        raise ReleaseError(f"release {rid!r}: `atoms` must be a non-empty list")

    atoms, seen = [], set()
    for raw in atoms_raw:
        if not isinstance(raw, dict):
            raise ReleaseError(f"release {rid!r}: each atom must be a mapping, got {type(raw).__name__}")
        akeys = set(raw.keys())
        amiss = _ATOM_FIELDS - akeys
        if amiss:
            raise ReleaseError(f"release {rid!r}: atom {raw.get('id')!r} missing field(s) {sorted(amiss)}")
        aunknown = akeys - _ATOM_FIELDS - _ATOM_OPTIONAL_FIELDS
        if aunknown:
            raise ReleaseError(f"release {rid!r}: atom {raw.get('id')!r} unknown field(s) {sorted(aunknown)}")
        aid = raw["id"]
        if not isinstance(aid, str) or not _SLUG.match(aid):
            raise ReleaseError(f"release {rid!r}: atom id {aid!r} is not a [a-z0-9-]+ slug")
        if aid in seen:
            raise ReleaseError(f"release {rid!r}: duplicate atom id {aid!r}")
        seen.add(aid)
        for field in ("spec_ref", "contract_ref"):
            if not isinstance(raw[field], str) or not raw[field].strip():
                raise ReleaseError(f"release {rid!r}: atom {aid!r} {field} must be a non-empty string")
        dep = raw["depends_on"]
        if not isinstance(dep, list) or not all(isinstance(d, str) for d in dep):
            raise ReleaseError(f"release {rid!r}: atom {aid!r} depends_on must be a list of atom ids")
        paths = raw.get("paths", [])
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise ReleaseError(f"release {rid!r}: atom {aid!r} paths must be a list of strings")
        journeys = raw.get("journeys", [])
        # review finding (feat-*-certification, PR #272): an empty/whitespace-only
        # journey tag substring-matches EVERY title under a naive matcher, and even under
        # `skills/certify-local/certify_local.py`'s boundary-aware `_tag_source` remains a
        # meaningless zero-width pattern — reject it here, at the ONE place every journey tag is
        # authored, rather than relying on the consumer to defend against a malformed tag.
        if not isinstance(journeys, list) or not all(isinstance(j, str) and j.strip() for j in journeys):
            raise ReleaseError(f"release {rid!r}: atom {aid!r} journeys must be a list of "
                               f"non-empty, non-whitespace-only strings")
        atoms.append(Atom(aid, raw["spec_ref"], raw["contract_ref"], dep, paths=paths, journeys=journeys))

    order = _toposort(atoms)                      # raises on cycle / dangling edge
    acceptance = _validate_acceptance(rid, doc.get("acceptance", []))
    return Release(rid, doc["description"], state, atoms, order, acceptance=acceptance)


def load_release(id, *, project_dir=None, root=None):
    """Load + validate a single release by id, fail-closed. `id` is a [a-z0-9-]+ slug (no path
    separators/traversal) resolved strictly within <project_dir>/.foundry/releases/<id>/release.yaml."""
    if not isinstance(id, str) or not _SLUG.match(id):
        raise ReleaseError(f"release id {id!r} is not a [a-z0-9-]+ slug (no path separators/traversal)")
    base = os.path.join(_project_dir(project_dir), ".foundry", "releases")
    path = os.path.join(base, id, "release.yaml")
    real_base, real_path = os.path.realpath(base), os.path.realpath(path)
    if os.path.commonpath([real_base, real_path]) != real_base:
        raise ReleaseError(f"release id {id!r}: resolved path escapes the releases dir (containment)")
    if not os.path.isfile(path):
        raise ReleaseError(f"release {id!r}: no manifest at {path}")
    try:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ReleaseError(f"release {id!r}: malformed YAML in {path}: {e}")
    return _validate(doc, expected_id=id)


def save_release(release, *, project_dir=None):
    """Persist a release's state fail-closed-atomically (temp + os.replace; single-active-operator norm →
    last-writer-wins). Re-serializes from the validated Release (state is the only thing transitions change)."""
    path = release_path(release.id, project_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    def _atom_doc(a):
        d = {"id": a.id, "spec_ref": a.spec_ref, "contract_ref": a.contract_ref,
             "depends_on": a.depends_on}
        # round-trip the optional fields only when populated, so a manifest
        # authored before this extension is re-saved byte-stable (no `paths: []` noise added).
        if a.paths:
            d["paths"] = a.paths
        if a.journeys:
            d["journeys"] = a.journeys
        return d

    doc = {
        "id": release.id,
        "description": release.description,
        "state": release.state,
        "atoms": [_atom_doc(a) for a in release.atoms],
    }
    # round-trip `acceptance:` only when populated, so a manifest authored before this
    # extension (or one with no acceptance records yet) re-saves byte-stable (no `acceptance: []`
    # noise added) — same convention as `paths`/`journeys` above.
    if release.acceptance:
        doc["acceptance"] = list(release.acceptance)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".release-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(doc, f, sort_keys=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


# ── acceptance (feat-*-certification): a PRACTICE record, never a gate ─────────────────────
#
# `append_acceptance` writes a one-line signed record {date, operator, verdict, note} to the release
# manifest's OPTIONAL `acceptance:` list, once the operator has run `/foundry:certify-local` and
# decided the release is (or is not) fit to ship. This is deliberately NOT wired into
# `transition`/`derive_closure`/`close` above, and it never will be: CONSTITUTION.md's factory-mode
# tail is "certify locally -> operator acceptance -> staging", and the umbrella workspace's own
# CLAUDE.md ("Delivery sign-off — operator-held, the terminal step") states plainly that the
# operator's own test pass is a human practice, "deliberately not machine-enforced — no gate,
# branch-protection context, or CI job asserts it ran, and none should". This function is that
# practice's durable, greppable record — nothing more.
def append_acceptance(release_id, operator, verdict, note, *, project_dir=None, date=None):
    """Append {date, operator, verdict, note} to release `release_id`'s `acceptance:` list.

    Fail-closed refusals (raises ReleaseError, never a silent no-op on a bad input):
      * an unknown/malformed `release_id` — propagated from `load_release` (never a soft return).
      * a `verdict` outside the closed 2-value TERMINAL enum (`ACCEPT_VERDICTS` — accepted|rejected;
        there is no in-between/pending value to record here, so anything else is refused as
        "not terminal").
      * a non-string/empty `operator`, or a non-string `note`.

    Idempotent per (operator, date, verdict): re-appending the IDENTICAL (operator, date, verdict)
    tuple is a no-op (first-write-wins on `note`) rather than growing a duplicate entry on every
    re-run of the same day's same verdict by the same operator. `date` defaults to today
    (`datetime.date.today().isoformat()`); pass it explicitly only for deterministic tests.

    Returns the updated (saved) Release. NEVER a gate: no floor primitive (`transition`,
    `derive_closure`, the merge gate, authorization) reads this list."""
    if verdict not in ACCEPT_VERDICTS:
        raise ReleaseError(f"acceptance verdict {verdict!r} is not terminal — "
                           f"must be one of {sorted(ACCEPT_VERDICTS)}")
    if not isinstance(operator, str) or not operator.strip():
        raise ReleaseError("acceptance operator must be a non-empty string")
    if not isinstance(note, str):
        raise ReleaseError("acceptance note must be a string")
    release = load_release(release_id, project_dir=project_dir)   # raises ReleaseError: unknown release
    d = date or datetime.date.today().isoformat()
    key = (operator, d, verdict)
    for existing in release.acceptance:
        if (existing.get("operator"), existing.get("date"), existing.get("verdict")) == key:
            return release   # idempotent no-op — identical (operator, date, verdict) already recorded
    release.acceptance.append({"date": d, "operator": operator, "verdict": verdict, "note": note})
    save_release(release, project_dir=project_dir)
    return release


# ── evidence re-derivation (the closure principle) ─────────────────────────────────────────────────────

def _contract_info(atom, project_dir):
    """Read (target_repo, contract_sha256) from an atom's contract_ref (resolved under the project dir).
    target_repo is the top-level CHECK-4b venue field (None = self-host/default repo); contract_sha256 is
    the frozen authorized: block value. Returns (None, None) on any read error (fail-safe → not-merged)."""
    path = os.path.join(_project_dir(project_dir), atom.contract_ref)
    try:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return (None, None)
    blk = doc.get("authorized") or {}
    return (doc.get("target_repo"), blk.get("contract_sha256"))


def _default_authorized(atom, project_dir):
    """Re-derive an atom's authorization via foundry_authz.is_authorized (recompute-match of spec_sha256 +
    contract_sha256 over the live working-tree spec+contract — the same primitive the merge gate's CHECK 1
    uses). Resolves spec_ref/contract_ref under the project dir. Fail-safe → False on any error."""
    sys.path.insert(0, HERE)
    try:
        import foundry_authz as fa
        pd = _project_dir(project_dir)
        spec = os.path.join(pd, atom.spec_ref)
        contract = os.path.join(pd, atom.contract_ref)
        return bool(fa.is_authorized(spec, contract))
    except Exception:
        return False


def _resolve_repo(target_repo, project_dir):
    """Resolve a target_repo KEY to a checkout path. None / the merge-gate sentinel "workspace" → the
    project's own repo (self-host default). Otherwise look the key up in the canonical inventory at
    <project_dir>/.claude/foundry-project.json repos{} (UL-0022 — the SAME path every other primitive uses:
    foundry-doctor, foundry-wt, the merge-gate hook), anchoring any RELATIVE inventory path to the project
    dir; else treat the key as a path relative to the project dir."""
    pd = _project_dir(project_dir)

    def _anchor(p):
        return p if os.path.isabs(p) else os.path.join(pd, p)

    def _read_project():
        proj = os.path.join(pd, ".claude", "foundry-project.json")
        if os.path.isfile(proj):
            try:
                import json
                with open(proj, encoding="utf-8") as f:
                    return json.load(f) or {}
            except (OSError, ValueError):
                pass
        return {}

    def _resolve_key(key, cfg):
        """A repos{} key -> path: inventory dict/str (anchored, existence not required — unchanged),
        else a bare-key relative dir if it exists, else None."""
        ent = (cfg.get("repos") or {}).get(key)
        if isinstance(ent, dict) and ent.get("path"):
            return _anchor(ent["path"])
        if isinstance(ent, str):
            return _anchor(ent)
        cand = os.path.join(pd, key)
        return cand if os.path.isdir(cand) else None

    # explicit "workspace" sentinel -> the project's own repo (unchanged).
    if target_repo == "workspace":
        return pd
    # self-host DEFAULT (target_repo falsy/unset): honor a designated self_host_code_repo (resolved through
    # the SAME repos{} inventory) when present + resolvable; else the project's own repo (backward-compatible).
    if not target_repo:
        cfg = _read_project()
        shcr = cfg.get("self_host_code_repo")
        if shcr:
            r = _resolve_key(shcr, cfg)
            if r is not None:
                return r
        return pd
    # explicit key -> inventory lookup, else project dir (unchanged behavior).
    r = _resolve_key(target_repo, _read_project())
    return r if r is not None else pd


def _history_contains(repo, branch, contract_sha256):
    """The producer-aligned, content-based, squash/rebase-safe merged signal, bound to the authorized
    contract via the FULL 64-hex contract_sha256 (no prefix matching → no abbreviated-sha false-positives).

    Merged-on-main is resolved SOLELY by a content-addressed digest match over the committed
    `.foundry/build-provenance.yaml` marker's GIT HISTORY (the SLSA/in-toto digest-match pattern). Because
    the marker is truncating-overwritten per PR, reading only the branch-TIP blob would regress multi-PR
    closure; instead enumerate the marker states that LANDED ON the mainline via
    `git log <branch> --first-parent --format=%H -- .foundry/build-provenance.yaml` (`--first-parent`, NOT
    `--full-history`: first-parent walks only the mainline merge sequence, so each enumerated revision is a
    gate-verified merged tip — squash, merge-commit, and rebase-and-merge alike land the marker on the
    first-parent chain — excluding pre-merge feature-branch transients that were never the merged tip). For
    each landed revision parse `git show <sha>:.foundry/build-provenance.yaml` and union its
    `authorizations[].contract_sha256`; return True iff the target digest is in that union.

    Per-revision handling: a `git show` that returns NON-ZERO (path absent/deleted at that revision) is a
    legit "marker absent here" → SKIP. A blob that IS present but parses to a non-mapping / invalid YAML is
    fail-closed FOR THAT REVISION (treated as carrying no authorizations) with a surfaced diagnostic — never
    a silent skip, never an abort of the whole scan. If the `git log` enumeration itself errors (not a repo /
    bad branch) → fail-closed return False. (a gate that cannot verify does not close.)"""
    if not contract_sha256:
        return False
    # Enumerate the marker states that LANDED ON <branch>'s mainline (--first-parent), newest first.
    try:
        r = subprocess.run(
            ["git", "-C", repo, "log", branch, "--first-parent", "--format=%H",
             "--", ".foundry/build-provenance.yaml"],
            capture_output=True, text=True)
    except Exception:
        return False
    if r.returncode != 0:
        return False
    shas = [s for s in r.stdout.split() if s]
    for sha in shas:
        try:
            sh = subprocess.run(
                ["git", "-C", repo, "show", f"{sha}:.foundry/build-provenance.yaml"],
                capture_output=True, text=True)
        except Exception as e:
            sys.stderr.write(f"_history_contains: git show failed at {sha[:12]}: {e}\n")
            continue
        if sh.returncode != 0:
            # path absent / deleted at this revision — a legit skip.
            continue
        try:
            doc = yaml.safe_load(sh.stdout)
        except Exception as e:
            sys.stderr.write(f"_history_contains: invalid YAML in marker at {sha[:12]} "
                             f"(fail-closed for this revision): {e}\n")
            continue
        if not isinstance(doc, dict):
            sys.stderr.write(f"_history_contains: non-mapping marker at {sha[:12]} "
                             f"(fail-closed for this revision)\n")
            continue
        for a in (doc.get("authorizations") or []):
            if isinstance(a, dict) and a.get("contract_sha256") == contract_sha256:
                return True
    return False


def _default_merged(atom, project_dir, branch="main"):
    """Re-derive MERGED-on-main: the atom's contract_sha256 appears in its target repo's branch history.
    target_repo + contract_sha256 come from the atom's contract (not the manifest). Fail-safe → False."""
    target_repo, csha = _contract_info(atom, project_dir)
    repo = _resolve_repo(target_repo, project_dir)
    return _history_contains(repo, branch, csha)


def derive_closure(release, *, project_dir=None, branch="main",
                   authorized_fn=None, merged_fn=None):
    """Re-derive per-atom closure from PRIMARY artifacts (NEVER an assertion). CLOSED iff AUTHORIZED
    (recompute-match) AND MERGED-on-main (contract_sha256 in target-repo history — which subsumes the full
    per-atom floor, incl. the merge gate's CHECK 7 walk). Returns per-atom verdict dicts. The two resolvers
    are injectable FOR TESTS ONLY; the production defaults are fixed."""
    authorized_fn = authorized_fn or (lambda a: _default_authorized(a, project_dir))
    merged_fn = merged_fn or (lambda a: _default_merged(a, project_dir, branch))
    verdicts = []
    for a in release.atoms:
        authd = bool(authorized_fn(a))
        merged = bool(merged_fn(a))
        missing = []
        if not authd:
            missing.append("authorized")
        if not merged:
            missing.append("merged-on-main")
        verdicts.append({"atom_id": a.id, "authorized": authd, "merged": merged,
                         "closed": authd and merged, "missing": missing})
    return verdicts


def _require_specs_and_contracts(release, project_dir):
    pd = _project_dir(project_dir)
    missing = []
    for a in release.atoms:
        if not os.path.isfile(os.path.join(pd, a.spec_ref)):
            missing.append(f"{a.id}:spec_ref")
        if not os.path.isfile(os.path.join(pd, a.contract_ref)):
            missing.append(f"{a.id}:contract_ref")
    if missing:
        raise ReleaseError(f"cannot plan: missing spec/contract file(s): {missing}")


def _require_all_authorized(release, project_dir, authorized_fn=None):
    authorized_fn = authorized_fn or (lambda a: _default_authorized(a, project_dir))
    unauth = [a.id for a in release.atoms if not authorized_fn(a)]
    if unauth:
        raise ReleaseError(f"cannot activate: atom(s) not AUTHORIZED (re-derived): {unauth}")


def transition(release, target, *, project_dir=None, branch="main",
               authorized_fn=None, merged_fn=None, save=True):
    """Forward-only state transition with RE-DERIVED preconditions. Raises ReleaseError on an illegal
    transition or a precondition that does not re-derive. There is NO precondition-skip / --force path —
    `active → completed` is the evidence-derived closure gate (AC-RM-3)."""
    cur = release.state
    if _LEGAL.get(cur) != target:
        raise ReleaseError(
            f"illegal transition {cur!r} -> {target!r} (forward-only: {' -> '.join(STATES)})")
    if target == "planned":
        _require_specs_and_contracts(release, project_dir)        # dep graph acyclic already (load-time)
    elif target == "active":
        _require_all_authorized(release, project_dir, authorized_fn)
    elif target == "completed":
        verdicts = derive_closure(release, project_dir=project_dir, branch=branch,
                                  authorized_fn=authorized_fn, merged_fn=merged_fn)
        unclosed = [v for v in verdicts if not v["closed"]]
        if unclosed:
            detail = "; ".join(f"{v['atom_id']} (missing: {', '.join(v['missing'])})" for v in unclosed)
            raise ReleaseError(f"cannot close: {len(unclosed)} atom(s) not CLOSED by evidence — {detail}")
    release.state = target
    if save:
        save_release(release, project_dir=project_dir)
    return release


# ── CLI (the /foundry:release operator surface) ──────────────────────────────────────────────────────

def _print_status(release, project_dir):
    print(f"release {release.id}: state={release.state}  atoms={len(release.atoms)}  "
          f"(wave order: {', '.join(release.order)})")
    if release.state in ("active", "completed"):
        for v in derive_closure(release, project_dir=project_dir):
            mark = "CLOSED" if v["closed"] else f"open (missing: {', '.join(v['missing'])})"
            print(f"  - {v['atom_id']}: {mark}")


# ── run-state (feat-foundry-release-run-state, AC-RRS-1..8): a per-atom, machine-derived run ────────
# object — every column re-derived at read time from ground truth (authorization recompute,
# supersession-as-state, merged-on-main, dispatch claim, walk verdict). It REPLACES the hand-carried
# run-notes/NIGHT-STATE genre; it never replaces a gate — the merge gate, closure gate, and authorize
# flow are untouched consumers of the SAME primitives (_default_authorized/_contract_info/_resolve_repo/
# _history_contains), never re-derived here. Read-only over the floor (AC-RRS-7): no writes to specs,
# contracts, authorization records, provenance markers, or the release manifest. No emitted field is
# ever read from a hand-authored ledger, run-notes file, or a PRIOR run-state emission (AC-RRS-1) — there
# is no cache/durable-truth path; `--summary` output a consumer may cache is never itself re-derived from.

FIELDS = ("id", "state", "authorized", "superseded", "dispatched", "pr", "merged_on_main",
          "walk_verdict", "runnable", "blocked_reason", "probe_error")

STATE_ORDER = ("superseded", "merged", "dispatched", "unauthorized", "runnable", "blocked", "UNKNOWN")

_SUMMARY_CEILING_BYTES = 2048

# AC-RRS-2: supersession is state the tooling reads, not prose — a YAML frontmatter `status:
# superseded` key, OR a body line matching this regex (bold-tolerant, case-insensitive on the word).
_SUPERSEDED_BODY_RE = re.compile(r"^\*{0,2}[Ss]tatus:?\*{0,2}\s*superseded\b", re.M)

FANOUT_REL = os.path.join(".foundry", "fanout")   # the process-spawn dispatch-manifest claim surface


def _probe_superseded(atom, project_dir):
    """AC-RRS-2: (superseded_or_None, probe_error_or_None). None+error iff the atom's spec_ref is
    itself unresolvable (absent/unreadable) — an unresolvable input per AC-RRS-5, not a silent False."""
    path = os.path.join(_project_dir(project_dir), atom.spec_ref)
    if not os.path.isfile(path):
        return None, f"{atom.id}: spec unreadable ({atom.spec_ref})"
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        return None, f"{atom.id}: spec read error: {e}"
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


def _probe_repo_branch(repo, branch):
    """Returns an error string (or None) probing that <repo> is a real git working tree with
    <branch> resolvable — the AC-RRS-5 unresolvable-input checks ('missing target repo', 'git
    failure') that _history_contains itself fail-closes SILENTLY (to False, by design, for the
    closure gate). run-state needs to DISTINGUISH 'verified not merged' from 'could not derive'."""
    if not os.path.isdir(repo):
        return f"target repo path does not exist: {repo}"
    try:
        r = subprocess.run(["git", "-C", repo, "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True)
    except Exception as e:
        return f"git failure probing repo {repo}: {e}"
    if r.returncode != 0 or r.stdout.strip() != "true":
        return f"not a git repository: {repo}"
    try:
        r2 = subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "--quiet", branch],
                            capture_output=True, text=True)
    except Exception as e:
        return f"git failure resolving branch {branch!r} in {repo}: {e}"
    if r2.returncode != 0:
        return f"branch {branch!r} not resolvable in {repo}"
    return None


def _probe_merged_on_main(atom, project_dir, branch):
    """AC-RRS-1/-5/-6: (merged_or_None, probe_error_or_None) — REUSES _contract_info/_resolve_repo/
    _history_contains (the existing RD merged-on-main derivation; no new mechanism). None+error iff
    the contract is UNREADABLE/malformed, the target repo is missing/unresolvable, or a git probe
    fails — the AC-RRS-5 unresolvable-input examples. A contract that reads fine but simply has NO
    `authorized:` block yet (the common not-yet-authorized case — most backlog atoms) is NOT
    unresolvable: an atom cannot merge before it is authorized, so `False` is a confident, well-
    defined derivation here, not a probe failure (this is what keeps the `unauthorized` bucket
    reachable — every un-authorized atom would otherwise misroute to UNKNOWN)."""
    contract_path = os.path.join(_project_dir(project_dir), atom.contract_ref)
    if not os.path.isfile(contract_path):
        return None, f"{atom.id}: contract unreadable ({atom.contract_ref})"
    try:
        with open(contract_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as e:
        return None, f"{atom.id}: contract malformed ({atom.contract_ref}): {e}"
    if not isinstance(raw, dict):
        return None, f"{atom.id}: contract malformed ({atom.contract_ref}): not a mapping"
    target_repo, csha = _contract_info(atom, project_dir)
    if not csha:
        return False, None   # not yet authorized -- definitively not merged, not unresolvable
    repo = _resolve_repo(target_repo, project_dir)
    err = _probe_repo_branch(repo, branch)
    if err:
        return None, f"{atom.id}: {err}"
    return bool(_history_contains(repo, branch, csha)), None


def _read_marker(repo_dir):
    """Read the code repo's CURRENT (tip) `.foundry/build-provenance.yaml` marker doc, else None.
    Read-only; never the marker's git HISTORY (that is _history_contains' job for merged_on_main)."""
    path = os.path.join(repo_dir, ".foundry", "build-provenance.yaml")
    try:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return None
    return doc if isinstance(doc, dict) else None


def _marker_entry_for_atom(marker, atom):
    if not marker:
        return None
    for a in (marker.get("authorizations") or []):
        if isinstance(a, dict) and a.get("spec_ref") == atom.spec_ref:
            return a
    return None


def _probe_pr(atom, repo_dir):
    """AC-RRS-1: the PR reference recorded in the atom's build-provenance.yaml marker, else null.
    No writer in the shipped provenance schema populates `pr` today (out of THIS atom's scope —
    provenance is a denied_path); this is the forward-compatible READ path, so it is `null` today
    and picks up a future producer without a run-state change."""
    marker = _read_marker(repo_dir)
    entry = _marker_entry_for_atom(marker, atom)
    if entry and entry.get("pr") is not None:
        return entry["pr"]
    if marker and marker.get("pr") is not None:
        return marker["pr"]
    return None


def _probe_walk_verdict(atom, repo_dir):
    """AC-RRS-1: the atom's walk-evidence artifact when present, else 'none' (absent evidence is not
    a failure). Reads the same provenance marker `_probe_pr` reads (the shipped per-atom provenance
    artifact); forward-compatible with a future walk_verdict-carrying producer."""
    marker = _read_marker(repo_dir)
    entry = _marker_entry_for_atom(marker, atom)
    if entry and entry.get("walk_verdict"):
        return entry["walk_verdict"]
    if marker and marker.get("walk_verdict"):
        return marker["walk_verdict"]
    return "none"


def _probe_dispatched(atom, project_dir):
    """AC-RRS-1: the `dispatched` value-space rule — a claim reference string when a live claim
    exists in the process-spawn dispatch-manifest claim surface (`.foundry/fanout/*.report.json`,
    AC-PSF-4's dispatch report, matched by the record's `task` == this atom's id — the natural task
    name a release-wave dispatch gives a manifest atom); 'none' when the surface is readable and
    holds no matching claim (including: the directory has never been created — a well-defined,
    legitimately-empty claim surface, same 'absent evidence is not a failure' posture as
    walk_verdict); 'unknown' when the surface is unreadable/corrupt (a malformed report — the
    residual: other dispatch postures/v1-unsupported shapes also read 'unknown', never
    permanently disqualifying (AC-RRS-3))."""
    d = os.path.join(_project_dir(project_dir), FANOUT_REL)
    if not os.path.isdir(d):
        return "none"
    try:
        names = sorted(fn for fn in os.listdir(d) if fn.endswith(".report.json"))
    except OSError:
        return "unknown"
    for fn in names:
        try:
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            return "unknown"
        if not isinstance(doc, dict):
            return "unknown"
        for rec in (doc.get("report") or []):
            if isinstance(rec, dict) and rec.get("task") == atom.id:
                return f"fanout:{fn}#{rec.get('key')}/{rec.get('task')}:{rec.get('terminus')}"
    return "none"


def _derive_run_row(atom, project_dir, branch):
    """One atom's raw per-field derivation (AC-RRS-1), each read fresh at call time. Returns a dict
    with every FIELDS key EXCEPT state/runnable/blocked_reason, which need the whole-release
    dependency graph (added by the second pass in `derive_run_state`)."""
    probe_errors = []
    authorized = bool(_default_authorized(atom, project_dir))
    superseded, sup_err = _probe_superseded(atom, project_dir)
    if sup_err:
        probe_errors.append(sup_err)
    merged, merged_err = _probe_merged_on_main(atom, project_dir, branch)
    if merged_err:
        probe_errors.append(merged_err)
    target_repo, _csha = _contract_info(atom, project_dir)
    repo_dir = _resolve_repo(target_repo, project_dir)
    return {
        "id": atom.id,
        "authorized": authorized,
        "superseded": superseded,
        "dispatched": _probe_dispatched(atom, project_dir),
        "pr": _probe_pr(atom, repo_dir),
        "merged_on_main": merged,
        "walk_verdict": _probe_walk_verdict(atom, repo_dir),
        "probe_error": "; ".join(probe_errors) if probe_errors else None,
    }


def derive_run_state(release, *, project_dir=None, branch="main"):
    """AC-RRS-1..6/-8: re-derive the per-atom run object for every atom in `release`, in manifest
    declaration order. Two passes: (1) each atom's independent fields; (2) `state`/`runnable`/
    `blocked_reason`, which need every OTHER atom's `merged_on_main` (the depends_on dependency gate,
    AC-RRS-3) and the AC-RRS-5 precedence/UNKNOWN-dominance rule. Read-only (AC-RRS-7); never
    writes specs/contracts/authz/provenance/the manifest."""
    pd = _project_dir(project_dir)
    rows = {a.id: _derive_run_row(a, pd, branch) for a in release.atoms}

    for atom in release.atoms:
        row = rows[atom.id]
        # AC-RRS-5: the unresolvable-input rule DOMINATES bucket precedence — checked FIRST,
        # regardless of what superseded/authorized/merged/dispatched independently computed to.
        if row["probe_error"]:
            row["state"], row["runnable"], row["blocked_reason"] = "UNKNOWN", False, None
            continue
        if row["superseded"]:
            row["state"], row["runnable"], row["blocked_reason"] = "superseded", False, None
            continue
        if row["merged_on_main"]:
            row["state"], row["runnable"], row["blocked_reason"] = "merged", False, None
            continue
        if row["dispatched"] not in ("none", "unknown"):
            row["state"], row["runnable"], row["blocked_reason"] = "dispatched", False, None
            continue
        if not row["authorized"]:
            row["state"], row["runnable"], row["blocked_reason"] = "unauthorized", False, None
            continue
        # AC-RRS-3: runnable dependency gate — fail-closed; an edge to an atom absent from the
        # manifest is UNMET (never assumed merged), named in blocked_reason.
        unmet = []
        for dep in atom.depends_on:
            dep_row = rows.get(dep)
            if dep_row is None:
                unmet.append(f"{dep} (absent from manifest)")
            elif not dep_row.get("merged_on_main"):
                unmet.append(dep)
        if unmet:
            row["state"] = "blocked"
            row["runnable"] = False
            row["blocked_reason"] = "unmet dependency: " + ", ".join(unmet)
        else:
            row["state"] = "runnable"
            row["runnable"] = True
            row["blocked_reason"] = None

    return [{k: rows[a.id][k] for k in FIELDS} for a in release.atoms]


def _dag_order_declaration_tiebreak(atoms):
    """AC-RRS-4: Kahn's toposort over `depends_on`, but the ready-queue pops in ASCENDING MANIFEST
    DECLARATION INDEX (not alphabetical — unlike `_toposort`'s wave order) so ties break by manifest
    declaration order, per the summary's next-runnable ordering rule. An edge to an atom absent from
    the atom set is ignored for ordering purposes only (`derive_run_state` already fail-closes it
    into `blocked` above; this function must not raise on it — defense in depth)."""
    ids = [a.id for a in atoms]
    idx = {aid: i for i, aid in enumerate(ids)}
    indeg = {aid: 0 for aid in ids}
    adj = {aid: [] for aid in ids}
    for a in atoms:
        for d in a.depends_on:
            if d in idx:
                adj[d].append(a.id)
                indeg[a.id] += 1
    ready = sorted([aid for aid in ids if indeg[aid] == 0], key=lambda x: idx[x])
    order = []
    while ready:
        ready.sort(key=lambda x: idx[x])
        n = ready.pop(0)
        order.append(n)
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
    # a residual (a real dependency cycle, unreachable via the normal load_release path, which
    # already rejects cycles at load time) is appended in declaration order — never dropped, never
    # raised: this function only ORDERS a next-runnable list, it does not gate anything.
    if len(order) != len(ids):
        order += [aid for aid in ids if aid not in order]
    return order


def render_summary(release, rows):
    """AC-RRS-4: a PURE function of `release` (for the DAG-order + id set) and precomputed `rows`
    (as returned by `derive_run_state`) — a compact YAML digest <=2048 UTF-8 bytes: the release
    pointer, all seven per-state counts (zero counts included), and the next runnable atoms in
    manifest-DAG order (ties by declaration order). WHEN the natural content would exceed the
    ceiling, the runnable list is truncated (same order) with a trailing '...+N more' marker — the
    release pointer + counts are never dropped; the digest never exceeds the ceiling."""
    by_id = {r["id"]: r for r in rows}
    counts = {s: 0 for s in STATE_ORDER}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    dag_order = _dag_order_declaration_tiebreak(release.atoms)
    next_runnable = [aid for aid in dag_order if by_id.get(aid, {}).get("state") == "runnable"]

    payload = {"release": release.id, "counts": {s: counts[s] for s in STATE_ORDER},
               "next_runnable": list(next_runnable)}
    text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    if len(text.encode("utf-8")) <= _SUMMARY_CEILING_BYTES:
        return text

    full = list(next_runnable)
    for keep in range(len(full) - 1, -1, -1):
        marker = f"…+{len(full) - keep} more"
        payload["next_runnable"] = full[:keep] + [marker]
        text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        if len(text.encode("utf-8")) <= _SUMMARY_CEILING_BYTES:
            return text
    return text   # best-effort floor: release pointer + counts were never dropped above


def _cli_run_state(release, *, project_dir=None, branch="main", summary=False, strict=False):
    rows = derive_run_state(release, project_dir=project_dir, branch=branch)
    if summary:
        sys.stdout.write(render_summary(release, rows))
    else:
        yaml.safe_dump(rows, sys.stdout, sort_keys=False, allow_unicode=True)
    if strict and any(r["state"] == "UNKNOWN" for r in rows):
        return 1
    return 0


_TEMPLATE = """\
# Release manifest (RA shaping). Edit the atoms list, then: /foundry:release plan|activate|close <id>.
id: {id}
description: TODO — what this release delivers.
state: backlog
atoms:
  - id: example-atom
    spec_ref: specs/features/<product>/<domain>/<cap>/feat-<…>.md   # relative to CLAUDE_PROJECT_DIR
    contract_ref: specs/features/<product>/<domain>/<cap>/acceptance-contract.yaml
    depends_on: []                                                  # ids of atoms that must precede it
"""


def shape(id, *, project_dir=None):
    """RA shaping: scaffold a `release.yaml` TEMPLATE if none exists (so the operator can author the atom
    set), else load + validate + show the existing manifest. Returns 0; never writes over an existing one."""
    path = release_path(id, project_dir)
    if not os.path.isfile(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(_TEMPLATE.format(id=id))
        print(f"scaffolded release template at {path} — edit the atoms, then `/foundry:release plan {id}`")
        return 0
    rel = load_release(id, project_dir=project_dir)   # validate the operator-authored manifest
    _print_status(rel, project_dir)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Release lifecycle: shaping (RA) + evidence-derived closure (RD).")
    sub = ap.add_subparsers(dest="verb", required=True)
    for v in ("shape", "status", "plan", "activate", "close"):
        sp = sub.add_parser(v)
        sp.add_argument("id")
        sp.add_argument("--branch", default="main")
    sp_rs = sub.add_parser("run-state")
    sp_rs.add_argument("id")
    sp_rs.add_argument("--branch", default="main")
    sp_rs.add_argument("--summary", action="store_true",
                       help="emit the <=2048-byte YAML digest (release pointer + per-state counts + "
                            "next-runnable) instead of the full per-atom object list")
    sp_rs.add_argument("--strict", action="store_true",
                       help="exit non-zero if any atom's re-derived state is UNKNOWN (AC-RRS-5)")
    sp_acc = sub.add_parser("accept", help="append a PRACTICE acceptance record (never a gate)")
    sp_acc.add_argument("id")
    sp_acc.add_argument("--operator", required=True)
    sp_acc.add_argument("--verdict", required=True, choices=sorted(ACCEPT_VERDICTS))
    sp_acc.add_argument("--note", default="")
    args = ap.parse_args(argv)
    try:
        if args.verb == "shape":
            return shape(args.id)
        if args.verb == "accept":
            release = append_acceptance(args.id, args.operator, args.verdict, args.note)
            print(f"release {release.id}: acceptance recorded — {args.verdict} by {args.operator} "
                  f"(practice record only, never a gate).")
            return 0
        # AC-RRS-1: a <release-id> that resolves to no manifest is a hard error (non-zero, no
        # emission) — load_release() raises BEFORE any run-state output is printed.
        rel = load_release(args.id)
    except ReleaseError as e:
        print(f"release error: {e}", file=sys.stderr)
        return 2
    try:
        if args.verb == "status":
            _print_status(rel, None)
            return 0
        if args.verb == "run-state":
            return _cli_run_state(rel, branch=args.branch, summary=args.summary, strict=args.strict)
        target = {"plan": "planned", "activate": "active", "close": "completed"}[args.verb]
        transition(rel, target, branch=args.branch)
        print(f"release {rel.id}: -> {rel.state}")
        if rel.state == "completed":
            print("  closure re-derived from evidence (authorized + merged-on-main); CLOSED.")
        return 0
    except ReleaseError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
