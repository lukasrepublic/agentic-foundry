#!/usr/bin/env bash
# foundry-git-discipline — the deterministic, no-bypass PreToolUse(Bash) destructive-op
# guard (ER #42). A fail-closed mistake-catcher for the COMMON, DIRECT irreversible git
# ops an erratic-but-trusted agent can emit, BEFORE they execute. It reads the Bash
# tool-input `command` string and exits 2 (BLOCK) when that string contains a member of
# the ACTIVE destructive-op set at ANY command position (env-prefix / wrapper / quoting /
# compound). It exits 0 (ADMIT) otherwise.
#
# DEFAULT (wired-on) active set = the IRREVERSIBLE tier, exactly:
#   (a) force-push to a PROTECTED branch  — `git push` with force intent (a --force/-f/
#       --force-with-lease flag OR a leading-`+` refspec) whose RESOLVED destination is —
#       or cannot be PROVEN not to be — a protected branch (refspec-aware; see below).
#   (d) `git branch -D` / `--delete --force` of a protected branch
#   (e) `git filter-repo`
#   (f) `git filter-branch`
#   (g) `rm -rf .git`  (history/repo destruction)
#   (h) `git commit --no-verify` / `-n`  (other-hook bypass)
#   (i) `gh pr merge` — (floor-#3 review B2) the outcome-level replacement for
#       the deleted merge-gate PreToolUse hook's block: `--admin` (server-side-check bypass)
#       anywhere in the argv is refused OUTRIGHT; a plain `gh pr merge` is admitted ONLY when
#       a live `gh pr checks` query reports every check passing (non-zero exit, a fail/pending
#       row, or a query error/timeout all BLOCK, fail-closed). This is the ONE clause in this
#       hook that executes anything beyond a static token scan — see its own comment below.
# The two reflog-recoverable history ops `git reset --hard` and `git rebase -i` are NOT in
# the default set; they are the `strict_history_ops` OPT-IN widening (--strict-history),
# which ADDS them to the active set (a widening, never a relaxation).
#
# VERB MATCHING (what "at ANY command position" actually covers) — the guarded verbs (`git`,
# `gh`, `rm`) are matched by `_is_verb()` below, which resolves a PATH-QUALIFIED invocation to
# its final path segment: `/usr/bin/git`, `./git`, `../bin/git`, `~/bin/git` and `bin/git` are
# all `git`. Exact token equality alone let EVERY clause be bypassed at once (`/usr/bin/git
# push --force origin main` was ADMITTED while the bare form blocked), because the path prefix
# carries no security meaning. Quoted (`"git"`) and backslash-escaped (`\git`) forms are
# normalized by shlex before the scan sees them. Matching is on the WHOLE final segment, never
# a substring, so `gitlab-runner` / `github-cli` / `git-lfs` / `gitk` are correctly not matched.
# NOT covered: resolving PATH to discover some other NAME is really this binary (a copied or
# symlinked `mygit`) — that would trade a bounded token scan for unbounded filesystem
# inspection, and is out of scope by design.
#
# THREAT MODEL — trusted operator, honest limit: an AGENT-FALLIBILITY mistake-catcher, NOT
# an unbypassable sandbox. "NO bypass" = there is NO in-session CONFIG off-switch (no
# per-invocation flag, no env var, no session-writable file) that downgrades a BLOCK to an
# ADMIT. It does NOT mean "no command can evade it": a literal-string scan cannot see
# through shell indirection (`bash -c …`, `eval`, `$(…)`, variable indirection, aliases,
# write-script-then-run) or reach non-Bash channels — those are acknowledged BOUNDED
# RESIDUALS (spec §8), not closed holes. Path-qualified invocation used to belong on that
# list; it no longer does (see VERB MATCHING above) — an absolute path is an ordinary thing
# for an agent to emit, not an unusual construction, so it was a realistic mistake-path rather
# than a deliberate-evasion residual.
#
# CONFIG SOURCE (load-bearing) — the protected-branch list + the strict_history_ops opt-in
# are read ONLY from this hook's COMMAND-LINE ARGUMENTS as set in hooks.json (--protected /
# --strict-history). hooks.json ships with the plugin; no mechanical integrity check covers
# it today — the retired pin/manifest stack that once did was removed in the v0.24.0
# subtraction and nothing replaced it — a change to it is ROUTED to review by the security-path
# lane (^hooks/), which is advisory on a Tier B repo: it reports, it does not block, and a
# direct push or API write is not gated at all. They are NOT read from any
# environment variable (env is the bypass surface we must ignore) and NOT from any
# session-writable file. `main` is ALWAYS protected and --protected may only ADD branches
# (widen-only by construction).
#
# FAIL-CLOSED BOUNDARY (precise): a STDIN payload from which NO command is recoverable
# (unparseable / non-Bash-shaped / command absent/non-string/empty) => exit 0 (nothing to
# guard — the admit-on-no-command precedent every PreToolUse hook in this plugin follows).
# But once a command IS recovered AND an active-set destructive token is present, ANY
# subsequent internal error => exit 2 (BLOCK, fail-closed).
set -uo pipefail

# ----------------------------------------------------------------------------------------
# Config (ARGUMENTS ONLY — never env, never a session file). `main` is always protected.
# ----------------------------------------------------------------------------------------
PROTECTED="main"          # comma-joined; `main` is always a member (widen-only)
STRICT_HISTORY=0          # strict_history_ops opt-in (adds reset --hard + rebase -i)

