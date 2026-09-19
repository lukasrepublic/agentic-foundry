"""tests/test_manifest_to_tasklist.py — feat manifest-to-tasklist (AC-MTL-1..5).

Drives `scripts/foundry-manifest-to-tasklist.py`'s pure projection (`build_plan`,
`resolve_tasks_dir`, `_existing_subjects`) over a throwaway `tmp_path` fixture manifest carrying
BOTH lanes (a charter atom + a factory atom, per AC-MTL-5), plus the CLI's own exit-code contract
(0 plan / 2 malformed-or-unresolvable release / 3 non-slug-or-outside-expectations), plus the
registration of the new script into both permission-floor copies (AC-MTL-5's fourth bullet).

Fixture construction mirrors `tests/test_command_deck_watch.py`'s idiom (`_git` / a hand-authored
`authorized:` trailer computed via `foundry_contract`, never the full audit-record+authorize CLI
round-trip) — a charter atom's authorization is instead controlled by whether ITS OWN fixture copy
is `git commit`-ed in the throwaway repo (AC-RLV-3's real mechanism), exercised via
`tests/fixtures/tasklist/sample-charter.md`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap

import pytest
import yaml

from conftest import REPO_ROOT, load_module

mtl = load_module("scripts/foundry-manifest-to-tasklist.py", "foundry_manifest_to_tasklist")
fr = load_module("scripts/foundry_release.py", "foundry_release")
fc = load_module("scripts/foundry_contract.py", "foundry_contract")

SCRIPT = os.path.join(REPO_ROOT, "scripts", "foundry-manifest-to-tasklist.py")
FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures", "tasklist")


# ================================================================================================ #
# fixture construction
# ================================================================================================ #

def _git(root, *args, check=True):
    subprocess.run(["git", *args], cwd=root, check=check,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _init_git(root):
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "fixture")


def _write_charter(root, rel_path, *, commit):
    """Copies the shipped `sample-charter.md` fixture verbatim to `rel_path` under `root`, and
    `git commit`s ONLY that file iff `commit` — the real AC-RLV-3 mechanism a charter-lane atom's
    authorization is decided by."""
    dst = os.path.join(root, rel_path)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(os.path.join(FIXTURES, "sample-charter.md"), dst)
    if commit:
        _git(root, "add", rel_path)
        _git(root, "commit", "-qm", f"add {rel_path}")


SPEC_TMPL = textwrap.dedent("""\
    # {name}

    **Status:** DRAFT

    <!-- normative -->

    - **AC-{up}-1** *(Invariant)*: the fixture atom {name} SHALL exist.

    <!-- /normative -->
    """)

CONTRACT_TMPL = textwrap.dedent("""\
    spec_ref: {spec_ref}
    spec_sha256: "{spec_sha}"
    scope:
      allowed_paths:
        - "{allowed_path}"
    checkpoints:
      - {{ac_id: AC-{up}-1, surface: "cli:x", locator: "true", expect: {{op: matches, value: "OK", baseline: pre-change}}}}
    """)


def _write_factory_atom(root, name, *, authorized=True, done_when=None, escalate_when=None,
                        allowed_path=None):
    """Mirrors `tests/test_command_deck_watch.py::_write_atom` — a hand-authored `authorized:`
    trailer (computed via `foundry_contract`, never the audit-record+authorize CLI) so a
    factory-lane atom's AUTHORIZED state is controlled directly, independent of git."""
    d = os.path.join(root, "specs", name)
    os.makedirs(d, exist_ok=True)
    spec_ref = f"specs/{name}/feat-{name}.md"
    contract_ref = f"specs/{name}/acceptance-contract.yaml"
    spec_path, contract_path = os.path.join(root, spec_ref), os.path.join(root, contract_ref)
    up = name.upper().replace("-", "")

    with open(spec_path, "w", encoding="utf-8") as f:
        f.write(SPEC_TMPL.format(name=name, up=up))
    spec_sha = fc.spec_sha256(spec_path)

    body = CONTRACT_TMPL.format(spec_ref=spec_ref, spec_sha=spec_sha, name=name, up=up,
                                allowed_path=allowed_path or f"scripts/{name}/**")
    if done_when is not None:
        body += "done_when:\n" + "\n".join(f'  - "{x}"' for x in done_when) + "\n"
    if escalate_when is not None:
        body += "escalate_when:\n" + "\n".join(f"  - {x}" for x in escalate_when) + "\n"
    with open(contract_path, "w", encoding="utf-8") as f:
        f.write(body)

    if authorized:
        csha = fc.contract_sha256(contract_path)
        with open(contract_path, "a", encoding="utf-8") as f:
            f.write(
                f"{fc.SENTINEL}\n"
                "authorized:\n"
                "  operator_id: op_fixture\n"
                "  authorized_at: 2026-09-19T00:00:00Z\n"
                "  auth_seq: 1\n"
                "  supersedes: null\n"
                f"  spec_sha256: {spec_sha}\n"
                f"  contract_sha256: {csha}\n"
                "  merge_autonomy_mode: lean\n"
            )
    return spec_ref, contract_ref


