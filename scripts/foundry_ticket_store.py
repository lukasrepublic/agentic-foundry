"""foundry_ticket_store — where the active ticket lives, and the one redactor its outputs pass through.

Library (no argparse/__main__); imported by scripts/foundry-ticket.py, hooks/foundry-paper-guard.py and
hooks/foundry-ticket-stop.py.

The ticket is stored INSIDE THE GIT DIR (`git rev-parse --git-path foundry-ticket.json`, per worktree),
never in the working tree: a file under the work tree could be committed on a fork branch and checked
out by `gh pr checkout`, and the Stop hook would then run an attacker's command unattended (security
review of #270, B1). Nothing under `.claude/` in the work tree is ever read as a ticket. A project that
is not a git repository has no ticket.
"""
from __future__ import annotations

import calendar
import hashlib
import hmac
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

TICKET_BASENAME = "foundry-ticket.json"
CLAIMS_BASENAME = "foundry-claims.json"   # gn-08: the local execution record of claimed issues (a cache, never authority)
LEASE_RELPATH = Path(".foundry") / "runtime" / "lead.json"   # gn-08: the local duplicate-lead lease

_SECRET_ASSIGN = re.compile(
    r"(?i)\b([A-Za-z_][A-Za-z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|APIKEY|API_KEY|_KEY|_PAT))=(\S+)")
_BEARER = re.compile(r"(?i)\b(authorization:\s*bearer)\s+\S+")
_GH_TOKEN = re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")


_URL_USERINFO = re.compile(r"(//)[^@/\s]+@")   # `https://user:<token>@host/...` (the git-discipline hook's own pattern)


def redact(s: str) -> str:
    s = _URL_USERINFO.sub(r"\1", s or "")
    s = _SECRET_ASSIGN.sub(r"\1=<redacted>", s)
    s = _BEARER.sub(r"\1 <redacted>", s)
    return _GH_TOKEN.sub("<redacted>", s)


def project_root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and Path(env).is_dir():
        return Path(env).resolve()
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, check=True, timeout=10).stdout.strip()
        return Path(top).resolve()
    except Exception:
        return Path.cwd().resolve()


def ticket_file(root: Path | None = None) -> Path | None:
    """The git-dir path of the ticket for `root` (None when root is not a git repository)."""
    return _git_path(TICKET_BASENAME, root)


class ClaimsUnreadable(ValueError):
    """The claims file exists but is unreadable or malformed — never treated as "no claim"."""


def _git_common_path(basename: str, root: Path | None = None) -> Path | None:
    root = root or project_root()
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--git-common-dir"],
                             capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    p = Path(out.stdout.strip())
    return (p if p.is_absolute() else (root / p)).resolve() / basename


def claims_file(root: Path | None = None) -> Path | None:
    """Where claim records are WRITTEN: the repository's common git dir, shared by the main checkout and
    every linked worktree (None when root is not a git repository)."""
    return _git_common_path(CLAIMS_BASENAME, root)


def legacy_claims_file(root: Path | None = None) -> Path | None:
    """The pre-fix per-worktree location; read only when the common file does not exist."""
    return _git_path(CLAIMS_BASENAME, root)


def norm_repo(repo) -> str:
    """ASCII-lowercase `owner/name` (GitHub logins and repo names are case-insensitive)."""
    return "".join(c.lower() if "A" <= c <= "Z" else c for c in str(repo or ""))


def claim_key(repo, number) -> str:
    return f"{norm_repo(repo)}#{number}"


