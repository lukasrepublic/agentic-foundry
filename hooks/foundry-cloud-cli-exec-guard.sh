#!/usr/bin/env bash
# foundry-cloud-cli-exec-guard — a CONFIG-GATED-INERT PreToolUse(Bash) execution guard
# (ER #49) that makes an adopter's session-boundary guarded-exec wrapper non-bypassable
# for the realistic direct / piped / prefixed paths. Generified from a proven upstream
# exec-guard reference (parameterize the wrapper + the guarded tool-set via a
# config seam; the regex machinery is the generic core, unchanged in shape).
#
# WHAT IT ENFORCES (only once a valid `wrapper` resolves): a bare invocation of a guarded
# cloud/IaC CLI (default `aws|kubectl|tofu|terraform|helm|argocd`) at ANY command position
# — command start, or after a separator/opener (`;` `&` `|` `&&` `||` `(` `{` backtick,
# newline), behind ≥0 leading `VAR=val` env-assignments, ≥0 leading redirections, and ≥0
# leading wrapper/launcher words with their flag or numeric operands (`sudo env time xargs
# command builtin exec nohup stdbuf timeout nice ionice setsid doas flock chrt taskset
# unbuffer script runuser su`) — is BLOCKED (exit 2, denial on stderr naming the wrapped
# form, inline `VAR=secret` values redacted), UNLESS routed through
# `<wrapper> exec [--flag…] <tool>` (neutralized first), OR it is an adopter-listed
# `offline_exempt` command-position prefix.
#
# The tool word, the launcher word and the FIRST word of the wrapper/exempt sequences are all
# resolved to their final path segment, so `/usr/bin/aws` is `aws` and `/usr/bin/env aws …`
# does not shield the tool. Later words of a sequence are SUBCOMMANDS and are matched
# literally — resolving those by basename would widen an exemption into a bypass.
#
# A quoted MENTION (`grep 'kubectl …'`) does not false-positive because it is not at a command
# POSITION — not because it is quoted. Quoting is not an escape: `"aws" s3 rm …` is blocked.
#
# CONFIG-GATED ACTIVATION — FAIL-INERT (the key difference from the git-discipline guard):
# the guard reads `cloud_cli_exec_guard:{guarded_tools, wrapper, offline_exempt}` from the
# ADOPTER config seam at `${CLAUDE_PROJECT_DIR:-$PWD}/.claude/foundry-project.json` (the
# WORKSPACE root; a dispatched product worktree reads the workspace seam, NOT a per-product
# seam). It is INERT (exit 0, allow-all no-op, blocks NOTHING) for EVERY resolution failure:
#   (i) CLAUDE_PROJECT_DIR unset/empty/unresolvable; (ii) the seam file absent; (iii) the
#   file malformed/unreadable JSON; (iv) the `cloud_cli_exec_guard` block absent; (v) the
#   block present but `wrapper` absent / empty-string / wrong-typed.
# An adopter without a resolvable wrapper has NO wrapper to route through, so blocking a
# bare cloud CLI would brick their infra work — hence fail-INERT, NEVER fail-closed-block,
# when unconfigured. The fail-closed / ambiguity-blocks behaviour engages ONLY once a valid
# `wrapper` IS resolved.
#
# THREAT MODEL — TRUSTED OPERATOR, honest limit. A command-string guard, NOT a sandbox.
# It is one tier weaker than the MAC systems it is sometimes compared to: those bind to the
# executed object at the kernel exec() boundary, this matches text before anything runs.
# OUT OF SCOPE, and NOT closed holes: shell indirection (`bash -c "<tool> …"`, `eval`,
# `$(echo tool) …`, variable indirection, aliases); a guarded tool reachable under a DIFFERENT
# NAME (copy/symlink, write-then-exec); intra-word quote/escape splicing (`aw"s"`, `\aws`);
# and a launcher taking a non-numeric, non-flag operand ahead of the tool
# (`flock /tmp/lock aws …`). It catches the realistic direct/piped/prefixed paths — the
# motivating incident (a bare direct `tofu apply`) IS caught.
#
# The pattern that closes the whole class is CREDENTIAL SCOPING (the wrapper being the only
# thing that materializes live cloud credentials, so a bare invocation fails on authentication
# however it is spelled) — filed as an open enhancement request against this guard series.
# Do not extend this matcher with another spelling variant before that research runs.
set -uo pipefail

