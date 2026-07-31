"""The ONE shared proprietary-name / structural-leak-marker scan module
(feat-foundry-proprietary-name-leak-gate, AC-PNLG-1/2/6).

This is the single implementation invoked IDENTICALLY by both live consumers:
  (i)   the `leak-gate` composite action (`action.yml`), over a caller's checked-out tree;
  (ii)  `scripts/foundry-prepublication-leak-scan.py`, for its denylist-load + term-matching.

(Two further consumers under `scripts/foundry_checks/` were named here until
feat-foundry-denylist-out-of-tree; that directory was retired and they no longer exist. The list is
corrected rather than left stale because this docstring also documents the denylist-resolution
contract, which that atom changed.)

Using one module (one Python `re` engine) for both consumers makes "identical matcher /
identical resolved term-set / identical corpus" true BY CONSTRUCTION -- never two implementations
asserted to agree.

THE TERM LIST IS NOT SHIPPED IN THIS REPOSITORY (feat-foundry-denylist-out-of-tree). It is supplied
by the caller: from a repository secret in CI, or from an operator-local path resolved outside the
tree. `load_denylist` remains fail-closed -- missing, unreadable, or zero-term RAISES -- so an absent
denylist REFUSES, never silently passes. The one deliberate exception is `markers_only=True`, an
EXPLICIT opt-in for the fork-PR case where secrets structurally cannot resolve; it is never inferred
from a denylist being absent, because that would turn a deleted secret into a silent downgrade.

A FINDING NEVER CARRIES THE MATCHED TERM. `format_name_term_finding` takes an index, not text, so no
code path can put a term into a hit string, stdout, or an exception -- a public Actions log would
otherwise republish the term the gate exists to protect. The operator maps index -> term using the
denylist they hold; a public reader cannot.

Pinned public API:
    load_denylist(path) -> list[str]
    build_matcher(terms) -> a single compiled regex (any-of, identifier-boundaried, case-insensitive,
                            one CAPTURING group per term so the fired branch is recoverable)
    match_term_index(m) -> 0-based term index for a match, or None
    format_name_term_finding(path, line_no, term_index) -> the ONE finding constructor
    scan_tree(root, denylist_path, markers_only=False) -> (exit_code, hits)

NOTE (self-clean under its own scan): only `denylist_path` (the single file, by `os.path.realpath`)
is excluded from the AC-PNLG-2 scan corpus -- this file, `leak_scan.py`, IS scanned like any other
tree file. So its own structural-marker patterns below are ASSEMBLED (never a contiguous literal
that its own scan would self-catch); `HBK-[0-9]` contains no `HBK-<digit>` literal so it is
self-clean as written.
"""
import os
import re


class DenylistError(Exception):
    """Fail-closed signal (AC-PNLG-1): raised by load_denylist() on a missing, unreadable, or
    zero-term denylist file. Never a silent empty set."""


# --- structural leak-markers (AC-PNLG-6) -- regex patterns, NOT names, so they do not live in
# denylist.txt. Both matched case-insensitively (non-regression vs the pre-refactor `grep -riI`).
_HBK_MARKER = re.compile(r"HBK-[0-9]", re.IGNORECASE)
# Assembled (two string literals joined at runtime) so this module's OWN source carries no
# contiguous bracket-memory-colon literal that its own AC-PNLG-2 tree-scan would self-catch.
_MEMORY_MARKER = re.compile(r"\[" + "memory:", re.IGNORECASE)

_NUL = b"\x00"
_BINARY_SNIFF_BYTES = 8192


