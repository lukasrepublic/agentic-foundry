#!/usr/bin/env python3
"""foundry-shell-shape — PreToolUse(Bash) lint of the COMMAND SHAPE (gv-11, operating-model rule B6).

Why a hook: a Bash command whose shape the permission layer cannot read ahead of time (a packed
`bash -c '…; …'`, a heredoc script, a `cd` that moves the session's write boundary, an `rm` of a path the
agent has not proven it owns) raises a permission prompt or, worse, runs something nobody reviewed. The
rule is: write the script to a file and run it BY NAME; use `git -C <abs-dir>` and absolute paths; delete
only inside the session scratch directory. This hook refuses the shapes that break the rule and says,
in the refusal, which shape is allowed.

REFUSES (exit 2, reason + the allowed shape on stderr and as a `permissionDecision: deny` JSON):
  * `rm` (also `unlink`, `shred`, `rmdir`, and `find … -delete` / `-exec rm`) whose target is not
    provably INSIDE the session scratch directory. A target built from `$VAR`, a command substitution
    or `~user` is unknowable here, so it is refused (convict on doubt). `git worktree remove` is not `rm`.
  * a PACKED script: `bash -c` / `sh -c` / `zsh -c` / `dash -c`, `eval`, `python -c`, whose payload holds
    `;`, `&&`, `||`, a newline or a heredoc; and `python3 -` / `bash -s` / a bare `bash` (a script on
    stdin). A single simple command string (`bash -c 'git status'`) is admitted — and its payload is then
    judged by these same rules, so `bash -c 'rm -rf /'` is still the `rm` refusal.
  * a heredoc `the foundry_shell_scan module` classifies `code` (anything but a closed, inert,
    file-sink shape). A `data` heredoc whose sink is a file (`cat > notes.txt <<'EOF' …`) is ADMITTED:
    that IS "write the script to a file and run it by name".
  * gv-20: also `truncate`, `git clean` (without -n), `rsync --delete*` / `--remove-source-files`; a leading redirection
    (`</dev/null rm -rf x`) and ANSI-C quoting (`$'rm'`) no longer hide the verb; a trailing redirection (`2>/dev/null`) is not a
    delete target; `bash -c "$CMD"`, a script on stdin (`cat s.py | python3`) and `perl|ruby|node|php -e` are refused as packed.
  * `cd` / `pushd` / `popd` as a command (use `git -C <abs-dir> …` and absolute paths).

SCRATCH RESOLUTION ORDER (first that exists wins; later ones are NOT consulted once an earlier one is set):
  1. `$CLAUDE_SCRATCHPAD`                           (the hook process's environment, an absolute path)
  2. the harness-provided path in the hook payload  (`scratchpad` / `scratchpad_dir` / `scratch_dir`,
                                                      top level or under `tool_input`)
  3. the pattern `/tmp/claude-*/**/<session_id>/scratchpad/` for THIS session only (the payload's `session_id`; another
     session's scratchpad is not admitted, and with no session id the fallback admits nothing)
A target equals-or-escapes the scratch root is refused: only paths STRICTLY INSIDE it are admitted, symlinks
are resolved first (`realpath`), and `..` cannot climb out.

FLOOR: there is no policy off-switch and no argument that admits a refused shape. `FOUNDRY_SHELL_SHAPE_STRICT=1`
(or `--strict`) is the CI-preflight widening: it ALSO refuses a command line holding more than one command
(`;`, `&&`, `||`, `|`, `&`, newline). One simple command per call is otherwise advisory text in the brief.

FAILURE POSTURE: a payload with no command is admitted (nothing to lint). Once a command IS recovered, an
internal error exits 2 (fail closed) rather than admitting an unlinted command.

NOT A BOUNDARY: this is a lint over command TEXT. Shell indirection it does not model (a script that runs
`rm` for you, `$(…)` evaluation, an interpreter other than those named) is a bounded residual; the real
boundaries are the write-jail hooks and the platform's permission layer.
"""
from __future__ import annotations

import json
import os
import re
import sys

_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
scan = None     # foundry_shell_scan, imported inside main() once a command is in hand (so a broken
                # install fails CLOSED with exit 2 instead of a traceback, which the harness treats as admit)

