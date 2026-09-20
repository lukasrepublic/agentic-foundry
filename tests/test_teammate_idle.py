"""tests/test_teammate_idle.py — the `teammate-idle-continue` charter (autonomy-continuation R3,
AC-TIC-1..4).

Drives `hooks/foundry-teammate-idle.py` (`run()` directly for fixture-tree scenarios, plus the
real CLI over a subprocess once for the stdin-wiring path) against throwaway `tmp_path` fixture
trees — never the real `.foundry/` state or the real `~/.claude/tasks/` corpus. Fixture payloads
live in `tests/fixtures/teammate-idle/`.

Registration clarification (build note, not a spec change): the two sibling floor hooks
(`hooks/foundry-task-created.py`, `hooks/foundry-task-completed.py`) needed NO entry in
`cli/permission-floor.json` / `docs/permission-floor.json` themselves — only their SHARED LIBRARY
(`scripts/foundry_floor_hooks.py`) got a `not_invoked` row there, because a hook entry point is
invoked by the platform via `hooks/hooks.json`, never by the agent's Bash tool, which is the
surface `permission-floor.json` polices. This hook reuses that same shared library with NO new
`scripts/*.py` file, so — following that precedent exactly — it needs no `not_invoked` row and no
digest re-pin either; `test_permission_floor_files_still_parse_and_are_unchanged_by_this_atom`
below is the checkpoint that keeps that claim honest rather than asserted in prose alone.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone

import pytest
import yaml

from conftest import REPO_ROOT, load_module

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "teammate-idle")

# `hooks/` never carried a `.py` file before feat-foundry-authorization-floor-hooks landed, so
# `hooks/__pycache__` never existed either — suppress bytecode-cache creation on import exactly
# like tests/test_floor_hooks.py already does, rather than teaching the permission-floor-map
# suite about a bytecode cache under hooks/.
_prev_dont_write_bytecode = sys.dont_write_bytecode
sys.dont_write_bytecode = True
try:
    tic = load_module("hooks/foundry-teammate-idle.py", "foundry_teammate_idle")
finally:
    sys.dont_write_bytecode = _prev_dont_write_bytecode

# NEVER `load_module("scripts/foundry_floor_hooks.py", "foundry_floor_hooks")` here, and never
# cache its result in a MODULE-LEVEL name either: that helper always registers a FRESH module
# object under `sys.modules["foundry_floor_hooks"]`, `tests/test_floor_hooks.py` does the exact
# same thing under the exact same name, and pytest collects files in COMMAND-LINE order, not
# always alphabetical — so whichever of the two files collects LAST re-registers the name out
# from under a module-level variable captured earlier (reproduced while wiring this suite: a
# `monkeypatch.setattr` on a collection-time-captured `ffh` silently stopped affecting the hook at
# all, depending on argument order on the pytest command line). The one call site below that needs
# the live module (`test_internal_error_still_exits_0_and_records`) instead calls
# `tic._import_ffh()` INSIDE the test function, at TEST-EXECUTION time (after every file's
# collection-time registration has already settled) — the same bare `import foundry_floor_hooks
# as ffh` the hook itself runs at call time, so it is always the object `run()` will actually use.


# ================================================================================================ #
# fixture helpers
# ================================================================================================ #


def _load_fixture(name):
    path = os.path.join(FIXTURES, name)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _write_release(project_dir, release_id, atoms, state="active"):
    rel_dir = os.path.join(project_dir, ".foundry", "releases", release_id)
    os.makedirs(rel_dir, exist_ok=True)
    doc = {"id": release_id, "description": "fixture release", "state": state, "atoms": atoms}
    with open(os.path.join(rel_dir, "release.yaml"), "w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False)


def _write_charter(project_dir, rel_charter_path, done_when):
    path = os.path.join(project_dir, rel_charter_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = ["# Fixture charter\n", "\n", "## Done when\n"] + [f"- {b}\n" for b in done_when]
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)


def _soon(offset_seconds=5):
    return (datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_evidence(project_dir, atom_id, rows):
    ev_dir = os.path.join(project_dir, ".foundry", "evidence")
    os.makedirs(ev_dir, exist_ok=True)
    doc = {"atom": atom_id, "done_when": rows, "recorded_by": "test-agent", "at": _soon()}
    with open(os.path.join(ev_dir, f"{atom_id}.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


def _met_row(locator):
    return {"locator": locator, "status": "met", "evidence": "ok", "at": _soon()}


def _unmet_row(locator):
    return {"locator": locator, "status": "unmet", "evidence": "still red", "at": _soon()}


def _write_task(tasks_dir, task_id, *, subject=None, description="", status="in_progress"):
    os.makedirs(tasks_dir, exist_ok=True)
    doc = {
        "id": task_id, "subject": subject or "", "description": description,
        "activeForm": "Working", "status": status, "blocks": [], "blockedBy": [],
    }
    with open(os.path.join(tasks_dir, f"{task_id}.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


def _read_records(path):
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


class _FakeInboxServer:
    """A thread accepting exactly ONE connection on a throwaway Unix socket, capturing every byte
    it receives before the client closes — AC-TIC-4's "fake Unix-socket server". Binds under a
    fresh `tempfile.mkdtemp()` (NOT pytest's own `tmp_path`, whose path under
    `pytest-of-<user>/pytest-<n>/<nodeid>` regularly exceeds the ~104-byte `AF_UNIX` path limit on
    macOS/BSD once the node id is long)."""

    def __init__(self):
        self._tmpdir = tempfile.mkdtemp(prefix="tic-sock-")
        self.socket_path = os.path.join(self._tmpdir, "inbox.sock")
        self.received = b""
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(self.socket_path)
        self._srv.listen(1)
        self._srv.settimeout(5)
        self._thread = threading.Thread(target=self._accept_once, daemon=True)
        self._thread.start()

    def _accept_once(self):
        try:
            conn, _addr = self._srv.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(5)
            while True:
                try:
                    chunk = conn.recv(4096)
                except OSError:
                    break
                if not chunk:
                    break
                self.received += chunk

    def join(self, timeout=5):
        self._thread.join(timeout)

    def close(self):
        try:
            self._srv.close()
        except OSError:
            pass
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass
        try:
            os.rmdir(self._tmpdir)
        except OSError:
            pass


@pytest.fixture()
def fake_socket():
    srv = _FakeInboxServer()
    try:
        yield srv
    finally:
        srv.close()


def _setup_atom(tmp_path, release_id, atom_id, done_when, teammate_name="worker-a"):
    """A committed-not-required charter atom (this hook never checks authorization/commit state —
    only `TaskCreated` does) with a release + charter carrying `done_when`. Returns project_dir."""
    project_dir = str(tmp_path / "project")
    os.makedirs(project_dir, exist_ok=True)
    charter_rel = f".foundry/releases/{release_id}/charters/{atom_id}.md"
    _write_charter(project_dir, charter_rel, done_when=done_when)
    _write_release(project_dir, release_id, [
        {"id": atom_id, "charter_ref": charter_rel, "depends_on": []},
    ])
    return project_dir


# ================================================================================================ #
# AC-TIC-1 — unmet -> message posted + record
# ================================================================================================ #


def test_unmet_locator_posts_message_and_records(tmp_path, fake_socket, monkeypatch):
    project_dir = _setup_atom(tmp_path, "r-unmet", "c-atom", ["test:tests/test_x.py"])
    nudges_file = str(tmp_path / "idle-nudges.jsonl")

    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", fake_socket.socket_path)
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_TOKEN", "tok-123")

    payload = _load_fixture("payload-base.json")
    payload["task_subject"] = "atom:r-unmet/c-atom"

    rc = tic.run(payload, project_dir=project_dir, nudges_path=nudges_file)
    assert rc == 0

    fake_socket.join()
    lines = [l for l in fake_socket.received.decode("utf-8").splitlines() if l.strip()]
    assert len(lines) == 2, lines
    auth = json.loads(lines[0])
    assert auth == {"type": "auth", "token": "tok-123"}
    message = json.loads(lines[1])
    assert message["type"] == "message"
    assert message["text"].startswith("IDLE-UNMET worker-a atom:r-unmet/c-atom")
    assert "test:tests/test_x.py" in message["text"]

    records = _read_records(nudges_file)
    assert len(records) == 1
    rec = records[0]
    assert rec["type"] == "idle-unmet"
    assert rec["teammate_name"] == "worker-a"
    assert rec["release_id"] == "r-unmet" and rec["atom_id"] == "c-atom"
    assert rec["unmet"] == ["test:tests/test_x.py"]
    assert rec["message_sent"] is True


def test_unmet_no_auth_line_when_token_unset(tmp_path, fake_socket, monkeypatch):
    project_dir = _setup_atom(tmp_path, "r-noauth", "c-atom", ["test:tests/test_y.py"])
    nudges_file = str(tmp_path / "idle-nudges.jsonl")
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", fake_socket.socket_path)
    monkeypatch.delenv("CLAUDE_CODE_MESSAGING_TOKEN", raising=False)

    payload = _load_fixture("payload-base.json")
    payload["task_subject"] = "atom:r-noauth/c-atom"
    assert tic.run(payload, project_dir=project_dir, nudges_path=nudges_file) == 0

    fake_socket.join()
    lines = [l for l in fake_socket.received.decode("utf-8").splitlines() if l.strip()]
    assert len(lines) == 1, lines
    message = json.loads(lines[0])
    assert message["type"] == "message"


# ================================================================================================ #
# AC-TIC-1/-4 — met -> no message, no record
# ================================================================================================ #


def test_met_locator_no_message_no_record(tmp_path, fake_socket, monkeypatch):
    project_dir = _setup_atom(tmp_path, "r-met", "c-atom", ["test:tests/test_x.py"])
    _write_evidence(project_dir, "c-atom", [_met_row("test:tests/test_x.py")])
    nudges_file = str(tmp_path / "idle-nudges.jsonl")
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", fake_socket.socket_path)

    payload = _load_fixture("payload-base.json")
    payload["task_subject"] = "atom:r-met/c-atom"
    assert tic.run(payload, project_dir=project_dir, nudges_path=nudges_file) == 0

    # nothing was ever sent -- close the server side and prove zero bytes arrived.
    time.sleep(0.2)
    assert fake_socket.received == b""
    assert _read_records(nudges_file) == []


# ================================================================================================ #
# AC-TIC-2 — cap reached -> record only, no message
# ================================================================================================ #


def test_cap_reached_record_only_no_message(tmp_path, fake_socket, monkeypatch):
    project_dir = _setup_atom(tmp_path, "r-cap", "c-atom", ["test:tests/test_x.py"])
    nudges_file = str(tmp_path / "idle-nudges.jsonl")
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", fake_socket.socket_path)

    # pre-seed three prior idle-unmet nudges for this exact atom.
    with open(nudges_file, "w", encoding="utf-8") as fh:
        for _ in range(3):
            fh.write(json.dumps({
                "type": "idle-unmet", "release_id": "r-cap", "atom_id": "c-atom",
                "teammate_name": "worker-a", "unmet": ["test:tests/test_x.py"],
                "message_sent": True, "at": _soon(),
            }) + "\n")

    payload = _load_fixture("payload-base.json")
    payload["task_subject"] = "atom:r-cap/c-atom"
    assert tic.run(payload, project_dir=project_dir, nudges_path=nudges_file) == 0

    time.sleep(0.2)
    assert fake_socket.received == b"", "the cap must suppress the message entirely"
    records = _read_records(nudges_file)
    assert len(records) == 4
    assert records[-1]["type"] == "idle-cap-reached"
    assert "reached" in records[-1]["reason"]


# ================================================================================================ #
# AC-RES-2 — the idle-nudges ledger rotates at 2000 lines
# ================================================================================================ #


def test_ledger_rotates_at_2000_lines(tmp_path):
    nudges_file = str(tmp_path / "idle-nudges.jsonl")
    rotated_file = str(tmp_path / "idle-nudges.1.jsonl")

    with open(nudges_file, "w", encoding="utf-8") as fh:
        for i in range(tic.ROTATION_MAX_LINES):
            fh.write(json.dumps({"type": "idle-error", "reason": f"seed-{i}"}) + "\n")

    tic._append_record(nudges_file, {"type": "idle-error", "reason": "trigger"})

    # the pre-existing 2000 lines moved to the rotation sibling, untouched.
    with open(rotated_file, encoding="utf-8") as fh:
        rotated_lines = [json.loads(line) for line in fh if line.strip()]
    assert len(rotated_lines) == tic.ROTATION_MAX_LINES
    assert rotated_lines[0]["reason"] == "seed-0"
    assert rotated_lines[-1]["reason"] == "seed-1999"

    # the live file starts fresh with only the record that triggered rotation.
    live_records = _read_records(nudges_file)
    assert len(live_records) == 1
    assert live_records[0]["reason"] == "trigger"


def test_ledger_rotation_replaces_an_older_rotation(tmp_path):
    nudges_file = str(tmp_path / "idle-nudges.jsonl")
    rotated_file = str(tmp_path / "idle-nudges.1.jsonl")

    with open(rotated_file, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "idle-error", "reason": "stale-generation"}) + "\n")
    with open(nudges_file, "w", encoding="utf-8") as fh:
        for i in range(tic.ROTATION_MAX_LINES):
            fh.write(json.dumps({"type": "idle-error", "reason": f"seed-{i}"}) + "\n")

    tic._append_record(nudges_file, {"type": "idle-error", "reason": "trigger"})

    with open(rotated_file, encoding="utf-8") as fh:
        rotated_lines = [json.loads(line) for line in fh if line.strip()]
    assert len(rotated_lines) == tic.ROTATION_MAX_LINES, (
        "the OLDER rotation must be replaced, not appended to"
    )
    assert all(r["reason"] != "stale-generation" for r in rotated_lines)


def test_nudge_cap_counts_rows_across_live_and_rotated_files(tmp_path, fake_socket, monkeypatch):
    project_dir = _setup_atom(tmp_path, "r-cap-rot", "c-atom", ["test:tests/test_x.py"])
    nudges_file = str(tmp_path / "idle-nudges.jsonl")
    rotated_file = str(tmp_path / "idle-nudges.1.jsonl")
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", fake_socket.socket_path)

    def _idle_unmet_row():
        return json.dumps({
            "type": "idle-unmet", "release_id": "r-cap-rot", "atom_id": "c-atom",
            "teammate_name": "worker-a", "unmet": ["test:tests/test_x.py"],
            "message_sent": True, "at": _soon(),
        }) + "\n"

    # two prior nudges already rotated out, one still live -> the cap (3) is already reached,
    # even though the LIVE file alone only shows one.
    with open(rotated_file, "w", encoding="utf-8") as fh:
        fh.write(_idle_unmet_row())
        fh.write(_idle_unmet_row())
    with open(nudges_file, "w", encoding="utf-8") as fh:
        fh.write(_idle_unmet_row())

    payload = _load_fixture("payload-base.json")
    payload["task_subject"] = "atom:r-cap-rot/c-atom"
    assert tic.run(payload, project_dir=project_dir, nudges_path=nudges_file) == 0

    time.sleep(0.2)
    assert fake_socket.received == b"", "a rotation must never reset the nudge cap"
    live_records = _read_records(nudges_file)
    assert live_records[-1]["type"] == "idle-cap-reached"


# ================================================================================================ #
# AC-TIC-2 — no socket -> record only
# ================================================================================================ #


def test_no_socket_configured_record_only(tmp_path, monkeypatch):
    project_dir = _setup_atom(tmp_path, "r-nosock", "c-atom", ["test:tests/test_x.py"])
    nudges_file = str(tmp_path / "idle-nudges.jsonl")
    monkeypatch.delenv("CLAUDE_CODE_MESSAGING_SOCKET", raising=False)

    payload = _load_fixture("payload-base.json")
    payload["task_subject"] = "atom:r-nosock/c-atom"
    assert tic.run(payload, project_dir=project_dir, nudges_path=nudges_file) == 0

    records = _read_records(nudges_file)
    assert len(records) == 1
    assert records[0]["type"] == "idle-unmet"
    assert records[0]["message_sent"] is False
    assert "CLAUDE_CODE_MESSAGING_SOCKET" in records[0]["reason"]


def test_unwritable_socket_record_only(tmp_path, monkeypatch):
    project_dir = _setup_atom(tmp_path, "r-badsock", "c-atom", ["test:tests/test_x.py"])
    nudges_file = str(tmp_path / "idle-nudges.jsonl")
    # a path with no listener behind it -- connect() must fail with ECONNREFUSED/ENOENT.
    ghost_sock = str(tmp_path / "no-such.sock")
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", ghost_sock)

    payload = _load_fixture("payload-base.json")
    payload["task_subject"] = "atom:r-badsock/c-atom"
    assert tic.run(payload, project_dir=project_dir, nudges_path=nudges_file) == 0

    records = _read_records(nudges_file)
    assert len(records) == 1
    assert records[0]["message_sent"] is False
    assert "unwritable" in records[0]["reason"]


# ================================================================================================ #
# malformed payload -> exit 0 with a record
# ================================================================================================ #


def test_malformed_payload_exit_0_with_record(tmp_path):
    project_dir = str(tmp_path / "project")
    os.makedirs(project_dir, exist_ok=True)
    nudges_file = str(tmp_path / "idle-nudges.jsonl")

    # not even a dict.
    assert tic.run(["not", "a", "dict"], project_dir=project_dir, nudges_path=nudges_file) == 0
    records = _read_records(nudges_file)
    assert len(records) == 1
    assert records[0]["type"] == "idle-error"

    # a dict with no usable teammate_name at all.
    assert tic.run(_load_fixture("payload-no-teammate-name.json"),
                    project_dir=project_dir, nudges_path=nudges_file) == 0
    records = _read_records(nudges_file)
    assert len(records) == 2
    assert records[1]["reason"] == "missing or unsafe teammate_name"

    # an unsafe teammate_name (path-shaped) is refused the same way, never interpolated.
    unsafe_payload = dict(_load_fixture("payload-base.json"))
    unsafe_payload["teammate_name"] = "../etc/passwd"
    unsafe_payload["task_subject"] = "atom:r/x"
    assert tic.run(unsafe_payload, project_dir=project_dir, nudges_path=nudges_file) == 0
    records = _read_records(nudges_file)
    assert len(records) == 3
    assert records[2]["reason"] == "missing or unsafe teammate_name"

    # the real CLI over unparseable stdin -- exit 0 always, no fail-closed atom-mention rule
    # (unlike the two floor hooks): this hook is never a gate.
    cli = os.path.join(REPO_ROOT, "hooks", "foundry-teammate-idle.py")
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = project_dir
    with open(os.path.join(FIXTURES, "payload-malformed.txt"), encoding="utf-8") as fh:
        raw = fh.read()
    proc = subprocess.run([sys.executable, cli], input=raw, capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr


# ================================================================================================ #
# unresolved task -> record only (no task_subject, tasks-dir fallback fails)
# ================================================================================================ #


def test_unresolved_task_no_subject_no_fallback_match(tmp_path, monkeypatch):
    project_dir = str(tmp_path / "project")
    os.makedirs(project_dir, exist_ok=True)
    nudges_file = str(tmp_path / "idle-nudges.jsonl")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    payload = _load_fixture("payload-base.json")  # no task_subject at all
    assert tic.run(payload, project_dir=project_dir, nudges_path=nudges_file) == 0

    records = _read_records(nudges_file)
    assert len(records) == 1
    assert records[0]["type"] == "idle-unresolved"
    assert "unresolved" in records[0]["reason"]


def test_unresolved_task_multiple_matches(tmp_path, monkeypatch):
    project_dir = str(tmp_path / "project")
    os.makedirs(project_dir, exist_ok=True)
    nudges_file = str(tmp_path / "idle-nudges.jsonl")
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))

    session_id = "3bdd2d91-b30b-426b-a560-f57375be2c98"
    tasks_dir = str(home / ".claude" / "tasks" / session_id)
    _write_task(tasks_dir, "1", subject="atom:r/a", description="worker-a is on this")
    _write_task(tasks_dir, "2", subject="atom:r/b", description="worker-a is also on this")

    payload = _load_fixture("payload-base.json")
    assert tic.run(payload, project_dir=project_dir, nudges_path=nudges_file) == 0
    records = _read_records(nudges_file)
    assert len(records) == 1
    assert records[0]["type"] == "idle-unresolved"
    assert "more than one" in records[0]["reason"]


# ================================================================================================ #
# AC-TIC-1 — tasks-dir fallback resolution when the payload carries no task_subject
# ================================================================================================ #


def test_fallback_resolves_single_in_progress_task_by_description(tmp_path, fake_socket, monkeypatch):
    project_dir = _setup_atom(tmp_path, "r-fallback", "c-atom", ["test:tests/test_z.py"])
    nudges_file = str(tmp_path / "idle-nudges.jsonl")
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", fake_socket.socket_path)

    session_id = "3bdd2d91-b30b-426b-a560-f57375be2c98"
    tasks_dir = str(home / ".claude" / "tasks" / session_id)
    _write_task(tasks_dir, "1", subject="atom:r-fallback/c-atom",
                description="Claimed by Worker-A right now")  # case-insensitive substring match
    _write_task(tasks_dir, "2", subject="atom:other/thing",
                description="unrelated", status="pending")  # not in_progress -- ignored

    payload = _load_fixture("payload-base.json")  # no task_subject; session_id matches tasks_dir
    assert tic.run(payload, project_dir=project_dir, nudges_path=nudges_file) == 0

    fake_socket.join()
    records = _read_records(nudges_file)
    assert len(records) == 1
    assert records[0]["type"] == "idle-unmet"
    assert records[0]["release_id"] == "r-fallback" and records[0]["atom_id"] == "c-atom"


# ================================================================================================ #
# atom with no done_when declared -> unresolved record only
# ================================================================================================ #


def test_no_done_when_declared_record_only(tmp_path):
    project_dir = str(tmp_path / "project")
    os.makedirs(project_dir, exist_ok=True)
    charter_rel = ".foundry/releases/r-nodw/charters/c-atom.md"
    path = os.path.join(project_dir, charter_rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# No done-when section\n")
    _write_release(project_dir, "r-nodw", [{"id": "c-atom", "charter_ref": charter_rel, "depends_on": []}])
    nudges_file = str(tmp_path / "idle-nudges.jsonl")

    payload = _load_fixture("payload-base.json")
    payload["task_subject"] = "atom:r-nodw/c-atom"
    assert tic.run(payload, project_dir=project_dir, nudges_path=nudges_file) == 0
    records = _read_records(nudges_file)
    assert len(records) == 1
    assert "no done_when" in records[0]["reason"]


# ================================================================================================ #
# non-atom subject -> exit 0, no record at all (out of this hook's sight, like the floor hooks)
# ================================================================================================ #


def test_non_atom_subject_exit_0_no_record(tmp_path):
    project_dir = str(tmp_path / "project")
    os.makedirs(project_dir, exist_ok=True)
    nudges_file = str(tmp_path / "idle-nudges.jsonl")

    payload = _load_fixture("payload-base.json")
    payload["task_subject"] = "fix the flaky test"
    assert tic.run(payload, project_dir=project_dir, nudges_path=nudges_file) == 0
    assert _read_records(nudges_file) == []


# ================================================================================================ #
# deprecated team_name fallback field
# ================================================================================================ #


def test_deprecated_team_name_field_accepted(tmp_path, fake_socket, monkeypatch):
    project_dir = _setup_atom(tmp_path, "r-legacy", "c-atom", ["test:tests/test_x.py"],
                               teammate_name="worker-legacy")
    nudges_file = str(tmp_path / "idle-nudges.jsonl")
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_SOCKET", fake_socket.socket_path)

    payload = _load_fixture("payload-deprecated-team-name.json")
    payload["task_subject"] = "atom:r-legacy/c-atom"
    assert tic.run(payload, project_dir=project_dir, nudges_path=nudges_file) == 0

    records = _read_records(nudges_file)
    assert len(records) == 1
    assert records[0]["teammate_name"] == "worker-legacy"


# ================================================================================================ #
# internal-error path -- an injected crash is still exit 0, still recorded
# ================================================================================================ #


def test_internal_error_still_exits_0_and_records(tmp_path, monkeypatch):
    project_dir = _setup_atom(tmp_path, "r-crash", "c-atom", ["test:tests/test_x.py"])
    nudges_file = str(tmp_path / "idle-nudges.jsonl")

    def _boom(*_a, **_k):
        raise RuntimeError("injected crash")

    # fetched fresh, at test-execution time — see the module-level comment above `tic` for why.
    live_ffh = tic._import_ffh()
    monkeypatch.setattr(live_ffh, "declared_done_when", _boom)
    payload = _load_fixture("payload-base.json")
    payload["task_subject"] = "atom:r-crash/c-atom"
    assert tic.run(payload, project_dir=project_dir, nudges_path=nudges_file) == 0

    records = _read_records(nudges_file)
    assert len(records) == 1
    assert records[0]["type"] == "idle-error"
    assert "injected crash" in records[0]["reason"]


# ================================================================================================ #
# post_idle_message() unit coverage over the fake socket
# ================================================================================================ #


def test_post_idle_message_wire_format(fake_socket):
    sent, reason = tic.post_idle_message(
        "IDLE-UNMET worker-a atom:r/x — unmet: test:tests/test_x.py",
        socket_path=fake_socket.socket_path, token="secret-tok",
    )
    assert sent is True and reason is None
    fake_socket.join()
    lines = [l for l in fake_socket.received.decode("utf-8").splitlines() if l.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"type": "auth", "token": "secret-tok"}
    body = json.loads(lines[1])
    assert body["type"] == "message"
    assert body["text"] == "IDLE-UNMET worker-a atom:r/x — unmet: test:tests/test_x.py"


def test_post_idle_message_no_socket_configured(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_MESSAGING_SOCKET", raising=False)
    sent, reason = tic.post_idle_message("hello", socket_path=None)
    assert sent is False
    assert "not set" in reason


# ================================================================================================ #
# hooks.json registration — TeammateIdle, no matcher, executable script
# ================================================================================================ #


def test_teammate_idle_registered_in_hooks_json_no_matcher():
    with open(os.path.join(REPO_ROOT, "hooks", "hooks.json"), encoding="utf-8") as fh:
        doc = json.load(fh)

    assert "TeammateIdle" in doc["hooks"], "TeammateIdle must be registered in hooks/hooks.json"
    groups = doc["hooks"]["TeammateIdle"]
    assert len(groups) == 1
    group = groups[0]
    assert "matcher" not in group, "TeammateIdle registration must carry no matcher"
    commands = [h["command"] for h in group["hooks"] if h.get("type") == "command"]
    assert commands == ['"${CLAUDE_PLUGIN_ROOT}"/hooks/foundry-teammate-idle.py']

    script = os.path.join(REPO_ROOT, "hooks", "foundry-teammate-idle.py")
    assert os.path.isfile(script)
    assert os.access(script, os.X_OK), "direct-invoked (no interpreter prefix) — must stay X_OK"
    with open(script, encoding="utf-8") as fh:
        assert fh.readline().startswith("#!"), "direct-invoked hook must carry a shebang"


def test_permission_floor_files_still_parse_and_are_unchanged_by_this_atom():
    """See this module's own docstring: this atom introduces NO new `scripts/*.py` file (it
    reuses `scripts/foundry_floor_hooks.py`), so — following the floor-hooks precedent exactly —
    neither `cli/permission-floor.json` nor `docs/permission-floor.json` needs a new entry. This
    checkpoint keeps that a checked fact rather than an assumption: both copies still parse, and
    neither hook script this atom ships (there is only one) appears as a `not_invoked` script
    name in either file."""
    for rel in ("cli/permission-floor.json", "docs/permission-floor.json"):
        with open(os.path.join(REPO_ROOT, rel), encoding="utf-8") as fh:
            doc = json.load(fh)
        names = {e["script"] for e in doc.get("not_invoked", [])}
        assert "foundry-teammate-idle.py" not in names, (
            f"{rel}: foundry-teammate-idle.py is a hook entry point, never Bash-invoked -- it "
            f"must not appear in not_invoked (that would imply it needs a Bash allow-rule)"
        )