while [ $# -gt 0 ]; do
  case "$1" in
    --protected)
      # next arg is a comma-separated list; UNION with the always-protected `main`.
      shift
      if [ $# -gt 0 ]; then PROTECTED="main,$1"; fi
      ;;
    --protected=*)
      PROTECTED="main,${1#--protected=}"
      ;;
    --strict-history)
      STRICT_HISTORY=1
      ;;
    *)
      # Unknown args are ignored (NO --allow / --force / off-switch is honored; there is no
      # argument that downgrades a BLOCK to an ADMIT — AC-GITGUARD-3).
      ;;
  esac
  shift
done

# ----------------------------------------------------------------------------------------
# Recover the command string. NO recoverable command => exit 0 (nothing to guard).
# ----------------------------------------------------------------------------------------
payload="$(cat 2>/dev/null || true)"
cmd="$(printf '%s' "$payload" | python3 -c 'import sys,json
try:
    c = json.load(sys.stdin).get("tool_input", {}).get("command", "")
    print(c if isinstance(c, str) else "")
except Exception:
    print("")' 2>/dev/null || true)"

# No command recoverable (empty / unparseable / absent / non-string) => admit.
[ -z "$cmd" ] && exit 0

# ----------------------------------------------------------------------------------------
# The decision is delegated to a single python3 evaluator over the recovered command. It is
# a PURE token scan (no shell execution of the command). Force intent is refspec-aware. The
# evaluator prints "BLOCK <reason>" or "ALLOW". Per the fail-closed boundary, any internal
# error here (the python3 evaluator failing while a command IS recovered) => exit 2.
# ----------------------------------------------------------------------------------------
# bash-3.2 parse compat: the heredoc lives INSIDE a function body, never inside `$(...)` —
# bash 3.2's command-substitution scanner mis-tracks backquotes/quotes across heredoc content
# (feat-foundry-bash32-parse-guard), so the substitution below contains only the function call.
_git_discipline_eval() {
  PROTECTED="$PROTECTED" STRICT_HISTORY="$STRICT_HISTORY" CMD="$cmd" python3 - <<'PY'
import os, re, shlex, subprocess, sys

cmd = os.environ.get("CMD", "")
protected = {b.strip() for b in os.environ.get("PROTECTED", "main").split(",") if b.strip()}
protected.add("main")                                   # always protected (widen-only)
strict = os.environ.get("STRICT_HISTORY", "0") == "1"

# Refusal messages echo the offending command (and, for the gh clause, the check query's output)
# into the agent transcript. Since an inline `GH_TOKEN=<pat> gh pr merge …` is a supported form,
# redact secret-bearing assignments centrally here so EVERY clause inherits it.
_SECRET_ASSIGN = re.compile(
    r"(?i)\b([A-Za-z_][A-Za-z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|APIKEY|API_KEY|_KEY|_PAT))=(\S+)")

def _redact(s):
    return _SECRET_ASSIGN.sub(r"\1=<redacted>", s)

def block(reason):
    print("BLOCK " + _redact(reason))
    sys.exit(0)

def allow():
    print("ALLOW")
    sys.exit(0)

# --- Connector normalization (HEURISTIC, NOT a shell parser). shlex.split does NOT treat
# shell connectors (`&&`, `||`, `;`, `|`, `&`) as token boundaries, so a separator GLUED to
# an adjacent token with no surrounding space (`echo;git push --force`, `true&&git branch -D
# main`, `foo|git filter-repo`) would FUSE the destructive verb into one token (`echo;git`)
# and hide it from the git-clause loop → FAIL-OPEN. We pre-insert spaces around the
# connectors so shlex yields them as standalone SEPARATOR tokens (the clause loop already
# bounds clauses on them). This is a literal-string static scan — it does NOT see through
# shell indirection (`bash -c "…"`, `eval`, `$(…)`, variable indirection), which remain the
# spec's honest §8 BOUNDED RESIDUALS, not closed holes. Two-char operators are spaced before
# the single-char ones so `&&`/`||` are not shredded into `& &` / `| |`. ---
norm = cmd
# A backslash LINE CONTINUATION is removed by the shell BEFORE it parses words, so it must be
# removed here FIRST — before the newline rule below turns it into a separator. Two defects
# otherwise, both verified: `gi\<newline>t push --force origin main` executes `git push --force`
# while the scan sees tokens `["gi ;", "t", …]` and ADMITS (fail-open); and an ordinary
# `cd /repo && \<newline>  gh pr merge …` produced a bogus `" ;"` token that the gh clause's
# context binding reported as an unrecognized token preceding `gh` (a false BLOCK).
norm = re.sub(r"\\\r?\n", "", norm)     # DELETED, not spaced — bash splices the word back together

# --- Heredoc bodies are DATA, not commands (CONVICT-SAFE excision) ---------------------
# `cat > f <<EOF … EOF` feeds its body to a program's STDIN; the shell never parses those lines
# as commands. Without this, the newline rule below turned every body line into its own clause,
# so merely WRITING a file that MENTIONED a blocked command was refused as though it ran one —
# a false BLOCK on authoring a doc, a commit message or a test fixture. This guard's own source
# is full of the strings it refuses, so the guard forbade editing itself.
#
# EXONERATION IS THE FAIL-OPEN DIRECTION (the convict/exonerate asymmetry: a crude rule may
# CONVICT, but only a careful one may rule out), so BOTH conditions must hold or the body stays
# in the scan exactly as it was:
#   1. the consuming command is a known-INERT sink — one that treats stdin as data and can never
#      execute it. `bash <<EOF` / `sh` / `python3 <<EOF` / `awk -f -` DO execute their body. A
#      sink that is UNLISTED or UNIDENTIFIABLE is treated as executing: unknown CONVICTS.
#   2. the body cannot expand into a command — the delimiter is QUOTED (`<<'EOF'`, on which bash
#      performs no expansion at all), or the body contains no `$(`, backtick or `${`.
# Command substitution stays an acknowledged §8 residual either way; condition 2 exists so that
# this excision can never WIDEN it.
INERT_HEREDOC_SINKS = {"cat", "tee", "head", "tail", "wc", "sort", "uniq", "git", "gh"}
_SEG_BREAK = re.compile(r"\|\||&&|[;|&\n]")

def _first_word(seg):
    """Basename of the command a segment invokes, skipping leading VAR=val assignments. Returns
    "" when unidentifiable — which every caller treats as NOT inert."""
    for w in seg.split():
        if w.startswith("-"):
            return ""                                  # a flag before any verb: give up, convict
        if "=" in w.split("/")[-1]:
            continue                                   # leading VAR=val assignment
        return w.rsplit("/", 1)[-1].lower()            # /bin/cat -> cat
    return ""

def _heredoc_pipeline_is_inert(prefix, opener_line_tail):
    """EVERY command the body flows THROUGH must be inert, not just the one the opener is
    attached to. `cat <<EOF | bash` hands the body to cat, whose STDOUT bash then EXECUTES —
    checking the attached command alone exonerated a body that really does run (caught by the
    adversarial pass on this very change).

    The check is deliberately asymmetric, because the data flow is. UPSTREAM of the opener
    nothing sees the body (`bash | cat <<EOF` — cat's stdin is the heredoc, so bash's output is
    discarded), so the prefix is cut at the nearest separator INCLUDING `|`. DOWNSTREAM every
    pipeline member receives it, so the whole rest of the opener's line is taken UNSPLIT and
    every `|`-separated command in it must be inert. Taking it unsplit over-includes commands
    after a `;` or `&&`, which never see the body — that only makes the test STRICTER, and
    strictness is the safe direction here."""
    head = _SEG_BREAK.split(prefix)[-1]
    return all(_first_word(part) in INERT_HEREDOC_SINKS
               for part in (head + " " + opener_line_tail).split("|"))

def _excise_inert_heredoc_bodies(s):
    opener = re.compile(r"<<-?[ \t]*(?:'([^']*)'|\"([^\"]*)\"|([A-Za-z_][A-Za-z0-9_]*))")
    pos = 0
    while True:
        m = opener.search(s, pos)
        if m is None:
            return s
        if m.start() > 0 and s[m.start() - 1] == "<":
            pos = m.end()                              # `<<<` here-STRING, not a here-doc
            continue
        quoted = m.group(1) is not None or m.group(2) is not None
        delim = m.group(1) or m.group(2) or m.group(3)
        nl = s.find("\n", m.end())
        if nl == -1:
            return s                                   # an opener with no body on this input
        closer = re.compile(r"^[ \t]*" + re.escape(delim) + r"[ \t]*$", re.M).search(s, nl + 1)
        if closer is None:
            return s                                   # UNTERMINATED: leave every line visible
        body = s[nl + 1:closer.start()]
        inert = _heredoc_pipeline_is_inert(s[:m.start()], s[m.end():nl])
        expandable = (not quoted) and re.search(r"\$\(|`|\$\{", body) is not None
        if inert and not expandable:
            s = s[:nl + 1] + s[closer.end():]          # drop the body AND its delimiter line
            pos = nl + 1
        else:
            pos = closer.end()

norm = _excise_inert_heredoc_bodies(norm)
# A NEWLINE bounds a clause exactly as `;` does. shlex discards newlines as plain whitespace, so
# without this a multi-line script is ONE unbounded clause: `clause_start` walks back to index 0
# and the per-clause context binding below (cwd / inline env) reads tokens from unrelated lines.
norm = re.sub(r"[\r\n]+", " ; ", norm)
norm = re.sub(r"&&", " && ", norm)
norm = re.sub(r"\|\|", " || ", norm)
norm = re.sub(r";", " ; ", norm)
# single `|` / `&` not already part of a (now-spaced) `&&`/`||`: the two-char forms above are
# already surrounded by spaces, so a remaining bare `|`/`&` is a genuine pipe / background op.
norm = re.sub(r"\|", " | ", norm)
norm = re.sub(r"&", " & ", norm)

# --- Tokenize. shlex strips quotes (handles `"--force"`, `'rebase'`, --fo"rce" splice is a
# residual, not in scope). On a shlex failure (unbalanced quotes etc.) fall back to a
# whitespace split so detection still runs (never silently stop scanning). ---
try:
    toks = shlex.split(norm, posix=True)
except Exception:
    # DEGRADED PATH — reached only when shlex refuses the string (an unbalanced quote, e.g. an
    # apostrophe inside a trailing `# comment` that bash ignores entirely). A plain split would
    # PRESERVE quotes, so `"git" push --force origin main # don't` yielded the token '"git"',
    # matched no verb, and ADMITTED a force-push bash really does run. Strip quote characters
    # outright here: this scan's only job is detection, the input is already known-malformed, and
    # over-matching is the safe direction for a guard that must never silently stop scanning.
    toks = [t.replace('"', "").replace("'", "") for t in norm.split()]

low = [t.lower() for t in toks]
n = len(toks)

def strip_dst_ref(ref):
    """Resolve a refspec/ref to its destination BRANCH NAME (or '' if unresolvable/HEAD).
    RHS of <src>:<dst> (drop leading +), else the bare ref (drop leading +), normalizing
    refs/heads/<b>. HEAD / empty => '' (unknown => caller treats as protected)."""
    if not ref:
        return ""
    if ref.startswith("+"):
        ref = ref[1:]
    if ":" in ref:
        ref = ref.split(":", 1)[1]                      # destination side
    if ref.startswith("refs/heads/"):
        ref = ref[len("refs/heads/"):]
    if ref in ("", "HEAD"):
        return ""                                       # current branch — unknown to a scan
    return ref

# --- Locate `git <subcommand>` clauses at ANY position. A clause is a `git` token (with an
# optional env-prefix / wrapper before it already handled because we scan ALL positions)
# followed by its first non-option subcommand token. We also handle env-prefix `X=y git …`
# and wrappers `sudo`/`time`/`env git …` implicitly: we just look for the literal `git`
# token anywhere, then read forward to its subcommand. Compound separators (&&, ;, |) are
# ordinary tokens that simply bound a clause's argument run. ---
SEPARATORS = {"&&", "||", ";", "|", "&"}


def _is_verb(tok, verb):
    """True when `tok` invokes `verb`, however it is spelled.

    Exact token equality alone let EVERY clause be bypassed at once by a path-qualified
    invocation — `/usr/bin/git push --force origin main`, `./git`, `~/bin/git`, `/bin/rm -rf
    .git` — because the path prefix carries no security meaning but the matcher treated it as
    identity. Quoted (`"git"`) and backslash-escaped (`\\git`) forms never had this problem;
    shlex normalises those before we see them.

    Matching is on the WHOLE final path segment, never a substring, so `gitlab-runner`,
    `github-cli`, `git-lfs` and `gitk` are correctly NOT this verb. Resolving PATH to discover
    that some other name is really this binary is deliberately out of scope (it would trade a
    bounded token scan for unbounded filesystem inspection); shell indirection (`bash -c`,
    `eval`, `$(…)`, variable indirection) remains the declared BOUNDED RESIDUAL it always was.
    """
    if tok == verb:
        return True
    return "/" in tok and tok.rsplit("/", 1)[1] == verb

def clause_args(start):
    """Return the argument tokens of the git clause whose `git` token is at index `start`,
    i.e. everything after `git` up to the next shell separator."""
    out = []
    i = start + 1
    while i < n:
        if toks[i] in SEPARATORS:
            break
        out.append(toks[i])
        i += 1
    return out

# Iterate every `git` token (position-independent — env-prefix/wrapper/compound all fall
# out of scanning all indices).
for i, t in enumerate(low):
    if not _is_verb(t, "git"):
        continue
    args = clause_args(i)
    largs = [a.lower() for a in args]
    if not args:
        continue
    # subcommand = first non-option token (skip leading -c/--git-dir-style globals + their
    # values conservatively: any token starting with '-' is treated as an option; a `-c k=v`
    # value would be a bare token, but we only need the first BARE non-option as subcommand).
    sub = None
    sub_idx = None
    j = 0
    while j < len(args):
        a = args[j]
        if a.startswith("-"):
            # global option; skip. (`-c key=val` value follows as a bare token — to avoid
            # mis-reading it as the subcommand we skip ONE following bare token for the
            # known value-taking globals.)
            if a in ("-c", "--git-dir", "--work-tree", "-C", "--namespace") and j + 1 < len(args):
                j += 2
                continue
            j += 1
            continue
        sub = a.lower()
        sub_idx = j
        break
    if sub is None:
        continue
    rest = args[sub_idx + 1:]
    lrest = [r.lower() for r in rest]

    # (a) force-push — refspec-aware.
    if sub == "push":
        force_flag = any(r in ("--force", "-f", "--force-with-lease") or
                         r.startswith("--force-with-lease=") for r in lrest)
        # bare refs (non-option tokens after `push`); the FIRST is the remote, the rest are
        # refspecs. A leading-`+` on a refspec is ALSO force intent.
        bare = [r for r in rest if not r.startswith("-")]
        # drop the remote (first bare token) if present; remaining are refspecs.
        refspecs = bare[1:] if len(bare) >= 1 else []
        plus_force = any(rs.startswith("+") for rs in refspecs)
        force_intent = force_flag or plus_force
        if not force_intent:
            continue                                    # not a force push → not this op
        # Resolve destination(s). No refspec / HEAD / unparseable => current branch =>
        # UNKNOWN => assume protected (BLOCK). Otherwise BLOCK iff any resolved dst is
        # protected OR unresolvable; ADMIT only if EVERY resolved dst is provably
        # non-protected.
        if not refspecs:
            block("force-push with no refspec (destination = current branch, unknown to a "
                  "string scan) => assumed protected (fail-closed). Command: " + cmd)
        for rs in refspecs:
            dst = strip_dst_ref(rs)
            if dst == "":                               # HEAD / unparseable => unknown
                block("force-push to an unresolvable/HEAD destination (assumed protected, "
                      "fail-closed). Command: " + cmd)
            if dst in protected:
                block("force-push to PROTECTED branch %r. Command: %s" % (dst, cmd))
        # every refspec resolved to a provably-non-protected branch => admit this clause.
        continue

    # (d) branch -D / --delete --force of a protected branch.
    if sub == "branch":
        # force-delete intent = -D (force delete), or (--delete AND --force), or a short
        # combined flag containing an UPPERCASE D (e.g. -D, -Dr). `-d` (lowercase, safe
        # delete) is NOT force-delete and not in the irreversible tier.
        force_del = ("--delete" in lrest and "--force" in lrest)
        for r in rest:
            if r.startswith("-") and not r.startswith("--") and "D" in r:   # case-sensitive: -D
                force_del = True
        if force_del:
            targets = [r for r in rest if not r.startswith("-")]
            for tgt in targets:
                nm = tgt[len("refs/heads/"):] if tgt.startswith("refs/heads/") else tgt
                if nm in protected:
                    block("force-delete (branch -D) of PROTECTED branch %r. Command: %s" % (nm, cmd))
        continue

    # (e)/(f) filter-repo / filter-branch — literal subcommands.
    if sub == "filter-repo":
        block("git filter-repo (history rewrite). Command: " + cmd)
    if sub == "filter-branch":
        block("git filter-branch (history rewrite). Command: " + cmd)

    # (h) commit --no-verify / -n.
    if sub == "commit":
        if "--no-verify" in lrest or "-n" in lrest:
            block("git commit --no-verify (other-hook bypass). Command: " + cmd)
        for r in lrest:
            if r.startswith("-") and not r.startswith("--") and "n" in r[1:]:
                # short-combined flag containing n (e.g. -mn) — conservative block.
                block("git commit with combined -n (--no-verify bypass). Command: " + cmd)
        continue

    # strict_history_ops opt-in: reset --hard / rebase -i.
    if sub == "reset":
        if strict and "--hard" in lrest:
            block("git reset --hard (strict_history_ops enabled). Command: " + cmd)
        continue
    if sub == "rebase":
        if strict and ("-i" in lrest or "--interactive" in lrest):
            block("git rebase -i (strict_history_ops enabled). Command: " + cmd)
        continue

# (g) rm -rf .git — NOT necessarily a `git` clause. Scan every `rm` token.
for i, t in enumerate(low):
    if not _is_verb(t, "rm"):
        continue
    args = clause_args(i)
    largs = [a.lower() for a in args]
    # recursive + force flagset: -rf / -fr / -r -f / --recursive --force (any combination).
    has_r = any(a == "-r" or a == "--recursive" or a == "-rf" or a == "-fr" or
                (a.startswith("-") and not a.startswith("--") and "r" in a) for a in largs)
    has_f = any(a == "-f" or a == "--force" or a == "-rf" or a == "-fr" or
                (a.startswith("-") and not a.startswith("--") and "f" in a) for a in largs)
    if not (has_r and has_f):
        continue
    # target is `.git` (the common literal component; path-form variants are residuals).
    for a in args:
        if a.startswith("-"):
            continue
        base = a.rstrip("/")
        if base == ".git" or base.endswith("/.git") or base == "./.git":
            block("rm -rf .git (repo/history destruction). Command: " + cmd)

# (i) `gh pr merge` — (floor-#3 review B2) the outcome-level replacement for the
# deleted merge-gate PreToolUse hook's block. The native floor (ci.yml + btb-gates) is Tier B
# ADVISORY on Tier-B repos (no server-enforced required-status), so it cannot itself refuse a
# merge — this clause is the deterministic backstop. UNLIKE every other clause above, this one
# is NOT a pure token scan: it actually RUNS `gh pr checks` to query the REAL, current check
# status, because the floor being enforced here is "every check is actually green right now",
# which no string pattern over the command itself could ever prove or disprove.
for i, t in enumerate(low):
    if not _is_verb(t, "gh"):
        continue
    args = clause_args(i)
    largs = [a.lower() for a in args]
    # bare (non-option) tokens, skipping the VALUE of the one value-taking global gh flag we
    # know about (--repo/-R owner/repo) so it can't masquerade as the "pr" subcommand token —
    # any OTHER value-taking global flag interposed before `pr merge` is a bounded residual
    # (mirrors the git-clause conservatism of this file itself, not a closed hole).
    # `gh` is cobra/pflag. Enumerating literal flag SPELLINGS was the wrong shape and left a
    # false-ALLOW: pflag CLUSTERS short flags, so `-sRowner/repo` is `-s` + `-R owner/repo`, and
    # a token starting `-s` matched neither the recognized `-R` forms nor a `-R…` catch-all — it
    # fell through the generic "skip anything starting with -" branch and the repo selector was
    # silently dropped. `-st 12 11` likewise leaked `--subject`'s value into the PR-selector slot,
    # verifying #12 while merging #11. Parse pflag's grammar STRUCTURALLY against gh pr merge's
    # CLOSED declared flag set instead, and fail closed on anything outside it — a flag gh adds
    # later then refuses loudly rather than silently unpinning the query.
    _BOOL_SHORT = set("dmrsh")                  # -d -m -r -s (+ -h help)
    _VALUE_SHORT = set("AbFtR")                 # -A -b -F -t -R
    _BOOL_LONG = {"--admin", "--auto", "--disable-auto", "--delete-branch", "--merge",
                  "--rebase", "--squash", "--help"}
    _VALUE_LONG = {"--author-email", "--body", "--body-file", "--match-head-commit",
                   "--subject", "--repo"}

    def _parse_merge_args(argv):
        """(bare_positionals, repo_selectors, help_requested) or raises _AmbiguousFlag."""
        bare_, repos_, want_help, k_, end_of_flags = [], [], False, 0, False
        while k_ < len(argv):
            a_ = argv[k_]
            # Redirection operators are not arguments. The `&` connector normalization splits
            # `2>&1` into `2>` and `1`, and `2>` then landed in the positional slot and became
            # the "PR selector" — the guard queried a nonexistent PR named `2>`. Skip them.
            if re.match(r"^\d*[<>]+&?\d*$", a_):
                k_ += 1
                continue
            if end_of_flags or not a_.startswith("-") or a_ == "-":
                bare_.append(a_)
                k_ += 1
                continue
            if a_ == "--":
                end_of_flags = True
                k_ += 1
                continue
            if a_.startswith("--"):
                name_, _, inline_ = a_.partition("=")
                if name_ in _VALUE_LONG:
                    val_ = inline_ if "=" in a_ else (argv[k_ + 1] if k_ + 1 < len(argv) else None)
                    if val_ is None:
                        raise _AmbiguousFlag(f"{name_} was given without a value")
                    if name_ == "--repo":
                        repos_.append(val_)
                    k_ += 1 if "=" in a_ else 2
                    continue
                if name_ in _BOOL_LONG:
                    if name_ == "--help":
                        want_help = True
                    k_ += 1
                    continue
                raise _AmbiguousFlag(f"unrecognized flag {name_!r} for `gh pr merge`")
            # single-dash: walk the CLUSTER letter by letter
            j_ = 1
            while j_ < len(a_):
                ch_ = a_[j_]
                if ch_ in _BOOL_SHORT:
                    if ch_ == "h":
                        want_help = True
                    j_ += 1
                    continue
                if ch_ in _VALUE_SHORT:
                    rest_ = a_[j_ + 1:]
                    if rest_.startswith("="):
                        rest_ = rest_[1:]
                    if rest_:                              # attached value: -Rowner/repo
                        val_ = rest_
                        k_ += 1
                    else:                                  # detached value: -R owner/repo
                        if k_ + 1 >= len(argv):
                            raise _AmbiguousFlag(f"-{ch_} was given without a value")
                        val_ = argv[k_ + 1]
                        k_ += 2
                    if ch_ == "R":
                        repos_.append(val_)
                    break                                  # the cluster ends at a value flag
                raise _AmbiguousFlag(f"unrecognized short flag -{ch_} for `gh pr merge`")
            else:
                k_ += 1                                    # cluster was all booleans
        return bare_, repos_, want_help

    class _AmbiguousFlag(Exception):
        pass

    try:
        bare, _repo_sels_parsed, _want_help = _parse_merge_args(args)
    except _AmbiguousFlag as e:
        bare, _repo_sels_parsed, _want_help = [], [], False
        _flag_error = str(e)
    else:
        _flag_error = None
    if len(bare) < 2 or bare[0].lower() != "pr" or bare[1].lower() != "merge":
        # A parse failure must not silently skip the clause when this REALLY is `gh pr merge`.
        _raw = [a for a in args if not a.startswith("-")]
        if _flag_error and len(_raw) >= 2 and _raw[0].lower() == "pr" and _raw[1].lower() == "merge":
            block("gh pr merge refused: this command's flags could not be parsed against the "
                  f"known `gh pr merge` flag set ({_flag_error}), so the PR being merged cannot "
                  "be resolved and its checks cannot be verified. Command: " + cmd)
        continue
    if _want_help:
        continue                                           # `gh pr merge --help` merges nothing
    # cobra bool flags also accept `--admin=true`, which an exact-token test misses. On a repo
    # whose protection requires REVIEWS, that form bypasses the review requirement even when the
    # checks the clause verifies are green.
    if any(a == "--admin" or a.startswith("--admin=") for a in largs):
        block("gh pr merge --admin (server-side-check bypass) refused outright — the native "
              "floor may be Tier B advisory on this repo (see docs/merge-floor.md) and --admin would "
              "skip it entirely. Command: " + cmd)

    # --- CONTEXT BINDING (feat-foundry-merge-verify-context, AC-MVC-1..8) -------------------
    # The verification MUST grade THE PR BEING MERGED. `gh` resolves a PR from ambient state —
    # the cwd's git remote, GH_CONFIG_DIR/GH_HOST/GH_TOKEN, the current branch — so a query
    # built from a stripped argv in THIS process's context grades whatever PR the ambient
    # environment considers current. That produced both a false-BLOCK (a same-numbered PR in an
    # unrelated repo) and, far worse, a silent FALSE-ALLOW: a red PR admitted because a
    # same-numbered ambient PR was green. On a Tier-B repo this clause is the only in-session
    # control preventing that merge, so the query is PINNED to the command's own coordinates or
    # the merge is REFUSED. There is deliberately NO ambient fallback (AC-MVC-4).
    def block_ctx(detail):
        # Distinct from a red-check refusal (AC-MVC-7): this is an UNVERIFIABLE command, not a
        # failing one. Never phrase it as a check failure — that misdiagnosis is the bug.
        block("gh pr merge refused: the PR being merged cannot be resolved unambiguously from "
              "this command, so its checks CANNOT be verified against the right PR. " + detail +
              " Re-run naming the PR explicitly — a PR number or URL, plus `--repo owner/name` "
              "when the target repo is not the working directory's. Command: " + cmd)

    def clause_start(idx):
        """Index of the first token of the clause containing token `idx`."""
        j = idx
        while j > 0 and toks[j - 1] not in SEPARATORS:
            j -= 1
        return j

    _NONLITERAL = ("$", "`", "*", "?", "~")            # `~` only matters mid-token; see below

    def _is_literal(tok):
        return not any(c in tok for c in ("$", "`", "*", "?"))

    # (1) The repo selector. Captured from --repo/-R/--repo=… and PROPAGATED (it was previously
    #     parsed only to skip it, then discarded).
    #     The repo selectors come from the STRUCTURAL pflag parse above, so every spelling
    #     gh accepts — including clustered `-sRowner/repo` — is captured by construction
    #     rather than by enumerating literal token shapes.
    repo_sels = list(_repo_sels_parsed)
    repo_flag_seen = bool(repo_sels)
    if len(set(repo_sels)) > 1:
        # Two different repos named in one merge: gh's precedence is not ours to guess.
        block_ctx(f"Conflicting repo selectors {sorted(set(repo_sels))!r} were given.")
    repo_sel = repo_sels[0] if repo_sels else None
    if repo_flag_seen and not (repo_sel and _is_literal(repo_sel)):
        block_ctx("`--repo`/`-R` was given without a resolvable owner/name value.")

    # (2) The PR selector — the third bare token. Absent, `gh pr checks` would fall back to the
    #     CURRENT BRANCH's PR, which is exactly the ambient lookup this clause must never make.
    pr_ref = bare[2] if len(bare) >= 3 else None
    if not pr_ref:
        block_ctx("No PR number, branch or URL was given, so the check query would fall back to "
                  "whichever PR the current branch points at.")
    if not _is_literal(pr_ref):
        block_ctx(f"The PR selector {pr_ref!r} is not a literal value.")
    # A PR URL carries owner/repo/number. If `--repo` names a DIFFERENT repo the command is
    # self-contradictory and we must not pick a winner (AC-MVC-8 keeps a lone URL sufficient).
    _m_url = re.match(r"^https?://[^/]+/([^/]+/[^/]+)/pull/\d+", pr_ref)
    if _m_url and repo_sel and _m_url.group(1).lower() != repo_sel.lower():
        block_ctx(f"The PR URL names {_m_url.group(1)!r} but `--repo` names {repo_sel!r}.")

    # (3) The working directory. `cd <dir> && gh …` changes where gh resolves the repo from; the
    #     hook does not inherit it. A non-literal or non-existent target is unresolvable.
    #     Only an ABSOLUTE, literal `cd` target is resolvable. A RELATIVE one is resolved against
    #     this process's cwd, which is not reliably the shell's — Claude Code's Bash tool keeps a
    #     persistent shell, so an earlier turn's `cd` (or a dispatch worktree) makes the two
    #     differ, and in a multi-repo tree that can silently select a different checkout with a
    #     same-numbered PR. `pushd` and a subshell-grouped `(cd …` are directory changes this
    #     scan does not model, so they block rather than being ignored.
    run_cwd, cd_unresolved = None, None
    j, cstart = 0, clause_start(i)
    while j < cstart:
        tok, ltok = toks[j], low[j]
        if ltok in ("pushd", "popd") or ltok.lstrip("(") == "cd" and ltok != "cd":
            run_cwd, cd_unresolved = None, f"a directory change this scan cannot model ({tok!r})"
        elif ltok == "cd":
            tgt, m = None, j + 1
            while m < cstart and toks[m] not in SEPARATORS:
                if not toks[m].startswith("-"):
                    tgt = toks[m]
                    break
                m += 1
            if tgt is None:
                run_cwd, cd_unresolved = None, "a bare `cd` (to $HOME)"
            elif not _is_literal(tgt):
                run_cwd, cd_unresolved = None, f"a non-literal `cd` target ({tgt!r})"
            elif not os.path.isabs(os.path.expanduser(tgt)):
                run_cwd, cd_unresolved = None, (
                    f"a relative `cd` target ({tgt!r}), which this guard cannot resolve against "
                    "the shell's own working directory")
            else:
                run_cwd, cd_unresolved = os.path.expanduser(tgt), None
        j += 1
    if cd_unresolved:
        block_ctx(f"This command chain contains {cd_unresolved}, so the directory gh would "
                  "resolve the repo from is unknown.")
    if run_cwd is not None and not os.path.isdir(run_cwd):
        block_ctx(f"The `cd` target {run_cwd!r} does not resolve to an existing directory.")

    # (4) The GitHub identity. Inline `VAR=value gh …` assignments select the account, host and
    #     config dir; without them the query runs as a DIFFERENT account, which resolves
    #     different repos and different visibility.
    #     STRICT ALLOWLIST — only variables that steer gh's own identity/host resolution are
    #     carried. Copying an arbitrary assignment into this subprocess's environment would be a
    #     NEW bypass, not a fidelity improvement: `PATH=/tmp/evil gh pr merge …` would make the
    #     verification resolve a planted `gh` that prints "All checks were successful". The
    #     executable lookup honours the passed env's PATH, so the allowlist is load-bearing, not
    #     defence in depth. Anything outside it BLOCKS rather than being silently ignored — a
    #     dropped assignment could equally change the answer.
    #     An EXPLICIT name allowlist, not a `GH_*` namespace one: that namespace also contains
    #     GH_PAGER / GH_BROWSER / GH_EDITOR, each of which gh LOOKS UP AND EXECUTES, plus
    #     GH_FORCE_TTY (which re-enables the pager under capture_output). Carrying those would
    #     hand an attacker-chosen program a say in the verdict text.
    _ENV_ALLOW = {"GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
                  "GH_HOST", "GH_REPO", "GH_CONFIG_DIR"}
    run_env = dict(os.environ)
    #     The inherited environment is sanitised too — the allowlist governs ADDITIONS, but an
    #     AMBIENT GH_PAGER/GH_BROWSER already reaches gh and could rewrite the output the text
    #     scan reads. (The returncode gate below is the stronger check and is unaffected, but the
    #     fail/pending text scan is not, so both are hardened.)
    for _v in ("GH_PAGER", "PAGER", "GH_BROWSER", "BROWSER", "GH_EDITOR", "EDITOR",
               "GH_FORCE_TTY", "GH_DEBUG"):
        run_env.pop(_v, None)
    run_env["GH_PROMPT_DISABLED"] = "1"
    run_env["GH_NO_UPDATE_NOTIFIER"] = "1"
    _WRAPPERS = ("sudo", "env", "time", "command", "nohup")
    for t in toks[cstart:i]:
        # Wrappers are matched through the SAME path-qualified resolver as the guarded verbs, or
        # `/usr/bin/env GH_TOKEN=… gh pr merge …` would be refused as an unrecognized token
        # rather than taking the intended environment-reproduction path.
        if t == "-" or any(_is_verb(t.lower(), w) for w in _WRAPPERS):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", t)
        if not m:
            block_ctx(f"An unrecognized token {t!r} precedes `gh` in this clause, so the "
                      "environment the merge would run under cannot be reproduced.")
        name, val = m.group(1), m.group(2)
        if name not in _ENV_ALLOW:
            block_ctx(f"The inline assignment {name}= is not a carryable GitHub-identity "
                      f"variable ({', '.join(sorted(_ENV_ALLOW))}), so this command's "
                      "environment cannot be reproduced safely.")
        if not _is_literal(val):
            block_ctx(f"The inline assignment {t!r} is not a literal value, so the GitHub "
                      "identity the merge would use cannot be reproduced.")
        run_env[name] = val

    check_argv = ["gh", "pr", "checks", pr_ref] + (["--repo", repo_sel] if repo_sel else [])
    try:
        proc = subprocess.run(check_argv, capture_output=True, text=True, timeout=30,
                              cwd=run_cwd, env=run_env)
    except FileNotFoundError:
        # Distinct from a check failure: `gh` is not resolvable on THIS process's PATH. Hook
        # processes do not inherit a login shell's rc-modified PATH, so a shim-managed install
        # (homebrew/mise/asdf) is the common cause — and it is exactly why a command names an
        # absolute path in the first place. Say that, rather than reporting it as a red check.
        block("gh pr checks could not run: `gh` is not on this guard's PATH, so the merge cannot "
              "be verified; fail-closed. This is a tooling problem, not a failing check — the PR's "
              "checks were never queried. Command: " + cmd)
    except Exception as e:
        block(f"gh pr checks query failed ({type(e).__name__}: {e}) — cannot confirm the "
              "native floor is green; fail-closed. Command: " + cmd)
    # The query's output is echoed into refusals, i.e. into the agent's own transcript. It is
    # UNTRUSTED CONTENT — it comes from whatever host GH_HOST resolved to — so redact and cap it
    # before it is quoted, and never treat it as directive text.
    out = (proc.stdout or "") + (proc.stderr or "")
    if len(out) > 4000:
        out = out[:4000] + f"\n… [truncated, {len(out)} bytes total]"
    out = _redact(out)
    if proc.returncode != 0:
        block("gh pr checks reports a non-zero exit (not every check is green) — merge "
              f"refused. gh pr checks output:\n{out}\nCommand: {cmd}")
    if re.search(r"\bfail\b|\bpending\b", out, re.IGNORECASE):
        block("gh pr checks output names a failing or pending check — merge refused. "
              f"gh pr checks output:\n{out}\nCommand: {cmd}")
    # Every check reports passing => admit this clause.

allow()
PY
}
verdict="$(_git_discipline_eval)"
rc=$?

