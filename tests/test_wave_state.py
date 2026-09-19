"""feat wave-learn — AC-WVL-1..5.

"Next waves learn from previous ones" (operator decision
`.foundry/decisions/2026-09-18-spec-is-a-living-document.md`). What one wave discovers —
decisions, reusable artifacts, open risks, the amendments reality forced — gets a file
(`.foundry/releases/<id>/state.yaml`), the command deck writes it at wave close, and the next
wave's `/foundry:intake` reads it before asking anything.

EVERY TEST PRINTS ITS OWN TOKEN (house convention — a skipped/deselected/collection-errored test
prints nothing, so the row convicts).

FIXTURES ARE MATERIALIZED FILES under tests/fixtures/wave-state/, not inlined dicts: the merge and
schema tests read the same bytes a real wave would have left on disk. Names in the fixtures are
deliberately neutral ("acme", "productA") — no real client/product name from the workspace.
"""
import json
import os
import subprocess
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)

import foundry_command_deck as cd            # noqa: E402
import foundry_release as fr                 # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures", "wave-state")
SCHEMA_PATH = os.path.join(os.path.dirname(HERE), "schema", "wave-state.schema.json")


def _fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ───────────────────────────────────────────────────────────────── AC-WVL-2: the schema itself

def test_schema_file_is_valid_json():
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        schema = json.load(fh)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"decisions", "artifacts", "open_risks", "amendments_needed"}
    assert set(schema["properties"]["artifacts"]["items"]["required"]) == {"path", "reuse_as"}
    print("WVL-SCHEMA-FILE-OK")


def test_schema_valid_document_passes():
    doc = _fixture("existing-state.yaml")
    assert cd.validate_wave_state(doc) == []
    print("WVL-SCHEMA-VALID-OK")


def test_schema_extra_top_level_key_fails():
    doc = _fixture("invalid-extra-key.yaml")
    errors = cd.validate_wave_state(doc)
    assert errors, "an extra top-level key must be rejected"
    assert any("status" in e for e in errors), errors
    print("WVL-SCHEMA-EXTRA-KEY-REJECTED-OK")


def test_schema_missing_reuse_as_fails():
    doc = _fixture("invalid-missing-reuse-as.yaml")
    errors = cd.validate_wave_state(doc)
    assert errors, "an artifacts[] item with no reuse_as must be rejected"
    assert any("reuse_as" in e for e in errors), errors
    print("WVL-SCHEMA-MISSING-REUSE-AS-REJECTED-OK")


def test_schema_non_list_fails():
    doc = _fixture("invalid-non-list.yaml")
    errors = cd.validate_wave_state(doc)
    assert errors, "decisions given as a scalar must be rejected"
    assert any("must be a list" in e for e in errors), errors
    print("WVL-SCHEMA-NON-LIST-REJECTED-OK")


def test_schema_no_per_atom_status_key_admitted():
    """The charter's 'Out of scope': no per-atom status/tracker key is ever a valid top-level key,
    whatever it is named."""
    for bogus in ("status", "next_action", "atom_state", "progress"):
        doc = {"decisions": [], "artifacts": [], "open_risks": [], "amendments_needed": [], bogus: []}
        errors = cd.validate_wave_state(doc)
        assert errors, f"{bogus!r} must not be an admitted top-level key"
    print("WVL-NO-PER-ATOM-STATUS-OK")


# ───────────────────────────────────────────────────────────────── merge never drops an entry

def test_merge_preserves_existing_entries_and_dedupes():
    existing = _fixture("existing-state.yaml")
    new = _fixture("new-entries.yaml")
    merged = cd.merge_wave_state(existing, new)

    assert cd.validate_wave_state(merged) == []
    for key in ("decisions", "artifacts", "open_risks", "amendments_needed"):
        for item in existing[key]:
            assert item in merged[key], f"merge dropped an existing {key} entry: {item!r}"

    # the fixture's new-entries.yaml repeats one decision and one artifact verbatim — a merge must
    # not duplicate them.
    assert merged["decisions"].count(existing["decisions"][0]) == 1
    assert merged["artifacts"].count(existing["artifacts"][0]) == 1
    # and it must still carry what was genuinely new.
    assert len(merged["decisions"]) == 2
    assert len(merged["artifacts"]) == 2
    print("WVL-MERGE-PRESERVES-AND-DEDUPES-OK")


def test_merge_against_absent_existing_state_is_the_new_entries():
    new = _fixture("new-entries.yaml")
    merged = cd.merge_wave_state(None, new)
    assert cd.validate_wave_state(merged) == []
    assert merged["decisions"] == new["decisions"]
    print("WVL-MERGE-NO-EXISTING-OK")


# ───────────────────────────────────────────────────────────────── AC-WVL-1/-5: write → read round trip

