#!/usr/bin/env python3
"""foundry-wave-plan — deterministic release-wave planning (feat-foundry-wave-plan).

Reads a release manifest in the `foundry_release.py` `release.yaml` schema (atoms[{id, spec_ref,
contract_ref, depends_on[], paths[]?, journeys[]?}] — `paths`/`journeys` are the additive optional
atom fields `foundry_release.py` now accepts) and emits a **wave plan**: atoms grouped into waves
such that

  1. an atom is in a wave STRICTLY AFTER every wave containing one of its `depends_on` (dependency
     closure), AND
  2. no atom shares a wave with a sibling whose declared `paths` OVERLAP its own (declared-write-path
     overlap forces a later wave — two atoms that touch the same tree must never run concurrently).

Pure + deterministic: the SAME manifest always produces the byte-identical wave-plan JSON (no
wall-clock, no randomness, no ambient state) — the wave assignment is a total function of the
manifest's atoms, their `depends_on` edges, and their `paths`. A dependency CYCLE is a hard error
that NAMES the atoms in the cycle (never emits a partial/best-effort plan). This module never
mutates anything; it is a pure planning tool consumed by `release-wave.js` (the wave-by-wave
fan-out executor) and by the session materializing the native Task graph (TaskCreate per atom,
blockedBy = the DAG, metadata = spec path + wave + journey tags — see `skills/release/SKILL.md`).

A manifest atom's `paths[]` is a SEPARATE, hand-authored declaration (used only for wave-splitting
here) — nothing upstream of this module forces it to match what the atom's frozen
`acceptance-contract.yaml` actually scopes the atom to. `--check` mode (only) CHECKS that
correspondence: for every atom with a non-empty `paths[]` whose `contract_ref` resolves to a
parseable contract, `paths[]` must be a verbatim subset of that contract's `scope.allowed_paths`
(REFUSE, fail-closed, if not); an atom whose contract does not yet resolve SKIPS the check (named,
not silent) rather than assuming consistency it cannot verify. See
`check_paths_subset_of_contract` below.

Declared-path overlap is computed on the SAME globset shape an acceptance-contract's
`scope.allowed_paths` uses (e.g. `"src/foo/**"`, `"scripts/*.py"`) — each path is first
`posixpath.normpath`-normalized (collapsing `./`/`a/../b` and a leading `/`), then reduced to a
LITERAL directory-prefix "overlap surface": a single TRAILING `/**` or `/*` glob segment is
stripped (the common `src/foo/**` shape); any OTHER glob metacharacter (`*`, `?`, `[`, `]`)
occurring anywhere else (a mid-path segment, or a non-bare trailing segment like `scripts/*.py`)
truncates the path at the FIRST metachar-bearing segment, so only the literal segments before it
are compared. Two paths overlap iff their overlap-surface prefixes are equal, or one is a
directory-prefix of the other. This is a CONSERVATIVE (never under-splits) approximation of full
glob-set intersection — truncating at the first metachar can only ever WIDEN the surface two globs
are compared over (more prefixes match, never fewer), so it never misses a real overlap, though it
can occasionally split two globs that would never actually collide bytewise. Deterministic and
dependency-free (no third-party glob-algebra library, no `fnmatch`/regex-based set intersection).

CLI:
  foundry-wave-plan.py <manifest.yaml>            # emit the wave-plan JSON to stdout
  foundry-wave-plan.py --check <manifest.yaml>     # validate + the paths[]-vs-contract subset
                                                    # check; print a one-line verdict, no JSON
"""
from __future__ import annotations

import argparse
import json
import os
import posixpath
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import foundry_release as fr   # noqa: E402  — the shared schema/validator (ReleaseError, _validate)


class WavePlanError(Exception):
    """Fail-closed: a malformed manifest or a dependency cycle. Never a partial/best-effort plan."""


# ------------------------------------------------------------------------------------------------ #
# manifest loading — BY PATH (not by release id / `.foundry/releases/<id>/` resolution), reusing
# foundry_release's schema validator so the wave planner and the release state machine can never
# silently diverge on what a well-formed manifest is.
# ------------------------------------------------------------------------------------------------ #

def load_manifest(path):
    """Load + schema-validate a release manifest from an EXPLICIT file path (fail-closed on missing
    file / malformed YAML / schema violation — a `fr.ReleaseError`, re-raised as-is so callers see
    the SAME diagnostic the release state machine would). `expected_id=None` — a by-path load never
    requires the manifest's `id` to match a directory name (there is none)."""
    if not os.path.isfile(path):
        raise WavePlanError(f"no manifest at {path!r}")
    try:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise WavePlanError(f"malformed YAML in {path!r}: {e}")
    try:
        return fr._validate(doc, expected_id=None)   # raises fr.ReleaseError on a cycle/schema defect
    except fr.ReleaseError as e:
        raise WavePlanError(str(e))


