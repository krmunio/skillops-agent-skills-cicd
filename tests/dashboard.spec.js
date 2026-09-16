const { test, expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

async function pageWith(page, index, status = 200) {
  await page.route('http://dashboard.test/**', route => {
    const name = new URL(route.request().url()).pathname;
    if (name === '/results/index.json') {
      return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(index) });
    }
    const assets = { '/': 'index.html', '/styles.css': 'styles.css', '/app.js': 'app.js',
      '/views.js': 'views.js', '/sample-data.json': 'sample-data.json' };
    if (!assets[name]) return route.fallback();
    const contentType = name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css' :
      name.endsWith('.json') ? 'application/json' : 'text/html';
    return route.fulfill({ contentType, body: fs.readFileSync(path.join('dashboard', assets[name])) });
  });
  await page.goto('http://dashboard.test/');
}

async function comparisonPage(page, snapshots = null) {
  const run = {
    schema_version: 1, project_id: 'sample_repo', run_id: '123-1', purpose: 'comparison',
    created_at: '2026-09-15T05:00:00Z', origin: 'historical_import',
    guide: { status: 'not_assessed', reason_code: 'historical_import', metrics: null, decision: null },
    execution: { status: 'completed', reason_code: 'historical_import', decision: 'rejected',
      metrics: { base_requested: 5, candidate_requested: 5, base_correctness_successes: 5,
        candidate_correctness_successes: 5, base_judge_score: 100, candidate_judge_score: 100,
        base_cost_nano_aiu: 125.712, candidate_cost_nano_aiu: 127.772,
        base_elapsed_seconds: 296.064, candidate_elapsed_seconds: 302.923,
        cost_improvement_percent: -1.638665838106148, time_improvement_percent: -2.3167 } },
  };
  await page.route('http://dashboard.test/results/sample_repo/index.json', route => route.fulfill({
    json: { schema_version: 1, history: [{ ...run, execution_status: 'completed', guide_status: 'not_assessed',
      ...(snapshots ? { skill_id: snapshots.skill_id, skill_snapshots: '123-1/skill-snapshots.json',
        base_skill_sha256: snapshots.base.sha256, candidate_skill_sha256: snapshots.candidate?.sha256 } : {}) }] },
  }));
  if (snapshots) await page.route('http://dashboard.test/results/sample_repo/123-1/skill-snapshots.json',
    route => route.fulfill({ json: snapshots }));
  await page.route('http://dashboard.test/results/sample_repo/123-1/report.json', route => route.fulfill({ json: run }));
  await pageWith(page, { schema_version: 1, projects: [{
    id: 'sample_repo', state: 'active', history_count: 1, current_run: '999-1',
  }] });
  await expect(page.locator('#execution')).toContainText('후보 거절');
}

test('evaluation and skill changes precede history without inventing real evidence', async ({ page }) => {
  await comparisonPage(page);
  const order = await page.locator('[data-section]').evaluateAll(nodes => nodes.map(node => node.dataset.section));
  expect(order).toEqual(['quality', 'execution', 'changes', 'history']);
  await expect(page.locator('#quality-section')).toContainText('Anthropic');
  await expect(page.locator('#quality-section')).toContainText('APO');
  await expect(page.locator('#quality-section h3')).not.toContainText(['Anthropic', 'APO']);
  await expect(page.locator('#skill-changes')).toContainText('Skill 원문이 공개 기록에 없습니다');
  await expect(page.locator('#improvement-evidence')).toContainText('개선 근거가 공개 기록에 없습니다');
  await expect(page.locator('#freshness')).toContainText('현재 버전의 검증 근거가 아닙니다');
});

test('real comparison shows measured deltas, not fabricated policy or task detail', async ({ page }) => {
  await comparisonPage(page);
  await expect(page.locator('#cost-card')).toContainText('1.64%');
  await expect(page.locator('#cost-card')).toContainText('증가');
  await expect(page.locator('#time-card')).toContainText('2.32%');
  await expect(page.locator('#execution')).toContainText('5/5');
  await expect(page.locator('#decision-reasons')).toContainText('판정 정책이 공개 기록에 없습니다');
  await expect(page.locator('#task-results')).toContainText('작업별 상세 결과가 공개 기록에 없습니다');
});

