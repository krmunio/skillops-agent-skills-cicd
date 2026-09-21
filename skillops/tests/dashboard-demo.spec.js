const { test, expect } = require('@playwright/test');
const path = require('node:path');
const fs = require('node:fs');

const origin = process.env.SKILLOPS_DEMO_ORIGIN;
const backup = process.env.SKILLOPS_DEMO_BACKUP;
for (const [project, run, skill, prefix] of [
  ['sample_repo', '35184191312-1', 'auto:6a35463f3754402e8e4c78163c180479', '01-sample-repo'],
  ['project-a', '35203851697-1', 'path:db18433c1af9c5cacc3d5e2d', '02-project-a'],
]) {
  test(`local static demo: ${project}/${run}`, async ({ page }) => {
    test.skip(!origin, 'Set SKILLOPS_DEMO_ORIGIN to the prepared loopback-only static demo.');
    const address = new URL(origin);
    expect(['127.0.0.1', 'localhost', '[::1]']).toContain(address.hostname);
    const unexpected = [], methods = [];
    page.on('request', request => {
      methods.push(request.method());
      if (new URL(request.url()).origin !== address.origin) unexpected.push(request.url());
    });
    await page.setViewportSize({ width: 1440, height: 1000 });
    const query = new URLSearchParams({ project, run, skill });
    await page.goto(`${address.origin}/?${query}`);
    await expect(page.locator('#detail')).toBeVisible();
    await expect(page.locator('#error')).toBeHidden();
    await expect(page.locator('#trace-error')).toHaveCount(0);
    await expect(page.locator('#report-link')).toHaveAttribute('href', `/results/${project}/${run}/report.json`);
    await expect(page.locator('#skill-select')).toHaveValue(skill);
    await expect(page.locator('#evidence-trace')).toContainText('승인·사용: 미기록');
    if (project === 'sample_repo') {
      await expect(page.locator('#task-results')).toContainText('코드 출력 해시 동일');
      await expect(page.locator('#time-card')).toContainText('증가');
    } else {
      await expect(page.locator('#execution')).toContainText('검증 불충분');
      await expect(page.locator('#decision-reasons')).toContainText('unsupported_dependencies');
    }
    await page.reload();
    await expect(page.locator('#detail')).toBeVisible();
    await expect(page.locator('#report-link')).toHaveAttribute('href', `/results/${project}/${run}/report.json`);
    if (backup) {
      fs.mkdirSync(backup, { recursive: true });
      for (const section of ['quality-section', 'execution-section', 'changes-section', 'evidence-trace']) {
        await page.locator(`#${section}`).screenshot({ path: path.join(backup, `${prefix}-${section}.png`) });
      }
    }
    expect(unexpected).toEqual([]);
    expect(new Set(methods)).toEqual(new Set(['GET']));
  });
}

test('local static demo keeps an invalid exact link visibly invalid', async ({ page }) => {
  test.skip(!origin, 'Set SKILLOPS_DEMO_ORIGIN to the prepared loopback-only static demo.');
  const address = new URL(origin);
  expect(['127.0.0.1', 'localhost', '[::1]']).toContain(address.hostname);
  await page.goto(`${address.origin}/?project=sample_repo&run=99999999999-1`);
  await expect(page.locator('#error')).toBeVisible();
  await expect(page.locator('#detail')).toBeHidden();
  if (backup) {
    fs.mkdirSync(backup, { recursive: true });
    await page.screenshot({ path: path.join(backup, '03-invalid-link.png'), fullPage: true });
  }
});

test('screenshot backup opens without a server or external requests', async ({ page }) => {
  test.skip(!backup, 'Set SKILLOPS_DEMO_BACKUP to the prepared screenshot gallery.');
  const requests = [];
  page.on('request', request => requests.push(request.url()));
  await page.goto(require('node:url').pathToFileURL(path.join(backup, 'index.html')).href);
  await expect(page).toHaveTitle('SkillOps — 사전 보고서 스크린샷 백업');
  await expect(page.locator('img')).toHaveCount(7);
  expect(await page.locator('img').evaluateAll(images => images.every(image => image.complete && image.naturalWidth > 0))).toBe(true);
  expect(requests.every(url => url.startsWith('file:'))).toBe(true);
});