# Fail-closed boundary: the evaluator MUST produce a verdict. If python3 failed (non-zero)
# or emitted nothing, AND a command was recovered, BLOCK (an internal error after a command
# is recovered fails closed). We cannot know whether a destructive token was present if the
# scan itself failed, so we fail closed conservatively.
if [ "$rc" -ne 0 ] || [ -z "$verdict" ]; then
  echo "foundry-git-discipline: internal evaluator error while a command was recovered; fail-closed BLOCK." >&2
  # Security review 2026-08-02: never echo the raw command on the error path — an inline
  # GH_TOKEN=<pat> or similar would leak verbatim into the transcript exactly when the
  # evaluator (which knows how to handle it) has failed.
  echo "  (command withheld: evaluator failed, raw echo could leak inline credentials)" >&2
  exit 2
fi

case "$verdict" in
  BLOCK\ *)
    echo "FOUNDRY GIT-DISCIPLINE GUARD: BLOCKED (fail-closed) — irreversible/destructive git op refused." >&2
    echo "  ${verdict#BLOCK }" >&2
    echo "  This guard has NO in-session off-switch. To proceed, run the command yourself outside the agent." >&2
    exit 2
    ;;
  ALLOW)
    exit 0
    ;;
  *)
    # Unrecognized verdict while a command was recovered => fail-closed.
    echo "foundry-git-discipline: unrecognized evaluator verdict; fail-closed BLOCK." >&2
    exit 2
    ;;
esac