def _manifest(root, rid, atoms, *, state="active"):
    d = os.path.join(root, ".foundry", "releases", rid)
    os.makedirs(d, exist_ok=True)
    doc = {"id": rid, "description": f"fixture release {rid}", "state": state, "atoms": atoms}
    with open(os.path.join(d, "release.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, sort_keys=False)


RELEASE_ID = "r-mtl-fixture"


@pytest.fixture
def corpus(tmp_path):
    """A release carrying THREE atoms, per AC-MTL-5 ("charter + factory atoms"):
      - `alpha` — charter-lane, its charter file COMMITTED -> AUTHORIZED.
      - `beta`  — factory-lane, depends_on [alpha], AUTHORIZED (hand-authored trailer).
      - `gamma` — charter-lane, depends_on [alpha], its charter file left UNCOMMITTED ->
                  NOT authorized (AC-MTL-3's own vocabulary: "charter not committed")."""
    root = os.path.realpath(str(tmp_path))
    _init_git(root)

    alpha_charter = "charters/alpha.md"
    _write_charter(root, alpha_charter, commit=True)
    gamma_charter = "charters/gamma.md"
    _write_charter(root, gamma_charter, commit=False)   # deliberately uncommitted

    beta_spec, beta_contract = _write_factory_atom(
        root, "beta", authorized=True,
        done_when=["test:tests/test_manifest_to_tasklist.py::test_plan_shape_and_ordering"],
        escalate_when=["no-consensus-after-research"],
        allowed_path="scripts/beta-fixture/**",
    )

    _manifest(root, RELEASE_ID, [
        {"id": "alpha", "charter_ref": alpha_charter, "depends_on": []},
        {"id": "beta", "spec_ref": beta_spec, "contract_ref": beta_contract,
         "depends_on": ["alpha"]},
        {"id": "gamma", "charter_ref": gamma_charter, "depends_on": ["alpha"]},
    ])
    return root


def _load(root):
    return fr.load_release(RELEASE_ID, project_dir=root)


# ================================================================================================ #
# AC-MTL-1: plan shape + manifest-declaration order
# ================================================================================================ #

class TestPlanShapeAndOrdering:
    def test_plan_shape_and_ordering(self, corpus):
        release = _load(corpus)
        plan = mtl.build_plan(release, project_dir=corpus, tasks_dir=None)
        assert plan["release"] == RELEASE_ID
        ids = [t["subject"] for t in plan["tasks"]]
        assert ids == [
            "atom:r-mtl-fixture/alpha",
            "atom:r-mtl-fixture/beta",
            "atom:r-mtl-fixture/gamma",
        ], ids   # manifest declaration order, not topological/alphabetical order

        by_subject = {t["subject"]: t for t in plan["tasks"]}
        alpha = by_subject["atom:r-mtl-fixture/alpha"]
        beta = by_subject["atom:r-mtl-fixture/beta"]
        gamma = by_subject["atom:r-mtl-fixture/gamma"]

        assert alpha["blockedBy"] == []
        assert beta["blockedBy"] == ["atom:r-mtl-fixture/alpha"]
        assert gamma["blockedBy"] == ["atom:r-mtl-fixture/alpha"]

        # description carries the ref + done_when + escalate_when + scope (AC-MTL-1)
        assert "charter_ref: charters/alpha.md" in alpha["description"]
        assert "done_when:" in alpha["description"] and "escalate_when:" in alpha["description"]
        assert "scope:" in alpha["description"]
        assert "tests/fixtures/tasklist/sample-charter.md" in alpha["description"]  # its own scope

        assert "spec_ref: specs/beta/feat-beta.md" in beta["description"]
        assert "contract_ref: specs/beta/acceptance-contract.yaml" in beta["description"]
        assert "test:tests/test_manifest_to_tasklist.py::test_plan_shape_and_ordering" in beta["description"]
        assert "no-consensus-after-research" in beta["description"]
        assert "scripts/beta-fixture/**" in beta["description"]

    def test_executes_nothing_and_defaults_to_create(self, corpus):
        release = _load(corpus)
        plan = mtl.build_plan(release, project_dir=corpus, tasks_dir=None)
        assert all(t["action"] == "create" for t in plan["tasks"])
        assert all("task_id" not in t for t in plan["tasks"])


# ================================================================================================ #
# AC-MTL-2: idempotence against a fixture tasks dir
# ================================================================================================ #

class TestIdempotence:
    def _tasks_dir_with(self, tmp_path, subject, task_id="5"):
        team_dir = tmp_path / "tasks" / "team-1"
        team_dir.mkdir(parents=True)
        (team_dir / f"{task_id}.json").write_text(json.dumps({
            "id": task_id, "subject": subject, "description": "x", "activeForm": "y",
            "status": "pending", "blocks": [], "blockedBy": [],
        }), encoding="utf-8")
        return str(tmp_path / "tasks")

    def test_existing_subject_is_skipped_with_its_task_id(self, corpus, tmp_path):
        release = _load(corpus)
        tasks_dir = self._tasks_dir_with(tmp_path, "atom:r-mtl-fixture/beta", task_id="5")
        plan = mtl.build_plan(release, project_dir=corpus, tasks_dir=tasks_dir)
        by_subject = {t["subject"]: t for t in plan["tasks"]}
        assert by_subject["atom:r-mtl-fixture/beta"]["action"] == "skip"
        assert by_subject["atom:r-mtl-fixture/beta"]["task_id"] == "5"
        # nothing else is duplicated/affected
        assert by_subject["atom:r-mtl-fixture/alpha"]["action"] == "create"
        assert by_subject["atom:r-mtl-fixture/gamma"]["action"] == "create"

    def test_missing_tasks_dir_is_all_create(self, corpus, tmp_path):
        release = _load(corpus)
        never_created = str(tmp_path / "does" / "not" / "exist")
        plan = mtl.build_plan(release, project_dir=corpus, tasks_dir=never_created)
        assert all(t["action"] == "create" for t in plan["tasks"])

    def test_malformed_json_file_is_skipped_not_a_crash(self, tmp_path):
        d = tmp_path / "tasks" / "team-1"
        d.mkdir(parents=True)
        (d / "broken.json").write_text("{not json", encoding="utf-8")
        (d / "not-a-mapping.json").write_text("[1,2,3]", encoding="utf-8")
        assert mtl._existing_subjects(str(tmp_path / "tasks")) == {}

    def test_recursive_scan_across_team_directories(self, tmp_path):
        d1 = tmp_path / "tasks" / "team-1"
        d2 = tmp_path / "tasks" / "team-2"
        d1.mkdir(parents=True)
        d2.mkdir(parents=True)
        (d1 / "1.json").write_text(json.dumps({"id": "1", "subject": "atom:x/y"}), encoding="utf-8")
        (d2 / "9.json").write_text(json.dumps({"id": "9", "subject": "atom:x/z"}), encoding="utf-8")
        subjects = mtl._existing_subjects(str(tmp_path / "tasks"))
        assert subjects == {"atom:x/y": "1", "atom:x/z": "9"}


# ================================================================================================ #
# AC-MTL-3: the unauthorized flag — still emitted, never omitted
# ================================================================================================ #

class TestUnauthorizedFlag:
    def test_committed_charter_is_authorized_true(self, corpus):
        release = _load(corpus)
        plan = mtl.build_plan(release, project_dir=corpus, tasks_dir=None)
        by_subject = {t["subject"]: t for t in plan["tasks"]}
        assert by_subject["atom:r-mtl-fixture/alpha"]["authorized"] is True

    def test_authorized_factory_atom_is_authorized_true(self, corpus):
        release = _load(corpus)
        plan = mtl.build_plan(release, project_dir=corpus, tasks_dir=None)
        by_subject = {t["subject"]: t for t in plan["tasks"]}
        assert by_subject["atom:r-mtl-fixture/beta"]["authorized"] is True

    def test_uncommitted_charter_is_authorized_false_but_still_emitted(self, corpus):
        release = _load(corpus)
        plan = mtl.build_plan(release, project_dir=corpus, tasks_dir=None)
        subjects = [t["subject"] for t in plan["tasks"]]
        assert "atom:r-mtl-fixture/gamma" in subjects   # never omitted
        by_subject = {t["subject"]: t for t in plan["tasks"]}
        gamma = by_subject["atom:r-mtl-fixture/gamma"]
        assert gamma["authorized"] is False
        assert gamma["action"] == "create"   # AC-MTL-1: plan still has a row to create it


# ================================================================================================ #
# resolve_tasks_dir — fail-closed containment (exit 3's mechanism)
# ================================================================================================ #

class TestResolveTasksDir:
    def test_none_defaults_to_tasks_root(self, tmp_path):
        home = str(tmp_path)
        assert mtl.resolve_tasks_dir(None, home=home) == mtl.default_tasks_root(home)

    def test_relative_dir_resolves_under_tasks_root(self, tmp_path):
        home = str(tmp_path)
        got = mtl.resolve_tasks_dir("team-1", home=home)
        assert got == os.path.join(mtl.default_tasks_root(home), "team-1")

    def test_absolute_dir_inside_root_is_accepted(self, tmp_path):
        home = str(tmp_path)
        inside = os.path.join(mtl.default_tasks_root(home), "team-1")
        assert mtl.resolve_tasks_dir(inside, home=home) == inside

    def test_absolute_dir_outside_root_is_refused(self, tmp_path):
        home = str(tmp_path)
        outside = str(tmp_path / "elsewhere")
        with pytest.raises(mtl.PlanError):
            mtl.resolve_tasks_dir(outside, home=home)

    def test_traversal_escape_is_refused(self, tmp_path):
        home = str(tmp_path)
        escaping = os.path.join(mtl.default_tasks_root(home), "..", "..", "etc")
        with pytest.raises(mtl.PlanError):
            mtl.resolve_tasks_dir(escaping, home=home)


# ================================================================================================ #
# the CLI — exit codes (0 / 2 / 3) and JSON on stdout
# ================================================================================================ #

class TestCli:
    def _run(self, args, env_extra=None):
        env = dict(os.environ)
        env.pop("CLAUDE_PROJECT_DIR", None)
        if env_extra:
            env.update(env_extra)
        r = subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, env=env)
        return r

    def test_exit_0_prints_json_plan(self, corpus, tmp_path):
        home = str(tmp_path / "home")
        os.makedirs(home, exist_ok=True)
        r = self._run([RELEASE_ID, "--root", corpus], env_extra={"HOME": home})
        assert r.returncode == 0, r.stderr
        doc = json.loads(r.stdout)
        assert doc["release"] == RELEASE_ID
        assert len(doc["tasks"]) == 3

    def test_exit_3_on_non_slug_release_id(self, tmp_path):
        r = self._run(["not a slug!", "--root", str(tmp_path)])
        assert r.returncode == 3, (r.returncode, r.stdout, r.stderr)

    def test_exit_2_on_unresolvable_release(self, tmp_path):
        r = self._run(["no-such-release", "--root", str(tmp_path)])
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)

    def test_exit_3_on_tasks_dir_outside_expectations(self, corpus, tmp_path):
        home = str(tmp_path / "home2")
        os.makedirs(home, exist_ok=True)
        outside = str(tmp_path / "definitely-not-under-home")
        r = self._run([RELEASE_ID, "--root", corpus, "--tasks-dir", outside],
                      env_extra={"HOME": home})
        assert r.returncode == 3, (r.returncode, r.stdout, r.stderr)


