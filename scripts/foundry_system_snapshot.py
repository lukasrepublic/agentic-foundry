#!/usr/bin/env python3
"""foundry_system_snapshot.py — the reality-grounding gate's FOUNDATION primitive (Atom A of
A -> B(#120) -> C(#121) -> D(#124) -> E(#123); feat-foundry-system-state-snapshot, AC-SSS-1..8).

A deterministic, host-side, read-only inventory of the live system's RESOLVED persisted-schema
+ module surface, resolved from an adopter-pluggable `grounding` block in
`.claude/foundry-project.json`. This module only PRODUCES the snapshot; every consumer
(the audit binder, the frozen `system_grounding` contract block, the authoring survey, the
drift sweep) is a separate atom.

Prior-art grounding (see the spec's "Prior art / industry grounding" section): schema state is
read from the migration tool's own machine-readable RESOLVED-STATE artifact (Drizzle Kit's
`meta/NNNN_snapshot.json`, or foundry's own tool-agnostic `schema-json` form) — the *net current
state* with renames/drops already applied — never a hand-rolled union-scan of raw `CREATE TABLE`
statements (a raw scan cannot resolve a later `RENAME`/`DROP` and therefore reports dropped/
renamed tables as live forever; verified on the real origin adopter: 94 scanned vs. 27 resolved).
The module dimension (`fs-tree`) mirrors the npm/Yarn/pnpm/Cargo/Nx workspace-discovery
convention: one-level child-directory enumeration under a declared root, dot-prefixed/
non-directory entries excluded, no manifest-content parsing.

Configuration + error contract (mirrors `foundry_project_config.py`'s reader idiom — ER #111):
  - `.claude/foundry-project.json` under the resolved project root (`project_dir` ->
    `$CLAUDE_PROJECT_DIR` -> cwd) is read; absent/unreadable/non-dict degrades to `{}`, NEVER
    raises (AC-SSS-2 unconfigured no-op).
  - The `grounding` block declares up to two INDEPENDENT sources — `schema_source` and
    `module_source` — each resolved per-source. `kind: "none"` and a wholly ABSENT source key
    both mean "that dimension is unconfigured". A PRESENT source object whose `kind` is missing/
    null/empty, or whose `kind` is unrecognized, or whose `path` is absent/unreadable/
    undecodable, or whose decoded artifact is structurally malformed (top-level or per-entry) —
    ALL of these are configured-but-BROKEN and raise the exported `GroundingSourceError`
    (AC-SSS-4 fail-closed): a broken configuration must never silently degrade to an empty
    snapshot (which would turn a downstream reality-divergence HALT into a vacuous pass).
  - `schema_source.path` / `module_source.path` are resolved project-root-relative and path-
    confined (defense-in-depth, mirroring `foundry_project_config._confine`): an ABSOLUTE path or
    one that escapes the project root via a leading `..` component is REJECTED — which, because
    there is no sensible "default" grounding path to fall back to (unlike the governance-path
    reader), surfaces as `GroundingSourceError` (folded into the AC-SSS-4c "path absent/
    unreadable" fail-closed case) rather than a silent warn-and-default.

Read-only (AC-SSS-5): every reader is a `json.load` over a committed, version-controlled file.
No DB driver import, no socket/HTTP call, no live database connection anywhere in this module.

stdlib-only — no third-party imports.
"""
import hashlib
import json
import os
import re
import sys

SCHEMA_VERSION = 1

_SCHEMA_READER_KINDS = ("drizzle-meta", "schema-json")
_MODULE_READER_KINDS = ("fs-tree",)

_SNAPSHOT_FN_RE = re.compile(r"^(\d+)_snapshot\.json$")


class GroundingSourceError(ValueError):
    """Raised when a grounding source is PRESENT but cannot yield a well-formed inventory
    (AC-SSS-4): a kind-less/unrecognized source, an absent/unreadable/undecodable path, or a
    structurally malformed resolved-state artifact (top-level or per-entry). Never raised for the
    unconfigured no-op path (absent `grounding` block, absent source key, or `kind: "none"`)."""


# ── project-config resolution (mirrors foundry_project_config.py's reader idiom) ──────────────


