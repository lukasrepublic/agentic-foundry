#!/usr/bin/env python3
"""foundry-fleet-session-machinery — derive a typed PROCESS-MACHINERY overlay for the fleet surface.

Implements feat-foundry-fleet-session-machinery (v1.1). Beyond *what* a session works on (the
session-registry work-context overlay), a one-operator-many-sessions supervisor needs *how the
machinery is running it*: the isolation (collision/blast surface), where
it sits in the front-auth→gate→merge readiness pipeline, the mode, whether it trips the security
floor, and (for infra) its blast-radius + ctx-posture + target repo.

These are SYSTEM STATE, not human intent → this atom DERIVES them and attaches a typed machinery
sub-record to each session-registry record by `session_id`. It is READ-ONLY (computes, never mutates)
and FAILS CLOSED: an uncertain gate/security/isolation signal is the field's attention value, never a
false all-clear.

THE TRUST MODEL (the audit's core finding — graded by what each source ACTUALLY guarantees):
  (i)  CORPUS-DERIVED VERDICTS — read from the WORKSPACE GOVERNANCE CORPUS at the canonical ref by the
       `atom_or_spec` key: `gate_readiness` (authorized-trailer + governance + best-effort PR), `mode`,
       `target_repo`. A session CANNOT forge these (it cannot make an un-authorized atom read
       `authorized`). NEVER read from the session's repo, the gitignored build-provenance marker, the
       session-worktree contract bytes, or any session-authored narration enrichment channel (the
       retired custom narration surface is gone — work-context is DERIVED, never session-narrated).
  (ii) SESSION-DECLARED ENVIRONMENT — the git env (`isolation`). Cooperating-operator claims:
       a mis-declaration mislabels only the declaring session's OWN row, and every risk-bearing field
       fails toward attention.

CLI: derive [--root WS] | --selftest   (AC-SMACH-1..5; selftest is hermetic).
"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# ----------------------------------------------------------------------------- sanitization (AC-SMACH-5)
# Every rendered string — target_repo, AND every discriminated-outcome reason / gh/git
# error text — is secret-scrubbed AND control/ANSI/newline-neutralized (gh/git stderr can carry tokens
# or terminal-injection). Mirrors the sibling registry/roster floors (scrub-before-sanitize).
_CTRL_RE = re.compile(r"(\x1b\[[0-9;]*[A-Za-z]|\x1b[@-Z\\-_]|[\x00-\x1f\x7f-\x9f])")
_SECRET_RE = re.compile(
    r"(?i)(?:sk-[a-z0-9]{16,}|ghp_[a-z0-9]{20,}|gh[opusr]_[a-z0-9]{20,}"
    r"|aws_secret[^\s]*|AKIA[0-9A-Z]{16}|[A-Za-z0-9+/]{40,}={0,2}"
    r"|(?:password|secret|token|api[_-]?key)\s*[:=]\s*\S+)"
)


def _clean(v):
    """AC-SMACH-5 floor: secret-scrub THEN neutralize control/ANSI/newline (C0 0x00–0x1F + 0x7F + C1
    0x80–0x9F + ANSI/CSI introducers). Applied to EVERY externally-sourced string the record renders."""
    if not isinstance(v, str):
        return v
    return _CTRL_RE.sub("", _SECRET_RE.sub("«redacted»", v))


# ----------------------------------------------------------------------------- field domains + trust
ISOLATION_DOMAIN = ("direct_main", "worktree_on_main", "worktree", "unknown")
GATE_DOMAIN = ("unauthorized", "authorized", "pr_open", "gate_pass", "gate_block", "merged", "unknown")
SECURITY_DOMAIN = ("clear", "needs_review")
CTX_DOMAIN = ("EXECUTE", "GENERATE", "REFUSE", None)            # uppercase — the source's exact domain
BLAST_DOMAIN = ("low", "medium", "high", None)
MERGE_AUTONOMY_DOMAIN = ("regular", "lean", None)               # the contract's ACTUAL domain
STAGE_DOMAIN = ("lean", "scale", None)

# DEFAULT-DENY known-SAFE sets — the canonical vocabulary the roster (AC-ROST-6) + OSP render (AC-OSP-5)
# DEFAULT-DENY against. A value renders clear ONLY when it is in its field's known-safe set; everything
# else — unknown, gate_block, direct_main, worktree_on_main, needs_review, high, REFUSE, ANY null/novel
# token, AND a break-glass EXECUTE — is attention. Exposed so consumers do not re-declare (drift guard).
KNOWN_SAFE = {
    "gate_readiness": frozenset({"authorized", "gate_pass", "merged"}),
    "isolation": frozenset({"worktree"}),
    "security_flag": frozenset({"clear"}),
    "ctx_posture": frozenset({"EXECUTE", "GENERATE"}),          # EXECUTE clear ONLY when not break-glass
    "blast_radius": frozenset({"low", "medium"}),
}


def is_field_clear(field, value, *, break_glass=False):
    """The shared DEFAULT-DENY predicate. True iff `value` is an explicit known-SAFE value for `field`.
    `target_repo` is clear iff it resolved to a non-empty string. `ctx_posture: EXECUTE` is clear ONLY
    when not break-glass. Every other / unknown / novel / null risk value ⇒ attention (False)."""
    if field == "target_repo":
        return isinstance(value, str) and bool(value.strip())
    if field == "ctx_posture" and value == "EXECUTE" and break_glass:
        return False
    return value in KNOWN_SAFE.get(field, frozenset())


# ----------------------------------------------------------------------------- security-flag classifier
# A changed-path classifier over the governance-visible diff. A matching auth/secrets/supply-chain path
# OR an un-derivable diff ⇒ needs_review (binary; there is no non-needs_review escape on uncertainty).
_SECURITY_PATH_RE = re.compile(
    r"(?i)(?:"
    r"auth|secret|credential|password|token|/iam|[._-]iam|identity"           # auth / secrets / IAM
    r"|package(?:-lock)?\.json|requirements.*\.txt|pipfile|poetry\.lock"      # supply-chain manifests
    r"|go\.(?:mod|sum)|cargo\.(?:toml|lock)|pyproject\.toml|gemfile"
    r"|yarn\.lock|pnpm-lock|composer\.(?:json|lock)"
    # ER #162 (AC-LGC-4; feat-foundry-light-lane-guard-coverage): `/hooks/` left-anchored to
    # `(?:^|/)hooks/`, the M2.5 convention (see the `(?:^|/)` siblings below) — every path reaching
    # `derive_security_flag` is repo-relative, so the unanchored form never matched a root-level
    # `hooks/…` (only a nested `.claude/hooks/…`, where the parent supplies the slash). Widening-only:
    # every path that flagged before still flags (AC-LGC-5).
    r"|dockerfile|\.github/workflows/|(?:^|/)hooks/|\.claude-plugin/plugin\.json"  # supply-chain surfaces
    # M2.5 (AC-SFC-1/-2/-3): ADDITIVE-ONLY alternations closing the secret-scanning coverage consensus
    # gap (git-secrets/gitleaks/trufflehog/GitHub secret-scanning baseline). Each is anchored to a
    # basename/extension boundary so the widening tracks the intended file family and never becomes a
    # loose substring match (AC-SFC-4 negative control: `scripts/foo.py` / `environment.py` stay clear).
    r"|(?:^|/)\.env(?:\.[^/]*)?$"                                             # dotenv (AC-SFC-1)
    r"|\.pem$|\.key$"                                                        # private-key extensions (AC-SFC-2)
    r"|(?:^|/)id_(?:rsa|ed25519|ecdsa|dsa)(?:\.pub)?$"                       # OpenSSH private-key basenames (AC-SFC-2)
    r"|(?:^|/)\.npmrc$|(?:^|/)\.pypirc$|(?:^|/)\.netrc$"                    # registry/credential files (AC-SFC-3)
    r"|(?:^|/)\.dockercfg$|(?:^|/)\.docker/config\.json$"                   # registry/credential files (AC-SFC-3)
    r")"
)


def derive_security_flag(diff_paths):
    """AC-SMACH-1/3: binary, over the governance-visible diff. `diff_paths` is the changed-path set
    (PR / pushed branch vs the canonical base — NOT the session's unstaged worktree), or None when the
    diff cannot be derived. None (un-derivable) ⇒ needs_review (fail-closed); any auth/secrets/supply-
    chain path ⇒ needs_review; else clear."""
    if diff_paths is None:
        return "needs_review", "needs_review", "diff un-derivable (fail-closed)"
    hit = next((p for p in diff_paths if _SECURITY_PATH_RE.search(p or "")), None)
    if hit:
        return "needs_review", "ok", f"security-relevant path in diff: {_clean(hit)}"
    return "clear", "ok", "no auth/secrets/supply-chain path in the governance-visible diff"


# ----------------------------------------------------------------------------- isolation (AC-SMACH-4)
def derive_isolation(topo):
    """AC-SMACH-4: isolation from git TOPOLOGY (`--git-common-dir` vs `--git-dir`) AND the checked-out
    branch. `topo` is {git_dir, git_common_dir, branch, default_branch} (paths realpath-comparable) or
    None for a non-git / unreadable context.
      * canonical main checkout on the default branch          → direct_main      (⚠ flagged)
      * a LINKED worktree on the default branch                → worktree_on_main (⚠ flagged — the
                                                                 collision a topology-only check misses)
      * a linked worktree on a NON-default branch              → worktree         (safe)
      * unreadable / ambiguous / non-git                       → unknown          (attention)
    There is NO `dispatch_wt` value (native single-repo dispatch leaves no manifest; it is just a
    `worktree`). Honest caveat: the git env is session-controllable → the fail-direction is toward the
    flagged/attention state, never a silent `worktree`."""
    if not topo:
        return "unknown", "source_unavailable", "non-git or unreadable git context"
    gd, gcd = topo.get("git_dir"), topo.get("git_common_dir")
    branch, default = topo.get("branch"), topo.get("default_branch")
    if not gd or not gcd or not branch:
        return "unknown", "source_unavailable", "git topology/branch unreadable (ambiguous)"
    linked = os.path.realpath(gd) != os.path.realpath(gcd)   # a linked worktree has a distinct git-dir
    on_default = (default is not None and branch == default)
    if not linked:
        # canonical main checkout. On the default branch it is direct-on-main; otherwise it is a plain
        # branch checkout of the main clone — still NOT a worktree, fail toward the flagged state.
        if on_default:
            return "direct_main", "ok", "canonical checkout on the default branch (direct-on-main)"
        return "direct_main", "degraded", f"canonical checkout on branch {_clean(branch)!r} (non-worktree)"
    # a linked worktree
    if on_default:
        return "worktree_on_main", "ok", "linked worktree checked out on the default branch (collision risk)"
    if default is None:
        return "unknown", "degraded", "linked worktree but default branch unresolved (ambiguous)"
    return "worktree", "ok", f"linked worktree on non-default branch {_clean(branch)!r}"


# ----------------------------------------------------------------------------- gate-readiness (corpus)
def _contract_for(atom_or_spec, corpus_root):
    """The sibling acceptance-contract.yaml for an atom/spec key, resolved IN THE GOVERNANCE CORPUS
    (never the session repo). Returns (path, text) or (None, None)."""
    if not atom_or_spec:
        return None, None
    spec_path = atom_or_spec if os.path.isabs(atom_or_spec) else os.path.join(corpus_root, atom_or_spec)
    contract = os.path.join(os.path.dirname(spec_path), "acceptance-contract.yaml")
    if not os.path.isfile(contract):
        return None, None
    try:
        return contract, open(contract, encoding="utf-8", errors="replace").read()
    except OSError:
        return None, None


def derive_gate_readiness(atom_or_spec, corpus_root, pr_state=None):
    """AC-SMACH-1/3: a read-only RE-PROJECTION of governance/gate state — NOT a merge authority and it
    never re-runs/weakens the gate. Source: the contract authorized-trailer + governance read from the
    CORPUS at the canonical ref by `atom_or_spec`, plus best-effort PR state.

    `pr_state` is a best-effort discriminated value: None (no PR info), "error" (gh errored), or one of
    {"merged","gate_pass","gate_block","pr_open"}.

    Precedence: merged ▷ gate verdict ▷ pr_open ▷ authorized ▷ unauthorized. ANY unresolved/ambiguous
    input (no resolvable atom_or_spec, corpus unreadable, gh error) ⇒ `unknown` (attention) — NEVER
    `gate_pass`/`merged` on uncertainty."""
    if not atom_or_spec:
        return "unknown", "source_unavailable", "no resolvable atom_or_spec key (fail-closed)"
    path, txt = _contract_for(atom_or_spec, corpus_root)
    if txt is None:
        return "unknown", "source_unavailable", "contract not resolvable in the governance corpus (fail-closed)"
    if pr_state == "error":
        return "unknown", "degraded", "PR state un-derivable (gh error) — fail-closed"
    authorized = re.search(r"^\s*auth_seq:\s*[1-9]", txt, re.M) is not None
    # PR state takes precedence per the derivation predicate (merged ▷ gate ▷ pr_open).
    if pr_state == "merged":
        return "merged", "ok", "corpus-authorized + PR merged"
    if pr_state in ("gate_pass", "gate_block"):
        return pr_state, "ok", f"corpus-authorized + merge-floor {pr_state}"
    if pr_state == "pr_open":
        return "pr_open", "ok", "corpus-authorized + PR open (pre-gate)"
    if authorized:
        return "authorized", "ok", "corpus authorized-trailer present (no PR state)"
    return "unauthorized", "ok", "no authorized-trailer in the corpus contract"


# ----------------------------------------------------------------------------- mode (corpus + stage)
def _stage_mode(corpus_root, stage_override=None):
    """The workspace stage mode from the corpus CLAUDE.md `**Current mode:** \\`lean\\`` line."""
    if stage_override is not None:
        return stage_override if stage_override in ("lean", "scale") else None
    p = os.path.join(corpus_root, "CLAUDE.md")
    if not os.path.isfile(p):
        return None
    try:
        txt = open(p, encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    m = re.search(r"Current mode:\**\s*`?(\w+)`?", txt)
    return m.group(1) if (m and m.group(1) in ("lean", "scale")) else None


def derive_mode(atom_or_spec, corpus_root, stage_override=None):
    """AC-SMACH-1: {stage, merge_autonomy}. stage ∈ {lean,scale} (workspace stage mode); merge_autonomy
    ∈ {regular,lean} (the contract's ACTUAL `merge_autonomy_mode` domain). Read from the corpus;
    unresolvable ⇒ null (derived-authoritative for what it can read)."""
    stage = _stage_mode(corpus_root, stage_override)
    _path, txt = _contract_for(atom_or_spec, corpus_root)
    merge_autonomy = None
    if txt:
        m = re.search(r"^\s*merge_autonomy_mode:\s*(\w+)", txt, re.M)
        if m and m.group(1) in ("regular", "lean"):
            merge_autonomy = m.group(1)
    outcome = "ok" if (stage is not None and merge_autonomy is not None) else "degraded"
    return {"stage": stage, "merge_autonomy": merge_autonomy}, outcome, "workspace stage + contract merge_autonomy"


# ----------------------------------------------------------------------------- target_repo (corpus)
def derive_target_repo(atom_or_spec, corpus_root):
    """AC-SMACH-1/3: the contract `target_repo` IN THE CORPUS (authoritative) — NOT the session repo's
    `.claude/foundry-project.json` (session-writable). Unresolved ⇒ null (rendered attention: a
    repo-scoped verdict MUST NOT run against the wrong/default repo)."""
    if not atom_or_spec:
        return None, "source_unavailable", "no atom_or_spec key"
    _path, txt = _contract_for(atom_or_spec, corpus_root)
    if txt is None:
        return None, "source_unavailable", "contract not resolvable in the corpus"
    m = re.search(r"^\s*target_repo:\s*(\S+)", txt, re.M)
    if m:
        return _clean(m.group(1)), "ok", "contract target_repo (corpus)"
    return None, "ok", "contract declares no target_repo (single-repo / self-host)"


# ----------------------------------------------------------------------------- ctx-posture + blast
def derive_infra(*, present, ctx_state=None, blast_tier=None):
    """AC-SMACH-5 (conditional, infra-only, READ-ONLY). The infra fields populate ONLY when the session
    has DERIVABLE infra context (`present` — a CTX session resolved). For a non-infra session both are
    `null` (the discriminated outcome distinguishes non-infra from infra-but-no-impact-run).

    `present=False`                → blast_radius=null, ctx_posture=null  (non-infra; source_unavailable)
    `present=True` + ctx_state      → ctx_posture = resolve_posture(ctx_state).decision; a present-but-
                                      stale/bad CTX resolves to REFUSE (the source's own fail-closed
                                      value, NEVER squashed to null); break-glass (audited prod-EXECUTE)
                                      sets break_glass=True so consumers render it as attention, never a
                                      benign EXECUTE.
    blast_radius: the id-impact tier (advisory, may be stale) or null when no run — NEVER a false low.

    Returns (blast_radius, ctx_posture, break_glass, outcome, reason)."""
    if not present:
        return None, None, False, "source_unavailable", "no CTX session (non-infra)"
    import foundry_ctx_posture as cp
    posture = cp.resolve_posture(ctx_state) if ctx_state is not None else cp.Posture(cp.REFUSE, reason="no ctx_state")
    ctx_posture = posture.decision
    break_glass = bool(getattr(posture, "audited", False))
    blast = blast_tier if blast_tier in ("low", "medium", "high") else None
    reason = _clean(posture.reason)
    outcome = "ok" if ctx_posture in ("EXECUTE", "GENERATE") and not break_glass else "degraded"
    return blast, ctx_posture, break_glass, outcome, reason


# ----------------------------------------------------------------------------- assembly
def unavailable_record(session_id, reason):
    """AC-SMACH-5: a MISSING machinery block (join miss / total source failure) is itself a discriminated
    state — distinct from a populated record with null fields."""
    return {"session_id": session_id, "status": "unavailable", "reason": _clean(reason)}


def derive_machinery(rec, *, corpus_root, invoking_sid=None,
                     git_topo=None, pr_state=None,
                     diff_paths=None, infra_present=False, ctx_state=None,
                     blast_tier=None, stage_override=None):
    """AC-SMACH-1/2/5: assemble the typed machinery sub-record for one session-registry record,
    joined by `session_id`. Corpus-derived verdicts use the record's `atom_or_spec`; the session-
    declared env fields (`isolation`, infra) are the INVOKING process's own. A purely
    ADDITIVE overlay — it adds fields, never overrides a registry/native field. All probes are injectable
    (hermetic selftest); the live `main` supplies real read-only probes."""
    sid = rec.get("session_id")
    if not sid:
        return unavailable_record(sid, "registry record has no session_id (join miss)")
    atom = rec.get("atom_or_spec")
    is_invoking = (invoking_sid is not None and sid == invoking_sid)

    iso, iso_o, iso_r = derive_isolation(git_topo) if is_invoking else (
        "unknown", "source_unavailable", "isolation is the invoking process's git env (null cross-session)")
    gate, gate_o, gate_r = derive_gate_readiness(atom, corpus_root, pr_state)
    mode, mode_o, mode_r = derive_mode(atom, corpus_root, stage_override)
    # security_flag is derived from the INVOKING process's governance-visible diff (the single diff probe),
    # so it is invoking-process-only exactly like isolation/infra — a cross-session row must NOT inherit the
    # invoking session's verdict (that would stamp a false `clear` on another session, a fail-closed
    # violation). Cross-session ⇒ needs_review/source_unavailable (fail toward attention).
    sec, sec_o, sec_r = derive_security_flag(diff_paths) if is_invoking else (
        "needs_review", "source_unavailable", "diff is the invoking process's (null cross-session)")
    repo, repo_o, repo_r = derive_target_repo(atom, corpus_root)
    blast, ctx, break_glass, infra_o, infra_r = (
        derive_infra(present=infra_present, ctx_state=ctx_state, blast_tier=blast_tier)
        if is_invoking else (None, None, False, "source_unavailable", "infra context is the invoking process's"))

    return {
        "session_id": sid,
        "status": "ok",
        "isolation": iso,
        "gate_readiness": gate,
        "mode": mode,
        "security_flag": sec,
        "target_repo": repo,
        "blast_radius": blast,
        "ctx_posture": ctx,
        "break_glass": break_glass,
        # infra presence is the INVOKING process's CTX probe — do not leak it onto cross-session rows
        # (blast_radius/ctx_posture are already null cross-session; keep `infra` consistent).
        "infra": bool(infra_present) if is_invoking else False,
        # discriminated outcome per source (AC-SMACH-5): ok | degraded | source_unavailable + reason.
        "sources": {
            "isolation": {"outcome": iso_o, "reason": _clean(iso_r)},
            "gate_readiness": {"outcome": gate_o, "reason": _clean(gate_r)},
            "mode": {"outcome": mode_o, "reason": _clean(mode_r)},
            "security_flag": {"outcome": sec_o, "reason": _clean(sec_r)},
            "target_repo": {"outcome": repo_o, "reason": _clean(repo_r)},
            "infra": {"outcome": infra_o, "reason": _clean(infra_r)},
        },
    }


# ----------------------------------------------------------------------------- live probes (read-only)
def _git_topo(cwd):
    """Read-only git topology + branch probe at `cwd`. Returns the topo dict or None (non-git/unreadable).
    NEVER mutates (only rev-parse / symbolic-ref reads). The git env is session-controllable — the
    caller's fail-direction handles that (AC-SMACH-4 honest caveat)."""
    def _g(*args):
        try:
            r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=5)
            return r.stdout.strip() if r.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None
    git_dir = _g("rev-parse", "--absolute-git-dir")
    if not git_dir:
        return None
    common = _g("rev-parse", "--path-format=absolute", "--git-common-dir") or git_dir
    branch = _g("rev-parse", "--abbrev-ref", "HEAD")
    default = None
    head_ref = _g("symbolic-ref", "--short", "refs/remotes/origin/HEAD")  # e.g. "origin/main"
    if head_ref and "/" in head_ref:
        default = head_ref.split("/", 1)[1]
    return {"git_dir": git_dir, "git_common_dir": common, "branch": branch, "default_branch": default}


def _ctx_probe_present():
    """Classify CTX presence for the infra discriminator (AC-SMACH-5). Returns (present, ctx_state):
      * ctx binary absent / no active session → (False, None)  — non-infra
      * CTX session present (even stale/bad)   → (True, state)  — infra context exists (REFUSE if bad)
    Read-only (only `ctx status --json`)."""
    try:
        import foundry_ctx_posture as cp
    except Exception:
        return False, None
    state = cp.probe_ctx()
    if state.reachable:
        return True, state
    d = (state.detail or "")
    if d.startswith("ctx probe failed to run") or d == "no active CTX session (active_env is null)":
        return False, None      # genuinely no CTX → non-infra
    return True, state          # present-but-bad → infra context exists → REFUSE


def _governance_diff_paths(topo):
    """Best-effort governance-visible diff path set (PR/pushed-branch vs canonical base). Returns the
    changed-path list or None (un-derivable ⇒ fail-closed needs_review). Read-only."""
    if not topo or not topo.get("git_dir") or not topo.get("branch") or not topo.get("default_branch"):
        return None
    cwd = os.path.dirname(topo["git_dir"])
    base = f"origin/{topo['default_branch']}"
    try:
        r = subprocess.run(["git", "-C", cwd, "diff", "--name-only", f"{base}...HEAD"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return None
        return [p for p in r.stdout.splitlines() if p.strip()]
    except (OSError, subprocess.SubprocessError):
        return None


def derive_all(corpus_root, invoking_sid=None, registry_result=None):
    """Live entry: join machinery onto every session-registry record. Read-only end-to-end."""
    if registry_result is None:
        import importlib.util
        p = os.path.join(HERE, "foundry-fleet-session-registry.py")
        spec = importlib.util.spec_from_file_location("fleet_session_registry", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        registry_result = mod.list_sessions(corpus_root)
    invoking_sid = invoking_sid or os.environ.get("CLAUDE_CODE_SESSION_ID")
    out = {"status": registry_result.get("status"), "machinery": {}}
    if registry_result.get("status") == "native_unavailable":
        return out
    # invoking-process-only probes (resolved once; null cross-session).
    # the invoking process's OWN cwd (read-only) — NOT the registry's scrubbed display `repo` (reusing a
    # scrubbed display value as an operational path is the repo-vs-key anti-pattern; a scrubbed path would
    # silently degrade isolation/diff to fail-closed). We are the invoking process, so os.getcwd() is it.
    topo = _git_topo(os.getcwd())
    infra_present, ctx_state = _ctx_probe_present()
    diff_paths = _governance_diff_paths(topo)
    for rec in registry_result.get("sessions", []):
        m = derive_machinery(rec, corpus_root=corpus_root, invoking_sid=invoking_sid,
                             git_topo=topo, pr_state=None,
                             diff_paths=diff_paths, infra_present=infra_present, ctx_state=ctx_state)
        out["machinery"][rec.get("session_id")] = m
    return out


# ----------------------------------------------------------------------------- selftest (hermetic)
def _selftest():
    import foundry_ctx_posture as cp
    results = []

    def check(token, ok):
        results.append((token, ok))
        print(f"  {token}: {'PASS' if ok else 'FAIL'}")

    import tempfile
    with tempfile.TemporaryDirectory() as corpus:
        # --- a governance corpus: an AUTHORIZED contract + a CLAUDE.md stage line ---
        spec_dir = os.path.join(corpus, "specs", "x")
        os.makedirs(spec_dir)
        open(os.path.join(spec_dir, "feat-x.md"), "w").write("# x\n")
        open(os.path.join(spec_dir, "acceptance-contract.yaml"), "w").write(
            "spec_ref: specs/x/feat-x.md\n"
            "authorized:\n  auth_seq: 1\n  merge_autonomy_mode: lean\n")
        open(os.path.join(corpus, "CLAUDE.md"), "w").write("## Stage mode\n**Current mode:** `lean`\n")
        spec_key = "specs/x/feat-x.md"
        sid = "0927afe0-4ed1-4401-abb6-85fa7c380564"
        other = "12340000-0000-4000-8000-000000000000"
        rec = {"session_id": sid, "atom_or_spec": spec_key}
        rec_unauth = {"session_id": other, "atom_or_spec": "specs/none/feat-none.md"}

        # === AC-SMACH-1: typed record, every field sourced; reject malformed =================
        # invoking record with a worktree-on-main git topology + non-infra.
        topo_wtmain = {"git_dir": "/wt/.git/worktrees/w", "git_common_dir": "/main/.git",
                       "branch": "main", "default_branch": "main"}
        m = derive_machinery(rec, corpus_root=corpus, invoking_sid=sid, git_topo=topo_wtmain,
                             pr_state=None, diff_paths=["scripts/foundry-fleet-roster.py"],
                             infra_present=False)
        typed = (m["session_id"] == sid and set(m).issuperset(
            {"isolation", "gate_readiness", "mode", "security_flag",
             "target_repo", "blast_radius", "ctx_posture", "sources"}))
        mode_ok = m["mode"] == {"stage": "lean", "merge_autonomy": "lean"}
        gate_ok = m["gate_readiness"] == "authorized"        # corpus authorized-trailer, no PR
        # a MISSING-block (join miss) is its own discriminated state, not a populated record.
        miss = unavailable_record(None, "no session_id")["status"] == "unavailable"
        check("AC-SMACH-1 typed-machinery-record-sourced", typed and mode_ok and gate_ok and miss)

        # === AC-SMACH-2: derived-not-self-reported (corpus verdict not session-forgeable) ====
        # A session that mis-declares its subject (points atom_or_spec at an UNauthorized atom) cannot
        # forge an `authorized` verdict — the corpus read governs. And a build-provenance marker / a
        # foundry-project.json in the SESSION repo is NEVER consulted: gate/mode/target_repo come ONLY
        # from the corpus contract. Prove the corpus read drives the verdict, not any session-writable file.
        m_unauth = derive_machinery(rec_unauth, corpus_root=corpus, invoking_sid=other)
        forge_blocked = m_unauth["gate_readiness"] == "unknown"     # unresolvable corpus key ⇒ fail-closed
        # cross-session row: isolation/infra are the invoking process's → null cross-session,
        # but the corpus-keyed gate verdict still derives from the corpus for the declared (authorized) atom.
        m_cross = derive_machinery(rec, corpus_root=corpus, invoking_sid=other,
                                   diff_paths=["scripts/foundry-fleet-roster.py"])  # rec is NOT the invoker
        # every invoking-process-only field is null/attention cross-session — incl. security_flag (a clean
        # invoking diff must NOT stamp a false `clear` on another session's row) and infra.
        cross_null = (m_cross["isolation"] == "unknown"
                      and m_cross["ctx_posture"] is None and m_cross["security_flag"] == "needs_review"
                      and m_cross["infra"] is False)
        cross_corpus = m_cross["gate_readiness"] == "authorized"   # corpus verdict still derives
        # the module exposes NO reader of the build-provenance marker / foundry-project.json.
        src = open(os.path.abspath(__file__), encoding="utf-8").read()
        no_marker_source = "build-provenance" not in src.split("THE TRUST MODEL")[1].split("CLI:")[0] \
            or "NEVER read" in src  # the marker is named only in the trust-model prohibition
        check("AC-SMACH-2 derived-not-self-reported",
              forge_blocked and cross_null and cross_corpus and no_marker_source)

        # === AC-SMACH-3: gate AND security fail-closed ========================================
        gate_noatom = derive_gate_readiness(None, corpus)[0] == "unknown"
        gate_nocontract = derive_gate_readiness("specs/ghost/feat.md", corpus)[0] == "unknown"
        gate_gherror = derive_gate_readiness(spec_key, corpus, pr_state="error")[0] == "unknown"
        gate_block_attn = derive_gate_readiness(spec_key, corpus, pr_state="gate_block")[0] == "gate_block"
        no_false_pass = derive_gate_readiness(spec_key, corpus, pr_state="error")[0] != "gate_pass"
        sec_undiff = derive_security_flag(None)[0] == "needs_review"          # un-derivable ⇒ needs_review
        sec_authpath = derive_security_flag(["scripts/foundry_authz.py"])[0] == "needs_review"
        sec_supply = derive_security_flag([".claude-plugin/plugin.json"])[0] == "needs_review"
        sec_clear = derive_security_flag(["scripts/foundry-fleet-roster.py"])[0] == "clear"
        sec_binary = set(SECURITY_DOMAIN) == {"clear", "needs_review"}        # no `unknown` escape
        # ctx_posture preserves REFUSE (present-but-stale CTX), never squashed to null:
        stale = cp.CtxState(reachable=False, detail="CTX state not OK (stale_state='STALE'); refuse")
        ctx_refuse = derive_infra(present=True, ctx_state=stale)[1] == "REFUSE"
        # break-glass (audited prod-EXECUTE) is flagged, not a benign EXECUTE-clear:
        bg = cp.CtxState(reachable=True, active_env="prod", production_flag=True, guard_state="OFF",
                         stale_state="OK")
        b_blast, b_ctx, b_break, _o, _r = derive_infra(present=True, ctx_state=bg)
        bg_attn = (b_ctx == "EXECUTE" and b_break is True
                   and is_field_clear("ctx_posture", b_ctx, break_glass=b_break) is False)
        # target_repo unresolved ⇒ attention (not clear)
        repo_attn = is_field_clear("target_repo", derive_target_repo(spec_key, corpus)[0]) is False
        check("AC-SMACH-3 gate-and-security-fail-closed",
              gate_noatom and gate_nocontract and gate_gherror and gate_block_attn and no_false_pass
              and sec_undiff and sec_authpath and sec_supply and sec_clear and sec_binary
              and ctx_refuse and bg_attn and repo_attn)

        # === AC-SMACH-4: isolation from git topology AND branch; flagged-by-default ==========
        direct = derive_isolation({"git_dir": "/main/.git", "git_common_dir": "/main/.git",
                                   "branch": "main", "default_branch": "main"})[0] == "direct_main"
        wtmain = derive_isolation(topo_wtmain)[0] == "worktree_on_main"        # ⚠ topology-only misses this
        wt_safe = derive_isolation({"git_dir": "/wt/.git/worktrees/w", "git_common_dir": "/main/.git",
                                    "branch": "impl/x", "default_branch": "main"})[0] == "worktree"
        nongit = derive_isolation(None)[0] == "unknown"
        ambiguous = derive_isolation({"git_dir": "/wt/.git/worktrees/w", "git_common_dir": "/main/.git",
                                      "branch": "impl/x", "default_branch": None})[0] == "unknown"
        no_dispatch_wt = "dispatch_wt" not in ISOLATION_DOMAIN
        # the flagged states are NOT clear under DEFAULT-DENY; only `worktree` is.
        flagged = (is_field_clear("isolation", "direct_main") is False
                   and is_field_clear("isolation", "worktree_on_main") is False
                   and is_field_clear("isolation", "unknown") is False
                   and is_field_clear("isolation", "worktree") is True)
        check("AC-SMACH-4 isolation-from-git-direct-flagged",
              direct and wtmain and wt_safe and nongit and ambiguous and no_dispatch_wt and flagged)

        # === AC-SMACH-5: conditional, additive, read-only, sanitized, differentiated ========
        # non-infra session → both infra fields null (distinct from infra-but-no-run).
        non_infra = derive_infra(present=False) == (None, None, False, "source_unavailable", "no CTX session (non-infra)")
        # infra-present, healthy dev CTX, no impact run → EXECUTE + blast null (not a false low).
        dev = cp.CtxState(reachable=True, active_env="dev", production_flag=False, guard_state="ON",
                          stale_state="OK")
        i_blast, i_ctx, i_break, i_o, _ir = derive_infra(present=True, ctx_state=dev, blast_tier=None)
        infra_run = (i_ctx == "EXECUTE" and i_blast is None and i_o == "ok")
        # discriminated outcome per source present on a record.
        disc = (m["sources"]["gate_readiness"]["outcome"] == "ok"
                and m_cross["sources"]["isolation"]["outcome"] == "source_unavailable")
        # sanitization: a secret + control/ANSI/newline in a sourced string is scrubbed AND neutralized.
        evil_topo = {"git_dir": "/wt/.git/worktrees/w", "git_common_dir": "/main/.git",
                     "branch": "feat/sk-abcdef0123456789abcdef\x1b[31m\n", "default_branch": "x"}
        ev = derive_isolation(evil_topo)[2]
        sanitized = "sk-abcdef" not in ev and "\x1b" not in ev and "\n" not in ev
        # target_repo with a planted secret/escape is scrubbed.
        open(os.path.join(spec_dir, "acceptance-contract.yaml"), "a").write("target_repo: r-sk-abcdef0123456789abcdef\n")
        tr = derive_target_repo(spec_key, corpus)[0]
        tr_clean = tr is not None and "sk-abcdef" not in tr
        # additive overlay: the record adds fields, never an override key matching a native/registry field.
        additive = "repo" not in m and "name" not in m and "native_summary" not in m
        check("AC-SMACH-5 conditional-additive-readonly-sanitized",
              non_infra and infra_run and disc and sanitized and tr_clean and additive)

    ok = all(v for _, v in results)
    print("FLEET-SESSION-MACHINERY-SELFTEST-" + ("GREEN" if ok else "RED"))
    return ok


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="foundry-fleet-session-machinery")
    ap.add_argument("cmd", nargs="?", choices=["derive"], default=None)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--root", default=os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if _selftest() else 1)
    if a.cmd == "derive":
        print(json.dumps(derive_all(a.root), indent=2))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
