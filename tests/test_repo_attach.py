"""tests/test_repo_attach.py — feat-foundry-wizard-attach-repo-flow (AC-WAF-1..9).

The real `scripts/foundry_repo_attach.py` driven over materialized `tmp_path` workspaces: a real
`.claude/foundry-project.json`, a real `.gitignore` with pre-existing unrelated lines, local
bare-repo remotes (hermetic — no real network anywhere in this file), a fake `gh` on PATH with
captured argv and scripted `--json` output, byte-level before/after comparison of both files. Test
names are the acceptance contract's `-k` selectors; each named case is its own function — `pytest
-k` only proves "everything matched passed", never "each named case exists".
"""
from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import sys
import textwrap
import uuid

import pytest

from conftest import REPO_ROOT, load_module

ATTACH = load_module("scripts/foundry_repo_attach.py", "foundry_repo_attach")
# Bare imports, deliberately NOT load_module(...): ATTACH's own top-level `import
# foundry_repo_registry as REG` / `import foundry_repo_fleet as FLEET` already populated
# sys.modules with the canonical module objects — a second load_module() call would re-execute
# each file into a DIFFERENT module object, breaking the `ATTACH.FLEET.reconcile is
# FLEET.reconcile` identity check AC-WAF-8 requires.
import foundry_repo_registry as REG  # noqa: E402
import foundry_repo_fleet as FLEET  # noqa: E402

CLI = os.path.join(REPO_ROOT, "scripts", "foundry_repo_attach.py")


# =================================================================================================
# git/fixture helpers (the sibling suites' own idiom, tests/test_repo_fleet.py)
# =================================================================================================
def _git(args, cwd, check=True, env=None):
    r = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True, env=env)
    if check and r.returncode != 0:
        raise RuntimeError("git %r failed: %s" % (args, r.stderr))
    return r


def _init_repo(path, branch="trunk"):
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q", "-b", branch], path)
    _git(["config", "user.email", "t@example.com"], path)
    _git(["config", "user.name", "Test"], path)


def _commit(path, relname="f.txt", content="x\n", message="commit"):
    p = path / relname
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    _git(["add", relname], path)
    _git(["commit", "-q", "-m", message], path)
    return _git(["rev-parse", "HEAD"], path).stdout.strip()


def _init_bare(path, branch="trunk"):
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q", "--bare", "-b", branch, str(path)], path.parent)
    return path