def _parse_claims_file(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ClaimsUnreadable(f"{p.name}: {e}")
    if not isinstance(d, dict):
        raise ClaimsUnreadable(f"{p.name}: not a JSON object")
    out: dict = {}
    for k, v in d.items():
        repo, sep, num = str(k).rpartition("#")
        nk = f"{norm_repo(repo)}#{num}" if sep else str(k)
        if nk in out:   # two raw keys normalise alike: the newest claimed_at wins
            old = out[nk]
            ts = lambda r: str(r.get("claimed_at") or "") if isinstance(r, dict) else ""  # noqa: E731
            if ts(v) < ts(old):
                continue
        out[nk] = v
    return out


def read_claims(root: Path | None = None) -> tuple[dict, bool]:
    """(claims keyed by lower-cased repo#N, found). found=False when no claims file exists anywhere.
    The common-dir file and the legacy per-worktree file are MERGED (common wins on a collision, legacy-only
    keys are kept). Raises ClaimsUnreadable when either present file is unreadable or not a JSON object."""
    common, legacy = claims_file(root), legacy_claims_file(root)
    paths = [p for p in (legacy, common) if p is not None and p.is_file()]
    if legacy is not None and common is not None and legacy.resolve() == common.resolve():
        paths = [common] if common.is_file() else []
    merged: dict = {}
    for p in paths:          # legacy first, then common overlays it
        merged.update(_parse_claims_file(p))
    return merged, bool(paths)


class claims_lock:
    """Exclusive advisory lock (flock on a sibling file in the common dir) around a claims read-modify-write."""

    def __init__(self, root: Path | None = None):
        p = claims_file(root)
        self.path = p.with_name(CLAIMS_BASENAME + ".lock") if p else None
        self.fd = None

    def __enter__(self):
        import fcntl
        if self.path is None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)   # closing releases the flock
            self.fd = None
        return False


def update_claims(root: Path | None, fn) -> Path:
    """Locked read-modify-write of the common claims file. `fn(claims_dict)` mutates in place. Raises
    ClaimsUnreadable (never overwrites an unreadable file)."""
    p = claims_file(root)
    if p is None:
        raise ClaimsUnreadable("not a git repository — the claim record lives in the git dir")
    with claims_lock(root):
        claims, _ = read_claims(root)
        fn(claims)
        atomic_write_json(p, claims)
    return p


def claim_done_digest(root: Path | None, repo: str, number) -> str | None:
    """The vetted Done-means digest a `claim` recorded for repo#number, or None when there is no file / no
    record carrying one (the unclaimed path). Raises ClaimsUnreadable when the file is present but unusable
    or the record is malformed."""
    claims, found = read_claims(root)
    if not found:
        return None
    rec = claims.get(claim_key(repo, number))
    if rec is None:
        return None
    if not isinstance(rec, dict):
        raise ClaimsUnreadable(f"record for {claim_key(repo, number)} is malformed")
    if "done_digest" not in rec:
        return None
    dd = rec["done_digest"]
    if not isinstance(dd, str) or not dd:
        raise ClaimsUnreadable(f"record for {claim_key(repo, number)} has a malformed done_digest")
    return dd


def lease_file(root: Path | None = None) -> Path:
    return (root or project_root()) / LEASE_RELPATH


def _inside(root, path) -> bool:
    r, p = os.path.realpath(root), os.path.realpath(path)
    return p == r or p.startswith(r + os.sep)


def confined_makedirs(ws, d) -> None:
    """makedirs whose containment is decided on the nearest EXISTING ancestor BEFORE anything is created, and
    re-checked after (the same rule as foundry_migrate.ensure_dir, kept stdlib-only here so the always-on
    runtime writers need no YAML dependency). A symlinked directory that leaves the workspace is a refusal."""
    cur = str(d)
    while not os.path.lexists(cur):
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    if not os.path.isdir(cur) or not _inside(ws, cur):
        raise PermissionError(f"refusing to create a directory outside the workspace: {d}")
    os.makedirs(d, exist_ok=True)
    if not _inside(ws, d):
        raise PermissionError(f"refusing to use a directory outside the workspace: {d}")


