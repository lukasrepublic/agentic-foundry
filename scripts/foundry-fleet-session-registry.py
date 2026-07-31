#!/usr/bin/env python3
"""foundry-fleet-session-registry — attach foundry work-context to the NATIVE session list.

Implements feat-foundry-fleet-session-registry (v3.2). The registry is a THIN overlay over the
native Claude Code session surfaces — it does NOT rebuild enumeration/liveness (the harness owns
those). Native sources (exact per-surface field map, AC-SREG-1):

  * `claude agents --json`         — the enumeration SPINE, consumed verbatim:
                                     {sessionId, cwd, name, status, pid, startedAt}
  * `~/.claude/sessions/<pid>.json`— OPTIONAL, keyed by CONTENT sessionId → adds {updated_at}
  * Agent SDK (list/get_session)   — OPTIONAL, the ONLY source of {branch, native_summary}

Foundry adds ONLY the work-context overlay (epic/atom/governance + writer-bound self-reported
enrichment). Read-only against sessions/repos/governance; the sole write is the writer's own
`.foundry/session-registry/<session_id>.json` enrichment file.

CLI: list | read <id> | enrich <k=v...> | --selftest   (AC-SREG-1..5; selftest is hermetic).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# best-effort secret scrub (AC-SREG-4 scrub-at-rest); not a guarantee (bounded residual).
_SECRET_RE = re.compile(
    r"(?i)(?:sk-[a-z0-9]{16,}|ghp_[a-z0-9]{20,}|aws_secret[^\s]*|[A-Za-z0-9+/]{40,}={0,2}"
    r"|(?:password|secret|token|api[_-]?key)\s*[:=]\s*\S+)"
)
# control chars / ANSI / newlines — neutralized on every rendered/emitted field (AC-SREG-5).
_CTRL_RE = re.compile(r"(\x1b\[[0-9;]*[A-Za-z]|[\x00-\x1f\x7f])")

REQUIRED = ("session_id", "state", "epic_kind")
EPIC_KINDS = ("release", "feature", "ad_hoc")
GOV_DOMAIN = ("draft", "authorized", "merged", "superseded")
ENRICH_FIELDS = ("epic_ref", "task", "why", "pending_decision", "blocker", "risk")


# ----------------------------------------------------------------------------- helpers
def _sanitize(v):
    return _CTRL_RE.sub("", v) if isinstance(v, str) else v


def _scrub(v):
    return _SECRET_RE.sub("«redacted»", v) if isinstance(v, str) else v


def _clean(v):
    """AC-SREG-5 render floor: secret-scrub THEN neutralize control/ANSI/newline — applied to
    EVERY session-content-derived display field so the registry's own `list`/`read` CLI output
    is scrubbed+sanitized too (not only the roster's). Scrub-before-sanitize, matching the roster."""
    return _sanitize(_scrub(v))


def _home_sessions_dir():
    return os.path.join(os.environ.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude")), "sessions")


def _registry_dir(root):
    return os.path.join(root, ".foundry", "session-registry")


# ----------------------------------------------------------------------------- native reads
def native_spine(agents_json=None):
    """AC-SREG-2: the spine = `claude agents --json`, consumed VERBATIM.

    Returns (status, list-or-None). status: 'ok' | 'native_unavailable'.
    `agents_json` injects a fixture (hermetic selftest); else the live CLI is run.
    """
    if agents_json is None:
        try:
            r = subprocess.run(["claude", "agents", "--json"], capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                return "native_unavailable", None
            agents_json = r.stdout
        except (OSError, subprocess.SubprocessError):
            return "native_unavailable", None
    try:
        data = json.loads(agents_json)
    except (ValueError, TypeError):
        return "native_unavailable", None
    if not isinstance(data, list):
        return "native_unavailable", None
    return "ok", data


def ondisk_clocks(sessions_dir=None):
    """OPTIONAL on-disk enrichment: ~/.claude/sessions/<pid>.json keyed by CONTENT sessionId.
    Returns {sessionId: updated_at}. Absent dir => {} (degraded for updated_at)."""
    sessions_dir = sessions_dir or _home_sessions_dir()
    out = {}
    if not os.path.isdir(sessions_dir):
        return out
    for fn in os.listdir(sessions_dir):
        if not fn.endswith(".json"):
            continue
        try:
            d = json.load(open(os.path.join(sessions_dir, fn)))
        except (ValueError, OSError):
            continue
        sid = d.get("sessionId")
        if sid:  # keyed by CONTENT sessionId, not the filename PID
            out[sid] = d.get("updatedAt")
    return out


def sdk_fields(sdk_data=None):
    """OPTIONAL SDK enrichment: the ONLY source of {branch, native_summary}. Absent => {} (degraded).
    v1 does not invoke the SDK live (it is a separate Python/TS dep); `sdk_data` injects a fixture."""
    return sdk_data or {}


# ----------------------------------------------------------------------------- overlay
def _active_release_atom(releases_dir):
    """AC-SREG-3 (b): the unambiguous case = exactly one active release w/ exactly one in-progress atom."""
    if not os.path.isdir(releases_dir):
        return None, None
    active = []
    for rid in os.listdir(releases_dir):
        p = os.path.join(releases_dir, rid, "release.yaml")
        if not os.path.isfile(p):
            continue
        txt = open(p, encoding="utf-8", errors="replace").read()
        m = re.search(r"^state:\s*(\S+)", txt, re.M)
        if m and m.group(1).strip() == "active":
            atoms = re.findall(r"^\s*-\s*id:\s*(\S+)", txt, re.M)
            active.append((rid, atoms))
    if len(active) == 1 and len(active[0][1]) == 1:
        return active[0][0], active[0][1][0]
    if len(active) == 1:
        return active[0][0], None  # release known, atom ambiguous -> left for enrichment
    return None, None


def governance_state(atom_or_spec, corpus_root):
    """AC-SREG-3: looked up in the AUTHORITATIVE corpus by the atom key — NEVER the session's repo.
    Reads the spec's sibling acceptance-contract.yaml authorized-trailer. Domain GOV_DOMAIN | None."""
    if not atom_or_spec:
        return None
    spec_path = atom_or_spec if os.path.isabs(atom_or_spec) else os.path.join(corpus_root, atom_or_spec)
    contract = os.path.join(os.path.dirname(spec_path), "acceptance-contract.yaml")
    if not os.path.isfile(contract):
        return None
    txt = open(contract, encoding="utf-8", errors="replace").read()
    if re.search(r"^\s*auth_seq:\s*[1-9]", txt, re.M):
        return "authorized"  # merged/superseded refinement is a corpus-state follow-on
    return "draft"


def read_enrichment(session_id, registry_dir):
    """AC-SREG-4 read side: ignore a file whose content session_id != its filename."""
    p = os.path.join(registry_dir, session_id + ".json")
    if not os.path.isfile(p):
        return {}
    try:
        d = json.load(open(p))
    except (ValueError, OSError):
        return {}
    if d.get("session_id") != session_id:  # tamper / mis-named file -> ignored
        return {}
    return {k: d[k] for k in ENRICH_FIELDS if k in d}


def build_record(spine_entry, clocks, sdk, releases_dir, corpus_root, registry_dir):
    """AC-SREG-1: assemble native base (verbatim spine ⊕ additive optional) ⊕ foundry overlay."""
    sid = spine_entry.get("sessionId")
    enr = read_enrichment(sid, registry_dir) if sid else {}

    # --- foundry overlay: atom key is self-reported OR single-active-release; NEVER the repo ---
    epic_ref = enr.get("epic_ref")
    atom_or_spec = None
    epic_kind = "ad_hoc"
    if epic_ref and epic_ref.startswith("feature:"):
        atom_or_spec = epic_ref.split(":", 1)[1]
        epic_kind = "feature"
    elif epic_ref and epic_ref.startswith("release:"):
        epic_kind = "release"
    else:
        rid, atom = _active_release_atom(releases_dir)
        if rid:
            epic_kind, epic_ref = "release", "release:" + rid
            atom_or_spec = atom

    rec = {
        # native base — spine, VERBATIM (AC-SREG-2)
        "session_id": sid,
        "repo": _clean(spine_entry.get("cwd")),  # DISPLAY ONLY (AC-SREG-3) — never a governance key
        "name": _clean(spine_entry.get("name")),          # session-influenced -> scrub+sanitize
        "state": spine_entry.get("status"),
        "pid": spine_entry.get("pid"),
        "started_at": spine_entry.get("startedAt"),
        # native base — OPTIONAL additive (null when the source surface is absent)
        "updated_at": clocks.get(sid),
        "branch": _clean((sdk.get(sid) or {}).get("branch")),  # SDK/session-influenced -> scrub+sanitize
        "native_summary": _clean((sdk.get(sid) or {}).get("native_summary")),  # UNTRUSTED -> scrub+sanitize
        # foundry overlay
        "epic_kind": epic_kind,
        "epic_ref": _clean(epic_ref),
        "atom_or_spec": atom_or_spec,
        "governance_state": governance_state(atom_or_spec, corpus_root),
        "task": _clean(enr.get("task")),
        "why": _clean(enr.get("why")),
        "pending_decision": _clean(enr.get("pending_decision")),
        "blocker": _clean(enr.get("blocker")),
        "risk": _clean(enr.get("risk")),
    }
    return rec


def validate_record(rec):
    """AC-SREG-1: reject malformed (missing required / bad enum)."""
    for k in REQUIRED:
        if not rec.get(k):
            return False
    if rec["epic_kind"] not in EPIC_KINDS:
        return False
    if rec.get("governance_state") not in (None,) + GOV_DOMAIN:
        return False
    return True


# ----------------------------------------------------------------------------- read/list (AC-SREG-5)
def list_sessions(root, agents_json=None, sessions_dir=None, sdk_data=None, releases_dir=None):
    """Discriminated outcome — never a bare empty list."""
    corpus_root = root
    releases_dir = releases_dir if releases_dir is not None else os.path.join(root, ".foundry", "releases")
    registry_dir = _registry_dir(root)
    status, spine = native_spine(agents_json)
    if status == "native_unavailable":
        return {"status": "native_unavailable", "reason": "could not read `claude agents --json`", "sessions": []}
    clocks = ondisk_clocks(sessions_dir)
    sdk = sdk_fields(sdk_data)
    recs = []
    for e in spine:
        r = build_record(e, clocks, sdk, releases_dir, corpus_root, registry_dir)
        if validate_record(r):
            recs.append(r)
    recs.sort(key=lambda r: ((r.get("epic_ref") or "~"), r["session_id"]))  # deterministic
    degraded = any(r["branch"] is None and r["native_summary"] is None for r in recs) or (sdk == {})
    out = {"status": "degraded" if degraded else "ok", "sessions": recs}
    if degraded:
        out["reason"] = "SDK/on-disk enrichment absent: branch/native_summary/updated_at may be null"
    return out


def read_session(root, session_id, **kw):
    res = list_sessions(root, **kw)
    if res["status"] == "native_unavailable":
        return res
    for r in res["sessions"]:
        if r["session_id"] == session_id:
            return {"status": "ok", "session": r}
    return {"status": "ok", "session": None}


# ----------------------------------------------------------------------------- enrich (AC-SREG-4)
def enrich(root, fields, env_sid=None):
    """Writer-bound, scrubbed-at-rest. filename == content session_id == $CLAUDE_CODE_SESSION_ID,
    all matching the UUID grammar; absent env => reject-closed."""
    sid = env_sid if env_sid is not None else os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not sid or not UUID_RE.match(sid):
        return {"ok": False, "reason": "no/invalid $CLAUDE_CODE_SESSION_ID (reject-closed)"}
    rdir = _registry_dir(root)
    # MERGE-semantics: overlay this call's fields onto the prior writer-bound record so a partial
    # update never silently clobbers fields it doesn't carry. The OSP status block supplies the full
    # overlay key-set every turn (with explicit None for cleared fields), so OSP-driven clears still
    # take effect; a non-OSP field (e.g. `why`) set by a separate call survives a later status block.
    rec = {"session_id": sid}
    rec.update(read_enrichment(sid, rdir))  # writer-bound; {} when absent/mis-named (already scrubbed at rest)
    for k, v in fields.items():
        if k in ENRICH_FIELDS:
            rec[k] = _scrub(v)  # scrub at WRITE time (not only render)
    os.makedirs(rdir, exist_ok=True)
    dst = os.path.join(rdir, sid + ".json")
    fd, tmp = tempfile.mkstemp(dir=rdir)
    with os.fdopen(fd, "w") as f:
        json.dump(rec, f)
    os.rename(tmp, dst)  # atomic
    return {"ok": True, "path": dst}


# ----------------------------------------------------------------------------- selftest (hermetic)
def _selftest():
    results = []

    def check(token, ok):
        results.append((token, ok))
        print(f"  {token}: {'PASS' if ok else 'FAIL'}")

    with tempfile.TemporaryDirectory() as root:
        # fixtures ----------------------------------------------------------------
        sid_a = "0927afe0-4ed1-4401-abb6-85fa7c380564"
        sid_b = "12340000-0000-4000-8000-000000000000"
        spine = json.dumps([
            {"sessionId": sid_a, "cwd": "/w/agentic-workspace", "name": "op-load", "status": "busy",
             "pid": 1, "startedAt": 1},
            {"sessionId": sid_b, "cwd": "/w/other", "name": "x\x1b[31mEVIL\x1b[0m\n", "status": "idle",
             "pid": 2, "startedAt": 2},
        ])
        sdir = os.path.join(root, "sessions"); os.makedirs(sdir)
        json.dump({"sessionId": sid_a, "updatedAt": 111}, open(os.path.join(sdir, "1.json"), "w"))
        rel = os.path.join(root, ".foundry", "releases", "r1"); os.makedirs(rel)
        open(os.path.join(rel, "release.yaml"), "w").write("id: r1\nstate: active\natoms:\n  - id: only-atom\n")

        # AC-SREG-4 first (writes the enrichment overlay used below) ---------------
        ok_match = enrich(root, {"task": "do X", "why": "matters", "pending_decision": "merge?"}, env_sid=sid_a)["ok"]
        rej_mismatch = not enrich(root, {"task": "y"}, env_sid="not-a-uuid")["ok"]
        enrich(root, {"why": "token: sk-abcdef0123456789abcdef"}, env_sid=sid_a)
        after = json.load(open(os.path.join(_registry_dir(root), sid_a + ".json")))
        scrubbed = "sk-abcdef" not in after["why"]
        # MERGE-semantics: the why-only update must PRESERVE the prior task/pending_decision (not clobber)
        merge_preserved = after.get("task") == "do X" and after.get("pending_decision") == "merge?"
        # a mis-named file (victim id, attacker content) is ignored on read
        open(os.path.join(_registry_dir(root), sid_b + ".json"), "w").write(json.dumps({"session_id": "WRONG", "task": "poison"}))
        ignored = read_enrichment(sid_b, _registry_dir(root)) == {}
        check("AC-SREG-4 enrich-writer-bound-scrubbed-at-rest",
              ok_match and rej_mismatch and scrubbed and merge_preserved and ignored)

        res = list_sessions(root, agents_json=spine, sessions_dir=sdir)
        recs = {r["session_id"]: r for r in res["sessions"]}
        a = recs[sid_a]

        # AC-SREG-1: exact field map + reject malformed ---------------------------
        field_map = (a["repo"] == "/w/agentic-workspace" and a["state"] == "busy"
                     and a["updated_at"] == 111 and a["branch"] is None and a["native_summary"] is None)
        rejects = not validate_record({"session_id": "x", "epic_kind": "bogus"})
        check("AC-SREG-1 typed-record-exact-field-map", field_map and rejects)

        # AC-SREG-2: spine verbatim; on-disk additive-only; SDK absent -> null ----
        spine_verbatim = a["pid"] == 1 and a["started_at"] == 1 and a["name"] == "op-load"
        additive_only = a["updated_at"] == 111  # added by on-disk, did not override spine
        sdk_absent_null = a["branch"] is None
        check("AC-SREG-2 native-spine-consumed-as-is", spine_verbatim and additive_only and sdk_absent_null)

        # AC-SREG-3: atom from active release; governance from corpus; repo != key -
        atom_from_release = a["epic_kind"] == "release" and a["epic_ref"] == "release:r1"
        gov_none_no_contract = a["governance_state"] is None  # fixture release atom has no contract on disk
        repo_not_key = a["repo"] == "/w/agentic-workspace"  # present as display, not used for governance
        check("AC-SREG-3 overlay-atom-key-governance-lookup", atom_from_release and gov_none_no_contract and repo_not_key)

        # AC-SREG-5: discriminated outcome + sanitized + read-only ----------------
        ok_outcome = res["status"] in ("ok", "degraded") and "sessions" in res
        unavail = list_sessions(root, agents_json="not json")["status"] == "native_unavailable"
        sanitized = "\x1b" not in recs[sid_b]["name"] and "\n" not in recs[sid_b]["name"]
        # secret-scrub + sanitize on the registry's OWN render path — native_summary/repo/branch are
        # session-content-derived UNTRUSTED fields; a secret/control-char in any must not reach output.
        leak_spine = json.dumps([{"sessionId": sid_a, "cwd": "/w/sk-abcdef0123456789abcdef",
                                  "name": "n", "status": "busy", "pid": 1, "startedAt": 1}])
        leak = list_sessions(root, agents_json=leak_spine, sessions_dir=sdir,
                             sdk_data={sid_a: {"branch": "feat/sk-abcdef0123456789abcdef",
                                               "native_summary": "ran token sk-abcdef0123456789abcdef\nL2"}})
        lr = leak["sessions"][0]
        scrub_render = ("sk-abcdef" not in lr["native_summary"] and "sk-abcdef" not in lr["repo"]
                        and "sk-abcdef" not in lr["branch"] and "\n" not in lr["native_summary"])
        check("AC-SREG-5 read-list-differentiated-sanitized", ok_outcome and unavail and sanitized and scrub_render)

    ok = all(v for _, v in results)
    print("FLEET-SESSION-REGISTRY-SELFTEST-" + ("GREEN" if ok else "RED"))
    return ok


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="foundry-fleet-session-registry")
    ap.add_argument("cmd", nargs="?", choices=["list", "read", "enrich"], default=None)
    ap.add_argument("arg", nargs="?", default=None)
    ap.add_argument("kv", nargs="*", help="enrich: field=value ...")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--root", default=os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if _selftest() else 1)
    if a.cmd == "list":
        print(json.dumps(list_sessions(a.root), indent=2))
    elif a.cmd == "read":
        print(json.dumps(read_session(a.root, a.arg), indent=2))
    elif a.cmd == "enrich":
        fields = dict(p.split("=", 1) for p in ([a.arg] if a.arg and "=" in a.arg else []) + a.kv if "=" in p)
        print(json.dumps(enrich(a.root, fields), indent=2))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