test('sample view is opt-in and clears all synthetic evidence when returning to real data', async ({ page }) => {
  await comparisonPage(page);
  await expect(page.locator('#sample-banner')).toBeHidden();
  await page.getByRole('button', { name: '샘플 화면 보기', exact: true }).click();
  await expect(page.locator('#sample-banner')).toContainText('합성 데이터');
  await expect(page.locator('#project-title')).toContainText('샘플');
  await expect(page.locator('#guide')).toContainText('적용 제외');
  await expect(page.locator('#improvement-evidence')).toContainText('개선 가설');
  await expect(page.locator('#skill-changes pre').first()).toContainText('name: issue-helper');
  await page.getByRole('button', { name: '변경 비교', exact: true }).click();
  await expect(page.locator('#skill-diff')).toBeVisible();
  await expect(page.locator('#skill-diff')).toContainText('+');
  await page.getByRole('button', { name: /sample_repo/ }).click();
  await expect(page.locator('#sample-banner')).toBeHidden();
  await expect(page.locator('#skill-changes')).toContainText('Skill 원문이 공개 기록에 없습니다');
  await expect(page.locator('#skill-changes')).not.toContainText('name: issue-helper');
  await expect(page.locator('#history-count')).toHaveText('1건');
});

test('sample skill and run selection keep baseline and candidate evidence separate', async ({ page }) => {
  await pageWith(page, { schema_version: 1, projects: [] });
  await page.getByRole('button', { name: '샘플 화면 보기', exact: true }).click();
  await page.locator('#skill-select').selectOption('release-notes');
  await expect(page.locator('#skill-changes')).not.toContainText('issue-helper');
  await expect(page.locator('#execution')).toContainText('미평가');
  await page.locator('#skill-select').selectOption('issue-helper');
  await page.getByRole('tab', { name: '실행 이력', exact: true }).click();
  await page.locator('#history').getByRole('button', { name: /기준 평가/ }).click();
  await expect(page.locator('#skill-changes')).toContainText('후보 Skill이 없습니다');
  await expect(page.locator('#improvement-evidence')).toContainText('개선 근거가 공개 기록에 없습니다');
  await expect(page.locator('#cost-card')).not.toContainText('1.64%');
});

test('mobile layout remains within the viewport including sample code and history', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await pageWith(page, { schema_version: 1, projects: [] });
  await page.getByRole('button', { name: '샘플 화면 보기', exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.getByRole('tab', { name: '실행 이력', exact: true }).click();
  await expect(page.locator('#history')).toBeVisible();
});

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

test('sample instructions and diff render HTML-looking content as inert text', async ({ page }) => {
  await pageWith(page, { schema_version: 1, projects: [] });
  const sample = JSON.parse(fs.readFileSync('dashboard/sample-data.json', 'utf8'));
  const literal = '<img src=x onerror="window.sampleExecuted=true">';
  sample.skills[0].versions.v2.content += literal;
  await page.route('http://dashboard.test/sample-data.json', route => route.fulfill({ json: sample }));
  await page.getByRole('button', { name: '샘플 화면 보기', exact: true }).click();
  await expect(page.locator('#skill-changes pre').nth(1)).toContainText(literal);
  await page.getByRole('button', { name: '변경 비교', exact: true }).click();
  await expect(page.locator('#skill-diff')).toContainText(literal);
  await expect(page.locator('#skill-changes img')).toHaveCount(0);
  expect(await page.evaluate(() => window.sampleExecuted)).toBeUndefined();
});

test('late real-report responses cannot overwrite an active sample view', async ({ page }) => {
  await comparisonPage(page);
  let release;
  let arrived;
  const waiting = new Promise(resolve => { arrived = resolve; });
  const delayed = new Promise(resolve => { release = resolve; });
  await page.route('http://dashboard.test/results/sample_repo/123-1/report.json', async route => {
    arrived();
    await delayed;
    await route.fallback();
  });
  await page.getByRole('button', { name: '새로고침', exact: true }).click();
  await waiting;
  await page.getByRole('button', { name: '샘플 화면 보기', exact: true }).click();
  await expect(page.locator('#sample-banner')).toBeVisible();
  const response = page.waitForResponse('http://dashboard.test/results/sample_repo/123-1/report.json');
  release();
  await response;
  await expect(page.locator('#project-title')).toContainText('샘플 프로젝트');
  await expect(page.locator('#origin')).toHaveText('합성 샘플');
  await expect(page.locator('#history-count')).toHaveText('2건');
});

test('a zero baseline retains zero values and does not invent a percentage', async ({ page }) => {
  await pageWith(page, { schema_version: 1, projects: [] });
  const sample = JSON.parse(fs.readFileSync('dashboard/sample-data.json', 'utf8'));
  const metrics = sample.skills[0].reports[0].execution.metrics;
  metrics.base_cost_nano_aiu = 0;
  metrics.candidate_cost_nano_aiu = 1;
  metrics.cost_improvement_percent = null;
  await page.route('http://dashboard.test/sample-data.json', route => route.fulfill({ json: sample }));
  await page.getByRole('button', { name: '샘플 화면 보기', exact: true }).click();
  await expect(page.locator('#cost-card')).toContainText('변화율 미기록');
  await expect(page.locator('#cost-card .bar-value').first()).toHaveText('0');
});

test('malformed sample history is an explicit error rather than a blank detail view', async ({ page }) => {
  await pageWith(page, { schema_version: 1, projects: [] });
  const sample = JSON.parse(fs.readFileSync('dashboard/sample-data.json', 'utf8'));
  sample.skills[0].reports = null;
  await page.route('http://dashboard.test/sample-data.json', route => route.fulfill({ json: sample }));
  await page.getByRole('button', { name: '샘플 화면 보기', exact: true }).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.locator('#detail')).toBeHidden();
});