# --eval (SELFTEST ONLY) — drive the matcher hermetically over a throwaway command-string
# under an EXPLICIT (possibly empty) config, NEVER reading the real seam:
#   foundry-cloud-cli-exec-guard.sh --eval '<cmd>' '<wrapper>' '<tools-csv>' '<exempt-newline-joined>'
# An empty <wrapper> => INERT (the no-wrapper inert control). This serves the SAME evaluator
# as the live PreToolUse path (consumed, never re-implemented). It prints the verdict line
# ("BLOCK <reason>" / "ALLOW") on stdout and exits 0 BLOCK / nonzero... no: the verdict is
# the stdout token (the drop-in selftest greps stdout); the process exit mirrors the live
# guard (0 allow / 2 block) so an adopter can also test by exit code.
# ----------------------------------------------------------------------------------------
# Resolve the command string from the PreToolUse(Bash) payload, OR (selftest) from --eval.
# NO recoverable command => exit 0 (nothing to guard; the merge-gate / git-discipline
# admit-on-no-command precedent).
# ----------------------------------------------------------------------------------------
EVAL_MODE=0
if [ "${1:-}" = "--eval" ]; then
  EVAL_MODE=1
  cmd="${2:-}"
  wrapper_in="${3:-}"
  tools_in="${4:-}"
  exempt_in="${5:-}"
else
  payload="$(cat 2>/dev/null || true)"
  cmd="$(printf '%s' "$payload" | python3 -c 'import sys,json
try:
    c = json.load(sys.stdin).get("tool_input", {}).get("command", "")
    print(c if isinstance(c, str) else "")
except Exception:
    print("")' 2>/dev/null || true)"
fi

# No command recoverable (empty / unparseable / absent / non-string) => admit.
[ -z "$cmd" ] && exit 0

# ----------------------------------------------------------------------------------------
# Resolve the config seam (LIVE path only). FAIL-INERT on ANY resolution failure: emit the
# wrapper/tools/exempt as empty/absent so the evaluator runs in INERT mode (allow-all).
# Read from CLAUDE_PROJECT_DIR (the WORKSPACE root); a product worktree reads the workspace
# seam, not a per-repo seam. python3 does the JSON read so a malformed file => INERT, never
# a crash.
# ----------------------------------------------------------------------------------------
if [ "$EVAL_MODE" -eq 0 ]; then
  proj="${CLAUDE_PROJECT_DIR:-$PWD}"
  seam="$proj/.claude/foundry-project.json"
  # The python reader emits three BASE64-encoded fields on three lines: wrapper / guarded_tools
  # (csv) / offline_exempt (newline-joined). base64 carries no NUL or newline, so a bash $(...)
  # capture is lossless (unlike a NUL-delimited stream, which bash strips). Any failure => the
  # reader still emits three lines of empty-base64 => empty wrapper => INERT downstream.
  read_out="$(SEAM="$seam" python3 -c 'import os,json,sys,base64
seam = os.environ.get("SEAM","")
wrapper=""; tools=""; exempt=""
try:
    with open(seam) as fh:
        doc = json.load(fh)
    blk = doc.get("cloud_cli_exec_guard")
    if isinstance(blk, dict):
        w = blk.get("wrapper")
        if isinstance(w, str):
            wrapper = w
        gt = blk.get("guarded_tools")
        if isinstance(gt, list):
            tools = ",".join(str(t) for t in gt if isinstance(t, str) and t.strip())
        oe = blk.get("offline_exempt")
        if isinstance(oe, list):
            exempt = "\n".join(str(e) for e in oe if isinstance(e, str) and e.strip())
except Exception:
    wrapper=""; tools=""; exempt=""
def b(s): return base64.b64encode(s.encode("utf-8")).decode("ascii")
sys.stdout.write(b(wrapper) + "\n" + b(tools) + "\n" + b(exempt) + "\n")' 2>/dev/null || true)"
  # Read the three base64 lines positionally; decode each (a malformed/empty line => empty).
  b64_wrapper="$(printf '%s\n' "$read_out" | sed -n '1p')"
  b64_tools="$(printf '%s\n' "$read_out" | sed -n '2p')"
  b64_exempt="$(printf '%s\n' "$read_out" | sed -n '3p')"
  wrapper_in="$(printf '%s' "$b64_wrapper" | python3 -c 'import sys,base64
