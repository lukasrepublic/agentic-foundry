"""tests/test_runtime_gitignore.py — behavioral coverage for
feat-foundry-runtime-gitignore-leak-scan's fragment (AC-RGLS-1/2) and applier (AC-RGLS-3/4).

Every test drives the REAL shipped artifacts: `scripts/foundry-runtime.gitignore` (the fragment,
read directly and/or applied via `git check-ignore` in a throwaway scratch repo) and
`scripts/foundry-apply-runtime-gitignore.sh` (the applier, driven end-to-end via subprocess,
never re-implemented in Python) — plus `scripts/foundry-bootstrap.sh --selftest` for the
onboarding-wiring checkpoint (AC-RGLS-4).

Every git repo used is a throwaway `tmp_path` scratch repo; nothing here touches this
repository's own `.gitignore` or `.foundry/`.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT

APPLIER = os.path.join(REPO_ROOT, "scripts", "foundry-apply-runtime-gitignore.sh")
FRAGMENT = os.path.join(REPO_ROOT, "scripts", "foundry-runtime.gitignore")
BOOTSTRAP = os.path.join(REPO_ROOT, "scripts", "foundry-bootstrap.sh")

DESIGNED_TRACKED_SET = [
    ".foundry/README.md",
    ".foundry/build-provenance.yaml",
    ".foundry/stack-profile.lock",
]

BEGIN_TOKEN = "FOUNDRY-RUNTIME-GITIGNORE-BEGIN"
END_TOKEN = "FOUNDRY-RUNTIME-GITIGNORE-END"


def _git(args, cwd, check=False):
    return subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True, check=check)


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q"], path, check=True)


def _check_ignored(path, rel):
    return _git(["check-ignore", "-q", rel], path).returncode == 0


def run_applier(root):
    return subprocess.run([APPLIER, str(root)], capture_output=True, text=True)


def _block_span(text):
    """Return (lines, begin_idx, end_idx) for the SOLE managed block in `text`."""
    lines = text.splitlines()
    begin_idxs = [i for i, l in enumerate(lines) if BEGIN_TOKEN in l]
    end_idxs = [i for i, l in enumerate(lines) if END_TOKEN in l]
    assert len(begin_idxs) == 1, f"expected exactly one BEGIN sentinel, found {len(begin_idxs)}"
    assert len(end_idxs) == 1, f"expected exactly one END sentinel, found {len(end_idxs)}"
    return lines, begin_idxs[0], end_idxs[0]


def _assert_converged(gitignore_path):
    text = gitignore_path.read_text(encoding="utf-8")
    lines, begin_idx, end_idx = _block_span(text)
    interior = lines[begin_idx + 1 : end_idx]
    fragment_lines = Path(FRAGMENT).read_text(encoding="utf-8").splitlines()
    assert interior == fragment_lines, (interior, fragment_lines)
    return lines, begin_idx, end_idx


# --------------------------------------------------------------------------------------- AC-SSC-7b
def test_own_gitignore_block_matches_fragment():
    """This repository's OWN committed `.gitignore` managed-block interior is byte-identical to
    the shipped fragment `scripts/foundry-runtime.gitignore` — the assertion that makes the
    `.gitignore` edit OBLIGATORY (spec Clarification C3), not merely a claim carried in a comment.
    Reuses `_assert_converged`, exactly as every applied-in-a-scratch-repo test does, but points it
    at the real, committed `.gitignore` instead of a throwaway one."""
    _assert_converged(Path(REPO_ROOT) / ".gitignore")


# ---------------------------------------------------------------------------------------- AC-RGLS-2
def test_fragment_effective_in_scratch_repo(tmp_path):
    """The fragment's effect, proven in a throwaway repo (not just this repository's own
    .gitignore, which AC-RGLS-6/7 already cover): an unnamed runtime file/subdir is ignored, and
    all three designed-tracked members are not."""
    _init_repo(tmp_path)
    r = run_applier(tmp_path)
    assert r.returncode == 0, r.stderr

    assert _check_ignored(tmp_path, ".foundry/zz-future-runtime-artifact.jsonl")
    assert _check_ignored(tmp_path, ".foundry/zz-future-dir/nested.txt")
    for member in DESIGNED_TRACKED_SET:
        assert not _check_ignored(tmp_path, member), f"{member} unexpectedly ignored"


# ---------------------------------------------------------------------------------------- AC-RGLS-3
class TestApplierConvergence:
    @pytest.mark.parametrize(
        "label,content",
        [
            ("absent_file", None),
            ("no_block", "*.tmp\nkeep-me.log\n"),
            (
                "stale_block",
                "before-line\n"
                f"# {BEGIN_TOKEN} (stale)\n"
                "/.foundry/*\n"
                "!/.foundry/README.md\n"
                f"# {END_TOKEN} (stale)\n"
                "after-line\n",
            ),
        ],
    )
    def test_applier_convergence(self, tmp_path, label, content):
        _init_repo(tmp_path)
        gi = tmp_path / ".gitignore"
        if content is not None:
            gi.write_text(content, encoding="utf-8")
        r = run_applier(tmp_path)
        assert r.returncode == 0, r.stderr
        assert gi.exists()
        _assert_converged(gi)

    def test_applier_convergence_already_converged_is_idempotent(self, tmp_path):
        _init_repo(tmp_path)
        r1 = run_applier(tmp_path)
        assert r1.returncode == 0, r1.stderr
        first = (tmp_path / ".gitignore").read_bytes()
        r2 = run_applier(tmp_path)
        assert r2.returncode == 0, r2.stderr
        second = (tmp_path / ".gitignore").read_bytes()
        assert first == second

    def test_preserves_lines_outside_sentinels(self, tmp_path):
        _init_repo(tmp_path)
        content = "keep-first\n# a pre-existing comment\nkeep-second\n*.pyc\n"
        (tmp_path / ".gitignore").write_text(content, encoding="utf-8")
        r = run_applier(tmp_path)
        assert r.returncode == 0, r.stderr
        lines = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
        for l in ["keep-first", "# a pre-existing comment", "keep-second", "*.pyc"]:
            assert l in lines
        assert lines.index("keep-first") < lines.index("# a pre-existing comment") < lines.index("keep-second") < lines.index("*.pyc")

    def test_converges_two_blocks_to_one(self, tmp_path):
        _init_repo(tmp_path)
        content = (
            "a\n"
            f"# {BEGIN_TOKEN} (first)\n"
            "/.foundry/*\n"
            "!/.foundry/README.md\n"
            f"# {END_TOKEN} (first)\n"
            "b\n"
            f"# {BEGIN_TOKEN} (second)\n"
            "/.foundry/*\n"
            "!/.foundry/README.md\n"
            f"# {END_TOKEN} (second)\n"
            "c\n"
        )
        gi = tmp_path / ".gitignore"
        gi.write_text(content, encoding="utf-8")
        r = run_applier(tmp_path)
        assert r.returncode == 0, r.stderr
        text = gi.read_text(encoding="utf-8")
        assert text.count(BEGIN_TOKEN) == 1
        assert text.count(END_TOKEN) == 1
        _assert_converged(gi)
        lines = text.splitlines()
        for l in ["a", "b", "c"]:
            assert l in lines
        assert lines.index("a") < lines.index("b") < lines.index("c")

    def test_refuses_malformed_sentinels_begin_without_end(self, tmp_path):
        _init_repo(tmp_path)
        original = f"x\n# {BEGIN_TOKEN}\n/.foundry/*\n"
        gi = tmp_path / ".gitignore"
        gi.write_text(original, encoding="utf-8")
        r = run_applier(tmp_path)
        assert r.returncode != 0
        assert gi.read_text(encoding="utf-8") == original
        assert "sentinel" in r.stderr.lower()

    def test_refuses_malformed_sentinels_end_before_begin(self, tmp_path):
        _init_repo(tmp_path)
        original = f"x\n# {END_TOKEN}\ny\n"
        gi = tmp_path / ".gitignore"
        gi.write_text(original, encoding="utf-8")
        r = run_applier(tmp_path)
        assert r.returncode != 0
        assert gi.read_text(encoding="utf-8") == original
        assert "sentinel" in r.stderr.lower()

    def test_does_not_silently_unignore(self, tmp_path):
        """A pre-existing adopter rule ignoring a designed-tracked member (build-provenance.yaml,
        exactly this repository's own real departure) must survive as an explicit deviation line
        after END -- never be silently beaten by the block's own re-include (AC-RGLS-3d)."""
        _init_repo(tmp_path)
        gi = tmp_path / ".gitignore"
        gi.write_text(
            "# regenerable co-carry snapshot; not part of any contract's allowed_paths\n"
            ".foundry/build-provenance.yaml\n",
            encoding="utf-8",
        )
        r = run_applier(tmp_path)
        assert r.returncode == 0, r.stderr

        lines, begin_idx, end_idx = _assert_converged(gi)
        pat_idx = next(i for i, l in enumerate(lines) if l == ".foundry/build-provenance.yaml")
        assert pat_idx > end_idx, "deviation pattern must sit AFTER the END sentinel"
        assert lines[pat_idx - 1].startswith("#"), "deviation line must be immediately preceded by a comment"

        assert _check_ignored(tmp_path, ".foundry/build-provenance.yaml")
        for member in DESIGNED_TRACKED_SET:
            if member == ".foundry/build-provenance.yaml":
                continue
            assert not _check_ignored(tmp_path, member)

        # idempotent: a second run leaves the deviation untouched (no duplication/drift)
        before = gi.read_bytes()
        r2 = run_applier(tmp_path)
        assert r2.returncode == 0, r2.stderr
        assert gi.read_bytes() == before

    def test_validates_fragment_before_write(self, tmp_path):
        """A malformed fragment is a refusal, before any write -- exercised by staging a scratch
        copy of the applier alongside a deliberately-invalid fragment (the applier resolves the
        fragment relative to its OWN location, never --root)."""
        scratch = tmp_path / "scratch_scripts"
        scratch.mkdir()
        applier_copy = scratch / "foundry-apply-runtime-gitignore.sh"
        shutil.copy(APPLIER, applier_copy)
        applier_copy.chmod(applier_copy.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        (scratch / "foundry-runtime.gitignore").write_text(
            "/.foundry/*\n.foundry/a-second-name-based-line\n!/.foundry/README.md\n", encoding="utf-8"
        )

        repo = tmp_path / "repo"
        _init_repo(repo)
        original = "unrelated\n"
        gi = repo / ".gitignore"
        gi.write_text(original, encoding="utf-8")

        r = subprocess.run([str(applier_copy), str(repo)], capture_output=True, text=True)
        assert r.returncode != 0
        assert gi.read_text(encoding="utf-8") == original
        assert "fragment" in r.stderr.lower()

    def test_stubs_recorded_bad_sha_file_when_absent(self, tmp_path):
        _init_repo(tmp_path)
        r = run_applier(tmp_path)
        assert r.returncode == 0, r.stderr
        stub = tmp_path / ".foundry" / "leak-scan" / "known-bad-shas.txt"
        assert stub.exists()
        text = stub.read_text(encoding="utf-8")
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            pytest.fail(f"stub carries a non-comment/non-blank line: {line!r}")
        assert not re.search(r"^[0-9a-f]{40}$", text, re.MULTILINE)

    def test_stubs_recorded_bad_sha_file_leaves_existing_byte_unchanged(self, tmp_path):
        _init_repo(tmp_path)
        d = tmp_path / ".foundry" / "leak-scan"
        d.mkdir(parents=True)
        existing = b"deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n"
        p = d / "known-bad-shas.txt"
        p.write_bytes(existing)
        r = run_applier(tmp_path)
        assert r.returncode == 0, r.stderr
        assert p.read_bytes() == existing

    # ------------------------------------------------------------ symlinked-.gitignore refusal
    def test_refuses_symlinked_gitignore(self, tmp_path):
        """The behavior already exists (`load_target`'s explicit symlink check) but was previously
        untested: a `.gitignore` that is a symlink is refused, never followed and never replaced."""
        _init_repo(tmp_path)
        real_target = tmp_path / "real-gitignore-target.txt"
        real_target.write_text("unrelated\n", encoding="utf-8")
        gi = tmp_path / ".gitignore"
        gi.symlink_to(real_target)

        r = run_applier(tmp_path)
        assert r.returncode != 0
        assert "symlink" in r.stderr.lower()
        assert gi.is_symlink()
        assert real_target.read_text(encoding="utf-8") == "unrelated\n"

    # ------------------------------------------------------------ Risk #9: relocation blast radius
    def test_refuses_broad_wildcard_matching_a_designed_member_basename(self, tmp_path):
        """A non-anchored wildcard (an adopter's blanket `*.lock` ignore) that happens to also
        match a designed-tracked member's basename must be REFUSED, never silently relocated --
        relocating it would move its position and could defeat a later `!` re-include the adopter
        placed for an UNRELATED `*.lock` file (gitignore is last-match-wins). FAILS without the
        fix: the old glob-based `line_matches_designed_member` treated this as an ordinary
        relocatable conflict and moved it."""
        _init_repo(tmp_path)
        gi = tmp_path / ".gitignore"
        original = "*.lock\n"
        gi.write_text(original, encoding="utf-8")
        r = run_applier(tmp_path)
        assert r.returncode != 0
        assert gi.read_text(encoding="utf-8") == original
        assert "*.lock" in r.stderr

    def test_refuses_broad_literal_basename_matching_a_designed_member(self, tmp_path):
        """A literal (non-wildcard) but NON-ANCHORED pattern like bare `README.md` matches the
        basename at ANY depth (e.g. this repository's own top-level README.md too) -- also not
        specific to the designed member, also refused rather than relocated. FAILS without the
        fix: a literal basename match was treated as an ordinary relocatable conflict."""
        _init_repo(tmp_path)
        gi = tmp_path / ".gitignore"
        original = "README.md\n"
        gi.write_text(original, encoding="utf-8")
        r = run_applier(tmp_path)
        assert r.returncode != 0
        assert gi.read_text(encoding="utf-8") == original
        assert "README.md" in r.stderr

    def test_anchored_literal_designed_member_pattern_is_still_relocated(self, tmp_path):
        """Sanity companion: a pattern that IS specific to exactly one member path (path-anchored,
        no wildcard) is still safely relocated as a deviation -- the fix narrows what relocates,
        it does not disable relocation altogether."""
        _init_repo(tmp_path)
        gi = tmp_path / ".gitignore"
        gi.write_text("# a pre-existing, path-anchored rule\n.foundry/README.md\n", encoding="utf-8")
        r = run_applier(tmp_path)
        assert r.returncode == 0, r.stderr
        lines, begin_idx, end_idx = _assert_converged(gi)
        assert any(l == ".foundry/README.md" for l in lines[end_idx + 1 :])

    # ------------------------------------------------------------ Risk #10: directory-form exclusion
    def test_refuses_directory_form_foundry_exclusion_trailing_slash(self, tmp_path):
        """A bare directory-form ignore of `.foundry/` outside the block makes git structurally
        unable to re-include ANY child via the block's `!` lines, regardless of order -- the
        applier must refuse (naming the line), never report convergence over a block it knows is
        inert. FAILS without the fix: the old applier had no check for this shape at all and would
        report success while the freshly-written block's re-includes are dead on arrival."""
        _init_repo(tmp_path)
        gi = tmp_path / ".gitignore"
        original = ".foundry/\n"
        gi.write_text(original, encoding="utf-8")
        r = run_applier(tmp_path)
        assert r.returncode != 0
        assert gi.read_text(encoding="utf-8") == original
        assert ".foundry/" in r.stderr or ".foundry" in r.stderr

    def test_refuses_bare_directory_name_without_trailing_slash(self, tmp_path):
        _init_repo(tmp_path)
        gi = tmp_path / ".gitignore"
        original = ".foundry\n"
        gi.write_text(original, encoding="utf-8")
        r = run_applier(tmp_path)
        assert r.returncode != 0
        assert gi.read_text(encoding="utf-8") == original

    def test_refuses_root_anchored_bare_directory_form(self, tmp_path):
        _init_repo(tmp_path)
        gi = tmp_path / ".gitignore"
        original = "/.foundry/\n"
        gi.write_text(original, encoding="utf-8")
        r = run_applier(tmp_path)
        assert r.returncode != 0
        assert gi.read_text(encoding="utf-8") == original

    def test_root_anchored_star_glob_is_not_a_directory_exclusion(self, tmp_path):
        """Sanity companion: the block's OWN deny shape (`/.foundry/*`) is NOT a directory-form
        exclusion (it excludes each CHILD, not the parent directory entry) -- the applier itself
        must remain applyable."""
        _init_repo(tmp_path)
        r = run_applier(tmp_path)
        assert r.returncode == 0, r.stderr


# ---------------------------------------------------------------------------------------- AC-RGLS-4
def test_bootstrap_selftest_covers_applier():
    r = subprocess.run(["bash", BOOTSTRAP, "--selftest"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "BOOTSTRAP-SELFTEST-GREEN" in r.stdout
    assert "runtime-gitignore" in r.stdout.lower()
    assert "foundry-apply-runtime-gitignore.sh" in Path(BOOTSTRAP).read_text(encoding="utf-8")
