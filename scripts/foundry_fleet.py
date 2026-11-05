#!/usr/bin/env python3
"""foundry_fleet - fleet builders: ref-create claims, leases, worker identity, deck slots and capacity (dl-11).

    foundry_fleet.py identity
    foundry_fleet.py trailers
    foundry_fleet.py marker
    foundry_fleet.py claim      <owner/repo> <programme> <batch>
    foundry_fleet.py heartbeat  <owner/repo> <programme> <batch>
    foundry_fleet.py transition <owner/repo> <programme> <batch> <state> [--pr <url>]
    foundry_fleet.py status     <owner/repo> <programme> <batch>
    foundry_fleet.py arm-deck   <owner/repo>
    foundry_fleet.py capacity   --workers <n[,n...]> --reviewers <n> [--plan-max <n>]
    foundry_fleet.py build-next <owner/repo> <programme> (--plan-file <f> | --plan-ref [--remote origin])
                                --workers <n[,n...]> --reviewers <n>

A CLAIM is the creation of the ref refs/heads/claim/<programme>/<batch>. The ref points at a commit whose message
carries the claim record (worker, state, heartbeat). HTTP 201 = won, HTTP 422 = lost. A heartbeat or a state
change moves the ref with the GraphQL mutation `updateRefs` and a `beforeOid`: when the tip moved, the call
re-reads the ref and never overwrites it. A claim whose last heartbeat is older than the policy value
`claims.expires_after` can be taken over (the same `updateRefs`); a live one cannot.

WORKER ID  <op_id>@<machine12>/<container12>/<session>  from the operators file, /etc/machine-id, the cgroup
container id and CLAUDE_CODE_SESSION_ID. A missing or unreadable part REFUSES with a named reason; there is no
per-clone fallback. The id is carried as commit trailers and a hidden PR-body marker.

TOKEN  One GitHub App installation token per worker session, from a token source. This file creates, registers and
configures no App and no credential. The CLI reads the token from the file named by FOUNDRY_FLEET_TOKEN_FILE (mode
0600) and refuses with `external-provisioning` when it is absent. The token travels only in the environment of the
`gh` child process, never in an argument, and every message is redacted.

BUILDER MODE (`build-next`) reads the plan, claims ONE batch of the open wave inside the fleet capacity and prints
it. It never plans and never edits the plan.

Exit: 0 ok, 2 refused or lost (one `foundry_fleet: <reason>: ...` line on stderr).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import foundry_message_log as fml  # noqa: E402  (redact, valid_remote)
import foundry_ticket_store as store  # noqa: E402  (lease policy, lease_state, iso, epoch)

REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
OP_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
HANDLE_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
HEX12_RE = re.compile(r"^[0-9a-f]{12}$")
OID_RE = re.compile(r"^[0-9a-f]{40}$")
CONTAINER_RE = re.compile(r"(?<![0-9a-f])([0-9a-f]{64})(?![0-9a-f])")
STATES = ("claimed", "building", "review_ready", "stopped")
DECK_SLOTS = 2
DECK_PROGRAMME = "decks"
CLAIM_PREFIX = "refs/heads/claim/"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbd4904"
TOKEN_RE = re.compile(r"(gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,}|[Bb]earer\s+[A-Za-z0-9._~+/=-]{16,})")


class FleetError(Exception):
    """A refusal with a named reason: str(e) is `<reason>: <detail>`."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


def redact(text, token: str | None = None) -> str:
    out = TOKEN_RE.sub("[REDACTED]", fml.redact(str(text)))
    if token:
        out = out.replace(token, "[REDACTED]")
    return out


# ---------------------------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------------------------
def check_repo(repo) -> str:
    if not isinstance(repo, str) or not REPO_RE.match(repo) or ".." in repo:
        raise FleetError("bad-repo", f"{repo!r} is not <owner>/<name>")
    return repo


