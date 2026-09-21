const { test: base, expect } = require('@playwright/test');
const { createHash } = require('node:crypto');

// Existing public-evidence assertions are Korean; default-language tests use the base fixture.
const test = base.extend({
  page: async ({ page }, use) => {
    await page.addInitScript(() => localStorage.setItem('skillops.dashboard.locale', 'ko'));
    await use(page);
  },
});

async function assertDashboardModules(request, origin) {
  const entry = await request.get(new URL('/', origin).href);
  expect(entry.ok()).toBe(true);
  const match = (await entry.text()).match(/src="\/(app\.[a-f0-9]{12}\.js)"/);
  expect(match).not.toBeNull();
  const pending = [match[1]], modules = new Set();
  while (pending.length) {
    const name = pending.pop();
    if (modules.has(name)) continue;
    modules.add(name);
    expect(modules.size).toBeLessThanOrEqual(6);
    const response = await request.get(new URL(`/${name}`, origin).href);
    expect(response.ok()).toBe(true);
    const bytes = await response.body();
    expect(name.split('.')[1]).toBe(createHash('sha256').update(bytes).digest('hex').slice(0, 12));
    for (const imported of bytes.toString().matchAll(/^import\b[^;]*?\bfrom\s*['"]\.\/([^'"]+)['"]/gm)) {
      expect(imported[1]).toMatch(/^(i18n|views|evolution|assessments|trace)\.[a-f0-9]{12}\.js$/);
      pending.push(imported[1]);
    }
  }
  expect([...modules].map(name => name.split('.')[0]).sort()).toEqual(['app', 'assessments', 'evolution', 'i18n', 'trace', 'views']);
}

module.exports = { test, expect, assertDashboardModules };
