#!/usr/bin/env python3
"""foundry-paper-guard — PreToolUse (Edit|Write|MultiEdit|NotebookEdit).

Paper is written only when the ticket asks for it. A write under a PAPER path — specs/, intake/,
.foundry/, status-reports/, charters/, docs/, or an acceptance-contract.yaml / release.yaml /
state.yaml anywhere — is refused unless the active ticket (in the repository's git dir, see
scripts/foundry-ticket.py) lists that path or a parent directory under `paper_allowed`.

Measured reason this exists: over 60 days on two adopter workspaces 76% of all agent file edits
went to paper and the paper:code line ratio ran 1.5:1 to 8:1. The guard makes paper deliberate,
not impossible: `foundry-ticket.py allow <path>` lifts it for one path when the operator asks.

Admitted without a ticket (rule-mandated): `.foundry/research/`, `.foundry/decisions/`, `governance/local/`,
`governance/rendered/` — except a file named acceptance-contract.yaml / release.yaml / state.yaml, which
still needs a ticket.

A second class, `ci` (gv-10, operating-model rule A2: never edit CI to make a PR pass), is gated the same
way: `.github/workflows/**`, `.github/actions/**`, other CI-system files (`.gitlab-ci.yml`, `.circleci/**`,
`Jenkinsfile`, `azure-pipelines.yml`, `scripts/ci/**`, `ci/**`), any script the repo's own workflows invoke
from `scripts/`/`ci/`/`tools/`, test configs (`pyproject.toml`, `setup.cfg`, `.coveragerc` at the root;
`pytest.ini`, `tox.ini`, `jest|vitest|playwright|karma.config.*` anywhere), and quarantine lists
(`quarantine*.json|yaml|yml`). It is refused unless the active ticket names the path (or a parent directory)
in `paper_allowed` or in `ci_allowed` (`foundry-ticket.py allow --ci <path>`). With no ticket it is refused.
The point is not that CI never changes: it is that an agent never changes it to turn its own red check green.

Everything else — code, infra, tests, CLAUDE.md, config — is admitted without a ticket. Writes
outside the project root (e.g. ~/.claude memory) are admitted; so is a symlink that resolves
outside it. Malformed payloads are admitted (a visibility control, not a security floor). The
first path component is compared case-insensitively on case-insensitive filesystems (darwin).
A refusal is exit 2 with the reason on stderr (what the agent sees) and as JSON on stdout.
"""
import fnmatch
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import foundry_ticket_store as store  # noqa: E402

PAPER_DIRS = ("specs", "intake", ".foundry", "status-reports", "charters", "docs")
# Rule-mandated paper: written by the governance and research rules themselves, so admitted without a
# ticket. Exact directory names, not a free pass: every other path under `.foundry/` stays paper.
PAPER_ADMITTED = (".foundry/research", ".foundry/decisions", "governance/local", "governance/rendered")
PAPER_FILES = ("acceptance-contract.yaml", "release.yaml", "state.yaml")
# The `ci` class (gv-10). Globs are matched against the root-relative POSIX path; `**/x` also matches a
# root-level `x`. A path under a directory glob (`dir/**`) matches at any depth below it.
CI_GLOBS = (
    ".github/workflows/**", ".github/actions/**", ".gitlab-ci.yml", ".circleci/**", "Jenkinsfile",
    "azure-pipelines.yml", "scripts/ci/**", "ci/**",
    "pyproject.toml", "setup.cfg", ".coveragerc",
    "**/pytest.ini", "**/tox.ini", "**/jest.config.*", "**/vitest.config.*", "**/playwright.config.*",
    "**/karma.conf.*", ".mocharc.*",
    "**/quarantine*.json", "**/quarantine*.yaml", "**/quarantine*.yml",
)
# A script a workflow names: `run: scripts/x.sh`, `python3 ci/check.py`, `bash ./tools/lint.sh`.
_CI_SCRIPT_RE = re.compile(r"(?<![\w./-])(?:\./)?((?:scripts|ci|tools)/[\w./-]+\.(?:sh|py|js|mjs|cjs|ts|rb|ps1))\b")
CASEFOLD = sys.platform == "darwin" or os.name == "nt"


def _root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and Path(env).is_dir():
        return Path(env).resolve()
    return Path.cwd().resolve()


def _key(s: str) -> str:
    return s.casefold() if CASEFOLD else s


def _is_paper(rel: Path) -> bool:
    parts = rel.parts
    if not parts:
        return False
    if _key(rel.name) in {_key(f) for f in PAPER_FILES}:
        return True  # named paper files need a ticket everywhere, admitted directories included
    rel_s = _key(rel.as_posix())
    if any(rel_s.startswith(_key(a) + "/") for a in PAPER_ADMITTED):
        return False
    return _key(parts[0]) in {_key(d) for d in PAPER_DIRS}