# ================================================================================================ #
# AC-MTL-5 (4th bullet): the new script's registration
# ================================================================================================ #

class TestRegistration:
    def test_registered_allow_in_both_floor_copies_identically(self):
        cli_path = os.path.join(REPO_ROOT, "cli", "permission-floor.json")
        docs_path = os.path.join(REPO_ROOT, "docs", "permission-floor.json")
        with open(cli_path, encoding="utf-8") as f:
            cli_doc = json.load(f)
        with open(docs_path, encoding="utf-8") as f:
            docs_doc = json.load(f)
        assert cli_doc == docs_doc   # byte-identical-in-substance (both floor copies)

        needle = "scripts/foundry-manifest-to-tasklist.py"
        matches = [e for e in cli_doc["entries"] if needle in e["rule"]]
        assert len(matches) == 1, matches
        assert matches[0]["tier"] == "allow"

    def test_registered_files_are_byte_identical(self):
        cli_path = os.path.join(REPO_ROOT, "cli", "permission-floor.json")
        docs_path = os.path.join(REPO_ROOT, "docs", "permission-floor.json")
        with open(cli_path, "rb") as f:
            cli_bytes = f.read()
        with open(docs_path, "rb") as f:
            docs_bytes = f.read()
        assert cli_bytes == docs_bytes
