# python-uv-lib test strategy

What each test lane proves for this blueprint; the exact commands live in `stack-profile.yaml` (`test_recipe`).

- Unit: every public function, behaviour-first, coverage gate from the profile.
- Integration: the documented usage examples run as tests.
- No e2e boot: a library has no runtime surface; consumers' contract tests are the e2e analogue.
- Property tests for any parser or serialiser on the public surface.
