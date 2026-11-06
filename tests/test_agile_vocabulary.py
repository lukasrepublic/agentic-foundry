"""The 2.0 agile vocabulary guard (dl-17, amendment (d) of the SDLC 2.0 review).

A retired 2.0 term must not appear in a user-facing 2.0 surface. The failure names the file and line.

Guarded: docs/**/*.md, skills/**/*.md, agents/*.md, README.md, the 2.0.0 section of CHANGELOG.md, and the text of
scripts/foundry_deck_report.py and scripts/foundry_message_log.py.

Allowed (by path or by marker, never by weakening a pattern):
  - ALLOW_PATHS: files whose job is to name the old term (the alias parsers' docs, decision records, historical records).
  - CHANGELOG.md below the 2.0.0 section (1.x history).
  - A block between `<!-- vocab-allow:start -->` and `<!-- vocab-allow:end -->` (the glossary map and the rename entry).
  - The 1.x opt-in spec lane keeps "atomic spec"; "atom" is not a retired term here.
  - Frozen test fixtures live under tests/ and are not a guarded surface.
Identifiers joined by a hyphen, underscore, dot or slash (`foundry-wave-plan.py`, `programme-state`) are file or key names
and are not prose; the patterns refuse a match inside such a token.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# A retired term as prose: not glued to another identifier character.
_L = r"(?<![\w/.\-`])"
_R = r"(?![\w\-])"
RETIRED = {
    "programme": re.compile(_L + r"[Pp]rogrammes?" + _R),
    "execution plan": re.compile(_L + r"[Ee]xecution[ -]plans?" + _R),
    "close-out": re.compile(r"(?<![\w/.`])[Cc]lose-?out" + _R),
    "concurrency limit": re.compile(_L + r"[Cc]oncurrency limits?" + _R),
    "Done means": re.compile(r"\bDone[ -]means\b|\bdone means\b"),   # `--done-means` and `done-means-untrusted` are code names
    "Paper allowed": re.compile(r"Paper allowed", re.I),
    "## Learned": re.compile(r"^\s*#+ Learned\b|`## Learned`"),
    "wave as the unit of work": re.compile(_L + r"[Ww]aves?" + _R),
}

# Whole files that may name the old terms. Keep this list short; each entry needs a reason.
ALLOW_PATHS = {
    "docs/permission-floor.json": "names the script file foundry_wave_plan.py",
}
ALLOW_PREFIXES = (
    "docs/decisions/",   # dated decision records: history, not guidance
)

START, END = "<!-- vocab-allow:start -->", "<!-- vocab-allow:end -->"


def _guarded_files() -> list[Path]:
    files: list[Path] = []
    for pat in ("docs/**/*.md", "skills/**/*.md", "agents/*.md"):
        files += sorted(ROOT.glob(pat))
    files += [ROOT / "README.md", ROOT / "CHANGELOG.md",
              ROOT / "scripts/foundry_deck_report.py", ROOT / "scripts/foundry_message_log.py"]
    out = []
    for f in files:
        rel = f.relative_to(ROOT).as_posix()
        if not f.is_file() or rel in ALLOW_PATHS or rel.startswith(ALLOW_PREFIXES):
            continue
        out.append(f)
    return out


def scan_text(rel: str, text: str) -> list[str]:
    """Return 'file:line: term: text' for each retired term outside the allowed regions."""
    hits: list[str] = []
    allowed = False
    in_scope = rel != "CHANGELOG.md"   # the changelog is guarded from the v2.0.0 heading to the next release heading
    for n, line in enumerate(text.splitlines(), 1):
        if rel == "CHANGELOG.md":
            if line.startswith("## v2.0.0"):
                in_scope = True
            elif line.startswith("## ") and in_scope:
                in_scope = False
        if START in line:
            allowed = True
            continue
        if END in line:
            allowed = False
            continue
        if allowed or not in_scope:
            continue
        for term, rx in RETIRED.items():
            if rx.search(line):
                hits.append(f"{rel}:{n}: retired term {term!r}: {line.strip()[:120]}")
    return hits


def scan() -> list[str]:
    hits: list[str] = []
    for f in _guarded_files():
        hits += scan_text(f.relative_to(ROOT).as_posix(), f.read_text(encoding="utf-8"))
    return hits


def test_no_retired_term_in_a_guarded_surface():
    hits = scan()
    assert not hits, "retired 2.0 terms in guarded surfaces:\n" + "\n".join(hits)


def test_guard_names_file_and_line_for_each_retired_term():
    """Meta-test: every pattern fires, and the failure names file and line (a guard that never fires proves nothing)."""
    samples = {
        "programme": "The programme is done.",
        "execution plan": "Read the execution plan.",
        "close-out": "Run close-out now.",
        "concurrency limit": "Set the concurrency limit.",
        "Done means": "Write a Done means command.",
        "Paper allowed": "List it under Paper allowed.",
        "## Learned": "## Learned",
        "wave as the unit of work": "Build wave 2 next.",
    }
    assert set(samples) == set(RETIRED)
    for term, line in samples.items():
        hits = scan_text("docs/x.md", "ok line\n" + line + "\n")
        assert len(hits) == 1 and hits[0].startswith("docs/x.md:2:") and repr(term) in hits[0], (term, hits)


def test_identifiers_and_allowed_regions_are_not_flagged():
    clean = [
        "`foundry-wave-plan.py` and `workflows/release-wave.js` and `programme-state`",
        "the old heading is parsed as an alias",
        "a Story, an Increment, a Release plan, the WIP limit, the Epic review",
    ]
    assert scan_text("docs/x.md", "\n".join(clean)) == []
    fenced = f"{START}\nprogramme wave Done means\n{END}\nprogramme\n"
    assert [h.split(":")[1] for h in scan_text("docs/x.md", fenced)] == ["4"]


def test_changelog_is_guarded_only_for_the_2_0_0_section():
    text = "# Changelog\nprogramme here\n## v2.0.0 - unreleased\nclose-out here\n## v1.18.5\nclose-out old\n"
    hits = scan_text("CHANGELOG.md", text)
    assert [h.split(":")[1] for h in hits] == ["4"]
