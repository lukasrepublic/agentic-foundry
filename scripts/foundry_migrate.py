#!/usr/bin/env python3
"""foundry_migrate.py -- artifact disposition and existing-project cutover (gn-14).

Preserve historical artifact bytes, accepted knowledge, provenance, restrictions, customization, and
unfinished obligations. Shared cutover happens once; individual setup happens per operator.

Foundry 2.0 moves *shared planning* to GitHub Issues. It does not convert history and does not rewrite
it. This tool makes the move concrete and reviewable, in steps that each leave something to inspect:

  inventory [--json] [--offline]
      Every artifact the disposition table names, with one of four classes (current-knowledge,
      active-work, retained-history, runtime-residue) and the rule that produced it; every active item
      with its migration status and eligibility flags; the recovery diagnostics. Writes nothing.
  preview [--repo OWNER/NAME] [--select IDS] [--owner L] [--priority P] [--json] [--offline]
      For each selected item: the issue that would be created, the sidecar that would be written, and
      the instruction sentences that still claim specs/manifests are the planning authority, each with
      a before/after proposal. Writes nothing.
  cutover [--apply [--dry-run]] --repo OWNER/NAME --select IDS [--owner L] [--priority P]
          [--project TITLE] [--override-flag ITEM=FLAG ...] [--reopen] [--json] [--offline]
      Without --apply (or with --apply --dry-run) prints the plan and the preflight verdicts and writes
      nothing. With --apply: creates or adopts one issue per selected item (marker lookup over the full
      paginated listing), writes .foundry/migration/**, and APPENDS one `migrated_to:` entry to each
      selected manifest -- the only edit made to anything that existed before the run. Never commits.
  cutover record --operator OP_ID --scope TEXT [--reconciled PATH ...] [--acknowledge-empty] [--dry-run]
      Writes .foundry/migration/authority.yaml (exclusive create). Only a registered operator, naming
      themselves, can do it; the tool never infers adoption.

GitHub scope: `cutover` (every form: it reads and writes GitHub) requires a well-formed `github_scope`
and refuses with `github-scope-missing` before any gh call. `inventory` and `preview` are read-only and
need no scope.

Exit: 0 ok, 1 refused/failed (reason tokens are printed), 2 usage / mode gate.
"""
from __future__ import annotations

import argparse
import fcntl
import fnmatch
import json
import os
import posixpath
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import yaml  # noqa: E402

import foundry_yaml_load as yl  # noqa: E402
import foundry_ticket_store as _ticket_store  # noqa: E402

TOOL_VERSION = "1"
SCHEMA_VERSION = 1

ACTIVE = {"planned", "active", "proposed", "backlog", "in_progress", "in-progress",
          "partially-released", "partially-merged"}
DONE = {"completed", "released", "done", "closed-superseded"}
SEG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")
PRIORITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,31}$")
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+$")
# Guard pattern G (any run of hashes: the ticket tool's heading regexes take `##+`). The spec quotes `(?i)^\s{0,3}(#{1,6}\s+)?(done means|paper allowed)`; this is a strict
# superset of it (optional whitespace after the hashes, any run of blanks inside the phrase), so it also
# covers every heading the ticket tool's own `^##+[ \t]*done[ \t]+means\b` / paper-allowed regexes match.
GUARD_G = re.compile(r"(?i)^\s{0,3}(#+[ \t]*)?(?:" + _ticket_store.DONE_HEADING_ALT + "|" + _ticket_store.PAPER_HEADING_ALT + ")")
MARKER_ANY = re.compile(r"foundry-migrated:")
FLAG_ORDER = ("likely-shipped", "coupled", "program-closed", "program-unresolved")
FLOOR_PHRASES = ("front-authoriz", "merge floor", "merge-floor", "security review", "security-review",
                 "sign-off", "signoff", "branch protection")
INSTRUCTION_FILES = ("CLAUDE.md", "AGENTS.md", "WORKFLOW.md", "CONSTITUTION.md")
CUR3_FILES = INSTRUCTION_FILES + (".claude/settings.json",)
CUR3_PREFIXES = (".claude/hooks/", ".github/workflows/")
MIG_DIR = ".foundry/migration"
TRUSTED_ASSOC = {"OWNER", "MEMBER", "COLLABORATOR"}


class Killed(BaseException):
    """Test-only simulated kill (see Options.crash_after). BaseException so no handler swallows it."""


@dataclass
class Options:
    """Test-only seams, passed to main() by the tests. Never read from env or CLI by production code."""
    cwd: str | None = None
    plugin_root: str | None = None
    crash_after: int | None = None          # kill after completion step N (1..4) of the first item
    manifest_append_hook: object = None     # callable(bytes) -> bytes; corrupts the appended write


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------------------------------ helpers
def _run(argv, cwd=None, stdin=None):
    try:
        p = subprocess.run(argv, cwd=cwd, input=stdin, capture_output=True, text=True, shell=False,
                           timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)
    return p.returncode, p.stdout, p.stderr


def inside(root: str, path: str) -> bool:
    r = os.path.realpath(root)
    p = os.path.realpath(path)
    return p == r or p.startswith(r + os.sep)


def norm_rel(ref):
    """Normalized workspace-relative posix path, or None when it escapes / is not a path."""
    if not isinstance(ref, str) or not ref or "\x00" in ref or ref.startswith("/"):
        return None
    n = posixpath.normpath(ref)
    if n == "." or n == ".." or n.startswith("../"):
        return None
    return n


def dump_yaml(data) -> str:
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)


def ensure_dir(ws: str, d: str):
    """makedirs, but containment is decided on the nearest existing ancestor BEFORE anything is created,
    and the migration directory itself may never be a symlink."""
    if os.path.islink(os.path.join(ws, MIG_DIR)):
        raise PermissionError(f"{MIG_DIR} is a symlink")
    cur = d
    while not os.path.lexists(cur):
        cur = os.path.dirname(cur)
    if not os.path.isdir(cur) or not inside(ws, cur):
        raise PermissionError(f"refusing to create a directory outside the workspace: {d}")
    os.makedirs(d, exist_ok=True)
    if not inside(ws, d):
        raise PermissionError(f"refusing to use a directory outside the workspace: {d}")


def atomic_write(ws: str, rel: str, data: bytes, mode_from: str | None = None):
    target = os.path.join(ws, rel)
    parent = os.path.dirname(target)
    ensure_dir(ws, parent)
    if not inside(ws, parent) or (os.path.lexists(target) and not inside(ws, target)):
        raise PermissionError(f"refusing to write outside the workspace: {rel}")
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".migrate-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if mode_from and os.path.exists(mode_from):
            os.chmod(tmp, stat.S_IMODE(os.stat(mode_from).st_mode))
        else:
            os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load_yaml_rel(ws: str, rel: str):
    """(data, None) or (None, reason). Confined to the workspace."""
    path = os.path.join(ws, rel)
    if not os.path.lexists(path):
        return None, "missing"
    if not inside(ws, path):
        return None, "outside-workspace"
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        return yl.safe_load(raw), None
    except (OSError, yaml.YAMLError, UnicodeDecodeError, ValueError) as exc:
        return None, f"unparseable: {type(exc).__name__}"


def read_text_rel(ws: str, rel: str):
    path = os.path.join(ws, rel)
    if not inside(ws, path):
        return None
    try:
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return None


# ------------------------------------------------------------------------------------------- context
class RunState:
    def __init__(self, ws: str, opts: Options):
        self.ws = ws
        self.opts = opts
        self.plugin_root = opts.plugin_root or str(HERE.parent)
        self._head = None
        self._slug = False

    def git(self, *args, stdin=None):
        return _run(["git", "--no-optional-locks", "-C", self.ws, *args], stdin=stdin)

    @property
    def head(self):
        if self._head is None:
            rc, out, _ = self.git("rev-parse", "HEAD")
            self._head = out.strip() if rc == 0 else ""
        return self._head

    @property
    def workspace(self):
        """owner/repo of the origin remote, or None."""
        if self._slug is False:
            rc, out, _ = self.git("remote", "get-url", "origin")
            self._slug = None
            if rc == 0:
                m = re.search(r"[/:]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$", out.strip())
                if m:
                    self._slug = f"{m.group(1)}/{m.group(2)}"
        return self._slug


