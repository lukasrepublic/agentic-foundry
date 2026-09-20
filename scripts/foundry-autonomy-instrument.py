#!/usr/bin/env python3
"""foundry-autonomy-instrument — six report-only autonomy ratios (feat
yield-and-silent-yield-instrument). Promotes the two workspace extractors
(`docs/micro-workflows/empirical-mining/mine_failures.py`, `spec_signals.py`) into one Foundry
CLI, per their method doc `mine-autonomy-2026-09-18.md` §0.

REPORT ONLY (AC-INS-5): this CLI gates nothing on the RATIOS it computes — it always exits 0
regardless of the computed ratios, and writes no ledger and no `.foundry/` file unless `--out` is
given. A malformed/unsafe `--out` argument is a distinct CLI-usage refusal (non-zero exit, clear
stderr message), not a "gated ratio" — see `validate_out_path()`.

PRE-EXISTING BUG, FOUND BY THIS ATOM'S OWN round-2 review (certify-by-remeasure, AC-CBR-1): every
prior run of this instrument AND its two workspace-extractor ancestors
(`mine_failures.py`/`spec_signals.py`, `mine-autonomy-2026-07-16.md`/`mine-autonomy-2026-09-18.md`)
evaluated extended thinking's multi-fragment assistant turns per RAW record instead of per
LOGICAL turn (see `_group_logical_assistant_turns()`). A thinking-only fragment misread as a
tool-free "stop" inflated BOTH `other-harness` (its next record is usually the sibling tool_use's
tool_result) and `silent_yield` (a phantom SILENT stop preceding every extended-thinking turn that
happened to end in a real question, since the thinking fragment alone never carries the
question). The July 2026-07-16 and September 2026-09-18 mining numbers this instrument was built
to reproduce (the "0.81" fixture, the R3-boundary 0.80 read) are TAINTED by this same bug and
should be treated as upper bounds, not as the corrected ground truth — this fix changes the
computed ratios going forward but does not retroactively correct those two prior write-ups.

The six ratios:
  1. silent-yield        — of the agent's real "stops" (a tool-free assistant turn end) that are
                            `human-resumed` (see the three-way classification below), the share
                            with no trailing question either. The corrected instrument from §0's
                            blind spot: `ended_with_open_question` cannot see a silent stop. AC-
                            CBR-1 (certify-by-remeasure): a tool-free turn end followed by a
                            harness envelope — the agent kept working on its own, no human was
                            needed — is `harness-resumed`, never counted in this ratio's
                            denominator; one that ends the transcript is `session-end`. All three
                            counts are reported in `corpus.turn_ends` alongside the ratio, plus a
                            `harness_resumed_by_kind` sub-kind breakdown (round-2 review:
                            `classify_envelope_kind()` is now actually consulted per turn end,
                            not dead code next to the binary `classify_turn_end()`).
  2. directive-reply      — of those same stops, the share answered by an operator reply of
                            <=45 characters (`merge it`, `go`, `push to main`).
  3. granted-verb-denial  — of every classifier/hook denial found in a tool_result, the share
                            whose denied Bash command is already covered by a LONGEST-PREFIX
                            match against the workspace's `Bash(<prefix>:*)` settings allow rules
                            (a denial the operator believes is granted). Round-2 review fix: a
                            first-token ("verb") match would incorrectly count `git push` as
                            granted next to an allowed `Bash(git commit:*)` — this compares the
                            full command prefix, not just the first word.
  4. guard-false-positive — SAMPLED, never auto-classified: up to `--sample-size` (default 40)
                            of the denials above. Excerpts are OPT-IN (`--with-excerpts`, default
                            off): off, the sample reports counts + file/turn references only; on,
                            secret-shaped spans are redacted before truncation (see `redact()`).
  5. authorized->built    — the INTERSECTION of the front-authorization corpus (specs/**/
                            acceptance-contract.yaml carrying a real, non-placeholder
                            spec_sha256) and the spec_refs actually recorded in a
                            `.foundry/build-provenance.yaml` under `--repo`, divided by the
                            authorized count. Round-2 review fix: a provenance marker for a
                            spec that was never authorized must not inflate the numerator.
  6. rounds-per-shipped   — total `rounds` summed across every `.foundry/audit-ledger.jsonl` row
                            found under `--repo`, divided by the count of distinct built spec_refs.

certify-by-remeasure (AC-CBR-1): a turn-end's THREE-WAY classification, matched on STRUCTURAL
envelope markers only — never on the wrapped text's prose:
  - `human-resumed`   — the next user-role record is a genuine operator turn
                        (`is_operator_turn()` below returns True — `origin.kind == "human"`, or
                        the legacy `promptSource` fallback).
  - `harness-resumed` — the next user-role record is a system notification. `mine_file()` also
                        attributes it a sub-kind, via `classify_envelope_kind()`, one of THREE
                        documented shapes (quoted here from real ~/.claude/projects transcripts,
                        never regex-matched against their prose) plus "other-harness" for any
                        non-human shape none of the three match (never silently dropped):
                          * "task-notification": the literal tag `<task-notification>` in the
                            message text; structurally `origin == {"kind": "task-notification"}`.
                          * "cross-session-message": the literal tag
                            `<cross-session-message from="...">`; structurally
                            `origin.get("kind") == "peer"` (carries `from`/`name`/`body`).
                          * "wakeup-or-hook" — THREE sub-shapes sharing one structure (`origin`
                            None, `isMeta` true), round-2 review having added the third: a
                            scheduled wakeup (a Routine's tick — see
                            `scripts/foundry_message_kind.py`'s `TICK <programme> <UTC-stamp>`
                            convention, `promptSource == "system"`); a hook/system notification
                            (same); and a Stop hook's own feedback (real transcripts carry a
                            "Stop hook feedback: ..." body with `promptSource` ABSENT entirely,
                            not merely a different value — `.get("promptSource")` reads `None`
                            either way, so the branch matches "system" OR absent). These three are
                            structurally indistinguishable from each other under "wakeup-or-hook"
                            (and are not distinguished further), only from a real human turn.
  - `session-end`     — no further user-role record exists in the file.
Three extractor lies documented in the method doc, and what this instrument does instead:
  - `\\bBLOCK\\b` (case-insensitive) matches "Processing block 454393353" on a blockchain
    product. This instrument's DENY regex has NO bare-word "block" alternative — every member
    names a denial-shaped PHRASE (`blocked by ...`, `classifier block(ed)`, `hook (blocked|
    denied|refused)`, `permission denied`, ...).
  - ~33% of "questions" are rhetorical self-questions the agent answers itself and keeps going
    (a tool_use in the SAME assistant turn as the question text). This instrument only classifies
    an assistant turn as a question (or as a silent yield) when that turn carries NO tool_use —
    a turn that also calls a tool is a rhetorical aside, not a real handoff, and is never the one
    evaluated (the pending-assistant pointer keeps moving forward until a real stop).
  - the operator-turn discriminator must be the validated one. `is_operator_turn()` below is
    copied verbatim from `docs/micro-workflows/empirical-mining/mine_failures.py` (P/R 1.000,
    n=2,973, `mine-autonomy-2026-07-16.md`) — not re-derived.

usage:
  foundry-autonomy-instrument.py --since YYYY-MM-DD --projects-dir DIR [--projects-dir DIR ...]
                                  [--repo DIR] [--json] [--out PATH] [--sample-size N]
                                  [--with-excerpts] [--certify]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

try:
    import yaml  # pyyaml — pinned in requirements-dev.txt / requirements.txt
except ImportError:  # pragma: no cover - degrades to a hand-rolled scalar fallback below
    yaml = None

EXC = 240  # excerpt cap, matches mine_failures.py

# --------------------------------------------------------------------------------------------- #
# validated operator-turn discriminator — copied verbatim from
# docs/micro-workflows/empirical-mining/mine_failures.py (P/R 1.000; AC-INS-3)
# --------------------------------------------------------------------------------------------- #


def is_operator_turn(r):
    if r.get("type") != "user": return False
    if r.get("isSidechain"): return False
    if r.get("isMeta") or r.get("isCompactSummary"): return False
    o, ps = r.get("origin"), r.get("promptSource")
    if isinstance(o, dict): return o.get("kind") == "human"
    return o is None and ps in ("typed", "queued", "suggestion_accepted")


# --------------------------------------------------------------------------------------------- #
# certify-by-remeasure (AC-CBR-1): the three-way turn-end classifier, matched on STRUCTURAL
# envelope markers only (never on the wrapped text's prose) — see the module docstring for the
# exact markers quoted from real transcripts.
# --------------------------------------------------------------------------------------------- #


# The four sub-kinds `corpus.turn_ends.harness_resumed_by_kind` breaks down into — the three
# `classify_envelope_kind()` names, plus "other-harness" for a non-human turn end whose shape it
# does not recognize (never silently dropped; see `classify_turn_end()`).
HARNESS_KINDS = ("task-notification", "cross-session-message", "wakeup-or-hook", "other-harness")


def classify_envelope_kind(r):
    """Classifies a non-operator, user-role record's harness envelope kind, structurally.
    Returns one of "task-notification", "cross-session-message", "wakeup-or-hook", or None (a
    real human turn, or a dict-shaped `origin` not matching any documented kind — see
    `classify_turn_end()`'s "other-harness" fallback for how mine_file() still counts an
    unrecognized non-human shape, rather than silently dropping it).

    NOT exhaustive over every non-human shape by construction — only over the ones this atom
    documented from real transcripts (module docstring). "wakeup-or-hook" covers THREE observed
    sub-shapes, all sharing the same `origin is None, isMeta true` structure and differing only
    in whether `promptSource` is present: a Routine's tick (`promptSource == "system"`), a
    hook/system notification (same), and a Stop-hook's own feedback (`type: "user"`, `isMeta:
    true`, NO `promptSource` key at all — round-2 review finding, real transcripts carry a
    "Stop hook feedback: ..." body with this exact shape). `promptSource` therefore matches
    "system" OR absent, never any other value, for this branch."""
    if r.get("type") != "user":
        return None
    o = r.get("origin")
    if isinstance(o, dict):
        kind = o.get("kind")
        if kind == "task-notification":
            return "task-notification"
        if kind == "peer":
            return "cross-session-message"
        return None  # "human", or an unrecognized dict-shaped origin — never a harness envelope
    if o is None and r.get("isMeta") and r.get("promptSource") in (None, "system"):
        return "wakeup-or-hook"
    return None


def classify_turn_end(next_record):
    """AC-CBR-1: classifies a tool-free assistant turn's end by what follows it — `next_record`
    is the next user-role record in the file, or None when none remains.
      - None                                -> "session-end"
      - is_operator_turn(next_record)        -> "human-resumed"
      - anything else                        -> "harness-resumed"
    A non-human next record is demonstrably NOT the operator saying "continue"
    (`is_operator_turn` is the validated, P/R 1.000 discriminator for that), so counting it
    toward silent-yield would reintroduce the exact inflation this atom exists to remove — that
    holds regardless of which envelope shape it is. `mine_file()` calls `classify_envelope_kind()`
    SEPARATELY on every "harness-resumed" turn end to attribute a sub-kind (round-2 review: this
    function alone left `classify_envelope_kind` dead code; a shape it does not recognize is
    still counted, as "other-harness", never silently dropped)."""
    if next_record is None:
        return "session-end"
    if is_operator_turn(next_record):
        return "human-resumed"
    return "harness-resumed"


# --------------------------------------------------------------------------------------------- #
# question / denial regexes — the corrected instrument (§0's three lies avoided)
# --------------------------------------------------------------------------------------------- #

Q_TAIL = re.compile(r"\?\s*$")
Q_PHRASE = re.compile(
    r"\b(want me to|should i|do you want|would you like|which (one|option|do you)|"
    r"let me know|shall i|can you confirm|please confirm|your call|up to you|say the word)\b",
    re.I,
)

# Deliberately NO bare \bBLOCK\b member (the documented false-positive on "Processing block N"
# blockchain-sync output). Every alternative below names a denial-shaped phrase.
DENY = re.compile(
    r"(permission (for this action )?was denied|blocked by (the )?classifier|"
    r"auto[- ]mode classifier block(ed)?|classifier block(ed)?|denied by (the )?|"
    r"is not allowed|hook (blocked|denied|refused)|blocked by (the )?hook|"
    r"disabled for safety|requires approval|permission denied|EPERM|EACCES|"
    r"Read-only file system|refused by|not permitted)",
    re.I,
)


def ex(s):
    return re.sub(r"\s+", " ", s or "")[:EXC]


# --------------------------------------------------------------------------------------------- #
# redaction (round-2 review remediation, RISK: excerpts can carry secrets) — applied BEFORE ex()
# --------------------------------------------------------------------------------------------- #

_BEARER_RE = re.compile(r"(?i)\bBearer\s+\S+")
_TOKEN_PARAM_RE = re.compile(r'(?i)\btoken=[^\s&"\']+')
_AKIA_RE = re.compile(r"AKIA[0-9A-Z]{16}")
_ARN_RE = re.compile(r"arn:[a-zA-Z0-9:_\-/]+")
# generic catch-all, applied LAST so it never re-matches text an earlier, more specific
# replacement already collapsed to the short literal "[REDACTED]".
_LONG_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-/+=]{20,}")


def redact(text):
    """Replaces secret-shaped spans with the literal `[REDACTED]`. Order matters: the specific
    patterns (Bearer/token=/AKIA/arn:) run first so their replacement keeps a useful prefix
    (`Bearer [REDACTED]`, `token=[REDACTED]`); the generic >=20-char token catch-all runs last so
    it cannot re-flag a span already collapsed above."""
    if not text:
        return text
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _TOKEN_PARAM_RE.sub("token=[REDACTED]", text)
    text = _AKIA_RE.sub("[REDACTED]", text)
    text = _ARN_RE.sub("[REDACTED]", text)
    text = _LONG_TOKEN_RE.sub("[REDACTED]", text)
    return text


# --------------------------------------------------------------------------------------------- #
# transcript record helpers
# --------------------------------------------------------------------------------------------- #


def blocks(r):
    m = r.get("message") or {}
    c = m.get("content")
    if isinstance(c, str):
        return [{"type": "text", "text": c}]
    return c if isinstance(c, list) else []


def text_of(r):
    return " ".join(b.get("text", "") for b in blocks(r) if b.get("type") == "text").strip()


def tool_uses(r):
    return [b for b in blocks(r) if b.get("type") == "tool_use"]


def tool_results(r):
    """Yields (tool_use_id, is_error, text) for every tool_result block in a user-type record."""
    out = []
    for b in blocks(r):
        if b.get("type") != "tool_result":
            continue
        c = b.get("content")
        if isinstance(c, list):
            c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
        out.append((b.get("tool_use_id"), bool(b.get("is_error")), (c or "")[:2000]))
    return out


def record_ts(r):
    t = r.get("timestamp")
    if not t:
        return None
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def read_jsonl(path):
    recs = []
    try:
        with open(path, errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    recs.append(json.loads(line))
                except Exception:
                    pass
    except OSError:
        pass
    return recs


# --------------------------------------------------------------------------------------------- #
# per-file mining: stops (silent-yield/directive-reply input) + denials
# --------------------------------------------------------------------------------------------- #


def _message_id(r):
    return (r.get("message") or {}).get("id")


def _stop_reason(r):
    return (r.get("message") or {}).get("stop_reason")


def _group_logical_assistant_turns(filtered):
    """AC-CBR-1 round-2 review (extended-thinking phantom stop): Claude Code writes one logical
    assistant turn with extended thinking as MULTIPLE consecutive `type: "assistant"` JSONL
    records sharing one `message.id` — a thinking-only fragment (content=[thinking],
    `stop_reason: "tool_use"`) followed by a tool_use fragment (content=[tool_use], the SAME
    `message.id`). Evaluating each raw record as its own turn-end candidate (the pre-fix design)
    let the thinking-only fragment pass `tool_uses(r)` as empty and read as a tool-free "stop"
    whose immediately-following record is the tool_result — misclassified as harness-resumed
    (`other-harness`) at best, or as a phantom SILENT human-resumed stop when the REAL turn
    happened to end in a question (the thinking fragment carries no question, so `is_q` reads
    False) — inflating BOTH `other-harness` and `silent_yield` on every extended-thinking turn.

    Yields `(fragments, last_j)` for each logical assistant turn: `fragments` is the list of
    raw records to merge (tool_uses/text/stop_reason unioned across all of them — see
    `_evaluate_logical_turn()`); `last_j` is the LAST fragment's position in `filtered` (the
    forward scan for `next_record` resumes from `last_j + 1`, never from the thinking-only
    fragment's own position). Consecutive `type: "assistant"` records are merged into ONE group
    ONLY when they share the same NON-None `message.id`; a record with no `message.id` (older
    transcripts, and every pre-existing fixture in this suite) is always its own singleton group
    — this keeps every fixture predating this fix byte-for-byte unaffected."""
    fragments = []
    current_mid = None
    last_j = None
    for j, (_i, r) in enumerate(filtered):
        if r.get("type") != "assistant":
            if fragments:
                yield fragments, last_j
                fragments = []
                current_mid = None
            continue
        mid = _message_id(r)
        if fragments and mid is not None and mid == current_mid:
            fragments.append(r)
            last_j = j
        else:
            if fragments:
                yield fragments, last_j
            fragments = [r]
            current_mid = mid
            last_j = j
    if fragments:
        yield fragments, last_j


def _logical_turn_has_tool_use(fragments):
    """True iff ANY fragment of a logical assistant turn carries a tool_use content block OR a
    `stop_reason: "tool_use"` (the latter consulted per round-2 review as a redundant signal — a
    real transcript's thinking-only fragment already carries `stop_reason: "tool_use"` even
    before its sibling tool_use fragment arrives, so this alone is enough to exclude it without
    needing the merge to have completed)."""
    for r in fragments:
        if tool_uses(r):
            return True
        if _stop_reason(r) == "tool_use":
            return True
    return False


def mine_file(path):
    """Returns {"stops": [{"silent": bool, "reply_len": int}], "denials": [...],
    "harness_resumed_count": int, "harness_resumed_by_kind": {kind: int, ...}, "session_end_count":
    int}. A "stop" (silent-yield/directive-reply input) is a tool-free LOGICAL assistant turn end
    (see `_group_logical_assistant_turns()` — extended-thinking's multi-fragment turns are merged
    first) classified `human-resumed` by `classify_turn_end()` (AC-CBR-1) — the immediately
    following user-role record is a genuine operator turn. A tool-free turn end classified
    `harness-resumed` or `session-end` is counted but never added to `stops` — it never was a real
    handoff to a human, so it must not inflate silent-yield's denominator (the R3-boundary blind
    spot this atom removes). Every `harness-resumed` turn end is ALSO attributed a sub-kind via
    `classify_envelope_kind()` (round-2 review: `classify_turn_end()` alone left that function
    dead code) — one of `HARNESS_KINDS`, "other-harness" covering a non-human shape it does not
    recognize, never silently dropped. A logical turn that DOES carry a tool_use (in any fragment)
    is never evaluated as a turn end at all — it is a rhetorical aside or in-flight work, not a
    handoff (avoids the "self-answered question" lie AND, per this round of review, the
    extended-thinking phantom-stop lie).

    Each denial's "text" is the RAW (2000-char-capped) tool_result body — NOT yet redacted or
    excerpt-truncated. Redaction + `ex()` truncation happen at report-assembly time, gated by
    `--with-excerpts` (round-2 review remediation), so a denial mined here can still be counted
    (and its command compared for granted-verb-denial) even when excerpts are never surfaced.
    `record_index` is the 0-based offset of the tool_result record within this file — a
    content-free "turn reference" a human can use to open the raw transcript themselves."""
    recs = read_jsonl(path)
    filtered = [(i, r) for i, r in enumerate(recs) if not r.get("isSidechain")]
    n = len(filtered)
    tool_use_by_id = {}
    stops = []
    denials = []
    harness_resumed_count = 0
    harness_resumed_by_kind = {k: 0 for k in HARNESS_KINDS}
    session_end_count = 0
    fname = os.path.basename(path)

    # Pass 1: register every tool_use block's id -> (tool_name, input), across ALL assistant
    # fragments, regardless of grouping — a later tool_result always names the id of whichever
    # fragment actually emitted it, so this must see every fragment, not just group heads.
    for _j, (_i, r) in enumerate(filtered):
        if r.get("type") != "assistant":
            continue
        for tu in tool_uses(r):
            tid = tu.get("id") or tu.get("tool_use_id")
            if tid:
                tool_use_by_id[tid] = (tu.get("name", ""), tu.get("input") or {})

    # Pass 2: denials (independent of assistant-turn grouping — every "user" record's tool_result
    # blocks are mined exactly as before).
    for i, r in enumerate(recs):
        if r.get("isSidechain") or r.get("type") != "user":
            continue
        for tid, _is_err, txt in tool_results(r):
            if DENY.search(txt):
                name, inp = tool_use_by_id.get(tid, ("", {}))
                cmd = (inp or {}).get("command") if name == "Bash" else None
                denials.append({
                    "file": fname,
                    "record_index": i,
                    "text": txt,
                    "tool_name": name,
                    "verb": verb_of(name, inp) if name else None,
                    "command": cmd,
                })

    # Pass 3: logical-turn-end classification (AC-CBR-1 + the extended-thinking merge above).
    for fragments, last_j in _group_logical_assistant_turns(filtered):
        if _logical_turn_has_tool_use(fragments):
            continue  # a turn WITH a tool call (in any fragment) is never a turn end
        next_record = None
        for k in range(last_j + 1, n):
            _, rk = filtered[k]
            if rk.get("type") == "user":
                next_record = rk
                break
        verdict = classify_turn_end(next_record)
        if verdict == "human-resumed":
            merged_text = " ".join(t for t in (text_of(frag) for frag in fragments) if t)
            tail = merged_text[-400:]
            is_q = bool(Q_TAIL.search(tail)) or bool(Q_PHRASE.search(tail))
            reply_len = len(text_of(next_record).strip())
            stops.append({"silent": not is_q, "reply_len": reply_len})
        elif verdict == "harness-resumed":
            harness_resumed_count += 1
            subkind = classify_envelope_kind(next_record) or "other-harness"
            harness_resumed_by_kind[subkind] = harness_resumed_by_kind.get(subkind, 0) + 1
        else:
            session_end_count += 1

    return {
        "stops": stops,
        "denials": denials,
        "harness_resumed_count": harness_resumed_count,
        "harness_resumed_by_kind": harness_resumed_by_kind,
        "session_end_count": session_end_count,
    }


def iter_jsonl_files(root, since_ts):
    """Walk root for *.jsonl main-session transcripts, mtime-filtered by since_ts (matches
    mine_failures.py's method). Subagent transcripts (path contains /subagents/) are skipped —
    counted, not mined, per the method doc."""
    if not os.path.isdir(root):
        return
    for dirpath, _dirnames, filenames in os.walk(root):
        if "/subagents/" in (dirpath + "/"):
            continue
        for fn in filenames:
            if not fn.endswith(".jsonl"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                if since_ts is not None and os.path.getmtime(path) < since_ts:
                    continue
            except OSError:
                continue
            yield path


# --------------------------------------------------------------------------------------------- #
# granted-verb-denial: is the denied command already covered by a settings allow rule?
# (round-2 review remediation: LONGEST-PREFIX match on the full command, never a first-token
# "verb" match — see is_granted()/module docstring point 3)
# --------------------------------------------------------------------------------------------- #


def verb_of(tool_name, tool_input):
    if tool_name == "Bash":
        cmd = (tool_input or {}).get("command", "") or ""
        cmd = cmd.strip()
        return cmd.split()[0] if cmd else None
    return tool_name


_RULE_HEAD_RE = re.compile(r"^([A-Za-z0-9_]+)\((.*)\)$")


def load_allow_rules(repo_dir):
    """Union of permissions.allow from .claude/settings.json + .claude/settings.local.json under
    repo_dir. Best-effort: a missing or malformed file contributes nothing."""
    rules = []
    for relpath in (".claude/settings.json", ".claude/settings.local.json"):
        path = os.path.join(repo_dir, relpath)
        if not os.path.isfile(path):
            continue
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        allow = ((data.get("permissions") or {}).get("allow")) or []
        if isinstance(allow, list):
            rules.extend(str(a) for a in allow)
    return rules


def _bash_allow_prefixes(allow_rules):
    """Every Bash(...) allow rule's command PREFIX (the `:*`-wildcarded body with the wildcard
    stripped, or the literal body for an exact-match rule)."""
    prefixes = []
    for rule in allow_rules:
        m = _RULE_HEAD_RE.match(rule.strip())
        if not m or m.group(1) != "Bash":
            continue
        body = m.group(2).strip()
        if not body:
            continue
        if body.endswith(":*"):
            prefixes.append(body[:-2].strip())
        else:
            prefixes.append(body)
    return prefixes


def _bash_command_granted(cmd, allow_rules):
    """True iff `cmd` is covered by the LONGEST matching prefix among the workspace's
    `Bash(<prefix>:*)` allow rules. A rule matches only when `cmd` equals the prefix exactly or
    starts with `prefix + " "` — a rule body is never compared by its first token alone, which is
    the round-2 review fix: `Bash(git commit:*)` must NOT be read as granting `git push ...`."""
    cmd = (cmd or "").strip()
    if not cmd:
        return False
    best_len = -1
    for prefix in _bash_allow_prefixes(allow_rules):
        if cmd == prefix or cmd.startswith(prefix + " "):
            best_len = max(best_len, len(prefix))
    return best_len >= 0


def is_granted(tool_name, command, allow_rules):
    """True iff the denied (tool_name, command) is already covered by the settings allow rules.
    Bash commands use the longest-prefix match (`_bash_command_granted`); every other tool is an
    exact tool-name match (`Read`, `Write`, ... — these carry no command body to sub-match)."""
    if tool_name == "Bash":
        return _bash_command_granted(command, allow_rules)
    if not tool_name:
        return False
    for rule in allow_rules:
        m = _RULE_HEAD_RE.match(rule.strip())
        if not m:
            if rule.strip() == tool_name:
                return True
            continue
        head = m.group(1)
        if head == tool_name:
            return True
    return False


# --------------------------------------------------------------------------------------------- #
# authorized->built conversion + rounds-per-shipped-atom (--repo, optional)
# --------------------------------------------------------------------------------------------- #

_PLACEHOLDER_SHA_RE = re.compile(r"^0+$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")

_SKIP_DIRS = {".git", "node_modules", ".worktrees"}


def _walk_repo(repo_dir):
    for dirpath, dirnames, filenames in os.walk(repo_dir):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        yield dirpath, dirnames, filenames


def _load_yaml_line_scalar_fallback(path, key):
    """Hand-rolled fallback when pyyaml is unavailable: finds a top-level `key: value` line and
    returns its (quote-stripped) value verbatim."""
    pat = re.compile(r"^" + re.escape(key) + r':\s*"?([^"\n]+?)"?\s*$')
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                m = pat.match(line.strip())
                if m:
                    return m.group(1)
    except OSError:
        pass
    return None


def scan_specs_authorized_refs(repo_dir):
    """Set of spec_ref values from specs/**/acceptance-contract.yaml carrying a real
    (non-placeholder, non-all-zero) spec_sha256 — the front-authorization corpus, keyed by
    spec_ref so authorized_to_built can intersect against the built side (round-2 review fix)."""
    specs_dir = os.path.join(repo_dir, "specs")
    refs = set()
    if not os.path.isdir(specs_dir):
        return refs
    for dirpath, _dirnames, filenames in _walk_repo(specs_dir):
        if "acceptance-contract.yaml" not in filenames:
            continue
        path = os.path.join(dirpath, "acceptance-contract.yaml")
        sha = None
        spec_ref = None
        if yaml is not None:
            try:
                data = yaml.safe_load(open(path, encoding="utf-8")) or {}
                sha = data.get("spec_sha256")
                spec_ref = data.get("spec_ref")
            except Exception:
                sha = None
                spec_ref = None
        if sha is None:
            sha = _load_yaml_line_scalar_fallback(path, "spec_sha256")
        if spec_ref is None:
            spec_ref = _load_yaml_line_scalar_fallback(path, "spec_ref")
        if spec_ref and sha and _SHA_RE.match(sha) and not _PLACEHOLDER_SHA_RE.match(sha):
            refs.add(spec_ref)
    return refs


def scan_specs_authorized(repo_dir):
    """Count form of scan_specs_authorized_refs(), kept for callers that only need the count."""
    return len(scan_specs_authorized_refs(repo_dir))


def scan_provenance_spec_refs(repo_dir):
    """Unique spec_ref values recorded across every .foundry/build-provenance.yaml under
    repo_dir — the "built" side of the authorized->built conversion."""
    spec_refs = set()
    for dirpath, _dirnames, filenames in _walk_repo(repo_dir):
        if os.path.basename(dirpath) != ".foundry" or "build-provenance.yaml" not in filenames:
            continue
        path = os.path.join(dirpath, "build-provenance.yaml")
        data = None
        if yaml is not None:
            try:
                data = yaml.safe_load(open(path, encoding="utf-8")) or {}
            except Exception:
                data = None
        if data is None:
            continue
        for a in (data.get("authorizations") or []):
            sr = (a or {}).get("spec_ref")
            if sr:
                spec_refs.add(sr)
    return spec_refs


def scan_audit_rounds(repo_dir):
    """Sum of the `rounds` field across every .foundry/audit-ledger.jsonl row under repo_dir
    (v1 legacy rows and v2 rows both carry `rounds`)."""
    total = 0
    for dirpath, _dirnames, filenames in _walk_repo(repo_dir):
        if os.path.basename(dirpath) != ".foundry" or "audit-ledger.jsonl" not in filenames:
            continue
        path = os.path.join(dirpath, "audit-ledger.jsonl")
        for row in read_jsonl(path):
            r = row.get("rounds")
            if isinstance(r, int):
                total += r
    return total


# --------------------------------------------------------------------------------------------- #
# --out path confinement (round-2 review remediation, RISK: --out path traversal)
# --------------------------------------------------------------------------------------------- #


class OutPathError(Exception):
    """Raised when --out resolves outside the allowed roots (cwd or --repo)."""


def validate_out_path(out_path, repo):
    """Resolves `out_path` with os.path.realpath (following symlinks, to defeat a
    symlink-assisted escape at the CONTAINMENT check) and refuses it unless the resolved path is
    the current working directory / --repo root, or falls under one of them. Raises
    OutPathError with a clear message on refusal; the caller decides how to exit."""
    cwd_real = os.path.realpath(os.getcwd())
    roots = [cwd_real]
    if repo:
        roots.append(os.path.realpath(repo))
    target_real = os.path.realpath(out_path)
    for root in roots:
        if target_real == root or target_real.startswith(root + os.sep):
            return target_real
    where = "the current working directory or --repo" if repo else "the current working directory"
    raise OutPathError(
        f"--out {out_path!r} resolves to {target_real!r}, which is outside {where}; refusing to write"
    )


def write_out_report(out_path, report):
    """Writes `report` to `out_path`, refusing to FOLLOW a symlink at the final write step (the
    containment check above already resolved symlinks to validate the target; this step
    additionally guards the TOCTOU window between that check and the actual write)."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(out_path, flags, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)


# --------------------------------------------------------------------------------------------- #
# ratio assembly
# --------------------------------------------------------------------------------------------- #


def _ratio(num, den):
    return {"numerator": num, "denominator": den, "ratio": (round(num / den, 4) if den else None)}


def build_report(since_str, projects_dirs, repo, sample_size=40, with_excerpts=False):
    since_ts = None
    if since_str:
        since_ts = datetime.strptime(since_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()

    all_stops = []
    all_denials = []
    files_scanned = 0
    harness_resumed_count = 0
    harness_resumed_by_kind = {k: 0 for k in HARNESS_KINDS}
    session_end_count = 0
    for pd in projects_dirs:
        for path in iter_jsonl_files(pd, since_ts):
            files_scanned += 1
            result = mine_file(path)
            all_stops.extend(result["stops"])
            all_denials.extend(result["denials"])
            harness_resumed_count += result["harness_resumed_count"]
            for k, v in result["harness_resumed_by_kind"].items():
                harness_resumed_by_kind[k] = harness_resumed_by_kind.get(k, 0) + v
            session_end_count += result["session_end_count"]

    # AC-CBR-1: silent_yield (and directive_reply) are computed over `all_stops` only, which by
    # construction (mine_file) already holds `human-resumed` turn ends exclusively — a
    # `harness-resumed` or `session-end` turn end is counted (below, in `corpus.turn_ends`) but
    # never added to `all_stops`, so it never reaches these ratios' denominators.
    silent_yield = _ratio(sum(1 for s in all_stops if s["silent"]), len(all_stops))
    directive_reply = _ratio(sum(1 for s in all_stops if s["reply_len"] <= 45), len(all_stops))

    allow_rules = load_allow_rules(repo) if repo else []
    granted_hits = [
        d for d in all_denials if is_granted(d.get("tool_name"), d.get("command"), allow_rules)
    ]
    granted_verb_denial = _ratio(len(granted_hits), len(all_denials))

    raw_sample = all_denials[: max(0, sample_size)]
    sample = []
    for d in raw_sample:
        entry = {
            "file": d["file"],
            "record_index": d["record_index"],
            "tool_name": d.get("tool_name"),
            "verb": d.get("verb"),
        }
        if with_excerpts:
            entry["text"] = ex(redact(d.get("text", "")))
        sample.append(entry)
    guard_false_positive_sample = {
        "numerator": None,
        "denominator": len(sample),
        "ratio": None,
        "denials_total": len(all_denials),
        "with_excerpts": with_excerpts,
        "sample": sample,
    }

    if repo:
        built_refs = scan_provenance_spec_refs(repo)
        authorized_refs = scan_specs_authorized_refs(repo)
        rounds_total = scan_audit_rounds(repo)
    else:
        built_refs, authorized_refs, rounds_total = set(), set(), 0

    authorized_built_intersection = built_refs & authorized_refs
    authorized_to_built = _ratio(len(authorized_built_intersection), len(authorized_refs))
    rounds_per_shipped_atom = _ratio(rounds_total, len(built_refs))

    return {
        "corpus": {
            "since": since_str,
            "until": datetime.now(timezone.utc).isoformat(),
            "projects_dirs": list(projects_dirs),
            "repo": repo,
            "files_scanned": files_scanned,
            "stops_found": len(all_stops),
            "denials_found": len(all_denials),
            # AC-CBR-1: the three-way turn-end classification counts, alongside silent_yield's
            # ratio (whose denominator is "human_resumed" exactly — see build_report() above).
            "turn_ends": {
                "human_resumed": len(all_stops),
                "harness_resumed": harness_resumed_count,
                # round-2 review: the sub-kind breakdown from classify_envelope_kind() — proves
                # that function is actually consulted, not dead code (see mine_file()).
                "harness_resumed_by_kind": harness_resumed_by_kind,
                "session_end": session_end_count,
            },
        },
        "ratios": {
            "silent_yield": silent_yield,
            "directive_reply": directive_reply,
            "granted_verb_denial": granted_verb_denial,
            "guard_false_positive_sample": guard_false_positive_sample,
            "authorized_to_built": authorized_to_built,
            "rounds_per_shipped_atom": rounds_per_shipped_atom,
        },
    }


def render_text(report):
    lines = []
    c = report["corpus"]
    lines.append(f"corpus: since={c['since']} until={c['until']} files_scanned={c['files_scanned']}")
    lines.append(f"        projects_dirs={c['projects_dirs']} repo={c['repo']}")
    te = c.get("turn_ends") or {}
    lines.append(
        f"        turn_ends: human_resumed={te.get('human_resumed')} "
        f"harness_resumed={te.get('harness_resumed')} session_end={te.get('session_end')}"
    )
    by_kind = te.get("harness_resumed_by_kind") or {}
    if by_kind:
        breakdown = " ".join(f"{k}={v}" for k, v in by_kind.items())
        lines.append(f"        harness_resumed_by_kind: {breakdown}")
    for name, r in report["ratios"].items():
        if name == "guard_false_positive_sample":
            lines.append(
                f"{name}: SAMPLED {r['denominator']} of {r['denials_total']} denials — "
                f"not auto-classified, excerpts={'on' if r['with_excerpts'] else 'off (--with-excerpts to include, redacted)'}, "
                f"see 'sample' in --json output"
            )
            continue
        lines.append(f"{name}: {r['ratio']} ({r['numerator']}/{r['denominator']})")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------- #
# certify-by-remeasure (AC-CBR-2): --certify compares the computed ratios against the programme's
# thresholds and prints a verdict line. REPORT ONLY, same as every other ratio here: the verdict
# is a printed line, never a gate — main() always returns 0 regardless of PASS/OPEN.
# --------------------------------------------------------------------------------------------- #

_SHIPPED_THRESHOLDS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "context", "autonomy-thresholds.yaml"
)
# The workspace copy, resolved under $CLAUDE_PROJECT_DIR (falling back to cwd), OVERRIDES the
# shipped default when present — same "workspace corpus via CLAUDE_PROJECT_DIR, plugin's own
# files via the plugin root" split every other Foundry script reads through.
_WORKSPACE_THRESHOLDS_RELPATH = os.path.join("docs", "programs", "autonomy-continuation", "thresholds.yaml")


def thresholds_path(workspace_dir=None):
    """Resolves which thresholds file `--certify` reads: the workspace copy at
    `docs/programs/autonomy-continuation/thresholds.yaml` under `workspace_dir` (default
    `$CLAUDE_PROJECT_DIR`, else cwd) when it exists, else the plugin's shipped
    `context/autonomy-thresholds.yaml` (AC-CBR-2)."""
    base = workspace_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    override = os.path.join(base, _WORKSPACE_THRESHOLDS_RELPATH)
    if os.path.isfile(override):
        return override
    return _SHIPPED_THRESHOLDS_PATH


_THRESHOLD_MAX_RE = re.compile(r'^([A-Za-z0-9_]+):\s*$')
_THRESHOLD_FIELD_RE = re.compile(r'^\s+(max|sampled):\s*(\S+)\s*$')


def _load_thresholds_fallback(path):
    """Hand-rolled fallback for the simple two-level `<name>:\\n  max: <float>` /
    `<name>:\\n  sampled: true` shape, used only when pyyaml is unavailable (mirrors the
    module's existing scalar-fallback style for acceptance-contract.yaml)."""
    out = {}
    current = None
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                m = _THRESHOLD_MAX_RE.match(line.rstrip("\n"))
                if m:
                    current = m.group(1)
                    out[current] = {}
                    continue
                m = _THRESHOLD_FIELD_RE.match(line.rstrip("\n"))
                if m and current:
                    key, val = m.group(1), m.group(2)
                    if key == "max":
                        try:
                            out[current]["max"] = float(val)
                        except ValueError:
                            pass
                    elif key == "sampled":
                        out[current]["sampled"] = val.strip().lower() == "true"
    except OSError:
        pass
    return out


# The four ratios PROGRAM.md §7 actually names as the programme's exit criteria. `certify()`
# treats an ABSENT entry for one of these as a misconfiguration (round-2 review: a thresholds
# file silently missing one of these must never read as an automatic pass) — distinct from a
# ratio with no programme exit criterion at all (authorized_to_built, rounds_per_shipped_atom),
# which stays uncompared when absent, exactly as before.
PROGRAMME_RATIOS = ("silent_yield", "directive_reply", "granted_verb_denial", "guard_false_positive_sample")


class ThresholdsError(Exception):
    """Raised by `load_thresholds_strict()` when the resolved thresholds file EXISTS but cannot
    be parsed as the expected shape (round-2 review: `load_thresholds()`'s silent `{}` fallback
    let a malformed file print a bare CERTIFY-PASS — see `load_thresholds_strict()`'s
    docstring)."""


def load_thresholds(path=None):
    """Returns {ratio_name: {"max": float}} or {ratio_name: {"sampled": True}}. A ratio absent
    from the thresholds file is never compared by `certify()` UNLESS it is one of
    `PROGRAMME_RATIOS` (round-2 review: those four are never silently skipped — see `certify()`).
    Missing/malformed input degrades to `{}` here (the LENIENT loader — direct callers that
    already have a trusted dict, and every pre-existing test, keep this exact shape); the CLI's
    `--certify` path uses `load_thresholds_strict()` instead, which raises `ThresholdsError`
    distinguishably on a malformed file rather than degrading silently."""
    path = path or thresholds_path()
    if not os.path.isfile(path):
        return {}
    if yaml is not None:
        try:
            data = yaml.safe_load(open(path, encoding="utf-8")) or {}
            return data.get("thresholds") or {}
        except Exception:
            return {}
    return _load_thresholds_fallback(path)


def load_thresholds_strict(path=None):
    """Like `load_thresholds()`, but RAISES `ThresholdsError` (naming the exact path + reason)
    instead of silently degrading to `{}` when the resolved file EXISTS but is malformed —
    invalid YAML syntax, a non-mapping top level, or a `thresholds:` value that is not itself a
    mapping. A file that does not exist at all is NOT an error (returns `{}`): `thresholds_path()`
    already resolves to the shipped default whenever no workspace override exists, so a missing
    file at the resolved path is a normal, expected state, never a parse failure. Used by the CLI
    `--certify` path (AC-CBR-2 round-2 review) so a malformed thresholds file is reported as
    `CERTIFY-OPEN: thresholds unreadable — ...` rather than a misleadingly bare CERTIFY-PASS."""
    path = path or thresholds_path()
    if not os.path.isfile(path):
        return {}
    if yaml is None:
        return _load_thresholds_fallback(path)  # best-effort; this shape tolerates malformed lines
    try:
        data = yaml.safe_load(open(path, encoding="utf-8"))
    except Exception as e:
        raise ThresholdsError(f"{path}: {e}") from e
    data = data or {}
    if not isinstance(data, dict):
        raise ThresholdsError(f"{path}: expected a mapping at the top level, got {type(data).__name__}")
    thresholds = data.get("thresholds")
    if thresholds is None:
        return {}
    if not isinstance(thresholds, dict):
        raise ThresholdsError(f"{path}: 'thresholds' must be a mapping, got {type(thresholds).__name__}")
    return thresholds


def certify(report, thresholds):
    """Compares `report["ratios"]` against `thresholds` (as returned by `load_thresholds()` /
    `load_thresholds_strict()`). Returns a list of miss strings — empty means CERTIFY-PASS:
      - `"<ratio> <value> vs <threshold>"` — a `max:` ceiling was exceeded.
      - `"<ratio> N denials sampled, unclassified (...) vs 0"` — a `sampled: true` entry with a
        nonzero `denials_total` (guard_false_positive_sample).
      - `"<ratio> no threshold configured"` — one of `PROGRAMME_RATIOS` has NO entry in
        `thresholds` at all (round-2 review: a thresholds file silently missing one of the
        programme's four named exit criteria must show up as an open item, never an automatic
        pass by omission).

    A ratio with `ratio: None` (nothing was measured — an empty corpus, or --repo omitted) is
    treated as trivially within threshold: an unmeasured ratio cannot MISS a "stay under X"
    ceiling, and is never reported as "no threshold configured" either (that miss is about the
    THRESHOLDS FILE being incomplete, not about the corpus). `guard_false_positive_sample` is
    SAMPLED, never auto-classified (see the module docstring) — its `ratio` field is always None
    regardless of corpus size, so it is compared on `denials_total` instead: zero denials
    trivially certifies (nothing to have been a false positive); any nonzero count stays a miss
    (the operator's own classification of the sample is the only way to close it — never
    auto-PASS on an unclassified sample)."""
    thresholds = thresholds or {}
    misses = []
    names = list(PROGRAMME_RATIOS) + [n for n in thresholds if n not in PROGRAMME_RATIOS]
    for name in names:
        r = (report.get("ratios") or {}).get(name)
        if r is None:
            continue  # this ratio was never computed in this report at all — nothing to certify
        spec = thresholds.get(name)
        if spec is None:
            misses.append(f"{name} no threshold configured")
            continue
        if spec.get("sampled"):
            denials_total = r.get("denials_total") or 0
            if denials_total:
                misses.append(
                    f"{name} {denials_total} denials sampled, unclassified (needs the operator's "
                    f"manual review of the sample) vs 0"
                )
            continue
        max_allowed = spec.get("max")
        if max_allowed is None:
            misses.append(f"{name} no threshold configured")  # spec present but has neither key
            continue
        ratio = r.get("ratio")
        if ratio is None:
            continue
        if ratio > max_allowed:
            misses.append(f"{name} {ratio} vs {max_allowed}")
    return misses


def render_certify_verdict(misses):
    if not misses:
        return "CERTIFY-PASS"
    return "CERTIFY-OPEN: " + ", ".join(misses)


# --------------------------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------------------------- #


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="foundry-autonomy-instrument.py",
        description=(
            "Report-only autonomy instrument: six ratios (silent-yield, directive-reply, "
            "granted-verb-denial, guard-false-positive-sample, authorized->built conversion, "
            "rounds-per-shipped-atom) over Claude Code transcripts + the authorization corpus, "
            "plus an optional --certify verdict against the programme's thresholds. "
            "Gates nothing; always exits 0 (a refused --out path is the one usage-error exception)."
        ),
    )
    p.add_argument("--since", required=True, metavar="YYYY-MM-DD", help="corpus window start date")
    p.add_argument(
        "--projects-dir",
        action="append",
        default=[],
        dest="projects_dirs",
        metavar="DIR",
        help="directory tree of Claude Code transcripts (*.jsonl); repeatable",
    )
    p.add_argument(
        "--repo",
        default=None,
        metavar="DIR",
        help="workspace/repo root for the corpus-side ratios (authorized->built, rounds-per-shipped-atom, "
        "and the settings allow-rules used by granted-verb-denial); optional",
    )
    p.add_argument("--json", action="store_true", help="emit one JSON object instead of text lines")
    p.add_argument(
        "--out",
        default=None,
        metavar="PATH",
        help="also write the JSON report to PATH; refused if it resolves outside the cwd or --repo",
    )
    p.add_argument(
        "--sample-size",
        type=int,
        default=40,
        help="max denials in the guard-false-positive sample (default 40)",
    )
    p.add_argument(
        "--with-excerpts",
        action="store_true",
        help="include a redacted text excerpt per sampled denial (default off: counts + file/turn "
        "references only, since excerpts can carry secrets)",
    )
    p.add_argument(
        "--certify",
        action="store_true",
        help="compare the computed ratios against the programme thresholds (context/"
        "autonomy-thresholds.yaml, overridden by the workspace copy when present) and print "
        "CERTIFY-PASS / CERTIFY-OPEN; report-only, always exits 0 either way",
    )
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    if args.out:
        try:
            validate_out_path(args.out, args.repo)
        except OutPathError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2

    report = build_report(
        args.since, args.projects_dirs, args.repo,
        sample_size=args.sample_size, with_excerpts=args.with_excerpts,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=False))
    else:
        print(render_text(report))

    if args.certify:
        try:
            thresholds = load_thresholds_strict()
        except ThresholdsError as e:
            # round-2 review: a malformed thresholds file must never print a bare CERTIFY-PASS.
            print(f"CERTIFY-OPEN: thresholds unreadable — {e}")
        else:
            misses = certify(report, thresholds)
            print(render_certify_verdict(misses))  # AC-CBR-2: a printed line, never a gate

    if args.out:
        try:
            write_out_report(args.out, report)
        except OSError as e:
            print(f"error: could not write --out {args.out!r}: {e}", file=sys.stderr)
            return 2

    return 0  # AC-INS-5: always exit 0 on any COMPUTED result; this instrument gates nothing


if __name__ == "__main__":
    sys.exit(main())
