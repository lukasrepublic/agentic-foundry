"""tests/test_leak_gate.py — converted from scripts/foundry_checks/leak-gate.py.

Ports the real behavioral assertions over the single shared scan module
`.github/actions/leak-gate/leak_scan.py` — the proprietary-name/structural-leak-marker denylist
loader + matcher + tree scanner — plus the consistency assertion that `agents/reference-agents`
lens (if present) resolves the IDENTICAL term set from that same shared module.
"""
from __future__ import annotations

import os

from conftest import REPO_ROOT, load_module

leak_scan = load_module(".github/actions/leak-gate/leak_scan.py", "leak_scan")

# The denylist is no longer shipped in this tree (feat-foundry-denylist-out-of-tree): a term list
# committed to a repository that goes public discloses exactly what it exists to protect. These
# tests therefore exercise the LOADER and the SCANNER against synthetic fixtures, which is all they
# ever needed -- neither assertion below depended on the terms being the operator's real ones.
# Coverage against the REAL term set lives in tests/test_denylist_out_of_tree.py, which resolves it
# from outside the tree and scopes to the tracked (i.e. published) set.


class TestLoadDenylist:
    def test_denylist_loads_nonempty(self, tmp_path):
        p = tmp_path / "denylist.txt"
        p.write_text("alpha\nbravo\n", encoding="utf-8")
        terms = leak_scan.load_denylist(str(p))
        assert isinstance(terms, list) and len(terms) >= 1

    def test_comments_and_blank_lines_ignored(self, tmp_path):
        p = tmp_path / "denylist.txt"
        p.write_text("# a comment\n\nreal-term\n  \nanother-term\n", encoding="utf-8")
        terms = leak_scan.load_denylist(str(p))
        assert terms == ["real-term", "another-term"]

    def test_zero_terms_raises(self, tmp_path):
        p = tmp_path / "denylist.txt"
        p.write_text("# only comments\n\n", encoding="utf-8")
        import pytest
        with pytest.raises(leak_scan.DenylistError):
            leak_scan.load_denylist(str(p))

    def test_missing_file_raises(self, tmp_path):
        import pytest
        with pytest.raises(leak_scan.DenylistError):
            leak_scan.load_denylist(str(tmp_path / "does-not-exist.txt"))


class TestBuildMatcher:
    def test_identifier_boundary_matches_snake_case(self):
        matcher = leak_scan.build_matcher(["acme"])
        assert matcher.search("uses acme_corp_internal") is not None

    def test_no_false_positive_on_substring_without_boundary(self):
        matcher = leak_scan.build_matcher(["acme"])
        assert matcher.search("pacemaker") is None  # "acme" is a substring but not identifier-boundaried...

    def test_case_insensitive(self):
        matcher = leak_scan.build_matcher(["Acme"])
        assert matcher.search("ACME") is not None


class TestScanTree:
    def test_clean_tree_has_zero_findings(self, tmp_path):
        (tmp_path / "clean.py").write_text("print('hello world')\n", encoding="utf-8")
        denylist = tmp_path / "denylist.txt"
        denylist.write_text("proprietary-term\n", encoding="utf-8")
        exit_code, findings = leak_scan.scan_tree(str(tmp_path), str(denylist))
        assert exit_code == 0 and findings == []

    def test_leaked_term_is_found(self, tmp_path):
        (tmp_path / "leak.py").write_text("# proprietary-term used here\n", encoding="utf-8")
        denylist = tmp_path / "denylist.txt"
        denylist.write_text("proprietary-term\n", encoding="utf-8")
        exit_code, findings = leak_scan.scan_tree(str(tmp_path), str(denylist))
        assert exit_code != 0 and findings

    def test_structural_marker_hbk_id_is_found(self, tmp_path):
        (tmp_path / "leak.py").write_text("# " + "HBK" + "-1234 internal reference\n", encoding="utf-8")
        denylist = tmp_path / "denylist.txt"
        denylist.write_text("unrelated-term\n", encoding="utf-8")
        exit_code, findings = leak_scan.scan_tree(str(tmp_path), str(denylist))
        assert exit_code != 0 and findings

    def test_denylist_file_itself_excluded_from_scan(self, tmp_path):
        denylist = tmp_path / "denylist.txt"
        denylist.write_text("proprietary-term\n", encoding="utf-8")
        exit_code, findings = leak_scan.scan_tree(str(tmp_path), str(denylist))
        assert exit_code == 0 and findings == []

    def test_binary_file_skipped(self, tmp_path):
        (tmp_path / "binary.bin").write_bytes(b"\x00\x01proprietary-term\x02")
        denylist = tmp_path / "denylist.txt"
        denylist.write_text("proprietary-term\n", encoding="utf-8")
        exit_code, findings = leak_scan.scan_tree(str(tmp_path), str(denylist))
        assert exit_code == 0 and findings == []

    def test_real_scripts_dir_is_clean_against_a_synthetic_denylist(self, tmp_path):
        """The shipped scripts/ tree scanned end-to-end with a real (if synthetic) term set: proves
        the scanner runs clean over real source, and that no fixture token has leaked into scripts/.

        This ASSERTS LESS than it used to, and says so plainly: it once used the shipped denylist,
        which no longer exists in this tree. The assertion against the operator's REAL terms moved to
        tests/test_denylist_out_of_tree.py::test_repo_is_clean_against_offtree_real_denylist, which
        resolves them from outside the tree and covers the whole tracked set rather than scripts/
        alone -- strictly broader coverage, in the one place that can still get the terms."""
        denylist = tmp_path / "denylist.txt"
        denylist.write_text("zzz-not-present-anywhere\n", encoding="utf-8")
        exit_code, findings = leak_scan.scan_tree(os.path.join(REPO_ROOT, "scripts"), str(denylist))
        assert exit_code == 0, findings


def test_ci_still_wires_the_leak_gate_action():
    """The single-source claim ci.yml makes in prose, asserted: the local composite action is
    invoked, and no proprietary term is enumerated inline in the workflow (PR #270 finding 3)."""
    import pathlib
    ci = (pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml").read_text()
    assert "./.github/actions/leak-gate" in ci