def _glob_match(rel_s: str, pat: str) -> bool:
    if pat.endswith("/**"):
        return rel_s.startswith(pat[:-2])
    if pat.startswith("**/"):
        parts = rel_s.split("/")
        return any(fnmatch.fnmatchcase("/".join(parts[k:]), pat[3:]) for k in range(len(parts)))
    return fnmatch.fnmatchcase(rel_s, pat)


def _ci_scripts(root: Path) -> set:
    """Scripts the repo's own workflows invoke (read live, so a new CI script is covered at once)."""
    found = set()
    wf = root / ".github" / "workflows"
    try:
        for f in sorted(wf.iterdir()):
            if f.suffix in (".yml", ".yaml") and f.is_file():
                for m in _CI_SCRIPT_RE.finditer(f.read_text(encoding="utf-8", errors="replace")):
                    found.add(_key(m.group(1)))
    except OSError:
        pass
    return found


def _is_ci(rel: Path, root: Path) -> bool:
    rel_s = _key(rel.as_posix())
    if any(_glob_match(rel_s, _key(g)) for g in CI_GLOBS):
        return True
    return rel_s in _ci_scripts(root)


def _allowed(rel: Path, allowed: list) -> bool:
    rel_s = _key(rel.as_posix())
    for a in allowed:
        a = str(a).strip()
        while a.startswith("./"):
            a = a[2:]
        a = _key(a.strip("/"))
        if not a:
            continue
        if rel_s == a or rel_s.startswith(a + "/"):
            return True
    return False


def _refuse(reason: str) -> int:
    msg = "foundry-paper-guard: " + reason
    sys.stderr.write(msg + "\n")
    sys.stdout.write(json.dumps({"decision": "block", "reason": msg}))
    return 2


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    tool = payload.get("tool_name", "")
    if tool not in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return 0
    ti = payload.get("tool_input") or {}
    target = ti.get("file_path") or ti.get("notebook_path") or ""
    if not target:
        return 0
    root = _root()
    try:
        abs_t = Path(target).expanduser()
        if not abs_t.is_absolute():
            abs_t = root / abs_t
        # resolve the PARENT (the file may not exist yet) so symlinked dirs land where they point
        resolved = abs_t.parent.resolve() / abs_t.name
        rel = resolved.relative_to(root)
    except Exception:
        return 0  # outside the project → not our business
    is_paper = _is_paper(rel)
    is_ci = (not is_paper) and _is_ci(rel, root)
    if not (is_paper or is_ci):
        return 0

    ticket_file = store.ticket_file(root)   # git dir only — never the work tree
    doc = None
    if ticket_file and ticket_file.is_file():
        try:
            doc = json.loads(ticket_file.read_text(encoding="utf-8"))
        except Exception:
            doc = None
    if doc and _allowed(rel, (doc.get("paper_allowed") or []) + ((doc.get("ci_allowed") or []) if is_ci else [])):
        return 0

    p = rel.as_posix()
    if is_ci:
        base = ("%s is a CI path (workflows, CI scripts, test configs, quarantine lists). Never edit CI "
                "to make a PR pass: fix the code or report the failure. " % p)
        if doc:
            return _refuse(base + "Ticket #%s does not allow it. If the operator asked for this CI change, run "
                           "`python3 \"$CLAUDE_PLUGIN_ROOT/scripts/foundry-ticket.py\" allow --ci %s` and retry." % (doc.get("issue"), p))
        return _refuse(base + "No active ticket; start one (`foundry-ticket.py start <issue>`) and have the "
                       "operator allow the path with `allow --ci %s`." % p)
    if doc:
        return _refuse("ticket #%s does not allow writing %s. If the operator asked for this document, run "
                       "`python3 \"$CLAUDE_PLUGIN_ROOT/scripts/foundry-ticket.py\" allow %s` and retry. "
                       "Otherwise write code, not paper." % (doc.get("issue"), p, p))
    return _refuse("no active ticket, and %s is a paper path (specs/, intake/, .foundry/, docs/, "
                   "status-reports/, charters/, contracts, manifests). Start the ticket you are working "
                   "(`python3 \"$CLAUDE_PLUGIN_ROOT/scripts/foundry-ticket.py\" start <issue>`) and list the "
                   "paper it needs under `## Paper allowed`; a ticket's Done means is a command, not a "
                   "document." % p)


if __name__ == "__main__":
    sys.exit(main())