def workspace_root(opts: Options):
    cwd = opts.cwd or os.getcwd()
    rc, out, _ = _run(["git", "-C", cwd, "rev-parse", "--show-toplevel"])
    if rc != 0 or not out.strip():
        return None
    return os.path.realpath(out.strip())


# --------------------------------------------------------------------------------------- inventory
@dataclass
class Manifest:
    rel: str
    dirname: str
    kind: str                     # release.yaml | program.yaml
    data: dict | None = None
    state: str | None = None
    ok: bool = False
    reason: str | None = None
    entries: list = field(default_factory=list)   # (kind, id, entry)


@dataclass
class Item:
    id: str
    rid: str
    kind: str                     # atom | coupled | release
    entry: dict
    manifest: Manifest
    flags: list = field(default_factory=list)


@dataclass
class Inventory:
    paths: list = field(default_factory=list)       # (path, class, rule)
    unreadable: list = field(default_factory=list)  # (path, reason)
    items: dict = field(default_factory=dict)
    manifests: dict = field(default_factory=dict)   # (dir, kind) -> Manifest
    residue_ok: bool = True


def _load_manifest(ws: str, dirname: str, kind: str) -> Manifest:
    rel = f".foundry/releases/{dirname}/{kind}"
    m = Manifest(rel=rel, dirname=dirname, kind=kind)
    data, err = load_yaml_rel(ws, rel)
    if err:
        m.reason = err
        return m
    if not isinstance(data, dict):
        m.reason = "not-a-mapping"
        return m
    m.data = data
    st = data.get("state")
    if not isinstance(st, str) or (st not in ACTIVE and st not in DONE):
        m.reason = f"state-not-active-or-done: {st!r}"
        return m
    m.state = st
    mid = data.get("id")
    if mid != dirname:
        m.reason = "id-differs-from-directory"
        return m
    if not SEG_RE.match(dirname):
        m.reason = "bad-id-segment"
        return m
    if kind == "release.yaml" and st in ACTIVE:
        seen = set()
        for lst, label in (("atoms", "atom"), ("coupled_reauthorization", "coupled")):
            raw = data.get(lst)
            if raw is None:
                continue
            if not isinstance(raw, list):
                m.reason = f"{lst}-not-a-list"
                return m
            for e in raw:
                eid = e.get("id") if isinstance(e, dict) else None
                if not isinstance(eid, str) or not SEG_RE.match(eid):
                    m.reason = "bad-id-segment"
                    return m
                full = f"{dirname}/{eid}"
                if full in seen:
                    m.reason = "duplicate-item-id"
                    return m
                seen.add(full)
                m.entries.append((label, full, e))
        if not m.entries:
            m.entries.append(("release", dirname, {}))
    m.ok = True
    return m


def _walk(ws: str):
    files = []
    for name in CUR3_FILES:
        p = os.path.join(ws, name)
        if os.path.lexists(p) and not os.path.isdir(p):
            files.append(name)
    unreadable = []
    for root in ("specs", ".foundry", "docs", "status-reports", ".claude/hooks", ".github/workflows"):
        base = os.path.join(ws, root)
        if not os.path.isdir(base):
            continue
        if not inside(ws, base):
            unreadable.append((root, "outside-workspace"))
            continue
        for dp, dns, fns in os.walk(base):
            dns.sort()
            for fn in sorted(fns):
                rel = os.path.relpath(os.path.join(dp, fn), ws).replace(os.sep, "/")
                files.append(rel)
    return sorted(set(files)), unreadable


def _catalogue(rs: RunState):
    path = os.path.join(rs.plugin_root, "cli", "retired-artifacts.json")
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        ents = doc["entries"]
        out = []
        for e in ents:
            if isinstance(e, dict) and isinstance(e.get("path"), str):
                out.append((e["path"], e.get("kind", "file")))
        return out
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _catalogue_matches(ws: str, entries):
    """Existing workspace paths matched by catalogue entries -> {rel: kind}."""
    hits = {}
    for path, kind in entries:
        n = norm_rel(path)
        if n is None:
            continue
        d, base = posixpath.split(n)
        if "*" in base:
            full = os.path.join(ws, d) if d else ws
            try:
                names = sorted(os.listdir(full))
            except OSError:
                continue
            for nm in names:
                if fnmatch.fnmatchcase(nm, base):
                    hits[posixpath.join(d, nm) if d else nm] = kind
        elif os.path.lexists(os.path.join(ws, n)):
            hits[n] = kind
    return hits


def build_inventory(rs: RunState) -> Inventory:
    ws = rs.ws
    inv = Inventory()
    files, unread = _walk(ws)
    inv.unreadable.extend(unread)

    # manifests
    rel_root = os.path.join(ws, ".foundry", "releases")
    if os.path.isdir(rel_root) and inside(ws, rel_root):
        for d in sorted(os.listdir(rel_root)):
            if not os.path.isdir(os.path.join(rel_root, d)):
                continue
            for kind in ("release.yaml", "program.yaml"):
                if os.path.lexists(os.path.join(rel_root, d, kind)):
                    m = _load_manifest(ws, d, kind)
                    inv.manifests[(d, kind)] = m
                    if not m.ok:
                        inv.unreadable.append((m.rel, m.reason))

    # frozen contracts + their specs (R-HIS-3)
    frozen = set()
    bad = set()
    for rel in files:
        if rel.startswith("specs/") and posixpath.basename(rel) == "acceptance-contract.yaml":
            txt = read_text_rel(ws, rel)
            if txt is None or "authorized:" not in txt:
                continue
            data, err = load_yaml_rel(ws, rel)
            if err:
                inv.unreadable.append((rel, err))
                bad.add(rel)
                continue
            if isinstance(data, dict) and isinstance(data.get("authorized"), dict):
                frozen.add(rel)
                sr = norm_rel(data.get("spec_ref"))
                if sr:
                    frozen.add(sr)

    cat = _catalogue(rs)
    if cat is None:
        inv.residue_ok = False
        inv.unreadable.append(("cli/retired-artifacts.json", "residue-catalogue-unavailable"))
        cat = []
    res_hits = _catalogue_matches(ws, cat)
    res_dirs = [p for p, k in res_hits.items() if k == "dir"]

    def classify(rel):
        parts = rel.split("/")
        if rel.startswith(MIG_DIR + "/"):
            return "R-MIG-1", "current-knowledge"
        if len(parts) >= 4 and parts[0] == ".foundry" and parts[1] == "releases":
            d = parts[2]
            if len(parts) == 4 and parts[3] in ("release.yaml", "program.yaml"):
                m = inv.manifests.get((d, parts[3]))
                if m is None or not m.ok:
                    return None, None
                return (("R-ACT-1", "active-work") if m.state in ACTIVE else ("R-HIS-1", "retained-history"))
            rm = inv.manifests.get((d, "release.yaml"))
            if rm is not None and rm.ok and rm.state in ACTIVE:
                return "R-CUR-4", "current-knowledge"
            return "R-HIS-2", "retained-history"
        if rel in bad:
            return None, None
        if rel in frozen:
            return "R-HIS-3", "retained-history"
        if (rel.startswith("specs/charters/") or rel.startswith(".foundry/evidence/")
                or rel.startswith("status-reports/")
                or (len(parts) == 2 and parts[0] == ".foundry" and rel.endswith(".jsonl"))):
            return "R-HIS-4", "retained-history"
        if parts[0] == "specs" and (rel.endswith(".md") or posixpath.basename(rel) == "acceptance-contract.yaml"):
            return "R-CUR-1", "current-knowledge"
        if rel.startswith("docs/") or rel.startswith(".foundry/decisions/"):
            return "R-CUR-2", "current-knowledge"
        if rel in CUR3_FILES or any(rel.startswith(p) for p in CUR3_PREFIXES):
            return "R-CUR-3", "current-knowledge"
        return None, None

    unreadable_paths = {p for p, _ in inv.unreadable}
    for rel in files:
        if rel in unreadable_paths:
            continue
        rule, cls = classify(rel)
        if rule:
            inv.paths.append((rel, cls, rule))
            continue
        if any(rel == dpath or rel.startswith(dpath + "/") for dpath in res_dirs):
            continue                      # covered by its directory row
        if rel in res_hits and res_hits[rel] != "dir":
            inv.paths.append((rel, "runtime-residue", "R-RES-1"))
    have = {p for p, _, _ in inv.paths}
    for rel, kind in sorted(res_hits.items()):
        if rel in have or rel in unreadable_paths:
            continue
        full = os.path.join(ws, rel)
        if kind == "dir" and os.path.isdir(full) and not os.path.islink(full):
            inv.paths.append((rel, "runtime-residue", "R-RES-1"))
        elif kind != "dir" and not os.path.isdir(full):
            inv.paths.append((rel, "runtime-residue", "R-RES-1"))
    inv.paths.sort()
    inv.unreadable.sort()

    # items + flags
    for (d, kind), m in sorted(inv.manifests.items()):
        if kind != "release.yaml" or not m.ok or m.state not in ACTIVE:
            continue
        gates = m.data.get("gates") if isinstance(m.data.get("gates"), dict) else {}
        shipped_gate = any(str(gates.get(k)).lower() in ("done", "passed") for k in ("merge", "release_cut"))
        prog = m.data.get("program")
        for label, iid, entry in m.entries:
            it = Item(id=iid, rid=d, kind=label, entry=entry, manifest=m)
            fl = set()
            if label == "atom" and shipped_gate and _contract_authorized(ws, entry):
                fl.add("likely-shipped")
            if label == "coupled":
                fl.add("coupled")
            if prog is not None:
                pm = inv.manifests.get((prog, "program.yaml")) if isinstance(prog, str) and SEG_RE.match(prog) else None
                if pm is not None and pm.ok:
                    if pm.state in DONE:
                        fl.add("program-closed")
                else:
                    fl.add("program-unresolved")
            it.flags = [f for f in FLAG_ORDER if f in fl]
            inv.items[iid] = it
    return inv