def _project_dir(project_dir=None):
    """Resolve the adopter project root: an explicit override, else CLAUDE_PROJECT_DIR, else cwd."""
    return project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def _load_project_config(project_dir=None):
    """Read `.claude/foundry-project.json` under the resolved project root. Absent, unreadable, or
    non-dict-decoding content degrades to `{}` (never raises) — the caller falls back to the
    fully-unconfigured no-op."""
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


def _confine_source_path(value, project_root, label):
    """Project-root-relative + `..`-escape-free path confinement for a grounding source `path`
    (defense-in-depth, mirroring `foundry_project_config._confine`). Unlike that reader (which has
    a safe static default to fall back to), a grounding source has no such default, so a rejected
    value raises `GroundingSourceError` — folded into the AC-SSS-4c "path absent/unreadable"
    fail-closed case rather than silently substituting a default."""
    if not isinstance(value, str) or not value.strip():
        raise GroundingSourceError(f"{label}.path is missing or blank")
    v = value.strip()
    if os.path.isabs(v):
        raise GroundingSourceError(
            f"{label}.path must be project-root-relative (absolute path rejected): {value!r}"
        )
    norm = os.path.normpath(v)
    parts = norm.split(os.sep)
    if norm == os.pardir or parts[0] == os.pardir:
        raise GroundingSourceError(f"{label}.path escapes the project root via '..': {value!r}")
    return os.path.join(project_root, norm)


# ── per-source kind resolution (AC-SSS-2 / AC-SSS-4a/b) ───────────────────────────────────────


def _source_kind(source, label):
    """Returns the source's `kind` string, or `None` when the dimension is UNCONFIGURED (source
    key wholly absent, or `kind: "none"`). Raises `GroundingSourceError` when the source is
    PRESENT but malformed: not an object, or a `kind` that is missing/null/empty (AC-SSS-4a) or
    not a string."""
    if source is None:
        return None
    if not isinstance(source, dict):
        raise GroundingSourceError(f"{label} is present but not an object: {source!r}")
    kind = source.get("kind")
    if kind == "none":
        return None
    if not kind:
        raise GroundingSourceError(
            f"{label} is present but 'kind' is missing/null/empty (a present-but-malformed "
            f"source is NOT the unconfigured path)"
        )
    if not isinstance(kind, str):
        raise GroundingSourceError(f"{label}.kind must be a string, got {type(kind).__name__}")
    return kind


# ── drizzle-meta reader (AC-SSS-1, AC-SSS-8) ───────────────────────────────────────────────────


def _latest_drizzle_snapshot_filename(meta_dir):
    """The latest `meta/NNNN_snapshot.json` filename, per the pinned chapeau rule: the snapshot
    named by the LAST entry of `meta/_journal.json` (Drizzle's own authoritative ordering) when
    the journal is present and well-formed; else the `*_snapshot.json` with the highest
    zero-padded numeric `NNNN` prefix. A present-but-malformed journal is structurally malformed
    (AC-SSS-4d) — it does NOT silently fall through to the filename scan."""
    journal_path = os.path.join(meta_dir, "_journal.json")
    if os.path.isfile(journal_path):
        try:
            with open(journal_path, encoding="utf-8") as f:
                journal = json.load(f)
        except Exception as e:
            raise GroundingSourceError(
                f"drizzle-meta _journal.json unreadable/undecodable ({journal_path}): {e}"
            ) from e
        if not isinstance(journal, dict):
            raise GroundingSourceError(f"drizzle-meta _journal.json is not an object ({journal_path})")
        entries = journal.get("entries")
        if not isinstance(entries, list) or not entries:
            raise GroundingSourceError(
                f"drizzle-meta _journal.json has no usable 'entries' list ({journal_path})"
            )
        last = entries[-1]
        idx = last.get("idx") if isinstance(last, dict) else None
        if not isinstance(idx, int) or isinstance(idx, bool) or idx < 0:
            raise GroundingSourceError(
                f"drizzle-meta _journal.json last entry has no usable integer 'idx' ({journal_path})"
            )
        fn = f"{idx:04d}_snapshot.json"
        if not os.path.isfile(os.path.join(meta_dir, fn)):
            raise GroundingSourceError(
                f"drizzle-meta _journal.json points at a missing snapshot file {fn!r} under {meta_dir}"
            )
        return fn

    candidates = []
    try:
        names = os.listdir(meta_dir)
    except Exception as e:
        raise GroundingSourceError(f"drizzle-meta 'meta' directory unreadable ({meta_dir}): {e}") from e
    for fn in names:
        m = _SNAPSHOT_FN_RE.match(fn)
        if m:
            candidates.append((int(m.group(1)), fn))
    if not candidates:
        raise GroundingSourceError(f"no '*_snapshot.json' files found under {meta_dir}")
    candidates.sort(key=lambda t: t[0])
    return candidates[-1][1]


