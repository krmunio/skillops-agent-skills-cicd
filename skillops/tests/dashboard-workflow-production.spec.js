const { test, expect, assertDashboardModules } = require('./dashboard-public-harness');
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');

// Read-only checks: use the reviewed PR38 packet with a separately built/deployed site.
const origin = process.env.SKILLOPS_WORKFLOW_ORIGIN;
const evidence = process.env.SKILLOPS_WORKFLOW_EVIDENCE;
const capture = process.env.SKILLOPS_WORKFLOW_CAPTURE;
const skill = 'path:90ae807bd3d394fc140a6df8';
const examples = [
  ['local-20260918T094535Z-b9a681f6cfb2', 'unverified', '미검증'],
  ['local-20260918T094533Z-f6297a587afc', 'passed', '통과'],
  ['local-20260918T094534Z-bcba9c0a589e', 'failed', '실패'],
];
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const source = (run, file) => fs.readFileSync(path.join(evidence, 'public-results', 'sample_repo', run, file));

test.beforeEach(() => {
  test.skip(!origin || !evidence, 'Set SKILLOPS_WORKFLOW_ORIGIN and SKILLOPS_WORKFLOW_EVIDENCE to the site and reviewed b8dade4 packet.');
  expect(['http:', 'https:']).toContain(new URL(origin).protocol);
  expect(hash(fs.readFileSync(path.join(evidence, 'manifest.json'))))
    .toBe('78040e9e5f48d69c9f2026f533724e62c0be47cd7a7f7d9904f35ab93db0ea2f');
});

test('official workflow entry uses hashed modules and preserves every reviewed public payload byte', async ({ request }) => {
  await assertDashboardModules(request, origin);
  const root = path.join(evidence, 'public-results');
  for (const project of fs.readdirSync(root, { withFileTypes: true }).filter(item => item.isDirectory())) {
    const directory = path.join(root, project.name);
    for (const run of fs.readdirSync(directory, { withFileTypes: true }).filter(item => item.isDirectory())) {
      for (const file of fs.readdirSync(path.join(directory, run.name))) {
        const relative = `${project.name}/${run.name}/${file}`;
        const response = await request.get(new URL(`/results/${relative}`, origin).href);
        expect(response.ok(), relative).toBe(true);
        expect(hash(await response.body()), relative).toBe(hash(fs.readFileSync(path.join(root, relative))));
      }
    }
  }
});