def _contract_authorized(ws: str, entry: dict) -> bool:
    ref = entry.get("contract_ref")
    if not isinstance(ref, str):
        sr = entry.get("spec_ref")
        if not isinstance(sr, str):
            return False
        ref = posixpath.join(posixpath.dirname(sr), "acceptance-contract.yaml")
    n = norm_rel(ref)
    if n is None:
        return False
    data, err = load_yaml_rel(ws, n)
    return err is None and isinstance(data, dict) and isinstance(data.get("authorized"), dict)


# ------------------------------------------------------------------------------- migration records
def index_path():
    return f"{MIG_DIR}/index.yaml"


def sidecar_rel(item_id: str) -> str:
    return f"{MIG_DIR}/items/{item_id}.yaml"


def load_index(ws: str):
    """(index|None, err|None). A missing index is (None, None)."""
    if not os.path.lexists(os.path.join(ws, index_path())):
        return None, None
    data, err = load_yaml_rel(ws, index_path())
    if err:
        return None, err
    if not isinstance(data, dict):
        return None, "not-a-mapping"
    if not isinstance(data.get("items"), dict):
        data["items"] = {}
    return data, None


def manifest_migrated_to(inv: Inventory, item_id: str):
    rid = item_id.split("/")[0]
    m = inv.manifests.get((rid, "release.yaml"))
    if m is None or not m.ok:
        return None
    mt = m.data.get("migrated_to")
    if isinstance(mt, dict) and item_id in mt:
        return mt[item_id]
    return None


def item_status(inv: Inventory, idx, item_id: str) -> str:
    ent = (idx or {}).get("items", {}).get(item_id) if idx else None
    ent = ent if isinstance(ent, dict) else {}
    if ent.get("status") == "migrated" or manifest_migrated_to(inv, item_id) is not None:
        return "migrated"
    if ent.get("status") == "pending":
        return "pending"
    return "unmigrated"


def item_url(inv, idx, item_id):
    ent = ((idx or {}).get("items", {}) or {}).get(item_id)
    if isinstance(ent, dict) and ent.get("issue_url"):
        return ent["issue_url"]
    u = manifest_migrated_to(inv, item_id)
    return u if isinstance(u, str) else None


def load_sidecar(ws, item_id):
    rel = sidecar_rel(item_id)
    if not os.path.lexists(os.path.join(ws, rel)):
        return None, None
    d, err = load_yaml_rel(ws, rel)
    if err or not isinstance(d, dict):
        return None, err or "not-a-mapping"
    return d, None


def authority_state(rs: RunState):
    """('absent'|'present'|'unreadable', detail)."""
    rel = f"{MIG_DIR}/authority.yaml"
    p = os.path.join(rs.ws, rel)
    if not os.path.lexists(p):
        return "absent", None
    data, err = load_yaml_rel(rs.ws, rel)
    if err:
        return "unreadable", err
    need = ("schema_version", "recorded_by", "at", "planning_authority", "scope",
            "instructions_reconciled", "workspace", "head")
    if not isinstance(data, dict):
        return "unreadable", "empty-or-not-a-mapping"
    for k in need:
        if k not in data or data[k] is None and k != "instructions_reconciled":
            return "unreadable", f"missing-field:{k}"
    if data.get("workspace") != rs.workspace:
        return "unreadable", "foreign-workspace"
    if data.get("recorded_by") not in operators(rs.ws):
        return "unreadable", "recorded-by-not-registered"
    return "present", None


# ------------------------------------------------------------------------------------------- gh
READ_PROJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")


def read_allowed(argv) -> bool:
    if argv == ["api", "user"]:
        return True
    if len(argv) == 5 and argv[:2] == ["repo", "view"] and argv[3:] == ["--json", "visibility"]:
        return REPO_RE.match(argv[2]) is not None
    if len(argv) == 3 and argv[:2] == ["api", "--paginate"]:
        return re.match(r"^repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues\?state=all&per_page=100$", argv[2]) is not None
    if len(argv) == 6 and argv[:3] == ["project", "list", "--owner"] and argv[4:] == ["--format", "json"]:
        return READ_PROJECT.match(argv[3]) is not None
    return False


class Gh:
    def __init__(self, read_only: bool):
        self.read_only = read_only

    def run(self, argv, stdin=None):
        if self.read_only and not read_allowed(argv):
            raise AssertionError(f"gh argv outside the read allowlist: {argv!r}")
        return _run(["gh", *argv], stdin=stdin)


def parse_pages(text: str):
    dec = json.JSONDecoder()
    i, n, out, seen = 0, len(text), [], False
    while True:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        obj, i = dec.raw_decode(text, i)
        if not isinstance(obj, list) or not all(isinstance(x, dict) for x in obj):
            raise ValueError("page is not an array of objects")
        out.extend(obj)
        seen = True
    if not seen:
        raise ValueError("empty listing")
    return out


def fetch_issues(gh: Gh, repo: str):
    rc, out, err = gh.run(["api", "--paginate", f"repos/{repo}/issues?state=all&per_page=100"])
    if rc != 0:
        return None, "listing exited non-zero"
    try:
        issues = parse_pages(out)
    except (ValueError, json.JSONDecodeError) as exc:
        return None, f"unparseable listing: {exc}"
    return [i for i in issues if "pull_request" not in i], None


def issue_number(url, repo=None):
    m = re.match(r"^https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/(\d+)$", url or "")
    if not m or (repo and m.group(1) != repo):
        return None
    return int(m.group(2))


def bears_marker(issue: dict, marker: str) -> bool:
    body = issue.get("body")
    if not isinstance(body, str):
        return False
    return any(line == marker for line in re.split(r"\r\n|\n|\r", body))


def adoptable(issue, login, reopen):
    """(ok, token). token is the refusal token when not ok.

    Trust choice (fix round, deliberately STRICTER than the spec's OWNER/MEMBER/COLLABORATOR rule): a
    marker issue is adopted only when its author IS the authenticated `gh` login. The ticket tool trusts
    write/maintain/admin, but reading a collaborator permission is not in the AC-CUT-3 gh read allowlist
    and this tool adds no call, so the association is not trusted at all. The refusal keeps the spec's
    token `marker-issue-untrusted` and carries the detail `adoption-untrusted`."""
    user = (issue.get("user") or {}).get("login")
    if not (login and user == login):
        return False, "marker-issue-untrusted"
    if issue.get("state") == "closed" and issue.get("state_reason") in ("not_planned", "duplicate") and not reopen:
        return False, "marker-issue-closed-not-planned"
    return True, None


