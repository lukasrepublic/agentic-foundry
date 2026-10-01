"""tests/test_yaml_fast_loader.py — the doctor's session-start hot path must parse YAML with libyaml's
C loader when it is available (a pure-Python parse over ~600 documents cost ~25 s), keep SAFE
semantics, and never parse an unchanged file twice in one process."""
from __future__ import annotations

import os

import pytest
import yaml

from conftest import load_module

YL = load_module("scripts/foundry_yaml_load.py", "foundry_yaml_load")
CPF = load_module("scripts/foundry-capability-preflight.py", "foundry_capability_preflight")


def test_loader_is_c_loader_when_available():
    expected = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    assert YL.SAFE_LOADER is expected
    # never the unsafe full loader
    assert YL.SAFE_LOADER in (getattr(yaml, "CSafeLoader", None), yaml.SafeLoader)


def test_safe_semantics_refuse_python_tags():
    with pytest.raises(yaml.YAMLError):
        YL.safe_load("x: !!python/object/apply:os.system ['true']")


def test_contract_loader_uses_the_shared_helper(tmp_path, monkeypatch):
    """A regression that reverts load_contract_capabilities to yaml.safe_load (pure Python) fails
    here: the contract must be parsed through the shared helper's loader."""
    root = tmp_path / "ws"
    contract = root / "c.yaml"
    root.mkdir()
    contract.write_text("requires_capabilities:\n  - Bash(git status)\n", encoding="utf-8")

    seen = []
    real_load = yaml.load

    def spy_load(stream, Loader=None):  # noqa: N803
        seen.append(Loader)
        return real_load(stream, Loader=Loader)

    def forbid_safe_load(*a, **k):
        raise AssertionError("pure-Python yaml.safe_load used on the preflight hot path")

    YL._MEMO.clear()
    monkeypatch.setattr(yaml, "load", spy_load)
    monkeypatch.setattr(yaml, "safe_load", forbid_safe_load)
    caps = CPF.load_contract_capabilities(str(contract), str(root))
    assert caps == ["Bash(git status)"]
    assert seen == [getattr(yaml, "CSafeLoader", yaml.SafeLoader)]


def test_same_file_is_parsed_once_and_invalidated_on_change(tmp_path, monkeypatch):
    f = tmp_path / "a.yaml"
    f.write_text("k: 1\n", encoding="utf-8")
    calls = []
    real = YL.safe_load

    def counting(raw):
        calls.append(1)
        return real(raw)

    YL._MEMO.clear()
    monkeypatch.setattr(YL, "safe_load", counting)
    a = YL.safe_load_file(str(f))
    a["k"] = 99                                   # a caller mutating its copy ...
    assert YL.safe_load_file(str(f)) == {"k": 1}   # ... cannot poison the memo
    assert len(calls) == 1
    f.write_text("k: 22222\n", encoding="utf-8")  # size changes -> re-parse
    assert YL.safe_load_file(str(f)) == {"k": 22222}
    assert len(calls) == 2