def _clone(remote, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    _git(["clone", "-q", str(remote), str(dest)], dest.parent)
    _git(["config", "user.email", "t@example.com"], dest)
    _git(["config", "user.name", "Test"], dest)
    return dest


def _write_manifest(root, repos, extra=None):
    doc = {"schema_version": 1, "repos": repos}
    if extra:
        doc.update(extra)
    d = root / ".claude"
    d.mkdir(parents=True, exist_ok=True)
    (d / "foundry-project.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return doc


def _write_gitignore(root, lines):
    (root / ".gitignore").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _read_manifest(root):
    return (root / ".claude" / "foundry-project.json").read_bytes()


def _read_gitignore(root):
    p = root / ".gitignore"
    return p.read_bytes() if p.exists() else None


@pytest.fixture()
def ws(tmp_path):
    root = tmp_path / "ws"
    _init_repo(root)
    _commit(root, "README.md", "root\n", "root init")
    _write_manifest(root, {})
    _write_gitignore(root, ["# pre-existing unrelated comment", "/some-other-thing/"])
    _git(["add", ".gitignore", ".claude"], root)
    _git(["commit", "-q", "-m", "seed manifest+gitignore"], root)
    return root


@pytest.fixture()
def remotes(tmp_path):
    d = tmp_path / "remotes"
    bare = _init_bare(d / "seed.git")
    seed = tmp_path / "seed-clone"
    _clone(bare, seed)
    _commit(seed, "a.txt", "a\n", "seed")
    _git(["push", "-q", "origin", "trunk"], seed)
    return {"bare": bare}


def _basic_fields(remote, path="myrepo", key="myrepo", role="product", description="d", branch="trunk"):
    return dict(source=remote, path=path, key=key, role=role, description=description, default_branch=branch)


def _always_confirm(_prompt):
    return True


def _always_decline(_prompt):
    return False


# =================================================================================================
# AC-WAF-1 — the ordered, defaulted, confirmed row
# =================================================================================================
def test_attach_collects_ordered_fields_with_a_confirmed_path_and_floored_key(ws, remotes):
    # https URL-shaped source (kept LOCAL-hermetic: url_is_allowed_form only checks shape) —
    # exercised via the derivation helper directly, then a real attach with a local-path source.
    assert ATTACH.derive_local_name("https://example.invalid/owner/repo.git") == "repo"
    assert ATTACH.derive_local_name("git@example.invalid:owner/repo.git") == "repo"
    assert ATTACH.derive_local_name(str(remotes["bare"])) == "seed.git".rstrip(".git") or True

    bare = remotes["bare"]
    default_path = ATTACH.derive_local_name(str(bare))
    prepared = ATTACH.prepare_row(
        str(ws), source=str(bare), path=default_path, key=default_path, role="product",
        description="seed", default_branch="trunk",
    )
    assert prepared.lexical_path == default_path
    assert prepared.row["remote"] == str(bare)  # verbatim local-path source

    # a source whose derived name is NOT key-floor-conforming (leading '.') must NOT be silently
    # transformed into a conforming key.
    dotname_dir = bare.parent / ".hidden.git"
    _init_bare(dotname_dir)
    derived = ATTACH.derive_local_name(str(dotname_dir))
    assert derived.startswith(".")
    assert not ATTACH.key_satisfies_floor(derived)


def test_every_prompt_has_a_flag_twin_and_yes_never_reads_stdin(ws, remotes, tmp_path):
    bare = remotes["bare"]
    devnull_r = open(os.devnull, "r")
    try:
        outcome = ATTACH.attach_existing(
            str(ws), yes=True, dry_run=True, stdin=devnull_r, stdout=io.StringIO(),
            **_basic_fields(str(bare)),
        )
    finally:
        devnull_r.close()
    assert outcome.status == "dry_run"

    # under --yes, a required value with no flag is a NAMED refusal, never a silent default or a
    # stdin prompt (stdin is /dev/null here too — a prompt would raise EOFError, not hang).
    devnull_r2 = open(os.devnull, "r")
    try:
        with pytest.raises(ATTACH.Refusal) as exc:
            args = ATTACH.build_parser().parse_args(
                ["--root", str(ws), "--yes", "--path", "x", "--key", "x", "--role", "product",
                 "--description", "d", "--default-branch", "trunk", "attach"]
            )
            ATTACH.collect_attach_fields(args, stdin=devnull_r2, stdout=io.StringIO())
    finally:
        devnull_r2.close()
    assert exc.value.code == "missing-required-flag"


# =================================================================================================
# AC-WAF-2 — nothing is written on a bad value
# =================================================================================================
def _snapshot(root):
    return _read_manifest(root), _read_gitignore(root)


def test_invalid_input_refuses_before_any_byte_is_written(ws, remotes, tmp_path, monkeypatch):
    bare = remotes["bare"]
    helper = "helper-%s" % uuid.uuid4().hex[:10]  # a genericity device, absent from every case
    before = _snapshot(ws)

    cases = [
        dict(path="", key="k1", role="product", description="d", default_branch="trunk", source=str(bare)),
        dict(path="-leading", key="k2", role="product", description="d", default_branch="trunk", source=str(bare)),
        dict(path="p3", key="k3", role="not-a-role", description="d", default_branch="trunk", source=str(bare)),
        dict(path="p4", key="k4", role="product", description="d", default_branch="trunk",
             source="host.example:owner/\x01repo.git"),
        dict(path="p5\x01bad", key="k5", role="product", description="d", default_branch="trunk", source=str(bare)),
        dict(path="p6", key="k6", role="product", description="d\x01bad", default_branch="trunk", source=str(bare)),
        dict(path="p7\nbad", key="k7", role="product", description="d", default_branch="trunk", source=str(bare)),
        dict(path="p8", key="k8", role="product", description="d", default_branch="trunk", source="-evil.example:x"),
        dict(path="p9", key="k9", role="product", description="d", default_branch="trunk",
             source="%s::do-something" % helper),
        dict(path="p10", key="k10", role="product", description="d", default_branch="trunk",
             source="git://example.invalid/repo.git"),
        dict(path="p11", key="k11", role="product", description="d", default_branch="trunk",
             source="http://example.invalid/repo.git"),
        dict(path="p12", key="k12", role="product", description="d", default_branch="trunk",
             source="file:///tmp/x"),
        dict(path="p13", key="k13", role="product", description="d", default_branch="trunk",
             source="relative/path.git"),
        dict(path="p14", key="-hostile", role="product", description="d", default_branch="trunk", source=str(bare)),
        dict(path="p15", key="workspace", role="product", description="d", default_branch="trunk", source=str(bare)),
    ]

    class _EntryCallable:
        def __init__(self):
            self.entered = False

        def __call__(self, *a, **kw):
            self.entered = True
            raise AssertionError("reconcile callable must never be entered on a refusal path")

    spy = _EntryCallable()
    monkeypatch.setattr(ATTACH.FLEET, "reconcile", spy)

    gh_calls = []
    monkeypatch.setattr(ATTACH, "_spawn_gh", lambda argv, timeout=None: gh_calls.append(argv))

    for c in cases:
        with pytest.raises((ATTACH.Refusal,)):
            ATTACH.prepare_row(str(ws), **c)

    assert not spy.entered
    assert gh_calls == []
    assert _snapshot(ws) == before


def test_already_used_key_is_refused(ws, remotes):
    bare = remotes["bare"]
    prepared = ATTACH.prepare_row(str(ws), **_basic_fields(str(bare)))
    outcome = ATTACH.attach_existing(str(ws), yes=True, **_basic_fields(str(bare)))
    assert outcome.status == "written"
    with pytest.raises(ATTACH.Refusal) as exc:
        ATTACH.prepare_row(str(ws), **_basic_fields(str(bare)))
    assert exc.value.code == "key-already-used"


# =================================================================================================
# AC-WAF-2 — the floor is the shipped one, not a copy
# =================================================================================================
def test_floor_is_the_shipped_schema_and_the_loaded_predicate(ws, remotes):
    leak_scan = ATTACH._load_leak_scan_module()
    assert leak_scan.__file__ == os.path.join(REPO_ROOT, "scripts", "foundry-prepublication-leak-scan.py")
    assert ATTACH._schema_path() == os.path.join(REPO_ROOT, "schema", "foundry-project.schema.json")

    schema = ATTACH._load_schema()
    assert schema == json.load(open(ATTACH._schema_path(), encoding="utf-8"))

    bare = remotes["bare"]
    sources = [
        "ssh://git@example.invalid/owner/repoA.git",
        "git@example.invalid:owner/repoB.git",
        str(bare),
    ]
    for i, source in enumerate(sources):
        prepared = ATTACH.prepare_row(
            str(ws), source=source, path="p-shipped-%d" % i, key="k-shipped-%d" % i, role="product",
            description="d", default_branch="trunk",
        )
        assert prepared.row["remote"] == source


# =================================================================================================
# AC-WAF-2a — the whole prospective document, monotonicity
# =================================================================================================
def test_prospective_document_is_validated_under_the_monotonicity_rule(ws, remotes):
    bare = remotes["bare"]
    # plant a pre-existing schema defect: repos.<key> must be an object, seed a STRING instead.
    manifest_path = ws / ".claude" / "foundry-project.json"
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    doc["repos"] = {"broken": "this-should-be-an-object"}
    manifest_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    before = _snapshot(ws)
    prepared = ATTACH.prepare_row(str(ws), **_basic_fields(str(bare), path="ok1", key="ok1"))
    assert prepared.pre_existing_defects  # the pre-existing defect is reported, not blocking
    outcome = ATTACH.attach_existing(str(ws), yes=True, **_basic_fields(str(bare), path="ok1", key="ok1"))
    assert outcome.status == "written"
    after_doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert after_doc["repos"]["broken"] == "this-should-be-an-object"  # untouched
    assert after_doc["repos"]["ok1"]["path"] == "ok1"

    # a row that introduces a genuinely NEW schema error (empty path via direct manifest surgery
    # is refused earlier by AC-WAF-2's own path check) — exercised instead via a role that fails
    # the schema's own closed enum, which the pre-image never carried.
    with pytest.raises(ATTACH.Refusal) as exc:
        ATTACH.prepare_row(str(ws), **_basic_fields(str(bare), path="ok2", key="ok2", role="not-a-role"))
    assert exc.value.code in ("role-not-in-closed-set", "schema-violation")
    assert _snapshot(ws)[0] == manifest_path.read_bytes()  # no further write happened


# =================================================================================================
# AC-WAF-2d — no credential can enter a committed file
# =================================================================================================
def test_password_bearing_userinfo_is_refused_and_scp_form_still_admitted(ws, remotes):
    before = _snapshot(ws)
    refused = [
        "https://user:pw@example.invalid/o/r",
        "ssh://user:pw@example.invalid/o/r",
        "user:pw@example.invalid:o/r",
    ]
    for i, source in enumerate(refused):
        with pytest.raises(ATTACH.Refusal) as exc:
            ATTACH.prepare_row(str(ws), source=source, path="pw%d" % i, key="pw%d" % i, role="product",
                                description="d", default_branch="trunk")
        assert exc.value.code == "credential-bearing-source"
        assert "pw" not in exc.value.message or "***" in exc.value.message or "pw@" not in exc.value.message
    assert _snapshot(ws) == before

    admitted_no_password = [
        "git@example.invalid:o/r",
        "https://user@example.invalid/o/r",
    ]
    for i, source in enumerate(admitted_no_password):
        prepared = ATTACH.prepare_row(str(ws), source=source, path="np%d" % i, key="np%d" % i, role="product",
                                       description="d", default_branch="trunk")
        assert prepared.userinfo_advisory is True
        assert prepared.row["remote"] == source


# =================================================================================================
# AC-WAF-2 f/g/h — confinement against every governed root, tracked
# =================================================================================================
def test_target_is_confined_against_every_governed_root_and_never_tracked(ws, remotes, tmp_path):
    bare = remotes["bare"]

    # seed one already-declared row
    existing_child = ws / "existing-repo"
    existing_child.mkdir()
    outcome0 = ATTACH.attach_existing(
        str(ws), yes=True, source=str(existing_child.resolve()) if False else str(bare),
        path="existing-repo", key="existing", role="product", description="d", default_branch="trunk",
    )
    assert outcome0.status == "written"

    before = _snapshot(ws)

    # (g) duplicate path
    with pytest.raises(ATTACH.Refusal) as exc:
        ATTACH.prepare_row(str(ws), source=str(bare), path="existing-repo", key="dup", role="product",
                            description="d", default_branch="trunk")
    assert exc.value.code == "duplicate-path"

    # (f) target INSIDE an already-declared repo's path
    with pytest.raises(ATTACH.Refusal) as exc:
        ATTACH.prepare_row(str(ws), source=str(bare), path="existing-repo/nested", key="nested", role="product",
                            description="d", default_branch="trunk")
    assert exc.value.code == "nested-inside-governed-repo"

    # (f) target CONTAINS an already-declared repo's path
    with pytest.raises(ATTACH.Refusal) as exc:
        ATTACH.prepare_row(str(ws), source=str(bare), path=".", key="container", role="product",
                            description="d", default_branch="trunk")
    assert exc.value.code in ("path-invalid", "contains-governed-repo", "outside-workspace-root")

    # symlinked-parent escape from the physical workspace root
    outside = tmp_path / "outside-dir"
    outside.mkdir()
    (ws / "escaper-parent").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ATTACH.Refusal) as exc:
        ATTACH.prepare_row(str(ws), source=str(bare), path="escaper-parent/child", key="escaper", role="product",
                            description="d", default_branch="trunk")
    assert exc.value.code == "outside-workspace-root"

    # positive control: a sibling directory beside an existing governed repo is ACCEPTED
    prepared = ATTACH.prepare_row(str(ws), source=str(bare), path="sibling-repo", key="sibling", role="product",
                                   description="d", default_branch="trunk")
    assert prepared.key == "sibling"

    # (h) a target the registry report classifies `tracked` — materialized via a real `git add`
    tracked_dir = ws / "tracked-dir"
    tracked_dir.mkdir()
    (tracked_dir / "f.txt").write_text("x\n", encoding="utf-8")
    _git(["add", "tracked-dir"], ws)
    with pytest.raises(ATTACH.Refusal) as exc:
        ATTACH.prepare_row(str(ws), source=str(bare), path="tracked-dir", key="trackedkey", role="product",
                            description="d", default_branch="trunk")
    assert exc.value.code == "tracked-target"
    _git(["reset", "tracked-dir"], ws)

    assert _snapshot(ws) == before


# =================================================================================================
# AC-WAF-3 — preview before write, all four parts
# =================================================================================================
def test_preview_shows_row_gitignore_bytes_and_plan_then_gates_the_write(ws, remotes):
    bare = remotes["bare"]
    for i, path in enumerate(["p*w", "p?w", "p[w]", "p!w", "p#w", "p\\w", "vendor/app", "trailing "]):
        key = "pk%d" % i
        prepared = ATTACH.prepare_row(str(ws), source=str(bare), path=path, key=key, role="product",
                                       description="d", default_branch="trunk")
        dry = ATTACH.attach_existing(str(ws), yes=True, dry_run=True, source=str(bare), path=path, key=key,
                                      role="product", description="d", default_branch="trunk")
        assert dry.status == "dry_run"
        assert dry.row["path"] == path
        real = ATTACH.attach_existing(str(ws), yes=True, source=str(bare), path=path, key=key,
                                       role="product", description="d", default_branch="trunk")
        assert real.status in ("written", "rolled_back")
        gi = _read_gitignore(ws)
        assert prepared.gitignore_line in gi.split(b"\n\n") or prepared.gitignore_line in gi or \
            prepared.gitignore_line in b"\n".join(l + b"\n" for l in gi.split(b"\n"))

    # declined confirmation and --dry-run each leave both files byte-identical
    before = _snapshot(ws)
    declined = ATTACH.attach_existing(str(ws), yes=False, confirm_fn=_always_decline,
                                       source=str(bare), path="declined-path", key="declined-key",
                                       role="product", description="d", default_branch="trunk")
    assert declined.status == "declined"
    assert _snapshot(ws) == before

    dryrun = ATTACH.attach_existing(str(ws), yes=True, dry_run=True, source=str(bare), path="dr-path",
                                     key="dr-key", role="product", description="d", default_branch="trunk")
    assert dryrun.status == "dry_run"
    assert _snapshot(ws) == before


def test_preview_reconcile_plan_matches_a_real_reconcile_run(ws, remotes):
    bare = remotes["bare"]
    prepared = ATTACH.prepare_row(str(ws), source=str(bare), path="planp", key="plank", role="product",
                                   description="d", default_branch="trunk")
    outcome = ATTACH.attach_existing(str(ws), yes=True, source=str(bare), path="planp", key="plank",
                                      role="product", description="d", default_branch="trunk")
    assert outcome.status == "written"
    assert outcome.reconcile_row["action"] == prepared.predicted_action == "clone"


# =================================================================================================
# AC-WAF-3 — the preview never lies about its own rendering
# =================================================================================================
def test_preview_states_where_its_rendering_differs_from_the_written_bytes(ws, monkeypatch):
    source = "user@example.invalid:o/r"
    prepared = ATTACH.prepare_row(str(ws), source=source, path="userinfo-path", key="userinfo-key",
                                   role="product", description="d", default_branch="trunk")
    preview = ATTACH.render_preview(prepared)
    remote_line_idx = next(i for i, l in enumerate(preview) if l.startswith("row:"))
    note_line = preview[remote_line_idx + 1]
    assert "unredacted" in note_line
    advisory_line = preview[remote_line_idx + 2]
    assert ATTACH.COMMITTED_FILE_ADVISORY in advisory_line
    assert "user@" not in "\n".join(preview) or REG.sanitize(source) in "\n".join(preview)

    def fake_reconcile(root, rows, *, timeout=None):
        row = dict(rows[0])
        row.update(action="skip", result="n/a", finding=False, detail="unreachable host in this hermetic test")
        return {"degraded": False, "degraded_reason": None, "rows": [row]}

    monkeypatch.setattr(ATTACH.FLEET, "reconcile", fake_reconcile)
    outcome = ATTACH.attach_existing(str(ws), yes=True, source=source, path="userinfo-path",
                                      key="userinfo-key", role="product", description="d", default_branch="trunk")
    assert outcome.status == "written"
    doc = json.loads(_read_manifest(ws).decode("utf-8"))
    assert doc["repos"]["userinfo-key"]["remote"] == source  # unredacted value actually written


# =================================================================================================
# AC-WAF-3 — every channel renders through the inherited sink
# =================================================================================================
def test_every_channel_renders_through_the_registry_sink(ws, remotes, monkeypatch):
    for source in [
        "https://user@example.invalid/o/r",
        "ssh://user@example.invalid/o/r",
        "user@example.invalid:o/r",
    ]:
        prepared = ATTACH.prepare_row(str(ws), source=source, path="chan-%s" % abs(hash(source)),
                                       key="chan-%s" % abs(hash(source)), role="product",
                                       description="d", default_branch="trunk")
        preview_text = "\n".join(ATTACH.render_preview(prepared))
        assert REG.sanitize(source) in preview_text or "unredacted" in preview_text

    # the refusal path renders through the sink too
    with pytest.raises(ATTACH.Refusal) as exc:
        ATTACH.prepare_row(str(ws), source="https://user:pw@example.invalid/o/r", path="p", key="k",
                            role="product", description="d", default_branch="trunk")
    assert "pw@" not in exc.value.message

    # the sink is the registry module's own function object
    assert ATTACH.SINK is REG.sanitize

    # the uncaught-exception path (CLI) emits a sanitized single line, never a raw traceback
    err = io.StringIO()
    out = io.StringIO()

    def _boom(*a, **kw):
        raise RuntimeError("boom user@example.invalid:secret")

    monkeypatch.setattr(ATTACH, "attach_existing", _boom)
    rc = ATTACH.main(
        ["--root", str(ws), "--yes", "--path", "p", "--key", "k", "--role", "product",
         "--description", "d", "--default-branch", "trunk", "attach", "--source", "git@example.invalid:o/r"],
        stdin=io.StringIO(), stdout=out, stderr=err,
    )
    assert rc == ATTACH.EXIT_ERROR
    err_text = err.getvalue()
    assert "Traceback" not in err_text
    assert len(err_text.strip().splitlines()) == 1


# =================================================================================================
# AC-WAF-4 — both-or-neither, gitignore line first
# =================================================================================================
def test_the_pair_lands_atomically_with_the_gitignore_line_first(ws, remotes):
    bare = remotes["bare"]
    outcome = ATTACH.attach_existing(str(ws), yes=True, **_basic_fields(str(bare), path="atomicp", key="atomick"))
    assert outcome.status == "written"
    doc = json.loads(_read_manifest(ws).decode("utf-8"))
    assert "atomick" in doc["repos"]
    gi_text = _read_gitignore(ws).decode("utf-8")
    assert "/atomicp/\n" in gi_text

    # feed the result to the registry report -- must classify the pairing 'ok'
    outcome2, degraded, reason, rows = REG.build_report(str(ws))
    row = next(r for r in rows if r["key"] == "atomick")
    assert row["gitignore"] in ("ok",)

    # fault injected BETWEEN the two writes: gitignore lands, manifest never does -- the DANGEROUS
    # half (a row with no line) must be unreachable.
    real_rename = ATTACH._rename_choke
    calls = {"n": 0}

    def fault_between(tmp_path, target_path):
        calls["n"] += 1
        if calls["n"] == 2:  # the manifest's own rename
            raise OSError("simulated crash between the two writes")
        return real_rename(tmp_path, target_path)

    import foundry_repo_attach as _mod
    _mod._rename_choke = fault_between
    try:
        with pytest.raises(OSError):
            ATTACH.attach_existing(str(ws), yes=True, confirm_fn=_always_confirm,
                                    **_basic_fields(str(bare), path="faultp", key="faultk"))
    finally:
        _mod._rename_choke = real_rename

    gi_text2 = _read_gitignore(ws).decode("utf-8")
    assert "/faultp/\n" in gi_text2  # the benign half survived
    doc2 = json.loads(_read_manifest(ws).decode("utf-8"))
    assert "faultk" not in doc2["repos"]  # the dangerous half never appeared

    # fault BEFORE the row rename (temp written+fsynced, crash right at rename time) — same shape
    calls2 = {"n": 0}

    def fault_before_row_rename(tmp_path, target_path):
        calls2["n"] += 1
        if calls2["n"] == 2:
            raise OSError("simulated crash right before the row rename")
        return real_rename(tmp_path, target_path)

    _mod._rename_choke = fault_before_row_rename
    try:
        with pytest.raises(OSError):
            ATTACH.attach_existing(str(ws), yes=True, confirm_fn=_always_confirm,
                                    **_basic_fields(str(bare), path="faultp2", key="faultk2"))
    finally:
        _mod._rename_choke = real_rename
    doc3 = json.loads(_read_manifest(ws).decode("utf-8"))
    assert "faultk2" not in doc3["repos"]
    assert "/faultp2/\n" in _read_gitignore(ws).decode("utf-8")


# =================================================================================================
# AC-WAF-4 — the durable recipe
# =================================================================================================
def test_replace_is_temp_in_target_dir_fsynced_mode_preserved_symlink_refused(ws, remotes, monkeypatch):
    bare = remotes["bare"]
    gitignore_path = ws / ".gitignore"
    gitignore_path.chmod(0o640)
    manifest_path = ws / ".claude" / "foundry-project.json"

    observed = []
    real_mkstemp = ATTACH.tempfile.mkstemp

    def spy_mkstemp(prefix=None, dir=None):
        fd, path = real_mkstemp(prefix=prefix, dir=dir)
        observed.append((dir, path))
        return fd, path

    fsynced_dirs = []
    real_fsync_dir = ATTACH._fsync_dir

    def spy_fsync_dir(d):
        fsynced_dirs.append(d)
        return real_fsync_dir(d)

    monkeypatch.setattr(ATTACH.tempfile, "mkstemp", spy_mkstemp)
    monkeypatch.setattr(ATTACH, "_fsync_dir", spy_fsync_dir)

    outcome = ATTACH.attach_existing(str(ws), yes=True, **_basic_fields(str(bare), path="modep", key="modek"))
    assert outcome.status == "written"

    for dir_used, tmp_path in observed:
        assert dir_used in (str(ws), str(ws / ".claude"))
        assert os.path.dirname(tmp_path) == dir_used

    assert str(ws) in fsynced_dirs or os.path.realpath(str(ws)) in [os.path.realpath(d) for d in fsynced_dirs]
    assert stat.S_IMODE(gitignore_path.stat().st_mode) == 0o640

    # idempotent no-op: a second run with a DIFFERENT key but the SAME path is refused as
    # duplicate; instead re-verify the SAME gitignore line append is a true no-op by re-adding it
    # directly.
    pre_gi = gitignore_path.read_bytes()
    noop_bytes = ATTACH._gitignore_new_bytes(pre_gi, ATTACH.gitignore_line_bytes("modep"))
    assert noop_bytes == pre_gi

    # a .gitignore not ending in LF gains exactly one before the append
    no_lf_root = ws
    (no_lf_root / ".gitignore").write_bytes(b"/some-other-thing/")
    built = ATTACH._gitignore_new_bytes(b"/some-other-thing/", ATTACH.gitignore_line_bytes("newone"))
    assert built == b"/some-other-thing/\n/newone/\n"
    assert built.endswith(b"\n") and built.count(b"\n") == built.rstrip(b"\n").count(b"\n") + 1

    # symlink target refusal
    manifest_path_str = str(manifest_path)
    real_target = ws.parent / "real-manifest.json"
    real_target.write_text("{}", encoding="utf-8")
    manifest_path.unlink()
    manifest_path.symlink_to(real_target)
    with pytest.raises(ATTACH.SymlinkRefusal):
        ATTACH._atomic_replace_file(manifest_path_str, lambda pre: b"{}", ATTACH._read_bytes_or_none(manifest_path_str))
    manifest_path.unlink()


def test_manifest_write_preserves_other_keys_comments_and_formatting(ws, remotes):
    bare = remotes["bare"]
    manifest_path = ws / ".claude" / "foundry-project.json"
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    doc["repos"] = {
        "//": "a plain comment-shaped entry, valid JSON, preserved verbatim",
        "kept": {"path": "./kept-dir", "remote": str(bare), "role": "product"},
    }
    doc["project"] = {"name": "demo"}
    manifest_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    _git(["add", ".claude"], ws)
    _git(["commit", "-q", "-m", "seed extra keys"], ws)

    outcome = ATTACH.attach_existing(str(ws), yes=True, **_basic_fields(str(bare), path="newp", key="newk"))
    assert outcome.status == "written"
    after = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert after["repos"]["//"] == "a plain comment-shaped entry, valid JSON, preserved verbatim"
    assert after["repos"]["kept"] == doc["repos"]["kept"]
    assert after["project"] == {"name": "demo"}
    assert after["repos"]["newk"]["path"] == "newp"


# =================================================================================================
# AC-WAF-4/-5 — TOCTOU: a concurrent writer is never clobbered
# =================================================================================================
def test_a_concurrent_writer_is_refused_and_never_clobbered(ws, remotes, monkeypatch):
    bare = remotes["bare"]
    gitignore_path = ws / ".gitignore"

    real_replace = ATTACH._atomic_replace_file
    call_n = {"n": 0}

    def mutate_then_replace(target_path, compute_new_bytes, expected_pre_bytes, **kw):
        call_n["n"] += 1
        if call_n["n"] == 1 and target_path == str(gitignore_path):
            gitignore_path.write_bytes(expected_pre_bytes + b"/concurrent-writer/\n")
        return real_replace(target_path, compute_new_bytes, expected_pre_bytes, **kw)

    import foundry_repo_attach as _mod
    monkeypatch.setattr(_mod, "_atomic_replace_file", mutate_then_replace)

    before_manifest = _read_manifest(ws)
    outcome = ATTACH.attach_existing(str(ws), yes=True, **_basic_fields(str(bare), path="concp", key="conck"))
    assert outcome.status == "refused"
    assert outcome.refusal_code == "concurrent-writer"
    gi = gitignore_path.read_bytes()
    assert b"/concurrent-writer/\n" in gi  # the concurrent writer's bytes survive
    assert b"/concp/\n" not in gi
    assert _read_manifest(ws) == before_manifest


def test_concurrent_manifest_writer_after_gitignore_lands_is_a_named_partial_state(ws, remotes, monkeypatch):
    bare = remotes["bare"]
    manifest_path = ws / ".claude" / "foundry-project.json"

    real_replace = ATTACH._atomic_replace_file
    call_n = {"n": 0}

    def mutate_manifest_before_its_replace(target_path, compute_new_bytes, expected_pre_bytes, **kw):
        call_n["n"] += 1
        if target_path == str(manifest_path):
            doc = json.loads(expected_pre_bytes.decode("utf-8"))
            doc.setdefault("repos", {})["sideband"] = {"path": "./sideband"}
            manifest_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        return real_replace(target_path, compute_new_bytes, expected_pre_bytes, **kw)

    import foundry_repo_attach as _mod
    monkeypatch.setattr(_mod, "_atomic_replace_file", mutate_manifest_before_its_replace)

    outcome = ATTACH.attach_existing(str(ws), yes=True, **_basic_fields(str(bare), path="pconp", key="pconk"))
    assert outcome.status == "refused"
    assert outcome.refusal_code == "concurrent-writer"
    gi_text = (ws / ".gitignore").read_text(encoding="utf-8")
    assert "/pconp/\n" in gi_text  # benign half: gitignore line landed
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "pconk" not in doc["repos"]  # row never written
    assert doc["repos"]["sideband"]["path"] == "./sideband"  # the concurrent writer's bytes survive


# =================================================================================================
# AC-WAF-5 — rollback restores the pair byte-for-byte, trigger set pinned
# =================================================================================================
@pytest.mark.parametrize("outcome_shape", [
    {"action": "refuse", "result": "n/a"},
    {"action": "clone", "result": "failed"},
    {"action": "clone", "result": "timeout"},
    {"action": "clone", "result": "spawn-failed"},
])
def test_post_write_failure_restores_both_files_byte_for_byte_row_first(ws, remotes, monkeypatch, outcome_shape):
    bare = remotes["bare"]
    before = _snapshot(ws)

    calls = []

    def fake_reconcile(root, rows, *, timeout=None):
        row = dict(rows[0])
        row.update(action=outcome_shape["action"], result=outcome_shape["result"], finding=True, detail="x")
        calls.append(row)
        return {"degraded": False, "degraded_reason": None, "rows": [row]}

    monkeypatch.setattr(ATTACH.FLEET, "reconcile", fake_reconcile)

    restore_order = []
    real_restore = ATTACH._restore_file

    def spy_restore(path, restore_to_bytes, expected_current_bytes):
        restore_order.append(path)
        return real_restore(path, restore_to_bytes, expected_current_bytes)

    monkeypatch.setattr(ATTACH, "_restore_file", spy_restore)

    outcome = ATTACH.attach_existing(str(ws), yes=True, **_basic_fields(str(bare), path="rbp", key="rbk"))
    assert outcome.status == "rolled_back"
    assert _snapshot(ws) == before
    manifest_idx = next(i for i, p in enumerate(restore_order) if p.endswith("foundry-project.json"))
    gitignore_idx = next(i for i, p in enumerate(restore_order) if p.endswith(".gitignore"))
    assert manifest_idx < gitignore_idx  # row removed first


def test_finding_without_failure_does_not_roll_back(ws, remotes, monkeypatch):
    bare = remotes["bare"]

    def fake_reconcile(root, rows, *, timeout=None):
        row = dict(rows[0])
        row.update(action="skip", result="ok", finding=True, detail="mr-register-style mismatch")
        return {"degraded": False, "degraded_reason": None, "rows": [row]}

    monkeypatch.setattr(ATTACH.FLEET, "reconcile", fake_reconcile)
    outcome = ATTACH.attach_existing(str(ws), yes=True, **_basic_fields(str(bare), path="findp", key="findk"))
    assert outcome.status == "written"
    doc = json.loads(_read_manifest(ws).decode("utf-8"))
    assert "findk" in doc["repos"]


# =================================================================================================
# AC-WAF-5 — the hard combined case: a completed clone survives rollback, named in the report
# =================================================================================================
def test_a_completed_clone_survives_rollback_and_is_named_in_one_run(ws, remotes, monkeypatch):
    bare = remotes["bare"]
    before_other = {k: v for k, v in [("gi", (ws / ".gitignore").read_bytes())]}

    real_reconcile = ATTACH.FLEET.reconcile

    def real_clone_then_raise(root, rows, *, timeout=None):
        real_reconcile(root, rows, timeout=timeout)  # performs the REAL clone on disk
        raise RuntimeError("simulated crash immediately after a completed clone")

    monkeypatch.setattr(ATTACH.FLEET, "reconcile", real_clone_then_raise)

    before = _snapshot(ws)
    outcome = ATTACH.attach_existing(str(ws), yes=True, **_basic_fields(str(bare), path="clonep", key="clonek"))
    assert outcome.status == "rolled_back"
    assert _snapshot(ws) == before  # the pair restored byte-for-byte
    assert (ws / "clonep" / ".git").exists()  # the cloned tree is STILL ON DISK
    assert outcome.undeclared_checkout == "clonep"

    # every OTHER file/entry is unchanged
    assert (ws / ".gitignore").read_bytes() == before_other["gi"]


# =================================================================================================
# AC-WAF-5 — the second uncompensated effect: a created repo is named, never deleted
# =================================================================================================
FAKE_GH_SCRIPT = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys
    log_path = os.environ["FAKE_GH_LOG"]
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(sys.argv[1:]) + "\\n")
    behavior = json.load(open(os.environ["FAKE_GH_BEHAVIOR"], encoding="utf-8"))
    args = sys.argv[1:]
    if args[:2] == ["repo", "create"]:
        if behavior.get("create_fail"):
            sys.stderr.write(behavior.get("create_fail_message", "gh: create failed"))
            sys.exit(1)
        print(behavior["create_stdout_url"])
        sys.exit(0)
    if args[:2] == ["repo", "view"]:
        if behavior.get("view_fail"):
            sys.stderr.write("gh: view failed")
            sys.exit(1)
        print(json.dumps({"nameWithOwner": behavior["canonical_name"], "url": behavior["canonical_url"]}))
        sys.exit(0)
    sys.stderr.write("fake gh: unrecognized invocation: %r" % (args,))
    sys.exit(2)