def ensure_runtime_dir(ws) -> Path:
    """`<ws>/.foundry/runtime`, confined, with a `*` .gitignore so a runtime file is never committed."""
    d = Path(ws) / ".foundry" / "runtime"
    confined_makedirs(ws, d)
    gi = d / ".gitignore"
    try:
        fd = os.open(gi, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    except FileExistsError:
        return d
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("*\n")
    return d


def atomic_write_json(path: Path, doc: dict) -> None:
    """temp file in the same directory + rename, so a reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _git_path(basename: str, root: Path | None = None) -> Path | None:
    root = root or project_root()
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--git-path", basename],
                             capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    p = Path(out.stdout.strip())
    return p if p.is_absolute() else (root / p)


def command_digest(cmd: str) -> str:
    return hashlib.sha256((cmd or "").encode("utf-8")).hexdigest()


# ---- gv-16: the signed CI-allow lists --------------------------------------------------------------------------
# `ci_allowed` (written by the ask-gated `ci-allow` verb) and `issue_paper_allowed` (a trusted issue author's list,
# recorded by `start`) lift the paper guard's CI class. They live in <git-dir>/foundry-ticket.json, a file an agent
# can edit by hand, so both are covered by an HMAC-SHA256 under a per-git-dir secret (<git-dir>/foundry-ticket.key,
# mode 0600, created by those two verbs). The guard honours the lists only when the signature verifies; a hand
# edit leaves the CI guard in force. This raises the bar from "edit one JSON field" to "read the key and sign";
# it is a visibility control for the operator's own test pass, not a defence against an agent that reads the key.
KEY_BASENAME = "foundry-ticket.key"
SIG_FIELD = "lists_sig"


def _lists_payload(doc: dict) -> bytes:
    def lst(k):
        v = doc.get(k)
        return [str(x) for x in v] if isinstance(v, list) else []
    return json.dumps({"v": 1, "issue": doc.get("issue"), "repo": str(doc.get("repo") or ""),
                       "ci_allowed": lst("ci_allowed"), "issue_paper_allowed": lst("issue_paper_allowed")},
                      sort_keys=True, separators=(",", ":")).encode("utf-8")


def _read_key(root: Path | None = None, create: bool = False) -> bytes | None:
    p = _git_path(KEY_BASENAME, root)
    if p is None:
        return None
    try:
        got = p.read_bytes()
        if got.strip():
            return got.strip()
    except OSError:
        pass
    if not create:
        return None
    new = os.urandom(32).hex().encode("ascii")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(new + b"\n")
        return new
    except FileExistsError:
        try:
            return p.read_bytes().strip() or None
        except OSError:
            return None
    except OSError:
        return None


def sign_ticket_lists(doc: dict, root: Path | None = None, create: bool = False) -> bool:
    """Set doc[lists_sig] over the two CI-lifting lists. False (and no signature) when no key is available."""
    key = _read_key(root, create=create)
    if key is None:
        doc.pop(SIG_FIELD, None)
        return False
    doc[SIG_FIELD] = hmac.new(key, _lists_payload(doc), hashlib.sha256).hexdigest()
    return True


def verify_ticket_lists(doc: dict, root: Path | None = None) -> bool:
    """True only when the stored signature matches the lists under this git dir's key. Never creates a key."""
    sig = doc.get(SIG_FIELD) if isinstance(doc, dict) else None
    key = _read_key(root, create=False)
    if not isinstance(sig, str) or key is None:
        return False
    return hmac.compare_digest(sig, hmac.new(key, _lists_payload(doc), hashlib.sha256).hexdigest())


# ---- gv-11: claim leases (operating-model rule B5) ---------------------------------------------------------
# A claim record is a LEASE: `heartbeat_at` is refreshed by `lead renew` and `reconcile`; a lease with no heartbeat
# for `expires_after` (policy `claims.expires_after`, default 2h) is EXPIRED, and ONLY an expired lease can be taken
# over by another session (recorded in the new record as `taken_over_from`). A long chunk continues under a SUCCESSOR
# claim (`claim --successor-of`), which is not a takeover. The store stays a local cache in the git common dir, never
# authority; it is read fail-CLOSED by the claim-required push clause (hooks/foundry-git-discipline.sh clause (m)).
DEFAULT_EXPIRES_AFTER = 2 * 3600


def iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def epoch(stamp) -> float:
    """Epoch seconds of a `%Y-%m-%dT%H:%M:%SZ` stamp; 0.0 when absent/malformed (i.e. long expired)."""
    try:
        return float(calendar.timegm(time.strptime(str(stamp), "%Y-%m-%dT%H:%M:%SZ")))
    except (ValueError, OverflowError):
        return 0.0


def parse_expires(text) -> int | None:
    m = re.fullmatch(r"([1-9][0-9]*)([mhd])", str(text).strip())
    return int(m.group(1)) * {"m": 60, "h": 3600, "d": 86400}[m.group(2)] if m else None


def claims_policy(root: Path | None = None) -> tuple[bool, int]:
    """(claims.enabled, claims.expires_after in seconds) from the operating-model policy. An absent, unreadable or
    broken policy yields the defaults (enabled, 2h): a policy problem never turns the lease off."""
    root = root or project_root()
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_foundry_operating_model_for_claims",
                                                      Path(__file__).resolve().parent / "foundry_operating_model.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        pol = mod.load(str(root))
        secs = parse_expires(pol.get("claims.expires_after"))
        return bool(pol.get("claims.enabled")), secs or DEFAULT_EXPIRES_AFTER
    except Exception:
        return True, DEFAULT_EXPIRES_AFTER


def lease_state(rec, now: float, default_expires: int = DEFAULT_EXPIRES_AFTER) -> dict:
    """{"state": live|expired, "age": seconds since the last heartbeat, "expires_after": seconds,
    "heartbeat_at": stamp}. The heartbeat falls back to `claimed_at` (a pre-lease record); a missing, malformed or
    FUTURE stamp is expired (a lease that cannot be shown live is not live)."""
    rec = rec if isinstance(rec, dict) else {}
    stamp = rec.get("heartbeat_at") or rec.get("claimed_at")
    exp = rec.get("expires_after_s")
    # the record's own length can only SHORTEN the policy's: a stored 99999 never outlives `claims.expires_after`
    exp = min(exp, default_expires) if isinstance(exp, int) and not isinstance(exp, bool) and exp > 0 \
        else default_expires
    t = epoch(stamp)
    age = now - t if t else float("inf")
    return {"state": "live" if 0 <= age < exp else "expired", "age": age, "expires_after": exp,
            "heartbeat_at": stamp}


def heartbeat_claims(root: Path | None, session_id: str, now: float) -> list[str]:
    """Refresh `heartbeat_at` on every claim THIS session holds. Never touches another session's claim. Returns
    the keys touched. Raises ClaimsUnreadable (never overwrites an unreadable file); writes nothing when there is
    no claims file."""
    claims, found = read_claims(root)
    if not found or not session_id:
        return []
    touched: list[str] = []

    def fn(cl):
        for k, rec in cl.items():
            if isinstance(rec, dict) and rec.get("session_id") == session_id:
                rec["heartbeat_at"] = iso(now)
                touched.append(k)
    update_claims(root, fn)
    return touched


def _gate_root(cwd) -> Path:
    """The workspace a push is judged in. The pushing cwd's git toplevel wins over CLAUDE_PROJECT_DIR when the two differ AND
    it carries `.claude/foundry-project.json` (a worktree or a sibling workspace checkout has its own ticket and claims). A
    toplevel with no project file (a product clone pushed from inside the workspace) is NOT the answer: the push is still
    gated by the workspace's ticket, so CLAUDE_PROJECT_DIR wins. The toplevel is the fallback only when there is no
    CLAUDE_PROJECT_DIR (gv-16)."""
    top = None
    if cwd:
        try:
            r = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"], capture_output=True,
                               text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                top = Path(r.stdout.strip()).resolve()
        except Exception:
            pass
    if top is not None and (top / ".claude" / "foundry-project.json").is_file():
        return top
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and Path(env).is_dir():
        return Path(env).resolve()
    return top if top is not None else project_root()


def _team_scope(root: Path):
    """True = GitHub-backed (`github_scope` configured); False = no project file / no scope key (claims not in
    use); None = the project file or its `github_scope` is unreadable or malformed (the caller fails CLOSED)."""
    f = root / ".claude" / "foundry-project.json"
    if not f.exists():
        return False
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    if "github_scope" not in d:
        return False
    gs = d["github_scope"]
    if not isinstance(gs, dict):
        return None
    return bool(gs.get("owner")) and bool(gs.get("planning_repo")) or None


def current_session_id(root: Path | None = None) -> str:
    """The caller's session id as the claim CLI derives it, WITHOUT creating anything: CLAUDE_CODE_SESSION_ID,
    else the per-user id kept in the git dir; "" when neither exists."""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if sid:
        return sid
    p = _git_path("foundry-lead-session", root)
    try:
        return p.read_text(encoding="utf-8").strip() if p else ""
    except OSError:
        return ""


def claim_gate(cwd=None, now: float | None = None, explicit: bool = False,
               session_id: str | None = None) -> tuple[bool, str]:
    """Clause (m) of hooks/foundry-git-discipline.sh: may a work-branch push / non-draft `gh pr create` proceed?
    -> (True, why) to admit, (False, reason) to refuse; a refusal reason always starts `claim-required`.

    APPLICABLE only when ALL hold: `claims.enabled` (policy, default true); the workspace is GitHub-backed
    (`github_scope` configured, i.e. the owned-queue/claim model is in use - a single-operator workspace has no
    claims and is never gated); and the active ticket (git dir) is bound to an issue. Then the claims store must
    hold a LIVE lease for that issue. FAIL CLOSED: an unreadable claims store or ticket record refuses."""
    now = time.time() if now is None else now
    root = _gate_root(cwd)
    if explicit and cwd:            # `git -C <dir> push`: the repository the push is aimed at, when it holds a project file
        try:
            top = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"], capture_output=True,
                                 text=True, timeout=10)
            if top.returncode == 0 and top.stdout.strip() and _team_scope(Path(top.stdout.strip()).resolve()) is not False:
                root = Path(top.stdout.strip()).resolve()
        except Exception:
            pass
    enabled, expires = claims_policy(root)
    if not enabled:
        return True, "claims disabled by policy"
    scope = _team_scope(root)
    if scope is None:
        return False, ("claim-required: claim-store-unreadable - the project file .claude/foundry-project.json or its "
                       "github_scope is unreadable or malformed, so whether claims are in use cannot be told; failing closed")
    if not scope:
        return True, "no github_scope: claims are not in use"
    tf = ticket_file(root)
    if tf is None or not tf.is_file():
        return True, "no ticket is bound to an issue"
    try:
        doc = json.loads(tf.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            raise ValueError("not an object")
    except (OSError, ValueError) as e:
        return False, f"claim-required: the active ticket record is unreadable ({redact(str(e))}); failing closed"
    number, repo = doc.get("issue"), str(doc.get("repo") or "")
    if not isinstance(number, int) or isinstance(number, bool):
        return True, "no ticket is bound to an issue"
    try:
        claims, _found = read_claims(root)
    except ClaimsUnreadable as e:
        return False, (f"claim-required: the claims store is unreadable ({redact(str(e))}); failing closed. Repair or "
                       "remove the claims file in the git common dir, then claim the issue again")
    key = claim_key(repo, number)
    rec = claims.get(key)
    if rec is None and not repo:
        rec = next((r for k, r in claims.items() if k.endswith(f"#{number}")), None)
    label = key if repo else f"#{number}"
    if rec is None:
        return False, (f"claim-required: no claim for {label}. Claim it first: `foundry-ticket.py claim {number}"
                       f"{' --repo ' + repo if repo else ''}`")
    me = session_id if session_id is not None else current_session_id(root)
    tof = rec.get("taken_over_from") if isinstance(rec, dict) else None
    if me and isinstance(tof, dict) and tof.get("session_id") == me and rec.get("session_id") != me:
        return False, (f"claim-required: this session's lease on {label} was taken over by session "
                       f"{rec.get('session_id')} at {tof.get('at')}; claim it again only once that lease expires")
    holder = rec.get("session_id") if isinstance(rec, dict) else None
    if holder and holder != me:
        # gv-16: the gate asks "does THIS session hold the live lease", not "does any lease exist"
        return False, (f"claim-required: the lease on {label} is held by session {holder}, not by this session"
                       f"{' (' + me + ')' if me else ' (this session has no id yet)'}. Another session's claim is not yours to push "
                       f"under; claim the issue from this session (`foundry-ticket.py claim {number}"
                       f"{' --repo ' + repo if repo else ''}`) once that lease expires")
    st = lease_state(rec, now, expires)
    if st["state"] != "live":
        return False, (f"claim-required: the lease on {label} expired (no heartbeat for "
                       f"{'longer than the record' if st['age'] == float('inf') else str(int(st['age'])) + 's'}; it lasts "
                       f"{st['expires_after']}s). Renew it: `foundry-ticket.py reconcile` or `foundry-ticket.py lead renew`, "
                       "or claim again if it was taken over")
    return True, f"live claim on {label}"
