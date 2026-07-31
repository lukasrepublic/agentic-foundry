// tests/fixtures/certify/certify.spec.js — the certify-local fixture proof's 2-journey suite
// (tests/test_certify_fixture.py). Classic Playwright title-tag convention: `--grep` matches
// against the full test TITLE, so a tag lives in the title string, not a separate API.
const { test, expect } = require("@playwright/test");

test("home page renders @FIX-1", async ({ page }) => {
  await page.goto("/index.html");
  await expect(page.locator("#hello")).toHaveText("hello certify-local fixture");
});

test("navigates to the second page @FIX-2", async ({ page }) => {
  await page.goto("/index.html");
  await page.click("#go");
  await expect(page.locator("#second")).toHaveText("second page reached");
});
