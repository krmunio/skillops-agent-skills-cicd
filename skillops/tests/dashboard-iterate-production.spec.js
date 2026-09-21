const { test, expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');

const origin = process.env.SKILLOPS_ITERATE_ORIGIN;
const evidence = process.env.SKILLOPS_ITERATE_EVIDENCE;
const backup = process.env.SKILLOPS_ITERATE_BACKUP;
const cycleId = 'local-20260917T145756Z-1f55e25f8cf6';
const roundIds = ['local-20260917T145756Z-5a17f0210563', 'local-20260917T145756Z-399bf04adcc9'];
const skill = 'path:90ae807bd3d394fc140a6df8';
const manifestHash = '7412a29735dc56db48a93a22e199a87fb4498d5e630a852a0ff347fc34e79905';
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const link = run => `/?${new URLSearchParams({ project: 'sample_repo', run, skill })}`;
const source = (run, file) => fs.readFileSync(path.join(evidence, 'n2-feedback', 'sample_repo', run, file));

test.beforeEach(() => {
  test.skip(!origin || !evidence, 'Set SKILLOPS_ITERATE_ORIGIN and SKILLOPS_ITERATE_EVIDENCE to the reviewed PR34 package.');
  const address = new URL(origin);
  expect(['http:', 'https:']).toContain(address.protocol);
  expect(['127.0.0.1', 'localhost', '[::1]']).toContain(address.hostname);
  expect(digest(fs.readFileSync(path.join(evidence, 'manifest.json')))).toBe(manifestHash);
});

async function open(page, run) {
  await page.goto(new URL(link(run), origin).href);
  await expect(page.locator('#detail')).toBeVisible();
  await expect(page.locator('#error')).toBeHidden();
  await expect(page.locator('#evidence-trace .trace-round')).toHaveCount(2);
  await expect(page.locator('#trace-error')).toHaveCount(0);
  await expect(page.locator('#skill-select')).toHaveValue(skill);
  await expect(page.locator('#report-link')).toHaveAttribute('href', `/results/sample_repo/${run}/report.json`);
}

async function capture(page, name, section) {
  if (!backup) return;
  fs.mkdirSync(backup, { recursive: true });
  await page.locator(section).screenshot({ path: path.join(backup, `${name}.png`) });
}

test('official entrypoint resolves five content-hashed modules without unbundled fallbacks', async ({ request }) => {
  const entry = await request.get(new URL('/', origin).href);
  expect(entry.ok()).toBe(true);
  const match = (await entry.text()).match(/src="\/(app\.[a-f0-9]{12}\.js)"/);
  expect(match).not.toBeNull();
  const pending = [match[1]], seen = new Set();
  while (pending.length) {
    const name = pending.pop();
    if (seen.has(name)) continue;
    expect(seen.size).toBeLessThan(5);
    seen.add(name);
    const response = await request.get(new URL(`/${name}`, origin).href);
    expect(response.ok()).toBe(true);
    const body = await response.body();
    expect(name.split('.')[1]).toBe(digest(body).slice(0, 12));
    for (const imported of body.toString('utf8').matchAll(/^import\b[^;]*?\bfrom\s*['"]\.\/([^'"]+)['"]/gm)) {
      expect(imported[1]).toMatch(/^(views|evolution|assessments|trace)\.[a-f0-9]{12}\.js$/);
      pending.push(imported[1]);
    }
  }
  expect([...seen].map(name => name.split('.')[0]).sort()).toEqual(['app', 'assessments', 'evolution', 'trace', 'views']);
});

test('PR34 N=2 serves the exact producer bytes and complete transitive references', async ({ request }) => {
  const cycle = JSON.parse(source(cycleId, 'cycle.json'));
  expect(cycle.execution_mode).toBe('offline_test');
  expect(cycle.max_rounds).toBe(2);
  expect(cycle.rounds.map(round => round.run_id)).toEqual(roundIds);
  expect(cycle.confirmation_ref).toBeNull();
  expect(cycle.confirmation_status).toBe('unverified');
  expect(digest(source(cycleId, 'cycle.json'))).toBe('d1985e76bc69928552e20ee6505b8a762903bdeaf825431aa2a1432cbebe50ea');
  const indexResponse = await request.get(new URL('/results/sample_repo/index.json', origin).href);
  expect(indexResponse.ok()).toBe(true);
  const history = (await indexResponse.json()).history;
  for (const run of [cycleId, ...roundIds]) {
    const row = history.find(item => item.run_id === run);
    expect(row).toBeDefined();
    const files = run === cycleId ? ['report.json', 'cycle.json'] :
      ['report.json', 'replay-evaluation.json', 'skill-evolution.json'];
    if (run === cycleId) expect(row.cycle).toBe(`${run}/cycle.json`);
    else {
      expect(row.replay_evaluation).toBe(`${run}/replay-evaluation.json`);
      expect(row.skill_evolution).toBe(`${run}/skill-evolution.json`);
      expect(row.evolution_skills.map(item => item.skill_key)).toContain(skill);
    }
    expect(row.adoption).toBeUndefined();
    for (const file of files) {
      const response = await request.get(new URL(`/results/sample_repo/${run}/${file}`, origin).href);
      expect(response.ok(), `${run}/${file}`).toBe(true);
      expect(digest(await response.body()), `${run}/${file}`).toBe(digest(source(run, file)));
    }
  }
  for (const [index, round] of cycle.rounds.entries()) {
    const raw = source(round.run_id, 'replay-evaluation.json');
    const replay = JSON.parse(raw);
    expect(round.evaluation_ref.sha256).toBe(digest(raw));
    expect(replay.report_sha256).toBe(digest(source(round.run_id, 'report.json')));
    expect(replay.reference.reference_sha256).toBe(cycle.reference_sha256);
    expect(replay.reference.input_sha256).toBe(cycle.input_sha256);
    expect(replay.evaluation.base_version_id).toBe(cycle.original_version_id);
    expect(replay.generation.parent_version_id).toBe(round.parent_version_id);
    expect(replay.generation.feedback_sha256).toBe(round.feedback_sha256);
    expect(round.parent_version_id).toBe(index ? cycle.rounds[0].candidate_version_id : cycle.original_version_id);
    expect(round.feedback_source_round_id).toBe(index ? `${cycleId}-r1` : null);
    expect(round.decision.status).toBe(index ? 'improved' : 'not_improved');
  }
});

test('PR34 N=2 cycle validates in the browser and remains offline/unconfirmed after refresh', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const unexpected = [];
  page.on('request', request => {
    if (new URL(request.url()).origin !== new URL(origin).origin || request.method() !== 'GET') {
      unexpected.push(request.url());
    }
  });
  await open(page, cycleId);
  const panel = page.locator('#evidence-trace');
  await expect(panel).toContainText('offline_test');
  await expect(panel).toContainText('confirmation: unverified');
  await expect(panel).toContainText('최종 확인 미완료');
  await expect(panel).toContainText('승인·사용: 미기록');
  await expect(panel).toContainText('현재 로컬 Active: 공개 근거로 확인 불가');
  const cycle = JSON.parse(source(cycleId, 'cycle.json'));
  for (const [index, round] of cycle.rounds.entries()) {
    const article = panel.locator('.trace-round').nth(index);
    await expect(article).toContainText(round.parent_version_id);
    await expect(article).toContainText(round.candidate_version_id);
    await expect(article).toContainText(round.decision.status);
    await expect(article.getByRole('link')).toHaveAttribute('href', link(round.run_id));
  }
  await capture(page, '01-n2-cycle-offline-unconfirmed', '#evidence-trace');
  await page.reload();
  await expect(panel.locator('.trace-round')).toHaveCount(2);
  await expect(page.locator('#skill-select')).toHaveValue(skill);
  expect(new URL(page.url()).searchParams.get('run')).toBe(cycleId);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  expect(unexpected).toEqual([]);
});