# ------------------------------------------------------------------------------- issue body / plan
def spec_h1_and_acs(rs: RunState, spec_ref: str):
    rc, out, _ = rs.git("show", f"HEAD:{spec_ref}")
    text = out if rc == 0 else ""
    h1 = None
    for line in text.splitlines():
        if line.startswith("# "):
            h1 = line[2:].strip()
            break
    acs = []
    m = re.search(r"<!--\s*normative\s*-->(.*?)<!--\s*/normative\s*-->", text, re.S)
    if m:
        cur = None
        for line in m.group(1).splitlines():
            if re.match(r"^\s*[-*]\s+\*\*AC-[A-Za-z0-9]+-\d+[a-z]?\*\*", line):
                cur = [line]
                acs.append(cur)
            elif cur is not None and (not line.strip() or line[0] in " \t"):
                cur.append(line)
            elif cur is not None:
                # a non-continuation line ends the AC; a line the guard would refuse is kept in the body so
                # the item is refused (body-guard) instead of the heading being silently dropped
                if GUARD_G.match(line) or MARKER_ANY.search(line):
                    cur.append(line)
                cur = None
        acs = ["\n".join(a).rstrip() for a in acs]
    return h1, acs


def blob_link(rs: RunState, rel: str) -> str:
    return f"https://github.com/{rs.workspace}/blob/{rs.head}/{quote(rel)}"


def marker_for(rs: RunState, item_id: str) -> str:
    return f"<!-- foundry-migrated: {rs.workspace}/{item_id} -->"


def dep_ids(inv: Inventory, item: Item):
    """(resolved ids, invalid?) for depends_on; invalid when an entry names an unknown atom."""
    raw = item.entry.get("depends_on")
    if raw is None:
        return [], False
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return [], True
    out, bad = [], False
    for d in raw:
        if not isinstance(d, str):
            bad = True
            continue
        full = d if "/" in d else f"{item.rid}/{d}"
        if full in inv.items:
            out.append(full)
        else:
            bad = True
    return out, bad


def dep_release_ids(item: Item):
    raw = item.entry.get("depends_on_release", item.manifest.data.get("depends_on_release")
                         if item.kind == "release" else None)
    if raw is None:
        return [], False
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not all(isinstance(r, str) and SEG_RE.match(r) for r in raw):
        return [], True
    return raw, False


def dependency_problem(inv: Inventory, item: Item):
    """True when depends_on names an unknown atom or the (transitive) entries form a cycle."""
    _, bad = dep_ids(inv, item)
    _, bad2 = dep_release_ids(item)
    if bad or bad2:
        return True
    state = {}

    def visit(i):
        st = state.get(i.id)
        if st == 1:
            return True
        if st == 2:
            return False
        state[i.id] = 1
        ds, b = dep_ids(inv, i)
        if b:
            return True
        for d in ds:
            if visit(inv.items[d]):
                return True
        state[i.id] = 2
        return False

    return visit(item)


@dataclass
class Plan:
    item: Item
    title: str = ""
    owner: str | None = None
    priority: str = "unset"
    body: str = ""
    marker: str = ""
    sidecar: dict = field(default_factory=dict)
    problems: list = field(default_factory=list)
    blocked_by: list = field(default_factory=list)
    context_links: list = field(default_factory=list)
    acceptance: list = field(default_factory=list)
    dependencies: list = field(default_factory=list)


def guard_body(body: str, marker: str) -> bool:
    """True when the body meets AC-CUT-6."""
    all_lines = body.split("\n") + body.splitlines()
    if any(GUARD_G.match(l) for l in all_lines):
        return False
    eq = [l for l in re.split(r"\r\n|\n|\r", body) if l == marker]
    anym = [l for l in re.split(r"\r\n|\n|\r", body) if MARKER_ANY.search(l)]
    return len(eq) == 1 and len(anym) == 1


def build_plan(rs: RunState, inv: Inventory, idx, item: Item, args, overrides, live=None) -> Plan:
    """Static, gh-free assembly of one item's proposal. `live` maps item id -> url for migrated-this-run."""
    live = live or {}
    pl = Plan(item=item)
    e, man = item.entry, item.manifest.data
    pl.marker = marker_for(rs, item.id)

    def pick(key):
        for src in (e, man):
            if isinstance(src, dict) and src.get(key) is not None:
                return src[key]
        return None

    owner = pick("owner")
    owner = owner if owner is not None else getattr(args, "owner", None)
    pl.owner = owner if isinstance(owner, str) else None
    if pl.owner is None or not OWNER_RE.match(pl.owner):
        pl.problems.append("owner-invalid")
    prio = pick("priority")
    prio = prio if prio is not None else getattr(args, "priority", None)
    prio = "unset" if prio is None else prio
    pl.priority = prio if isinstance(prio, str) else str(prio)
    if not isinstance(prio, str) or not PRIORITY_RE.match(prio):
        pl.problems.append("priority-invalid")

    refs = {}
    for k in ("spec_ref", "contract_ref", "charter_ref"):
        v = e.get(k)
        if v is None:
            continue
        refs[k] = v
    outside = missing = False
    clean = {}
    for k, v in refs.items():
        n = norm_rel(v)
        if n is None or not inside(rs.ws, os.path.join(rs.ws, n)):
            outside = True
            continue
        rc, _, _ = rs.git("cat-file", "-e", f"HEAD:{n}")
        if rc != 0:
            missing = True
            continue
        clean[k] = n
    if outside:
        pl.problems.append("source-outside-workspace")
    elif missing:
        pl.problems.append("source-missing")

    if dependency_problem(inv, item):
        pl.problems.append("dependency-invalid")
    mt = item.manifest.data.get("migrated_to")
    if "migrated_to" in item.manifest.data and list(item.manifest.data)[-1] != "migrated_to":
        pl.problems.append("migrated-to-not-last")
    elif mt is not None and not isinstance(mt, dict):
        pl.problems.append("migrated-to-not-last")

    # title / acceptance
    h1 = None
    if "spec_ref" in clean:
        h1, acs = spec_h1_and_acs(rs, clean["spec_ref"])
        pl.acceptance = acs
    else:
        pl.acceptance = ["No spec recorded for this item."]
    title = item.id if not h1 else f"{item.id}: {h1}"
    pl.title = title[:250]

    # dependencies
    deps, _ = dep_ids(inv, item)
    for d in deps:
        st = "migrated" if d in live else item_status(inv, idx, d)
        url = live.get(d) or (item_url(inv, idx, d) if st == "migrated" else None)
        pl.dependencies.append({"id": d, "status": st, "url": url})
        n = issue_number(url, getattr(args, "repo", None) or (idx or {}).get("target_repo")) if url else None
        if st == "migrated" and n:
            pl.blocked_by.append(n)
    rels, _ = dep_release_ids(item)
    rel_links = [blob_link(rs, f".foundry/releases/{r}/release.yaml") for r in rels]

    # context links
    srcs = [item.manifest.rel] + [clean[k] for k in ("spec_ref", "contract_ref", "charter_ref") if k in clean]
    pl.context_links = [blob_link(rs, s) for s in srcs] + rel_links

    lines = [pl.marker, "",
             "Migrated from a local planning record. The recorded state below is unverified; the owner "
             "reconciles real progress on GitHub.", "",
             "## Item", "",
             f"- Item: `{item.id}`",
             f"- Owner: {pl.owner}",
             f"- Priority: {pl.priority}",
             f"- Recorded state (unverified): {item.manifest.state}",
             f"- Flags: {', '.join(item.flags) if item.flags else 'none'}", ""]
    lines += ["## Dependencies", ""]
    if not pl.dependencies and not rel_links:
        lines.append("- none")
    for d in pl.dependencies:
        lines.append(f"- `{d['id']}`: {d['status']}" + (f" ({d['url']})" if d["url"] else ""))
    for l in rel_links:
        lines.append(f"- release context: {l}")
    lines += ["", "## Acceptance criteria", ""]
    lines += [a for a in pl.acceptance]
    lines += ["", "## Context links", ""]
    lines += [f"- {l}" for l in pl.context_links]
    pl.body = "\n".join(lines) + "\n"
    if not guard_body(pl.body, pl.marker):
        pl.problems.append("body-guard")

    pending = [d["id"] for d in pl.dependencies if d["status"] != "migrated"]
    pl.sidecar = {
        "schema_version": SCHEMA_VERSION, "tool_version": TOOL_VERSION, "id": item.id,
        "marker": pl.marker, "issue_url": "<assigned when the issue is created or adopted>",
        "migrated_at": "<set at apply>",
        "source": {"manifest": item.manifest.rel,
                   **{k: clean[k] for k in ("spec_ref", "contract_ref", "charter_ref") if k in clean},
                   "commit": rs.head},
        "context_links": list(pl.context_links), "pending_dependencies": pending,
        "flags_overridden": sorted(overrides.get(item.id, set()) & set(item.flags)),
    }
    return pl


