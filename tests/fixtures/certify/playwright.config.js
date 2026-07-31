// tests/fixtures/certify/playwright.config.js — the certify-local fixture proof's own Playwright
// config (tests/test_certify_fixture.py). `FIXTURE_PORT` is set by the test at run time to match
// the fixture stack profile's `app_exercise_binding.boot` (a plain `python3 -m http.server <port>`
// serving this same directory) — never a hardcoded port, so parallel test runs never collide.
module.exports = {
  testDir: __dirname,
  use: {
    baseURL: `http://127.0.0.1:${process.env.FIXTURE_PORT || "8199"}`,
  },
};
