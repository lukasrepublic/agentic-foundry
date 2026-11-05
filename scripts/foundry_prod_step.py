#!/usr/bin/env python3
"""foundry_prod_step.py -- state a production step before, report it after (rule D2; gv-13).

  foundry_prod_step.py --repo DIR --remote NAME --what "<one line>" [--to ID] [--from ME]
                       [--on-failure "<cleanup command>" ...] -- <command> [args ...]

What it does, in this order:
  1. Posts PROD-BEFORE (a fresh `corr`, the `--what` line, the program name) to the shared message log through
     foundry_message_log. If that post fails the command does NOT run: the before line is the floor.
  2. Runs the command (an argument list, never a shell). Its output goes to the caller, never to the log.
  3. Exit 0: posts PROD-AFTER with the outcome and exits 0.
     Any other result (a non-zero status, a signal, a command that cannot start): runs each `--on-failure`
     cleanup command in order (an argument list, never a shell), THEN posts STOP whose body is the reason and the
     cleanup result, and exits non-zero with the command's status (128+N for a signal, 127 when it cannot start).

`--repo` and `--remote` are REQUIRED. The log is written to the repository and remote you name, never to
whatever the working directory's origin happens to be (a deck works across several repositories).
A missing BEFORE shows up later as a process miss in `foundry_message_log.py audit`.

The posted bodies carry the `--what` line, the program name (not its arguments, which may hold secrets), the
exit status and the cleanup result. They never carry command output.

Signals. SIGINT and SIGTERM end the child and still post STOP (after cleanup); a second signal is ignored while
cleanup runs. A cleanup command that fails or raises is reported in the STOP body and nothing more.

This script EXECUTES the command it is given: the plugin-scripts-allow hook never grants it a silent allow, and
the permission floor tiers it `ask`. NEVER persist an allow for it ("always allow"): that would let any command run
unprompted under the plugin's name. Exit: the command's status / 1 hard failure (a log post failed) / 2 usage.
Stdlib only. No network beyond the git remote named.
"""
from __future__ import annotations

import argparse
import os
import shlex
import signal
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import foundry_message_log as _ml  # noqa: E402

MAX_WHAT = 300


def _status_of(returncode):
    """A signal death is -N from subprocess; report it the way a shell does."""
    return 128 - returncode if returncode < 0 else returncode


def _clean(text, limit):
    text = "".join(" " if c in "\r\n\t" else c for c in str(text) if c in "\r\n\t" or c.isprintable())
    return " ".join(text.split())[:limit]


class _Interrupted(BaseException):
    """Raised by the signal handler in the main thread; carries the signal number."""

    def __init__(self, signum):
        super().__init__(signum)
        self.signum = signum


class _Adapter(_ml.Adapter):
    new_session = True      # git children get their own process group, so a signal to us is not delivered to them


def _killpg(pid, sig):
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _run(argv):
    """-> (status, reason). Never raises for a command that cannot start. The child runs in its own process group;
    an interrupt ends the whole group (TERM, then KILL) before it is re-raised."""
    try:
        proc = subprocess.Popen(argv, start_new_session=True)
    except FileNotFoundError:
        return 127, "the command was not found"
    except PermissionError:
        return 126, "the command is not executable"
    except OSError as exc:
        return 126, f"the command could not start ({type(exc).__name__})"
    try:
        rc = proc.wait()
    except BaseException:
        _killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            pass
        _killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        raise
    st = _status_of(rc)
    if rc < 0:
        return st, f"the command was ended by signal {-rc}"
    return st, f"the command exited with status {st}"


def _post(ad, repo, remote, sender, to, kind, body, corr, kw):
    rec, errs = _ml.post(ad, repo, remote, sender, to, kind, body, corr=corr, **kw)
    if errs:
        raise RuntimeError("; ".join(errs)[:200])
    return rec


def _install_handlers():
    """SIGINT and SIGTERM raise _Interrupted once. After that a "stopping" flag makes the handler return, so
    cleanup and the STOP line finish; no signal is set to ignore, so children never inherit an ignored signal."""
    saved, state = {}, {"stopping": False}

    def handler(signum, _frame):
        if state["stopping"]:
            return
        state["stopping"] = True
        raise _Interrupted(signum)

    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            saved[sig] = signal.signal(sig, handler)
    except ValueError:      # not the main thread: no handlers, the except blocks below still post STOP
        return {}
    return saved


def _restore_handlers(saved):
    for sig, old in saved.items():
        try:
            signal.signal(sig, old)
        except (ValueError, TypeError):
            pass


