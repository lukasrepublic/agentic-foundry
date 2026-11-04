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
  * `cd` / `pushd` / `popd` as a command (use `git -C <abs-dir> …` and absolute paths).

SCRATCH RESOLUTION ORDER (first that exists wins; later ones are NOT consulted once an earlier one is set):
  1. `$CLAUDE_SCRATCHPAD`                           (the hook process's environment, an absolute path)
  2. the harness-provided path in the hook payload  (`scratchpad` / `scratchpad_dir` / `scratch_dir`,
                                                      top level or under `tool_input`)
  3. the pattern `/tmp/claude-*/**/scratchpad/`      (the Claude Code per-session scratch layout)
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
DELETE_VERBS = {"rm", "unlink", "shred", "rmdir"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
SCRATCH_PATTERN = re.compile(r"^/tmp/claude-[^/]+/(?:[^/]+/)+scratchpad/.+$")
_GLOB = re.compile(r"[*?\[]")
_PACKED = re.compile(r";|&&|\|\||\n|<<")

SHAPE_RM = ("delete only inside the session scratch directory (use an absolute path under "
            "$CLAUDE_SCRATCHPAD, or `git worktree remove <path>` for a worktree); a fresh scratch "
            "directory per run needs no cleanup")
SHAPE_PACKED = ("write the script to a file (the Write tool, or `cat > file <<'EOF'` as its own command) "
                "and run it by name: `bash /abs/path/script.sh`; a single simple command may be passed "
                "to `-c` as one string")
SHAPE_HEREDOC = ("a heredoc may only feed a file (`cat > notes.txt <<'EOF' … EOF`, one command, the file "
                 "not run in the same call); write any script to a file and run it by name")
SHAPE_CD = "use `git -C <abs-dir> …` and absolute paths; `cd` moves the session's write boundary"
SHAPE_STRICT = "one simple command per call (no `;`, `&&`, `||`, `|`, `&` or newline)"


class Refuse(Exception):
    def __init__(self, what, shape):
        super().__init__(what)
        self.what, self.shape = what, shape


# ------------------------------------------------------------------------------------ scratch --
def scratch_root(payload):
    """(kind, value): ("path", realpath) for an explicit root, ("pattern", None) for the layout pattern."""
    env = os.environ.get("CLAUDE_SCRATCHPAD", "")
    if env and os.path.isabs(env):
        return "path", os.path.realpath(env)
    if isinstance(payload, dict):
        tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        for src in (payload, tool_input):
            for key in ("scratchpad", "scratchpad_dir", "scratch_dir"):
                v = src.get(key)
                if isinstance(v, str) and os.path.isabs(v):
                    return "path", os.path.realpath(v)
    return "pattern", None


def _literal_prefix(target):
    m = _GLOB.search(target)
    if not m:
        return target
    head = target[:m.start()]
    return head.rsplit("/", 1)[0] + "/" if "/" in head else ""


def inside_scratch(target, cwd, root):
    """True only when `target` is provably strictly inside the scratch root."""
    if not target or any(c in target for c in ("$", "`")):
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
    if kind == "path":
        return real.startswith(base.rstrip("/") + "/") and real != base
    return SCRATCH_PATTERN.match(real) is not None


# ------------------------------------------------------------------------------------ analysis --
def _deletion_targets(args):
    targets, end_opts = [], False
    for a in args:
        if not end_opts and a == "--":
            end_opts = True
            continue
        if not end_opts and a.startswith("-") and len(a) > 1:
            continue
        targets.append(a)
    return targets


def _check_delete(inv, cwd, root):
    if inv.has_subst or inv.sub:
        raise Refuse("`%s` with a command substitution (its target cannot be known)" % inv.cmd, SHAPE_RM)
    if "xargs" in inv.wrappers:
        raise Refuse("`xargs %s` (its targets arrive on stdin and cannot be known)" % inv.cmd, SHAPE_RM)
    for t in _deletion_targets(inv.args):
        if not inside_scratch(t, cwd, root):
            raise Refuse("`%s` of %r, which is not provably inside the session scratch directory"
                         % (inv.cmd, t), SHAPE_RM)


def _check_find(inv, cwd, root):
    args = inv.args
    destructive = "-delete" in args or any(a in ("-exec", "-execdir", "-ok", "-okdir") and
                                           i + 1 < len(args) and
                                           scan._cmd_basename(args[i + 1]) in DELETE_VERBS
                                           for i, a in enumerate(args))
    if not destructive:
        return
    paths = []
    for a in args:
        if a.startswith("-") or a in ("(", "!", ")"):
            break
        paths.append(a)
    if not paths or inv.has_subst:
        raise Refuse("`find … -delete/-exec rm` without literal start paths", SHAPE_RM)
    for p in paths:
        if not inside_scratch(p, cwd, root):
            raise Refuse("`find %s … -delete/-exec rm` outside the session scratch directory" % p, SHAPE_RM)


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


def _check_packed(inv, depth, cwd, root, strict_mode):
    cmd, args = inv.cmd, inv.args
    if cmd in SHELLS:
        payload = _payload_after_c(args, _SH_C, _SH_TAKES)
        if payload is not None:
            if inv.has_subst or "$(" in payload or "`" in payload:
                raise Refuse("`%s -c` with a command substitution in its payload" % cmd, SHAPE_PACKED)
            _judge_payload(cmd + " -c", payload, depth, cwd, root, strict_mode)
            return
        if not args or "-s" in args or _leading_dash(args, _SH_TAKES):
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


def _judge_payload(what, payload, depth, cwd, root, strict_mode, python=False):
    if _PACKED.search(payload):
        raise Refuse("packed `%s` script (a payload with ;, &&, ||, a newline or a heredoc)" % what, SHAPE_PACKED)
    if python:
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
        if inv.cmd in DELETE_VERBS:
            _check_delete(inv, cwd, root)
        elif inv.cmd == "find":
            _check_find(inv, cwd, root)
        _check_packed(inv, depth, cwd, root, strict)


# ------------------------------------------------------------------------------------ main --
def decide(payload, command, strict):
    """None = admit; a Refuse = deny."""
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) and payload.get("cwd") else os.getcwd()
    try:
        analyze(command, 0, cwd, scratch_root(payload), strict)
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