def _release(rid, project_dir):
    """A minimal one-atom release, no real spec/contract files needed (load_release never stats
    them; only derive_run_state would, and every test below either overrides `run_rows` or checks
    a REFUSAL that never reaches derive_run_state)."""
    atom = fr.Atom(id="a1", spec_ref="specs/fixture/feat-fixture.md",
                   contract_ref="specs/fixture/acceptance-contract.yaml", depends_on=[])
    return fr.Release(rid, "fixture release for wave-learn", "active", [atom], ["a1"])


def test_write_then_read_round_trip(tmp_path):
    """AC-WVL-5: write at wave close -> schema-validate -> the next wave's intake reads it back."""
    rid = "acme-wave-fixture"
    release = _release(rid, str(tmp_path))
    new_entries = _fixture("new-entries.yaml")
    settled_rows = [{"id": "a1", "state": "merged"}]

    written = cd.write_wave_state(release, new_entries, project_dir=str(tmp_path),
                                   run_rows=settled_rows)
    assert cd.validate_wave_state(written) == []

    path = cd.wave_state_path(rid, project_dir=str(tmp_path))
    assert os.path.isfile(path)

    # read back exactly as intake's read-first step would (AC-WVL-3): a fresh load off disk.
    read_back = cd.load_wave_state(rid, project_dir=str(tmp_path))
    assert read_back == written
    assert cd.validate_wave_state(read_back) == []
    assert read_back["decisions"] == new_entries["decisions"]
    print("WVL-ROUND-TRIP-OK")


def test_second_write_at_a_later_wave_close_merges_not_overwrites(tmp_path):
    rid = "acme-wave-fixture-2"
    release = _release(rid, str(tmp_path))
    settled_rows = [{"id": "a1", "state": "merged"}]

    first = cd.write_wave_state(release, _fixture("existing-state.yaml"),
                                 project_dir=str(tmp_path), run_rows=settled_rows)
    second = cd.write_wave_state(release, _fixture("new-entries.yaml"),
                                  project_dir=str(tmp_path), run_rows=settled_rows)

    for key in ("decisions", "artifacts", "open_risks", "amendments_needed"):
        for item in first[key]:
            assert item in second[key], f"a later wave-close write dropped {key} entry {item!r}"

    on_disk = cd.load_wave_state(rid, project_dir=str(tmp_path))
    assert on_disk == second
    print("WVL-SECOND-WRITE-MERGES-OK")


def test_write_refuses_when_release_not_completed(tmp_path):
    rid = "acme-wave-unfinished"
    release = _release(rid, str(tmp_path))
    unfinished_rows = [{"id": "a1", "state": "planned"}]

    with pytest.raises(cd.CommandDeckError):
        cd.write_wave_state(release, _fixture("new-entries.yaml"),
                             project_dir=str(tmp_path), run_rows=unfinished_rows)
    assert not os.path.isfile(cd.wave_state_path(rid, project_dir=str(tmp_path)))
    print("WVL-REFUSES-UNTIL-COMPLETED-OK")


def test_write_force_bypasses_the_completed_check(tmp_path):
    rid = "acme-wave-forced"
    release = _release(rid, str(tmp_path))
    unfinished_rows = [{"id": "a1", "state": "planned"}]

    written = cd.write_wave_state(release, _fixture("new-entries.yaml"),
                                   project_dir=str(tmp_path), run_rows=unfinished_rows, force=True)
    assert cd.validate_wave_state(written) == []
    print("WVL-FORCE-BYPASS-OK")


def test_write_refuses_invalid_new_entries(tmp_path):
    rid = "acme-wave-invalid-entries"
    release = _release(rid, str(tmp_path))
    settled_rows = [{"id": "a1", "state": "merged"}]

    with pytest.raises(cd.CommandDeckError):
        cd.write_wave_state(release, _fixture("invalid-extra-key.yaml"),
                             project_dir=str(tmp_path), run_rows=settled_rows)
    assert not os.path.isfile(cd.wave_state_path(rid, project_dir=str(tmp_path)))
    print("WVL-REFUSES-INVALID-ENTRIES-OK")


def test_write_wave_state_is_atomic_on_a_mid_write_crash(tmp_path, monkeypatch):
    """A crash mid-`yaml.safe_dump` must leave `state.yaml` exactly as it was — either the old
    document or the new one, never truncated/partial (a temp-file + os.replace write, not a
    write-in-place)."""
    rid = "acme-wave-atomic"
    release = _release(rid, str(tmp_path))
    settled_rows = [{"id": "a1", "state": "merged"}]

    cd.write_wave_state(release, _fixture("existing-state.yaml"),
                         project_dir=str(tmp_path), run_rows=settled_rows)
    path = cd.wave_state_path(rid, project_dir=str(tmp_path))
    with open(path, "rb") as fh:
        before = fh.read()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated crash mid-write")

    monkeypatch.setattr(cd.yaml, "safe_dump", _boom)

    with pytest.raises(RuntimeError):
        cd.write_wave_state(release, _fixture("new-entries.yaml"),
                             project_dir=str(tmp_path), run_rows=settled_rows)

    with open(path, "rb") as fh:
        after = fh.read()
    assert after == before, "a crash mid-write must never touch the existing state.yaml"

    leftover = [n for n in os.listdir(os.path.dirname(path)) if n.endswith(".tmp")]
    assert leftover == [], f"a crashed write left a temp file behind: {leftover}"
    print("WVL-ATOMIC-WRITE-OK")