def topo_order(inv: Inventory, ids):
    ids = list(ids)
    want = set(ids)
    out, done = [], set()

    def visit(i):
        if i in done:
            return
        done.add(i)
        ds, _ = dep_ids(inv, inv.items[i])
        for d in ds:
            if d in want:
                visit(d)
        out.append(i)

    for i in ids:
        visit(i)
    return out


# ------------------------------------------------------------------------------------ diagnostics
def closed_issue_check(rs, gh, idx, offline, issues=None):
    """-> (status dict, {url: issue} for the target repo or {})."""
    repo = (idx or {}).get("target_repo")
    migrated = [i for i, e in ((idx or {}).get("items") or {}).items()
                if isinstance(e, dict) and e.get("status") == "migrated"]
    if offline:
        return {"status": "skipped", "reason": "offline"}, {}
    if not repo:
        return {"status": "skipped", "reason": "target-repository-unknown"}, {}
    if not isinstance(repo, str) or not REPO_RE.match(repo):
        return {"status": "skipped", "reason": "target-repo-invalid"}, {}
    if not migrated:
        return {"status": "ok", "checked": 0}, {}
    if issues is None:
        issues, err = fetch_issues(gh, repo)
        if issues is None:
            return {"status": "skipped", "reason": f"gh-read-failed: {err}"}, {}
    return {"status": "ok", "checked": len(migrated)}, {i.get("html_url"): i for i in issues}


def diagnose(rs, inv, gh, offline, issues=None):
    ws = rs.ws
    idx, idx_err = load_index(ws)
    diags = []
    astate, adetail = authority_state(rs)
    if astate == "absent":
        diags.append({"name": "cutover-pending", "next_action": "record-cutover-when-decided"})
    elif astate == "unreadable":
        diags.append({"name": "authority-record-unreadable", "next_action": "repair-authority-record",
                      "detail": adetail})
    reactivate_next = {"absent": "restore-before-cutover", "present": "repair-after-cutover",
                       "unreadable": "repair-authority-record"}[astate]
    items = (idx or {}).get("items") or {}
    for iid, e in sorted(items.items()):
        if isinstance(e, dict) and e.get("status") == "pending":
            diags.append({"name": "migration-interrupted", "next_action": "rerun-cutover-apply", "item": iid})
    status, by_url = closed_issue_check(rs, gh, idx, offline, issues)
    for iid, e in sorted(items.items()):
        if not (isinstance(e, dict) and e.get("status") == "migrated"):
            continue
        reason = None
        if manifest_migrated_to(inv, iid) is None:
            reason = "no-migrated_to-entry"
        else:
            iss = by_url.get(e.get("issue_url"))
            # `duplicate` counts like `not_planned` (adoption refuses both): such an issue is not authority
            if iss and iss.get("state") == "closed" and iss.get("state_reason") in ("not_planned", "duplicate"):
                reason = "issue-closed-not-planned"
        if reason:
            diags.append({"name": "migrated-item-reactivated", "next_action": reactivate_next,
                          "item": iid, "detail": reason})
    # record consistency
    if idx_err:
        diags.append({"name": "migration-record-inconsistent", "next_action": "reconcile-migration-record",
                      "detail": f"index-unreadable: {idx_err}"})
    repo = (idx or {}).get("target_repo")
    man_map = {}
    for (d, kind), m in sorted(inv.manifests.items()):
        if kind == "release.yaml" and m.ok and isinstance(m.data.get("migrated_to"), dict):
            for k, v in m.data["migrated_to"].items():
                man_map[k] = v
    for iid in sorted(set(man_map) | {i for i, e in items.items() if isinstance(e, dict) and e.get("status") == "migrated"}):
        ent = items.get(iid) if isinstance(items.get(iid), dict) else {}
        if iid in man_map and ent.get("status") != "migrated":
            diags.append({"name": "migration-record-inconsistent", "next_action": "reconcile-migration-record",
                          "item": iid, "detail": "manifest-entry-without-index-record"})
        sc, _ = load_sidecar(ws, iid)
        urls = {u for u in (ent.get("issue_url"), (sc or {}).get("issue_url"), man_map.get(iid)) if u}
        if len(urls) > 1:
            diags.append({"name": "migration-record-inconsistent", "next_action": "reconcile-migration-record",
                          "item": iid, "detail": "index-sidecar-manifest-urls-differ"})
        for u in sorted(urls):
            if issue_number(u, repo) is None:
                diags.append({"name": "migration-record-inconsistent", "next_action": "reconcile-migration-record",
                              "item": iid, "detail": "url-not-an-issue-of-the-target-repository"})
                break
    versions = set()
    if idx is not None and idx.get("tool_version") != TOOL_VERSION:
        versions.add("index")
    for iid in items:
        sc, _ = load_sidecar(ws, iid)
        if sc is not None and sc.get("tool_version") != TOOL_VERSION:
            versions.add(f"sidecar:{iid}")
    if versions:
        diags.append({"name": "migration-tool-version-differs", "next_action": "align-migration-tool-version",
                      "detail": ",".join(sorted(versions))})
    return diags, status


def print_diags(diags, status, out=sys.stdout):
    print("Diagnostics:", file=out)
    for d in diags:
        extra = (f" item={d['item']}" if d.get("item") else "") + (f" ({d['detail']})" if d.get("detail") else "")
        print(f"  {d['name']}{extra} next-action={d['next_action']}", file=out)
    if not diags:
        print("  none", file=out)
    print(f"  closed-issue-check: {status['status']}" + (f" ({status['reason']})" if status.get("reason") else ""),
          file=out)


# ------------------------------------------------------------------------------ instruction proposals
def patterns_path():
    return HERE / "foundry_migrate_authority_patterns.json"


def load_patterns():
    with open(patterns_path(), encoding="utf-8") as fh:
        doc = json.load(fh)
    return [(e["id"], re.compile(e["match"], re.I), e["after"]) for e in doc["entries"]]


def sentences(text: str):
    """[(line, sentence)] per the spec's segmentation."""
    out = []
    buf, start = [], None
    in_fence = False

    def flush():
        nonlocal buf, start
        s = " ".join(" ".join(buf).split())
        if s:
            out.append((start, s))
        buf, start = [], None

    for ln, raw in enumerate(text.splitlines(), 1):
        if raw.lstrip().startswith("```"):
            flush()
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not raw.strip():
            flush()
            continue
        if re.match(r"^\s{0,3}#{1,6}\s", raw):
            flush()
            buf, start = [raw.strip()], ln
            flush()
            continue
        if raw.lstrip().startswith("|"):
            flush()
            buf, start = [raw.strip()], ln
            flush()
            continue
        m = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$", raw)
        line = raw
        if m:
            flush()
            line = m.group(1)
        pos = 0
        for t in re.finditer(r"[.?!](?=\s|$)", line):
            seg = line[pos:t.end()]
            if seg.strip():
                if start is None:
                    start = ln
                buf.append(seg.strip())
                flush()
            pos = t.end()
        rest = line[pos:]
        if rest.strip():
            if start is None:
                start = ln
            buf.append(rest.strip())
    flush()
    return out


def instruction_proposals(rs: RunState):
    pats = load_patterns()
    props = []
    for name in INSTRUCTION_FILES:
        path = os.path.join(rs.ws, name)
        if not os.path.isfile(path) or not inside(rs.ws, path):
            continue
        txt = read_text_rel(rs.ws, name) or ""
        for ln, s in sentences(txt):
            low = s.lower()
            if any(p in low for p in FLOOR_PHRASES):
                continue
            for pid, rx, after in pats:
                if rx.search(s):
                    props.append({"path": f"{name}:{ln}", "pattern": pid, "before": s, "after": after})
                    break
    return props


