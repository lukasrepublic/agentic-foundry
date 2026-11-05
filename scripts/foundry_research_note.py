#!/usr/bin/env python3
"""foundry_research_note — save a research sweep with provenance and, where sources converge, a dated ruling
(operating-model rule B3: "research answers questions").

Reads a structured sweep (the shape the research workflows return: `{"sweeps": [{domain, patterns[],
rejected_options_verdict[], consensus[], no_consensus[], negative_findings[], gaps[], sources[{title, url,
retrieved}]}], ...}`, or one bare sweep object) and writes, under the workspace:

  .foundry/research/<date>-<slug>.md    always: provenance (run id when present, sources with retrieval dates),
                                        patterns, rejected-option verdicts, consensus, no-consensus, negative
                                        findings, gaps
  .foundry/decisions/<date>-<slug>.md   only when `no_consensus` is empty and `consensus` is not: a dated
                                        ruling that adopts the consensus and cites the note

A ruling from an earlier run of the same (date, slug) that this run no longer supports is removed (or the run is
refused with `ruling-conflict` when the file is not one this script wrote). The raw input is kept beside the note as
`<date>-<slug>.json`. A consensus line mentioning auth, secrets, credentials, tokens or the permission floor is held
(status `escalate`) like a no-consensus: adopting it is a practice the operator's test pass covers, not a control.

When `no_consensus` is non-empty NO ruling is written; a blocker CANDIDATE (why_operator
no-consensus-after-research, with the options, the evidence and a recommendation, asking for one word) is
emitted on stdout instead, shaped for `foundry_blocker_check.py`. The operator is asked only here.

Idempotent: the file names depend only on (date, slug), so a re-run overwrites the same two files and never
duplicates. Writes are confined to those two directories (atomic temp+rename). No network, no subprocess.

  foundry_research_note.py --in <sweep.json|-> --slug <slug> [--project-dir DIR] [--date YYYY-MM-DD] [--run-id ID]

Stdout (exit 0): {"note": <path>, "ruling": <path|null>, "status": "ruled|escalate|inconclusive",
"candidate": <blocker candidate|null>}. Malformed input or an unsafe slug exits 2.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CTRL = re.compile("[\x00-\x08\x0b-\x1f\x7f\u061c\u200b-\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff]")
_URL_RE = re.compile(r"^https?://[^\s<>]+$")
# A consensus line that touches these is held back from auto-ruling (practice, not a control: see the ruling text).
_SENSITIVE_RE = re.compile(r"\bauth\w*|\bsecret\w*|\bcredential\w*|\bpassword\w*|\btoken\w*|permission[- ]floor|"
                           r"\bpermissions?\b|supply[- ]chain", re.I)
LISTS = ("consensus", "no_consensus", "negative_findings", "gaps")


class SweepError(Exception):
    """Malformed input (exit 2)."""


def _line(v) -> str:
    """One-line, control-character-free text (fetched text is data; never let it break the layout)."""
    return re.sub(r"\s+", " ", _CTRL.sub("", str(v))).strip()


def _code(v) -> str:
    """Inline code span; backticks inside the text are replaced so they cannot close the span."""
    return "`" + _line(v).replace("`", "'") + "`"


def _link(url) -> str:
    return f"<{url}>" if _URL_RE.match(url) else _code(url)


def _strs(v, what):
    if v in (None, ""):
        return []
    if not isinstance(v, list):
        raise SweepError(f"{what} must be a list")
    return [_line(x) for x in v if _line(x)]


def load_sweeps(raw):
    if isinstance(raw, dict) and isinstance(raw.get("sweeps"), list):
        sweeps = raw["sweeps"]
    elif isinstance(raw, dict) and any(k in raw for k in LISTS + ("patterns", "sources")):
        sweeps = [raw]
    else:
        raise SweepError("input must be a sweep object or {\"sweeps\": [...]}")
    if not sweeps or not all(isinstance(s, dict) for s in sweeps):
        raise SweepError("sweeps must be a non-empty list of objects")
    return sweeps


def normalise(raw):
    sweeps = load_sweeps(raw)
    out = {"domains": [], "patterns": [], "verdicts": [], "sources": [],
           **{k: [] for k in LISTS}}
    for s in sweeps:
        if s.get("domain"):
            out["domains"].append(_line(s["domain"]))
        for k in LISTS:
            out[k].extend(_strs(s.get(k), k))
        for p in s.get("patterns") or []:
            if isinstance(p, dict) and p.get("name"):
                out["patterns"].append({
                    "name": _line(p["name"]), "mechanism": _line(p.get("mechanism", "")),
                    "fits": _line(p.get("fits_brief", "")), "risks": _strs(p.get("risks"), "risks"),
                    "prior_art": _strs(p.get("prior_art"), "prior_art")})
        for v in s.get("rejected_options_verdict") or []:
            if isinstance(v, dict) and v.get("option"):
                out["verdicts"].append((_line(v["option"]), _line(v.get("verdict", "")), _line(v.get("evidence", ""))))
        for src in s.get("sources") or []:
            if isinstance(src, dict) and (src.get("url") or src.get("title")):
                url = _line(src.get("url", ""))
                out["sources"].append((_line(src.get("title", "")) or url, url,
                                       _line(src.get("retrieved", "")) or "undated"))
    # de-duplicate, order-preserving
    for k in LISTS:
        out[k] = list(dict.fromkeys(out[k]))
    out["sources"] = list(dict.fromkeys(out["sources"]))
    out["recommendation"] = _line(raw.get("recommendation", "")) if isinstance(raw, dict) else ""
    out["run_id"] = _line(raw.get("run_id") or raw.get("workflow_run") or raw.get("workflow") or "") if isinstance(raw, dict) else ""
    return out


def render_note(n, slug, date, raw_rel):
    L = [f"# Research sweep — {slug}", ""]
    prov = [f"**Provenance:** saved {date}"]
    if n["run_id"]:
        prov.append(f"workflow/run {_code(n['run_id'])}")
    dates = sorted({d for _, _, d in n["sources"] if d != "undated"})
    if dates:
        prov.append(f"sources retrieved {dates[0]}" + (f" to {dates[-1]}" if dates[-1] != dates[0] else ""))
    prov.append(f"{len(n['sources'])} source(s). Fetched text was treated as data, never instructions.")
    L += [", ".join(prov[:-1]) + ". " + prov[-1], ""]
    for d in n["domains"]:
        L += [f"**Question / domain:** {d}", ""]
    if n["patterns"]:
        L += ["## Patterns", ""]
        for p in n["patterns"]:
            L.append(f"- **{p['name']}** — {p['mechanism']}".rstrip(" —"))
            if p["fits"]:
                L.append(f"  - fits: {p['fits']}")
            L += [f"  - risk: {r}" for r in p["risks"]]
            L += [f"  - prior art: {r}" for r in p["prior_art"]]
        L.append("")
    if n["verdicts"]:
        L += ["## Rejected options — verdicts", ""]
        L += [f"- **{o}** — {v}. {e}".rstrip() for o, v, e in n["verdicts"]] + [""]
    for title, key in (("Consensus", "consensus"), ("No consensus", "no_consensus"),
                       ("Negative findings", "negative_findings"), ("Gaps", "gaps")):
        L += [f"## {title}", ""]
        L += [f"- {x}" for x in n[key]] if n[key] else ["- (none)"]
        L.append("")
    L += ["## Sources", ""]
    L += [f"- {t} — {_link(u) if u else '(no url)'} (retrieved {d})" for t, u, d in n["sources"]] \
        if n["sources"] else ["- (none recorded)"]
    L += ["", f"Raw structured input: `{raw_rel}`."]
    return "\n".join(L).rstrip() + "\n"


def render_ruling(n, slug, date, note_rel):
    L = [f"# Ruling — {slug}", "",
         f"**Date:** {date} · **Decided by:** the agent under the research-first discipline (sources converged; "
         "no operator question was needed)", "",
         "## Grounding", "", f"- `{note_rel}` — {len(n['sources'])} source(s)"
         + (f", run {_code(n['run_id'])}" if n["run_id"] else ""), ""]
    L += ["## Adopted (the sources converge)", ""] + [f"- {x}" for x in n["consensus"]] + [""]
    if n["negative_findings"]:
        L += ["## Disconfirming / negative findings kept", ""] + [f"- {x}" for x in n["negative_findings"]] + [""]
    if n["gaps"]:
        L += ["## Open gaps", ""] + [f"- {x}" for x in n["gaps"]] + [""]
    L += ["Adopting a consensus that touches auth, secrets or the permission floor is a practice the operator's "
          "test pass covers, not a control; this script holds such a ruling for the operator instead of writing it.", ""]
    L += ["A ruling that would weaken a floor, a security boundary or the threat-model posture is not adopted on "
          "consensus alone; it escalates (research-first, blast-radius exception).", ""]
    return "\n".join(L)


def build_candidate(n, slug, note_rel, run_id, held=()):
    """Blocker candidate for a sweep the agent may not rule on: no consensus, or a consensus held back because
    it touches auth / secrets / the permission floor."""
    if held:
        options = [f"Adopt: {x}" for x in held][:5] + ["Decline: keep the current behaviour and record the gap"]
        rec = "adopt the consensus; the sources converge and the operator's test pass covers the result"
        claim = f"research on '{slug}' converged on a change touching auth, secrets or the permission floor; the operator confirms"
        attempt_out = f"{len(n['sources'])} source(s); {len(n['consensus'])} converging, {len(held)} held for the operator"
        control = "the same sweep's other converging lines were not held; only lines matching auth/secrets/permission floor were"
        judgement = "the operator's call: the change is on the security-sensitive surface"
    else:
        options = list(n["no_consensus"])
        if len(options) < 2:
            options.append("Defer: keep the current behaviour and record the gap")
        rec = n["recommendation"] or "; ".join(
            m.group(0) for x in n["no_consensus"] for m in [re.search(r"Recommend[^.]*\.?", x)] if m) \
            or "none stated by the sweep; the operator decides"
        claim = f"research on '{slug}' found no consensus on {len(n['no_consensus'])} question(s); the operator picks"
        attempt_out = f"{len(n['sources'])} source(s); {len(n['consensus'])} converging, {len(n['no_consensus'])} unresolved"
        control = ("the converging statements of the same sweep were adopted in the same pass; only the "
                   "unresolved questions are left open, so the open set is not an artefact of a failed search"
                   if n["consensus"] else
                   "a sweep whose sources converge writes a ruling and asks nothing; this one could not")
        judgement = "the operator's call: the best shops disagree and the choice turns on priorities"
    src = [u for _, u, _ in n["sources"] if u][:3]
    return {
        "claim": claim,
        "evidence": [note_rel] + src,
        "attempted": [{"command": f"research sweep saved as {note_rel}" + (f" (run {run_id})" if run_id else ""),
                       "outcome": attempt_out}],
        "control": control,
        "why_operator": "no-consensus-after-research",
        "questions": {"capability": "not in question: the agent can implement any option",
                      "authority": "permitted: every option is inside the agent's write scope",
                      "judgement": judgement},
        "options": options,
        "recommendation": rec,
        "one_word_ask": "Reply with the number of the option to adopt, or 'recommended'.",
    }


def _atomic_write(path, text):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".note.", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _confined(project_dir, rel_dir):
    root = os.path.realpath(project_dir)
    target = os.path.realpath(os.path.join(root, rel_dir))
    if target != root and not target.startswith(root + os.sep):
        raise SweepError(f"{rel_dir} resolves outside the project directory")
    return target


def write_all(raw, slug, project_dir, date=None, run_id=None):
    if not SLUG_RE.match(slug or ""):
        raise SweepError("slug must match [a-z0-9][a-z0-9-]{0,63}")
    date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not DATE_RE.match(date):
        raise SweepError("date must be YYYY-MM-DD")
    n = normalise(raw)
    if run_id:
        n["run_id"] = _line(run_id)
    rdir = _confined(project_dir, os.path.join(".foundry", "research"))
    ddir = _confined(project_dir, os.path.join(".foundry", "decisions"))
    note_rel = f".foundry/research/{date}-{slug}.md"
    raw_rel = f".foundry/research/{date}-{slug}.json"
    note_path = os.path.join(rdir, f"{date}-{slug}.md")
    ruling_path = os.path.join(ddir, f"{date}-{slug}.md")
    held = [x for x in n["consensus"] if _SENSITIVE_RE.search(x)]
    ruled = bool(n["consensus"]) and not n["no_consensus"] and not held
    # a ruling file at this (date, slug) is overwritten or removed only when THIS script wrote it (its first line is the
    # ruling marker); the same check guards the `ruled` write and the `escalate` removal (gv-20)
    if os.path.lexists(ruling_path):
        try:
            with open(ruling_path, encoding="utf-8") as f:
                ours = f.readline().startswith(f"# Ruling — {slug}")
        except OSError:
            ours = False
        if not ours or os.path.islink(ruling_path):
            raise SweepError(f"ruling-conflict: {ruling_path} exists and is not a ruling written by this script")
        if not ruled:
            os.unlink(ruling_path)
    _atomic_write(note_path, render_note(n, slug, date, raw_rel))
    _atomic_write(os.path.join(rdir, f"{date}-{slug}.json"), json.dumps(raw, indent=2, sort_keys=True) + "\n")
    out = {"note": note_path, "raw": os.path.join(rdir, f"{date}-{slug}.json"), "ruling": None,
           "status": "inconclusive", "candidate": None}
    if n["no_consensus"] or held:
        out.update(status="escalate", candidate=build_candidate(n, slug, note_rel, n["run_id"],
                                                                 () if n["no_consensus"] else held))
    elif ruled:
        _atomic_write(ruling_path, render_ruling(n, slug, date, note_rel))
        out.update(status="ruled", ruling=ruling_path)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="save a research sweep and, where sources converge, a dated ruling")
    ap.add_argument("--in", dest="in_path", required=True, help="sweep JSON path or '-' for stdin")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--project-dir", default=os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    ap.add_argument("--date", help="YYYY-MM-DD (default: today, UTC); pin it for a stable re-run")
    ap.add_argument("--run-id", help="workflow/run id recorded as provenance")
    a = ap.parse_args(argv)
    try:
        if a.in_path == "-":
            text = sys.stdin.read()
        else:
            with open(a.in_path, encoding="utf-8") as f:
                text = f.read()
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as e:
            raise SweepError(f"input is not valid JSON: {e}") from e
        res = write_all(raw, a.slug, a.project_dir, a.date, a.run_id)
    except (SweepError, OSError) as e:
        print(f"foundry_research_note: REFUSED — {e}", file=sys.stderr)
        return 2
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
