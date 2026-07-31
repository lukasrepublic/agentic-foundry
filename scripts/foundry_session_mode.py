#!/usr/bin/env python3
"""foundry_session_mode — the session POSTURE store/resolver (feat-foundry-session-posture, M1 of the
operating-mode-postures epic).

Implements AC-POS-1/-2/-3/-5/-8: a closed-set {factory, noninteractive, interactive} session-level
operating mode, default `factory`, persisted hook-free at `.foundry/session-mode/<session_id>.json`
under `CLAUDE_PROJECT_DIR`.

MIRRORS the shipped fleet session-registry's exact mechanism (`scripts/foundry-fleet-session-registry.py`
`enrich()` / `_registry_dir()`), at a sibling path — same UUID-grammar sanitize, same atomic temp-file
rename, same reject-closed-when-the-session-id-is-absent discipline (AC-POS-8) — MIRRORED, not shared code,
so this atom's blast radius stays confined to its own allowed_paths (it does not import/edit the registry
module).

  set(mode, session_id=None)      -> {"ok": bool, "mode"|"reason": ...}
      Validates mode against the closed set (fail-closed on unknown -> AC-POS-1, state unchanged).
      Resolves the WRITE-side session id from `session_id` or $CLAUDE_CODE_SESSION_ID (UUID grammar);
      reject-closed with NO fallback/"unknown" key and NO silent no-op when absent/invalid (AC-POS-8).
      Writes atomically (temp-file + os.rename); idempotent (re-issuing the same mode is a state no-op
      that still echoes it — AC-POS-3).

  resolve(session_id=None)        -> mode string, always one of the closed set
      Reads the file for the given/env-resolved session id. Fail-safe to the default `factory` when the
      id is absent/invalid, the file is absent/unreadable/malformed, or the stored value is invalid
      (AC-POS-2 / AC-POS-6 fail-open). No cross-session leak: a different session id is independent.

  No lifecycle hook: the on-disk store is read fresh on every resolution, so persistence survives a
  `/compact` for free (AC-POS-5) — hooks/hooks.json stays untouched (denied_paths).

Extended (feat-foundry-autonomous-fork-policy, AC-AFP-1/-4/-6) with a second, orthogonal closed-set
field on the SAME session-keyed store record: `fork_policy` ∈ {`park`, `two-way-auto`}, default
`park`. Governs whether the autonomous driver charter (`skills/mode-autonomous/SKILL.md`)
auto-answers a reversible ("two-way-door") question fork instead of stopping the loop; a
security-flagged, authorization-adjacent, or irreversible fork always parks regardless of this
value (AC-AFP-3 — instruction-level defense-in-depth; the actual invariant is the operator
ceremony + the UL-0007 classifier, both untouched here). Every auto-answer a driver makes under
`two-way-auto` is appended to a separate append-only log (`record_auto_answer` / `.foundry/
auto-answers.jsonl`) — AC-AFP-4.

  set_fork_policy(policy, session_id=None)   -> {"ok": bool, "fork_policy"|"reason": ...}
      Mirrors set_mode(): closed-set fail-closed, write-side reject-closed on an absent session
      identity, persisted in the SAME record as `mode` (merge-write — never clobbers a sibling
      field), idempotent re-issue.

  resolve_fork_policy(session_id=None)       -> fork policy string, always in FORK_POLICIES
      Mirrors resolve(): fail-safe default `park` on absent/unreadable/malformed/invalid.

  record_auto_answer(session_id, question, options, chosen, reversibility_rationale)
      Appends one JSON line to `.foundry/auto-answers.jsonl` (append-only; never truncates/rewrites
      prior records). Write-side reject-closed on an absent session identity, mirroring the store.

Threat model — TRUSTED OPERATOR; non-security-flagged (spec threat model). Posture is operator-set
display + session state; it authorizes nothing and gates nothing (M1 scope). `fork_policy` is
likewise advisory-only — it changes which forks an autonomous driver auto-answers, never what
authorization/merge machinery accepts (spec threat model, AC-AFP-3 floor statement).

CLI: `set <name> [--session-id <id>]` (echoes the resulting mode) | `resolve [--session-id <id>]` |
`set-fork-policy <value> [--session-id <id>]` (echoes the resulting fork policy) |
`resolve-fork-policy [--session-id <id>]` |
`record-auto-answer --question <q> --chosen <opt> [--options <json-array>] [--rationale <text>]
[--session-id <id>]` | `--selftest` (hermetic, throwaway temp dirs only).
"""
import argparse
import datetime
import json
import os
import re
import sys
import tempfile

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

