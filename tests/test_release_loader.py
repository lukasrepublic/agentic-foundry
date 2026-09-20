"""tests/test_release_loader.py — the release manifest's optional `integration_branch` field
(branch-and-worktree-discipline, AC-BWD-4, v1.16.0).

`scripts/foundry_release.py`'s own broader manifest-loading surface (RA/RD state machine, the
`acceptance:` practice log, `depends_on` cycle detection, ...) is already covered by
`tests/test_release.py`; this file is scoped to the ONE new field this atom adds: acceptance,
round-trip through `save_release`, and refusal of a malformed value.
"""
from __future__ import annotations

import os

import pytest
import yaml

from conftest import REPO_ROOT, load_module

release = load_module("scripts/foundry_release.py", "foundry_release")


def _write_release(project_dir, rel_id, **extra_fields):
    rel_dir = os.path.join(project_dir, ".foundry", "releases", rel_id)
    os.makedirs(rel_dir, exist_ok=True)
    doc = {
        "id": rel_id, "description": "d", "state": "backlog",
        "atoms": [{"id": "a1", "spec_ref": "specs/a1.md", "contract_ref": "specs/a1.yaml",
                  "depends_on": []}],
    }
    doc.update(extra_fields)
    with open(os.path.join(rel_dir, "release.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, sort_keys=False)
    return rel_dir


def test_absent_integration_branch_defaults_to_none(tmp_path):
    _write_release(str(tmp_path), "r-ib-absent")
    rel = release.load_release("r-ib-absent", project_dir=str(tmp_path))
    assert rel.integration_branch is None


def test_valid_integration_branch_is_accepted_and_exposed(tmp_path):
    _write_release(str(tmp_path), "r-ib-ok", integration_branch="release/1.16.0")
    rel = release.load_release("r-ib-ok", project_dir=str(tmp_path))
    assert rel.integration_branch == "release/1.16.0"


@pytest.mark.parametrize("bad_value", [
    "", "   ", "  release/1.16.0", "release/1.16.0  ", "release/1.16.0\nrelease/1.16.1",
    123, [], {}, True,
])
def test_malformed_integration_branch_is_refused(tmp_path, bad_value):
    _write_release(str(tmp_path), "r-ib-bad", integration_branch=bad_value)
    with pytest.raises(release.ReleaseError):
        release.load_release("r-ib-bad", project_dir=str(tmp_path))


def test_integration_branch_round_trips_through_save_release(tmp_path):
    _write_release(str(tmp_path), "r-ib-rt", integration_branch="release/9.9.9")
    rel = release.load_release("r-ib-rt", project_dir=str(tmp_path))
    release.save_release(rel, project_dir=str(tmp_path))
    reloaded = release.load_release("r-ib-rt", project_dir=str(tmp_path))
    assert reloaded.integration_branch == "release/9.9.9"


def test_a_manifest_authored_before_this_extension_saves_byte_stable_no_noise(tmp_path):
    # AC-BWD-4 is additive: a manifest that never set integration_branch must never grow an
    # `integration_branch: null` key on a re-save (mirrors the `acceptance:`/`paths:` convention
    # this loader already follows).
    _write_release(str(tmp_path), "r-ib-none")
    rel = release.load_release("r-ib-none", project_dir=str(tmp_path))
    path = release.save_release(rel, project_dir=str(tmp_path))
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    assert "integration_branch" not in doc


def test_integration_branch_is_not_touched_by_the_ra_rd_state_machine(tmp_path):
    # purely descriptive at this loader (module docstring's own RA/RD principle) -- a `transition`
    # call must not require, consume, or clear it.
    _write_release(str(tmp_path), "r-ib-state", integration_branch="release/1.16.0")
    specs_dir = os.path.join(str(tmp_path), "specs")
    os.makedirs(specs_dir, exist_ok=True)
    for name in ("a1.md", "a1.yaml"):
        with open(os.path.join(specs_dir, name), "w", encoding="utf-8") as f:
            f.write("x")
    rel = release.load_release("r-ib-state", project_dir=str(tmp_path))
    rel = release.transition(rel, "planned", project_dir=str(tmp_path))
    assert rel.integration_branch == "release/1.16.0"
    assert rel.state == "planned"