def test_release_completed_reuses_wave_settled_vocabulary():
    assert cd.release_completed([{"id": "a", "state": "merged"}, {"id": "b", "state": "superseded"}])
    assert not cd.release_completed([{"id": "a", "state": "merged"}, {"id": "b", "state": "runnable"}])
    assert not cd.release_completed([])
    print("WVL-COMPLETED-VOCABULARY-OK")


def test_load_missing_state_returns_none_not_fabricated(tmp_path):
    """AC-WVL-4: absence is reported, never fabricated."""
    assert cd.load_wave_state("no-such-release", project_dir=str(tmp_path)) is None
    print("WVL-MISSING-STATE-NONE-OK")


def test_load_invalid_state_on_disk_raises_rather_than_reads_as_absent(tmp_path):
    rid = "acme-wave-corrupt"
    d = os.path.join(str(tmp_path), ".foundry", "releases", rid)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "state.yaml"), "w", encoding="utf-8") as fh:
        yaml.safe_dump({"decisions": [], "status": "nope"}, fh)
    with pytest.raises(cd.CommandDeckError):
        cd.load_wave_state(rid, project_dir=str(tmp_path))
    print("WVL-INVALID-STATE-RAISES-OK")


# ───────────────────────────────────────────────────────────────── the CLI subcommand

def _write_manifest(root, rid, atoms, state="active"):
    d = os.path.join(root, ".foundry", "releases", rid)
    os.makedirs(d, exist_ok=True)
    doc = {"id": rid, "description": f"fixture programme {rid}", "state": state, "atoms": atoms}
    with open(os.path.join(d, "release.yaml"), "w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False)


def test_cli_write_state_subcommand(tmp_path):
    rid = "acme-cli-wave"
    root = str(tmp_path)
    _write_manifest(root, rid, [{"id": "a1", "spec_ref": "specs/fixture/feat-fixture.md",
                                 "contract_ref": "specs/fixture/acceptance-contract.yaml",
                                 "depends_on": []}])
    entries = {"decisions": ["acme decided to ship the CLI test"],
               "artifacts": [{"path": "scripts/fixture_helper.py", "reuse_as": "CLI smoke fixture"}],
               "open_risks": [], "amendments_needed": []}

    r = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "foundry_command_deck.py"), "write-state", rid,
         "--root", root, "--force", "--entries-json", json.dumps(entries)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    on_disk = cd.load_wave_state(rid, project_dir=root)
    assert on_disk == entries
    print("WVL-CLI-WRITE-STATE-OK")


def test_cli_write_state_refuses_with_no_entries(tmp_path):
    rid = "acme-cli-wave-no-entries"
    root = str(tmp_path)
    _write_manifest(root, rid, [{"id": "a1", "spec_ref": "specs/fixture/feat-fixture.md",
                                 "contract_ref": "specs/fixture/acceptance-contract.yaml",
                                 "depends_on": []}])
    r = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS, "foundry_command_deck.py"), "write-state", rid,
         "--root", root, "--force"],
        capture_output=True, text=True)
    assert r.returncode == 2, r.stdout
    assert "REFUSED" in r.stderr
    print("WVL-CLI-NO-ENTRIES-REFUSED-OK")


# ───────────────────────────────────────────────────────────────── AC-WVL-3/-4: intake names the step

def test_intake_skill_names_the_read_first_step():
    """Grep-level: intake must name reading `depends_on_release`'s `state.yaml` BEFORE the first
    discovery question, surface the four lists, and say so (not fabricate) when it is absent."""
    skill_path = os.path.join(os.path.dirname(HERE), "skills", "intake", "SKILL.md")
    with open(skill_path, encoding="utf-8") as fh:
        text = fh.read()
    assert "depends_on_release" in text
    assert "state.yaml" in text
    assert "before the first discovery question" in text
    assert re_search_any(text, ["decisions", "artifacts", "open_risks", "amendments_needed"])
    assert "do **not** fabricate" in text or "do not fabricate" in text.lower()
    print("WVL-INTAKE-NAMES-READ-FIRST-OK")


def re_search_any(text, needles):
    return all(n in text for n in needles)