GUARD = "shell-shape"
MAX_DEPTH = 3
DELETE_VERBS = {"rm", "unlink", "shred", "rmdir", "truncate"}
# options of a delete verb that consume the next word (so the value is not mistaken for a target)
_VERB_TAKES = {"truncate": {"-s", "-r", "--size", "--reference", "-o"}, "shred": {"-n", "-s", "--iterations", "--size", "--random-source"}}
_RSYNC_DELETE = re.compile(r"--(?:del|delete(?:-[a-z]+)?|remove-source-files)(?:=.*)?")
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
SCRATCH_PATTERN = re.compile(r"^/tmp/claude-[^/]+/(?:[^/]+/)+scratchpad/.+$")
_GLOB = re.compile(r"[*?\[]")
_PY_DESTRUCTIVE = re.compile(r"rmtree|shutil|unlink|os\s*\.\s*(remove|rmdir|removedirs|system|popen|exec|spawn)|subprocess|rmdir|__import__")
_PACKED = re.compile(r";|&&|\|\||\n|<<")

SHAPE_RM = ("delete only inside the session scratch directory, naming the LITERAL expanded absolute path "
            "(a `$VAR`, `{a,b}`, backslash or substitution target is refused, not expanded for you), or "
            "`git worktree remove <path>` for a worktree; a fresh scratch directory per run needs no cleanup")
SHAPE_PACKED = ("write the script to a file (the Write tool, or `cat > file <<'EOF'` as its own command) "
                "and run it by name: `bash /abs/path/script.sh`; a single simple command may be passed "
                "to `-c` as one string")
SHAPE_HEREDOC = ("a heredoc may only feed a file: `cat > file <<'EOF'`, `cat >> file`, `tee file`, "
                 "`git commit -F -` or `gh pr create --body-file -`, ONE command, a plain path (not /dev, not /tmp "
                 "except the session scratchpad), a QUOTED delimiter if the body holds `$(`, backticks or `${`, and "
                 "the file not used elsewhere in the same call; write any script to a file and run it by name")
SHAPE_CD = "use `git -C <abs-dir> …` and absolute paths; `cd` moves the session's write boundary"
SHAPE_STRICT = "one simple command per call (no `;`, `&&`, `||`, `|`, `&` or newline)"


class Refuse(Exception):
    def __init__(self, what, shape):
        super().__init__(what)
        self.what, self.shape = what, shape


# ------------------------------------------------------------------------------------ scratch --
def _root_ok(real, cwd):
    """An explicit scratch root must look like a scratch directory: never `/`, `$HOME`, an ancestor of the
    project directory, or a proper ancestor of the cwd - unless it has the session-scratch layout itself."""
    if SCRATCH_PATTERN.match(real.rstrip("/") + "/x"):
        return True
    if real in ("/", os.path.realpath(os.path.expanduser("~"))):
        return False
    pre = real.rstrip("/") + "/"
    proj = os.environ.get("CLAUDE_PROJECT_DIR")
    if proj:
        pr = os.path.realpath(proj)
        if pr == real or pr.startswith(pre):
            return False
    if os.path.realpath(cwd).startswith(pre):
        return False
    return True


def _session_id(payload):
    sid = payload.get("session_id") if isinstance(payload, dict) else None
    sid = sid if isinstance(sid, str) and sid else os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    return sid if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", sid or "") else ""


def scratch_root(payload, cwd=None):
    """(kind, value): ("path", realpath) for an explicit root, ("pattern", session_id) for the layout pattern of THIS session's
    scratchpad (`/tmp/claude-<uid>/<project>/<session_id>/scratchpad/`), ("none", None) for an explicit root that is not a
    plausible scratch directory or a fallback with no session id (every rm is then refused). Another session's scratchpad
    is never admitted by the fallback. The payload's `tool_input` is agent-written and is NEVER a source."""
    cwd = cwd or os.getcwd()
    env = os.environ.get("CLAUDE_SCRATCHPAD", "")
    if env and os.path.isabs(env):
        real = os.path.realpath(env)
        return ("path", real) if _root_ok(real, cwd) else ("none", None)
    if isinstance(payload, dict):
        for key in ("scratchpad", "scratchpad_dir", "scratch_dir"):
            v = payload.get(key)
            if isinstance(v, str) and os.path.isabs(v):
                real = os.path.realpath(v)
                return ("path", real) if _root_ok(real, cwd) else ("none", None)
    sid = _session_id(payload)
    return ("pattern", sid) if sid else ("none", None)