try: sys.stdout.write(base64.b64decode(sys.stdin.read().strip() or "").decode("utf-8"))
except Exception: pass' 2>/dev/null || true)"
  tools_in="$(printf '%s' "$b64_tools" | python3 -c 'import sys,base64
try: sys.stdout.write(base64.b64decode(sys.stdin.read().strip() or "").decode("utf-8"))
except Exception: pass' 2>/dev/null || true)"
  exempt_in="$(printf '%s' "$b64_exempt" | python3 -c 'import sys,base64
try: sys.stdout.write(base64.b64decode(sys.stdin.read().strip() or "").decode("utf-8"))
except Exception: pass' 2>/dev/null || true)"
fi

# ----------------------------------------------------------------------------------------
# The decision is delegated to a single python3 evaluator over the recovered command, the
# resolved wrapper, the guarded tool-set, and the offline-exempt list. It is a PURE token
# scan (no shell execution of the command). It prints "BLOCK <reason>" or "ALLOW".
#
# FAIL-INERT vs FAIL-CLOSED: an EMPTY/absent `wrapper` => INERT (ALLOW always). Only with a
# non-empty wrapper does enforcement engage and THEN fail closed on a recognized guarded
# tool at a command position whose routing cannot be proven wrapper-safe.
# ----------------------------------------------------------------------------------------
# bash-3.2 parse compat: the heredoc lives INSIDE a function body, never inside `$(...)` —
# bash 3.2's command-substitution scanner mis-tracks backquotes/quotes across heredoc content
# (feat-foundry-bash32-parse-guard), so the substitution below contains only the function call.
_cloud_guard_eval() {
  CMD="$cmd" WRAPPER="$wrapper_in" GUARDED_TOOLS="$tools_in" OFFLINE_EXEMPT="$exempt_in" python3 - <<'PY'
import os, re, shlex, sys

cmd = os.environ.get("CMD", "")
wrapper_raw = os.environ.get("WRAPPER", "") or ""
tools_csv = os.environ.get("GUARDED_TOOLS", "") or ""
exempt_raw = os.environ.get("OFFLINE_EXEMPT", "") or ""

def out(s):
    print(s)
    sys.exit(0)

# --- CONFIG-GATED-INERT: no resolvable wrapper => allow-all no-op (blocks nothing). This is
# the load-bearing difference from the git-discipline guard — an unconfigured adopter must be
# wholly unaffected. ---
wrapper = wrapper_raw.strip()
if not wrapper:
    out("ALLOW")

# Default guarded tool-set (applies only once a wrapper is declared).
DEFAULT_TOOLS = ["aws", "kubectl", "tofu", "terraform", "helm", "argocd"]
guarded = [t.strip().lower() for t in tools_csv.split(",") if t.strip()] or DEFAULT_TOOLS
guarded_set = set(guarded)

# offline_exempt entries: each is a token-sequence (a command-POSITION prefix). Split each
# entry into tokens once.
exempt_entries = []
for line in exempt_raw.split("\n"):
    line = line.strip()
    if not line:
        continue
    try:
        toks = shlex.split(line, posix=True)
    except Exception:
        toks = line.split()
    if toks:
        exempt_entries.append([t for t in toks])

# Leading wrapper words skipped before a command word at a command position (item (3)). Same
# fixed list as the reference; the named bounded-residual launchers (timeout/setsid/doas/…)
# and flag-bearing wrapper words are honest limits (spec Out-of-scope), not covered here.
WRAPPER_WORDS = {"sudo", "env", "time", "xargs", "command", "builtin", "exec", "nohup", "stdbuf",
                 # An UNRECOGNISED leading word shields the rest of its segment (only the first
                 # command word after a separator is ever convicted), so every launcher missing from
                 # this set is a bypass: `timeout 60 aws s3 rm …` was admitted. These were named as
                 # bounded residuals in this file's own header while being one-token evasions.
                 "timeout", "nice", "ionice", "setsid", "doas", "flock", "chrt", "taskset",
                 "unbuffer", "script", "runuser", "su"}

# Wrapper-form tokens: `<wrapper> exec [--flag…] <tool>`. The wrapper value may itself be a
# multi-word command (`ctx exec`); the canonical reference form is `<wrapper-words> exec`.
# We treat the configured wrapper as its leading token sequence, then expect literal `exec`,
# then ≥0 `--flag`/`-f` option tokens, then the guarded tool. Tokenize the wrapper value.
try:
    wrapper_toks = [t.lower() for t in shlex.split(wrapper, posix=True)]
except Exception:
    wrapper_toks = [t.lower() for t in wrapper.split()]
# The reference convention is `<cmd> exec <tool>` — e.g. wrapper="ctx exec". Derive the
# wrapper-word prefix (everything; if the operator already included `exec` as the last token,
# treat the run up to and including `exec` as the routing prefix; else require an `exec` token
# after the wrapper words).
if wrapper_toks and wrapper_toks[-1] == "exec":
    route_prefix = wrapper_toks[:]            # e.g. ["ctx","exec"]
else:
    route_prefix = wrapper_toks + ["exec"]    # require the literal `exec` subverb

# --- Connector normalization (HEURISTIC, NOT a shell parser) — the SAME technique as
# foundry-git-discipline.sh. shlex.split does NOT treat shell connectors as token boundaries,
# so a separator GLUED to an adjacent token with no surrounding space (`x;aws s3 rm …`,
# `true&&tofu apply`, `foo|kubectl delete`) would FUSE the verb into one token (`x;aws`) and
# hide it from the command-position scan => FAIL-OPEN. We pre-insert spaces around the
# connectors + the openers `(` `)` `{` `}` and backtick so shlex yields them as standalone
# SEPARATOR/OPENER tokens. Two-char operators are spaced BEFORE the single-char ones so
# `&&`/`||` are not shredded into `& &` / `| |`. This is a literal-string static scan — it
# does NOT see through `bash -c "…"`, `eval`, `$(…)` (spec §8 bounded residuals). ---
_SECRET_ASSIGN = re.compile(
    r"(?i)\b([A-Za-z_][A-Za-z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|APIKEY|API_KEY|_KEY|_PAT))=(\S+)")


def _redact(s):
    """Mask inline VAR=value secrets before echoing a command back.

    The refusal prints the whole command, and skip_prefix explicitly anticipates leading
    `NAME=value` env assignments — which is precisely where a credential sits. Hook output lands in
    the session transcript and in anything scraping stderr, so `AWS_SECRET_ACCESS_KEY=… aws s3 rm …`
    would write the secret there. Mirrors the sibling git-discipline guard's redaction."""
    return _SECRET_ASSIGN.sub(lambda m: m.group(1) + "=***", s)


norm = cmd
# --- LINE STRUCTURE FIRST, and in this order. Both defects below were verified in this guard and
# were already fixed in the sibling git-discipline guard; the two must not disagree.
#   1. A backslash line-continuation is DELETED, never spaced. bash splices the word back together,
#      so `aw\<newline>s s3 rm …` executes `aws s3 rm` — and a lone surviving `\` token also blocks
#      the command-position test for everything after it (`cd /r && \<newline> aws s3 rm …`).
#   2. A newline is a COMMAND SEPARATOR. shlex consumes it as ordinary whitespace and never emits it
#      as a token, so the old `replace("\n", " \n ")` + SEPARATORS membership was dead code: only the
#      FIRST word of a multi-line string sat at a command position, and `echo hi<newline>aws s3 rm …`
#      was admitted. Rewriting to " ; " makes the separator real.
norm = re.sub(r"\\\r?\n", "", norm)
norm = re.sub(r"[\r\n]+", " ; ", norm)
# Protect fd-duplicating redirections (`2>&1`, `>&2`) from the blanket `&` split below, which would
# otherwise shred `2>&1` into `2>`, `&`, `1` — making `&` a separator and `1` the command word, so
# `2>&1 aws s3 rm …` never reached the tool. Restored immediately after the separator rewrites.
norm = re.sub(r"([<>])&", "\\1\x00", norm)
norm = re.sub(r"&&", " && ", norm)
norm = re.sub(r"\|\|", " || ", norm)
norm = re.sub(r";", " ; ", norm)
norm = re.sub(r"\|", " | ", norm)
norm = re.sub(r"&", " & ", norm)
norm = re.sub(r"\(", " ( ", norm)
norm = re.sub(r"\)", " ) ", norm)
norm = re.sub(r"\{", " { ", norm)
norm = re.sub(r"\}", " } ", norm)
norm = re.sub(r"`", " ` ", norm)
norm = norm.replace("\x00", "&")            # restore the protected fd-dup redirections

# --- Tokenize. shlex (posix) STRIPS quotes — so a quoted/argument mention (`grep 'kubectl …'`,
# `echo tofu`) becomes a bare token, which would false-positive a naive scan. To honor the
# quoted-mention ALLOW (AC-EXECGUARD-2), we instead tokenize in NON-posix mode so quote chars
# are RETAINED on the tokens, then identify "quoted" tokens (those that begin with a quote)
# and exclude them from command-position matching. Newline is also a separator. On a shlex
# failure fall back to a whitespace split (never silently stop scanning => the matcher must
# still run; the wrapper IS configured here so we are in fail-closed territory). ---
try:
    raw_toks = shlex.split(norm, posix=False)
except Exception:
    raw_toks = norm.split()

# A token is "quoted" (a mention, not a command word) iff it starts with a quote character.
def is_quoted(tok):
    return len(tok) >= 1 and tok[0] in ("'", '"')

# Strip surrounding quotes for comparison of NON-quoted structural tokens (none will be
# quoted, but normalize defensively) and lowercasing.
def bare(tok):
    t = tok
    if len(t) >= 2 and t[0] in ("'", '"') and t[-1] == t[0]:
        t = t[1:-1]
    return t

SEPARATORS = {"&&", "||", ";", "|", "&", "(", ")", "{", "}", chr(96), "\n"}

toks = raw_toks
n = len(toks)

# Build a parallel view: for each index, its lowercased bare text, whether it is a separator,
# and whether it is a quoted mention.
btext = [bare(t).lower() for t in toks]
quoted = [is_quoted(t) for t in toks]
is_sep = [(t in SEPARATORS) for t in toks]

def is_command_position(i):
    """True iff index i is a command-position start: i==0, or the previous NON-empty token is
    a separator/opener."""
    j = i - 1
    while j >= 0:
        if toks[j].strip() == "":
            j -= 1
            continue
        return is_sep[j]
    return True  # nothing before => start of line

def skip_prefix(i):
    """From a command-position index i, skip ≥0 leading VAR=val env-assignments and ≥0 leading
    wrapper-words, returning the index of the first actual command WORD (or n if none)."""
    k = i
    while k < n:
        if is_sep[k] or quoted[k]:
            return k  # a separator/quote terminates the prefix run (no command word here)
        t = btext[k]
        # env-assignment: NAME=value (NAME is a shell-identifier).
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):
            k += 1
            continue
        # a redirection operator token (`>f`, `>>f`, `2>&1`, `<f`) is not a command word — bash
        # accepts redirections before the command, so `>/dev/null aws s3 rm …` still runs aws.
        if re.match(r"^\d*[<>]+&?\d*", t):
            k += 1
            continue
        # wrapper word (bare, no flags — flag-bearing wrapper words are a named bounded residual).
        # PATH-RESOLVED, same as the tool word: `/usr/bin/env aws s3 rm …` and `/usr/bin/sudo aws …`
        # otherwise stop the prefix scan dead, leaving the real command word at a non-command
        # position and never checked at all. That is the atom's own premise ("a path prefix carries
        # no security meaning") applied to only one side of the same command.
        if _word(t) in WRAPPER_WORDS:
            k += 1
            # …and skip that launcher's own option flags / numeric operand, so `timeout 60 aws …`
            # and `nice -n 5 tofu …` reach the real command word. A launcher taking a NON-numeric,
            # non-flag operand (`flock /tmp/l aws …`, `su user -c …`) still shields what follows —
            # a NAMED residual (see AC-CGP-7), not a claim of coverage. Modelling every launcher's
            # argument grammar is exactly the anti-gaming machinery CLAUDE.md says to price against
            # the operator's own terminal test pass rather than build.
            while k < n and not is_sep[k] and not quoted[k] and (
                    btext[k].startswith("-") or btext[k].isdigit()):
                k += 1
            continue
        return k
    return n