MODES = ("factory", "noninteractive", "interactive")
DEFAULT_MODE = "factory"

# AC-AFP-1: the fork-policy closed set, a second field on the same session-keyed record.
FORK_POLICIES = ("park", "two-way-auto")
DEFAULT_FORK_POLICY = "park"

AUTO_ANSWER_LOG_REL = os.path.join(".foundry", "auto-answers.jsonl")


# ----------------------------------------------------------------------------- store paths
def _store_dir(root):
    return os.path.join(root, ".foundry", "session-mode")


def _store_path(root, session_id):
    return os.path.join(_store_dir(root), session_id + ".json")


def _resolve_write_session_id(session_id):
    """AC-POS-8: the write-side session id — the explicit arg, or $CLAUDE_CODE_SESSION_ID, matching the
    UUID grammar (the same env the registry's `enrich()` writer binds to). None when absent/invalid —
    the caller MUST reject-closed on None (no fallback/"unknown" key)."""
    sid = session_id if session_id is not None else os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not sid or not UUID_RE.match(sid):
        return None
    return sid


def _sanitize_read_session_id(session_id):
    """READ-side id (e.g. the statusline payload's `.session_id`): restrict to a safe charset (mirrors
    the shipped statusline's `tr -cd 'A-Za-z0-9_-'`), never raises. Empty/None -> None (caller falls back
    to the default, fail-open)."""
    if not session_id:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", session_id)
    return cleaned or None


def _read_raw(root, session_id):
    """Read the raw stored record for an already-resolved (write-side or sanitized read-side)
    session id, or None when absent/unreadable/malformed/tampered (mis-named file). Never raises.
    Shared by both fields (`mode` / `fork_policy`) that live on the SAME session-keyed record."""
    p = _store_path(root, session_id)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("session_id") != session_id:
        return None
    return d


def _atomic_write_record(sdir, dst, rec):
    """Shared atomic temp-file + rename writer for the session-mode record. Returns an error string
    or None on success."""
    fd, tmp = tempfile.mkstemp(dir=sdir)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(rec, f)
        os.rename(tmp, dst)  # atomic
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return f"write failed: {e}"
    return None


# ----------------------------------------------------------------------------- set (write)
def set_mode(root, mode, session_id=None):
    """AC-POS-1 (closed-set fail-closed) + AC-POS-3 (set+persist+idempotent echo) + AC-POS-8
    (write-side reject-closed on an absent session identity). Returns {"ok": bool, ...}.
    Merge-write: a pre-existing `fork_policy` field on the same record is preserved untouched
    (AC-AFP-1 — the two fields are orthogonal, never clobber each other)."""
    if mode not in MODES:
        return {"ok": False, "reason": f"unknown mode {mode!r}; valid modes: {', '.join(MODES)}"}
    sid = _resolve_write_session_id(session_id)
    if sid is None:
        return {"ok": False, "reason": "no/invalid $CLAUDE_CODE_SESSION_ID (reject-closed, no fallback)"}
    sdir = _store_dir(root)
    os.makedirs(sdir, exist_ok=True)
    dst = _store_path(root, sid)
    rec = dict(_read_raw(root, sid) or {})
    rec["session_id"] = sid
    rec["mode"] = mode
    err = _atomic_write_record(sdir, dst, rec)
    if err:
        return {"ok": False, "reason": err}
    return {"ok": True, "mode": mode, "session_id": sid, "path": dst}