test('PR34 round links retain the original comparison and then open the fixed real reports', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await open(page, cycleId);
  for (const [index, run] of roundIds.entries()) {
    await page.locator(`.trace-round[data-round="${index + 1}"]`).getByRole('link').click();
    await expect(page.locator('#report-link')).toHaveAttribute('href', `/results/sample_repo/${run}/report.json`);
    await expect(page.locator('#quality-summary')).toContainText('offline_test');
    await expect(page.locator('#trace-error')).toHaveCount(0);
    await expect(page.locator('#skill-changes')).toContainText(`Attempt ${index + 1}.`);
    await expect(page.locator('#evidence-trace .trace-round')).toHaveCount(2);
    await capture(page, `02-round-${index + 1}-quality`, '#quality-section');
    await capture(page, `03-round-${index + 1}-execution`, '#execution-section');
    await capture(page, `04-round-${index + 1}-changes`, '#changes-section');
  }
  for (const [project, run, legacySkill] of [
    ['sample_repo', '35184191312-1', 'auto:6a35463f3754402e8e4c78163c180479'],
    ['project-a', '35203851697-1', 'path:db18433c1af9c5cacc3d5e2d'],
  ]) {
    await page.goto(new URL(`/?${new URLSearchParams({ project, run, skill: legacySkill })}`, origin).href);
    await expect(page.locator('#report-link')).toHaveAttribute('href', `/results/${project}/${run}/report.json`);
    await expect(page.locator('#evidence-trace .trace-round')).toHaveCount(0);
    await expect(page.locator('#trace-error')).toHaveCount(0);
    if (project === 'sample_repo') await expect(page.locator('#task-results')).toContainText('코드 출력 해시 동일');
    else await expect(page.locator('#decision-reasons')).toContainText('unsupported_dependencies');
  }
});

test('official build rejects unknown exact links without replacing them with a successful run', async ({ page }) => {
  for (const query of [
    'project=sample_repo&run=99999999999-1',
    `project=sample_repo&run=${cycleId}&skill=path%3Aunknown`,
    `project=unknown-project&run=${cycleId}`,
  ]) {
    await page.goto(new URL(`/?${query}`, origin).href);
    await expect(page.locator('#error')).toBeVisible();
    await expect(page.locator('#detail')).toBeHidden();
    expect(new URL(page.url()).search).toBe(`?${query}`);
  }
});

test('PR34 screenshot backup and preserved real-report backup open without a server', async ({ page }) => {
  test.skip(!backup, 'Set SKILLOPS_ITERATE_BACKUP to the separately prepared producer screenshot gallery.');
  const requests = [];
  page.on('request', request => requests.push(request.url()));
  await page.goto(require('node:url').pathToFileURL(path.join(backup, 'index.html')).href);
  await expect(page).toHaveTitle('SkillOps — N=2 offline_test 임시 화면 백업');
  await expect(page.locator('.boundary')).toContainText('공식 빌드 통합 미검증');
  await expect(page.locator('img')).toHaveCount(7);
  expect(await page.locator('img').evaluateAll(images => images.every(image => image.complete && image.naturalWidth > 0))).toBe(true);
  await page.getByRole('link', { name: '기존 실제 보고서 백업으로 이동' }).click();
  await expect(page).toHaveTitle('SkillOps — 사전 보고서 스크린샷 백업');
  await expect(page.locator('img')).toHaveCount(7);
  expect(await page.locator('img').evaluateAll(images => images.every(image => image.complete && image.naturalWidth > 0))).toBe(true);
  expect(requests.every(url => url.startsWith('file:'))).toBe(true);
});
