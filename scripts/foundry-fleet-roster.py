#!/usr/bin/env python3
"""foundry-fleet-roster — the /foundry:fleet single-pane view over the session-registry.

Implements feat-foundry-fleet-roster (v4). Renders the native session list (via the session-registry's
discriminated `list` outcome) augmented with the foundry work-context columns, a `pending_decision`
marker, AND the headline process-machinery scan columns (ISO/GATE/WF·step) joined from the
session-machinery overlay by `session_id`. It does NOT re-host native's needs-input float/count
(`claude agents` owns that), does NOT enumerate (the registry does), and does NOT derive machinery
(the overlay does — the roster only reads + joins the typed record).

Read-only; secret-scrubbed + sanitized on EVERY rendered string (the native summary/name are
session-content-derived → the security-review floor applies). DEFAULT-DENY on machinery: a field renders
clear ONLY for an explicit known-safe value; every other/unknown/novel/null risk value ⇒ `⚠`; a
missing machinery block ⇒ `⚠ machinery unavailable`, never blank-as-green.

CLI: foundry-fleet-roster.py [--json] | --selftest   (AC-ROST-1..7). Runs in ANY session (read-only).
"""
import argparse
import json
import os
import re
import sys
import tempfile

# AC-ROST-5: control-char neutralization over BOTH the C0 range (0x00–0x1F incl. newline) and the C1
# range (0x80–0x9F) plus 0x7F and ANSI/CSI/OSC escape introducers (a C1-range introducer is a
# terminal-injection vector a C0-only filter misses).
_CTRL_RE = re.compile(r"(\x1b\[[0-9;]*[A-Za-z]|\x1b[@-Z\\-_]|[\x00-\x1f\x7f-\x9f])")
# NOTE: kept byte-identical to the machinery/registry secret regex (the most complete variant) so the
# scrub coverage cannot drift across the fleet surfaces (security-review finding).
_SECRET_RE = re.compile(
    r"(?i)(?:sk-[a-z0-9]{16,}|ghp_[a-z0-9]{20,}|gh[opusr]_[a-z0-9]{20,}"
    r"|aws_secret[^\s]*|AKIA[0-9A-Z]{16}|[A-Za-z0-9+/]{40,}={0,2}"
    r"|(?:password|secret|token|api[_-]?key)\s*[:=]\s*\S+)"
)


def _clean(v):
    """AC-ROST-5: secret-scrub THEN neutralize control/ANSI/newline (C0+C1) — both render paths.
    The scrub/sanitize set is EVERY rendered string, not a closed enumeration."""
    if not isinstance(v, str):
        return v
    return _CTRL_RE.sub("", _SECRET_RE.sub("«redacted»", v))