# ----------------------------------------------------------------------------- fork policy (AC-AFP-1)
def set_fork_policy(root, policy, session_id=None):
    """AC-AFP-1: closed-set fail-closed store for `fork_policy`, mirroring set_mode()'s fail-closed
    store semantics exactly (closed set, write-side reject-closed on an absent session identity,
    idempotent re-issue). Merge-write: a pre-existing `mode` field is preserved untouched."""
    if policy not in FORK_POLICIES:
        return {"ok": False,
                "reason": f"unknown fork_policy {policy!r}; valid fork policies: {', '.join(FORK_POLICIES)}"}
    sid = _resolve_write_session_id(session_id)
    if sid is None:
        return {"ok": False, "reason": "no/invalid $CLAUDE_CODE_SESSION_ID (reject-closed, no fallback)"}
    sdir = _store_dir(root)
    os.makedirs(sdir, exist_ok=True)
    dst = _store_path(root, sid)
    rec = dict(_read_raw(root, sid) or {})
    rec["session_id"] = sid
    rec["fork_policy"] = policy
    err = _atomic_write_record(sdir, dst, rec)
    if err:
        return {"ok": False, "reason": err}
    return {"ok": True, "fork_policy": policy, "session_id": sid, "path": dst}


def resolve_fork_policy(root, session_id=None):
    """AC-AFP-1: fail-safe default `park` on absent/unreadable/malformed/invalid — mirrors
    resolve()'s exact fail-open control shape for the sibling `mode` field."""
    sid = _sanitize_read_session_id(session_id) if session_id is not None else _resolve_write_session_id(None)
    if sid is None:
        return DEFAULT_FORK_POLICY
    d = _read_raw(root, sid)
    if d is None:
        return DEFAULT_FORK_POLICY
    fp = d.get("fork_policy")
    if fp not in FORK_POLICIES:
        return DEFAULT_FORK_POLICY
    return fp


# ----------------------------------------------------------------------------- auto-answer log (AC-AFP-4)
def _auto_answer_log_path(root):
    return os.path.join(root, AUTO_ANSWER_LOG_REL)


def record_auto_answer(root, session_id, question, options, chosen, reversibility_rationale, ts=None):
    """AC-AFP-4: append ONE record `{ts, session_id, question, options, chosen,
    reversibility_rationale}` to the append-only auto-answer log under `.foundry/` — never
    truncates/rewrites prior records (open-append only). Write-side reject-closed on an absent
    session identity, mirroring the store's own discipline (no fallback/"unknown" key)."""
    sid = _resolve_write_session_id(session_id)
    if sid is None:
        return {"ok": False, "reason": "no/invalid $CLAUDE_CODE_SESSION_ID (reject-closed, no fallback)"}
    rec = {
        "ts": ts or datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "session_id": sid,
        "question": question,
        "options": options,
        "chosen": chosen,
        "reversibility_rationale": reversibility_rationale,
    }
    log_path = _auto_answer_log_path(root)
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
    except OSError as e:
        return {"ok": False, "reason": f"append failed: {e}"}
    return {"ok": True, "record": rec, "path": log_path}


# ----------------------------------------------------------------------------- resolve (read)
def resolve(root, session_id=None):
    """AC-POS-2 (default `factory`, session-keyed, no cross-session leak) + AC-POS-6 fail-open: always
    returns a value in MODES. `session_id` here is the READ-side id (sanitized, not env-resolved) — the
    statusline supplies the payload `.session_id`; the CLI/selftest may pass one directly."""
    sid = _sanitize_read_session_id(session_id) if session_id is not None else _resolve_write_session_id(None)
    if sid is None:
        return DEFAULT_MODE
    p = _store_path(root, sid)
    if not os.path.isfile(p):
        return DEFAULT_MODE
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return DEFAULT_MODE
    if not isinstance(d, dict) or d.get("session_id") != sid:  # tamper / mis-named file -> ignored
        return DEFAULT_MODE
    m = d.get("mode")
    if m not in MODES:
        return DEFAULT_MODE
    return m


