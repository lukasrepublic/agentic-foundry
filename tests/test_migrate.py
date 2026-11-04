"""gn-14: scripts/foundry_migrate.py -- artifact disposition and existing-project cutover (AC-CUT-1..14).

Every test runs against a synthetic fixture workspace built at test time (a git repo with an `origin`
remote and a resolvable refs/remotes/origin/HEAD) and a fake `gh` first on PATH that records every argv
and serves state from a JSON file. No network.
"""
import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests" / "fixtures" / "migrate"))

import foundry_migrate as M  # noqa: E402
import workspaces as WS  # noqa: E402

FAKE_GH = ROOT / "tests" / "fixtures" / "migrate" / "fake_gh.py"
TARGET = WS.TARGET
MARKER_PREFIX = f"<!-- foundry-migrated: {WS.WS_SLUG}/"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def tree(root):
    out = {}
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d != ".git"]
        for fn in fns:
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, root)
            out[rel] = "link" if os.path.islink(p) else sha(p)
    return out


class W:
    """A fixture workspace + fake gh."""

    def __init__(self, tmp_path, monkeypatch, files=None, issues=None, plugin_root=None, visibility="PRIVATE"):
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.tmp = tmp_path
        self.root = WS.build(tmp_path / "ws", files if files is not None else WS.basic_files())
        bindir = tmp_path / "bin"
        bindir.mkdir()
        gh = bindir / "gh"
        gh.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE_GH} "$@"\n')
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        self.state_file = tmp_path / "gh-state.json"
        self.log_file = tmp_path / "gh-log.jsonl"
        self.log_file.write_text("")
        monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
        monkeypatch.setenv("FAKE_GH_STATE", str(self.state_file))
        monkeypatch.setenv("FAKE_GH_LOG", str(self.log_file))
        self.state_file.write_text(json.dumps({
            "user": "me", "projects": [],
            "repos": {TARGET: {"visibility": visibility, "issues": list(issues or [])}}}))
        self.plugin_root = plugin_root

    # -- gh state
    def state(self):
        return json.loads(self.state_file.read_text())

    def set_state(self, **kw):
        st = self.state()
        st.update(kw)
        self.state_file.write_text(json.dumps(st))

    def repo_state(self):
        return self.state()["repos"][TARGET]

    def log(self):
        return [json.loads(l) for l in self.log_file.read_text().splitlines() if l.strip()]

    def argvs(self):
        return [r["argv"] for r in self.log()]

    def creates(self):
        return [a for a in self.argvs() if a[:2] == ["issue", "create"]]

    # -- running
    def run(self, *argv, opts=None):
        o = opts or M.Options()
        o.cwd = str(self.root)
        if self.plugin_root is not None:
            o.plugin_root = str(self.plugin_root)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = M.main(list(argv), opts=o)
        return rc, out.getvalue(), err.getvalue()

    def inventory(self, *extra):
        rc, out, err = self.run("inventory", "--json", *extra)
        assert rc == 0, err
        return json.loads(out)

    def preview(self, select, *extra):
        rc, out, err = self.run("preview", "--json", "--repo", TARGET, "--select", select, *extra)
        assert rc == 0, out + err
        return json.loads(out)

    def apply(self, select, *extra, opts=None):
        return self.run("cutover", "--apply", "--repo", TARGET, "--owner", "owner-x", "--select", select,
                        *extra, opts=opts)

    def p(self, rel):
        return Path(self.root) / rel

    def git(self, *a):
        return subprocess.run(["git", "-C", str(self.root), *a], check=True, capture_output=True, text=True).stdout

    def head(self):
        return self.git("rev-parse", "HEAD").strip()

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "change set")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")

    def index(self):
        return yaml.safe_load(self.p(".foundry/migration/index.yaml").read_text())

    def manifest(self):
        return yaml.safe_load(self.p(".foundry/releases/rel-a/release.yaml").read_text())


@pytest.fixture
def w(tmp_path, monkeypatch):
    return W(tmp_path, monkeypatch)


def marker_issue(number, item, *, state="open", reason=None, login="me", assoc="OWNER", extra_body=""):
    return {"number": number, "state": state, "state_reason": reason, "user": {"login": login},
            "author_association": assoc, "title": "t", "html_url": f"https://github.com/{TARGET}/issues/{number}",
            "body": f"{MARKER_PREFIX}{item} -->\n{extra_body}"}


# ===================================================================================== AC-CUT-1
def test_inventory_rows_carry_class_rule_status_and_flags(w):
    w.p(".foundry/migration").mkdir()
    w.p(".foundry/migration/index.yaml").write_text(yaml.safe_dump({
        "schema_version": 1, "tool_version": M.TOOL_VERSION, "workspace": WS.WS_SLUG, "target_repo": TARGET,
        "items": {"rel-a/a1": {"status": "migrated", "issue_url": f"https://github.com/{TARGET}/issues/1"}}}))
    js = w.inventory("--offline")
    rows = {r["path"]: (r["class"], r["rule"]) for r in js["paths"]}
    expect = {
        ".foundry/migration/index.yaml": ("current-knowledge", "R-MIG-1"),
        ".foundry/releases/rel-a/release.yaml": ("active-work", "R-ACT-1"),
        ".foundry/releases/rel-done/release.yaml": ("retained-history", "R-HIS-1"),
        ".foundry/releases/rel-a/RESUME.md": ("current-knowledge", "R-CUR-4"),
        ".foundry/releases/rel-a/state.yaml": ("current-knowledge", "R-CUR-4"),
        ".foundry/releases/rel-done/NOTES.md": ("retained-history", "R-HIS-2"),
        "specs/features/foundry/demo/acceptance-contract.yaml": ("retained-history", "R-HIS-3"),
        "specs/features/foundry/demo/feat-a.md": ("retained-history", "R-HIS-3"),
        "specs/charters/c1.md": ("retained-history", "R-HIS-4"),
        ".foundry/evidence/e1.txt": ("retained-history", "R-HIS-4"),
        ".foundry/admission-ledger.jsonl": ("retained-history", "R-HIS-4"),
        "status-reports/s1.md": ("retained-history", "R-HIS-4"),
        "specs/features/foundry/demo/other.md": ("current-knowledge", "R-CUR-1"),
        "specs/features/foundry/demo/b/acceptance-contract.yaml": ("current-knowledge", "R-CUR-1"),
        "docs/note.md": ("current-knowledge", "R-CUR-2"),
        ".foundry/decisions/d1.md": ("current-knowledge", "R-CUR-2"),
        "CLAUDE.md": ("current-knowledge", "R-CUR-3"),
        ".claude/settings.json": ("current-knowledge", "R-CUR-3"),
        ".claude/hooks/h.sh": ("current-knowledge", "R-CUR-3"),
        ".github/workflows/ci.yml": ("current-knowledge", "R-CUR-3"),
        ".foundry/wiring-hash.pin": ("runtime-residue", "R-RES-1"),
    }
    for path, want in expect.items():
        assert rows.get(path) == want, (path, rows.get(path))
    assert {r["rule"] for r in js["paths"]} >= {"R-MIG-1", "R-ACT-1", "R-HIS-1", "R-CUR-4", "R-HIS-2", "R-HIS-3",
                                                "R-HIS-4", "R-CUR-1", "R-CUR-2", "R-CUR-3", "R-RES-1"}
    assert not any(p.startswith(".git/") for p in rows)
    assert ".claude/foundry-project.json" not in rows        # no rule names it
    items = {i["id"]: i for i in js["items"]}
    assert set(items) == {"rel-a/a1", "rel-a/a2", "rel-a/a3"}
    assert items["rel-a/a1"]["status"] == "migrated" and items["rel-a/a2"]["status"] == "unmigrated"
    assert all(i["rule"] == "R-ACT-1" and i["flags"] == [] for i in items.values())
    # the table form carries the same facts
    rc, out, _ = w.run("inventory", "--offline")
    assert rc == 0 and "R-ACT-1" in out and ".foundry/releases/rel-a/release.yaml" in out
    assert "rel-a/a1  R-ACT-1  migrated" in out


