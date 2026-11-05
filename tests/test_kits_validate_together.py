"""Every shipped kit loads and validates together; no rule id is claimed by two kits."""
import collections
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
KITS = ROOT / "governance" / "kits"


def test_all_shipped_kits_validate_with_zero_findings(tmp_path):
    r = subprocess.run([sys.executable, str(ROOT / "scripts/foundry_governance.py"),
                        "--plugin-root", str(ROOT), "--project-dir", str(tmp_path), "validate"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "governance-valid" in r.stdout


def test_no_duplicate_rule_ids_across_kits():
    owners = collections.defaultdict(list)
    dirs = sorted(p for p in KITS.iterdir() if p.is_dir())
    assert len(dirs) >= 10
    for d in dirs:
        for rule in yaml.safe_load((d / "rules.yaml").read_text())["rules"]:
            owners[rule["id"]].append(d.name)
    dups = {i: k for i, k in owners.items() if len(k) > 1}
    assert dups == {}, dups

