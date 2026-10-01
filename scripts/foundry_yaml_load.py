"""Shared safe-YAML loading for the plugin's hot paths (doctor --session-start, capability preflight).

`safe_load` is `yaml.load(raw, Loader=SAFE_LOADER)` where SAFE_LOADER is libyaml's `CSafeLoader`
when PyYAML was built with it (an order of magnitude faster than the pure-Python parser) and the
pure-Python `SafeLoader` otherwise. Both are *safe* loaders -- the full `Loader` is never used.

`safe_load_file` adds a per-process memo keyed by (realpath, mtime_ns, size) so one run never parses
the same file twice. It returns a deep copy, so a caller mutating the result cannot poison the memo.
"""
from __future__ import annotations

import copy
import os

import yaml

SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

_MEMO = {}
_MEMO_MAX = 4096


def safe_load(raw):
    """`yaml.safe_load` semantics, on the C loader when available."""
    return yaml.load(raw, Loader=SAFE_LOADER)  # noqa: S506 - SAFE_LOADER is a safe loader


def safe_load_file(path):
    """Parse the YAML file at `path` (bytes read, safe semantics), memoized per process on
    (realpath, mtime_ns, size). Raises OSError / yaml.YAMLError exactly like open()/safe_load."""
    real = os.path.realpath(path)
    st = os.stat(real)
    key = (real, st.st_mtime_ns, st.st_size)
    if key in _MEMO:
        return copy.deepcopy(_MEMO[key])
    with open(real, "rb") as fh:
        raw = fh.read()
    data = safe_load(raw)
    if len(_MEMO) >= _MEMO_MAX:
        _MEMO.clear()
    _MEMO[key] = data
    return copy.deepcopy(data)
