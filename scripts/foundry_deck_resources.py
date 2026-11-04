#!/usr/bin/env python3
"""foundry_deck_resources.py - B2/F2: bound parallelism by the machine.

Measures CPU load (1/5/15 against the CPU count), memory, swap, the root disk AND the mount of
`--path` (the mount the work actually writes to), and returns GREEN, AMBER or RED. It also prints
the heavy-slot count and `max_builders_effective`, the fan-out the tick may use.

  foundry_deck_resources.py [--path DIR] [--project-dir DIR] [--from-json FILE] [--reset-oom-baseline]

Prints one JSON object: verdict, reasons, readings, thresholds, heavy_slots, max_builders_policy,
max_builders_effective, oom_recent (true, false or "unknown"), oom_baseline.

Reads: /proc/loadavg, /proc/meminfo, /proc/uptime, /proc/sys/kernel/random/boot_id, statvfs of / and
of --path, the cgroup memory.max/memory.current when present, the kernel log (/dev/kmsg or
`journalctl -k`) when readable, the OOM-baseline file, and the policy file through
foundry_operating_model.py. Read-only except `--reset-oom-baseline`, which writes one small file
under <project>/.foundry/. `--from-json` replaces every live reading with a fixture; it is
confined to <plugin root>/tests/fixtures/resources/ (realpath check) and only the known reading
keys are ever echoed. Thresholds are the code defaults below (the policy schema has no key for them).

Fan-out rule (B2): cut only on the verdict or on a measured OOM kill.
  GREEN -> the policy max_builders.   AMBER -> policy minus `amber_step` (never below 1).
  RED   -> 1.                         A recent OOM kill (and a GREEN verdict) -> the AMBER cut.

OOM baseline. A kill the deck caused itself (a heavy command it started ran the host out of
memory) must not hold the fan-out down for ever. "Reset" means: record the current uptime as the
baseline; only kernel-log kills after the baseline count as recent, and a kill older than
`oom_window_s` never counts. The report names the action in `oom_baseline`.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_THRESHOLDS = {
    "load_amber": 0.75,        # x cpus, on the 5-minute load
    "load_red": 1.5,
    "mem_free_amber": 0.20,    # fraction of total memory still available
    "mem_free_red": 0.10,
    "swap_used_red": 0.50,     # fraction of total swap in use
    "disk_free_amber": 0.15,
    "disk_free_red": 0.05,
    "amber_step": 1,           # builders removed on AMBER
    "oom_window_s": 900,
}
DEFAULT_MAX_BUILDERS = 4
FIXTURE_DIR = os.path.join(os.path.dirname(HERE), "tests", "fixtures", "resources")
READING_KEYS = ("cpus", "load", "mem_total_mb", "mem_available_mb", "swap_total_mb", "swap_used_mb")
DISK_KEYS = ("disk_root", "disk_path")
BASELINE_REL = os.path.join(".foundry", "deck-resources-oom-baseline.json")
_OOM_RE = re.compile(r"out of memory:|oom-kill:|oom_reaper:", re.I)
_TS_RE = re.compile(r"^\s*\[\s*([0-9]+(?:\.[0-9]+)?)\]")


# ---------------------------------------------------------------------------------------------
# measurement (impure, read-only)
# ---------------------------------------------------------------------------------------------
class ResetUnavailable(Exception):
    """--reset-oom-baseline was asked but the uptime clock is unreadable."""


class FixtureRefused(Exception):
    """--from-json named a file outside the shipped fixture directory."""


def _boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id", encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def _cgroup_memory():
    """(limit_mb, current_mb) from the cgroup v2 files when a numeric limit is set, else None."""
    try:
        with open("/sys/fs/cgroup/memory.max", encoding="utf-8") as fh:
            raw = fh.read().strip()
        if not raw.isdigit():
            return None
        with open("/sys/fs/cgroup/memory.current", encoding="utf-8") as fh:
            cur = int(fh.read().strip())
        return int(raw) // 1048576, cur // 1048576
    except (OSError, ValueError):
        return None


def _cpu_count():
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def _meminfo():
    out = {}
    with open("/proc/meminfo", encoding="utf-8") as fh:
        for line in fh:
            k, _, v = line.partition(":")
            m = re.match(r"\s*(\d+)", v)
            if m:
                out[k.strip()] = int(m.group(1))
    return out


def _disk(path):
    st = os.statvfs(path)
    return {"total_mb": st.f_blocks * st.f_frsize // 1048576, "free_mb": st.f_bavail * st.f_frsize // 1048576}


def measure(path):
    mi = _meminfo()
    with open("/proc/loadavg", encoding="utf-8") as fh:
        load = [float(x) for x in fh.read().split()[:3]]
    swap_total = mi.get("SwapTotal", 0) // 1024
    mem_total = mi.get("MemTotal", 0) // 1024
    mem_avail = mi.get("MemAvailable", mi.get("MemFree", 0)) // 1024
    cg = _cgroup_memory()
    if cg is not None and cg[0] > 0:
        mem_total = min(mem_total, cg[0]) if mem_total else cg[0]
        mem_avail = min(mem_avail, max(0, cg[0] - cg[1]))
    return {
        "cpus": _cpu_count(),
        "load": load,
        "mem_total_mb": mem_total,
        "mem_available_mb": mem_avail,
        "swap_total_mb": swap_total,
        "swap_used_mb": swap_total - mi.get("SwapFree", 0) // 1024,
        "disk_root": _disk("/"),
        "disk_path": _disk(path),
    }


def _uptime_s():
    try:
        with open("/proc/uptime", encoding="utf-8") as fh:
            return float(fh.read().split()[0])
    except (OSError, ValueError):
        return None


def read_kernel_log():
    """Kernel log lines as '[ seconds-since-boot] text', or None when no log is readable.
    Never required: any failure is None."""
    try:
        fd = os.open("/dev/kmsg", os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        fd = None
    if fd is not None:
        lines = []
        try:
            while True:
                try:
                    rec = os.read(fd, 8192)
                except BlockingIOError:
                    break
                head, _, msg = rec.decode("utf-8", "replace").partition(";")
                f = head.split(",")
                if len(f) >= 3:
                    lines.append("[%.6f] %s" % (int(f[2]) / 1e6, msg.splitlines()[0] if msg else ""))
        except OSError:
            lines = None
        finally:
            os.close(fd)
        if lines is not None:
            return lines
    try:
        r = subprocess.run(["journalctl", "-k", "--no-pager", "-o", "short-monotonic", "-n", "5000"],
                           capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return [re.sub(r"^\[\s*", "[", l) for l in r.stdout.splitlines()]


# ---------------------------------------------------------------------------------------------
# pure decision core
# ---------------------------------------------------------------------------------------------
def auto_heavy_slots(cpus, mem_total_mb):
    return max(1, min(int(cpus) // 2, (int(mem_total_mb) // 1024) // 4))


def readings_incomplete(r):
    """True when a figure the verdict needs is missing or not usable (zero cpus, memory or disk)."""
    try:
        if not r or int(r.get("cpus") or 0) < 1 or len(r.get("load") or []) < 2:
            return True
        if not r.get("mem_total_mb") or r.get("mem_available_mb") is None:
            return True
        if r.get("swap_total_mb") is None or r.get("swap_used_mb") is None:
            return True
        for k in DISK_KEYS:
            d = r.get(k) or {}
            if not d.get("total_mb") or d.get("free_mb") is None:
                return True
    except (TypeError, ValueError):
        return True
    return False


def decide_verdict(readings, th):
    reasons, level = [], 0   # 0 GREEN, 1 AMBER, 2 RED

    def hit(lv, text):
        nonlocal level
        level = max(level, lv)
        reasons.append(text)

    if readings_incomplete(readings):
        hit(1, "readings-incomplete")
    cpus = max(1, int(readings.get("cpus") or 1))
    load = readings.get("load") or []
    if len(load) >= 2:
        l5 = float(load[1])
        if l5 > th["load_red"] * cpus:
            hit(2, "cpu load5 %.2f > %.2f x %d cpus" % (l5, th["load_red"], cpus))
        elif l5 > th["load_amber"] * cpus:
            hit(1, "cpu load5 %.2f > %.2f x %d cpus" % (l5, th["load_amber"], cpus))
    mt, ma = readings.get("mem_total_mb"), readings.get("mem_available_mb")
    if mt and ma is not None:
        free = ma / mt
        if free < th["mem_free_red"]:
            hit(2, "memory free %.1f%% < %.0f%%" % (free * 100, th["mem_free_red"] * 100))
        elif free < th["mem_free_amber"]:
            hit(1, "memory free %.1f%% < %.0f%%" % (free * 100, th["mem_free_amber"] * 100))
    st, su = readings.get("swap_total_mb"), readings.get("swap_used_mb")
    if st and su is not None and su / st > th["swap_used_red"]:
        hit(2, "swap used %.1f%% > %.0f%%" % (su / st * 100, th["swap_used_red"] * 100))
    for key, label in (("disk_root", "disk(root)"), ("disk_path", "disk(path)")):
        d = readings.get(key) or {}
        if d.get("total_mb"):
            free = d["free_mb"] / d["total_mb"]
            if free < th["disk_free_red"]:
                hit(2, "%s free %.1f%% < %.0f%%" % (label, free * 100, th["disk_free_red"] * 100))
            elif free < th["disk_free_amber"]:
                hit(1, "%s free %.1f%% < %.0f%%" % (label, free * 100, th["disk_free_amber"] * 100))
    return ("GREEN", "AMBER", "RED")[level], reasons


def decide_oom(log_lines, uptime_s, baseline_s, window_s):
    """True when a kill sits inside the window and after the baseline; False when the log is
    readable and shows none; "unknown" when there is no log (or no clock to place the kills)."""
    if log_lines is None:
        return "unknown"
    stamps = []
    for l in log_lines:
        if _OOM_RE.search(l):
            m = _TS_RE.match(l)
            if m:
                stamps.append(float(m.group(1)))
    if not stamps:
        return False
    if uptime_s is None:
        return "unknown"
    return any(t > baseline_s and uptime_s - t <= window_s for t in stamps)


def effective_builders(max_builders, verdict, oom_recent, amber_step):
    if verdict == "RED":
        return 1
    cut = verdict == "AMBER" or oom_recent is True
    return max(1, max_builders - int(amber_step)) if cut else max_builders


# ---------------------------------------------------------------------------------------------
# policy + state
# ---------------------------------------------------------------------------------------------
def _load_policy(project_dir):
    """(policy, error). A broken or unloadable policy gives the defaults AND a named error."""
    try:
        spec = importlib.util.spec_from_file_location("foundry_operating_model", os.path.join(HERE, "foundry_operating_model.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        p = mod.load(project_dir)
        return {"max_builders": int(p.get("deck.max_builders")), "heavy_slots": p.get("deck.heavy_slots")}, None
    except Exception as e:  # noqa: BLE001 - a broken policy must not stop the measurement
        return {"max_builders": DEFAULT_MAX_BUILDERS, "heavy_slots": "auto"}, ("%s: %s" % (type(e).__name__, str(e)))[:200]


def _confined_fixture(path):
    real = os.path.realpath(path)
    base = os.path.realpath(FIXTURE_DIR)
    if not (real == base or real.startswith(base + os.sep)):
        raise FixtureRefused("--from-json is confined to %s" % FIXTURE_DIR)
    return real


def _clean_fixture(fixture):
    """Only the known reading keys, coerced to numbers; nothing else is ever echoed."""
    if not isinstance(fixture, dict):
        raise FixtureRefused("fixture must be a JSON object")
    out = {}
    for k in READING_KEYS:
        v = fixture.get(k)
        if k == "load":
            out[k] = [float(x) for x in v] if isinstance(v, list) else None
        else:
            out[k] = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        if out[k] is not None and k != "load":
            out[k] = int(out[k]) if float(out[k]).is_integer() else out[k]
    for k in DISK_KEYS:
        d = fixture.get(k)
        out[k] = {"total_mb": d.get("total_mb"), "free_mb": d.get("free_mb")} if isinstance(d, dict) else None
        if out[k] is not None:
            for kk, vv in list(out[k].items()):
                if not isinstance(vv, (int, float)) or isinstance(vv, bool):
                    out[k][kk] = None
    return {k: v for k, v in out.items() if v is not None}


def _baseline_path(project_dir):
    return os.path.join(project_dir, BASELINE_REL)


def _read_baseline(project_dir, boot_id):
    """Baseline uptime for THIS boot; a baseline from another boot is ignored (0)."""
    try:
        with open(_baseline_path(project_dir), encoding="utf-8") as fh:
            d = json.load(fh)
        if d.get("boot_id") != boot_id:
            return 0.0
        return float(d.get("uptime_s", 0))
    except (OSError, ValueError, AttributeError, TypeError):
        return 0.0


def _write_baseline(project_dir, uptime_s, boot_id):
    p = _baseline_path(project_dir)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"uptime_s": uptime_s, "boot_id": boot_id}, fh)


def evaluate(path=None, project_dir=None, from_json=None, policy=None, thresholds=None, reset_oom_baseline=False):
    project_dir = project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    path = path or project_dir
    policy_error = None
    if policy is None:
        policy, policy_error = _load_policy(project_dir)
    else:
        policy = dict(policy)
    th = dict(DEFAULT_THRESHOLDS)
    th.update(thresholds or {})
    log, uptime, boot_id, measured_ok = None, None, None, True
    if from_json:
        with open(_confined_fixture(from_json), encoding="utf-8") as fh:
            fixture = json.load(fh)
        readings = _clean_fixture(fixture)
        log, uptime, boot_id = fixture.get("oom_log"), fixture.get("uptime_s"), fixture.get("boot_id")
        if log is not None and not (isinstance(log, list) and all(isinstance(l, str) for l in log)):
            log = None
        if not isinstance(uptime, (int, float)) or isinstance(uptime, bool):
            uptime = None
        if not isinstance(boot_id, str):
            boot_id = None
    else:
        try:
            readings = measure(path)
        except (OSError, ValueError, KeyError):
            readings, measured_ok = {}, False
        log, uptime, boot_id = read_kernel_log(), _uptime_s(), _boot_id()
    verdict, reasons = decide_verdict(readings, th)
    baseline_info = {"action": "none", "uptime_s": _read_baseline(project_dir, boot_id)}
    if reset_oom_baseline:
        if uptime is None:
            raise ResetUnavailable("uptime unreadable")
        _write_baseline(project_dir, uptime, boot_id)
        baseline_info = {"action": "reset", "uptime_s": uptime,
                         "meaning": "reset: kernel-log kills up to this uptime no longer count; only later kills do"}
    oom = decide_oom(log, uptime, baseline_info["uptime_s"], th["oom_window_s"])
    if oom is True:
        reasons.append("oom kill in the last %ds" % th["oom_window_s"])
    hs = policy.get("heavy_slots", "auto")
    if not measured_ok:
        slots = 1
    elif hs == "auto":
        slots = auto_heavy_slots(readings.get("cpus", 1), readings.get("mem_total_mb", 0))
    else:
        slots = int(hs)
    mb = int(policy.get("max_builders", DEFAULT_MAX_BUILDERS))
    out = {
        "verdict": verdict,
        "reasons": reasons,
        "readings": readings,
        "thresholds": th,
        "heavy_slots": slots,
        "max_builders_policy": mb,
        "max_builders_effective": effective_builders(mb, verdict, oom, th["amber_step"]),
        "oom_recent": oom,
        "oom_baseline": baseline_info,
    }
    if policy_error:
        out["policy_error"] = policy_error
    return out


def heavy_slots_now(project_dir=None):
    """The slot count for foundry-deck-heavy: policy + a live measurement only (no kernel log).
    Any failure gives 1."""
    project_dir = project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    policy, _err = _load_policy(project_dir)
    hs = policy.get("heavy_slots", "auto")
    if hs != "auto":
        return max(1, int(hs))
    try:
        r = measure(project_dir)
        return auto_heavy_slots(r.get("cpus", 1), r.get("mem_total_mb", 0))
    except (OSError, ValueError, KeyError):
        return 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="GREEN/AMBER/RED machine verdict (B2, F2)")
    ap.add_argument("--path", help="the mount the work writes to (default: the project dir)")
    ap.add_argument("--project-dir")
    ap.add_argument("--from-json", help="inject readings from a fixture file (tests)")
    ap.add_argument("--reset-oom-baseline", action="store_true")
    a = ap.parse_args(argv)
    try:
        out = evaluate(path=a.path, project_dir=a.project_dir, from_json=a.from_json,
                       reset_oom_baseline=a.reset_oom_baseline)
    except ResetUnavailable:
        print(json.dumps({"error": "oom-baseline-reset-unavailable", "detail": "uptime unreadable"}))
        return 2
    except FixtureRefused as e:
        print(json.dumps({"error": "from-json-refused", "detail": str(e)[:200]}))
        return 2
    except (OSError, ValueError, TypeError) as e:
        print(json.dumps({"error": "deck-resources-failed", "detail": str(e)[:200]}))
        return 2
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