def check_segment(value, what: str) -> str:
    if (not isinstance(value, str) or not SEGMENT_RE.match(value) or ".." in value
            or value.endswith((".lock", "."))):
        raise FleetError(f"bad-{what}", f"{value!r} is not a plain ref-safe segment")
    return value


def claim_ref(programme: str, batch: str) -> str:
    """refs/heads/claim/<programme>/<batch>, both segments validated; nothing else is ever built."""
    return f"{CLAIM_PREFIX}{check_segment(programme, 'programme')}/{check_segment(batch, 'batch')}"


# ---------------------------------------------------------------------------------------------
# worker identity
# ---------------------------------------------------------------------------------------------
def _read(path, reason: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise FleetError(reason, f"cannot read {path}: {e.__class__.__name__}")


def worker_identity(env=None, operators_file=None, machine_id_file="/etc/machine-id",
                    cgroup_file="/proc/self/cgroup") -> dict:
    """{"id", "op_id", "machine12", "container12", "session", "handle"}. Refuses with a named reason on any missing
    or unreadable part; never defaults."""
    env = os.environ if env is None else env
    session = env.get("CLAUDE_CODE_SESSION_ID", "")
    if not session:
        raise FleetError("session-missing", "CLAUDE_CODE_SESSION_ID is unset; there is no per-clone fallback")
    if not SESSION_RE.match(session) or "--" in session:
        raise FleetError("session-invalid", "CLAUDE_CODE_SESSION_ID is not a plain session id")
    op = env.get("FOUNDRY_OPERATOR", "")
    if not op:
        raise FleetError("operator-missing", "FOUNDRY_OPERATOR is unset")
    if not OP_RE.match(op):
        raise FleetError("operator-invalid", "FOUNDRY_OPERATOR is not a plain operator id")
    if operators_file is None:
        root = env.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        operators_file = os.path.join(root, ".claude", "foundry-operators.json")
    try:
        ops = json.loads(_read(operators_file, "operators-unreadable"))
        entry = ops["operators"][op]
    except (ValueError, KeyError, TypeError):
        raise FleetError("operator-unregistered", f"{op!r} is not in the operators file")
    handle = entry.get("github") if isinstance(entry, dict) else None
    if not isinstance(handle, str) or not HANDLE_RE.match(handle):
        raise FleetError("operator-handle-missing", f"operator {op!r} has no valid github handle in the operators file")
    mid = _read(machine_id_file, "machine-id-unreadable").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{32}", mid):
        raise FleetError("machine-id-invalid", f"{machine_id_file} does not hold a 32-hex machine id")
    container = None
    for line in _read(cgroup_file, "cgroup-unreadable").splitlines():
        m = CONTAINER_RE.search(line)
        if m:
            container = m.group(1)
            break
    if container is None:
        explicit = env.get("FOUNDRY_CONTAINER_ID", "").strip().lower()
        if explicit:
            if not re.fullmatch(r"[0-9a-f]{12,64}", explicit):
                raise FleetError("container-id-invalid", "FOUNDRY_CONTAINER_ID is not 12 to 64 hex characters")
            container = explicit
    if container is None:
        raise FleetError("container-id-missing", "the cgroup shows no container id and FOUNDRY_CONTAINER_ID is unset")
    m12, c12 = mid[:12], container[:12]
    return {"id": f"{op}@{m12}/{c12}/{session}", "op_id": op, "machine12": m12, "container12": c12,
            "session": session, "handle": handle}


def trailers(worker: dict) -> list:
    """Commit trailers as separate lines (no shell, one value each). Feed them to `git commit` with one `--trailer`
    argument each, or `git interpret-trailers`."""
    return [f"Co-authored-by: {worker['op_id']} (foundry worker) <{worker['handle']}@users.noreply.github.com>",
            f"Session-Id: {worker['session']}",
            f"Worker-Host: {worker['machine12']}/{worker['container12']}"]


def pr_marker(worker: dict) -> str:
    """The hidden PR-body marker. The parts are validated, so the comment cannot be closed early."""
    return f"<!-- foundry-worker: {worker['id']} -->"


# ---------------------------------------------------------------------------------------------
# the GitHub App installation token, once per worker session
# ---------------------------------------------------------------------------------------------
class SessionTokens:
    """Caches one installation token for each worker session. `mint()` returns (token, expires_at_epoch)."""

    def __init__(self, mint, clock=time.time, margin: int = 60):
        self._mint, self._clock, self._margin, self._cache = mint, clock, margin, {}

    def token_for(self, session: str) -> str:
        hit = self._cache.get(session)
        if hit and hit[1] - self._clock() > self._margin:
            return hit[0]
        token, expires = self._mint()
        if not isinstance(token, str) or not token or not isinstance(expires, (int, float)):
            raise FleetError("token-invalid", "the token source returned no usable token")
        self._cache[session] = (token, float(expires))
        return token


def file_token_mint(path_env: str = "FOUNDRY_FLEET_TOKEN_FILE", env=None, clock=time.time):
    """A mint that reads the token the operator provisioned into a 0600 file. Absent => external-provisioning."""
    env = os.environ if env is None else env

    def mint():
        path = env.get(path_env, "")
        if not path:
            raise FleetError("external-provisioning", "no GitHub App installation token is provisioned "
                             f"({path_env} is unset); the operator holds the App identity")
        try:
            st = os.stat(path)
            if st.st_mode & 0o077:
                raise FleetError("token-file-permissions", f"{path} must not be readable by group or others (0600)")
            token = Path(path).read_text(encoding="utf-8").strip()
        except OSError as e:
            raise FleetError("token-unreadable", f"cannot read the token file: {e.__class__.__name__}")
        return token, clock() + 3000
    return mint


class GhTransport:
    """The ONE network class. `gh api -i` with the token in the child environment only."""

    def __init__(self, tokens: SessionTokens, session: str, runner=subprocess.run):
        self._tokens, self._session, self._run = tokens, session, runner

    def _call(self, args: list, body) -> tuple:
        token = self._tokens.token_for(self._session)
        env = dict(os.environ)
        env["GH_TOKEN"] = token
        env.pop("GITHUB_TOKEN", None)
        try:
            r = self._run(["gh", "api", "-i", *args], input=json.dumps(body) if body is not None else None,
                          capture_output=True, text=True, timeout=120, env=env)
        except subprocess.TimeoutExpired:
            raise FleetError("github-timeout", "gh api timed out")
        m = re.match(r"HTTP/\S+\s+(\d{3})", r.stdout or "")
        if not m:
            raise FleetError("github-unreachable", redact(r.stderr.strip()[:200], token))
        text = r.stdout.split("\r\n\r\n", 1)[-1] if "\r\n\r\n" in r.stdout else r.stdout.split("\n\n", 1)[-1]
        try:
            doc = json.loads(text) if text.strip() else {}
        except ValueError:
            doc = {}
        return int(m.group(1)), doc

    def rest(self, method: str, path: str, body=None) -> tuple:
        args = ["-X", method, path] + (["--input", "-"] if body is not None else [])
        return self._call(args, body)

    def graphql(self, query: str, variables: dict) -> tuple:
        return self._call(["graphql", "--input", "-"], {"query": query, "variables": variables})


# ---------------------------------------------------------------------------------------------
# claims on refs
# ---------------------------------------------------------------------------------------------
UPDATE_REFS = ("mutation($in:UpdateRefsInput!){updateRefs(input:$in){clientMutationId}}")


class Fleet:
    """Claim operations over an injectable transport: rest(method, path, body) and graphql(query, vars), each
    returning (http_status, json)."""

    def __init__(self, transport, repo: str, expires_after_s: int = store.DEFAULT_EXPIRES_AFTER, clock=time.time):
        self.t, self.repo, self.expires, self.clock = transport, check_repo(repo), expires_after_s, clock
        self._repo_id = None

    # -- low level
    def _tip(self, ref: str):
        st, doc = self.t.rest("GET", f"repos/{self.repo}/git/ref/{ref[len('refs/'):]}")
        if st == 404:
            return None
        sha = (doc.get("object") or {}).get("sha") if st == 200 and isinstance(doc, dict) else None
        if not isinstance(sha, str) or not OID_RE.match(sha):
            raise FleetError("claim-unreadable", f"ref read answered HTTP {st}")
        return sha

    def _record(self, sha: str) -> dict:
        st, doc = self.t.rest("GET", f"repos/{self.repo}/git/commits/{sha}")
        msg = doc.get("message") if st == 200 and isinstance(doc, dict) else None
        try:
            rec = json.loads(msg.split("\n\n", 1)[1])
            if not isinstance(rec, dict) or not isinstance(rec.get("worker"), str):
                raise ValueError
        except (AttributeError, IndexError, ValueError):
            raise FleetError("claim-unreadable", f"commit {sha[:12]} does not carry a claim record")
        return rec

    def _commit(self, rec: dict, parent: str | None) -> str:
        st, tree = self.t.rest("POST", f"repos/{self.repo}/git/trees", {"tree": []})
        tsha = tree.get("sha") if st in (200, 201) and isinstance(tree, dict) else None
        if not isinstance(tsha, str):
            raise FleetError("github-error", f"tree create answered HTTP {st}")
        body = {"message": "foundry-claim\n\n" + json.dumps(rec, sort_keys=True), "tree": tsha,
                "parents": [parent] if parent else []}
        st, c = self.t.rest("POST", f"repos/{self.repo}/git/commits", body)
        sha = c.get("sha") if st in (200, 201) and isinstance(c, dict) else None
        if not isinstance(sha, str) or not OID_RE.match(sha):
            raise FleetError("github-error", f"commit create answered HTTP {st}")
        return sha

    def _repository_id(self) -> str:
        if self._repo_id is None:
            st, doc = self.t.rest("GET", f"repos/{self.repo}")
            nid = doc.get("node_id") if st == 200 and isinstance(doc, dict) else None
            if not isinstance(nid, str) or not nid:
                raise FleetError("github-error", f"repository read answered HTTP {st}")
            self._repo_id = nid
        return self._repo_id

    def _advance(self, ref: str, before: str, rec: dict) -> bool:
        """Move ref from `before` to a new commit by updateRefs with beforeOid. True when moved."""
        after = self._commit(rec, before)
        st, doc = self.t.graphql(UPDATE_REFS, {"in": {"repositoryId": self._repository_id(), "refUpdates": [
            {"name": ref, "beforeOid": before, "afterOid": after}]}})
        return st == 200 and isinstance(doc, dict) and not doc.get("errors")

    def _live(self, rec: dict, now: float) -> bool:
        return store.lease_state(rec, now, self.expires)["state"] == "live"

    def _new_record(self, worker: dict, programme: str, batch: str, now: float, kind: str = "batch") -> dict:
        title = f"deck: {worker['id']}" if kind == "deck" else f"batch: {programme}/{batch}"
        return {"v": 1, "kind": kind, "programme": programme, "batch": batch, "worker": worker["id"],
                "machine12": worker["machine12"], "title": title, "state": "claimed",
                "claimed_at": store.iso(now), "heartbeat_at": store.iso(now), "expires_after_s": self.expires}

    # -- operations
    def claim(self, worker: dict, programme: str, batch: str, kind: str = "batch") -> dict:
        ref, now = claim_ref(programme, batch), self.clock()
        rec = self._new_record(worker, programme, batch, now, kind)
        sha = self._commit(rec, None)
        st, doc = self.t.rest("POST", f"repos/{self.repo}/git/refs", {"ref": ref, "sha": sha})
        if st == 201:
            return {"result": "won", "ref": ref, "tip": sha, "worker": worker["id"]}
        if st != 422:
            raise FleetError("github-error", f"ref create answered HTTP {st}")
        tip = self._tip(ref)                         # 422: somebody holds it
        if tip is None:
            raise FleetError("claim-unreadable", "ref create answered 422 but the ref is gone")
        cur = self._record(tip)
        if cur["worker"] == worker["id"]:
            return {"result": "held", "ref": ref, "tip": tip, "worker": worker["id"]}
        if self._live(cur, now):
            return {"result": "lost", "ref": ref, "tip": tip, "holder": cur["worker"]}
        rec["taken_over_from"] = {"worker": cur["worker"], "heartbeat_at": cur.get("heartbeat_at")}
        if self._advance(ref, tip, rec):
            return {"result": "taken_over", "ref": ref, "tip": self._tip(ref), "worker": worker["id"],
                    "from": cur["worker"]}
        new_tip = self._tip(ref)
        return {"result": "lost", "ref": ref, "tip": new_tip,
                "holder": self._record(new_tip)["worker"] if new_tip else None}

    def _update(self, worker: dict, programme: str, batch: str, change: dict) -> dict:
        ref, now = claim_ref(programme, batch), self.clock()
        tip = self._tip(ref)
        if tip is None:
            return {"result": "missing", "ref": ref}
        cur = self._record(tip)
        if cur["worker"] != worker["id"]:
            return {"result": "not_holder", "ref": ref, "tip": tip, "holder": cur["worker"]}
        rec = {**cur, **change, "heartbeat_at": store.iso(now)}
        if self._advance(ref, tip, rec):
            return {"result": "ok", "ref": ref, "tip": self._tip(ref), "state": rec["state"]}
        new_tip = self._tip(ref)                    # beforeOid did not match: re-read, never overwrite
        holder = self._record(new_tip)["worker"] if new_tip else None
        return {"result": "conflict", "ref": ref, "tip": new_tip, "holder": holder}

    def heartbeat(self, worker: dict, programme: str, batch: str) -> dict:
        return self._update(worker, programme, batch, {})

    def transition(self, worker: dict, programme: str, batch: str, state: str, pr: str | None = None) -> dict:
        if state not in STATES:
            raise FleetError("bad-state", f"{state!r} is not one of {', '.join(STATES)}")
        change = {"state": state}
        if pr is not None:
            if not re.fullmatch(r"https://github\.com/[A-Za-z0-9._/-]{1,200}/pull/[0-9]{1,9}", pr):
                raise FleetError("bad-pr", "the PR must be a https://github.com/<owner>/<repo>/pull/<n> URL")
            change["pr"] = pr
        return self._update(worker, programme, batch, change)

    def status(self, programme: str, batch: str) -> dict:
        ref, now = claim_ref(programme, batch), self.clock()
        tip = self._tip(ref)
        if tip is None:
            return {"result": "unclaimed", "ref": ref}
        rec = self._record(tip)
        st = store.lease_state(rec, now, self.expires)
        return {"result": "claimed", "ref": ref, "tip": tip, "holder": rec["worker"], "state": rec.get("state"),
                "lease": st["state"]}

    def claims_under(self, programme: str) -> list:
        """[(batch, record)] for every claim ref of the programme (matching-refs, never a search)."""
        prefix = f"heads/claim/{check_segment(programme, 'programme')}/"
        st, doc = self.t.rest("GET", f"repos/{self.repo}/git/matching-refs/{prefix}")
        if st != 200 or not isinstance(doc, list):
            raise FleetError("claim-unreadable", f"matching-refs answered HTTP {st}")
        out = []
        for item in doc:
            name = item.get("ref", "") if isinstance(item, dict) else ""
            sha = (item.get("object") or {}).get("sha") if isinstance(item, dict) else None
            if name.startswith(CLAIM_PREFIX + programme + "/") and isinstance(sha, str):
                out.append((name.rsplit("/", 1)[-1], self._record(sha)))
        return out

    def arm_deck(self, worker: dict) -> dict:
        """Take one of the two deck slots of this machine, or report `deck-slot-full`."""
        for n in range(1, DECK_SLOTS + 1):
            r = self.claim(worker, DECK_PROGRAMME, f"deck-{worker['machine12']}-{n}", kind="deck")
            if r["result"] in ("won", "held", "taken_over"):
                return {**r, "result": "armed", "slot": n}
        return {"result": "deck-slot-full", "machine12": worker["machine12"], "slots": DECK_SLOTS}


# ---------------------------------------------------------------------------------------------
# capacity and builder mode
# ---------------------------------------------------------------------------------------------
def _count(v, what: str, minimum: int = 0) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v < minimum:
        raise FleetError("bad-capacity", f"{what} must be an integer >= {minimum}")
    return v


def fleet_capacity(workers: list, reviewers: int, plan_max: int | None = None) -> int:
    """min(sum of max_concurrent_per_worker, reviewer capacity), and the plan's max_concurrent when given."""
    cap = min(sum(_count(w, "max_concurrent_per_worker", 1) for w in workers), _count(reviewers, "reviewer capacity"))
    return min(cap, _count(plan_max, "plan max_concurrent")) if plan_max is not None else cap


def open_wave_batches(plan: dict) -> list:
    """The startable batches of the plan's open wave, in plan order: no held atom, wave == open_wave."""
    n, held = plan.get("open_wave"), set(plan.get("held") or [])
    for w in plan.get("waves") or []:
        if w.get("n") == n:
            return [b for b in w.get("batches") or [] if not held & set(b.get("atoms") or [])]
    return []


def build_next(fleet: Fleet, worker: dict, plan: dict, cap: int) -> dict:
    """Claim ONE batch of the open wave, inside `cap`. Reads the plan; never plans or edits it."""
    programme = plan.get("programme")
    cands = open_wave_batches(plan)
    if not cands:
        return {"result": "none", "why": "no startable batch in the open wave"}
    live = {}
    for b, rec in fleet.claims_under(programme):
        if rec.get("state") != "stopped" and fleet._live(rec, fleet.clock()):
            live[b] = rec
    cap_left = _count(cap, "cap") - len(live)
    mine = [b for b in cands if live.get(b["id"], {}).get("worker") == worker["id"]]
    if mine:
        return {"result": "held", "batch": mine[0]["id"], "atoms": mine[0]["atoms"]}
    if cap_left <= 0:
        return {"result": "at-capacity", "live": len(live), "cap": cap}
    for b in cands:
        if b["id"] in live:
            continue
        r = fleet.claim(worker, programme, b["id"])
        if r["result"] in ("won", "taken_over"):
            return {"result": "claimed", "batch": b["id"], "atoms": b["atoms"], "ref": r["ref"]}
    return {"result": "none", "why": "every startable batch is held by a live claim"}


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------
def _plan_from(args) -> dict:
    import yaml
    import foundry_execution_plan as fep
    if args.plan_file:
        root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()
        target = (root / args.plan_file).resolve()
        if target != root and root not in target.parents:
            raise FleetError("bad-plan", f"--plan-file {args.plan_file!r} is outside the project root")
        text = _read(target, "plan-unreadable")
    else:
        if not fml.valid_remote(args.remote):
            raise FleetError("bad-remote", f"{args.remote!r} is not a plain remote name or URL")
        prog = check_segment(args.programme, "programme")
        f = subprocess.run(["git", "fetch", "--no-tags", "--quiet", "--", args.remote, f"refs/heads/plan/{prog}"],
                           capture_output=True, text=True, timeout=120)
        if f.returncode != 0:
            raise FleetError("plan-unreadable", redact(f.stderr.strip()[:200]))
        s = subprocess.run(["git", "show", "FETCH_HEAD:execution-plan.yaml"], capture_output=True, text=True, timeout=60)
        if s.returncode != 0:
            raise FleetError("plan-unreadable", redact(s.stderr.strip()[:200]))
        text = s.stdout
    try:
        plan = yaml.safe_load(text)
        fep.validate(plan)
    except Exception as e:
        raise FleetError("plan-invalid", redact(str(e).splitlines()[0] if str(e) else type(e).__name__))
    if plan.get("programme") != args.programme:
        raise FleetError("plan-invalid", "the plan names another programme")
    return plan


def _ints(text: str) -> list:
    try:
        return [int(x) for x in text.split(",")]
    except ValueError:
        raise FleetError("bad-capacity", "--workers is a comma list of integers")


def _expires(root) -> int:
    return store.claims_policy(root)[1]


def main(argv=None, transport=None, env=None, identity=None) -> int:
    ap = argparse.ArgumentParser(prog="foundry_fleet.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    for v in ("identity", "trailers", "marker"):
        sub.add_parser(v)
    for v in ("claim", "heartbeat", "status", "transition"):
        p = sub.add_parser(v)
        p.add_argument("repo"), p.add_argument("programme"), p.add_argument("batch")
        if v == "transition":
            p.add_argument("state")
            p.add_argument("--pr")
    p = sub.add_parser("arm-deck")
    p.add_argument("repo")
    p = sub.add_parser("capacity")
    p.add_argument("--workers", required=True), p.add_argument("--reviewers", type=int, required=True)
    p.add_argument("--plan-max", type=int)
    p = sub.add_parser("build-next")
    p.add_argument("repo"), p.add_argument("programme")
    p.add_argument("--plan-file"), p.add_argument("--plan-ref", action="store_true"), p.add_argument("--remote", default="origin")
    p.add_argument("--workers", required=True), p.add_argument("--reviewers", type=int, required=True)
    a = ap.parse_args(argv)
    env = os.environ if env is None else env
    try:
        if a.verb == "capacity":
            print(json.dumps({"capacity": fleet_capacity(_ints(a.workers), a.reviewers, a.plan_max)}))
            return 0
        worker = identity or worker_identity(env=env)
        if a.verb == "identity":
            print(worker["id"])
            return 0
        if a.verb == "trailers":
            print("\n".join(trailers(worker)))
            return 0
        if a.verb == "marker":
            print(pr_marker(worker))
            return 0
        root = Path(env.get("CLAUDE_PROJECT_DIR") or os.getcwd())
        if transport is None:
            transport = GhTransport(SessionTokens(file_token_mint(env=env)), worker["session"])
        fleet = Fleet(transport, a.repo, _expires(root))
        if a.verb == "arm-deck":
            out = fleet.arm_deck(worker)
        elif a.verb == "claim":
            out = fleet.claim(worker, a.programme, a.batch)
        elif a.verb == "heartbeat":
            out = fleet.heartbeat(worker, a.programme, a.batch)
        elif a.verb == "transition":
            out = fleet.transition(worker, a.programme, a.batch, a.state, a.pr)
        elif a.verb == "status":
            out = fleet.status(a.programme, a.batch)
        else:
            if bool(a.plan_file) == bool(a.plan_ref):
                raise FleetError("bad-plan", "give exactly one of --plan-file or --plan-ref")
            plan = _plan_from(a)
            cap = fleet_capacity(_ints(a.workers), a.reviewers, plan.get("max_concurrent"))
            out = build_next(fleet, worker, plan, cap)
        print(json.dumps(out, sort_keys=True))
        return 2 if out.get("result") in ("lost", "deck-slot-full", "conflict", "not_holder", "missing") else 0
    except FleetError as e:
        print(f"foundry_fleet: {redact(str(e))}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