def test_inventory_golden_real_manifest_shapes(tmp_path, monkeypatch):
    g = W(tmp_path, monkeypatch, files=WS.golden_files())
    js = g.inventory("--offline")
    got = {k: js[k] for k in ("paths", "items", "unreadable")}
    want = json.loads((ROOT / "tests" / "fixtures" / "migrate" / "golden_inventory.json").read_text())
    assert got == want
    items = {i["id"]: i["flags"] for i in js["items"]}
    assert items["rel-live/shipped-one"] == ["likely-shipped", "program-closed"]
    assert items["rel-live/reauth-one"] == ["coupled", "program-closed"]
    assert items["rel-planned/flow-one"] == ["program-closed"]
    assert items["rel-orphan"] == ["program-unresolved"]
    rows = {r["path"]: r["rule"] for r in js["paths"]}
    for f in ("RESUME.md", "HANDOFF.md", "A2-PARKED.md", "state.yaml"):
        assert rows[f".foundry/releases/rel-live/{f}"] == "R-CUR-4"
    # selecting a flagged item is refused unless every flag is overridden
    rc, out, _ = g.run("cutover", "--repo", TARGET, "--owner", "o1", "--select", "rel-live/shipped-one")
    assert rc == 1 and "flagged:likely-shipped" in out and "flagged:program-closed" in out


# ===================================================================================== AC-CUT-2
UNREADABLE = {
    "paused-state": (".foundry/releases/pz/release.yaml", "id: pz\nstate: paused\natoms:\n  - id: x\n", "pz/x", "state-not-active-or-done"),
    "unparseable": (".foundry/releases/pz/release.yaml", "id: pz\nstate: [active\natoms:\n  - id: x\n", "pz/x", "unparseable"),
    "id-mismatch": (".foundry/releases/pz/release.yaml", "id: other\nstate: active\natoms:\n  - id: x\n", "pz/x", "id-differs-from-directory"),
    "bad-dir-segment": (".foundry/releases/Pz/release.yaml", "id: Pz\nstate: active\natoms:\n  - id: x\n", "Pz/x", "bad-id-segment"),
    "dotdot-atom": (".foundry/releases/pz/release.yaml", "id: pz\nstate: active\natoms:\n  - id: ..\n", "pz/..", "bad-id-segment"),
    "upper-atom": (".foundry/releases/pz/release.yaml", "id: pz\nstate: active\natoms:\n  - id: Big\n", "pz/Big", "bad-id-segment"),
}


@pytest.mark.parametrize("case", sorted(UNREADABLE) + ["catalogue-absent"])
def test_unreadable_inputs_listed_with_reason_and_unselectable(case, tmp_path, monkeypatch):
    if case == "catalogue-absent":
        empty = tmp_path / "plugin"
        empty.mkdir()
        w = W(tmp_path, monkeypatch, plugin_root=empty)
        js = w.inventory("--offline")
        assert {"path": "cli/retired-artifacts.json", "reason": "residue-catalogue-unavailable"} in js["unreadable"]
        assert not any(r["rule"] == "R-RES-1" for r in js["paths"])
        return
    path, text, item, reason = UNREADABLE[case]
    files = WS.basic_files()
    files[path] = text
    w = W(tmp_path, monkeypatch, files=files)
    js = w.inventory("--offline")
    hit = [u for u in js["unreadable"] if u["path"] == path]
    assert hit and reason in hit[0]["reason"], js["unreadable"]
    assert path not in {r["path"] for r in js["paths"]}              # no class
    assert item not in {i["id"] for i in js["items"]}
    rc, out, _ = w.run("cutover", "--repo", TARGET, "--owner", "o1", "--select", item)
    assert rc == 1 and "not-an-item" in out


# ===================================================================================== AC-CUT-3
READ_ALLOW = [
    re.compile(r"^api user$"),
    re.compile(r"^repo view [^ ]+ --json visibility$"),
    re.compile(r"^api --paginate repos/[^ /]+/[^ /]+/issues\?state=all&per_page=100$"),
    re.compile(r"^project list --owner [^ ]+ --format json$"),
]
RO_MODES = {
    "inventory": ["inventory"],
    "preview": ["preview", "--repo", TARGET, "--select", "rel-a/a1,rel-a/a2", "--owner", "o1"],
    "cutover-plan": ["cutover", "--repo", TARGET, "--select", "rel-a/a1", "--owner", "o1"],
    "cutover-dry-run": ["cutover", "--apply", "--dry-run", "--repo", TARGET, "--select", "rel-a/a1", "--owner", "o1"],
    "record-dry-run": ["cutover", "record", "--operator", "op_test", "--scope", "all", "--acknowledge-empty", "--dry-run"],
}


@pytest.mark.parametrize("mode", sorted(RO_MODES))
def test_read_only_modes_write_nothing_and_gh_argv_in_allowlist(mode, w):
    # an index that names a migrated item makes inventory/preview use their (allowed) issue listing
    w.p(".foundry/migration").mkdir()
    url = f"https://github.com/{TARGET}/issues/9"
    w.p(".foundry/migration/index.yaml").write_text(yaml.safe_dump({
        "schema_version": 1, "tool_version": M.TOOL_VERSION, "workspace": WS.WS_SLUG, "target_repo": TARGET,
        "items": {"rel-a/a3": {"status": "migrated", "issue_url": url}}}))
    w.set_state(projects=[{"title": "P", "public": False}])
    w.repo_state()["issues"].append(marker_issue(9, "rel-a/a3"))
    st = w.state()
    st["repos"][TARGET]["issues"] = [marker_issue(9, "rel-a/a3")]
    w.state_file.write_text(json.dumps(st))
    before = tree(w.root)
    args = RO_MODES[mode]
    if mode == "record-dry-run":
        os.unlink(w.p(".foundry/migration/index.yaml"))
        before = tree(w.root)
    rc, out, err = w.run(*args)
    assert rc == 0, out + err
    assert tree(w.root) == before                                       # bytes and file set
    assert not w.p(".foundry/migration/.lock").exists()
    for argv in w.argvs():
        assert any(r.match(" ".join(argv)) for r in READ_ALLOW), argv
    if mode in ("inventory", "preview"):
        assert w.argvs(), "expected the allowed listing to be used"