def _word(tok):
    """The command word `tok` denotes, path-qualified or not.

    Exact equality let `/usr/bin/aws` bypass this guard entirely while bare `aws` was refused —
    the path prefix carries no security meaning. The sibling git-discipline guard closed the
    identical hole in v1.0.1; this is the same rule, applied here so the two guards cannot
    disagree about whether /usr/bin/<tool> is <tool>. Matching is on the WHOLE final path
    segment, never a substring, so `aws-vault` / `gcloud-wrapper` / `myaws` are not this tool.

    Trailing separators resolve to the last NON-EMPTY segment (`aws/` -> `aws`, `/usr/bin/aws/.` ->
    `aws`) rather than to "" or ".", which would silently match nothing. Neither form is executable,
    so resolving them is a free widening.
    """
    parts = [p for p in tok.split("/") if p not in ("", ".")]
    return parts[-1] if parts else tok


def matches_prefix_seq(start, seq):
    """True iff the bare lowercased tokens at command-position `start` (after skipping
    env-assignments + wrapper words) begin with the token sequence `seq` (prefix-anchored)."""
    k = skip_prefix(start)
    for off, want in enumerate(seq):
        idx = k + off
        if idx >= n or is_sep[idx] or quoted[idx]:
            return False, None
        if off == 0:
            # Only the FIRST word of a sequence is a program name, so only it is path-resolved —
            # and BOTH sides are, so a wrapper configured as "/usr/local/bin/cloudwrap exec" still
            # routes (an unnormalized config side matched nothing and silently made the guard admit
            # everything wrapper-shaped while reporting as configured).
            if _word(btext[idx]) != _word(want):
                return False, None
        else:
            # Later words are SUBCOMMANDS (`exec`, `configure`), not programs. Resolving them by
            # basename flipped previously-BLOCKED input to ALLOW — under exempt `aws configure`,
            # `aws /x/configure list` matched the exemption. AC-CGP-6 forbids any such flip.
            if btext[idx] != want:
                return False, None
    return True, k + len(seq)