# ----------------------------------------------------------------------------- selftest (hermetic)
def _selftest():
    results = []

    def check(token, ok):
        results.append((token, ok))
        print(f"  {token}: {'PASS' if ok else 'FAIL'}")

    with tempfile.TemporaryDirectory() as root:
        sid_a = "0927afe0-4ed1-4401-abb6-85fa7c380564"
        sid_b = "12340000-0000-4000-8000-000000000000"

        # AC-POS-2: no explicit mode -> default `factory`; independent across session identities.
        d1 = resolve(root, sid_a) == DEFAULT_MODE
        d2 = resolve(root, sid_b) == DEFAULT_MODE
        check("default-factory-no-cross-session-leak", d1 and d2)

        # AC-POS-1: closed-set fail-closed; state unchanged on an unknown value.
        rej = set_mode(root, "bogus-mode", session_id=sid_a)
        rej_ok = rej["ok"] is False and "factory" in rej["reason"] and "noninteractive" in rej["reason"] \
            and "interactive" in rej["reason"]
        unchanged = resolve(root, sid_a) == DEFAULT_MODE
        check("closed-set-fail-closed", rej_ok and unchanged)

        # AC-POS-3: valid set + persist + idempotent echo; independent across sessions (AC-POS-2 cont'd).
        r1 = set_mode(root, "noninteractive", session_id=sid_a)
        set_ok = r1["ok"] is True and r1["mode"] == "noninteractive"
        persisted = resolve(root, sid_a) == "noninteractive"
        r2 = set_mode(root, "noninteractive", session_id=sid_a)  # idempotent re-issue
        idempotent = r2["ok"] is True and r2["mode"] == "noninteractive" and resolve(root, sid_a) == "noninteractive"
        sid_b_independent = resolve(root, sid_b) == DEFAULT_MODE  # not leaked
        check("set-persist-idempotent-no-leak", set_ok and persisted and idempotent and sid_b_independent)

        # AC-POS-8: write-side reject-closed when no session identity resolves (no arg, no env).
        prior_env = os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        try:
            no_id = set_mode(root, "interactive", session_id=None)
        finally:
            if prior_env is not None:
                os.environ["CLAUDE_CODE_SESSION_ID"] = prior_env
        no_id_rejected = no_id["ok"] is False and "mode" not in no_id
        no_file_written = not os.path.isfile(_store_path(root, "unknown"))
        # an invalid (non-UUID) id also rejects.
        bad_id = set_mode(root, "interactive", session_id="not-a-uuid")
        bad_id_rejected = bad_id["ok"] is False
        check("writeside-reject-closed-no-fallback", no_id_rejected and no_file_written and bad_id_rejected)

        # invalid stored value -> fail-safe default (mirrors AC-POS-6's fail-open resolve control).
        with open(_store_path(root, sid_b), "w", encoding="utf-8") as f:
            json.dump({"session_id": sid_b, "mode": "not-a-real-mode"}, f)
        invalid_stored_default = resolve(root, sid_b) == DEFAULT_MODE
        # unreadable (malformed json) -> fail-safe default.
        with open(_store_path(root, sid_b), "w", encoding="utf-8") as f:
            f.write("not json {{")
        unreadable_default = resolve(root, sid_b) == DEFAULT_MODE
        check("fail-safe-default-on-invalid-unreadable", invalid_stored_default and unreadable_default)

        # ── AC-AFP-1/-6: fork_policy — default `park`, set/resolve round-trip of `two-way-auto`,
        # rejection of an out-of-set value, persistence across a simulated re-resolve ─────────────
        sid_c = "abcdefab-cdef-4bcd-8bcd-abcdefabcdef"
        fp_default = resolve_fork_policy(root, sid_c) == DEFAULT_FORK_POLICY
        check("fork-policy-default-park", fp_default)

        fp_rej = set_fork_policy(root, "bogus-policy", session_id=sid_c)
        fp_rej_ok = fp_rej["ok"] is False and "park" in fp_rej["reason"] and "two-way-auto" in fp_rej["reason"]
        fp_unchanged = resolve_fork_policy(root, sid_c) == DEFAULT_FORK_POLICY
        check("fork-policy-closed-set-fail-closed", fp_rej_ok and fp_unchanged)

        fp_set = set_fork_policy(root, "two-way-auto", session_id=sid_c)
        fp_set_ok = fp_set["ok"] is True and fp_set["fork_policy"] == "two-way-auto"
        fp_persisted = resolve_fork_policy(root, sid_c) == "two-way-auto"
        # simulated re-resolve (a fresh call, no in-process cache carried) still reads "two-way-auto".
        fp_reresolved = resolve_fork_policy(root, sid_c) == "two-way-auto"
        # sibling `mode` field is untouched by the fork-policy write (merge-write, no clobber).
        mode_untouched = resolve(root, sid_c) == DEFAULT_MODE
        check("fork-policy-set-resolve-round-trip-persists", fp_set_ok and fp_persisted and fp_reresolved
              and mode_untouched)

    ok = all(v for _, v in results)
    print("SESSION-MODE-SELFTEST-" + ("GREEN" if ok else "RED"))
    return ok


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="foundry_session_mode — session posture store/resolver")
    ap.add_argument("cmd", nargs="?",
                     choices=["set", "resolve", "set-fork-policy", "resolve-fork-policy",
                              "record-auto-answer"],
                     default=None)
    ap.add_argument("arg", nargs="?", default=None, help="set: <mode>; set-fork-policy: <policy>")
    ap.add_argument("--session-id", default=None)
    ap.add_argument("--question", default=None, help="record-auto-answer: the fork's question")
    ap.add_argument("--options", default=None, help="record-auto-answer: JSON array string")
    ap.add_argument("--chosen", default=None, help="record-auto-answer: the chosen option")
    ap.add_argument("--rationale", default=None,
                     help="record-auto-answer: reversibility_rationale")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--root", default=os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
    a = ap.parse_args()

    if a.selftest:
        sys.exit(0 if _selftest() else 1)

    if a.cmd == "set":
        if not a.arg:
            print(f"usage: foundry_session_mode.py set <mode>  (valid modes: {', '.join(MODES)})",
                  file=sys.stderr)
            sys.exit(2)
        res = set_mode(a.root, a.arg, session_id=a.session_id)
        if res["ok"]:
            print(res["mode"])
            sys.exit(0)
        print(f"REJECTED: {res['reason']}", file=sys.stderr)
        sys.exit(1)
    elif a.cmd == "resolve":
        print(resolve(a.root, session_id=a.session_id))
    elif a.cmd == "set-fork-policy":
        if not a.arg:
            print(f"usage: foundry_session_mode.py set-fork-policy <policy>  "
                  f"(valid fork policies: {', '.join(FORK_POLICIES)})", file=sys.stderr)
            sys.exit(2)
        res = set_fork_policy(a.root, a.arg, session_id=a.session_id)
        if res["ok"]:
            print(res["fork_policy"])
            sys.exit(0)
        print(f"REJECTED: {res['reason']}", file=sys.stderr)
        sys.exit(1)
    elif a.cmd == "resolve-fork-policy":
        print(resolve_fork_policy(a.root, session_id=a.session_id))
    elif a.cmd == "record-auto-answer":
        if not a.question or not a.chosen:
            print("usage: foundry_session_mode.py record-auto-answer --question <q> --chosen <opt> "
                  "[--options <json-array>] [--rationale <text>] [--session-id <id>]", file=sys.stderr)
            sys.exit(2)
        options = []
        if a.options:
            try:
                options = json.loads(a.options)
            except ValueError:
                print("REJECTED: --options must be a JSON array string", file=sys.stderr)
                sys.exit(2)
        res = record_auto_answer(a.root, a.session_id, a.question, options, a.chosen,
                                  a.rationale or "")
        if res["ok"]:
            print(json.dumps(res["record"], sort_keys=True))
            sys.exit(0)
        print(f"REJECTED: {res['reason']}", file=sys.stderr)
        sys.exit(1)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
