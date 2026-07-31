"""tests/test_dashboards_decommission.py — converted from scripts/foundry_checks/
{dashboards-as-code, decommission-gate}.py.

`scripts/foundry-dashboard-fidelity.py` and `scripts/foundry-decommission.py` already carry their
own comprehensive, hermetic `--selftest` batteries (AC-DAC-1/-2, AC-DCG-1..4). Rather than
reimplement those fixtures, this module drives the REAL scripts' own selftests directly.
"""
from __future__ import annotations

import os
import subprocess
import sys

from conftest import REPO_ROOT


def test_dashboard_fidelity_selftest():
    script = os.path.join(REPO_ROOT, "scripts", "foundry-dashboard-fidelity.py")
    proc = subprocess.run([sys.executable, script, "--selftest"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "FOUNDRY-DASHBOARD-FIDELITY-SELFTEST-GREEN" in proc.stdout


def test_decommission_gate_selftest():
    script = os.path.join(REPO_ROOT, "scripts", "foundry-decommission.py")
    proc = subprocess.run([sys.executable, script, "--selftest"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "FOUNDRY-DECOMMISSION-SELFTEST-GREEN" in proc.stdout