# ------------------------------------------------------------------------------------------------ #
# declared-path overlap
# ------------------------------------------------------------------------------------------------ #

_GLOB_META = frozenset("*?[]")


def _has_glob_meta(segment):
    return any(c in _GLOB_META for c in segment)


def _normalize_path(p):
    """`posixpath.normpath` (collapses `./`, `a/../b`, duplicate slashes), then strip a leading
    `/` — the manifest/acceptance-contract path-scope convention is always repo-relative, so a
    leading `/` is never a meaningfully different root, just a stray absolute-looking prefix
    (security finding 1, PR #271)."""
    return posixpath.normpath(p).lstrip("/")


def _literal_overlap_prefix(p):
    """Reduce a declared write-path glob to the LITERAL directory-prefix `paths_overlap` compares
    (security finding 1, PR #271 — the prior implementation only stripped a bare trailing `/**`/`/*`
    and never normalized `./`/leading-`/`, so `'scripts/*.py'` vs `'scripts/foo.py'` and
    `'./src/foo'` vs `'src/foo'` both silently UNDER-split). See the module docstring for the rule."""
    norm = _normalize_path(p)
    for suffix in ("/**", "/*"):
        if norm.endswith(suffix):
            candidate = norm[: -len(suffix)]
            if not _has_glob_meta(candidate):
                return candidate
            # a trailing glob PLUS an earlier metachar (e.g. 'src/*/logs/**') — fall through to the
            # segment-truncation rule over the now-shorter remainder; at most one suffix strip.
            norm = candidate
            break
    segments = norm.split("/") if norm else []
    for i, seg in enumerate(segments):
        if _has_glob_meta(seg):
            return "/".join(segments[:i])
    return norm


def paths_overlap(a, b):
    """Two declared write-path globs OVERLAP iff their literal overlap-surface prefixes (see
    `_literal_overlap_prefix`) are equal, or one is a directory-prefix of the other (e.g.
    `src/foo/**` overlaps `src/foo/bar.py`, `scripts/*.py` overlaps `scripts/foo.py`, and `src/foo`
    overlaps `src/foo/bar/**`). A conservative approximation of glob-set intersection — never
    under-splits."""
    na, nb = _literal_overlap_prefix(a), _literal_overlap_prefix(b)
    if na == nb:
        return True
    return na.startswith(nb + "/") or nb.startswith(na + "/")


def _atoms_overlap(paths_a, paths_b):
    return any(paths_overlap(a, b) for a in paths_a for b in paths_b)


# ------------------------------------------------------------------------------------------------ #
# wave assignment (deterministic)
# ------------------------------------------------------------------------------------------------ #

def compute_wave_plan(release):
    """Assign every atom in `release` (a `fr.Release`, already cycle-checked by `fr._validate` at
    load time) to a wave index, fail-closed + deterministic. Returns
    `(waves, atom_meta)` — `waves` a list of sorted-by-id atom-id lists (list index == wave number),
    `atom_meta` a dict `{atom_id: {wave, spec_ref, contract_ref, depends_on, paths, journeys}}`.

    Processing order is `release.order` — the Kahn topological sort `fr._toposort` already computed
    (ascending-id tiebreak at every ready-queue pop), so the wave assignment below is a PURE,
    deterministic function of the manifest content alone: two runs over the same manifest produce
    byte-identical output."""
    by_id = release.by_id
    wave_of = {}
    wave_members = []   # wave_members[i] = list of Atom objects placed in wave i, in placement order

    for aid in release.order:
        atom = by_id[aid]
        min_wave = 0
        for dep in atom.depends_on:
            min_wave = max(min_wave, wave_of[dep] + 1)
        w = min_wave
        while True:
            if w >= len(wave_members):
                wave_members.append([])
            sibling_overlap = any(
                _atoms_overlap(atom.paths, sib.paths) for sib in wave_members[w]
            )
            if not sibling_overlap:
                break
            w += 1
        wave_members[w].append(atom)
        wave_of[aid] = w

    waves = [sorted(a.id for a in members) for members in wave_members]
    atom_meta = {
        a.id: {
            "wave": wave_of[a.id],
            "spec_ref": a.spec_ref,
            "contract_ref": a.contract_ref,
            "depends_on": sorted(a.depends_on),
            "paths": sorted(a.paths),
            "journeys": sorted(a.journeys),
        }
        for a in release.atoms
    }
    return waves, atom_meta