def load_denylist(path):
    """Parse `path` by the AC-PNLG-1 pinned deterministic rule: one term per line, surrounding ASCII
    whitespace stripped, blank lines and full-line `#`-comment lines ignored, NO inline trailing
    comments (a term is the entire trimmed line). Fail-closed: raises DenylistError on a missing,
    unreadable, or zero-term file -- never returns an empty list."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        raise DenylistError(f"denylist unreadable at {path}: {e}") from e
    except UnicodeDecodeError as e:
        raise DenylistError(f"denylist undecodable at {path}: {e}") from e

    terms = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped[0] == "#":
            continue
        terms.append(stripped)
    if not terms:
        raise DenylistError(f"denylist at {path} parsed to zero terms (fail-closed)")
    return terms


def build_matcher(terms):
    """Return ONE combined matcher (a single compiled regex, order-independent) over ALL `terms`: each
    term matched as a literal (`re.escape`), identifier-boundaried
    `(?<![A-Za-z0-9])<term>(?![A-Za-z0-9])`, case-insensitive -- ANY term matching is a hit. Supersedes
    plain `\\b...\\b` (which treats `_` as a word char and would miss snake_case identifier leaks).

    Each alternative wraps its term in a CAPTURING group so `match_term_index()` can recover WHICH
    term fired without the caller ever handling the matched text (AC-DOT-2/-3). Match semantics are
    otherwise byte-for-byte unchanged: the groups are positional only and alter nothing about what
    matches."""
    # R2 (PR #308 review): Python re is leftmost-FIRST, not leftmost-longest, and the
    # identifier boundary admits "-"/"_"/"." -- so with terms ["acme", "acme-corp"] in list
    # order, "acme-corp" matches only "acme" and span-exact PATH redaction would publish the
    # remainder "-corp", a fragment of a proprietary term. Alternation runs LONGEST-FIRST;
    # the map below preserves the ORIGINAL denylist index so term[i] keeps meaning line i of
    # the operator's decode table, not a sort position.
    # The ORIGINAL index rides in the group NAME (t<i>), not in a side table: a side table
    # keyed on the pattern object is per-module-instance state, and this module is loaded as
    # separate instances by its consumers -- a matcher built by one instance and redacted by
    # another silently fell back to the sorted branch index, renumbering the operator's
    # decode table. The name travels with the pattern itself; there is nothing to look up.
    order = sorted(range(len(terms)), key=lambda i: len(terms[i]), reverse=True)
    parts = [r"(?<![A-Za-z0-9])(?P<t" + str(i) + ">" + re.escape(terms[i]) + r")(?![A-Za-z0-9])"
             for i in order]
    return re.compile("(?:" + "|".join(parts) + ")", re.IGNORECASE)


def match_term_index(m):
    """0-based index into the term list that produced `m`'s matcher (AC-DOT-3).

    `re` sets exactly one group per alternation match, so `lastindex` identifies the branch;
    the branch order is longest-first (R2), so the map built by build_matcher translates it
    back to the ORIGINAL denylist index. Returns None if the matcher carries no groups (a
    caller-built matcher predating this change), so an unknown index degrades to an unindexed
    finding rather than raising."""
    if m.lastgroup is None:
        return None if m.lastindex is None else m.lastindex - 1
    return int(m.lastgroup[1:])


def format_name_term_finding(path, line_no, term_index):
    """The ONE constructor for a name-term finding (AC-DOT-2). It takes an INDEX, never the matched
    text, so there is no code path by which a term can reach a hit string, stdout, or an exception.

    Shape is term-independent by construction -- only `path`, `line_no` and `term_index` vary -- so
    two different terms cannot be told apart from the finding (AC-DOT-8(iii)). The line number is
    ADDED here: withholding the term costs the operator locating power, and this gives it back."""
    idx = "?" if term_index is None else term_index
    return f"NAME-TERM: {path}:{line_no}: term[{idx}]"


def redact_path(path, matcher):
    """THE one constructor of every path string that reaches a finding (AC-LPS-2).

    Replaces each matched term span in `path` with `term[<index>]`, span-exact: multiple
    distinct terms each redact to their own index, and unmatched segments are untouched --
    the operator keeps locating power, the term is withheld. With no matcher (markers-only
    mode), the path passes through unchanged: there are no terms to withhold.

    Why paths need this at all: findings previously echoed their path verbatim, so a term
    living in a file or directory NAME would be republished by the first marker/read-error
    finding inside it -- on a public repo, in a world-readable Actions log."""
    # R6: POSIX filenames may embed \n/\r/ANSI escapes; a crafted name could inject a fake
    # sentinel line into a public log. The one constructor bounds them -- redaction first,
    # then control characters to their escaped forms.
    if matcher is not None:
        path = matcher.sub(lambda m: f"term[{match_term_index(m)}]", path)
    return "".join(c if (31 < ord(c) != 127) else repr(c)[1:-1] for c in path)


def _is_binary(data):
    """Pinned rule (AC-PNLG-2): a NUL byte within the first 8192 bytes => binary => skip."""
    return _NUL in data[:_BINARY_SNIFF_BYTES]


def scan_tree(root, denylist_path, markers_only=False):
    """Walk `root` (os.walk, followlinks=False) and scan every regular text file for a name-term
    (from `denylist_path` via load_denylist/build_matcher) or a structural marker (HBK-[0-9] / the
    bracket-memory-colon marker, both case-insensitive). Excludes BY PATH exactly `.git/` and the
    single file at `os.path.realpath(denylist_path)` (derived from the argument, not hardcoded).

    Fail-closed aggregation: returns exit_code != 0 if ANY name-term OR marker matches, OR the
    denylist is missing/unreadable/zero-term, OR a regular non-binary file is unreadable/undecodable,
    OR a directory-listing error occurs during the walk. exit_code == 0 ("clean tree") only when NONE
    of those hold. Only a genuine NUL-byte binary file is skipped; a broken/dangling symlink is
    skipped (not followed, not a fail-close) -- only regular files are scanned.

    Returns (exit_code, hits) where `hits` is a list of human-readable finding strings.
    """
    hits = []
    denylist_error = False
    matcher = None
    visited = [0]
    if markers_only:
        # AC-DOT-5 DEGRADED: no denylist is loaded and its absence is NOT an error -- but the caller
        # is responsible for reporting the term scan as NOT RUN. This branch is reachable only via an
        # explicit opt-in, never by inferring "degrade" from an empty/missing denylist, which would
        # turn a deleted secret into a silent downgrade on every push.
        pass
    else:
        try:
            terms = load_denylist(denylist_path)
            matcher = build_matcher(terms)
        except DenylistError as e:
            # R1 (PR #308 review): REFUSE WITHOUT WALKING. With no matcher there is no
            # redaction, so every marker/read-error finding the walk produced would publish
            # its raw path -- in the exact configuration (a bad secret) where the
            # ::add-mask:: layer registered nothing either. The disclosure would be in the
            # log before the exit code said anything. No honest scan exists here; stop.
            hits.append(f"DENYLIST-ERROR: {e}")
            return 1, hits

    excluded_real = os.path.realpath(denylist_path) if denylist_path else None

    traversal_error = [False]

    def _onerror(err):
        # Fail-closed: a directory-listing error is NEVER silently swallowed. The error's
        # text embeds a path, so it rides the redactor like every other path field.
        traversal_error[0] = True
        hits.append(f"WALK-ERROR: {redact_path(str(err), matcher)}")

    read_error = [False]

    for dirpath, dirnames, filenames in os.walk(root, onerror=_onerror, followlinks=False):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            # Symlinks are not followed at all (dangling or not) -- only regular files are scanned.
            if os.path.islink(full):
                continue
            if not os.path.isfile(full):
                continue
            shown = redact_path(full, matcher)
            # Counted BEFORE the denylist exclusion: a tree whose only regular file is the
            # (correctly excluded) denylist is not an EMPTY tree, and convicting it would make the
            # empty-corpus refusal fire on a legitimate scan. What that refusal is for is a root
            # with no files at all — an unchecked-out or wrongly-pointed workspace.
            visited[0] += 1
            if excluded_real is not None and os.path.realpath(full) == excluded_real:
                continue
            try:
                with open(full, "rb") as fh:
                    data = fh.read()
            except OSError as e:
                read_error[0] = True
                hits.append(f"READ-ERROR: {shown}: {redact_path(str(e), matcher)}")
                continue
            if _is_binary(data):
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as e:
                read_error[0] = True
                hits.append(f"DECODE-ERROR: {shown}: {redact_path(str(e), matcher)}")
                continue
            if matcher is not None:
                # AC-LPS-1: the path itself is a scanned surface -- a term in a file or
                # directory NAME ships as surely as one in content. Matched on the
                # ROOT-RELATIVE path (the corpus text), never the absolute prefix, so an
                # operator's home directory or CI workspace path can never fire a term.
                rel = os.path.relpath(full, root)
                pm = matcher.search(rel)
                if pm:
                    hits.append(f"PATH-TERM: {redact_path(rel, matcher)}: term[{match_term_index(pm)}]")
                m = matcher.search(text)
                if m:
                    # AC-DOT-2: the term itself NEVER leaves this scope. Only its index and location
                    # do. `text.count` over the prefix is the 1-based line of the match start.
                    hits.append(format_name_term_finding(
                        shown, text.count("\n", 0, m.start()) + 1, match_term_index(m)))
            if _HBK_MARKER.search(text):
                hits.append(f"MARKER-HBK: {shown}")
            if _MEMORY_MARKER.search(text):
                hits.append(f"MARKER-MEMORY: {shown}")

    name_or_marker_hit = any(h.startswith(("NAME-TERM", "PATH-TERM", "MARKER-")) for h in hits)
    # R2: a clean verdict over ZERO files is indistinguishable from a real one. A leak-gate step
    # added to a job without `actions/checkout`, or pointed at the wrong root after a
    # working-directory/container change, walks an empty tree, raises nothing (an empty directory is
    # not a listing error) and reports CLEAN having scanned no bytes. The sibling scanner already
    # solved this with `probe_count`; same idea -- an empty corpus is a refusal, not a pass.
    if visited[0] == 0:
        hits.append("EMPTY-CORPUS-ERROR: the root contains no regular files -- refusing rather than "
                    "reporting clean over an empty corpus (is the tree checked out?)")
    exit_code = 1 if (denylist_error or traversal_error[0] or read_error[0]
                      or name_or_marker_hit or visited[0] == 0) else 0
    return exit_code, hits


def _cli(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Fail-closed proprietary-name / structural-leak-marker scan.")
    ap.add_argument("--root", default=".", help="root of the tree to scan (default: .)")
    ap.add_argument("--denylist", help="path to the denylist (resolved OUT of the scanned tree)")
    ap.add_argument("--markers-only", action="store_true",
                    help="AC-DOT-5 DEGRADED mode: run structural markers only, with NO term scan. "
                         "Valid ONLY where secrets structurally cannot resolve (a fork PR). Never "
                         "reports the term scan as clean.")
    args = ap.parse_args(argv)

    if args.markers_only:
        exit_code, hits = scan_tree(args.root, None, markers_only=True)
        for h in hits:
            print(h)
        # A degraded run must never look like a pass. The term-scan verdict is reported as NOT RUN,
        # distinctly from both CLEAN and FOUND, so no reader (or grep) can mistake it for coverage.
        print("LEAK-SCAN-DEGRADED: term scan NOT RUN (no denylist available on this event)")
        print("MARKER-SCAN-CLEAN" if exit_code == 0 else "MARKER-SCAN-FOUND")
        return exit_code

    if not args.denylist:
        # Fail-closed: absent a denylist and absent an explicit degrade, there is no honest verdict.
        print("DENYLIST-ERROR: no --denylist supplied and --markers-only not set (fail-closed)")
        print("LEAK-SCAN-FOUND")
        return 1

    exit_code, hits = scan_tree(args.root, args.denylist)
    for h in hits:
        print(h)
    # AC-DOT-3: the COUNT makes an index interpretable; the list is never printed.
    try:
        print(f"denylist: terms={len(load_denylist(args.denylist))}")
    except DenylistError:
        pass
    print("LEAK-SCAN-CLEAN" if exit_code == 0 else "LEAK-SCAN-FOUND")
    return exit_code


if __name__ == "__main__":
    import sys
    sys.exit(_cli())
