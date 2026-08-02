"""tests/test_repo_registry.py — feat-foundry-repo-registry-formalization (AC-RRF-1..7).

TWO SURFACES, driven for real:
  1. schema/foundry-project.schema.json -- validated with jsonschema's Draft202012Validator over
     both the shipped manifest corpus (must stay green) and hostile/malformed fixtures (must fail).
  2. scripts/foundry_repo_registry.py -- the read-only report, driven both as a directly-imported
     module (`RR`) over materialized tmp_path fixtures (real git checkouts, real .gitignore files)
     and, where the CLI's own exit code / --help / argv-hygiene is the point, as a real subprocess.

Test names are the acceptance contract's `-k` selectors; each named case is its own function --
`pytest -k` only proves "everything matched passed", never "each named case exists".
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import jsonschema
import pytest

from conftest import REPO_ROOT, load_module

RR = load_module("scripts/foundry_repo_registry.py", "foundry_repo_registry")
CLI = os.path.join(REPO_ROOT, "scripts", "foundry_repo_registry.py")
CONFIG_CLI = os.path.join(REPO_ROOT, "scripts", "foundry-config.py")
SCHEMA_PATH = os.path.join(REPO_ROOT, "schema", "foundry-project.schema.json")

with open(SCHEMA_PATH, encoding="utf-8") as _fh:
    SCHEMA = json.load(_fh)

VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)


def _valid(doc):
    return VALIDATOR.is_valid(doc)


def _errors(doc):
    return list(VALIDATOR.iter_errors(doc))


def _proj(repos):
    return {"schema_version": 1, "project": {"name": "demo"}, "repos": repos}


# ---------------------------------------------------------------------------------- git helpers --
def _git(args, cwd, check=True):
    r = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError("git %r failed: %s" % (args, r.stderr))
    return r


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q"], path)
    _git(["config", "user.email", "t@example.com"], path)
    _git(["config", "user.name", "Test"], path)


def _commit(path, relname, content, message="commit"):
    p = path / relname
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    _git(["add", relname], path)
    _git(["commit", "-q", "-m", message], path)


@pytest.fixture()
def workspace(tmp_path):
    """A git-initialized workspace root -- the non-degraded default for every report test."""
    root = tmp_path / "ws"
    _init_repo(root)
    return root


def _write_manifest(root, repos, extra=None):
    doc = {"schema_version": 1}
    if extra:
        doc.update(extra)
    doc["repos"] = repos
    d = root / ".claude"
    d.mkdir(parents=True, exist_ok=True)
    (d / "foundry-project.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return doc


def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, CLI, "--root", str(root)] + list(args),
        capture_output=True, text=True, timeout=60,
    )


def _run_module(root, as_json=False):
    """Direct in-process call -- returns (exit_code, stdout_text, stderr_text)."""
    import io

    out, err = io.StringIO(), io.StringIO()
    rc = RR.run(str(root), as_json, stdout=out, stderr=err)
    return rc, out.getvalue(), err.getvalue()


def _hash_tree(root):
    import hashlib

    out = {}
    for base, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(base, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = hashlib.sha256(fh.read()).hexdigest()
    return out


# ================================================================================== AC-RRF-1 ======
def test_schema_accepts_shipped_manifest_corpus():
    how_to_example = _proj({
        "workspace": {"path": "."},
        "app": {"path": "./my-app", "kind": "code", "role": "product"},
        "infra": {"path": "./my-infra", "kind": "code", "role": "infra"},
    })
    foundry_wt_selftest = {"schema_version": 1, "repos": {"app": {"path": "app"}, "escaper": {"path": "../outside"}}}
    hook_selftest = {"schema_version": 1, "repos": {"app": {"path": "app"}}}
    config_drift_good_proj = _proj({"app": {"path": "app", "datastores": ["postgres"]}})

    for doc in (how_to_example, foundry_wt_selftest, hook_selftest, config_drift_good_proj):
        errs = _errors(doc)
        assert not errs, "%r -> %s" % (doc, [e.message for e in errs])


def test_new_fields_are_optional_and_documented():
    # a record with ONLY path, no new fields at all, still validates
    assert _valid(_proj({"app": {"path": "app"}}))
    # unknown adopter keys still validate at all three additionalProperties levels
    assert _valid({"schema_version": 1, "totally_custom_root_key": {"a": 1}})
    assert _valid(_proj({"app": {"path": "app", "some_unknown_key": 1}}))
    assert _valid(_proj({"app": {"path": "app", "packages": {"pkg": {"path": "p", "unknown": True}}}}))
    # packages.<key>.role stays an UNCONSTRAINED string (prose still allowed there)
    assert _valid(_proj({"app": {"path": "app", "packages": {"pkg": {"role": "this is prose, not a vocabulary"}}}}))

    repos_schema = SCHEMA["properties"]["repos"]["additionalProperties"]["properties"]
    for new_field in ("remote", "default_branch", "role", "description"):
        desc = repos_schema[new_field].get("description")
        assert desc and desc.strip(), "%s has no non-empty description" % new_field
    role_desc = repos_schema["role"]["description"]
    for value in ("product", "handbook", "infra", "app", "workspace"):
        assert value in role_desc, "role description omits %r" % value

    # path required is the ONLY required addition anywhere in the schema
    assert SCHEMA["properties"]["repos"]["additionalProperties"]["required"] == ["path"]
    assert "required" not in SCHEMA["properties"]["repos"]["additionalProperties"]["properties"]["packages"]["additionalProperties"]
    # additionalProperties: true still in force at all three levels
    assert SCHEMA["additionalProperties"] is True
    assert SCHEMA["properties"]["repos"]["additionalProperties"]["additionalProperties"] is True
    pkg_schema = SCHEMA["properties"]["repos"]["additionalProperties"]["properties"]["packages"]["additionalProperties"]
    assert pkg_schema["additionalProperties"] is True
    assert pkg_schema["properties"]["role"] == {"type": "string"}


# ================================================================================== AC-RRF-2 ======
def test_schema_rejects_entry_without_path():
    errs = _errors(_proj({"app": {"kind": "code"}}))
    assert errs
    assert any(list(e.absolute_path) == ["repos", "app"] for e in errs)
    # non-string / empty path also fail, at the same location
    for bad in (123, ""):
        errs2 = _errors(_proj({"app": {"path": bad}}))
        assert errs2
        assert any(list(e.absolute_path)[:2] == ["repos", "app"] for e in errs2)


def test_schema_rejects_non_string_and_empty_values():
    NONSTRING_FIELDS = ("path", "remote", "default_branch", "description")
    EMPTY_REJECTED_FIELDS = ("path", "remote", "default_branch")

    for field in NONSTRING_FIELDS:
        assert not _valid(_proj({"app": {"path": "ok", field: 42}})), "%s=42 should fail" % field
    for field in EMPTY_REJECTED_FIELDS:
        assert not _valid(_proj({"app": {"path": "ok", field: ""}})), "%s='' should fail" % field
    # description='' is NOT in AC-RRF-2's empty-reject list -- must stay valid (not vacuous)
    assert _valid(_proj({"app": {"path": "ok", "description": ""}}))
    # the valid-value direction, so the constraint isn't vacuously satisfied by rejecting everything
    assert _valid(_proj({"app": {"path": "ok", "remote": "https://h/r.git",
                                  "default_branch": "main", "description": "prose"}}))


def test_schema_shape_floor_rejects_dash_leading_and_control_chars():
    for field in ("path", "remote", "default_branch"):
        assert not _valid(_proj({"app": {"path": "ok", field: "-leading-dash"}})), field
    for field in ("remote", "default_branch"):
        assert not _valid(_proj({"app": {"path": "ok", field: "has\x01control"}})), field
    # path is NOT in the C0/C1 clause (AC-RRF-2 names only remote/default_branch there)
    assert _valid(_proj({"app": {"path": "ok\x01x"}}))
    # legitimate shapes still validate -- a shape floor, never a URL validator
    for legit in ("/abs/local/path", "user@host:owner/repo.git", "git://example.com/repo.git"):
        assert _valid(_proj({"app": {"path": "ok", "remote": legit}})), legit


def test_schema_rejects_role_outside_closed_set():
    assert not _valid(_proj({"app": {"path": "ok", "role": "owner"}}))
    for value in ("product", "handbook", "infra", "app", "workspace"):
        assert _valid(_proj({"app": {"path": "ok", "role": value}})), value


def test_config_check_reports_registry_violations_unedited(tmp_path):
    ops = {"schema_version": 1,
           "operators": {"op_lukas": {"name": "Lukas", "github": "lukasrepublic", "added_at": "2026-06-12"}}}
    root = tmp_path / "cfg"
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / "foundry-operators.json").write_text(json.dumps(ops), encoding="utf-8")
    (root / ".claude" / "foundry-project.json").write_text(
        json.dumps({"schema_version": 1, "repos": {"app": {"kind": "code"}}}), encoding="utf-8")

    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    r = subprocess.run([sys.executable, CONFIG_CLI, "--root", str(root), "check", "--json"],
                        capture_output=True, text=True, env=env)
    findings = " ".join(json.loads(r.stdout)["schema"])
    assert "repos" in findings and "app" in findings

    r2 = subprocess.run([sys.executable, CONFIG_CLI, "--root", str(root), "adopt", "--yes"],
                         capture_output=True, text=True, env=env)
    assert r2.returncode == 1 and "refusing" in r2.stderr
    assert not os.path.exists(root / ".claude" / "foundry-config-baseline.json")


# ================================================================================== AC-RRF-3 ======
def test_report_distinguishes_not_cloned_from_dangling(workspace):
    _write_manifest(workspace, {
        "withremote": {"path": "missing-a", "remote": "https://example.com/o/r.git"},
        "noremote": {"path": "missing-b"},
    })
    outcome, degraded, reason, rows = RR.build_report(str(workspace))
    by_key = {r["key"]: r for r in rows}
    assert by_key["withremote"]["path_status"] == "not-cloned"
    assert by_key["noremote"]["path_status"] == "dangling"
    assert by_key["withremote"].get("remedy_remote") == "https://example.com/o/r.git"
    assert by_key["withremote"].get("remedy_target_path")
    assert "remote" in (by_key["noremote"].get("remedy_text") or "").lower()
    assert by_key["withremote"].get("remedy_text") != by_key["noremote"].get("remedy_text")


def test_report_classifies_present_and_outside_workspace(tmp_path):
    root = tmp_path / "ws"
    _init_repo(root)
    (root / "inside").mkdir()
    outside = tmp_path / "outside_real"
    outside.mkdir()
    _write_manifest(root, {
        "in": {"path": "inside"},
        "out": {"path": "../outside_real"},
    })
    outcome, degraded, reason, rows = RR.build_report(str(root))
    by_key = {r["key"]: r for r in rows}
    assert by_key["in"]["path_status"] == "present"
    assert by_key["out"]["path_status"] == "outside-workspace"


def test_report_symlink_escape_is_outside_workspace(tmp_path):
    root = tmp_path / "ws"
    _init_repo(root)
    outside_target = tmp_path / "outside_target"
    outside_target.mkdir()
    link = root / "linked"
    link.symlink_to(outside_target, target_is_directory=True)
    _write_manifest(root, {"linked": {"path": "linked"}})
    outcome, degraded, reason, rows = RR.build_report(str(root))
    assert rows[0]["path_status"] == "outside-workspace"


def test_report_remedy_emits_fields_not_command_line(workspace):
    _write_manifest(workspace, {"app": {"path": "missing", "remote": "https://example.com/o/r.git"}})
    rc, stdout, stderr = _run_module(workspace, as_json=False)
    assert "git clone" not in stdout
    assert "remote=" in stdout and "target_path=" in stdout
    assert "https://example.com/o/r.git" in stdout


# ================================================================================== AC-RRF-4 ======
def test_report_names_unpaired_gitignore_entry(workspace):
    (workspace / "app").mkdir()
    _write_manifest(workspace, {"app": {"path": "app"}, "workspace": {"path": "."}})
    outcome, degraded, reason, rows = RR.build_report(str(workspace))
    by_key = {r["key"]: r for r in rows}
    assert by_key["app"]["gitignore"] == "unpaired"
    assert by_key["app"]["remedy_gitignore_line"] == "/app/"
    assert by_key["workspace"]["gitignore"] == "n/a"


def test_report_names_unanchored_gitignore_pattern(workspace):
    (workspace / "app").mkdir()
    (workspace / ".gitignore").write_text("app/\n", encoding="utf-8")
    _write_manifest(workspace, {"app": {"path": "app"}})
    outcome, degraded, reason, rows = RR.build_report(str(workspace))
    assert rows[0]["gitignore"] == "unanchored"
    assert "app/" in rows[0]["gitignore_source"]


def test_report_never_edits_gitignore(workspace):
    (workspace / "app").mkdir()
    (workspace / ".gitignore").write_text("app/\n", encoding="utf-8")
    _write_manifest(workspace, {"app": {"path": "app"}})
    before = (workspace / ".gitignore").read_bytes()
    RR.build_report(str(workspace))
    _run_cli(workspace, "--json")
    after = (workspace / ".gitignore").read_bytes()
    assert before == after


def test_report_tracked_child_outranks_ignore_rule(workspace):
    (workspace / "app").mkdir()
    (workspace / "app" / "f.txt").write_text("hi\n", encoding="utf-8")
    (workspace / ".gitignore").write_text("/app/\n", encoding="utf-8")
    _git(["add", "-f", "app"], workspace)
    _write_manifest(workspace, {"app": {"path": "app"}})
    outcome, degraded, reason, rows = RR.build_report(str(workspace))
    assert rows[0]["gitignore"] == "tracked"
    assert rows[0]["tracked_count"] >= 1


# ================================================================================== AC-RRF-5 ======
def test_report_names_origin_mismatch_and_normalizes_url_forms(workspace):
    child = workspace / "app"
    _init_repo(child)
    _git(["remote", "add", "origin", "https://github.com/example/real-repo.git"], child)
    _write_manifest(workspace, {
        "mismatch": {"path": "app", "remote": "https://github.com/example/OTHER-repo.git"},
    })
    outcome, degraded, reason, rows = RR.build_report(str(workspace))
    row = rows[0]
    assert row["origin"] == "mismatch"
    assert row["discovered_origin"] == "https://github.com/example/real-repo.git"

    for form in (
        "https://github.com/example/real-repo.git",
        "ssh://git@github.com/example/real-repo.git",
        "git@github.com:example/real-repo.git",
        "https://GITHUB.com/example/real-repo",
    ):
        _write_manifest(workspace, {"m": {"path": "app", "remote": form}})
        _outcome, _d, _r, rows2 = RR.build_report(str(workspace))
        assert rows2[0]["origin"] == "match", form


def test_report_origin_oracle_ignores_insteadof_redirect(workspace):
    child = workspace / "app"
    _init_repo(child)
    real_url = "https://github.com/example/real-repo.git"
    _git(["remote", "add", "origin", real_url], child)
    _git(["config", "url.http://attacker.example/.insteadOf", "https://github.com/"], child)

    redirected = _git(["remote", "get-url", "origin"], child).stdout.strip()
    assert redirected.startswith("http://attacker.example/"), redirected

    _write_manifest(workspace, {"app": {"path": "app", "remote": real_url}})
    rc, stdout, stderr = _run_module(workspace, as_json=True)
    payload = json.loads(stdout)
    row = payload["rows"][0]
    assert row["origin"] == "match"
    assert "attacker.example" not in stdout
    assert "attacker.example" not in stderr


# ================================================================================== AC-RRF-7 ======
REMOTE_FORMS_WITH_USERINFO = [
    # Security review 2026-08-02: any-scheme + last-@ + bracketed-IPv6 coverage (Blocks 1-2, Risk 5)
    ("git://secretuser:secretpass@example.com/o/r.git", "git://***@example.com/o/r.git"),
    ("http://secrettoken@example.com/o/r.git", "http://***@example.com/o/r.git"),
    ("https://a@user:secretpass@example.com/o/r.git", "https://***@example.com/o/r.git"),
    ("secretuser:secretpass@[::1]:o/r.git", "***@[::1]:o/r.git"),
    ("https://secretuser:secretpass@github.com/o/r.git", "https://github.com/o/r"),
    ("ssh://secretuser:secretpass@github.com/o/r.git", "ssh://github.com/o/r"),
    ("secretuser:secretpass@github.com:o/r.git", "github.com:o/r"),
]


def _assert_no_raw_userinfo(text):
    assert "secretuser" not in text
    assert "secretpass" not in text


def test_report_redacts_every_remote_form_in_every_channel(workspace):
    (workspace / ".gitignore").write_text("/child/\n", encoding="utf-8")
    for hostile, _clean_hint in REMOTE_FORMS_WITH_USERINFO:
        # ---- clean (match) run: declared remote == checkout origin, both carry userinfo
        child = workspace / "child"
        if child.exists():
            shutil.rmtree(child)
        _init_repo(child)
        _git(["remote", "add", "origin", hostile], child)
        _write_manifest(workspace, {"c": {"path": "child", "remote": hostile}})
        for as_json in (False, True):
            rc, stdout, stderr = _run_module(workspace, as_json=as_json)
            assert rc == RR.EXIT_CLEAN
            _assert_no_raw_userinfo(stdout)
            _assert_no_raw_userinfo(stderr)
            assert "***" in stdout

        # ---- findings (mismatch) run: declared remote differs, both still carry userinfo
        _write_manifest(workspace, {"c": {"path": "child", "remote": hostile.replace("o/r.git", "o/OTHER.git")}})
        for as_json in (False, True):
            rc, stdout, stderr = _run_module(workspace, as_json=as_json)
            assert rc == RR.EXIT_FINDINGS
            _assert_no_raw_userinfo(stdout)
            _assert_no_raw_userinfo(stderr)

        # ---- not-cloned row: prints the declared remote with no checkout at all
        _write_manifest(workspace, {"nc": {"path": "does-not-exist", "remote": hostile}})
        for as_json in (False, True):
            rc, stdout, stderr = _run_module(workspace, as_json=as_json)
            _assert_no_raw_userinfo(stdout)
            _assert_no_raw_userinfo(stderr)
            assert "***" in stdout

    shutil.rmtree(workspace / "child", ignore_errors=True)


def test_report_sanitizes_hostile_fields_one_row_per_entry(workspace):
    hostile_key = "app\r\nFORGED: evil\x1b[31m"
    hostile_remote = "https://evil\x1b[31mred\x1b[0m.example/o/r\r\nINJECTED-LINE.git"
    manifest = {
        hostile_key: {
            "path": "missing-hostile",
            "remote": hostile_remote,
            "default_branch": "main\r\nEVIL\x01",
        }
    }
    _write_manifest(workspace, manifest)

    outcome, degraded, reason, rows = RR.build_report(str(workspace))
    assert len(rows) == 1
    row = rows[0]
    assert row["path_status"] == "not-cloned"

    rendered = RR._format_row_human(row)
    assert "\x1b" not in rendered
    assert "\r" not in rendered
    assert rendered.count("FORGED") == 1  # the hostile key text appears once, escaped, never re-parsed

    payload_text = json.dumps(RR._json_envelope(outcome, degraded, reason, [row]), sort_keys=True)
    parsed = json.loads(payload_text)
    assert len(parsed["rows"]) == 1
    assert "\x1b" not in payload_text
    flat = json.dumps(parsed)
    assert "\r\n" not in flat


# ================================================================================== AC-RRF-6 ======
def test_report_writes_nothing(workspace):
    (workspace / "app").mkdir()
    _write_manifest(workspace, {"app": {"path": "app"}})
    before = _hash_tree(workspace)
    RR.build_report(str(workspace))
    _run_cli(workspace, "--json")
    after = _hash_tree(workspace)
    assert before == after

    _write_manifest(workspace, {"missing": {"path": "gone"}})
    before2 = _hash_tree(workspace)
    _run_cli(workspace, "--json")
    after2 = _hash_tree(workspace)
    assert before2 == after2


def test_report_exit_codes_are_advisory_tristate(workspace):
    (workspace / "app").mkdir()
    (workspace / ".gitignore").write_text("/app/\n", encoding="utf-8")
    _write_manifest(workspace, {"app": {"path": "app"}})
    assert _run_cli(workspace, "--json").returncode == RR.EXIT_CLEAN

    _write_manifest(workspace, {"gone": {"path": "does-not-exist"}})
    assert _run_cli(workspace, "--json").returncode == RR.EXIT_FINDINGS

    missing_root = workspace / "nope"
    missing_root.mkdir()
    assert _run_cli(missing_root, "--json").returncode == RR.EXIT_ERROR

    docstring = RR.__doc__ or ""
    assert "no gate" in docstring.lower() or "advisory" in docstring.lower()
    help_out = subprocess.run([sys.executable, CLI, "--help"], capture_output=True, text=True).stdout
    assert "gate" in help_out.lower()


def test_report_json_rows_carry_typed_fields(workspace):
    (workspace / "app").mkdir()
    _write_manifest(workspace, {"app": {"path": "app"}, "gone": {"path": "nope"}})
    _outcome, _d, _r, rows = RR.build_report(str(workspace))
    for row in rows:
        for field in ("key", "path", "path_status", "gitignore", "origin"):
            assert field in row
        assert row["path_status"] in ("present", "not-cloned", "dangling", "outside-workspace")
        assert row["gitignore"] in ("ok", "unanchored", "unpaired", "tracked", "n/a", "unknown")
        assert row["origin"] in ("match", "mismatch", "undeclared", "not-a-checkout", "unknown")


def test_report_json_is_an_envelope_not_bare_rows(workspace, tmp_path):
    (workspace / "app").mkdir()
    _write_manifest(workspace, {"app": {"path": "app"}})
    r_clean = _run_cli(workspace, "--json")
    clean_payload = json.loads(r_clean.stdout)
    assert isinstance(clean_payload, dict)
    assert set(clean_payload.keys()) == {"degraded", "degraded_reason", "rows"}
    assert clean_payload["degraded"] is False and clean_payload["degraded_reason"] is None

    _write_manifest(workspace, {"gone": {"path": "does-not-exist"}})
    r_find = _run_cli(workspace, "--json")
    find_payload = json.loads(r_find.stdout)
    assert set(find_payload.keys()) == {"degraded", "degraded_reason", "rows"}

    # sibling of `workspace`, NOT nested inside it -- git's ancestor-.git discovery would
    # otherwise make a nested dir "inside a work tree" via the outer repo.
    no_git_root = tmp_path / "nogit"
    no_git_root.mkdir()
    (no_git_root / ".claude").mkdir()
    (no_git_root / ".claude" / "foundry-project.json").write_text(
        json.dumps({"schema_version": 1, "repos": {"x": {"path": "."}}}), encoding="utf-8")
    r_deg = _run_cli(no_git_root, "--json")
    deg_payload = json.loads(r_deg.stdout)
    assert set(deg_payload.keys()) == {"degraded", "degraded_reason", "rows"}
    assert deg_payload["degraded"] is True and deg_payload["degraded_reason"]


def test_report_no_repos_is_a_named_outcome_never_silent_zero(workspace):
    (workspace / ".claude").mkdir(parents=True, exist_ok=True)
    for bad_repos in (None, {}, "not-an-object", []):
        doc = {"schema_version": 1}
        if bad_repos is not None:
            doc["repos"] = bad_repos
        (workspace / ".claude" / "foundry-project.json").write_text(json.dumps(doc), encoding="utf-8")

        r_text = _run_cli(workspace)
        r_json = _run_cli(workspace, "--json")
        assert r_text.returncode == 2, bad_repos
        assert r_json.returncode == 2, bad_repos
        assert "no-repos" in r_text.stdout
        assert "no-repos" in r_json.stdout
        payload = json.loads(r_json.stdout)
        assert payload["rows"] == []


def test_report_git_argv_is_read_only_and_dash_dash_guarded(workspace, monkeypatch):
    (workspace / "app").mkdir()
    (workspace / ".gitignore").write_text("/app/\n", encoding="utf-8")
    _write_manifest(workspace, {"app": {"path": "app"}, "weird": {"path": "--upload-pack=evil"}})

    calls = []
    real_run = RR.subprocess.run

    def _recording_run(argv, **kwargs):
        calls.append((list(argv), dict(kwargs)))
        return real_run(argv, **kwargs)

    monkeypatch.setattr(RR.subprocess, "run", _recording_run)
    RR.build_report(str(workspace))

    assert calls, "no git calls recorded"
    for argv, kwargs in calls:
        assert argv[0] == "git"
        assert argv[1] in RR.GIT_READONLY_VERBS
        assert kwargs.get("shell") is False
        if argv[1] in ("check-ignore", "ls-files"):
            assert "--" in argv
            dd_index = argv.index("--")
            assert dd_index == len(argv) - 2, argv
            manifest_value = argv[-1]
            assert manifest_value.startswith("app/") or manifest_value.startswith("--upload-pack=evil")


def test_report_degrades_to_unknown_without_git(workspace, monkeypatch):
    (workspace / "app").mkdir()
    _write_manifest(workspace, {"app": {"path": "app", "remote": "https://example.com/o/r.git"}})

    monkeypatch.setattr(RR.shutil, "which", lambda name: None)
    outcome, degraded, reason, rows = RR.build_report(str(workspace))
    assert degraded is True and reason
    row = rows[0]
    assert row["gitignore"] == "unknown"
    assert row["origin"] == "unknown"
    assert not RR._row_is_finding(row)

    rc, stdout, stderr = _run_module(workspace, as_json=False)
    assert rc == RR.EXIT_CLEAN
    assert "degraded" in stdout.lower()