def _literal_prefix(target):
    m = _GLOB.search(target)
    if not m:
        return target
    head = target[:m.start()]
    return head.rsplit("/", 1)[0] + "/" if "/" in head else ""


def inside_scratch(target, cwd, root):
    """True only when `target` is provably strictly inside the scratch root."""
    if not target or any(c in target for c in ("$", "`", "{", "\\")):
        return False
    t = target
    if t.startswith("~"):
        if t != "~" and not t.startswith("~/"):
            return False                                   # ~user: unknowable
        t = os.path.expanduser(t)
    prefix = _literal_prefix(t)
    if _GLOB.search(t) and not prefix:
        return False
    if not os.path.isabs(prefix):
        prefix = os.path.join(cwd, prefix)
    real = os.path.realpath(prefix)
    if _GLOB.search(t):
        if ".." in t.split("/"):
            return False
        real = real.rstrip("/") + "/*"                     # the glob stays under the resolved directory
    kind, base = root
    if kind == "none":
        return False
    if kind == "path":
        return real.startswith(base.rstrip("/") + "/") and real != base
    own = re.compile(r"^/tmp/claude-[^/]+/(?:[^/]+/)*%s/scratchpad/.+$" % re.escape(base))
    return own.match(real) is not None


# ------------------------------------------------------------------------------------ analysis --
def _deletion_targets(args, verb=None):
    """The operands a delete verb names: no options, no option VALUES (`truncate -s 0 f`), no redirections (`2>/dev/null`)."""
    takes = _VERB_TAKES.get(verb, set())
    targets, end_opts, skip = [], False, False
    args = scan.strip_redirects(list(args))
    for a in args:
        if skip:
            skip = False
            continue
        if not end_opts and a == "--":
            end_opts = True
            continue
        if not end_opts and a.startswith("-") and len(a) > 1:
            skip = a in takes
            continue
        targets.append(a)
    return targets


def _check_delete(inv, cwd, root):
    if inv.has_subst or inv.sub:
        raise Refuse("`%s` with a command substitution (its target cannot be known)" % inv.cmd, SHAPE_RM)
    if "xargs" in inv.wrappers:
        raise Refuse("`xargs %s` (its targets arrive on stdin and cannot be known)" % inv.cmd, SHAPE_RM)
    for t in _deletion_targets(inv.args, inv.cmd):
        if not inside_scratch(t, cwd, root):
            raise Refuse("`%s` of %r, which is not provably inside the session scratch directory"
                         % (inv.cmd, t), SHAPE_RM)


