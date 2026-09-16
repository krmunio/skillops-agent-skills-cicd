const { test, expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

async function pageWith(page, index, status = 200) {
  await page.route('http://dashboard.test/**', route => {
    const name = new URL(route.request().url()).pathname;
    if (name === '/results/index.json') {
      return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(index) });
    }
    const assets = { '/': 'index.html', '/styles.css': 'styles.css', '/app.js': 'app.js' };
    if (!assets[name]) return route.fallback();
    const contentType = name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css' : 'text/html';
    return route.fulfill({ contentType, body: fs.readFileSync(path.join('dashboard', assets[name])) });
  });
  await page.goto('http://dashboard.test/');
}

test('an empty catalog is not a fabricated success', async ({ page }) => {
  await pageWith(page, { schema_version: 1, projects: [] });
  await expect(page.getByText('등록된 프로젝트가 없습니다.')).toBeVisible();
});

test('failed result retrieval is visible', async ({ page }) => {
  await pageWith(page, {}, 503);
  await expect(page.getByRole('alert')).toContainText('이력을 불러오지 못했습니다');
});

test('shows project history and clearly labels unassessed guide data', async ({ page }) => {
  await page.route('http://dashboard.test/results/sample_repo/index.json', route => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ schema_version: 1, history: [{
      run_id: '123-1', created_at: '2026-09-16T05:00:00Z', origin: 'historical_import',
      purpose: 'baseline', guide_status: 'not_assessed', execution_status: 'completed',
      report: '123-1/report.json',
    }] }),
  }));
  await page.route('http://dashboard.test/results/sample_repo/123-1/report.json', route => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({
      schema_version: 1, project_id: 'sample_repo', run_id: '123-1', purpose: 'baseline',
      created_at: '2026-09-16T05:00:00Z', origin: 'historical_import',
      guide: { status: 'not_assessed', reason_code: 'historical_import', metrics: null, decision: null },
      execution: { status: 'completed', reason_code: 'historical_import', metrics: { requested: 5 }, decision: null },
    }),
  }));
  await pageWith(page, { schema_version: 1, projects: [{
    id: 'sample_repo', state: 'active', history_count: 1, current_run: null,
    index: 'sample_repo/index.json',
  }] });
  await expect(page.getByRole('button', { name: /sample_repo/ })).toBeVisible();
  await expect(page.locator('#guide')).toContainText('미평가');
  await expect(page.locator('#execution')).toContainText('요청 작업');
  await expect(page.locator('body')).not.toContainText('공식 인증 완료');
});
