"""amendments-backfill (ER #214) — the two invariants only Python can pin.

AC-AMB-3: the JS module's `classifySpec` and scripts/foundry-amend.py's `amendments_section_ok`
must agree on every spec shape (heading inside a fence, heading before the marker, two normative
regions, CRLF, no marker at all). AC-AMB-4: appending the section moves no `spec_sha256` — the
real `scripts/foundry_contract.spec_sha256` hashes only the normative region, so a frozen
`authorized:` block stays valid after the backfill.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPTS = os.path.join(REPO, "scripts")
CLI_SRC = os.path.join(REPO, "cli", "src", "amendmentsBackfill.mjs")
sys.path.insert(0, SCRIPTS)

import foundry_contract as fc  # noqa: E402


def _load_amend():
    spec = importlib.util.spec_from_file_location("foundry_amend", os.path.join(SCRIPTS, "foundry-amend.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


AMEND = _load_amend()

BLOCK = "## Amendments\n\n| date | what changed | why reality required it | auth_seq |\n|---|---|---|---|\n"
NORMATIVE = "# feat-x\n\n<!-- normative -->\n- **AC-X-1**: it works.\n<!-- /normative -->\n"

SHAPES = {
    "absent": NORMATIVE,
    "present": NORMATIVE + "\n" + BLOCK,
    "heading-in-fence": NORMATIVE + "\n```md\n## Amendments\n```\n",
    "heading-in-fence-then-real": NORMATIVE + "\n```md\n## Amendments\n```\n\n" + BLOCK,
    "heading-before-marker": "## Amendments\n\n" + NORMATIVE,
    "two-regions-heading-between": "<!-- normative -->\na\n<!-- /normative -->\n\n## Amendments\n\n<!-- normative -->\nb\n<!-- /normative -->\n",
    "crlf-absent": NORMATIVE.replace("\n", "\r\n"),
    "crlf-present": (NORMATIVE + "\n" + BLOCK).replace("\n", "\r\n"),
    "no-marker-with-heading": "# spec\n\n## Amendments\n",
    "no-marker-no-heading": "# spec\n",
    # v1.18.1: normative prose that NAMES the heading must not hide the real section after the marker
    # (the updater appended a duplicate on every run), nor stand in for a missing one.
    "inline-mention-in-normative-then-real": NORMATIVE.replace("it works.", "append to the `## Amendments` table.") + "\n" + BLOCK,
    "inline-mention-in-normative-only": NORMATIVE.replace("it works.", "append to the `## Amendments` table."),
    "inline-mention-after-marker-only": NORMATIVE + "\nSee the `## Amendments` table.\n",
}


def _node(js: str, stdin: str = "", **env) -> str:
    """Run an ES-module snippet under node; the module under test is imported dynamically from
    AMB_MODULE (a static `import ... from <expr>` is a syntax error), inputs travel by env."""
    full_env = dict(os.environ, AMB_MODULE="file://" + CLI_SRC, **env)
    p = subprocess.run(["node", "--input-type=module", "-e", js], input=stdin, text=True,
                       capture_output=True, env=full_env)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def _js_classify(text: str) -> str:
    js = (
        "const m = await import(process.env.AMB_MODULE);"
        "let s=''; process.stdin.setEncoding('utf8');"
        "process.stdin.on('data', d => s += d);"
        "process.stdin.on('end', () => process.stdout.write(m.classifySpec(s)));"
    )
    return _node(js, stdin=text)


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_js_classifier_agrees_with_foundry_amend(name):
    text = SHAPES[name]
    py_ok = AMEND.amendments_section_ok(text)
    js = _js_classify(text)
    # The Python returns a bool; the JS adds `no-marker` only to say it will not write. Both must
    # agree on the one question amend asks: is the section there for amend's purposes?
    js_ok = js == "present" or (js == "no-marker" and "## Amendments" in text)
    assert js_ok == py_ok, f"{name}: python={py_ok} js={js}"


def test_backfill_moves_no_spec_sha256(tmp_path):
    spec = tmp_path / "specs" / "features" / "p" / "d" / "c" / "feat-x.md"
    spec.parent.mkdir(parents=True)
    spec.write_text(NORMATIVE, encoding="utf-8")
    before = fc.spec_sha256(str(spec))
    js = (
        "const m = await import(process.env.AMB_MODULE);"
        "const p = m.planAmendmentsBackfill({ physicalRoot: process.env.AMB_ROOT });"
        "process.stdout.write(String(m.applyAmendmentsBackfill(p)));"
    )
    assert _node(js, AMB_ROOT=str(tmp_path)) == "1"
    text = spec.read_text(encoding="utf-8")
    assert text.endswith("\n" + BLOCK)
    assert AMEND.amendments_section_ok(text)
    assert fc.spec_sha256(str(spec)) == before


def test_backfilled_section_is_appendable_by_amend(tmp_path):
    """The block the backfill writes has the separator row `append_amendment_row` looks for."""
    spec = tmp_path / "feat-x.md"
    spec.write_text(NORMATIVE + "\n" + BLOCK, encoding="utf-8")
    AMEND.append_amendment_row(str(spec), "2026-09-23", "what", "why", 2)
    assert "| 2026-09-23 | what | why | 2 |" in spec.read_text(encoding="utf-8")


def test_backfill_covers_any_basename_under_specs(tmp_path):
    """ER #223: an adopter's delivery atoms are `spec-*.md`; the walker has no filename rule, like
    foundry-amend.py itself. A README without a normative region is skipped, never written."""
    d = tmp_path / "specs" / "delivery"
    d.mkdir(parents=True)
    (d / "spec-atom.md").write_text(NORMATIVE, encoding="utf-8")
    (tmp_path / "specs" / "README.md").write_text("# index\n", encoding="utf-8")
    js = (
        "const m = await import(process.env.AMB_MODULE);"
        "const p = m.planAmendmentsBackfill({ physicalRoot: process.env.AMB_ROOT });"
        "process.stdout.write(m.applyAmendmentsBackfill(p) + ' ' + p.total + ' ' + p.skipped);"
    )
    assert _node(js, AMB_ROOT=str(tmp_path)) == "1 2 1"
    assert AMEND.amendments_section_ok((d / "spec-atom.md").read_text(encoding="utf-8"))
    assert (tmp_path / "specs" / "README.md").read_text(encoding="utf-8") == "# index\n"


def test_inline_mention_is_never_the_section_and_rerun_is_idempotent(tmp_path):
    """v1.18.1: a spec whose normative region mentions `## Amendments` and already has the section is
    left alone by the backfill (no duplicate on re-run), and `append_amendment_row` writes into the
    real ledger after the marker, never into a table inside the normative region."""
    spec = tmp_path / "specs" / "features" / "p" / "d" / "c" / "feat-x.md"
    spec.parent.mkdir(parents=True)
    body = ("# feat-x\n\n<!-- normative -->\n- **AC-X-1**: append a row to the `## Amendments` table.\n\n"
            "| a | b |\n|---|---|\n| 1 | 2 |\n<!-- /normative -->\n\n" + BLOCK)
    spec.write_text(body, encoding="utf-8")
    js = (
        "const m = await import(process.env.AMB_MODULE);"
        "const p = m.planAmendmentsBackfill({ physicalRoot: process.env.AMB_ROOT });"
        "process.stdout.write(String(m.applyAmendmentsBackfill(p)));"
    )
    assert _node(js, AMB_ROOT=str(tmp_path)) == "0"
    assert spec.read_text(encoding="utf-8") == body
    before = fc.spec_sha256(str(spec))
    AMEND.append_amendment_row(str(spec), "2026-09-26", "what", "why", 2)
    after = spec.read_text(encoding="utf-8")
    assert after.count("## Amendments\n") == 1
    assert "| 1 | 2 |\n<!-- /normative -->" in after
    assert after.rstrip().endswith("| 2026-09-26 | what | why | 2 |")
    assert fc.spec_sha256(str(spec)) == before
