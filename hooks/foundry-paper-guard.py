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
`pytest.ini`, `tox.ini`, `Makefile`/`GNUmakefile`/`makefile`, `noxfile.py`, `.pre-commit-config.yaml`, `codecov.yml`,
`**/conftest.py`, `pyproject.toml` at any depth, `jest|vitest|playwright|cypress.config.*`, `vitest.workspace.*`,
`.nycrc*` and `karma.conf.*` anywhere; `package.json` when the edit touches its `scripts`, `jest` or `vitest`
block), and quarantine lists (`quarantine*.json|yaml|yml`). It is refused unless the active ticket names the path
(or a parent directory) in `ci_allowed` (`foundry-ticket.py ci-allow <path>`, which the permission floor asks on) or
in `issue_paper_allowed` (the issue author's own `## Paper allowed` list, recorded at `start`) AND those two lists
carry a valid signature (gv-16: an HMAC under a per-git-dir key that `ci-allow` / `start` create, so a hand edit of
the ticket file lifts nothing). The CLI-writable `paper_allowed` never lifts a CI path. With no ticket it is refused.
The path is resolved as a FILE, symlinks included, so a link into a CI path is a CI path. A `Bash` payload is
checked too (gv-16): the write targets of `sed -i`, `tee`, a `>`/`>>` redirect, `cp`/`mv`/`rm`/`truncate`/`dd of=`/
`install` that name a CI path are refused the same way (a visibility control: it reads the command, it does not
emulate a shell). The hook is wired into the Bash matcher group of hooks/hooks.json.
DOCUMENTED LIMITS of the Bash reading (it is a visibility control, not a sandbox; it reads the command text and never emulates a
shell). gv-20 widened it: `cd X && ...` is tracked (a later relative operand is judged against the `cd` operand), `-t` /
`--target-directory` is read as the destination, a recursive `cp -r` / `rsync -a` into a directory is judged by the files of the
source directory, and refused outright when the destination directory is or contains a CI path, `rm -rf tests`, `rm -rf *`
and `rm -rf ./` are judged by the files they would remove, a shell write to package.json (`echo ... > package.json`) is refused because
a diff cannot be taken, and `.worktrees/<x>/.github/**` plus any nested clone's `.github/workflows/` and `.github/actions/` under the
project root are CI. STILL NOT SEEN: wrapper flags (`env -i`, `sudo -u x`), `git checkout --` / `git apply` / `patch`, `python -c
open().write`, process substitution, a path built by expansion or `$(...)`, `cd -`, and a `cd` inside a subshell or function (a `cd`
is treated as sticky for the rest of the command line, which over-approximates).
The point is not that CI never changes: it is that an agent never changes it to turn its own red check green.

Everything else — code, infra, tests, CLAUDE.md, config — is admitted without a ticket. Writes
outside the project root (e.g. ~/.claude memory) are admitted; so is a symlink that resolves
outside it. A payload that is not valid JSON, is not an object, or makes the hook raise is REFUSED (exit 2, with a reason): the hook fails
closed. The
first path component is compared case-insensitively on case-insensitive filesystems (darwin).
A refusal is exit 2 with the reason on stderr (what the agent sees) and as JSON on stdout.
"""
import fnmatch
import json
import os
import re
import sys
from pathlib import Path

# The one import that can fail (the ticket store lives beside the hooks in the plugin). A failure must not be a traceback with
# exit 1: `main` refuses (exit 2, with the reason) while `store` is None.
store = None
STORE_ERROR = ""
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import foundry_ticket_store as store  # noqa: E402
except BaseException as _exc:  # noqa: BLE001 - ImportError, SyntaxError in the store, anything: fail closed
    store = None
    STORE_ERROR = "%s: %s" % (type(_exc).__name__, str(_exc)[:160])

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
    "pyproject.toml", "**/pyproject.toml", "setup.cfg", ".coveragerc",
    "Makefile", "GNUmakefile", "makefile", "noxfile.py", ".pre-commit-config.yaml", "**/conftest.py",
    "**/pytest.ini", "**/tox.ini", "**/jest.config.*", "**/vitest.config.*", "**/playwright.config.*",
    "**/cypress.config.*", "**/vitest.workspace.*", "**/karma.conf.*", ".mocharc.*",
    "codecov.yml", ".codecov.yml", "**/.nycrc*",
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
        tail = pat[3:]
        if "/" not in tail:                    # a file-name pattern: match the NAME only, `*` never spans a `/`
            return fnmatch.fnmatchcase(rel_s.rsplit("/", 1)[-1], tail)
        parts = rel_s.split("/")
        return any(fnmatch.fnmatchcase("/".join(parts[k:]), tail) for k in range(len(parts)))
    return fnmatch.fnmatchcase(rel_s, pat)


_CI_SCRIPTS_MEMO = {}      # one hook process judges one payload; the workflows are read once


def _ci_scripts(root: Path) -> set:
    """Scripts the repo's own workflows invoke (read live, so a new CI script is covered at once)."""
    if str(root) in _CI_SCRIPTS_MEMO:
        return _CI_SCRIPTS_MEMO[str(root)]
    found = _CI_SCRIPTS_MEMO.setdefault(str(root), set())
    wf = root / ".github" / "workflows"
    try:
        for f in sorted(wf.iterdir()):
            if f.suffix in (".yml", ".yaml") and f.is_file():
                for m in _CI_SCRIPT_RE.finditer(f.read_text(encoding="utf-8", errors="replace")):
                    found.add(_key(m.group(1)))
    except OSError:
        pass
    return found


_TEST_SCRIPT_RE = re.compile(r'"(?:pre|post)?test(?::[\w:-]*)?"\s*:')
_PKG_BLOCK_RE = re.compile(r'"(?:scripts|jest|vitest)"\s*:')
_PKG_KEYS = ("scripts", "jest", "vitest")


def _apply_edits(text: str, ti: dict):
    """The file text after the payload's Edit / MultiEdit, or None when it cannot be simulated."""
    edits = [e for e in (ti.get("edits") or []) if isinstance(e, dict)]
    if not edits and isinstance(ti.get("old_string"), str):
        edits = [{"old_string": ti["old_string"], "new_string": ti.get("new_string", ""),
                  "replace_all": ti.get("replace_all")}]
    if not edits:
        return None
    for e in edits:
        old, new = e.get("old_string"), e.get("new_string", "")
        if not isinstance(old, str) or not isinstance(new, str):
            return None
        if old and old in text:
            text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
    return text


def _package_json_touches_ci(ti: dict, abs_path: Path) -> bool:
    """package.json is CI when the edit changes its TOP-LEVEL `scripts`, `jest` or `vitest` block (a dependency edit is
    not, even a `"jest": "^29"` line under devDependencies). The edit is SIMULATED on the current file and the blocks
    compared; only when the file is missing or unparseable (or the edit cannot be simulated) does the snippet text decide,
    and then fail-closed on a block or test-script key."""
    def blocks(txt):
        try:
            doc = json.loads(txt)
            return {k: doc.get(k) for k in _PKG_KEYS} if isinstance(doc, dict) else None
        except Exception:
            return None
    try:
        old_text = abs_path.read_text(encoding="utf-8")
    except OSError:
        old_text = None
    old = blocks(old_text) if old_text else None
    if isinstance(ti.get("content"), str):             # a whole-file Write: compare the blocks
        new = blocks(ti["content"])
        return new is None or new != (old if old is not None else {k: None for k in _PKG_KEYS})
    if old is not None:
        new_text = _apply_edits(old_text, ti)
        if new_text is not None:
            new = blocks(new_text)
            return new is None or new != old
    texts = [ti.get("old_string"), ti.get("new_string")]
    for e in ti.get("edits") or []:
        if isinstance(e, dict):
            texts += [e.get("old_string"), e.get("new_string")]
    return any(isinstance(t, str) and (_TEST_SCRIPT_RE.search(t) or _PKG_BLOCK_RE.search(t)) for t in texts)


def _ci_prefixes() -> list:
    """The literal leading directory of every CI glob (`.github/workflows/`, `ci/`, ...), plus exact files."""
    out = []
    for g in CI_GLOBS:
        if g.startswith("**/"):
            continue
        out.append(re.split(r"[*?\[]", g, 1)[0])
    return out


def ci_overlaps(rel_s: str, root: Path) -> bool:
    """True when `rel_s` is a CI path, or a directory that contains CI paths (`.github`, `scripts/ci`)."""
    rel = Path(rel_s)
    if _is_ci(rel, root):
        return True
    k = _key(rel.as_posix().strip("/"))
    if k.endswith("/.github") or re.search(r"(?:^|/)\.github/(?:workflows|actions)$", k):
        return True                                     # a nested clone's / worktree's .github directory
    return any(_key(p).startswith(k + "/") for p in _ci_prefixes()) or k in {_key(p) for p in _ci_prefixes()}


_NESTED_CI_RE = re.compile(r"(?:^|/)\.github/(?:workflows|actions)(?:/|$)")
_WORKTREE_RE = re.compile(r"^\.worktrees/[^/]+/(.+)$")


def _is_ci(rel: Path, root: Path, ti: dict = None) -> bool:
    rel_s = _key(rel.as_posix())
    if _NESTED_CI_RE.search(rel_s):                      # a worktree's, or a nested product clone's, own workflows
        return True
    m = _WORKTREE_RE.match(rel_s)
    if m and m.group(1) != rel_s:                        # `.worktrees/<x>/<path>` is judged as `<path>`
        return _is_ci(Path(m.group(1)), root, ti)
    if rel_s.rsplit("/", 1)[-1] == "package.json":
        return _package_json_touches_ci(ti or {}, root / rel) if ti is not None else False
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


_FILE_VERBS = {"tee", "truncate", "touch"}                 # every positional operand is written
_REMOVE_VERBS = {"rm", "shred", "unlink"}                  # every positional operand is removed
_DEST_VERBS = {"cp", "install", "rsync", "ln", "mv"}       # the LAST operand is the destination; mv also removes the rest
_WRAPPERS = {"sudo", "command", "env", "nohup", "time", "exec"}


def _split_newlines(command: str) -> str:
    """An unquoted newline separates commands exactly like `;`: turn it into one."""
    out, quote, esc = [], None, False
    for ch in command:
        if esc:
            esc = False
        elif ch == "\\" and quote != "'":
            esc = True
        elif quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "\n":
            out.append(" ; ")
            continue
        out.append(ch)
    return "".join(out)


_RECURSIVE_FLAG = re.compile(r"-[A-Za-z]*[rRa][A-Za-z]*")
_GLOB_CHARS = set("*?[")


def _is_recursive(flags: list) -> bool:
    return any(_RECURSIVE_FLAG.fullmatch(f) or f in ("--recursive", "--archive") for f in flags)


def _target_directory(rest: list):
    """-> (target_dir or None, remaining operands) for cp/mv/install/ln: `-t DIR`, `-tDIR`, `-rt DIR`, `--target-directory DIR`,
    `--target-directory=DIR`."""
    pos, tdir, i = [], None, 0
    while i < len(rest):
        a = rest[i]
        if a == "--target-directory" and i + 1 < len(rest):
            tdir, i = rest[i + 1], i + 2
            continue
        if a.startswith("--target-directory="):
            tdir = a.split("=", 1)[1]
        elif re.fullmatch(r"-[A-Za-z]*t", a) and i + 1 < len(rest):
            tdir, i = rest[i + 1], i + 2
            continue
        elif re.fullmatch(r"-[A-Za-z]*t.+", a) and not a.startswith("--"):
            tdir = a.split("t", 1)[1]
        elif not a.startswith("-"):
            pos.append(a)
        i += 1
    return tdir, pos


def _bash_write_targets(command: str) -> list:
    """Write targets named by a Bash command, as (path, kind, sources, recursive) with kind `file` (written), `remove` (deleted or
    moved away) or `dest` (a cp/mv/install/ln/rsync destination; it may be a directory the sources land in). Covers `sed -i` /
    `perl -i` operands, `tee`, `>` / `>>` redirects, cp/mv/rm/truncate/install/ln/rsync (`-t DIR` included) and `dd of=`. `cd X &&`
    is tracked: a later relative operand is joined onto the `cd` operand. A reading of the command text, not a shell: whatever it
    cannot see (a path built by expansion, `python -c`) is out of reach, which is why this is a VISIBILITY control, not a boundary."""
    import shlex
    command = _split_newlines(command)
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:
        toks = command.split()
    out, words, cur, stack = [], [], [""], []
    seg = [""]            # the base in force when the current pipeline began (both sides of a `|` / `&` inherit it)

    def jn(p):
        if cur[0] and p and not p.startswith(("/", "~")):
            return os.path.join(cur[0], p)
        return p

    def emit(path, kind, srcs, rec):
        """A relative target is judged against the `cd` base AND the payload cwd (either hitting CI refuses)."""
        out.append((jn(path), kind, [jn(x) for x in srcs], rec))
        if cur[0] and path and not path.startswith(("/", "~")):
            out.append((path, kind, list(srcs), rec))

    def finish():
        args = list(words)
        words.clear()
        while args and (re.fullmatch(r"[A-Za-z_]\w*=.*", args[0]) or os.path.basename(args[0]) in _WRAPPERS):
            args = args[1:]
        if not args:
            return
        verb, rest = os.path.basename(args[0]), args[1:]
        pos = [a for a in rest if not a.startswith("-")]
        flags = [a for a in rest if a.startswith("-")]
        rec = _is_recursive(flags)
        if verb in ("cd", "pushd", "popd"):
            if verb == "popd" or "-" in rest:
                return            # `cd -` / `popd`: the new directory is unknown. Keep the previous base and judge against the cwd too
            if not pos:
                cur[0] = "~"
            else:
                new = jn(pos[0])
                if not ("$" in new or "`" in new):         # `cd $VAR` / `cd $(…)`: unknown, same fail-closed handling
                    cur[0] = new
            return
        if verb in ("sed", "gsed", "perl") and any(re.fullmatch(r"-[A-Za-z]*i[A-Za-z0-9._]*", f) or f.startswith("--in-place")
                                                  for f in flags):
            for p in pos:                                 # the script text never resolves to a CI path; the files do
                emit(p, "file", [], False)
        elif verb in _FILE_VERBS:
            for p in pos:
                emit(p, "file", [], False)
        elif verb in _REMOVE_VERBS:
            for p in pos:
                emit(p, "remove", [], rec)
        elif verb in _DEST_VERBS and verb != "rsync" and _target_directory(rest)[0] is not None:
            tdir, srcs = _target_directory(rest)
            emit(tdir, "dest", srcs, rec)
            if verb == "mv":
                for p in srcs:
                    emit(p, "remove", [], True)
        elif verb in _DEST_VERBS and pos:
            emit(pos[-1], "dest", pos[:-1], rec or verb == "rsync")
            if verb == "mv":
                for p in pos[:-1]:
                    emit(p, "remove", [], True)
        elif verb == "dd":
            for a in rest:
                if a.startswith("of="):
                    emit(a.split("=", 1)[1], "file", [], False)

    i = 0
    while i < len(toks):
        t = toks[i]
        if t and set(t) <= set(";&|()"):                 # a command separator
            finish()
            # A successful `cd` persists across `;`, newline, `&&` and `||`, so the base is carried over all of them (judging against
            # it AND the cwd is fail-closed). It ends at a pipe (each side runs in its own subshell), a lone `&` (background)
            # and the `)` that closes a subshell. A token may combine operators (`&&(`): read it one operator at a time.
            k = 0
            while k < len(t):
                two = t[k:k + 2]
                if two in ("&&", "||"):
                    seg[0] = cur[0]
                    k += 2
                    continue
                c = t[k]
                if c == "(":
                    stack.append(cur[0])
                    seg[0] = cur[0]
                elif c == ")":
                    cur[0] = stack.pop() if stack else ""
                    seg[0] = cur[0]
                elif c in "|&":
                    cur[0] = seg[0]       # the `cd` itself ran in a subshell, but a cd made BEFORE the pipeline is inherited
                else:
                    seg[0] = cur[0]       # `;` starts a new pipeline with whatever base the cd left
                k += 1
        elif t and set(t) <= set("<>&|") and (">" in t or "<" in t):   # a redirect operator: its operand is the next word
            tgt = toks[i + 1] if i + 1 < len(toks) else ""
            if ">" in t and tgt and not tgt.isdigit() and not tgt.startswith("&"):
                emit(tgt, "file", [], False)
            i += 1
        elif t.isdigit() and i + 1 < len(toks) and set(toks[i + 1]) <= set("<>&") and ">" in toks[i + 1]:
            pass                                         # the fd number of `2>file`
        else:
            words.append(t)
        i += 1
    finish()
    return [x for x in out if x[0]]


def _resolve_rels(target: str, root: Path, base: Path) -> list:
    """Root-relative paths the write can be judged by: the LINK path (parent resolved, name kept) and the fully resolved
    FILE path (symlinks followed). Either may be a CI path; a path that lands outside the project root is dropped."""
    abs_t = Path(target).expanduser()
    if not abs_t.is_absolute():
        abs_t = base / abs_t
    rels = []
    for cand in (lambda: abs_t.parent.resolve() / abs_t.name, lambda: abs_t.resolve()):
        try:
            r = cand().relative_to(root)
        except Exception:
            continue
        if r not in rels:
            rels.append(r)
    return rels


def _tree_files(top: Path, cap: int = 20000):
    """Root-relative-agnostic walk of `top` (a directory): yields file paths below it, pruning .git and node_modules, capped."""
    n = 0
    for dp, dns, fns in os.walk(top):
        dns[:] = [d for d in dns if d not in _PRUNE_DIRS]
        for f in fns:
            n += 1
            if n > cap:
                return
            yield Path(dp) / f


_PRUNE_DIRS = ("node_modules", ".venv", ".git")


def _dir_holds_ci(directory: Path, root: Path) -> bool:
    try:
        if any(part in _PRUNE_DIRS for part in directory.relative_to(root).parts):
            return False                          # removing node_modules / .venv / .git itself is not a CI edit
    except ValueError:
        pass
    for f in _tree_files(directory):
        try:
            if _is_ci(f.relative_to(root), root, {}) and f.name != "package.json":
                return True
        except ValueError:
            continue
    return False


def _bash_ci_hit(rel: Path, kind: str, srcs: list, root: Path, base: Path, raw: str, recursive: bool = False):
    """True when this Bash write lands on (or removes) a CI path. A directory destination is judged by what lands in it."""
    if rel.name == "package.json" and kind != "remove":
        return True       # gv-20: a shell write of package.json cannot be diffed by block, so it is judged as CI (ci-allow lifts it)
    absdir = root / rel
    if kind == "remove":
        if _is_ci(rel, root, {}) or ci_overlaps(rel.as_posix(), root):
            return True
        return bool(recursive and absdir.is_dir() and _dir_holds_ci(absdir, root))
    if kind == "dest":
        into_dir = raw.endswith("/") or absdir.is_dir()
        if into_dir:
            names = [Path(x).name for x in srcs if Path(x).name] or ["_"]
            if any(_is_ci(rel / n, root, {}) for n in names):
                return True
            if recursive:
                if ci_overlaps(rel.as_posix(), root):
                    return True                          # a recursive copy into a directory that is or holds CI paths
                for x in srcs:
                    sp = Path(x).expanduser()
                    sp = sp if sp.is_absolute() else base / sp
                    if sp.is_dir():                      # judged by the files that would land
                        sub = rel if x.endswith("/") else rel / Path(x).name
                        for f in _tree_files(sp):
                            if _is_ci(sub / f.relative_to(sp), root, {}):
                                return True
            return False
    return _is_ci(rel, root, {})


def _load_ticket(root: Path):
    ticket_file = store.ticket_file(root)   # git dir only — never the work tree
    if ticket_file and ticket_file.is_file():
        try:
            return json.loads(ticket_file.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _ci_lists(doc, root: Path):
    """(ci_allowed + issue_paper_allowed, signature_ok). The lists lift nothing without a valid signature."""
    if not doc:
        return [], True
    lists = (doc.get("ci_allowed") or []) + (doc.get("issue_paper_allowed") or [])
    if not lists:
        return [], True
    return (lists, True) if store.verify_ticket_lists(doc, root) else ([], False)


def _refuse_ci(rel: Path, doc, sig_ok: bool) -> int:
    p = rel.as_posix()
    base = ("%s is a CI path (workflows, CI scripts, test configs, quarantine lists). Never edit CI "
            "to make a PR pass: fix the code or report the failure. " % p)
    if not sig_ok:
        base += ("The ticket's ci_allowed / issue_paper_allowed carry no valid signature (a hand edit of the ticket "
                 "file lifts nothing); the lists are ignored. ")
    if doc:
        return _refuse(base + "Ticket #%s does not allow it. If the operator asked for this CI change, run "
                       "`python3 \"$CLAUDE_PLUGIN_ROOT/scripts/foundry-ticket.py\" ci-allow %s` and retry." % (doc.get("issue"), p))
    return _refuse(base + "No active ticket; start one (`foundry-ticket.py start <issue>`) and have the "
                   "operator allow the path with `ci-allow %s`." % p)


def _expand_glob(target: str, base: Path, cap: int = 500) -> list:
    """`rm -rf *` / `rm a*`: the operand as the shell would expand it against the cwd (the pattern itself when nothing matches)."""
    if not (set(target) & set("*?[")):
        return [target]
    import glob
    t = os.path.expanduser(target)
    hits = sorted(glob.glob(t if os.path.isabs(t) else os.path.join(str(base), t), recursive=False))[:cap]
    return hits or [target]


def _main_bash(payload: dict, root: Path) -> int:
    ti = payload.get("tool_input")
    if not isinstance(ti, dict) or not isinstance(ti.get("command"), str):
        return _refuse("a Bash payload whose tool_input is not an object with a string `command` cannot be judged (fail closed)")
    command = ti["command"]
    if not command.strip():
        return 0
    cwd = payload.get("cwd")
    base = Path(cwd) if isinstance(cwd, str) and cwd and Path(cwd).is_absolute() else root
    doc = _load_ticket(root)
    lists, sig_ok = _ci_lists(doc, root)
    for tgt, kind, srcs, rec in _bash_write_targets(command):
        for one in _expand_glob(tgt, base):
            for rel in _resolve_rels(one, root, base):      # the link AND the file it resolves to: either being CI refuses
                if _bash_ci_hit(rel, kind, srcs, root, base, one, rec) and not _allowed(rel, lists):
                    return _refuse_ci(rel, doc, sig_ok)
    return 0


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return _refuse("the hook payload is not valid JSON, so it cannot be judged (fail closed)")
    if not isinstance(payload, dict):
        return _refuse("the hook payload is not a JSON object, so it cannot be judged (fail closed)")
    if store is None:
        return _refuse("the ticket store could not be imported (%s), so the write cannot be judged (fail closed)" % STORE_ERROR)
    tool = payload.get("tool_name", "")
    if tool == "Bash":
        return _main_bash(payload, _root())
    if tool not in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return 0
    ti = payload.get("tool_input")
    if not isinstance(ti, dict):
        return _refuse("tool_input is not an object, so the write cannot be judged (fail closed)")
    target = ti.get("file_path") or ti.get("notebook_path") or ""
    if not isinstance(target, str):
        return _refuse("the file path is not a string, so the write cannot be judged (fail closed)")
    if not target:
        return 0
    root = _root()
    rels = _resolve_rels(target, root, root)        # the link path AND the file it resolves to; each is judged
    if not rels:
        return 0  # outside the project → not our business
    doc = _load_ticket(root)
    lists, sig_ok = _ci_lists(doc, root)
    for rel in rels:
        is_paper = _is_paper(rel)
        is_ci = (not is_paper) and _is_ci(rel, root, ti)
        if is_ci:
            if not _allowed(rel, lists):
                return _refuse_ci(rel, doc, sig_ok)
        elif is_paper and not (doc and _allowed(rel, doc.get("paper_allowed") or [])):
            return _refuse_paper(rel, doc)
    return 0


def _refuse_paper(rel: Path, doc) -> int:
    p = rel.as_posix()
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
    try:
        code = main()
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - any uncaught failure refuses (exit 2), never 0 and never a traceback's 1
        code = _refuse("the hook failed (%s: %s), so the write cannot be judged (fail closed)" % (type(exc).__name__, str(exc)[:160]))
    sys.exit(code)
