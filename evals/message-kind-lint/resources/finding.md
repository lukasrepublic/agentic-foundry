FINDING: the nightly job leaks a 500 on empty input.
evidence: src/jobs/nightly.py:42 raises KeyError when `payload` is `{}`.