# ===================================================================================== AC-CUT-4
def test_preview_reports_issue_fields_and_sidecar_content(w):
    spec_path = w.p("specs/features/foundry/demo/feat-a.md")
    spec_path.write_text(spec_path.read_text().replace("Does the first thing.", "EDITED-IN-WORKING-TREE"))
    js = w.preview("rel-a/a1,rel-a/a2,rel-a/a3", "--owner", "cli-owner", "--priority", "P2")
    by = {i["id"]: i for i in js["items"]}
    head = w.head()
    a1, a2, a3 = by["rel-a/a1"], by["rel-a/a2"], by["rel-a/a3"]
    assert a1["title"] == "rel-a/a1: Feature A"
    assert a1["owner"] == "item-owner" and a1["priority"] == "P1"       # item field beats --owner/--priority
    assert a2["owner"] == "cli-owner" and a2["priority"] == "P2"
    assert any("AC-A-1" in a and "Does the first thing." in a for a in a1["acceptance"])
    assert not any("EDITED-IN-WORKING-TREE" in a for a in a1["acceptance"])      # read from the HEAD blob
    assert f"https://github.com/{WS.WS_SLUG}/blob/{head}/specs/features/foundry/demo/feat-a.md" in a1["context_links"]
    assert a1["recorded_state"] == "active (unverified)" and a1["flags"] == []
    assert a2["dependencies"] == [{"id": "rel-a/a1", "status": "unmigrated", "url": None}]
    assert a3["title"] == "rel-a/a3" and a3["acceptance"] == ["No spec recorded for this item."]
    assert f"https://github.com/{WS.WS_SLUG}/blob/{head}/.foundry/releases/rel-done/release.yaml" in a3["context_links"]
    assert a3["priority"] == "P2"
    js2 = w.preview("rel-a/a3", "--owner", "o")
    assert js2["items"][0]["priority"] == "unset"
    # exact sidecar content
    sc = a1["sidecar"]
    assert sc == {
        "schema_version": 1, "tool_version": M.TOOL_VERSION, "id": "rel-a/a1",
        "marker": f"{MARKER_PREFIX}rel-a/a1 -->",
        "issue_url": "<assigned when the issue is created or adopted>", "migrated_at": "<set at apply>",
        "source": {"manifest": ".foundry/releases/rel-a/release.yaml",
                   "spec_ref": "specs/features/foundry/demo/feat-a.md",
                   "contract_ref": "specs/features/foundry/demo/acceptance-contract.yaml", "commit": head},
        "context_links": a1["context_links"], "pending_dependencies": [], "flags_overridden": []}
    assert a2["sidecar"]["pending_dependencies"] == ["rel-a/a1"]
    # once a1 is migrated, a2 shows the dependency's status and URL
    w.p(".foundry/migration").mkdir()
    url = f"https://github.com/{TARGET}/issues/7"
    w.p(".foundry/migration/index.yaml").write_text(yaml.safe_dump({
        "schema_version": 1, "tool_version": M.TOOL_VERSION, "workspace": WS.WS_SLUG, "target_repo": TARGET,
        "items": {"rel-a/a1": {"status": "migrated", "issue_url": url}}}))
    d = w.preview("rel-a/a2", "--owner", "o", "--offline")["items"][0]
    assert d["dependencies"] == [{"id": "rel-a/a1", "status": "migrated", "url": url}]
    assert d["blocked_by"] == [7]


# ===================================================================================== AC-CUT-5
def test_preview_reports_instruction_conflicts_before_after(w):
    js = w.preview("rel-a/a1", "--owner", "o")
    text = w.p("CLAUDE.md").read_text()
    line = text.splitlines().index("The release manifest is the work context and the acceptance") + 1
    pats = json.loads((ROOT / "scripts" / "foundry_migrate_authority_patterns.json").read_text())["entries"]
    conflicts = js["instruction_conflicts"]
    assert len(conflicts) == 1, conflicts                                   # reported once
    c = conflicts[0]
    assert c["path"] == f"CLAUDE.md:{line}"
    assert c["before"] == "The release manifest is the work context and the acceptance record for every planned change."
    assert c["after"] == pats[0]["after"]
    # NEGATIVE: the front-authorization sentence matches a planning pattern but names a floor phrase
    front = [s for _, s in M.sentences(text) if "never reaches a product repo" in s]
    assert front and "(no skip)." in front[0]
    assert any(re.search(e["match"], front[0], re.I) for e in pats)         # non-vacuous
    assert not any("never reaches" in x["before"] for x in conflicts)
    # NEGATIVE: matches a pattern, names sign-off
    assert any("sign-off" in s for _, s in M.sentences(text))
    assert not any("sign-off" in x["before"] for x in conflicts)
    # NEGATIVE: a matching sentence in docs/ is not an instruction file
    assert not any(x["path"].startswith("docs/") for x in conflicts)
    # the tool never writes the instruction files
    assert w.p("CLAUDE.md").read_text() == text


# ===================================================================================== AC-CUT-6
def test_issue_body_one_marker_guard_clean_links_only(w):
    pv = w.preview("rel-a/a1", "--owner", "o")["items"][0]
    rc, out, err = w.apply("rel-a/a1")
    assert rc == 0, out + err
    sent = w.log()
    creates = [r for r in sent if r["argv"][:2] == ["issue", "create"]]
    assert len(creates) == 1
    for body in (pv["body"], creates[0]["stdin"]):
        lines = body.split("\n")
        assert lines.count(f"{MARKER_PREFIX}rel-a/a1 -->") == 1
        assert sum("foundry-migrated:" in l for l in lines) == 1
        assert not any(M.GUARD_G.match(l) for l in lines)
        for banned in ("SECRET-LOCATOR", "CHARTER-TEXT-NEVER-INLINED", "Evidence text that must never be inlined"):
            assert banned not in body
        assert "AC-A-1" in body and "Does the first thing." in body
        assert w.head() in body                                         # links at the HEAD sha
    assert creates[0]["argv"].count("--body-file") == 1 and creates[0]["argv"][creates[0]["argv"].index("--body-file") + 1] == "-"


