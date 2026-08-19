"""tests/test_reference_closure_wiki_prefix.py

G-4 reference closure (`scripts/foundry_audit_preconditions.py`) must distinguish a corpus
CITATION from quoted link SYNTAX.

Motivating defect: `_WIKI_REF_RE` matched any `[[token]]`, while the function's own docstring
already promised `[[feat-slug]]` refs. A spec that merely DOCUMENTS wikilink syntax — writing
`[[wikilink]]`, `[[link]]`, `[[Target]]` — was therefore read as citing three nonexistent atoms,
and G-4 fail-closed on the spec's own prose, blocking authorization of a correct spec. This is
unavoidable for a corpus whose subject matter is a wiki.

The discriminator is the `feat-` PREFIX, not position. A code-span exemption was tried first and
REJECTED by measurement: real references are frequently backticked, including
`[[feat-foundry-leak-scan-ls-remote-sink]]` — a genuinely dangling ref the gate must keep catching
— so masking code spans would have converted a harmless false positive into a silent false
negative. These tests pin both directions so neither regresses.
"""
from __future__ import annotations

from conftest import load_module

pre = load_module("scripts/foundry_audit_preconditions.py", "foundry_audit_preconditions")


def _refs(text, own=None):
    return pre._extract_references(text, own or set())


# --- quoted link SYNTAX is not a citation ------------------------------------------------------

def test_documented_wikilink_syntax_is_not_a_reference():
    """The exact defect: a spec describing its product's link syntax must not trip G-4."""
    assert _refs("the system SHALL wire a `[[wikilink]]` from the atom to that page") == []


def test_bare_link_placeholder_is_not_a_reference():
    assert _refs("a bare `[[link]]` means relates_to") == []


def test_capitalized_placeholder_is_not_a_reference():
    assert _refs("- <relation> [[Target]]") == []


# --- genuine `feat-` citations are still caught, backticked or not ------------------------------

def test_feat_prefixed_reference_is_still_extracted():
    assert _refs("builds on [[feat-foundry-thing]] for the graph") == [
        ("feat-slug", "feat-foundry-thing")
    ]


def test_feat_prefixed_reference_inside_a_code_span_is_STILL_extracted():
    """The rejected code-span approach would have silenced this. It must stay caught — the live
    corpus contains exactly this shape, and one such reference is genuinely dangling."""
    assert _refs("`[[feat-foundry-leak-scan-ls-remote-sink]]` (delivered) established that") == [
        ("feat-slug", "feat-foundry-leak-scan-ls-remote-sink")
    ]


def test_syntax_and_citation_in_one_line_are_separated():
    assert _refs("cites [[feat-real-atom]] while describing `[[wikilink]]` syntax") == [
        ("feat-slug", "feat-real-atom")
    ]


# --- the other extractors are untouched --------------------------------------------------------

def test_ac_id_extraction_is_unaffected():
    assert _refs("per AC-OTHER-3, the system SHALL x") == [("ac-id", "AC-OTHER-3")]


def test_atom_citation_extraction_is_unaffected():
    assert ("path", "specs/features/x/feat-x.md") in _refs(
        "see [Atom: specs/features/x/feat-x.md] for the boundary"
    )


def test_own_bold_ac_id_definition_is_still_excluded():
    assert _refs("- **AC-MINE-1** (Requirement): the system SHALL x") == []