# --- Walk every command position. At each, in priority order:
#   1. If it routes through the wrapper (`<route_prefix> [--flag…] <tool>`) => this span is
#      NEUTRALIZED (allowed); skip past the tool token (the remainder is re-matched at later
#      command positions).
#   2. Else if it matches an offline_exempt prefix => NEUTRALIZED for THIS command only; the
#      remainder after the next separator is still matched (the exemption does not whitelist
#      a FOLLOWING guarded tool).
#   3. Else if the first command word is a guarded tool => BLOCK (fail-closed).
#   4. Else => benign; continue.
# Quoted mentions are never at a command position as a real word (skip_prefix bails on them),
# so `grep 'kubectl …'` never matches. ---
i = 0
while i < n:
    if not is_command_position(i):
        i += 1
        continue
    # (1) wrapper-routed?
    ok, after = matches_prefix_seq(i, route_prefix)
    if ok:
        # after route_prefix there may be ≥0 option flags, then the tool — neutralize either
        # way (the wrapper owns posture). Advance past the routed tool token if present.
        k = after
        while k < n and not is_sep[k] and not quoted[k] and btext[k].startswith("-"):
            k += 1
        if k < n and not is_sep[k] and not quoted[k]:
            k += 1  # consume the routed tool token
        i = k
        continue
    # (2) offline_exempt prefix?
    exempt_hit = False
    for seq in exempt_entries:
        ok2, after2 = matches_prefix_seq(i, [s.lower() for s in seq])
        if ok2:
            exempt_hit = True
            # neutralize ONLY this command: advance to the next separator (the remainder is
            # re-matched at its own command position).
            k = after2
            while k < n and not is_sep[k]:
                k += 1
            i = k
            break
    if exempt_hit:
        continue
    # (3) guarded tool at this command position?
    k = skip_prefix(i)
    # NOTE: `quoted[k]` is deliberately NOT excluded here. The quoted-mention ALLOW is carried by
    # is_command_position, not by quoting: in `grep 'kubectl …'` the quoted token sits at index 1
    # whose predecessor is `grep`, so it is not a command position and is never reached. Excluding
    # quoted tokens here as well meant `"aws" s3 rm s3://bucket --recursive` — ordinary shell syntax,
    # not obfuscation — skipped the guard entirely. `bare()` already strips the surrounding quotes.
    if k < n and not is_sep[k]:
        word = btext[k]
        # A PATH-QUALIFIED form resolves to its final path segment (`_word`), so
        # `/usr/bin/aws`, `./aws` and `~/bin/aws` are all `aws` — and the same resolution is
        # applied to launcher words (`/usr/bin/env aws …`) and to the first word of the
        # wrapper/exempt sequences, so no call site disagrees about what a tool word is.
        #
        # AC-CGP-7 — WHAT IS STILL NOT COVERED (stated in full, not by example):
        #   * shell indirection: `bash -c "…"`, `eval`, `$(…)`, variable indirection, aliases;
        #   * a guarded tool reachable under a DIFFERENT NAME (a copy or symlink);
        #   * intra-word quote/escape splicing: `aw"s"`, `\aws` — `bare()` strips only
        #     SURROUNDING matched quotes, and non-posix shlex does no escape processing;
        #   * a launcher taking a non-numeric, non-flag operand ahead of the tool
        #     (`flock /tmp/lock aws …`, `su user -c …`), which still shields what follows.
        # None of these are closed by string matching at all; the pattern that closes the whole
        # class is credential scoping (the wrapper being the only thing that materializes live
        # credentials), which is filed as an open enhancement request against this guard series.
        if _word(word) in guarded_set:
            # Name the exact wrapped form the operator must use instead (route_prefix already
            # carries the `exec` subverb — never doubled).
            wrapped = " ".join(route_prefix) + " " + word
            print("BLOCK bare guarded tool %r at a command position not routed through the "
                  "wrapper. Re-run it as: %s …  Command: %s" % (word, wrapped, _redact(cmd)))
            sys.exit(0)
        # advance past this command word
        i = k + 1
        continue
    i += 1

