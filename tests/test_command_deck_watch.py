"""tests/test_command_deck_watch.py — feat-foundry-contract-done-when-escalate-when
(AC-DWE-3, ac-r1-stop-stopping charter contract-done-when-escalate-when).

Drives `scripts/foundry_command_deck_watch.py`'s tick-prompt rendering over a fixture release:
per ready atom, the prompt states its `done_when`/`escalate_when` — read from the contract when
the atom carries a `contract_ref` with those fields populated, and from the charter's own
`## Done when` / `## Escalate when` sections when the atom carries a `charter_ref` (simulated via
`setattr`, since the sibling atom `release-loader-vocabulary` — riding in parallel — is the one
that adds `charter_ref` to `Atom`'s loader; this suite never edits `scripts/foundry_release.py`)
— plus the standing yield rule, verbatim.

Fixture construction mirrors `tests/test_command_deck.py`'s own idiom (`_write_atom`/`_manifest`/
`_git`/`corpus`) rather than inventing a second one; this file's fixtures are intentionally a
minimal, self-contained subset (only what AC-DWE-3 needs), not an import of the sibling file's
private helpers.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
FIXTURES = os.path.join(HERE, "fixtures", "done-when")
sys.path.insert(0, SCRIPTS)

import foundry_command_deck as cd            # noqa: E402
import foundry_command_deck_watch as watch    # noqa: E402
import foundry_contract as fc                 # noqa: E402


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
        - "scripts/{name}.py"
    checkpoints:
      - {{ac_id: AC-{up}-1, surface: "cli:x", locator: "true", expect: {{op: matches, value: "OK", baseline: pre-change}}}}
    """)


