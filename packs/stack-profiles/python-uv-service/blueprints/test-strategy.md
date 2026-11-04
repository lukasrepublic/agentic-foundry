# python-uv-service test strategy

What each test lane proves for this blueprint; the exact commands live in `stack-profile.yaml` (`test_recipe`).

- Unit: domain layer, no I/O (`tests/unit`, coverage gate from the profile).
- Integration: data layer against a real (containerised) dependency (`tests/integration`).
- E2E: boot the service and exercise the health surface and one golden path (`tests/e2e`).
- Contract: the OpenAPI schema is diffed in CI; breaking changes need a version bump.