print("ALLOW")
PY
}
verdict="$(_cloud_guard_eval)"
rc=$?

# FAIL boundary: the evaluator MUST produce a verdict. If python3 failed (non-zero) or
# emitted nothing:
#   - LIVE path with NO wrapper configured cannot reach here (the inert branch is inside the
#     evaluator), but a python3 failure BEFORE producing output is indistinguishable from an
#     INERT outcome. Under the CONFIG-GATED-INERT posture an unconfigured adopter must NEVER be
#     blocked by an internal error. We therefore distinguish: if a wrapper was resolved we fail
#     CLOSED (block); if NO wrapper was resolved we fail INERT (allow). wrapper_in carries that.
if [ "$rc" -ne 0 ] || [ -z "$verdict" ]; then
  if [ -n "${wrapper_in:-}" ] && [ -n "$(printf '%s' "${wrapper_in:-}" | tr -d '[:space:]')" ]; then
    if [ "$EVAL_MODE" -eq 1 ]; then echo "BLOCK internal evaluator error (fail-closed)"; exit 2; fi
    echo "foundry-cloud-cli-exec-guard: internal evaluator error with a wrapper configured; fail-closed BLOCK." >&2
    echo "  command: $cmd" >&2
    exit 2
  fi
  # No wrapper configured => INERT (an unconfigured adopter is never affected).
  if [ "$EVAL_MODE" -eq 1 ]; then echo "ALLOW"; fi
  exit 0