def _write_atom(root, name, *, done_when=None, escalate_when=None):
    d = os.path.join(root, "specs", name)
    os.makedirs(d, exist_ok=True)
    spec_ref = f"specs/{name}/feat-{name}.md"
    contract_ref = f"specs/{name}/acceptance-contract.yaml"
    spec_path, contract_path = os.path.join(root, spec_ref), os.path.join(root, contract_ref)
    up = name.upper().replace("-", "")

    with open(spec_path, "w", encoding="utf-8") as f:
        f.write(SPEC_TMPL.format(name=name, up=up))
    spec_sha = fc.spec_sha256(spec_path)

    body = CONTRACT_TMPL.format(spec_ref=spec_ref, spec_sha=spec_sha, name=name, up=up)
    if done_when is not None:
        body += "done_when:\n" + "\n".join(f'  - "{x}"' for x in done_when) + "\n"
    if escalate_when is not None:
        body += "escalate_when:\n" + "\n".join(f"  - {x}" for x in escalate_when) + "\n"
    with open(contract_path, "w", encoding="utf-8") as f:
        f.write(body)

    csha = fc.contract_sha256(contract_path)
    with open(contract_path, "a", encoding="utf-8") as f:
        f.write(
            f"{fc.SENTINEL}\n"
            "authorized:\n"
            "  operator_id: op_fixture\n"
            "  authorized_at: 2026-08-13T00:00:00Z\n"
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
    doc = {"id": rid, "description": f"fixture programme {rid}", "state": state, "atoms": atoms}
    with open(os.path.join(d, "release.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, sort_keys=False)


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture
def corpus(tmp_path):
    root = os.path.realpath(str(tmp_path))
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "user.name", "fixture")

    _write_atom(root, "contractatom",
               done_when=["test:tests/test_command_deck_watch.py::test_fixture_contract_renders"],
               escalate_when=["external-provisioning", "no-consensus-after-research"])
    _write_atom(root, "bareatom")   # no done_when/escalate_when declared at all

    _manifest(root, "prog-dwe", [
        {"id": "contractatom", "spec_ref": "specs/contractatom/feat-contractatom.md",
         "contract_ref": "specs/contractatom/acceptance-contract.yaml", "depends_on": []},
        {"id": "bareatom", "spec_ref": "specs/bareatom/feat-bareatom.md",
         "contract_ref": "specs/bareatom/acceptance-contract.yaml", "depends_on": []},
    ])
    with open(os.path.join(root, "README.md"), "w") as f:
        f.write("fixture\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "fixture")
    return root


# ─────────────────────────────────────────────────── markdown-list parsing (lenient, AC-DWE-3) ==== #

def test_fixture_charter_parses_both_sections_leniently():
    with open(os.path.join(FIXTURES, "sample-charter.md"), encoding="utf-8") as fh:
        text = fh.read()
    done = watch._parse_markdown_list_section(text, "Done when")
    escalate = watch._parse_markdown_list_section(text, "Escalate when")
    assert len(done) == 2, done                 # one "-" bullet, one "*" bullet
    assert any(x.startswith("test:") for x in done), done
    assert any(x.startswith("cli:") for x in done), done
    assert len(escalate) == 2, escalate          # one plain, one indented "-" bullet
    assert "external-provisioning" in escalate, escalate
    assert "no-consensus-after-research" in escalate, escalate


def test_markdown_list_parser_returns_empty_for_absent_section():
    assert watch._parse_markdown_list_section("# Charter\n\n## Goal\nnothing here.\n",
                                               "Done when") == []


# ────────────────────────────────────────────────── done_when_escalate_when resolution (AC-DWE-3) ==== #

def test_done_when_escalate_when_reads_from_contract(corpus):
    rel = cd.resolve_programme("prog-dwe", project_dir=corpus)
    atom = rel.by_id["contractatom"]
    done, escalate = watch.done_when_escalate_when(atom, corpus)
    assert done == ["test:tests/test_command_deck_watch.py::test_fixture_contract_renders"]
    assert escalate == ["external-provisioning", "no-consensus-after-research"]


def test_done_when_escalate_when_absent_is_empty_not_error(corpus):
    rel = cd.resolve_programme("prog-dwe", project_dir=corpus)
    atom = rel.by_id["bareatom"]
    done, escalate = watch.done_when_escalate_when(atom, corpus)
    assert done == []
    assert escalate == []


def test_done_when_escalate_when_prefers_charter_ref_over_contract(corpus):
    """`charter_ref` is added to `Atom` by a sibling atom riding in PARALLEL
    (release-loader-vocabulary) — simulated here via `setattr` on the resolved Atom object, never
    by editing `scripts/foundry_release.py`'s loader (out of this atom's write boundary)."""
    rel = cd.resolve_programme("prog-dwe", project_dir=corpus)
    atom = rel.by_id["contractatom"]      # its CONTRACT carries a different done_when
    assert getattr(atom, "charter_ref", None) is None    # not yet wired in this repo — sanity
    charter_ref = "charters/sample-charter.md"
    setattr(atom, "charter_ref", charter_ref)
    # copy the fixture charter under the corpus root so the relative path resolves there
    dest = os.path.join(corpus, charter_ref)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(os.path.join(FIXTURES, "sample-charter.md"), encoding="utf-8") as fh:
        text = fh.read()
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(text)

    done, escalate = watch.done_when_escalate_when(atom, corpus)
    assert any(x.startswith("test:") for x in done), done
    assert "external-provisioning" in escalate, escalate
    # NOT the contract's own done_when — charter_ref took precedence
    assert done != ["test:tests/test_command_deck_watch.py::test_fixture_contract_renders"]


# ─────────────────────────────────────────────────────────── AC-DWE-3: the rendered tick prompt ==== #

def test_render_prompt_states_standing_rule_verbatim(corpus):
    text = watch.render_prompt("prog-dwe", project_dir=corpus)
    assert watch.STANDING_YIELD_RULE in text
    assert ("yield ONLY on done_when met | escalate_when hit | a fork the fork policy parks; "
           "anything else is a Next Task and the tick continues.") in text


def test_render_prompt_lists_done_when_and_escalate_when_per_ready_atom(corpus):
    text = watch.render_prompt("prog-dwe", project_dir=corpus)
    assert "contractatom" in text
    assert "test:tests/test_command_deck_watch.py::test_fixture_contract_renders" in text
    assert "external-provisioning" in text
    assert "no-consensus-after-research" in text
    # the bare atom (no done_when/escalate_when declared) renders the absence, not silence
    assert "bareatom" in text
    assert "(not declared)" in text