def build_plan_doc(release):
    waves, atom_meta = compute_wave_plan(release)
    return {
        "release_id": release.id,
        "waves": waves,
        "atoms": atom_meta,
    }


# ------------------------------------------------------------------------------------------------ #
# --check-only: declared paths[] vs the frozen contract's scope.allowed_paths (security finding 2,
# PR #271). An atom's `paths[]` is a SEPARATE, hand-authored declaration on the manifest (used for
# wave-splitting); nothing upstream forced it to actually match what the atom's FROZEN
# acceptance-contract.yaml scopes the atom to. `--check` REFUSES (fail-closed) if a resolvable
# contract's `scope.allowed_paths` does not verbatim-cover the declared `paths[]` — catching a
# manifest that under-declares its real write scope for wave-splitting purposes. An unresolvable
# contract SKIPS this check for that atom (never a silent pass, never a REFUSE over an input this
# check cannot read) — it is orthogonal to whether the RELEASE itself may reference an
# atom whose contract isn't authored yet.
# ------------------------------------------------------------------------------------------------ #

def _load_contract_allowed_paths(contract_ref, project_dir):
    """The frozen contract's `scope.allowed_paths` (list[str]), or None if the contract is
    absent/unreadable/malformed/missing the field — a definite "cannot check", never a false
    empty-list pass."""
    path = os.path.join(project_dir, contract_ref)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(doc, dict):
        return None
    scope = doc.get("scope")
    if not isinstance(scope, dict):
        return None
    allowed = scope.get("allowed_paths")
    if not isinstance(allowed, list) or not all(isinstance(p, str) for p in allowed):
        return None
    return allowed


def check_paths_subset_of_contract(release, *, project_dir=None):
    """For every atom with a non-empty `paths[]`: if its `contract_ref` resolves to a parseable
    contract, its declared `paths[]` MUST be a verbatim (string-literal — globs compared as
    written, never glob-expanded/intersected) SUBSET of that contract's `scope.allowed_paths`;
    otherwise the atom's contract is unresolvable/unreadable/malformed and the check is SKIPPED for
    it (named, not silent). Returns `(violations, skipped)`, both lists of human-readable strings —
    the caller REFUSES iff `violations` is non-empty."""
    pd = project_dir or fr._project_dir()
    violations, skipped = [], []
    for atom in release.atoms:
        if not atom.paths:
            continue
        allowed = _load_contract_allowed_paths(atom.contract_ref, pd)
        if allowed is None:
            skipped.append(f"{atom.id}: contract_ref unresolvable/unreadable/malformed "
                            f"({atom.contract_ref}) — paths-subset check skipped for this atom")
            continue
        allowed_set = set(allowed)
        extra = sorted(p for p in atom.paths if p not in allowed_set)
        if extra:
            violations.append(
                f"{atom.id}: declared paths {extra} are NOT a verbatim subset of contract "
                f"scope.allowed_paths {sorted(allowed_set)} ({atom.contract_ref})"
            )
    return violations, skipped


# ------------------------------------------------------------------------------------------------ #
# CLI
# ------------------------------------------------------------------------------------------------ #

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Deterministic release-wave planning over a release.yaml manifest.")
    ap.add_argument("manifest", help="path to a release.yaml manifest")
    ap.add_argument("--check", action="store_true",
                     help="validate + compute the wave plan without emitting the JSON; also "
                          "REFUSEs if a resolvable atom's paths[] is not a verbatim subset of its "
                          "frozen contract's scope.allowed_paths; print a one-line verdict and "
                          "exit 0/2")
    args = ap.parse_args(argv)

    try:
        release = load_manifest(args.manifest)
        plan = build_plan_doc(release)
    except WavePlanError as e:
        print(f"foundry-wave-plan: FAIL-CLOSED — {e}", file=sys.stderr)
        return 2

    if args.check:
        violations, skipped = check_paths_subset_of_contract(release)
        for note in skipped:
            print(f"foundry-wave-plan --check: SKIP — {note}", file=sys.stderr)
        if violations:
            print("foundry-wave-plan: FAIL-CLOSED — declared paths[] not a subset of the frozen "
                  "contract's scope.allowed_paths:", file=sys.stderr)
            for v in violations:
                print(f"  - {v}", file=sys.stderr)
            return 2
        print(f"foundry-wave-plan: OK — {len(plan['atoms'])} atom(s), {len(plan['waves'])} wave(s)")
        return 0

    # sort_keys=True: canonical/deterministic key order (list ORDER — wave number, and ids within
    # a wave — is already sorted by compute_wave_plan; sort_keys only orders the `atoms` dict keys).
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