test('both sample Skill versions are visible without opening collapsed panels', async ({ page }) => {
  await pageWith(page, { schema_version: 1, projects: [] });
  await page.getByRole('button', { name: '샘플 화면 보기', exact: true }).click();
  await expect(page.getByText('As-Is · 기존 Skill 원문', { exact: true })).toBeVisible();
  await expect(page.getByText('To-Be · 후보 Skill 원문', { exact: true })).toBeVisible();
  await expect(page.locator('.source-panel[open]')).toHaveCount(2);
  await expect(page.locator('#skill-changes pre').nth(1)).toBeVisible();
});

test('project Skill history lists bound Skills and opens actual saved versions', async ({ page }) => {
  const digest = content => require('node:crypto').createHash('sha256').update(content).digest('hex');
  const base = '---\nname: develop\n---\nAS-IS saved instructions\n';
  const candidate = '---\nname: develop\n---\nTO-BE saved instructions\n';
  await comparisonPage(page, { schema_version: 1, project_id: 'sample_repo', run_id: '123-1',
    skill_id: 'develop', report_sha256: 'a'.repeat(64),
    base: { content: base, sha256: digest(base) },
    candidate: { content: candidate, sha256: digest(candidate) } });
  await page.getByRole('tab', { name: 'Skill 이력', exact: true }).click();
  await expect(page.locator('#skill-list')).toContainText('develop');
  await expect(page.locator('#skill-list')).toContainText('버전 2개');
  await page.locator('#skill-list').getByRole('button', { name: /develop/ }).click();
  await expect(page.locator('#skill-history-runs')).toContainText('후보 비교');
  await expect(page.locator('#skill-changes pre').first()).toContainText('AS-IS saved instructions');
  await expect(page.locator('#skill-changes pre').nth(1)).toContainText('TO-BE saved instructions');
  await expect(page.locator('#skill-changes')).not.toContainText('합성');
  await expect(page.locator('#origin')).toHaveText('과거 로컬 이력');
  await expect(page.locator('#guide')).toContainText('미평가');
  await page.getByRole('tab', { name: '실행 이력', exact: true }).click();
  await expect(page.locator('#history')).toBeVisible();
});

test('sample Skill history lists both Skills and supports keyboard tab navigation', async ({ page }) => {
  await pageWith(page, { schema_version: 1, projects: [] });
  await page.getByRole('button', { name: '샘플 화면 보기', exact: true }).click();
  await expect(page.locator('#skill-list .skill-item')).toHaveCount(2);
  await page.locator('#skill-list').getByRole('button', { name: /release-notes/ }).click();
  await expect(page.locator('#skill-select')).toHaveValue('release-notes');
  await expect(page.locator('#skill-changes pre')).toContainText('name: release-notes');
  await expect(page.locator('#skill-history-runs .skill-run')).toHaveCount(1);
  await page.getByRole('tab', { name: 'Skill 이력', exact: true }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('tab', { name: '실행 이력', exact: true })).toBeFocused();
  await expect(page.locator('#execution-history-panel')).toBeVisible();
});