fi

# --- SELFTEST (--eval) emits the RAW verdict token on stdout (the drop-in selftest greps it)
# and mirrors the live exit code (0 allow / 2 block). The LIVE path emits the human-facing
# denial on stderr. ---
case "$verdict" in
  BLOCK\ *)
    if [ "$EVAL_MODE" -eq 1 ]; then
      printf '%s\n' "$verdict"
      exit 2
    fi
    echo "FOUNDRY CLOUD-CLI EXEC-GUARD: BLOCKED (fail-closed) — a guarded cloud/IaC CLI was run bare, bypassing the guarded-exec wrapper." >&2
    echo "  ${verdict#BLOCK }" >&2
    echo "  Route the tool through the wrapper. This guard has NO in-session off-switch; to proceed, route via the wrapper or run it yourself outside the agent." >&2
    exit 2
    ;;
  ALLOW)
    if [ "$EVAL_MODE" -eq 1 ]; then printf 'ALLOW\n'; fi
    exit 0
    ;;
  *)
    # Unrecognized verdict. Mirror the fail boundary: closed iff a wrapper is configured.
    if [ -n "${wrapper_in:-}" ] && [ -n "$(printf '%s' "${wrapper_in:-}" | tr -d '[:space:]')" ]; then
      if [ "$EVAL_MODE" -eq 1 ]; then echo "BLOCK unrecognized verdict (fail-closed)"; exit 2; fi
      echo "foundry-cloud-cli-exec-guard: unrecognized evaluator verdict with a wrapper configured; fail-closed BLOCK." >&2
      exit 2
    fi
    if [ "$EVAL_MODE" -eq 1 ]; then echo "ALLOW"; fi
    exit 0
    ;;
esac
