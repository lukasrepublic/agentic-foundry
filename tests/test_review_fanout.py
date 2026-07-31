"""tests/test_review_fanout.py — feat-foundry-review-fanout-hardening (AC-RFH-1..-14).

Drives the RFH-PURE block extracted VERBATIM out of `workflows/release-wave.js` (the sentinel
comments `// === RFH-PURE-BEGIN ... ===` / `// === RFH-PURE-END ===`) — see the spec's
Clarifications §1 (the native Workflow sandbox has no `fs`/`import()`/`node:` access, so the pure
consolidation + verdict-assembly logic cannot be factored into an importable sibling module; it
stays inline and this test extracts it instead).

Each test slices the extracted block out of the real shipped file, appends a small per-test JS
harness epilogue that calls the exported pure functions and prints their result as JSON, runs it
under `node`, and asserts on the COMPUTED result — never on the mere presence of a marker string.
This module is `pytest.mark.skipif`-gated on `node` being on PATH (Clarification §2: a Node-less
dev box still runs the rest of the suite green; CI already provisions Node 22 and already runs
`node --check` on this file).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import os

import pytest

from conftest import REPO_ROOT

RELEASE_WAVE_JS = os.path.join(REPO_ROOT, "workflows", "release-wave.js")
BEGIN_SENTINEL = "// === RFH-PURE-BEGIN"
END_SENTINEL = "// === RFH-PURE-END ==="

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node not on PATH — RFH-PURE block extraction requires the node runtime",
)


def _extract_pure_block():
    text = open(RELEASE_WAVE_JS, encoding="utf-8").read()
    assert text.count(BEGIN_SENTINEL) == 1, (
        f"expected exactly one {BEGIN_SENTINEL!r} sentinel in {RELEASE_WAVE_JS}, "
        f"found {text.count(BEGIN_SENTINEL)}"
    )
    assert text.count(END_SENTINEL) == 1, (
        f"expected exactly one {END_SENTINEL!r} sentinel in {RELEASE_WAVE_JS}, "
        f"found {text.count(END_SENTINEL)}"
    )
    begin = text.index(BEGIN_SENTINEL)
    end = text.index(END_SENTINEL) + len(END_SENTINEL)
    assert begin < end, "RFH-PURE-BEGIN must precede RFH-PURE-END"
    return text[begin:end]


def run_harness(epilogue: str):
    """Slice the RFH-PURE block out of the real shipped file, append `epilogue` (JS that calls the
    exported pure functions and does `console.log(JSON.stringify(<result>))`), run it under node,
    and return the parsed JSON."""
    block = _extract_pure_block()
    script = block + "\n\n" + epilogue + "\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".mjs", delete=False) as f:
        f.write(script)
        path = f.name
    try:
        proc = subprocess.run(
            [shutil.which("node"), path],
            capture_output=True, text=True, timeout=30,
        )
        assert proc.returncode == 0, (
            f"node exited {proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
        stdout = proc.stdout.strip()
        assert stdout, f"node produced no stdout (stderr:\n{proc.stderr})"
        return json.loads(stdout.splitlines()[-1])
    finally:
        os.unlink(path)


# =================================================== AC-RFH-1 =================================== #

def test_dedup_key_is_evidence_file_category_triple():
    """Scenario: two reviewers, one defect — whitespace-only variance in evidence, same file,
    same category => identical key. A divergent file OR a divergent category => a different key."""
    result = run_harness(
        """
        const f1 = { severity: "Risk", category: "correctness", location: "src/a.js:12",
                     evidence: "if (x = 1) {\\n  doThing()\\n}" }
        const f2 = { severity: "Block", category: "correctness", location: "src/a.js:12-14",
                     evidence: "if (x = 1) {   doThing() }" }
        const f3 = { severity: "Block", category: "security", location: "src/a.js:12-14",
                     evidence: "if (x = 1) {   doThing() }" }
        const f4 = { severity: "Block", category: "correctness", location: "src/b.js:12-14",
                     evidence: "if (x = 1) {   doThing() }" }
        console.log(JSON.stringify({
          key1: dedupKey(f1), key2: dedupKey(f2), key3: dedupKey(f3), key4: dedupKey(f4),
        }))
        """
    )
    assert result["key1"] == result["key2"], "whitespace-only variance must not change the key"
    assert result["key1"] != result["key3"], "a divergent category must never merge"
    assert result["key1"] != result["key4"], "a divergent file path must never merge"
    parsed = json.loads(result["key1"])
    assert parsed == ["if (x = 1) { doThing() }", "src/a.js", "correctness"]


def test_identical_code_two_files_never_merges():
    """Scenario: byte-identical evidence + category, two files => two survivors."""
    result = run_harness(
        """
        const findings = [
          { severity: "Risk", category: "hygiene", location: "src/a.js:9", evidence: "catch (e) {}" },
          { severity: "Risk", category: "hygiene", location: "src/b.js:41", evidence: "catch (e) {}" },
        ]
        console.log(JSON.stringify({ n: consolidateFindings(findings).length }))
        """
    )
    assert result["n"] == 2


def test_same_text_two_categories_never_merges():
    result = run_harness(
        """
        const findings = [
          { severity: "Risk", category: "security", location: "src/a.js:9", evidence: "x()" },
          { severity: "Risk", category: "performance", location: "src/a.js:9", evidence: "x()" },
        ]
        console.log(JSON.stringify({ n: consolidateFindings(findings).length }))
        """
    )
    assert result["n"] == 2


# =================================================== AC-RFH-2 =================================== #

def test_merged_finding_severity_rationale_locations_sources():
    """Scenario: two reviewers, one defect, one verifier dispatch."""
    result = run_harness(
        """
        const prReviewer = { severity: "Risk", category: "correctness", location: "src/a.js:12",
                              evidence: "if (x = 1) {\\n  doThing()\\n}",
                              rationale: "pr-reviewer: assignment-in-condition",
                              source: "pr-reviewer" }
        const securityReviewer = { severity: "Block", category: "correctness", location: "src/a.js:12-14",
                                    evidence: "if (x = 1) {   doThing() }",
                                    rationale: "security-reviewer: unauthenticated branch",
                                    source: "security-reviewer" }
        const consolidated = consolidateFindings([prReviewer, securityReviewer])
        console.log(JSON.stringify({ consolidated }))
        """
    )
    consolidated = result["consolidated"]
    assert len(consolidated) == 1
    finding = consolidated[0]
    assert finding["severity"] == "Block"
    assert finding["rationale"] == "security-reviewer: unauthenticated branch"
    assert finding["locations"] == ["src/a.js:12", "src/a.js:12-14"]
    assert finding["sources"] == [
        {"source": "pr-reviewer", "rationale": "pr-reviewer: assignment-in-condition"},
        {"source": "security-reviewer", "rationale": "security-reviewer: unauthenticated branch"},
    ]


# =================================================== AC-RFH-3 =================================== #

def test_unkeyable_findings_never_merge():
    """Scenario: an unkeyable finding merges with nothing — four distinct findings survive."""
    result = run_harness(
        """
        const findings = [
          { severity: "Risk", category: "x", location: "src/a.js:1", evidence: "one thing" },
          { severity: "Risk", category: "x", location: "src/a.js:1", evidence: "another thing" },
          { severity: "Risk", category: "x", location: "src/a.js:1" },
          { severity: "Risk", location: "src/a.js:1", evidence: "yet another" },
        ]
        console.log(JSON.stringify({ consolidated: consolidateFindings(findings) }))
        """
    )
    consolidated = result["consolidated"]
    assert len(consolidated) == 4
    for f in consolidated:
        assert "sources" in f and isinstance(f["sources"], list)


# =================================================== AC-RFH-4 =================================== #

def test_one_dispatch_per_consolidated_blocking():
    result = run_harness(
        """
        const findings = [
          { severity: "Block", category: "c", location: "src/a.js:1", evidence: "dupe", source: "r1" },
          { severity: "Block", category: "c", location: "src/a.js:1", evidence: "dupe", source: "r2" },
          { severity: "Block", category: "c", location: "src/b.js:1", evidence: "dupe", source: "r1" },
          { severity: "Nit", category: "c", location: "src/c.js:1", evidence: "cosmetic" },
        ]
        const consolidated = consolidateFindings(findings)
        const dispatchCount = consolidated.filter((f) => f.severity === "Block").length
        console.log(JSON.stringify({ dispatchCount, total: consolidated.length }))
        """
    )
    assert result["dispatchCount"] == 2
    assert result["total"] == 3

    clean = run_harness(
        """
        const consolidated = consolidateFindings([{ severity: "Nit", category: "c", location: "a:1" }])
        console.log(JSON.stringify({
          dispatchCount: consolidated.filter((f) => f.severity === "Block").length,
        }))
        """
    )
    assert clean["dispatchCount"] == 0


# =================================================== AC-RFH-5 / AC-RFH-14 ======================= #

def test_refutation_is_the_only_demotion():
    """Scenario: a refutation stays inside its own key."""
    result = run_harness(
        """
        const a = { severity: "Block", category: "c", location: "src/a.js:1", evidence: "shared" }
        const b = { severity: "Block", category: "c", location: "src/b.js:1", evidence: "shared" }
        const consolidated = consolidateFindings([a, b])
        const [ca, cb] = consolidated
        const verifications = new Map([
          [ca.dedupKey, { verified: false, reason: "guarded upstream" }],
          [cb.dedupKey, { verified: true }],
        ])
        const review = assembleReviewResult(consolidated, verifications)
        console.log(JSON.stringify({ review, keyA: ca.dedupKey, keyB: cb.dedupKey }))
        """
    )
    review = result["review"]
    nonblocking_locs = [loc for f in review["nonblocking"] for loc in f["locations"]]
    blocking_locs = [loc for f in review["blocking"] for loc in f["locations"]]
    assert "src/a.js:1" in nonblocking_locs
    assert "src/b.js:1" in blocking_locs
    assert "src/a.js:1" not in blocking_locs
    assert "src/b.js:1" not in nonblocking_locs
    refuted = [f for f in review["nonblocking"] if "src/a.js:1" in f["locations"]][0]
    assert refuted["refutation_reason"] == "guarded upstream"


def test_verdict_applies_only_to_its_own_dedup_key():
    """AC-RFH-14: a verdict dispatched for one key must never leak to another, even a sibling key
    with no verification entry at all (a missing entry must fail closed for ITS OWN key only)."""
    result = run_harness(
        """
        const a = { severity: "Block", category: "c", location: "src/a.js:1", evidence: "shared" }
        const b = { severity: "Block", category: "c", location: "src/b.js:1", evidence: "shared" }
        const consolidated = consolidateFindings([a, b])
        const [ca, cb] = consolidated
        // Only A has a verification entry (a refutation); B's key is absent from the map entirely.
        const verifications = new Map([[ca.dedupKey, { verified: false, reason: "guarded upstream" }]])
        const review = assembleReviewResult(consolidated, verifications)
        console.log(JSON.stringify({ review }))
        """
    )
    review = result["review"]
    assert len(review["nonblocking"]) == 1
    assert review["nonblocking"][0]["locations"] == ["src/a.js:1"]
    assert len(review["blocking"]) == 1
    blocking = review["blocking"][0]
    assert blocking["locations"] == ["src/b.js:1"]
    assert blocking["tag"] == "could-not-be-verified"
    assert review["verdict"] == "FAIL"


# =================================================== AC-RFH-6 / AC-RFH-7 / AC-RFH-8 ============== #

def test_unverifiable_stays_blocking_tagged():
    """Scenario: the verifier dies (a rejected promise, represented here as a caught-error marker
    object with no `verified` key — the shape the workflow's `.catch()` produces)."""
    result = run_harness(
        """
        const finding = { severity: "Block", category: "c", location: "src/a.js:1", evidence: "e" }
        const consolidated = consolidateFindings([finding])
        const verifications = new Map([[consolidated[0].dedupKey, { __error: "transport error", reason: "transport error" }]])
        const review = assembleReviewResult(consolidated, verifications)
        console.log(JSON.stringify({ review }))
        """
    )
    review = result["review"]
    assert len(review["blocking"]) == 1
    assert review["blocking"][0]["tag"] == "could-not-be-verified"
    assert review["blocking"][0]["reason"] == "transport error"


def test_could_not_be_verified_in_blocking_and_incomplete():
    result = run_harness(
        """
        const finding = { severity: "Block", category: "c", location: "src/a.js:1", evidence: "e" }
        const consolidated = consolidateFindings([finding])
        // No entry at all in the map — the "verifier never returned" shape.
        const review = assembleReviewResult(consolidated, new Map())
        console.log(JSON.stringify({ review }))
        """
    )
    review = result["review"]
    assert len(review["blocking"]) == 1
    assert len(review["incomplete"]) == 1
    assert review["blocking"][0]["locations"] == review["incomplete"][0]["locations"]
    assert review["blocking"][0]["tag"] == "could-not-be-verified"
    assert review["verdict"] == "FAIL"


def test_incomplete_forces_fail_verdict():
    result = run_harness(
        """
        const blockFinding = { severity: "Block", category: "c", location: "src/a.js:1", evidence: "e" }
        const dirtyConsolidated = consolidateFindings([blockFinding])
        const dirty = assembleReviewResult(dirtyConsolidated, new Map())
        const cleanConsolidated = consolidateFindings([
          { severity: "Nit", category: "c", location: "src/b.js:1", evidence: "cosmetic" },
        ])
        const clean = assembleReviewResult(cleanConsolidated, new Map())
        console.log(JSON.stringify({
          dirtyVerdict: dirty.verdict, dirtyIncompleteLen: dirty.incomplete.length,
          cleanVerdict: clean.verdict, cleanIncompleteLen: clean.incomplete.length,
        }))
        """
    )
    assert result["dirtyIncompleteLen"] == 1
    assert result["dirtyVerdict"] == "FAIL"
    assert result["cleanIncompleteLen"] == 0
    assert result["cleanVerdict"] == "PASS"


# =================================================== AC-RFH-9 =================================== #

def test_findings_conserved_across_blocking_and_nonblocking():
    result = run_harness(
        """
        const findings = [
          { severity: "Block", category: "c", location: "src/a.js:1", evidence: "confirmed" },
          { severity: "Block", category: "c", location: "src/b.js:1", evidence: "refuted" },
          { severity: "Block", category: "c", location: "src/c.js:1", evidence: "unverifiable" },
          { severity: "Risk", category: "c", location: "src/d.js:1", evidence: "a risk" },
          { severity: "Nit", category: "c", location: "src/e.js:1" },
        ]
        const consolidated = consolidateFindings(findings)
        const [confirmed, refuted, unverifiable] = consolidated
        const verifications = new Map([
          [confirmed.dedupKey, { verified: true }],
          [refuted.dedupKey, { verified: false, reason: "not real" }],
        ])
        const review = assembleReviewResult(consolidated, verifications)
        console.log(JSON.stringify({
          consolidatedCount: consolidated.length,
          unionCount: review.blocking.length + review.nonblocking.length,
        }))
        """
    )
    assert result["consolidatedCount"] == 5
    assert result["unionCount"] == 5


# =================================================== AC-RFH-10 ================================== #

def test_return_contract_keys_present():
    empty = run_harness("console.log(JSON.stringify({ r: assembleReviewResult([], new Map()) }))")["r"]
    assert set(empty.keys()) >= {"verdict", "incomplete", "blocking", "nonblocking"}
    assert empty["incomplete"] == [] and empty["blocking"] == [] and empty["nonblocking"] == []
    assert empty["verdict"] == "PASS"

    nonempty = run_harness(
        """
        const consolidated = consolidateFindings([
          { severity: "Block", category: "c", location: "src/a.js:1", evidence: "e" },
        ])
        console.log(JSON.stringify({ r: assembleReviewResult(consolidated, new Map()) }))
        """
    )["r"]
    for key in ("verdict", "incomplete", "blocking", "nonblocking"):
        assert key in nonempty
    assert isinstance(nonempty["incomplete"], list)
    assert isinstance(nonempty["blocking"], list)
    assert isinstance(nonempty["nonblocking"], list)
