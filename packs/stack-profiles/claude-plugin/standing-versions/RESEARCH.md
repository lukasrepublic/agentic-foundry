# claude-plugin pin research trail (gv-08)

Retrieved 2026-11-04. Only what this repository actually uses is pinned.

| subject | pin | source (vendor / registry URL) | retrieved | previous version |
|---|---|---|---|---|
| cpython (line) | 3.13 | https://www.python.org/downloads/ | 2026-11-04 | n/a (new profile) |
| pytest | 9.2.0 | https://pypi.org/pypi/pytest/json | 2026-11-04 | 9.1.1 (this repo's requirements-dev.txt) |
| PyYAML | 6.0.3 | https://pypi.org/pypi/pyyaml/json | 2026-11-04 | 6.0.3 (requirements.txt) |
| jsonschema | 4.26.0 | https://pypi.org/pypi/jsonschema/json | 2026-11-04 | 4.25.0 (requirements.txt) |
| nodejs-lts | 26.13.0 | https://nodejs.org/dist/index.json (first `lts` entry, "Lithium", dated 2026-10-28) | 2026-11-04 | n/a (cli engines floor is >=22.0.0) |

## Follow-up (not done here)

The repository itself still runs pytest 9.1.1 and jsonschema 4.25.0 (requirements*.txt, cli/python-requirements.json); the blueprint pins record the current vendor stable, so the skew is a follow-up upgrade of the repo, not of this atom. `python-uv-service` and `python-uv-lib` likewise keep their July 2026 pins (deliberate-upgrade rule).
