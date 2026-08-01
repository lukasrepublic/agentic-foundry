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
# THREAT MODEL — trusted operator, honest limit: an AGENT-FALLIBILITY mistake-catcher, NOT
# an unbypassable sandbox. "NO bypass" = there is NO in-session CONFIG off-switch (no
# per-invocation flag, no env var, no session-writable file) that downgrades a BLOCK to an
# ADMIT. It does NOT mean "no command can evade it": a literal-string scan cannot see
# through shell indirection (`bash -c …`, `eval`, `$(…)`, variable indirection, aliases,
# write-script-then-run) or reach non-Bash channels — those are acknowledged BOUNDED
# RESIDUALS (spec §8), not closed holes.
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
verdict="$(PROTECTED="$PROTECTED" STRICT_HISTORY="$STRICT_HISTORY" CMD="$cmd" python3 - <<'PY'
import os, re, shlex, subprocess, sys

cmd = os.environ.get("CMD", "")
protected = {b.strip() for b in os.environ.get("PROTECTED", "main").split(",") if b.strip()}
protected.add("main")                                   # always protected (widen-only)
strict = os.environ.get("STRICT_HISTORY", "0") == "1"

def block(reason):
    print("BLOCK " + reason)
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
    toks = norm.split()

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
    if t != "git":
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
    if t != "rm":
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
    if t != "gh":
        continue
    args = clause_args(i)
    largs = [a.lower() for a in args]
    # bare (non-option) tokens, skipping the VALUE of the one value-taking global gh flag we
    # know about (--repo/-R owner/repo) so it can't masquerade as the "pr" subcommand token —
    # any OTHER value-taking global flag interposed before `pr merge` is a bounded residual
    # (mirrors the git-clause conservatism of this file itself, not a closed hole).
    bare = []
    skip_next = False
    for a in args:
        if skip_next:
            skip_next = False
            continue
        if a in ("--repo", "-R"):
            skip_next = True
            continue
        if a.startswith("-"):
            continue
        bare.append(a)
    if len(bare) < 2 or bare[0].lower() != "pr" or bare[1].lower() != "merge":
        continue
    if "--admin" in largs:
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
    repo_sels, repo_flag_seen = [], False
    k = 0
    while k < len(args):
        a = args[k]
        if a in ("--repo", "-R"):
            repo_flag_seen = True
            if k + 1 < len(args) and not args[k + 1].startswith("-"):
                repo_sels.append(args[k + 1])
                k += 2
                continue
        elif a.startswith("--repo="):
            repo_flag_seen = True
            repo_sels.append(a.split("=", 1)[1])
        k += 1
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
    run_cwd, cd_unresolved = None, None
    j, cstart = 0, clause_start(i)
    while j < cstart:
        if low[j] == "cd":
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
    _ENV_ALLOW = re.compile(r"^(GH_|GITHUB_)[A-Z0-9_]*$")
    run_env = dict(os.environ)
    for t in toks[cstart:i]:
        if t.lower() in ("sudo", "env", "time", "command", "nohup", "-"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", t)
        if not m:
            block_ctx(f"An unrecognized token {t!r} precedes `gh` in this clause, so the "
                      "environment the merge would run under cannot be reproduced.")
        name, val = m.group(1), m.group(2)
        if not _ENV_ALLOW.match(name):
            block_ctx(f"The inline assignment {name}= is not a GH_*/GITHUB_* variable. The "
                      "verification subprocess carries only GitHub-identity variables, so this "
                      "command's environment cannot be reproduced safely.")
        if not _is_literal(val):
            block_ctx(f"The inline assignment {t!r} is not a literal value, so the GitHub "
                      "identity the merge would use cannot be reproduced.")
        run_env[name] = val

    check_argv = ["gh", "pr", "checks", pr_ref] + (["--repo", repo_sel] if repo_sel else [])
    try:
        proc = subprocess.run(check_argv, capture_output=True, text=True, timeout=30,
                              cwd=run_cwd, env=run_env)
    except Exception as e:
        block(f"gh pr checks query failed ({type(e).__name__}: {e}) — cannot confirm the "
              "native floor is green; fail-closed. Command: " + cmd)
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        block("gh pr checks reports a non-zero exit (not every check is green) — merge "
              f"refused. gh pr checks output:\n{out}\nCommand: {cmd}")
    if re.search(r"\bfail\b|\bpending\b", out, re.IGNORECASE):
        block("gh pr checks output names a failing or pending check — merge refused. "
              f"gh pr checks output:\n{out}\nCommand: {cmd}")
    # Every check reports passing => admit this clause.

allow()
PY
)"
rc=$?

# Fail-closed boundary: the evaluator MUST produce a verdict. If python3 failed (non-zero)
# or emitted nothing, AND a command was recovered, BLOCK (an internal error after a command
# is recovered fails closed). We cannot know whether a destructive token was present if the
# scan itself failed, so we fail closed conservatively.
if [ "$rc" -ne 0 ] || [ -z "$verdict" ]; then
  echo "foundry-git-discipline: internal evaluator error while a command was recovered; fail-closed BLOCK." >&2
  echo "  command: $cmd" >&2
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
