const { test, expect } = require('./dashboard-public-harness');
const fs = require('node:fs');
const path = require('node:path');

// Opt-in, read-only check against an exported, pre-run public results snapshot.
const results = process.env.SKILLOPS_PUBLIC_RESULTS;
for (const [project, run, skill] of [
  ['sample_repo', '35184191312-1', 'auto:6a35463f3754402e8e4c78163c180479'],
  ['project-a', '35203851697-1', 'path:db18433c1af9c5cacc3d5e2d'],
]) {
  test(`published pre-run evidence: ${project}/${run}`, async ({ page }) => {
    test.skip(!results, 'Set SKILLOPS_PUBLIC_RESULTS to a reviewed public results snapshot; no live calls.');
    const requests = [];
    await page.route('https://dashboard.test/**', route => {
      const request = route.request(), pathname = new URL(request.url()).pathname;
      requests.push(request.method());
      const root = path.resolve(pathname.startsWith('/results/') ? results : 'dashboard');
      const relative = pathname.startsWith('/results/') ? pathname.slice('/results/'.length) :
        pathname === '/' ? 'index.html' : pathname.slice(1);
      const file = path.resolve(root, relative);
      if (!file.startsWith(root + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
        return route.fulfill({ status: 404, body: 'Not found' });
      }
      const contentType = file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' :
        file.endsWith('.json') ? 'application/json' : 'text/html';
      return route.fulfill({ contentType, body: fs.readFileSync(file) });
    });
    const query = new URLSearchParams({ project, run, skill });
    await page.goto(`https://dashboard.test/?${query}`);
    await expect(page.locator('#detail')).toBeVisible();
    await expect(page.locator('#error')).toBeHidden();
    await expect(page.locator('#trace-error')).toHaveCount(0);
    await expect(page.locator('#report-link')).toHaveAttribute('href', `/results/${project}/${run}/report.json`);
    await expect(page.locator('#evidence-trace')).toContainText('승인·사용: 미기록');
    await expect(page.locator('#skill-select')).toHaveValue(skill);
    if (project === 'sample_repo') {
      await expect(page.locator('#task-results')).toContainText('코드 출력 해시 동일');
      await expect(page.locator('#execution')).toContainText('품질 점수 상승');
      await expect(page.locator('#time-card')).toContainText('증가');
    } else {
      await expect(page.locator('#execution')).toContainText('검증 불충분');
      await expect(page.locator('#decision-reasons')).toContainText('unsupported_dependencies');
    }
    await page.reload();
    await expect(page.locator('#report-link')).toHaveAttribute('href', `/results/${project}/${run}/report.json`);
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    expect(new Set(requests)).toEqual(new Set(['GET']));
  });
}