for (const [run, status, label] of examples) {
  test(`confirmed offline example ${status} remains explicit through rounds, confirmation and refresh`, async ({ page }) => {
    const unexpected = [];
    page.on('request', request => {
      if (request.method() !== 'GET' || new URL(request.url()).origin !== new URL(origin).origin) unexpected.push(request.url());
    });
    await page.goto(new URL('/', origin).href);
    await expect(page.locator('#skill-select')).toBeEnabled();
    await page.locator('#offline-examples summary').click();
    await page.locator(`#offline-examples a[href*="run=${run}&"]`).click();
    await expect(page.locator('#trace-mode')).toContainText('offline_test');
    await expect(page.locator('#skill-progress [data-stage="confirmation"] .stage-status')).toContainText(label);
    await expect(page.locator('#evidence-trace')).toContainText(`confirmation: ${status}`);
    await expect(page.locator('#evidence-trace')).not.toContainText('승인 대기');
    if (status === 'passed') await expect(page.locator('#skill-progress [data-stage="approval"]')).toContainText('승인 불가');
    await expect(page.locator('#evidence-trace')).toContainText('승인·사용: 미기록');
    await expect(page.locator('#trace-error')).toHaveCount(0);
    await expect(page.locator('#skill-select')).toHaveValue(skill);
    await page.reload();
    await expect(page.locator('#trace-mode')).toContainText('offline_test');
    expect(new URL(page.url()).searchParams.get('run')).toBe(run);
    await page.setViewportSize({ width: 320, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
    if (capture) {
      fs.mkdirSync(capture, { recursive: true });
      await page.locator('#evidence-trace').screenshot({ path: path.join(capture, `${status}.png`) });
    }
    const cycle = JSON.parse(source(run, 'cycle.json'));
    expect(cycle.execution_mode).toBe('offline_test');
    expect(cycle.max_rounds).toBe(2);
    await expect(page.locator('.trace-round')).toHaveCount(2);
    for (const round of cycle.rounds) {
      await page.locator(`.trace-round[data-round="${round.round_number}"] a`).click();
      await expect(page.locator('#report-link')).toHaveAttribute('href', `/results/sample_repo/${round.run_id}/report.json`);
      await expect(page.locator('#trace-mode')).toContainText('offline_test');
      await expect(page.locator('#trace-error')).toHaveCount(0);
    }
    await page.getByRole('link', { name: '별도 최종 확인 근거' }).click();
    await expect(page.locator('#report-link')).toHaveAttribute('href', `/results/sample_repo/${cycle.confirmation_ref.run_id}/report.json`);
    await expect(page.locator('#trace-error')).toHaveCount(0);
    await expect(page.locator('#evidence-trace')).toContainText(`confirmation: ${status}`);
    expect(unexpected).toEqual([]);
  });
}

test('default selection does not open offline examples and legacy measured routes remain exact', async ({ page }) => {
  await page.goto(new URL('/?project=sample_repo', origin).href);
  await expect(page.locator('#detail')).toBeVisible();
  expect(new URL(page.url()).searchParams.get('run')).not.toMatch(/^local-20260918/);
  await expect(page.locator('#trace-mode')).toHaveText('새 실행 방식: 미기록');
  for (const [project, run, key, selector, text] of [
    ['sample_repo', '35184191312-1', 'auto:6a35463f3754402e8e4c78163c180479', '#task-results', '코드 출력 해시 동일'],
    ['project-a', '35203851697-1', 'path:db18433c1af9c5cacc3d5e2d', '#decision-reasons', 'unsupported_dependencies'],
  ]) {
    const query = new URLSearchParams({ project, run, skill: key });
    await page.goto(new URL(`/?${query}`, origin).href);
    await expect(page.locator('#report-link')).toHaveAttribute('href', `/results/${project}/${run}/report.json`);
    await expect(page.locator(selector)).toContainText(text);
    await expect(page.locator('#skill-progress li')).toHaveCount(5);
    await expect(page.locator('#trace-error')).toHaveCount(0);
  }
});

test('an actually unevaluated detected Skill keeps all stages and local guidance after refresh', async ({ page }) => {
  const key = 'path:096d1d195c864df34280ab5e';
  const query = new URLSearchParams({ project: 'project-b', skill: key });
  await page.goto(new URL(`/?${query}`, origin).href);
  for (const reload of [false, true]) {
    if (reload) await page.reload();
    await expect(page.locator('#skill-select')).toHaveValue(key);
    await expect(page.locator('#detail')).toBeHidden();
    await expect(page.locator('#skill-progress .stage-status')).toHaveText(Array(5).fill('공개 기록 없음'));
    await expect(page.locator('#approval-guidance')).toContainText('승인은 로컬 CLI에서 수행합니다');
    expect(new URL(page.url()).search).toBe(`?${query}`);
  }
  await page.setViewportSize({ width: 320, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
  if (capture) {
    fs.mkdirSync(capture, { recursive: true });
    await page.locator('#evidence-trace').screenshot({ path: path.join(capture, 'unevaluated.png') });
  }
});

test('unknown exact public route keeps unverified progression without successful fallback', async ({ page }) => {
  const query = new URLSearchParams({ project: 'sample_repo', run: '99999999999-1', skill });
  await page.goto(new URL(`/?${query}`, origin).href);
  await expect(page.locator('#error')).toBeVisible();
  await expect(page.locator('#detail')).toBeHidden();
  await expect(page.locator('#skill-progress .stage-status')).toHaveText(Array(5).fill('근거 확인 실패 · 미검증'));
  expect(new URL(page.url()).search).toBe(`?${query}`);
});