def _load_ticket_tool():
    spec = importlib.util.spec_from_file_location("ticket_tool_oracle", ROOT / "scripts" / "foundry-ticket.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_guard_pattern_covers_ticket_tool_regexes():
    tk = _load_ticket_tool()
    corpus = []
    for phrase in ("Done means", "done means", "DONE MEANS", "Done  means", "Done\tmeans",
                   "Paper allowed", "paper allowed", "PAPER ALLOWED", "Paper   allowed"):
        for hashes in ("##", "###", "####", "######"):
            for gap in (" ", "  ", "\t", ""):
                for tail in ("", ":", " (x)", " now"):
                    corpus.append(f"{hashes}{gap}{phrase}{tail}")
    matched = 0
    for h in corpus:
        if tk._DONE_RE.search(h) or tk._PAPER_RE.search(h):
            matched += 1
            assert M.GUARD_G.match(h), f"G misses a heading the ticket tool executes on: {h!r}"
    assert matched > 100                                                 # the oracle is not vacuous
    for setext in ("Done means", "Paper allowed", "  Paper allowed"):
        assert M.GUARD_G.match(setext)
    assert not M.GUARD_G.match("## Other heading") and not M.GUARD_G.match("This is done and means little")


def _spec_with_ac_extra(extra_lines):
    return WS.spec("Feature A", [("AC-A-1", "first thing.")]).replace(
        "- **AC-A-1** (Requirement): first thing.", "- **AC-A-1** (Requirement): first thing.\n" + extra_lines)


GUARD_CASES = {
    "atx-heading": "## Done means\nlocator",
    "atx-heading-indented": "  ## Done means\n  locator",
    "setext-paper": "Paper allowed\n-------------",
    "second-marker-same": f"{MARKER_PREFIX}rel-a/a1 -->",
    "second-marker-other": f"{MARKER_PREFIX}rel-a/a2 -->",
}


@pytest.mark.parametrize("case", sorted(GUARD_CASES))
def test_body_guard_refuses_quoted_heading_or_second_marker(case, tmp_path, monkeypatch):
    files = WS.basic_files()
    files["specs/features/foundry/demo/feat-a.md"] = _spec_with_ac_extra(GUARD_CASES[case])
    w = W(tmp_path, monkeypatch, files=files)
    pv = w.preview("rel-a/a1", "--owner", "o")["items"][0]
    assert "body-guard" in pv["problems"]
    before = tree(w.root)
    rc, out, _ = w.apply("rel-a/a1")
    assert rc == 1 and "body-guard" in out and "item=rel-a/a1" in out
    after = tree(w.root)
    assert {k for k in after if after[k] != before.get(k)} <= {".foundry/migration/.lock"}
    assert w.creates() == []


# ===================================================================================== AC-CUT-7
def _mk_many(n, skip=()):
    return [{"number": i, "state": "open", "state_reason": None, "user": {"login": "someone"},
             "author_association": "NONE", "title": f"i{i}", "body": "no marker here",
             "html_url": f"https://github.com/{TARGET}/issues/{i}"} for i in range(1, n + 1) if i not in skip]


def test_rerun_adopts_marker_issue_across_pages_never_duplicates(tmp_path, monkeypatch):
    w = W(tmp_path / "one", monkeypatch)
    rc, out, err = w.apply("rel-a/a1,rel-a/a2")
    assert rc == 0, out + err
    argvs = w.argvs()
    listing = ["api", "--paginate", f"repos/{TARGET}/issues?state=all&per_page=100"]
    assert listing in argvs
    first_create = min(i for i, a in enumerate(argvs) if a[:2] == ["issue", "create"])
    assert argvs.index(listing) < first_create
    assert not any(a[:2] == ["issue", "list"] for a in argvs)
    creates = w.creates()
    assert len(creates) == 2
    n1 = w.repo_state()["issues"][0]["number"]
    assert "--blocked-by" not in creates[0]
    assert creates[1][creates[1].index("--blocked-by") + 1] == str(n1)    # dependant after its dependency
    # rerun: nothing created, nothing duplicated
    w.commit()
    rc, out, _ = w.apply("rel-a/a1,rel-a/a2")
    assert rc == 0 and "already-migrated rel-a/a1" in out and len(w.creates()) == 2
    markers = [i for i in w.repo_state()["issues"] if i["body"].split("\n")[0] == f"{MARKER_PREFIX}rel-a/a1 -->"]
    assert len(markers) == 1

    # a pre-existing adoptable marker issue the index does not know, CLOSED-completed on page 2 of 150
    pre = marker_issue(3, "rel-a/a1", state="closed", reason="completed")
    w2 = W(tmp_path / "two", monkeypatch, issues=_mk_many(150, skip={3}) + [pre])
    rc, out, err = w2.apply("rel-a/a1")
    assert rc == 0, out + err
    assert w2.creates() == []
    url = f"https://github.com/{TARGET}/issues/3"
    assert w2.index()["items"]["rel-a/a1"]["issue_url"] == url and w2.index()["items"]["rel-a/a1"]["status"] == "migrated"
    assert sum(1 for i in w2.repo_state()["issues"] if f"{MARKER_PREFIX}rel-a/a1 -->" in i["body"]) == 1
    assert w2.manifest()["migrated_to"]["rel-a/a1"] == url


ELIG = {
    "none-assoc-stranger": (dict(login="stranger", assoc="NONE"), False, "marker-issue-untrusted"),
    "member-other-login": (dict(login="teammate", assoc="MEMBER"), True, None),
    "collaborator-other-login": (dict(login="teammate", assoc="COLLABORATOR"), True, None),
    "authenticated-login-none-assoc": (dict(login="me", assoc="NONE"), True, None),
    "closed-not-planned": (dict(state="closed", reason="not_planned"), "reopen", "marker-issue-closed-not-planned"),
    "closed-duplicate": (dict(state="closed", reason="duplicate"), "reopen", "marker-issue-closed-not-planned"),
}


def test_adoption_eligibility_author_close_reason_uniqueness(tmp_path, monkeypatch):
    for case in sorted(ELIG):
        kw, outcome, token = ELIG[case]
        w = W(tmp_path / case, monkeypatch, issues=[marker_issue(5, "rel-a/a1", **kw)])
        rc, out, _ = w.apply("rel-a/a1")
        if outcome is True:
            assert rc == 0 and w.creates() == [], case
            continue
        assert rc == 1 and token in out and w.creates() == [], case
        assert not w.p(".foundry/migration/index.yaml").exists()
        if outcome == "reopen":
            rc, out, err = w.apply("rel-a/a1", "--reopen")
            assert rc == 0, out + err
            assert w.creates() == [] and any(a[:3] == ["issue", "reopen", "5"] for a in w.argvs())
            assert w.repo_state()["issues"][0]["state"] == "open"
            assert w.index()["items"]["rel-a/a1"]["issue_url"] == f"https://github.com/{TARGET}/issues/5"
    # two marker issues -> ambiguous, naming both; nothing written, nothing created
    w = W(tmp_path / "ambiguous", monkeypatch, issues=[marker_issue(5, "rel-a/a1"), marker_issue(6, "rel-a/a1")])
    rc, out, _ = w.apply("rel-a/a1")
    assert rc == 1 and "marker-ambiguous" in out and "/issues/5" in out and "/issues/6" in out
    assert w.creates() == [] and not w.p(".foundry/migration/index.yaml").exists()
    # a pull request that carries the marker is not an issue and is never adopted
    pr = marker_issue(8, "rel-a/a1")
    pr["pull_request"] = {"url": "x"}
    w = W(tmp_path / "pr", monkeypatch, issues=[pr])
    rc, out, err = w.apply("rel-a/a1")
    assert rc == 0, out + err
    assert len(w.creates()) == 1


# ===================================================================================== AC-CUT-8
def test_sidecar_and_index_mark_migrated_and_unmigrated(w):
    rc, out, err = w.apply("rel-a/a1")
    assert rc == 0, out + err
    sc = yaml.safe_load(w.p(".foundry/migration/items/rel-a/a1.yaml").read_text())
    url = w.repo_state()["issues"][0]["html_url"]
    assert sc["issue_url"] == url and sc["marker"] == f"{MARKER_PREFIX}rel-a/a1 -->"
    assert sc["source"]["manifest"] == ".foundry/releases/rel-a/release.yaml"
    assert sc["source"]["spec_ref"].endswith("feat-a.md") and sc["source"]["contract_ref"].endswith("acceptance-contract.yaml")
    assert sc["source"]["commit"] == w.head() and sc["schema_version"] == 1
    idx = w.index()
    assert idx["target_repo"] == TARGET and idx["workspace"] == WS.WS_SLUG
    st = {k: v["status"] for k, v in idx["items"].items()}
    assert st == {"rel-a/a1": "migrated", "rel-a/a2": "unmigrated", "rel-a/a3": "unmigrated"}
    # pausing keeps what moved: inventory labels the others unmigrated
    inv = {i["id"]: i["status"] for i in w.inventory("--offline")["items"]}
    assert inv == st
    # a later run selecting another keeps the first migrated
    w.commit()
    assert w.apply("rel-a/a3")[0] == 0
    st = {k: v["status"] for k, v in w.index()["items"].items()}
    assert st == {"rel-a/a1": "migrated", "rel-a/a2": "unmigrated", "rel-a/a3": "migrated"}
    # with index.yaml DELETED, a run keeps migrated items migrated (from migrated_to:) and does not demote
    w.commit()
    os.unlink(w.p(".foundry/migration/index.yaml"))
    inv = {i["id"]: i["status"] for i in w.inventory("--offline")["items"]}
    assert inv["rel-a/a1"] == "migrated" and inv["rel-a/a3"] == "migrated"
    rc, out, err = w.apply("rel-a/a2")
    assert rc == 0, out + err
    st = {k: v["status"] for k, v in w.index()["items"].items()}
    assert st == {"rel-a/a1": "migrated", "rel-a/a2": "migrated", "rel-a/a3": "migrated"}


# ===================================================================================== AC-CUT-9
@pytest.mark.parametrize("trailing_newline", [True, False])
def test_manifest_gains_migrated_to_by_append_only_and_reparse(trailing_newline, tmp_path, monkeypatch):
    files = WS.basic_files()
    files[".foundry/releases/rel-a/release.yaml"] = WS.REL_A if trailing_newline else WS.REL_A.rstrip("\n")
    w = W(tmp_path / "main", monkeypatch, files=files)
    mp = ".foundry/releases/rel-a/release.yaml"
    pre = w.p(mp).read_bytes()
    assert w.apply("rel-a/a1")[0] == 0
    post = w.p(mp).read_bytes()
    assert post.startswith(pre) and len(post) > len(pre)
    url1 = w.repo_state()["issues"][0]["html_url"]
    assert w.manifest()["migrated_to"] == {"rel-a/a1": url1}
    assert b"# a comment that must survive" in post and b"# trailing comment" in post
    w.commit()
    pre2 = w.p(mp).read_bytes()
    assert w.apply("rel-a/a3")[0] == 0
    post2 = w.p(mp).read_bytes()
    assert post2.startswith(pre2)
    mt = w.manifest()["migrated_to"]
    assert set(mt) == {"rel-a/a1", "rel-a/a3"} and mt["rel-a/a1"] == url1 and post2.count(b"migrated_to:") == 1

    # a stubbed write that corrupts the entry: re-parse mismatch, item left pending, exit non-zero
    for n, corrupt in enumerate((lambda b: b.replace(b"https://", b"http://"), lambda b: b + b"\n\t- [broken: {\n")):
        files = WS.basic_files()
        files[mp] = WS.REL_A
        c = W(tmp_path / f"corrupt{n}", monkeypatch, files=files)
        pre_c = c.p(mp).read_bytes()
        rc, out, _ = c.apply("rel-a/a1", opts=M.Options(manifest_append_hook=corrupt))
        assert rc != 0
        assert c.p(mp).read_bytes() == pre_c
        assert c.index()["items"]["rel-a/a1"]["status"] == "pending"


# ==================================================================================== AC-CUT-10
def test_retained_bytes_identical_and_writes_confined(w):
    inv = w.inventory("--offline")
    keep = {r["path"] for r in inv["paths"] if r["class"] in ("retained-history", "current-knowledge")
            and not r["path"].startswith(".foundry/migration/")}
    before = tree(w.root)
    frozen_spec = "specs/features/foundry/demo/feat-a.md"
    frozen_contract = "specs/features/foundry/demo/acceptance-contract.yaml"
    assert w.apply("rel-a/a1,rel-a/a2")[0] == 0
    after = tree(w.root)
    for p in keep:
        assert after[p] == before[p], p
    assert after[frozen_spec] == before[frozen_spec] and after[frozen_contract] == before[frozen_contract]
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert changed <= {k for k in changed if k.startswith(".foundry/migration/")} | {".foundry/releases/rel-a/release.yaml"}
    assert ".foundry/migration/index.yaml" in changed
    # nothing outside the workspace root was written next to it
    assert not any(Path(w.root).parent.glob("ws.*"))


def test_paths_confined_and_argv_discrete_no_shell(tmp_path, monkeypatch):
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n")
    base = WS.basic_files()

    def with_ref(ref, extra_files=None):
        files = dict(base)
        files[".foundry/releases/rel-a/release.yaml"] = WS.REL_A.replace(
            "spec_ref: specs/features/foundry/demo/b/feat-b.md", f"spec_ref: {ref}")
        files.update(extra_files or {})
        return files

    # (b1) ../outside.md
    w = W(tmp_path / "a", monkeypatch, files=with_ref("../outside.md"))
    rc, out, _ = w.apply("rel-a/a2")
    assert rc == 1 and "source-outside-workspace" in out and w.creates() == []
    # (b2) a tracked symlink pointing outside
    w = W(tmp_path / "b", monkeypatch, files=with_ref("specs/link.md"))
    os.symlink(str(outside), w.p("specs/link.md"))
    w.commit()
    rc, out, _ = w.apply("rel-a/a2")
    assert rc == 1 and "source-outside-workspace" in out and w.creates() == []
    # (b3) an absolute path
    w = W(tmp_path / "c", monkeypatch, files=with_ref(str(outside)))
    rc, out, _ = w.apply("rel-a/a2")
    assert rc == 1 and "source-outside-workspace" in out
    # (c) hostile H1 arrives as ONE --title element; nothing executes
    hostile = "Feat $(touch pwned); --repo x `id`"
    files = dict(base)
    files["specs/features/foundry/demo/feat-a.md"] = base["specs/features/foundry/demo/feat-a.md"].replace(
        "# Feature A", f"# {hostile}")
    w = W(tmp_path / "d", monkeypatch, files=files)
    cwd = os.getcwd()
    rc, out, err = w.apply("rel-a/a1")
    assert rc == 0, out + err
    create = w.creates()[0]
    assert create[create.index("--title") + 1] == f"rel-a/a1: {hostile}"
    assert [create[i + 1] for i, a in enumerate(create) if a == "--repo"] == [TARGET]
    assert create.count("--title") == 1 and sum(1 for a in create if "pwned" in a) == 1
    assert not w.p("pwned").exists() and not (Path(cwd) / "pwned").exists()
    assert not any(Path(w.root).rglob("pwned"))


# ==================================================================================== AC-CUT-11
CRASH_STATE = {
    1: dict(sidecar=False, manifest=False, index="pending", creates_first=0),
    2: dict(sidecar=False, manifest=False, index="pending", creates_first=1),
    3: dict(sidecar=True, manifest=False, index="pending", creates_first=1),
    4: dict(sidecar=True, manifest=True, index="pending", creates_first=1),
}


@pytest.mark.parametrize("step", [1, 2, 3, 4])
def test_resume_from_every_crash_point_in_fixed_order(step, w):
    with pytest.raises(M.Killed):
        w.apply("rel-a/a1", opts=M.Options(crash_after=step))
    exp = CRASH_STATE[step]
    assert len(w.creates()) == exp["creates_first"]
    assert w.p(".foundry/migration/items/rel-a/a1.yaml").exists() is exp["sidecar"]
    assert ("migrated_to" in w.manifest()) is exp["manifest"]
    assert w.index()["items"]["rel-a/a1"]["status"] == exp["index"]
    w.log_file.write_text("")
    rc, out, err = w.apply("rel-a/a1")
    assert rc == 0, out + err
    argvs = w.argvs()
    listing = ["api", "--paginate", f"repos/{TARGET}/issues?state=all&per_page=100"]
    assert listing in argvs
    resumed_creates = len(w.creates())
    assert resumed_creates == (1 if step == 1 else 0)
    assert argvs.index(listing) < min([i for i, a in enumerate(argvs) if a[:2] == ["issue", "create"]] or [len(argvs)])
    issues = [i for i in w.repo_state()["issues"] if f"{MARKER_PREFIX}rel-a/a1 -->" in i["body"]]
    assert len(issues) == 1
    assert w.p(".foundry/releases/rel-a/release.yaml").read_text().count('"rel-a/a1"') == 1
    assert w.index()["items"]["rel-a/a1"]["status"] == "migrated"
    assert w.manifest()["migrated_to"]["rel-a/a1"] == issues[0]["html_url"]


def test_selected_partial_item_healed_complete_item_untouched(w):
    # every pending item is processed whether or not it is selected
    with pytest.raises(M.Killed):
        w.apply("rel-a/a1", opts=M.Options(crash_after=2))
    rc, out, err = w.apply("rel-a/a3")
    assert rc == 0, out + err
    assert w.index()["items"]["rel-a/a1"]["status"] == "migrated"
    assert w.index()["items"]["rel-a/a3"]["status"] == "migrated"
    assert len(w.repo_state()["issues"]) == 2
    w.commit()
    # a migrated item with its migrated_to entry removed is healed by re-append (no new issue)
    mp = ".foundry/releases/rel-a/release.yaml"
    w.p(mp).write_text(WS.REL_A)
    n = len(w.creates())
    rc, out, err = w.apply("rel-a/a1")
    assert rc == 0, out + err
    assert len(w.creates()) == n
    mt = w.manifest()["migrated_to"]
    assert mt["rel-a/a1"] == w.index()["items"]["rel-a/a1"]["issue_url"]
    assert w.p(mp).read_text().count('"rel-a/a1"') == 1
    # a complete selected item is reported already-migrated with no write
    w.commit()
    before = tree(w.root)
    rc, out, _ = w.apply("rel-a/a1")
    assert rc == 0 and "already-migrated rel-a/a1" in out
    assert tree(w.root) == {**before, **{k: v for k, v in tree(w.root).items() if k == ".foundry/migration/.lock"}}
    assert len(w.creates()) == n


# ==================================================================================== AC-CUT-12
def _inv_manifest(w, text):
    w.p(".foundry/releases/rel-a/release.yaml").write_text(text)
    w.commit()


def _case_visibility(v):
    def f(w):
        w.repo_state()["visibility"] = v
        st = w.state()
        st["repos"][TARGET]["visibility"] = v
        w.state_file.write_text(json.dumps(st))
    return f


def _gh404(w):
    st = w.state()
    st["repos"][TARGET]["error"] = True
    w.state_file.write_text(json.dumps(st))


def _unset_origin_head(w):
    w.git("symbolic-ref", "--delete", "refs/remotes/origin/HEAD")


def _unpushed_commit(w):
    w.p("docs/new.md").write_text("x\n")
    w.git("add", "-A")
    w.git("commit", "-q", "-m", "unpushed")


def _dirty(w):
    w.p("CLAUDE.md").write_text(w.p("CLAUDE.md").read_text() + "\nlocal edit\n")


def _write_index(w, **kw):
    d = {"schema_version": 1, "tool_version": M.TOOL_VERSION, "workspace": WS.WS_SLUG, "target_repo": TARGET,
         "items": {}}
    d.update(kw)
    w.p(".foundry/migration").mkdir(exist_ok=True)
    w.p(".foundry/migration/index.yaml").write_text(yaml.safe_dump(d))
    w.commit()


def _bad_dep(w):
    _inv_manifest(w, WS.REL_A.replace("depends_on: [a1]", "depends_on: [nope]"))


def _cycle(w):
    _inv_manifest(w, WS.REL_A.replace("depends_on: []", "depends_on: [a2]"))


def _not_last(w):
    _inv_manifest(w, WS.REL_A.replace("gates:\n  merge: pending\n", "migrated_to:\n  rel-a/zzz: x\ngates:\n  merge: pending\n"))


def _missing_source(w):
    _inv_manifest(w, WS.REL_A.replace("feat-b.md", "gone.md"))


def _lock_held(w):
    w.p(".foundry/migration").mkdir(exist_ok=True)
    fd = os.open(w.p(".foundry/migration/.lock"), os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    w._held = fd


def _project_public(w):
    w.set_state(projects=[{"title": "P", "public": True}])


def _listing_fails(w):
    w.set_state(fail=[["api", "--paginate"]])


REFUSALS = {
    "visibility-internal": (_case_visibility("INTERNAL"), [], "repo-not-private", None),
    "visibility-public": (_case_visibility("PUBLIC"), [], "repo-not-private", None),
    "gh-404": (_gh404, [], "repo-not-private", None),
    "lock-held": (_lock_held, [], "migration-locked", None),
    "origin-head-unset": (_unset_origin_head, [], "head-not-on-default-branch", None),
    "head-not-on-default": (_unpushed_commit, [], "head-not-on-default-branch", None),
    "dirty-tracked-file": (_dirty, [], "working-tree-dirty", None),
    "target-repo-changed": (lambda w: _write_index(w, target_repo="example-org/other"), [], "target-repo-changed", None),
    "schema-newer": (lambda w: _write_index(w, schema_version=2), [], "schema-newer", None),
    "project-not-private": (_project_public, ["--project", "P"], "project-not-private", None),
    "project-absent": (lambda w: None, ["--project", "Nope"], "project-not-private", None),
    "listing-incomplete": (_listing_fails, [], "issue-listing-incomplete", None),
    "not-an-item-release": (lambda w: None, [], "not-an-item", "rel-a"),
    "not-an-item-unknown": (lambda w: None, [], "not-an-item", "nope/x"),
    "owner-invalid-dash": (lambda w: None, ["--owner=--x"], "owner-invalid", "rel-a/a2"),
    "owner-invalid-slash": (lambda w: None, ["--owner=a/b"], "owner-invalid", "rel-a/a2"),
    "priority-invalid": (lambda w: None, ["--priority=!bad"], "priority-invalid", "rel-a/a2"),
    "source-missing": (_missing_source, [], "source-missing", "rel-a/a2"),
    "dependency-unknown": (_bad_dep, [], "dependency-invalid", "rel-a/a2"),
    "dependency-cycle": (_cycle, [], "dependency-invalid", "rel-a/a1"),
    "migrated-to-not-last": (_not_last, [], "migrated-to-not-last", "rel-a/a1"),
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_cutover_refusals_name_token_and_write_nothing(case, tmp_path, monkeypatch):
    w = W(tmp_path, monkeypatch)
    setup, extra, token, item = REFUSALS[case]
    setup(w)
    select = item if (item and case.startswith("not-an-item")) else (item or "rel-a/a1")
    if item in ("rel-a/a2", "rel-a/a1") and not case.startswith("not-an-item"):
        select = item
    args = ["cutover", "--apply", "--repo", TARGET, "--select", select]
    if not any(e.startswith("--owner") for e in extra):
        args += ["--owner", "owner-x"]
    args += extra
    before = tree(w.root)
    w.log_file.write_text("")
    try:
        rc, out, err = w.run(*args)
    finally:
        if getattr(w, "_held", None) is not None:
            os.close(w._held)
    assert rc != 0, out
    assert token in out, out
    if item:
        assert f"item={item}" in out, out
    after = tree(w.root)
    assert {k for k in set(before) | set(after) if before.get(k) != after.get(k)} <= {".foundry/migration/.lock"}
    assert w.creates() == []


def test_cutover_flag_refusal_then_override_applies(tmp_path, monkeypatch):
    g = W(tmp_path, monkeypatch, files=WS.golden_files())
    rc, out, _ = g.apply("rel-live/shipped-one")
    assert rc == 1 and "flagged:likely-shipped item=rel-live/shipped-one" in out and "flagged:program-closed" in out
    assert g.creates() == []
    rc, out, err = g.apply("rel-live/shipped-one", "--override-flag", "rel-live/shipped-one=likely-shipped",
                           "--override-flag", "rel-live/shipped-one=program-closed")
    assert rc == 0, out + err
    sc = yaml.safe_load(g.p(".foundry/migration/items/rel-live/shipped-one.yaml").read_text())
    assert sc["flags_overridden"] == ["likely-shipped", "program-closed"]


def test_gh_failure_mid_run_keeps_completed_items(w):
    w.set_state(fail_create_after=1)
    rc, out, _ = w.apply("rel-a/a1,rel-a/a2")
    assert rc == 1 and "gh-call-failed" in out
    idx = w.index()["items"]
    assert idx["rel-a/a1"]["status"] == "migrated" and idx["rel-a/a2"]["status"] == "pending"
    mt = w.manifest()["migrated_to"]
    assert set(mt) == {"rel-a/a1"}
    assert w.p(".foundry/migration/items/rel-a/a1.yaml").exists()
    assert not w.p(".foundry/migration/items/rel-a/a2.yaml").exists()


# ==================================================================================== AC-CUT-13
AUTH_FIELDS = {"schema_version", "recorded_by", "at", "planning_authority", "scope", "instructions_reconciled",
               "workspace", "head"}


def _migrated_ws(w):
    assert w.apply("rel-a/a1")[0] == 0
    assert not w.p(".foundry/migration/authority.yaml").exists()      # no other subcommand creates it
    w.commit()


def test_authority_record_only_by_registered_operator(tmp_path, monkeypatch):
    w = W(tmp_path, monkeypatch)
    _migrated_ws(w)
    rec = ["cutover", "record", "--operator", "op_test", "--scope", "rel-a planning moves to GitHub"]
    auth = w.p(".foundry/migration/authority.yaml")

    def refused(args, token):
        before = auth.read_bytes() if auth.exists() else None
        rc, out, _ = w.run(*args)
        assert rc != 0 and token in out, out
        assert (auth.read_bytes() if auth.exists() else None) == before

    refused(["cutover", "record", "--operator", "op_nobody", "--scope", "s"], "operator-not-registered")
    refused(["cutover", "record", "--scope", "s"], "operator-missing")
    refused(["cutover", "record", "--operator", "op_test", "--scope", ""], "scope-empty")
    refused(rec + ["--reconciled", "MISSING.md"], "reconciled-path-invalid")
    refused(rec + ["--reconciled", "docs/note.md"], "reconciled-path-invalid")
    # dry-run prints the record and writes nothing
    rc, out, _ = w.run(*rec, "--reconciled", "CLAUDE.md", "--dry-run")
    assert rc == 0 and "planning_authority: github" in out and not auth.exists()
    rc, out, err = w.run(*rec, "--reconciled", "CLAUDE.md")
    assert rc == 0, out + err
    doc = yaml.safe_load(auth.read_text())
    assert set(doc) == AUTH_FIELDS
    assert doc["recorded_by"] == "op_test" and doc["planning_authority"] == "github"
    assert doc["instructions_reconciled"] == ["CLAUDE.md"] and doc["workspace"] == WS.WS_SLUG
    assert doc["head"] == w.head() and doc["scope"] == "rel-a planning moves to GitHub" and doc["schema_version"] == 1
    refused(rec, "authority-record-exists")                            # O_EXCL: left as it was
    # a pending index item
    p = W(tmp_path / "p", monkeypatch)
    with pytest.raises(M.Killed):
        p.apply("rel-a/a1", opts=M.Options(crash_after=2))
    rc, out, _ = p.run(*rec)
    assert rc != 0 and "index-has-pending-item" in out and not p.p(".foundry/migration/authority.yaml").exists()
    # zero migrated items needs --acknowledge-empty
    e = W(tmp_path / "e", monkeypatch)
    rc, out, _ = e.run(*rec)
    assert rc != 0 and "no-migrated-item-without-acknowledge-empty" in out
    rc, out, err = e.run(*rec, "--acknowledge-empty")
    assert rc == 0, out + err
    # single-operator workspace: cutover (every form) is a team-mode surface and is refused
    files = WS.basic_files()
    del files[".claude/foundry-project.json"]
    s = W(tmp_path / "s", monkeypatch, files=files)
    rc, out, err = s.run(*rec, "--acknowledge-empty")
    assert rc == 2 and "team-mode verb" in err and s.argvs() == []
    assert s.run("inventory", "--offline")[0] == 0                       # read-only verbs run in both modes


# ==================================================================================== AC-CUT-14
def diag(js, name):
    return [d for d in js["diagnostics"] if d["name"] == name]


def test_diagnostics_named_with_next_action_tokens(tmp_path, monkeypatch):
    w = W(tmp_path, monkeypatch)
    js = w.inventory("--offline")
    assert diag(js, "cutover-pending")[0]["next_action"] == "record-cutover-when-decided"
    assert js["closed_issue_check"]["status"] == "skipped" and js["closed_issue_check"]["reason"] == "offline"
    assert w.argvs() == []                                                # --offline makes no gh call

    # interrupted
    with pytest.raises(M.Killed):
        w.apply("rel-a/a1", opts=M.Options(crash_after=2))
    js = w.inventory("--offline")
    assert diag(js, "migration-interrupted")[0]["next_action"] == "rerun-cutover-apply"
    assert w.apply("rel-a/a1")[0] == 0
    w.commit()
    js = w.inventory()
    assert js["closed_issue_check"]["status"] == "ok"
    assert not diag(js, "migration-interrupted") and not diag(js, "migrated-item-reactivated")
    assert not diag(js, "migration-record-inconsistent") and not diag(js, "migration-tool-version-differs")

    # manifest entry removed -> reactivated; next action depends on the authority record
    mp = ".foundry/releases/rel-a/release.yaml"
    good = w.p(mp).read_text()
    w.p(mp).write_text(WS.REL_A)
    d = diag(w.inventory("--offline"), "migrated-item-reactivated")
    assert d and d[0]["next_action"] == "restore-before-cutover" and d[0]["item"] == "rel-a/a1"
    auth = w.p(".foundry/migration/authority.yaml")
    w.p(mp).write_text(good)
    assert w.run("cutover", "record", "--operator", "op_test", "--scope", "s")[0] == 0
    js = w.inventory("--offline")
    assert not diag(js, "cutover-pending")
    w.p(mp).write_text(WS.REL_A)
    d = diag(w.inventory("--offline"), "migrated-item-reactivated")
    assert d[0]["next_action"] == "repair-after-cutover"
    good_auth = auth.read_text()
    for label, content in (("empty", ""), ("corrupt", "a: [unterminated\n"),
                           ("foreign", good_auth.replace(WS.WS_SLUG, "someone/else")),
                           ("missing-field", "\n".join(l for l in good_auth.splitlines() if not l.startswith("scope")))):
        auth.write_text(content)
        js = w.inventory("--offline")
        u = diag(js, "authority-record-unreadable")
        assert u and u[0]["next_action"] == "repair-authority-record", label
        assert diag(js, "migrated-item-reactivated")[0]["next_action"] == "repair-authority-record", label
    auth.write_text(good_auth)
    w.p(mp).write_text(good)

    # index deleted while a migrated_to entry exists -> inconsistent; the item stays migrated
    idxp = w.p(".foundry/migration/index.yaml")
    saved = idxp.read_text()
    idxp.unlink()
    js = w.inventory("--offline")
    assert diag(js, "migration-record-inconsistent")[0]["next_action"] == "reconcile-migration-record"
    assert {i["id"]: i["status"] for i in js["items"]}["rel-a/a1"] == "migrated"
    # URL on another repository
    doc = yaml.safe_load(saved)
    doc["items"]["rel-a/a1"]["issue_url"] = "https://github.com/someone/else/issues/1"
    idxp.write_text(yaml.safe_dump(doc))
    assert diag(w.inventory("--offline"), "migration-record-inconsistent")
    # tool version differs
    doc = yaml.safe_load(saved)
    doc["tool_version"] = "0"
    idxp.write_text(yaml.safe_dump(doc))
    d = diag(w.inventory("--offline"), "migration-tool-version-differs")
    assert d and d[0]["next_action"] == "align-migration-tool-version"
    idxp.write_text(saved)
    # closed not_planned on a migrated item (online) -> reactivated
    st = w.state()
    st["repos"][TARGET]["issues"][0]["state"] = "closed"
    st["repos"][TARGET]["issues"][0]["state_reason"] = "not_planned"
    w.state_file.write_text(json.dumps(st))
    d = diag(w.inventory(), "migrated-item-reactivated")
    assert d and d[0]["next_action"] == "repair-after-cutover"
    # a failing gh read reports the check as skipped, never clear
    w.set_state(fail=[["api", "--paginate"]])
    js = w.inventory()
    assert js["closed_issue_check"]["status"] == "skipped" and "gh-read-failed" in js["closed_issue_check"]["reason"]


def test_diagnostics_restore_before_cutover_then_issue_closed(tmp_path, monkeypatch):
    w = W(tmp_path, monkeypatch)
    assert w.apply("rel-a/a1")[0] == 0
    # restore-before-cutover: discard the change set, close the issue as not planned
    w.git("checkout", "--", ".")
    import shutil
    shutil.rmtree(w.p(".foundry/migration"))
    st = w.state()
    st["repos"][TARGET]["issues"][0]["state"] = "closed"
    st["repos"][TARGET]["issues"][0]["state_reason"] = "not_planned"
    w.state_file.write_text(json.dumps(st))
    js = w.inventory()
    assert not diag(js, "migrated-item-reactivated") and not diag(js, "migration-record-inconsistent")
    rc, out, _ = w.apply("rel-a/a1")
    assert rc == 1 and "marker-issue-closed-not-planned" in out
    rc, out, err = w.apply("rel-a/a1", "--reopen")
    assert rc == 0, out + err
    assert w.repo_state()["issues"][0]["state"] == "open" and len(w.repo_state()["issues"]) == 1