def _import_sibling(filename, modname):
    import importlib.util
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    spec = importlib.util.spec_from_file_location(modname, p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _import_registry():
    return _import_sibling("foundry-fleet-session-registry.py", "fleet_session_registry")


def _import_machinery():
    return _import_sibling("foundry-fleet-session-machinery.py", "fleet_session_machinery")


def _registry_list(root):
    """Input seam = the session-registry `list` (the roster does no enumeration of its own)."""
    return _import_registry().list_sessions(root)


def _join_machinery(root, reg_result):
    """AC-ROST-6 join: call the session-machinery overlay keyed on the registry's session_ids and join
    by session_id (a JOIN, not an enumeration). Returns {session_id: machinery_record}."""
    try:
        return _import_machinery().derive_all(root, registry_result=reg_result).get("machinery", {})
    except Exception:
        return {}     # total overlay failure → every row is `⚠ machinery unavailable` (fail-closed)


def _summary_line(r):
    """AC-ROST-2: native Haiku summary SUFFIXED with the foundry atom/step (augment, not regenerate)."""
    base = r.get("native_summary") or "—"
    atom = r.get("atom_or_spec")
    return f"{base} · {atom}" if atom else base


# ----------------------------------------------------------------------------- machinery columns (DEFAULT-DENY)
def _machinery_cols(mrec):
    """AC-ROST-6: the headline scan columns (ISO/GATE/WF·step) from the joined machinery record, under
    DEFAULT-DENY. Returns a dict of display tokens. A MISSING/`unavailable` block (None, a join miss, or
    the machinery `unavailable` outcome) ⇒ the whole row is `⚠ machinery unavailable` (never blank)."""
    mach = _import_machinery()
    if not mrec or mrec.get("status") == "unavailable":
        return {"unavailable": True, "iso": "⚠ machinery unavailable", "gate": "", "wf": ""}

    def deny(field, value, *, break_glass=False):
        # clear ONLY for an explicit known-safe value; every other/unknown/novel/null ⇒ ⚠.
        if mach.is_field_clear(field, value, break_glass=break_glass):
            return str(value)
        return f"⚠{value}" if value is not None else "⚠—"

    iso = deny("isolation", mrec.get("isolation"))
    gate = deny("gate_readiness", mrec.get("gate_readiness"))
    # WF·step is honestly SPARSE: the workflow pointer is the invoking session's own, so this is
    # populated only for that row and shows "—" for others (until the pointer is session_id-keyed).
    wf = mrec.get("workflow")
    wf_tok = f"{_clean(wf.get('id'))}·{wf.get('position')}/{wf.get('total')}" if wf else "—"
    return {"unavailable": False, "iso": iso, "gate": gate, "wf": wf_tok}


def build_rows(reg_result, machinery=None):
    """AC-ROST-1..3/6: one row per registry session — native base ⊕ overlay ⊕ summary ⊕ pending flag ⊕
    machinery scan columns (joined by session_id). Every string cleaned (AC-ROST-5). Order = registry's."""
    machinery = machinery or {}
    rows = []
    for r in reg_result.get("sessions", []):
        sid = r["session_id"]
        cols = _machinery_cols(machinery.get(sid))
        row = {
            "session_id": sid,
            "repo": _clean(r.get("repo")) or "—",
            "branch": _clean(r.get("branch")) or "—",
            "name": _clean(r.get("name")) or "—",
            "state": _clean(r.get("state")) or "—",
            # AC-ROST-2: liveness = best-effort native updated_at; missing ⇒ "—", NEVER a fabricated
            # "live". The registry v3.2 no longer computes a dead-PID stale flag, so no `~ stale` glyph.
            "liveness": r.get("updated_at") if r.get("updated_at") is not None else "—",
            "epic": _clean(r.get("epic_ref")) or r.get("epic_kind") or "—",
            "atom": _clean(r.get("atom_or_spec")) or "—",
            "governance": _clean(r.get("governance_state")) or "—",
            "needs_decision": bool(r.get("pending_decision")),   # AC-ROST-3 marker (NOT a native count)
            "summary": _clean(_summary_line(r)),
            # AC-ROST-6 machinery scan columns (already DEFAULT-DENY-rendered + scrubbed below)
            "machinery_unavailable": cols["unavailable"],
            "iso": _clean(cols["iso"]),
            "gate": _clean(cols["gate"]),
            "wf": _clean(cols["wf"]),
        }
        rows.append(row)
    return rows


def render(reg_result, machinery=None, as_json=False):
    """AC-ROST-1 discriminated outcome: ok→rows; native_unavailable→VISIBLE banner (never empty);
    degraded→rows+banner. AC-ROST-5: the banner reason is scrubbed too (gh/git stderr may carry tokens)."""
    status = reg_result.get("status")
    rows = build_rows(reg_result, machinery) if status != "native_unavailable" else []
    if as_json:
        # AC-ROST-4/5: typed rows, cleaned on the --json path too; banner reason scrubbed.
        return json.dumps({"status": status, "reason": _clean(reg_result.get("reason")), "rows": rows}, indent=2)
    if status == "native_unavailable":
        # AC-ROST-1: fail-CLOSED — a visible banner, NOT an empty "all clear".
        return (f"⚠ fleet: native session list UNAVAILABLE — {_clean(reg_result.get('reason'))}\n"
                "(cannot show sessions; this is NOT 'nothing running')")
    out = []
    if status == "degraded":
        out.append(f"⚠ degraded: {_clean(reg_result.get('reason'))}")
    out.append(f"{'SESSION':10} {'STATE':6} {'!':1} {'ISO':18} {'GATE':14} {'WF·step':16} "
               f"{'EPIC':20} {'GOV':10} SUMMARY")
    for r in rows:
        flag = "◀" if r["needs_decision"] else " "
        if r["machinery_unavailable"]:
            # span the unavailable marker across the machinery columns (never truncate it to blank-green).
            mach = f"{'⚠ machinery unavailable':50}"
        else:
            mach = f"{r['iso'][:18]:18} {r['gate'][:14]:14} {r['wf'][:16]:16}"
        out.append(f"{r['session_id'][:8]:10} {r['state'][:6]:6} {flag:1} {mach} "
                   f"{r['epic'][:20]:20} {r['governance'][:10]:10} {r['summary'][:48]}")
    out.append(f"\n{len(rows)} session(s).  ◀ = foundry decision pending · ISO/GATE DEFAULT-DENY "
               "(⚠ = attention) · WF·step is invoking-session-only "
               "· (generic needs-input grouping lives in `claude agents`)")
    return "\n".join(out)


# ----------------------------------------------------------------------------- selftest (hermetic)
def _selftest():
    results = []

    def check(t, ok):
        results.append((t, ok)); print(f"  {t}: {'PASS' if ok else 'FAIL'}")

    ok_fixture = {"status": "ok", "sessions": [
        {"session_id": "0927afe0-4ed1-4401-abb6-85fa7c380564", "repo": "/w/agentic-workspace",
         "branch": "feat/sk-abcdef0123456789abcdef", "name": "op\x1b[31mX\x1b[0m\nload", "state": "busy",
         "updated_at": 111, "native_summary": "secret token: sk-abcdef0123456789abcdef",
         "epic_kind": "feature", "epic_ref": "feature:specs/.../feat-x.md",
         "atom_or_spec": "specs/.../feat-x.md", "governance_state": "authorized",
         "pending_decision": "merge approval?"},
        {"session_id": "12340000-0000-4000-8000-000000000000", "repo": "/w/other", "branch": None,
         "name": "other", "state": "idle", "updated_at": None, "native_summary": None,
         "epic_kind": "ad_hoc", "epic_ref": None, "atom_or_spec": None, "governance_state": None,
         "pending_decision": None},
    ]}
    # a joined machinery overlay: row A is the invoking session (clear worktree + gate_pass + workflow);
    # row B has an attention-bearing block (direct_main + unknown gate); row C (added below) is MISSING.
    machinery = {
        "0927afe0-4ed1-4401-abb6-85fa7c380564": {
            "session_id": "0927afe0-4ed1-4401-abb6-85fa7c380564", "status": "ok",
            "workflow": {"id": "software-delivery", "position": 5, "total": 13},
            "isolation": "worktree", "gate_readiness": "gate_pass",
            "mode": {"stage": "lean", "merge_autonomy": "lean"}, "security_flag": "clear",
            "target_repo": None, "blast_radius": None, "ctx_posture": None, "break_glass": False,
            "infra": False, "sources": {}},
        "12340000-0000-4000-8000-000000000000": {
            "session_id": "12340000-0000-4000-8000-000000000000", "status": "ok",
            "workflow": None, "isolation": "direct_main", "gate_readiness": "unknown",
            "mode": {"stage": "lean", "merge_autonomy": None}, "security_flag": "needs_review",
            "target_repo": None, "blast_radius": None, "ctx_posture": None, "break_glass": False,
            "infra": False, "sources": {}},
    }
    rows = build_rows(ok_fixture, machinery)
    a, b = rows[0], rows[1]

    # AC-ROST-1: complete inventory (none dropped; ad_hoc shown) + fail-closed on native_unavailable
    inv = len(rows) == 2 and b["epic"] == "ad_hoc"
    banner = "UNAVAILABLE" in render({"status": "native_unavailable", "reason": "no claude"})
    empty_forbidden = "NOT 'nothing running'" in render({"status": "native_unavailable", "reason": "x"})
    reg = _import_registry()
    with tempfile.TemporaryDirectory() as _tr:
        _spine = json.dumps([{"sessionId": "0927afe0-4ed1-4401-abb6-85fa7c380564", "cwd": "/w",
                              "name": "n", "status": "busy", "pid": 1, "startedAt": 1}])
        _live = reg.list_sessions(_tr, agents_json=_spine)
        seam = hasattr(reg, "list_sessions") and _live["status"] in ("ok", "degraded") \
            and "SESSION" in render(_live, _join_machinery(_tr, _live))
    check("AC-ROST-1 complete-inventory-fail-closed", inv and banner and empty_forbidden and seam)

    # AC-ROST-2: native base ⊕ overlay ⊕ summary (native + atom suffix); liveness = updated_at / "—",
    # NO fabricated "live" and NO dead-PID `~ stale` flag.
    base_overlay = (a["repo"] == "/w/agentic-workspace" and a["governance"] == "authorized"
                    and a["atom"].endswith("feat-x.md"))
    summary_suffix = a["summary"].endswith("feat-x.md")
    liveness = a["liveness"] == 111 and b["liveness"] == "—"
    no_stale_glyph = "stale" not in render(ok_fixture, machinery).lower() and "~" not in render(ok_fixture, machinery)
    check("AC-ROST-2 native-base-overlay-summary", base_overlay and summary_suffix and liveness and no_stale_glyph)

    # AC-ROST-3: pending_decision marker, NOT a native count re-host
    term = render(ok_fixture, machinery)
    no_count_rehost = "N awaiting" not in term and "awaiting input" not in term.lower()
    marker = a["needs_decision"] is True and b["needs_decision"] is False
    check("AC-ROST-3 pending-decision-flag-not-native-rehost", marker and no_count_rehost)

    # AC-ROST-4: deterministic + --json typed rows (read-only by construction — no mutating calls)
    det = render(ok_fixture, machinery, as_json=True) == render(ok_fixture, machinery, as_json=True)
    js = json.loads(render(ok_fixture, machinery, as_json=True))
    json_typed = js["status"] == "ok" and len(js["rows"]) == 2
    check("AC-ROST-4 read-only-deterministic", det and json_typed)

    # AC-ROST-5: secret-scrub + sanitize EVERY string incl. branch/repo/machinery cols/banner, C0 AND C1
    js_txt = render(ok_fixture, machinery, as_json=True)
    scrubbed = "sk-abcdef" not in term and "sk-abcdef" not in js_txt   # native_summary AND branch scrubbed
    sanitized = "\x1b" not in a["name"] and "\n" not in a["name"] and "\x1b" not in js_txt
    # C1-range neutralization (0x80–0x9F) — a C0-only filter misses it.
    c1 = "\x9b" not in _clean("x\x9bevil") and "\x85" not in _clean("a\x85b")
    # the native_unavailable BANNER reason is scrubbed too.
    banner_scrub = "sk-abcdef" not in render({"status": "native_unavailable",
                                              "reason": "gh error: token sk-abcdef0123456789abcdef"})
    check("AC-ROST-5 secret-scrub-sanitization-floor", scrubbed and sanitized and c1 and banner_scrub)

    # AC-ROST-6: machinery columns DEFAULT-DENY + missing-block ⇒ "⚠ machinery unavailable"
    # row A: clear values render plainly; row B: attention values render ⚠.
    a_clear = a["iso"] == "worktree" and a["gate"] == "gate_pass" and a["wf"].startswith("software-delivery")
    b_attn = a is not b and b["iso"].startswith("⚠") and b["gate"].startswith("⚠")  # direct_main + unknown
    # an out-of-vocabulary / novel GATE token is attention, not a silent clear.
    novel = _machinery_cols({"session_id": "z", "status": "ok", "workflow": None,
                             "isolation": "worktree", "gate_readiness": "totally_new_token"})
    oov = novel["gate"].startswith("⚠")
    # a MISSING machinery block (join miss / unavailable) ⇒ ⚠ machinery unavailable, never blank.
    miss_fixture = {"status": "ok", "sessions": [ok_fixture["sessions"][0]]}
    miss_rows = build_rows(miss_fixture, {})                # no joined record for this session_id
    miss = miss_rows[0]["machinery_unavailable"] is True and "machinery unavailable" in render(miss_fixture, {})
    unavail_outcome = _machinery_cols({"session_id": "z", "status": "unavailable", "reason": "boom"})["unavailable"]
    check("AC-ROST-6 machinery-columns-gate-fails-closed", a_clear and b_attn and oov and miss and unavail_outcome)

    # AC-ROST-7: universal any-session invocability — read-only, no dispatcher worker-assignment file
    # dependency, invocation-independent enumeration (the whole fleet regardless of which session invokes).
    src = open(os.path.abspath(__file__), encoding="utf-8").read()
    # needles built dynamically so the check literals don't self-match this scanner's own source.
    no_assignment = ("assignment" + ".json") not in src
    no_writes = all(n not in src for n in (".wr" + "ite(", "os.re" + "name", "mks" + "temp",
                                           "make" + "dirs"))
    # render is identical regardless of the invoking session id in the env.
    os.environ["CLAUDE_CODE_SESSION_ID"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    r1 = render(ok_fixture, machinery)
    os.environ["CLAUDE_CODE_SESSION_ID"] = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    r2 = render(ok_fixture, machinery)
    del os.environ["CLAUDE_CODE_SESSION_ID"]
    invocation_independent = r1 == r2 and len(rows) == 2
    check("AC-ROST-7 universal-any-session-read-only",
          no_assignment and no_writes and invocation_independent)

    ok = all(v for _, v in results)
    print("FLEET-ROSTER-SELFTEST-" + ("GREEN" if ok else "RED"))
    return ok


def main():
    ap = argparse.ArgumentParser(description="foundry-fleet-roster (/foundry:fleet)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--root", default=os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if _selftest() else 1)
    reg = _registry_list(a.root)
    print(render(reg, _join_machinery(a.root, reg), as_json=a.json))


if __name__ == "__main__":
    main()