# -------------------------------------------------------------------------------------- subcommands
def parse_args(argv):
    ap = argparse.ArgumentParser(prog="foundry_migrate.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("inventory", "preview", "cutover"):
        p = sub.add_parser(name)
        p.add_argument("--json", action="store_true")
        p.add_argument("--offline", action="store_true")
        if name in ("preview", "cutover"):
            p.add_argument("--repo")
            p.add_argument("--select")
            p.add_argument("--owner")
            p.add_argument("--priority")
        if name == "cutover":
            p.add_argument("action", nargs="?", choices=["record"])
            p.add_argument("--apply", action="store_true")
            p.add_argument("--dry-run", action="store_true")
            p.add_argument("--project")
            p.add_argument("--override-flag", action="append", default=[])
            p.add_argument("--reopen", action="store_true")
            p.add_argument("--operator")
            p.add_argument("--scope")
            p.add_argument("--reconciled", action="append", default=[])
            p.add_argument("--acknowledge-empty", action="store_true")
    return ap.parse_args(argv)


def parse_select(s):
    out = []
    for part in (s or "").split(","):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return out


def parse_overrides(lst):
    out = {}
    for o in lst:
        if "=" not in o:
            raise ValueError(f"--override-flag expects ITEM=FLAG, got {o!r}")
        i, f = o.split("=", 1)
        out.setdefault(i, set()).add(f)
    return out


def cmd_inventory(rs, args, gh):
    inv = build_inventory(rs)
    idx, _ = load_index(rs.ws)
    diags, status = diagnose(rs, inv, gh, args.offline)
    rows = [{"path": p, "class": c, "rule": r} for p, c, r in inv.paths]
    items = [{"id": i, "rule": "R-ACT-1", "status": item_status(inv, idx, i), "flags": it.flags}
             for i, it in sorted(inv.items.items())]
    unread = [{"path": p, "reason": r} for p, r in inv.unreadable]
    if args.json:
        print(json.dumps({"tool_version": TOOL_VERSION, "paths": rows, "items": items, "unreadable": unread,
                          "diagnostics": diags, "closed_issue_check": status}, indent=2))
        return 0
    print(f"{'class':<18} {'rule':<8} path")
    for r in rows:
        print(f"{r['class']:<18} {r['rule']:<8} {r['path']}")
    print("\nItems:")
    for i in items:
        print(f"  {i['id']}  R-ACT-1  {i['status']}  flags={','.join(i['flags']) or '-'}")
    if unread:
        print("\nUnreadable:")
        for u in unread:
            print(f"  {u['path']}: {u['reason']}")
    print()
    print_diags(diags, status)
    return 0


def _item_report(pl: Plan):
    return {"id": pl.item.id, "title": pl.title, "owner": pl.owner, "priority": pl.priority,
            "dependencies": pl.dependencies, "acceptance": pl.acceptance, "context_links": pl.context_links,
            "recorded_state": f"{pl.item.manifest.state} (unverified)", "flags": pl.item.flags,
            "blocked_by": pl.blocked_by, "body": pl.body, "sidecar": pl.sidecar, "problems": pl.problems}


def cmd_preview(rs, args, gh):
    if not rs.workspace or not rs.head:
        print("preview needs a git workspace with an origin remote and a HEAD commit", file=sys.stderr)
        return 2
    inv = build_inventory(rs)
    idx, _ = load_index(rs.ws)
    overrides = {}
    sel = parse_select(args.select)
    bad = [s for s in sel if s not in inv.items]
    if bad:
        for b in bad:
            print(f"REFUSED not-an-item item={b}")
        return 1
    reports = [_item_report(build_plan(rs, inv, idx, inv.items[s], args, overrides)) for s in sel]
    props = instruction_proposals(rs)
    diags, status = diagnose(rs, inv, gh, args.offline)
    if args.json:
        print(json.dumps({"items": reports, "instruction_conflicts": props, "diagnostics": diags,
                          "closed_issue_check": status}, indent=2))
        return 0
    for r in reports:
        print(f"== {r['id']}\ntitle: {r['title']}\nowner: {r['owner']}\npriority: {r['priority']}")
        print(f"recorded state: {r['recorded_state']}\nflags: {', '.join(r['flags']) or 'none'}")
        if r["problems"]:
            print(f"problems: {', '.join(r['problems'])}")
        print("--- issue body ---\n" + r["body"] + "--- sidecar ---\n" + dump_yaml(r["sidecar"]))
    print("Instruction conflicts:")
    for p in props:
        print(f"  {p['path']}\n    before: {p['before']}\n    after:  {p['after']}")
    if not props:
        print("  none")
    print()
    print_diags(diags, status)
    return 0


APPENDED_LINE = re.compile(r'^(migrated_to:|  "[^"\n]*": "[^"\n]*")$')


def pure_migrated_to_append(rs, rel) -> bool:
    """True when the working copy of `rel` is the HEAD blob plus appended migrated_to lines only."""
    rc, head, _ = rs.git("show", f"HEAD:{rel}")
    if rc != 0:
        return False
    try:
        with open(os.path.join(rs.ws, rel), "rb") as fh:
            work = fh.read().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    if not work.startswith(head) or work == head:
        return False
    tail = work[len(head):]
    if head and not head.endswith("\n"):
        if not tail.startswith("\n"):
            return False
        tail = tail[1:]
    lines = tail.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return bool(lines) and all(APPENDED_LINE.match(l) for l in lines)


def local_dirty(rs, idx, inv=None):
    rc, out, _ = rs.git("status", "--porcelain", "-z", "--untracked-files=no")
    if rc != 0:
        return True
    rids = {i.split("/")[0] for i in ((idx or {}).get("items") or {})}
    if inv is not None:
        for (d, kind), m in inv.manifests.items():
            if kind == "release.yaml" and m.ok and "migrated_to" in m.data:
                rids.add(d)
    allowed = {f".foundry/releases/{d}/release.yaml" for d in rids}
    toks = out.split("\0")
    paths = []
    k = 0
    while k < len(toks):
        t = toks[k]
        if len(t) > 3:
            paths.append(t[3:])
            if t[0] in "RC":
                k += 1
                if k < len(toks):
                    paths.append(toks[k])
        k += 1
    for p in paths:
        if p.startswith(MIG_DIR + "/"):
            continue
        if p in allowed and pure_migrated_to_append(rs, p):
            continue
        return True
    return False


def head_on_default(rs) -> bool:
    rc, out, _ = rs.git("symbolic-ref", "-q", "refs/remotes/origin/HEAD")
    ref = out.strip()
    if rc != 0 or not ref.startswith("refs/remotes/origin/") or ref == "refs/remotes/origin/HEAD":
        return False
    rc, _, _ = rs.git("merge-base", "--is-ancestor", "HEAD", ref)
    return rc == 0


def cmd_cutover(rs, args, gh_ro, gh_rw):
    if args.action == "record":
        return cmd_record(rs, args)
    return cmd_cutover_plan_or_apply(rs, args, gh_ro, gh_rw)


def cmd_cutover_plan_or_apply(rs, args, gh_ro, gh_rw):
    apply_ = bool(args.apply and not args.dry_run)
    if args.dry_run and not args.apply:
        print("--dry-run is only valid with --apply or `cutover record`", file=sys.stderr)
        return 2
    if not rs.workspace or not rs.head:
        print("cutover needs a git workspace with an origin remote and a HEAD commit", file=sys.stderr)
        return 2
    try:
        overrides = parse_overrides(args.override_flag)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.repo is not None and not REPO_RE.match(args.repo):
        print("--repo must be OWNER/NAME", file=sys.stderr)
        return 2
    reasons = []

    def refuse(tok, item=None, detail=None):
        reasons.append((tok, item, detail))

    gh = gh_rw if apply_ else gh_ro
    lock_fd = None
    lock_path = os.path.join(rs.ws, MIG_DIR, ".lock")
    try:
        if apply_:
            try:
                ensure_dir(rs.ws, os.path.dirname(lock_path))
                lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o644)
            except OSError as exc:      # PermissionError (unsafe dir) and ELOOP (symlinked lock) both land here
                print(f"REFUSED migration-directory-unsafe ({exc})")
                return 1
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                refuse("migration-locked")
                return _report_refusals(reasons)
        elif os.path.exists(lock_path):
            fd = os.open(lock_path, os.O_RDONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                refuse("migration-locked")
            finally:
                os.close(fd)
            if reasons:
                return _report_refusals(reasons)
        return _plan_or_apply(rs, args, gh, apply_, overrides, refuse, reasons)
    finally:
        if lock_fd is not None:
            os.close(lock_fd)


def _report_refusals(reasons):
    for tok, item, detail in reasons:
        print(f"REFUSED {tok}" + (f" item={item}" if item else "") + (f" ({detail})" if detail else ""))
    return 1


def _plan_or_apply(rs, args, gh, apply_, overrides, refuse, reasons):
    ws = rs.ws
    inv = build_inventory(rs)
    idx, idx_err = load_index(ws)
    if idx_err:
        refuse("index-unreadable", None, idx_err)
    itr = (idx or {}).get("target_repo")
    if itr is not None and (not isinstance(itr, str) or not REPO_RE.match(itr)):
        refuse("target-repo-invalid", None, "index target_repo is not OWNER/NAME")
        return _report_refusals(reasons)
    repo = args.repo or itr
    if not repo:
        print("--repo OWNER/NAME is required (no index records a target repository yet)", file=sys.stderr)
        return 2
    if idx and idx.get("target_repo") and args.repo and args.repo != idx["target_repo"]:
        refuse("target-repo-changed", None, f"index says {idx['target_repo']}")
    sel = parse_select(args.select)
    pend = [i for i, e in ((idx or {}).get("items") or {}).items() if isinstance(e, dict) and e.get("status") == "pending"]
    if not sel and not pend:
        print("--select <ids> is required", file=sys.stderr)
        return 2
    proc = list(dict.fromkeys(sel + [p for p in pend if p not in sel]))
    # schema
    newer = False
    if idx is not None:
        sv = idx.get("schema_version")
        newer |= not isinstance(sv, int) or sv > SCHEMA_VERSION
    for iid in list(((idx or {}).get("items") or {})):
        sc, _ = load_sidecar(ws, iid)
        if sc is not None:
            sv = sc.get("schema_version")
            newer |= not isinstance(sv, int) or sv > SCHEMA_VERSION
    if newer:
        refuse("schema-newer")
    if not head_on_default(rs):
        refuse("head-not-on-default-branch")
    if local_dirty(rs, idx, inv):
        refuse("working-tree-dirty")

    for oi, fl in sorted(overrides.items()):
        for f in sorted(fl):
            if oi not in inv.items or f not in inv.items[oi].flags:
                refuse("override-flag-unknown", oi, f"{oi}={f}")
    # item-level, local
    plans, complete = {}, set()
    for iid in proc:
        if iid not in inv.items:
            refuse("not-an-item", iid)
            continue
        it = inv.items[iid]
        st = item_status(inv, idx, iid)
        ent = ((idx or {}).get("items") or {}).get(iid)
        full = (isinstance(ent, dict) and ent.get("status") == "migrated" and manifest_migrated_to(inv, iid) is not None
                and load_sidecar(ws, iid)[0] is not None)
        if full and iid in sel:
            complete.add(iid)
            continue
        for f in it.flags:                     # every processed item, selected or pending
            if f not in overrides.get(iid, set()):
                refuse(f"flagged:{f}", iid)
        pl = build_plan(rs, inv, idx, it, args, overrides)
        plans[iid] = pl
        for p in pl.problems:
            refuse(p, iid)

    # gh run-level
    rc, out, _ = gh.run(["repo", "view", repo, "--json", "visibility"])
    private = False
    if rc == 0:
        try:
            private = json.loads(out).get("visibility") == "PRIVATE"
        except (ValueError, AttributeError):
            private = False
    login, issues = None, None
    if not private:
        refuse("repo-not-private")
    else:
        rc, out, _ = gh.run(["api", "user"])
        if rc == 0:
            try:
                login = json.loads(out).get("login")
            except (ValueError, AttributeError):
                login = None
        if args.project:
            owner = repo.split("/")[0]
            rc, out, _ = gh.run(["project", "list", "--owner", owner, "--format", "json"])
            pub_ok = False
            if rc == 0:
                try:
                    doc = json.loads(out)
                    projs = doc.get("projects", []) if isinstance(doc, dict) else doc
                    for p in projs:
                        if isinstance(p, dict) and p.get("title") == args.project and p.get("public") is False:
                            pub_ok = True
                except ValueError:
                    pass
            if not pub_ok:
                refuse("project-not-private")
        issues, err = fetch_issues(gh, repo)
        if issues is None:
            refuse("issue-listing-incomplete", None, err)

    decisions = {}
    if issues is not None:
        for iid, pl in plans.items():
            hits = [i for i in issues if bears_marker(i, pl.marker)]
            recorded = item_url(inv, idx, iid) or (load_sidecar(ws, iid)[0] or {}).get("issue_url")
            if len(hits) > 1:
                refuse("marker-ambiguous", iid, ", ".join(str(h.get("html_url")) for h in hits))
            elif len(hits) == 1:
                ok, tok = adoptable(hits[0], login, args.reopen)
                if not ok:
                    refuse(tok, iid, ("adoption-untrusted: " if tok == "marker-issue-untrusted" else "")
                           + str(hits[0].get("html_url")))
                elif not guard_body(hits[0].get("body") or "", pl.marker):
                    refuse("marker-issue-body-guard", iid, str(hits[0].get("html_url")))
                elif recorded and recorded != hits[0].get("html_url"):
                    refuse("marker-ambiguous", iid, f"recorded {recorded} vs {hits[0].get('html_url')}")
                else:
                    decisions[iid] = ("adopt", hits[0])
            elif recorded:
                known = {i.get("html_url") for i in issues}
                if issue_number(recorded, repo) is None or recorded not in known:
                    refuse("recorded-url-foreign", iid, str(recorded))
                else:
                    decisions[iid] = ("recorded", recorded)
            else:
                decisions[iid] = ("create", None)
    if reasons:
        return _report_refusals(reasons)

    order = topo_order(inv, [i for i in proc if i in plans])
    if not apply_:
        return _print_plan(rs, args, inv, idx, order, plans, complete, decisions, gh, issues, repo)
    return _apply(rs, args, inv, idx, repo, order, plans, complete, decisions, overrides, gh, login, issues)


def _print_plan(rs, args, inv, idx, order, plans, complete, decisions, gh, issues, repo):
    diags, status = diagnose(rs, inv, gh, args.offline, issues if idx and idx.get("target_repo") == repo else None)
    if args.json:
        print(json.dumps({"preflight": "ok", "order": order, "already_migrated": sorted(complete),
                          "actions": {k: v[0] for k, v in decisions.items()},
                          "items": [_item_report(plans[i]) for i in order],
                          "diagnostics": diags, "closed_issue_check": status}, indent=2))
        return 0
    print("preflight: ok")
    print("order: " + ", ".join(order))
    for i in sorted(complete):
        print(f"already-migrated {i}")
    for i in order:
        print(f"== {i}: {decisions[i][0]}\ntitle: {plans[i].title}\n" + plans[i].body)
    print()
    print_diags(diags, status)
    return 0


def _apply(rs, args, inv, idx0, repo, order, plans, complete, decisions, overrides, gh, login, issues):
    ws = rs.ws
    opts = rs.opts
    idx = idx0 if idx0 is not None else {"schema_version": SCHEMA_VERSION, "tool_version": TOOL_VERSION,
                                          "workspace": rs.workspace, "target_repo": repo, "items": {}}
    idx.setdefault("target_repo", repo)
    if not idx.get("target_repo"):
        idx["target_repo"] = repo
    items = idx["items"]
    wrote = False
    live = {}
    changed_manifests = set()
    crash = {"n": opts.crash_after}

    def write_index():
        atomic_write(ws, index_path(), dump_yaml(idx).encode())

    def maybe_crash(step):
        if crash["n"] == step:
            raise Killed(f"simulated kill after step {step}")

    for iid in sorted(complete):
        print(f"already-migrated {iid}")
    rc_final = 0
    try:
        for iid in order:
            it = inv.items[iid]
            kind, payload = decisions[iid]
            pl = build_plan(rs, inv, idx, it, args, overrides, live)   # refreshed: deps migrated this run
            if pl.problems:
                for p in pl.problems:
                    print(f"REFUSED {p} item={iid}")
                rc_final = 1
                break
            ent = items.get(iid) if isinstance(items.get(iid), dict) else None
            already_migrated = item_status(inv, idx, iid) == "migrated"
            # step 1
            if not already_migrated:
                items[iid] = {"status": "pending", "updated_at": now()}
                write_index()
                wrote = True
            maybe_crash(1)
            # step 2
            url = None
            if kind == "adopt":
                issue = payload
                url = issue["html_url"]
                if issue.get("state") == "closed" and issue.get("state_reason") in ("not_planned", "duplicate"):
                    rc, _, _ = gh.run(["issue", "reopen", str(issue["number"]), "--repo", repo])
                    if rc != 0:
                        print(f"REFUSED gh-call-failed item={iid}")
                        rc_final = 1
                        break
            elif kind == "recorded":
                url = payload
            else:
                argv = ["issue", "create", "--repo", repo, "--title", pl.title, "--body-file", "-",
                        "--assignee", pl.owner]
                for n in pl.blocked_by:
                    argv += ["--blocked-by", str(n)]
                if args.project:
                    argv += ["--project", args.project]
                rc, out, _ = gh.run(argv, stdin=pl.body)
                lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
                url = lines[-1] if lines else ""
                if rc != 0 or issue_number(url, repo) is None:
                    print(f"REFUSED gh-call-failed item={iid}")
                    rc_final = 1
                    break
            maybe_crash(2)
            # step 3
            old_sc, _ = load_sidecar(ws, iid)
            if old_sc is not None and old_sc.get("issue_url") == url:
                pass                                  # complete sidecar: a resume leaves it byte-identical
            else:
                sc = dict(pl.sidecar)
                sc["issue_url"] = url
                sc["migrated_at"] = (old_sc or {}).get("migrated_at") or now()
                if old_sc and isinstance(old_sc.get("flags_overridden"), list):
                    sc["flags_overridden"] = old_sc["flags_overridden"]
                atomic_write(ws, sidecar_rel(iid), dump_yaml(sc).encode())
                wrote = True
            maybe_crash(3)
            # step 4
            if manifest_migrated_to(inv, iid) is None:
                if already_migrated:                  # heal: the index must not say migrated until the manifest does
                    items[iid] = {"status": "pending", "issue_url": url, "updated_at": now()}
                    write_index()
                    wrote = True
                if not append_migrated_to(rs, inv, it, url):
                    print(f"REFUSED migrated-to-reparse-mismatch item={iid}")
                    rc_final = 1
                    break
                changed_manifests.add(it.manifest.rel)
            maybe_crash(4)
            # step 5
            items[iid] = {"status": "migrated", "issue_url": url, "updated_at": now()}
            write_index()
            live[iid] = url
            print(f"migrated {iid} {url}")
    except Killed:
        raise
    except OSError as exc:
        print(f"REFUSED write-failed ({exc})")
        rc_final = 1
    if wrote:
        for iid, it in sorted(inv.items.items()):
            if iid not in items:
                u = manifest_migrated_to(inv, iid)
                items[iid] = ({"status": "migrated", "issue_url": u, "updated_at": now()} if u
                              else {"status": "unmigrated", "updated_at": now()})
        write_index()
    if rc_final:
        return rc_final
    print()
    inv2 = build_inventory(rs)
    diags, status = diagnose(rs, inv2, gh, args.offline, issues)
    print_diags(diags, status)
    return 0


def append_migrated_to(rs: RunState, inv: Inventory, item: Item, url: str) -> bool:
    ws = rs.ws
    rel = item.manifest.rel
    path = os.path.join(ws, rel)
    with open(path, "rb") as fh:
        pre = fh.read()
    data = yl.safe_load(pre)
    entry = f"  {json.dumps(item.id)}: {json.dumps(url)}\n"
    lead = b"" if (not pre or pre.endswith(b"\n")) else b"\n"
    add = (entry if "migrated_to" in data else "migrated_to:\n" + entry).encode("utf-8")
    new = pre + lead + add
    hook = rs.opts.manifest_append_hook
    if hook is not None:
        new = hook(new)
    atomic_write(ws, rel, new, mode_from=path)
    ok = False
    try:
        with open(path, "rb") as fh:
            back = yl.safe_load(fh.read())
        mt = back.get("migrated_to") if isinstance(back, dict) else None
        ok = isinstance(mt, dict) and mt.get(item.id) == url
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        ok = False
    if not ok:
        atomic_write(ws, rel, pre, mode_from=path)
        return False
    item.manifest.data = back
    return True


def operators(ws):
    data, err = load_yaml_rel(ws, ".claude/foundry-operators.json")
    if err or not isinstance(data, dict):
        return set()
    ops = data.get("operators")        # only the `operators` mapping counts; anything else fails closed
    return {k for k in ops if isinstance(k, str)} if isinstance(ops, dict) else set()


def cmd_record(rs, args):
    ws = rs.ws
    fails = []
    if not args.operator:
        fails.append("operator-missing")
    elif args.operator not in operators(ws):
        fails.append("operator-not-registered")
    if not args.scope or not args.scope.strip():
        fails.append("scope-empty")
    auth_rel = f"{MIG_DIR}/authority.yaml"
    if os.path.lexists(os.path.join(ws, auth_rel)):
        fails.append("authority-record-exists")
    idx, idx_err = load_index(ws)
    if idx_err:
        fails.append("index-unreadable")
    items = (idx or {}).get("items") or {}
    if any(isinstance(e, dict) and e.get("status") == "pending" for e in items.values()):
        fails.append("index-has-pending-item")
    migrated = [i for i, e in items.items() if isinstance(e, dict) and e.get("status") == "migrated"]
    if not migrated and not args.acknowledge_empty:
        fails.append("no-migrated-item-without-acknowledge-empty")
    recon = []
    for r in args.reconciled:
        n = norm_rel(r)
        ok = (n is not None and (n in CUR3_FILES or any(n.startswith(p) and len(n) > len(p) for p in CUR3_PREFIXES))
              and os.path.isfile(os.path.join(ws, n)) and inside(ws, os.path.join(ws, n)))
        if ok:
            recon.append(n)
        else:
            fails.append(f"reconciled-path-invalid:{r}")
    if not rs.workspace or not rs.head:
        fails.append("workspace-unresolved")
    if fails:
        for f in fails:
            print(f"REFUSED {f}")
        return 1
    rec = {"schema_version": SCHEMA_VERSION, "recorded_by": args.operator, "at": now(),
           "planning_authority": "github", "scope": args.scope, "instructions_reconciled": recon,
           "workspace": rs.workspace, "head": rs.head}
    text = dump_yaml(rec)
    if args.dry_run:
        print(text, end="")
        return 0
    mig = os.path.join(ws, MIG_DIR)
    try:
        ensure_dir(ws, mig)
        lock_fd = os.open(os.path.join(mig, ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o644)
    except OSError as exc:
        print(f"REFUSED migration-directory-unsafe ({exc})")
        return 1
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("REFUSED migration-locked")
            return 1
        try:
            fd = os.open(os.path.join(ws, auth_rel), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            print("REFUSED authority-record-exists")
            return 1
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
    finally:
        os.close(lock_fd)
    print(f"authority record written: {auth_rel}")
    return 0


def main(argv=None, opts: Options | None = None) -> int:
    opts = opts or Options()
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    ws = workspace_root(opts)
    if ws is None:
        print("not inside a git workspace", file=sys.stderr)
        return 2
    rs = RunState(ws, opts)
    if args.cmd == "cutover":
        import foundry_github_capabilities as ghc
        rc = ghc.gate(ws, "cutover")
        if rc is not None:
            return rc
    gh_ro = Gh(read_only=True)
    if args.cmd == "inventory":
        return cmd_inventory(rs, args, gh_ro)
    if args.cmd == "preview":
        return cmd_preview(rs, args, gh_ro)
    return cmd_cutover(rs, args, gh_ro, Gh(read_only=False))


if __name__ == "__main__":
    sys.exit(main())