def _check_other_deleters(inv, cwd, root):
    """`git clean` and `rsync --delete*` / `--remove-source-files` delete without naming a delete verb."""
    args = scan.strip_redirects(list(inv.args))
    if inv.cmd == "git":
        i = 0
        while i < len(args) and args[i].startswith("-"):      # global options; -C/-c take a value
            i += 2 if args[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") else 1
        if i < len(args) and args[i] == "clean":
            rest = args[i + 1:]
            if not any(a == "--dry-run" or (a.startswith("-") and not a.startswith("--") and "n" in a[1:]) for a in rest):
                raise Refuse("`git clean` (it deletes untracked files; use -n to preview)", SHAPE_RM)
    elif inv.cmd == "rsync" and any(_RSYNC_DELETE.fullmatch(a) for a in args):
        ops = [a for a in args if not a.startswith("-")]
        if inv.has_subst or not ops or any(not inside_scratch(o, cwd, root) for o in ops):
            raise Refuse("`rsync --delete` / `--remove-source-files` with an operand not provably inside the session scratch "
                         "directory", SHAPE_RM)


def _check_find(inv, cwd, root):
    args = inv.args
    risky = SHELLS | DELETE_VERBS | {"eval", "xargs", "env", "sudo", "nohup"}
    destructive = "-delete" in args or any(
        a in ("-exec", "-execdir", "-ok", "-okdir") and i + 1 < len(args) and
        (scan._cmd_basename(args[i + 1]) in risky or re.fullmatch(r"python[0-9.]*", scan._cmd_basename(args[i + 1])))
        for i, a in enumerate(args))
    if not destructive:
        return
    paths = []
    for a in args:
        if a.startswith("-") or a in ("(", "!", ")"):
            break
        paths.append(a)
    if not paths or inv.has_subst:
        raise Refuse("`find … -delete/-exec <rm|shell>` without literal start paths", SHAPE_RM)
    for p in paths:
        if not inside_scratch(p, cwd, root):
            raise Refuse("`find %s … -delete/-exec <rm|shell>` outside the session scratch directory" % p, SHAPE_RM)


_SH_C = re.compile(r"-[A-Za-z]*c[A-Za-z]*")
_PY_C = re.compile(r"-[A-Za-z]*c")
_SH_TAKES = {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}
_PY_TAKES = {"-W", "-X", "-Q"}


def _payload_after_c(args, flag_re, takes):
    """The word following a `-c` flag cluster among the LEADING options (before the script/module word),
    else None. A `-c` after the script name belongs to the script, not the interpreter."""
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--" or not a.startswith(("-", "+")) or a == "-":
            return None
        if flag_re.fullmatch(a):
            return args[i + 1] if i + 1 < len(args) else ""
        i += 2 if a in takes else 1
    return None


def _leading_flag(args, takes, flag):
    """True when `flag` is among the interpreter's LEADING options (before the script word)."""
    i = 0
    while i < len(args):
        a = args[i]
        if a == flag:
            return True
        if not a.startswith(("-", "+")) or a in ("-", "--"):
            return False
        i += 2 if a in takes else 1
    return False


def _leading_dash(args, takes):
    """True when the interpreter's script operand is `-` (stdin): a bare `-` among the leading options."""
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-":
            return True
        if not a.startswith(("-", "+")) or a == "--":
            return False
        i += 2 if a in takes else 1
    return False


_EVAL_INTERPRETERS = {"perl", "ruby", "node", "nodejs", "php", "lua", "bun", "deno"}
_EVAL_FLAGS = {"--eval", "--print", "-r", "--exec"}


def _leading_options(args):
    """The interpreter's options before its first operand."""
    out = []
    for a in args:
        if not a.startswith("-") or a == "-":
            break
        out.append(a)
    return out


def _check_packed(inv, depth, cwd, root, strict_mode):
    cmd, args = inv.cmd, inv.args
    if cmd in SHELLS:
        payload = _payload_after_c(args, _SH_C, _SH_TAKES)
        if payload is not None:
            if inv.has_subst or "$(" in payload or "`" in payload:
                raise Refuse("`%s -c` with a command substitution in its payload" % cmd, SHAPE_PACKED)
            for sub in scan.invocations(payload):
                if sub.cmd.startswith("$"):
                    raise Refuse("`%s -c` whose command is an expansion (%s), which cannot be judged" % (cmd, sub.cmd), SHAPE_PACKED)
            _judge_payload(cmd + " -c", payload, depth, cwd, root, strict_mode)
            return
        if not args or _leading_flag(args, _SH_TAKES, "-s") or _leading_dash(args, _SH_TAKES):
            raise Refuse("`%s` reading a script from stdin" % cmd, SHAPE_PACKED)
    elif cmd == "eval":
        payload = " ".join(args)
        if inv.has_subst or "$" in payload or "`" in payload:
            raise Refuse("`eval` of an expansion (its payload cannot be known)", SHAPE_PACKED)
        _judge_payload("eval", payload, depth, cwd, root, strict_mode)
    elif re.fullmatch(r"python[0-9.]*", cmd):
        if _leading_dash(args, _PY_TAKES):
            raise Refuse("`%s -` (a script on stdin)" % cmd, SHAPE_PACKED)
        payload = _payload_after_c(args, _PY_C, _PY_TAKES)
        if payload is not None:
            _judge_payload(cmd + " -c", payload, depth, cwd, root, strict_mode, python=True)
        elif not [a for a in scan.strip_redirects(list(args)) if not a.startswith("-")] and "-m" not in args and "--version" not in args:
            raise Refuse("`%s` with no script operand (it reads the script from stdin, e.g. `cat s.py | %s`)" % (cmd, cmd), SHAPE_PACKED)
    elif cmd in _EVAL_INTERPRETERS:
        lead = _leading_options(args)
        if any(a in _EVAL_FLAGS for a in lead) or any(re.fullmatch(r"-[A-Za-z]*[eEp][A-Za-z]*", a) for a in lead if not a.startswith("--")):
            raise Refuse("`%s` with an inline script (-e/-E/--eval/-p)" % cmd, SHAPE_PACKED)
        if not [a for a in scan.strip_redirects(list(args)) if not a.startswith("-")]:
            raise Refuse("`%s` with no script operand (it reads the script from stdin)" % cmd, SHAPE_PACKED)


def _judge_payload(what, payload, depth, cwd, root, strict_mode, python=False):
    if _PACKED.search(payload):
        raise Refuse("packed `%s` script (a payload with ;, &&, ||, a newline or a heredoc)" % what, SHAPE_PACKED)
    if python:
        if _PY_DESTRUCTIVE.search(payload):
            raise Refuse("a python -c payload that deletes files or runs commands", SHAPE_PACKED)
        return              # a one-statement python payload is not shell text
    if depth + 1 > MAX_DEPTH:
        raise Refuse("`%s` nested more than %d deep" % (what, MAX_DEPTH), SHAPE_PACKED)
    analyze(payload, depth + 1, cwd, root, strict_mode)


def analyze(command, depth, cwd, root, strict):
    if scan.has_code_heredoc(command):
        raise Refuse("a heredoc the scanner classifies as code (not a closed file sink)", SHAPE_HEREDOC)
    if strict:
        clauses = {w.clause_index for w in scan.tokenize(command) if w.kind in ("word", "cmd_sub", "proc_sub")}
        if len(clauses) > 1:
            raise Refuse("more than one command on the line (strict mode)", SHAPE_STRICT)
    for inv in scan.invocations(command):
        if inv.cmd in ("cd", "pushd", "popd"):
            raise Refuse("`%s` as a command" % inv.cmd, SHAPE_CD)
        if "env-S" in inv.wrappers:
            raise Refuse("`env -S` (it runs a command string that cannot be judged)", SHAPE_PACKED)
        if inv.cmd == "function" or "()" in inv.cmd or (inv.args and inv.args[0].startswith("()")):
            raise Refuse("a shell function definition (its body cannot be judged where it is called)", SHAPE_PACKED)
        if inv.cmd in DELETE_VERBS:
            _check_delete(inv, cwd, root)
        elif inv.cmd == "find":
            _check_find(inv, cwd, root)
        elif inv.cmd in ("git", "rsync"):
            _check_other_deleters(inv, cwd, root)
        _check_packed(inv, depth, cwd, root, strict)


# ------------------------------------------------------------------------------------ main --
def decide(payload, command, strict):
    """None = admit; a Refuse = deny."""
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) and payload.get("cwd") else os.getcwd()
    try:
        analyze(command, 0, cwd, scratch_root(payload, cwd), strict)
    except Refuse as r:
        return r
    return None


def deny(r):
    reason = "shell-shape refused: %s. Allowed shape: %s." % (r.what, r.shape)
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                               "permissionDecisionReason": reason},
        "observation": {"status": "blocked", "guard": GUARD, "reason": r.what, "remediation": r.shape},
    }) + "\n")
    sys.stderr.write(reason + "\n")
    return 2


def main(argv):
    strict = os.environ.get("FOUNDRY_SHELL_SHAPE_STRICT") == "1" or "--strict" in argv
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    command = (payload.get("tool_input") or {}).get("command") if isinstance(payload, dict) and isinstance(
        payload.get("tool_input"), dict) else None
    if not isinstance(command, str) or not command.strip():
        return 0
    try:
        global scan
        sys.path.insert(0, os.path.join(_PLUGIN_ROOT, "scripts"))
        import foundry_shell_scan as scan
        r = decide(payload, command, strict)
    except Exception as e:      # fail closed once a command is in hand
        sys.stderr.write("shell-shape refused: internal error (%s: %s); failing closed. Allowed shape: %s.\n"
                         % (type(e).__name__, e, SHAPE_PACKED))
        return 2
    return deny(r) if r is not None else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