def run_step(command, repo, remote, sender, to, what, cleanups=(), adapter=None, sleep=None, runner=None):
    """Returns the process exit status (128+N for every signal path). Prints one line to stderr for every hard
    failure. Once the BEFORE post has started, a STOP line is always attempted unless the command succeeded."""
    ad = adapter or _Adapter()
    kw = {"sleep": sleep} if sleep else {}
    run = runner or _run
    corr = "prod-" + _ml.new_ulid().lower()
    program = os.path.basename(command[0])
    before = f"what: {what}\nprogram: {_clean(program, 80)}"
    saved = _install_handlers()
    try:
        status, reason = 1, "stopped before the command finished"
        try:
            try:
                _post(ad, repo, remote, sender, to, "PROD-BEFORE", before, corr, kw)
            except _Interrupted:
                raise
            except BaseException as exc:  # noqa: BLE001 -- the before line is the floor: no line, no step
                print(f"foundry_prod_step: the before line could not be posted, so the command was NOT run: "
                      f"{type(exc).__name__}: {_ml.redact(str(exc))[:200]}", file=sys.stderr)
                return 1
            status, reason = run(command)
        except _Interrupted as exc:      # anywhere from the start of the BEFORE post to the end of the command
            status, reason = 128 + exc.signum, f"interrupted by signal {exc.signum}"
        except BaseException as exc:  # noqa: BLE001
            status, reason = 1, f"unexpected {type(exc).__name__} while running the command"
        intr = status - 128 if reason.startswith("interrupted by signal") else None

        def post_final(kind, body):
            """-> (error or None, signal or None). A first signal that lands during the post: post again once (the
            handler no longer raises)."""
            sig = None
            for _ in (1, 2):
                try:
                    _post(ad, repo, remote, sender, to, kind, body, corr, kw)
                    return None, sig
                except _Interrupted as e:
                    sig = e.signum
                except BaseException as exc:  # noqa: BLE001
                    return exc, sig
            return RuntimeError("interrupted twice"), sig

        if status == 0:
            err, sig = post_final("PROD-AFTER", "outcome: ok\nstatus: 0")
            intr = intr or sig
            if err is not None:
                print(f"foundry_prod_step: the command succeeded but the after line could not be posted "
                      f"(process miss, corr {corr}): {type(err).__name__}: {_ml.redact(str(err))[:200]}", file=sys.stderr)
                return 128 + intr if intr else 1
            return 128 + intr if intr else 0
        failed = 0
        for text in cleanups:
            try:
                cstatus, _ = run(shlex.split(text))
            except _Interrupted as e:
                intr, cstatus = intr or e.signum, 1
            except BaseException:  # noqa: BLE001 -- a cleanup that blows up is a failed cleanup, nothing more
                cstatus = 1
            failed += 1 if cstatus != 0 else 0
        cleanup = "none" if not cleanups else ("ok" if not failed else f"failed ({failed} of {len(cleanups)} commands)")
        stop = f"reason: {reason}\nstatus: {status}\ncleanup: {cleanup}"
        err, sig = post_final("STOP", stop)
        intr = intr or sig
        if err is not None:
            print(f"foundry_prod_step: the STOP line could not be posted (process miss, corr {corr}): "
                  f"{type(err).__name__}: {_ml.redact(str(err))[:200]}", file=sys.stderr)
        return 128 + intr if intr else (status if status != 0 else 1)
    finally:
        _restore_handlers(saved)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" in argv:
        i = argv.index("--")
        head, command = argv[:i], argv[i + 1:]
    else:
        head, command = argv, []
    ap = argparse.ArgumentParser(prog="foundry_prod_step.py", description="state a production step before, report it after")
    ap.add_argument("--repo", required=True, help="the git repository the log is written to (required)")
    ap.add_argument("--remote", required=True, help="the git remote the log is pushed to (required)")
    ap.add_argument("--what", required=True, help="one line: what will happen")
    ap.add_argument("--to", default="all")
    ap.add_argument("--from", dest="sender", default=None)
    ap.add_argument("--on-failure", action="append", default=[], help="a cleanup command, run in order on failure")
    try:
        args = ap.parse_args(head)
    except SystemExit as exc:
        return 2 if exc.code else 0
    if not command:
        print("foundry_prod_step: give the command after --", file=sys.stderr)
        return 2
    if any(not c.strip() for c in args.on_failure):
        print("foundry_prod_step: an --on-failure cleanup command must not be empty", file=sys.stderr)
        return 2
    try:
        _ml._project_dir(args.repo)      # an existing directory that is not spelled like an option
    except Exception as exc:  # noqa: BLE001
        print(f"foundry_prod_step: {_clean(exc, 200)}", file=sys.stderr)
        return 2
    what = _clean(args.what, MAX_WHAT)
    if not what:
        print("foundry_prod_step: --what must say what will happen", file=sys.stderr)
        return 2
    try:
        _ml._check_remote(args.remote)
    except Exception as exc:  # noqa: BLE001
        print(f"foundry_prod_step: {_clean(exc, 200)}", file=sys.stderr)
        return 2
    return run_step(command, args.repo, args.remote, args.sender or _ml._default_sender(), args.to, what,
                    args.on_failure)


if __name__ == "__main__":
    sys.exit(main())
