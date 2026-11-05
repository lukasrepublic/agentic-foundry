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

This script EXECUTES the command it is given: the plugin-scripts-allow hook never grants it a silent allow, and
the permission floor tiers it `ask`. Exit: the command's status / 1 hard failure (a log post failed) / 2 usage.
Stdlib only. No network beyond the git remote named.
"""
from __future__ import annotations

import argparse
import os
import shlex
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


def _run(argv):
    """-> (status, reason). Never raises for a command that cannot start."""
    try:
        r = subprocess.run(argv)
    except FileNotFoundError:
        return 127, "the command was not found"
    except PermissionError:
        return 126, "the command is not executable"
    except OSError as exc:
        return 126, f"the command could not start ({type(exc).__name__})"
    st = _status_of(r.returncode)
    if r.returncode < 0:
        return st, f"the command was ended by signal {-r.returncode}"
    return st, f"the command exited with status {st}"


def _post(ad, repo, remote, sender, to, kind, body, corr, kw):
    rec, errs = _ml.post(ad, repo, remote, sender, to, kind, body, corr=corr, **kw)
    if errs:
        raise RuntimeError("; ".join(errs)[:200])
    return rec


def run_step(command, repo, remote, sender, to, what, cleanups=(), adapter=None, sleep=None, runner=None):
    """Returns the process exit status. Prints one line to stderr for every hard failure."""
    ad = adapter or _ml.Adapter()
    kw = {"sleep": sleep} if sleep else {}
    run = runner or _run
    corr = "prod-" + _ml.new_ulid().lower()
    program = os.path.basename(command[0])
    before = f"what: {what}\nprogram: {_clean(program, 80)}"
    try:
        _post(ad, repo, remote, sender, to, "PROD-BEFORE", before, corr, kw)
    except Exception as exc:  # noqa: BLE001 -- the before line is the floor: no line, no step
        print(f"foundry_prod_step: the before line could not be posted, so the command was NOT run: "
              f"{type(exc).__name__}: {_ml.redact(str(exc))[:200]}", file=sys.stderr)
        return 1
    status, reason = run(command)
    if status == 0:
        try:
            _post(ad, repo, remote, sender, to, "PROD-AFTER", "outcome: ok\nstatus: 0", corr, kw)
        except Exception as exc:  # noqa: BLE001
            print(f"foundry_prod_step: the command succeeded but the after line could not be posted "
                  f"(process miss, corr {corr}): {type(exc).__name__}: {_ml.redact(str(exc))[:200]}", file=sys.stderr)
            return 1
        return 0
    failed = 0
    for text in cleanups:
        try:
            cstatus, _ = run(shlex.split(text))
        except ValueError:
            cstatus = 2
        failed += 1 if cstatus != 0 else 0
    cleanup = "none" if not cleanups else ("ok" if not failed else f"failed ({failed} of {len(cleanups)} commands)")
    stop = f"reason: {reason}\nstatus: {status}\ncleanup: {cleanup}"
    try:
        _post(ad, repo, remote, sender, to, "STOP", stop, corr, kw)
    except Exception as exc:  # noqa: BLE001
        print(f"foundry_prod_step: the STOP line could not be posted (process miss, corr {corr}): "
              f"{type(exc).__name__}: {_ml.redact(str(exc))[:200]}", file=sys.stderr)
    return status if status != 0 else 1


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
