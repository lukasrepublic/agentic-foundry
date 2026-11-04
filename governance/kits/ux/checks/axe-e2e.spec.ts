// FG-UX-001: run axe on key pages in the e2e suite (Playwright + @axe-core/playwright). Pin versions per standing-versions.
import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

for (const path of ['/', '/login']) {
  test(`no detectable WCAG 2.2 AA violations on ${path}`, async ({ page }) => {
    await page.goto(path);
    const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag22aa']).analyze();
    expect(results.violations).toEqual([]);
  });
}