def _read_drizzle_meta(path, project_root):
    """AC-SSS-1: reads the LATEST `meta/NNNN_snapshot.json` under the migrations root `path` and
    returns `{identifier: {"columns": [...], "identifier": identifier}}` — `identifier` is the
    Drizzle `tables` key VERBATIM (schema-qualified, e.g. `public.vehicles`), `columns` is
    `sorted(tables[key]["columns"].keys())` (Drizzle stores columns as an object keyed by name)."""
    resolved = _confine_source_path(path, project_root, "schema_source")
    if not os.path.isdir(resolved):
        raise GroundingSourceError(f"schema_source.path not found or not a directory: {path!r}")
    meta_dir = os.path.join(resolved, "meta")
    if not os.path.isdir(meta_dir):
        raise GroundingSourceError(f"drizzle-meta 'meta' directory not found under {path!r}")

    snap_fn = _latest_drizzle_snapshot_filename(meta_dir)
    snap_path = os.path.join(meta_dir, snap_fn)
    try:
        with open(snap_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise GroundingSourceError(f"drizzle-meta snapshot unreadable/undecodable ({snap_path}): {e}") from e

    if not isinstance(data, dict) or not isinstance(data.get("tables"), dict):
        raise GroundingSourceError(f"drizzle-meta snapshot at {snap_path} has no 'tables' object")

    entities = {}
    for key, val in data["tables"].items():
        if not isinstance(val, dict) or not isinstance(val.get("columns"), dict):
            raise GroundingSourceError(
                f"drizzle-meta table {key!r} at {snap_path} lacks a usable 'columns' object"
            )
        col_names = list(val["columns"].keys())
        if not all(isinstance(c, str) for c in col_names):
            raise GroundingSourceError(f"drizzle-meta table {key!r} has a non-string column name")
        entities[key] = {"columns": sorted(col_names), "identifier": key}
    return entities


# ── schema-json reader (AC-SSS-1) ──────────────────────────────────────────────────────────────


def _read_schema_json(path, project_root):
    """AC-SSS-1: reads foundry's tool-agnostic resolved-schema JSON
    `{"tables": {<ident>: {"columns": [<name>, ...]}, ...}}` — `identifier` = each `tables` key,
    `columns` = the sorted string list at `tables[key]["columns"]`."""
    resolved = _confine_source_path(path, project_root, "schema_source")
    if not os.path.isfile(resolved):
        raise GroundingSourceError(f"schema_source.path not found: {path!r}")
    try:
        with open(resolved, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise GroundingSourceError(f"schema-json unreadable/undecodable ({path!r}): {e}") from e

    if not isinstance(data, dict) or not isinstance(data.get("tables"), dict):
        raise GroundingSourceError(f"schema-json at {path!r} has no 'tables' object")

    entities = {}
    for key, val in data["tables"].items():
        cols = val.get("columns") if isinstance(val, dict) else None
        if not isinstance(cols, list) or not all(isinstance(c, str) for c in cols):
            raise GroundingSourceError(
                f"schema-json table {key!r} at {path!r} lacks a usable 'columns' list of strings"
            )
        entities[key] = {"columns": sorted(cols), "identifier": key}
    return entities


# ── fs-tree module reader (AC-SSS-6) ───────────────────────────────────────────────────────────


def _read_fs_tree(path, project_root):
    """AC-SSS-6: one-level child-directory enumeration under `module_source.path` (the source
    root), each reported RELATIVE to that root, dot-prefixed entries and non-directories
    excluded, sorted. Mirrors the npm/Cargo/Nx workspace-discovery convention — no manifest
    parsing, no recursive walk."""
    resolved = _confine_source_path(path, project_root, "module_source")
    if not os.path.isdir(resolved):
        raise GroundingSourceError(f"module_source.path not found or not a directory: {path!r}")
    try:
        names = os.listdir(resolved)
    except Exception as e:
        raise GroundingSourceError(f"module_source.path unreadable ({path!r}): {e}") from e
    modules = [
        name for name in names
        if not name.startswith(".") and os.path.isdir(os.path.join(resolved, name))
    ]
    return sorted(modules)


# ── the builder ─────────────────────────────────────────────────────────────────────────────────


def build_system_snapshot(project_dir=None):
    """AC-SSS-1/2/3/4/5/6/8. Resolves the `grounding` block from `.claude/foundry-project.json`
    under the resolved project root and returns the pinned snapshot dict:
    `schema_version` (int, snapshot FORMAT version, currently 1), `schema_grounded` (bool),
    `module_grounded` (bool), `grounding_configured` (`schema_grounded OR module_grounded`),
    `entities` (map identifier -> {"columns": [...sorted...], "identifier": <=key>}),
    `modules` (sorted list), and `signature` (see `canonical_serialize`/`compute_signature`).

    Raises `GroundingSourceError` iff a source is PRESENT but broken (AC-SSS-4); the fully
    unconfigured case (`grounding` absent, or every source unconfigured) NEVER raises and returns
    the fully-inert snapshot (AC-SSS-2)."""
    root = _project_dir(project_dir)
    cfg = _load_project_config(root)
    grounding = cfg.get("grounding")
    grounding = grounding if isinstance(grounding, dict) else {}

    schema_source = grounding.get("schema_source")
    module_source = grounding.get("module_source")

    schema_kind = _source_kind(schema_source, "grounding.schema_source")
    entities = {}
    schema_grounded = schema_kind is not None
    if schema_grounded:
        if schema_kind not in _SCHEMA_READER_KINDS:
            raise GroundingSourceError(
                f"grounding.schema_source.kind {schema_kind!r} is not a recognized reader "
                f"(shipped: {_SCHEMA_READER_KINDS})"
            )
        path = schema_source.get("path")
        if schema_kind == "drizzle-meta":
            entities = _read_drizzle_meta(path, root)
        else:
            entities = _read_schema_json(path, root)

    module_kind = _source_kind(module_source, "grounding.module_source")
    modules = []
    module_grounded = module_kind is not None
    if module_grounded:
        if module_kind not in _MODULE_READER_KINDS:
            raise GroundingSourceError(
                f"grounding.module_source.kind {module_kind!r} is not a recognized reader "
                f"(shipped: {_MODULE_READER_KINDS})"
            )
        modules = _read_fs_tree(module_source.get("path"), root)

    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "schema_grounded": schema_grounded,
        "module_grounded": module_grounded,
        "grounding_configured": schema_grounded or module_grounded,
        "entities": entities,
        "modules": modules,
    }
    snapshot["signature"] = compute_signature(snapshot)
    return snapshot


def canonical_serialize(snapshot):
    """AC-SSS-3's pinned canonical encoding: the `signature` key excluded from its own input,
    sorted keys, tight separators, ASCII escaping. This is the single encoding the
    byte-identical-across-runs guarantee (and `signature` itself) is defined against."""
    payload = {k: v for k, v in snapshot.items() if k != "signature"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def compute_signature(snapshot):
    """sha256 hex digest of `canonical_serialize(snapshot)` (signature-key-excluded)."""
    return hashlib.sha256(canonical_serialize(snapshot).encode("utf-8")).hexdigest()


# ── test-fixture helpers (throwaway temp trees only — shared by this module's own selftest() and
#    the drop-in doctor check's selftest()) ───────────────────────────────────────────────────


def _write_project_config(root, grounding):
    """Writes a throwaway `.claude/foundry-project.json` under `root` carrying `grounding`
    (or an empty `{}` document when `grounding` is None)."""
    d = os.path.join(root, ".claude")
    os.makedirs(d, exist_ok=True)
    doc = {"grounding": grounding} if grounding is not None else {}
    with open(os.path.join(d, "foundry-project.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f)


def _write_drizzle_fixture(migrations_root, snapshots, use_journal=True):
    """Writes a throwaway Drizzle-style `meta/` dir under `migrations_root`: one zero-padded
    `NNNN_snapshot.json` per entry of `snapshots` (in order, `{"tables": {...}}` each) and, when
    `use_journal`, a `_journal.json` whose last entry points at the FINAL snapshot — so `snapshots`
    models a real migration history (earlier entries can carry tables later dropped/renamed away,
    for AC-SSS-8). Returns `migrations_root`."""
    meta_dir = os.path.join(migrations_root, "meta")
    os.makedirs(meta_dir, exist_ok=True)
    entries = []
    for i, tables in enumerate(snapshots):
        fn = f"{i:04d}_snapshot.json"
        with open(os.path.join(meta_dir, fn), "w", encoding="utf-8") as f:
            json.dump({"tables": tables}, f)
        entries.append({"idx": i, "tag": f"{i:04d}_snap", "version": "7"})
    if use_journal:
        with open(os.path.join(meta_dir, "_journal.json"), "w", encoding="utf-8") as f:
            json.dump({"version": "7", "dialect": "postgresql", "entries": entries}, f)
    return migrations_root


def _write_fs_tree_fixture(root, dirnames, dotdirs=(), files=()):
    """Writes throwaway child directories `dirnames` (module candidates), dot-prefixed dirs
    `dotdirs` (excluded per AC-SSS-6), and plain `files` (excluded, non-directories) under `root`.
    Returns `root`."""
    os.makedirs(root, exist_ok=True)
    for name in dirnames:
        os.makedirs(os.path.join(root, name), exist_ok=True)
    for name in dotdirs:
        os.makedirs(os.path.join(root, name), exist_ok=True)
    for name in files:
        with open(os.path.join(root, name), "w", encoding="utf-8") as f:
            f.write("not a directory\n")
    return root


# ── this module's own selftest (a lightweight sanity pass; the exhaustive per-AC proof with the
#    frozen PASS tokens lives in scripts/foundry_checks/system-state-snapshot.py) ───────────────


def selftest():
    """A lightweight self-check of the builder's own core invariants over throwaway temp
    fixtures: the unconfigured no-op, drizzle-meta resolution + determinism, and fail-closed on a
    broken (unrecognized-kind) source. Returns 0 iff all hold. The exhaustive AC-SSS-1..8 proof
    with the frozen `AC-SSS-N ...: PASS` tokens is the drop-in doctor check's job
    (`scripts/foundry_checks/system-state-snapshot.py`), which imports and exercises this module."""
    import tempfile

    ok = True
    try:
        with tempfile.TemporaryDirectory() as tmp:
            _write_project_config(tmp, None)
            snap = build_system_snapshot(project_dir=tmp)
            ok = ok and snap["grounding_configured"] is False
            ok = ok and snap["entities"] == {} and snap["modules"] == []

        with tempfile.TemporaryDirectory() as tmp:
            mig_root = os.path.join(tmp, "migrations")
            _write_drizzle_fixture(mig_root, [{"public.widgets": {"columns": {"id": {}, "name": {}}}}])
            _write_project_config(tmp, {"schema_source": {"kind": "drizzle-meta", "path": "migrations"}})
            snap1 = build_system_snapshot(project_dir=tmp)
            snap2 = build_system_snapshot(project_dir=tmp)
            ok = ok and snap1["signature"] == snap2["signature"]
            ok = ok and canonical_serialize(snap1) == canonical_serialize(snap2)
            ok = ok and snap1["entities"].get("public.widgets", {}).get("columns") == ["id", "name"]
            ok = ok and snap1["entities"]["public.widgets"]["identifier"] == "public.widgets"

        with tempfile.TemporaryDirectory() as tmp:
            _write_project_config(tmp, {"schema_source": {"kind": "not-a-real-kind", "path": "x"}})
            try:
                build_system_snapshot(project_dir=tmp)
                ok = False
            except GroundingSourceError:
                pass
    except Exception:
        ok = False

    print("FOUNDRY-SYSTEM-SNAPSHOT-SELFTEST-GREEN" if ok else "FOUNDRY-SYSTEM-SNAPSHOT-SELFTEST-RED")
    return 0 if ok else 1


# ── CLI ─────────────────────────────────────────────────────────────────────────────────────────


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "--selftest":
        return selftest()
    try:
        snapshot = build_system_snapshot()
    except GroundingSourceError as e:
        print(f"GroundingSourceError: {e}", file=sys.stderr)
        return 1
    print(canonical_serialize(snapshot))
    return 0


if __name__ == "__main__":
    sys.exit(main())