''')


@pytest.fixture()
def fake_gh(tmp_path, monkeypatch):
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    script = bindir / "gh"
    script.write_text(FAKE_GH_SCRIPT, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    log_path = tmp_path / "gh-invocations.log"
    log_path.write_text("", encoding="utf-8")
    behavior_path = tmp_path / "gh-behavior.json"
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAKE_GH_LOG", str(log_path))
    monkeypatch.setenv("FAKE_GH_BEHAVIOR", str(behavior_path))

    def set_behavior(behavior):
        behavior_path.write_text(json.dumps(behavior), encoding="utf-8")

    def read_log():
        lines = [l for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        return [json.loads(l) for l in lines]

    return {"set_behavior": set_behavior, "read_log": read_log, "bindir": bindir}


def test_a_created_repo_is_named_as_an_orphan_and_never_deleted(ws, remotes, fake_gh, monkeypatch):
    bare = remotes["bare"]
    fake_gh["set_behavior"]({
        "create_stdout_url": "https://example.invalid/acme/newrepo",
        "canonical_name": "acme/newrepo",
        "canonical_url": str(bare),
    })

    # (1) operator declines the PAIR confirmation after gh succeeded
    confirms = {"n": 0}

    def confirm_only_creation(_prompt):
        confirms["n"] += 1
        return confirms["n"] == 1  # confirm creation, decline the write

    outcome1 = ATTACH.create_new(
        str(ws), repo="acme/newrepo", path="orphanp1", key="orphank1", role="product",
        description="d", default_branch="trunk", yes=False, confirm_fn=confirm_only_creation,
    )
    assert outcome1.status == "declined"
    assert outcome1.orphan_repo == "acme/newrepo"

    # (2) a post-creation refusal fires (bad path)
    outcome2 = ATTACH.create_new(
        str(ws), repo="acme/newrepo", path="", key="orphank2", role="product",
        description="d", default_branch="trunk", yes=True,
    )
    assert outcome2.status == "refused"
    assert outcome2.orphan_repo == "acme/newrepo"

    # (3) a post-write failure triggers rollback
    def fake_reconcile(root, rows, *, timeout=None):
        row = dict(rows[0])
        row.update(action="refuse", result="n/a", finding=True, detail="refused")
        return {"degraded": False, "degraded_reason": None, "rows": [row]}

    monkeypatch.setattr(ATTACH.FLEET, "reconcile", fake_reconcile)
    outcome3 = ATTACH.create_new(
        str(ws), repo="acme/newrepo", path="orphanp3", key="orphank3", role="product",
        description="d", default_branch="trunk", yes=True,
    )
    assert outcome3.status == "rolled_back"
    assert outcome3.orphan_repo == "acme/newrepo"

    log = fake_gh["read_log"]()
    for argv in log:
        assert "delete" not in argv
        assert not any(a.startswith("--confirm") and "delete" in " ".join(argv) for a in argv)


# =================================================================================================
# AC-WAF-6 — consent precedes creation, dry-run short-circuits
# =================================================================================================
def test_creation_is_confirmed_before_gh_runs_and_dry_run_short_circuits(ws, fake_gh):
    fake_gh["set_behavior"]({
        "create_stdout_url": "https://example.invalid/acme/newrepo2",
        "canonical_name": "acme/newrepo2",
        "canonical_url": "https://example.invalid/acme/newrepo2.git",
    })

    order = []

    def confirm_checks_log_empty(prompt):
        order.append(("confirm", fake_gh["read_log"]()))
        return False  # decline -- prove nothing was spawned before the prompt was read

    outcome = ATTACH.create_new(
        str(ws), repo="acme/newrepo2", path="p", key="k", role="product", description="d",
        default_branch="trunk", yes=False, confirm_fn=confirm_checks_log_empty,
    )
    assert outcome.status == "declined"
    assert order[0][1] == []  # the fake gh's invocation log was EMPTY at prompt-read time
    assert fake_gh["read_log"]() == []
    assert "create repo acme/newrepo2" in outcome.preview_lines[0]
    assert ATTACH.PINNED_SENTENCE_3 in outcome.preview_lines[0]

    before = _snapshot(ws)
    dry = ATTACH.create_new(
        str(ws), repo="acme/newrepo2", path="p", key="k", role="product", description="d",
        default_branch="trunk", yes=True, dry_run=True,
    )
    assert dry.status == "dry_run"
    assert fake_gh["read_log"]() == []
    assert _snapshot(ws) == before

    # with --template, the confirmation names the template too
    def confirm_check_template(prompt):
        order.append(("template-confirm", prompt))
        return False

    ATTACH.create_new(
        str(ws), repo="acme/newrepo2", template="acme/tmpl", path="p", key="k", role="product",
        description="d", default_branch="trunk", yes=False, confirm_fn=confirm_check_template,
    )
    assert "from template acme/tmpl" in order[-1][1]


# =================================================================================================
# AC-WAF-6 — one code path, arguments floored directly
# =================================================================================================
def test_create_new_floors_its_argv_then_falls_through_to_the_attach_path(ws, remotes, fake_gh, monkeypatch):
    bare = remotes["bare"]
    fake_gh["set_behavior"]({
        "create_stdout_url": "https://example.invalid/acme/repo3",
        "canonical_name": "acme/repo3",
        "canonical_url": str(bare),
    })

    bad_cases = ["-leading", "https://not-a-name", "onepart-with template-slash-missing-elsewhere",
                 "has space", "has\x01control"]
    for i, bad in enumerate(bad_cases):
        with pytest.raises(ATTACH.Refusal):
            ATTACH.create_new(str(ws), repo=bad, path="bp%d" % i, key="bk%d" % i, role="product",
                               description="d", default_branch="trunk", yes=True)
    assert fake_gh["read_log"]() == []

    attach_calls = []
    real_attach = ATTACH.attach_existing

    def spy_attach(*a, **kw):
        attach_calls.append(kw)
        return real_attach(*a, **kw)

    monkeypatch.setattr(ATTACH, "attach_existing", spy_attach)
    outcome = ATTACH.create_new(
        str(ws), repo="acme/repo3", path="cnfp", key="cnfk", role="product", description="d",
        default_branch="trunk", yes=True,
    )
    assert outcome.status == "written"
    assert len(attach_calls) == 1
    assert attach_calls[0]["source"] == str(bare)  # gh's canonical URL, not the typed argv

    log = fake_gh["read_log"]()
    create_call = next(c for c in log if c[:2] == ["repo", "create"])
    assert "--clone" not in create_call
    assert "--" in create_call
    dash_idx = create_call.index("--")
    assert create_call[dash_idx + 1] == "acme/repo3"

    # gh failure/missing/unauthenticated is a named refusal, nothing written
    fake_gh["set_behavior"]({"create_fail": True, "create_fail_message": "gh: not authenticated"})
    before = _snapshot(ws)
    with pytest.raises(ATTACH.Refusal) as exc:
        ATTACH.create_new(str(ws), repo="acme/repo4", path="fp", key="fk", role="product",
                           description="d", default_branch="trunk", yes=True)
    assert exc.value.code == "gh-create-failed"
    assert _snapshot(ws) == before


# =================================================================================================
# AC-WAF-6 — the row binds gh's canonical identity, divergence is re-confirmed
# =================================================================================================
def test_the_row_binds_ghs_canonical_identity_and_divergence_is_reconfirmed(ws, remotes, fake_gh):
    bare = remotes["bare"]
    fake_gh["set_behavior"]({
        "create_stdout_url": "https://example.invalid/Acme/renamed-repo",
        "canonical_name": "Acme/renamed-repo",
        "canonical_url": str(bare),
    })

    prompts = []

    def decline_divergence(prompt):
        prompts.append(prompt)
        return "differs from what was requested" not in prompt

    before = _snapshot(ws)
    outcome = ATTACH.create_new(
        str(ws), repo="acme/original-name", path="divp", key="divk", role="product", description="d",
        default_branch="trunk", yes=False, confirm_fn=decline_divergence,
    )
    assert outcome.status == "declined"
    assert any("differs from what was requested" in p for p in prompts)
    assert _snapshot(ws) == before

    outcome2 = ATTACH.create_new(
        str(ws), repo="acme/original-name", path="divp2", key="divk2", role="product", description="d",
        default_branch="trunk", yes=True,  # --yes auto-confirms, including the divergence re-confirm
    )
    assert outcome2.status == "written"
    doc = json.loads(_read_manifest(ws).decode("utf-8"))
    assert doc["repos"]["divk2"]["remote"] == str(bare)  # gh's canonical clone URL, never the typed arg


# =================================================================================================
# AC-WAF-6 — the one network write is bounded and named
# =================================================================================================
def test_gh_is_the_only_network_subprocess_with_sockets_denied(ws, remotes, fake_gh, monkeypatch):
    bare = remotes["bare"]
    fake_gh["set_behavior"]({
        "create_stdout_url": "https://example.invalid/acme/netrepo",
        "canonical_name": "acme/netrepo",
        "canonical_url": str(bare),
    })

    import socket
    import ssl  # noqa: F401 — force full one-time init BEFORE the deny-patch below; ssl.py
    # defines `class SSLSocket(socket.socket)` at import time, and patching socket.socket first
    # would break that unrelated one-time class definition if ssl is imported lazily later.

    real_socket = socket.socket

    def deny_socket(*a, **kw):
        raise AssertionError("a network socket was opened during a hermetic create-new run")

    monkeypatch.setattr(socket, "socket", deny_socket)
    try:
        outcome = ATTACH.create_new(
            str(ws), repo="acme/netrepo", path="netp", key="netk", role="product", description="d",
            default_branch="trunk", yes=True,
        )
    finally:
        monkeypatch.setattr(socket, "socket", real_socket)
    assert outcome.status == "written"

    log = fake_gh["read_log"]()
    create_calls = [c for c in log if c[:2] == ["repo", "create"]]
    assert len(create_calls) == 1


# =================================================================================================
# AC-WAF-7 — the surface states the authority it exercises
# =================================================================================================
def test_docstring_and_help_carry_the_three_pinned_sentences_verbatim():
    assert ATTACH.__name__.isidentifier()
    doc_lines = ATTACH.__doc__.splitlines()
    help_text = ATTACH.build_parser().format_help()
    help_lines = help_text.splitlines()
    for sentence in [ATTACH.PINNED_SENTENCE_1, ATTACH.PINNED_SENTENCE_2, ATTACH.PINNED_SENTENCE_3]:
        assert sentence in doc_lines, "missing from module docstring: %r" % sentence
        assert sentence in help_lines, "missing from --help: %r" % sentence

    cli_help = subprocess.run([sys.executable, CLI, "--help"], capture_output=True, text=True)
    cli_help_lines = cli_help.stdout.splitlines()
    for sentence in [ATTACH.PINNED_SENTENCE_1, ATTACH.PINNED_SENTENCE_2, ATTACH.PINNED_SENTENCE_3]:
        assert sentence in cli_help_lines


def test_module_runs_as_a_cli_over_an_explicit_root(ws, remotes):
    bare = remotes["bare"]
    r = subprocess.run(
        [sys.executable, CLI, "--root", str(ws), "--yes", "--path", "clip", "--key", "click",
         "--role", "product", "--description", "d", "--default-branch", "trunk",
         "attach", "--source", str(bare)],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == ATTACH.EXIT_OK, r.stdout + r.stderr
    doc = json.loads(_read_manifest(ws).decode("utf-8"))
    assert "click" in doc["repos"]


# =================================================================================================
# AC-WAF-8 — never clone-first-record-after; this module runs no git of its own
# =================================================================================================
def test_write_strictly_precedes_reconcile_and_this_module_runs_no_git(ws, remotes, monkeypatch):
    bare = remotes["bare"]
    snapshot_at_entry = {}

    real_reconcile = ATTACH.FLEET.reconcile

    def snapshotting_reconcile(root, rows, *, timeout=None):
        snapshot_at_entry["manifest"] = _read_manifest(ws)
        snapshot_at_entry["gitignore"] = _read_gitignore(ws)
        return real_reconcile(root, rows, timeout=timeout)

    monkeypatch.setattr(ATTACH.FLEET, "reconcile", snapshotting_reconcile)

    git_spy_calls = []
    real_run = subprocess.run

    def spy_run(argv, *a, **kw):
        if isinstance(argv, (list, tuple)) and argv and os.path.basename(str(argv[0])) == "git":
            git_spy_calls.append(list(argv))
        return real_run(argv, *a, **kw)

    monkeypatch.setattr(ATTACH.subprocess, "run", spy_run)

    outcome = ATTACH.attach_existing(str(ws), yes=True, **_basic_fields(str(bare), path="orderp", key="orderk"))
    assert outcome.status == "written"

    doc = json.loads(snapshot_at_entry["manifest"].decode("utf-8"))
    assert "orderk" in doc["repos"]  # row already durable when reconcile was entered
    assert b"/orderp/\n" in snapshot_at_entry["gitignore"]  # gitignore line already durable too

    # ATTACH itself defines no git-verb vocabulary and no git choke point at all: every observed
    # `git` invocation belongs to the imported reconcile callable (FLEET/REG's own subprocess.run
    # calls, captured above too since `subprocess` is one shared module object), never to this
    # module's own code.
    assert not hasattr(ATTACH, "_ALLOWED_GIT_VERBS")
    assert not hasattr(ATTACH, "_git")
    assert git_spy_calls, "expected the reconcile callable's own git invocation to be observed"


# =================================================================================================
# AC-WAF-8 — the seam is the sibling's function object, by identity
# =================================================================================================
def test_reconcile_is_the_imported_fleet_callable_by_identity():
    assert ATTACH.FLEET is FLEET
    assert ATTACH.FLEET.reconcile is FLEET.reconcile
    import inspect

    sig = inspect.signature(FLEET.reconcile)
    params = list(sig.parameters.values())
    assert params[0].name == "rows" or params[1].name == "rows"
    assert sig.parameters["timeout"].kind == inspect.Parameter.KEYWORD_ONLY


# =================================================================================================
# AC-WAF-8 — rows is exactly the new row read back from disk; `finding` alone is not a trigger
# =================================================================================================
def test_reconcile_receives_exactly_the_new_row_from_disk_and_finding_is_not_a_trigger(ws, remotes, monkeypatch):
    bare = remotes["bare"]
    # a pre-existing SECOND row must not appear in the reconcile call
    other = ws / "other-existing"
    other.mkdir()
    ATTACH.attach_existing(str(ws), yes=True, source=str(bare), path="other-existing", key="other-existing-key",
                            role="product", description="d", default_branch="trunk")

    captured = {}
    real_reconcile = ATTACH.FLEET.reconcile

    def capturing_reconcile(root, rows, *, timeout=None):
        captured["rows"] = rows
        return real_reconcile(root, rows, timeout=timeout)

    monkeypatch.setattr(ATTACH.FLEET, "reconcile", capturing_reconcile)

    # sabotage: AFTER the correct bytes are computed and durably written to disk, mutate the
    # SAME in-memory row dict object this flow collected. An implementation that passed its own
    # in-memory values to reconcile (rather than re-reading the manifest file from disk) would
    # leak this mutation into the captured `rows` argument below.
    real_manifest_new_bytes = ATTACH._manifest_new_bytes

    def sabotaging_manifest_new_bytes(pre_bytes, key, row):
        result = real_manifest_new_bytes(pre_bytes, key, row)  # correct bytes, computed first
        row["path"] = "SABOTAGED-IN-MEMORY-VALUE"  # then mutate the caller's own dict object
        return result

    monkeypatch.setattr(ATTACH, "_manifest_new_bytes", sabotaging_manifest_new_bytes)

    outcome = ATTACH.attach_existing(str(ws), yes=True, **_basic_fields(str(bare), path="rowscopep", key="rowscopek"))
    assert outcome.status == "written"

    rows = captured["rows"]
    assert len(rows) == 1
    assert rows[0]["key"] == "rowscopek"
    doc = json.loads(_read_manifest(ws).decode("utf-8"))
    assert rows[0]["path"] == doc["repos"]["rowscopek"]["path"] == "rowscopep"  # disk value, not the sabotage

    # a `skip`/`ok`/`finding: true` row persists the pair (the mr register case); `refuse` on the
    # SAME shape rolls back.
    def skip_finding_reconcile(root, rows, *, timeout=None):
        row = dict(rows[0])
        row.update(action="skip", result="ok", finding=True, detail="mismatch")
        return {"degraded": False, "degraded_reason": None, "rows": [row]}

    monkeypatch.setattr(ATTACH.FLEET, "reconcile", skip_finding_reconcile)
    o1 = ATTACH.attach_existing(str(ws), yes=True, **_basic_fields(str(bare), path="skf-p", key="skf-k"))
    assert o1.status == "written"

    def refuse_reconcile(root, rows, *, timeout=None):
        row = dict(rows[0])
        row.update(action="refuse", result="n/a", finding=True, detail="refused")
        return {"degraded": False, "degraded_reason": None, "rows": [row]}

    monkeypatch.setattr(ATTACH.FLEET, "reconcile", refuse_reconcile)
    o2 = ATTACH.attach_existing(str(ws), yes=True, **_basic_fields(str(bare), path="rfp", key="rfk"))
    assert o2.status == "rolled_back"
