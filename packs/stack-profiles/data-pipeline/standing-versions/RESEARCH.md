# data-pipeline pin research trail (gv-08)

Retrieved 2026-11-04 from the registry JSON API (`info.version` = current stable). Previous = the version the sibling python profiles pinned in their July research (2026-07-28); this profile is new, so there is no prior pin of its own.

| subject | pin | source (vendor / registry URL) | retrieved | previous version |
|---|---|---|---|---|
| cpython (line) | 3.13 | https://www.python.org/downloads/ | 2026-11-04 | 3.13 |
| python_requires | >=3.11 | (floor, matches siblings) | 2026-11-04 | >=3.11 |
| uv | 0.13.7 | https://pypi.org/pypi/uv/json | 2026-11-04 | 0.11.33 |
| ruff | 0.17.3 | https://pypi.org/pypi/ruff/json | 2026-11-04 | 0.16.0 |
| mypy | 2.4.1 | https://pypi.org/pypi/mypy/json | 2026-11-04 | 2.3.0 |
| pytest | 9.2.0 | https://pypi.org/pypi/pytest/json | 2026-11-04 | 9.1.1 |
| pytest-cov | 7.1.0 | https://pypi.org/pypi/pytest-cov/json | 2026-11-04 | 7.1.0 |

## Follow-up (not done here)

`python-uv-service` and `python-uv-lib` still carry the July 2026 pins (uv 0.11.33, ruff 0.16.0, mypy 2.3.0, pytest 9.1.1). Per the deliberate-upgrade rule they are NOT bumped in this atom; the skew against the pins above is a recorded follow-up upgrade.
