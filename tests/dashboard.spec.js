const { test, expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

async function pageWith(page, index, status = 200, origin = 'http://dashboard.test') {
  await page.route(`${origin}/**`, route => {
    const name = new URL(route.request().url()).pathname;
    if (name === '/results/index.json') {
      return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(index) });
    }
    const assets = { '/': 'index.html', '/styles.css': 'styles.css', '/app.js': 'app.js',
      '/views.js': 'views.js', '/evolution.js': 'evolution.js', '/assessments.js': 'assessments.js',
      '/sample-data.json': 'sample-data.json' };
    if (!assets[name]) return route.fallback();
    const contentType = name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css' :
      name.endsWith('.json') ? 'application/json' : 'text/html';
    return route.fulfill({ contentType, body: fs.readFileSync(path.join('dashboard', assets[name])) });
  });
  await page.goto(`${origin}/`);
}

async function comparisonPage(page, snapshots = null, metrics = {}) {
  const run = {
    schema_version: 1, project_id: 'sample_repo', run_id: '123-1', purpose: 'comparison',
    created_at: '2026-09-15T05:00:00Z', origin: 'historical_import',
    guide: { status: 'not_assessed', reason_code: 'historical_import', metrics: null, decision: null },
    execution: { status: 'completed', reason_code: 'historical_import', decision: 'rejected',
      metrics: { base_requested: 5, candidate_requested: 5, base_correctness_successes: 5,
        candidate_correctness_successes: 5, base_judge_score: 100, candidate_judge_score: 100,
        base_cost_nano_aiu: 125.712, candidate_cost_nano_aiu: 127.772,
        base_elapsed_seconds: 296.064, candidate_elapsed_seconds: 302.923,
        cost_improvement_percent: -1.638665838106148, time_improvement_percent: -2.3167, ...metrics } },
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

function historyRun(number, purpose, execution, guide = 'not_assessed') {
  return { schema_version: 1, project_id: 'sample_repo', run_id: `${number}-1`, purpose,
    created_at: `2026-09-16T0${number}:00:00Z`, origin: 'github_actions',
    guide: { status: guide, reason_code: guide === 'completed' ? 'evaluation_completed' : 'live_disabled',
      metrics: null, decision: null },
    execution: { status: execution, reason_code: execution === 'completed' ? 'evaluation_completed' : 'live_disabled',
      metrics: null, decision: null } };
}

async function historyPage(page, reports, withSkill = false) {
  const history = reports.map(report => {
    const summary = { ...report, guide_status: report.guide.status, execution_status: report.execution.status };
    if (withSkill) Object.assign(summary, { skill_id: 'develop', skill_snapshots: `${report.run_id}/skill-snapshots.json`,
      base_skill_sha256: hash('Retained instructions\n'), candidate_skill_sha256: null });
    return summary;
  });
  await page.route('http://dashboard.test/results/sample_repo/index.json', route => route.fulfill({
    json: { schema_version: 1, history },
  }));
  for (const report of reports) {
    await page.route(`http://dashboard.test/results/sample_repo/${report.run_id}/report.json`, route => route.fulfill({ json: report }));
    if (withSkill) await page.route(`http://dashboard.test/results/sample_repo/${report.run_id}/skill-snapshots.json`, route => route.fulfill({
      json: { schema_version: 1, project_id: 'sample_repo', run_id: report.run_id, skill_id: 'develop',
        report_sha256: hash(JSON.stringify(report)), base: { content: 'Retained instructions\n', sha256: hash('Retained instructions\n') },
        candidate: null },
    }));
  }
  await pageWith(page, { schema_version: 1, projects: [{
    id: 'sample_repo', state: 'active', history_count: reports.length, current_run: null,
  }] });
  await expect(page.locator('#detail')).toBeVisible();
}

function blockedHistory() {
  return [historyRun(6, 'project_assessment', 'blocked', 'blocked'),
    historyRun(5, 'project_assessment', 'configuration_required', 'blocked'),
    historyRun(4, 'baseline', 'completed', 'completed'), historyRun(3, 'comparison', 'blocked'),
    historyRun(2, 'comparison', 'blocked'), historyRun(1, 'comparison', 'completed')];
}

test('project selection opens the newest fully completed run before historical comparisons or partial runs', async ({ page }) => {
  const historical = { ...historyRun(1, 'comparison', 'completed'), origin: 'historical_import' };
  await historyPage(page, [historical, historyRun(6, 'project_assessment', 'blocked', 'completed'),
    historyRun(3, 'project_assessment', 'completed', 'completed'), historyRun(5, 'baseline', 'completed'),
    historyRun(4, 'project_assessment', 'completed', 'completed')]);
  await expect(page.locator('#report-link')).toHaveAttribute('href', '/results/sample_repo/4-1/report.json');
  await expect(page.locator('#guide .badge')).toHaveText('실행 완료');
});

test('project selection falls back to the newest comparison when neither axis completes together', async ({ page }) => {
  await historyPage(page, [historyRun(1, 'comparison', 'completed'),
    historyRun(4, 'baseline', 'completed'), historyRun(3, 'project_assessment', 'blocked', 'completed'),
    historyRun(2, 'comparison', 'completed')]);
  await expect(page.locator('#report-link')).toHaveAttribute('href', '/results/sample_repo/2-1/report.json');
});

test('project selection falls back to the newest run when no comparison or fully completed run exists', async ({ page }) => {
  await historyPage(page, [historyRun(1, 'baseline', 'blocked'), historyRun(2, 'project_assessment', 'configuration_required')]);
  await expect(page.locator('#report-link')).toHaveAttribute('href', '/results/sample_repo/2-1/report.json');
  await expect(page.locator('#execution .decision-title')).toHaveText('설정 필요');
});

test('execution history collapses only consecutive blocked records and filters before grouping without dropping runs', async ({ page }) => {
  await historyPage(page, blockedHistory());
  await page.getByRole('tab', { name: '실행 이력', exact: true }).click();
  await expect(page.locator('#history-count')).toHaveText('6건');
  await expect(page.locator('#history > .blocked-runs')).toHaveCount(2);
  await expect(page.locator('#history .run')).toHaveCount(6);
  await expect(page.locator('#history .run:visible')).toHaveCount(2);
  const first = page.locator('#history > .blocked-runs').first();
  await expect(first.locator('summary')).toHaveText(/차단 기록 2건 \(최근 .+\)/);
  await expect(first).toHaveJSProperty('open', false);
  await first.locator('summary').click();
  await expect(page.locator('#history .run:visible')).toHaveCount(4);
  await page.locator('#history [data-run="5-1"]').click();
  await expect(page.locator('#report-link')).toHaveAttribute('href', '/results/sample_repo/5-1/report.json');
  await expect(first).toHaveJSProperty('open', true);
  await page.locator('#history-filter').selectOption('comparison');
  await expect(page.locator('#history .run')).toHaveCount(3);
  await expect(page.locator('#history > .blocked-runs')).toHaveCount(1);
  await expect(page.locator('#history > .run')).toHaveCount(1);
  await page.locator('#history-filter').selectOption('baseline');
  await expect(page.locator('#history .blocked-runs')).toHaveCount(0);
  await expect(page.locator('#history .run')).toHaveCount(1);
  await page.locator('#history-filter').selectOption('candidate');
  await expect(page.locator('#history')).toHaveText('해당 종류의 이력이 없습니다.');
  await page.locator('#history-filter').selectOption('');
  await expect(page.locator('#history .run')).toHaveCount(6);
});

test('Skill history keeps completed records visible and expanded blocked records remain selectable', async ({ page }) => {
  await historyPage(page, blockedHistory(), true);
  await expect(page.locator('#skill-history-runs > .blocked-runs')).toHaveCount(2);
  await expect(page.locator('#skill-history-runs .skill-run')).toHaveCount(6);
  await expect(page.locator('#skill-history-runs .skill-run:visible')).toHaveCount(2);
  const group = page.locator('#skill-history-runs > .blocked-runs').first();
  await expect(group).toHaveJSProperty('open', false);
  await group.locator('summary').click();
  await page.locator('#skill-history-runs [data-run="5-1"]').click();
  await expect(page.locator('#report-link')).toHaveAttribute('href', '/results/sample_repo/5-1/report.json');
  await expect(page.locator('#skill-select')).toHaveValue('legacy:sample_repo:develop');
  await expect(page.locator('#skill-changes pre')).toHaveText('Retained instructions\n');
  await expect(group).toHaveJSProperty('open', true);
});

test('empty-state titles remain visible while explanations and methodology require expansion', async ({ page }) => {
  await comparisonPage(page);
  for (const id of ['guide', 'improvement-evidence', 'skill-changes', 'decision-reasons', 'task-results']) {
    await expect(page.locator(`#${id} .empty-state > strong`)).toBeVisible();
    const details = page.locator(`#${id} .state-explanation`);
    await expect(details).toHaveJSProperty('open', false);
    await expect(details.locator('p')).toBeHidden();
    await details.locator('summary').click();
    await expect(details.locator('p')).toBeVisible();
  }
  for (const details of await page.locator('.methodology, .provenance').all()) {
    await expect(details).toHaveJSProperty('open', false);
    await expect(details.locator('p').first()).toBeHidden();
    await details.locator('summary').click();
    await expect(details.locator('p').first()).toBeVisible();
  }
  await expect(page.locator('footer')).toBeVisible();
  await page.getByRole('button', { name: '샘플 화면 보기', exact: true }).click();
  await expect(page.locator('#sample-banner')).toBeVisible();
  await expect(page.locator('#sample-banner')).toContainText('실제 평가·개선 성과·채택 근거가 아닙니다.');
  await expect(page.locator('footer')).toBeVisible();
});

test('blocked execution and missing metrics retain visible states without expanded disclaimers', async ({ page }) => {
  await historyPage(page, [historyRun(1, 'project_assessment', 'blocked', 'blocked')]);
  await expect(page.locator('#execution .decision-title')).toHaveText('차단');
  for (const id of ['execution', 'cost-card', 'time-card']) {
    await expect(page.locator(`#${id} .state-explanation`)).toHaveJSProperty('open', false);
    await expect(page.locator(`#${id} .state-explanation p`)).toBeHidden();
  }
  await expect(page.locator('#cost-card .decision-title')).toHaveText('미기록');
  await expect(page.locator('#time-card .decision-title')).toHaveText('미기록');
});

test('dashboard branding uses the requested bilingual name without mobile overflow', async ({ page }) => {
  await pageWith(page, { schema_version: 1, projects: [] });
  await expect(page).toHaveTitle('Self-Evolving Agent SkillOps · 자가 진화 에이전트 스킬옵스');
  await expect(page.getByRole('link', { name: 'Self-Evolving Agent SkillOps', exact: true })).toBeVisible();
  await expect(page.locator('.header-caption')).toHaveText('자가 진화 에이전트 스킬옵스');
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await expect(page.getByRole('button', { name: '새로고침', exact: true })).toBeVisible();
  }
});

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

test('paired measurements compute unrecorded deltas explicitly and convert NanoAIU to AIU', async ({ page }) => {
  await comparisonPage(page, null, { base_cost_nano_aiu: 1e9, candidate_cost_nano_aiu: 1.16e9,
    base_elapsed_seconds: 100, candidate_elapsed_seconds: 132,
    cost_improvement_percent: undefined, time_improvement_percent: undefined });
  await expect(page.locator('#cost-card .big-number')).toHaveText('+16%');
  await expect(page.locator('#time-card .big-number')).toHaveText('+32%');
  await expect(page.locator('#cost-card .metric-origin')).toHaveText('현장 계산');
  await expect(page.locator('#time-card .metric-origin')).toHaveText('현장 계산');
  await expect(page.locator('#cost-card .bar-value')).toHaveText(['1', '1.16']);
  await expect(page.locator('#cost-card .metric-note')).toHaveText('AIU는 CLI가 기록한 Copilot 사용량 단위입니다.');
  await expect(page.locator('#cost-card')).not.toContainText('NanoAIU');
  await expect(page.locator('#execution .efficiency-warning')).toBeVisible();
});

test('recorded percentages take precedence and efficiency warnings do not change the recorded decision', async ({ page }) => {
  await comparisonPage(page, null, { base_cost_nano_aiu: 1e9, candidate_cost_nano_aiu: 1.16e9,
    cost_improvement_percent: -6, time_improvement_percent: -5 });
  await expect(page.locator('#cost-card .big-number')).toHaveText('+6%');
  await expect(page.locator('#cost-card .metric-origin')).toHaveText('기록된 변화율');
  await expect(page.locator('#execution .decision-title')).toHaveText('후보 거절');
  await expect(page.locator('#execution .decision-heading .efficiency-warning')).toHaveText('비용·시간 증가 주의');
});

test('missing measurements never become computed deltas or efficiency warnings', async ({ page }) => {
  await comparisonPage(page, null, { base_cost_nano_aiu: null, candidate_elapsed_seconds: null,
    cost_improvement_percent: -16, time_improvement_percent: undefined });
  for (const id of ['cost-card', 'time-card']) {
    await expect(page.locator(`#${id} .decision-title`)).toHaveText('미기록');
    await expect(page.locator(`#${id} .big-number, #${id} .metric-origin`)).toHaveCount(0);
  }
  await expect(page.locator('#execution .efficiency-warning')).toHaveCount(0);
});

test('zero baselines and explicitly null percentages never become invented finite changes', async ({ page }) => {
  await comparisonPage(page, null, { base_cost_nano_aiu: 0, candidate_cost_nano_aiu: 1,
    cost_improvement_percent: undefined, time_improvement_percent: null });
  for (const id of ['cost-card', 'time-card']) {
    await expect(page.locator(`#${id}`)).toContainText('변화율 미기록');
    await expect(page.locator(`#${id} .big-number`)).toHaveCount(0);
  }
  await expect(page.locator('#cost-card .bar-value')).toHaveText(['0', '0.000000001']);
  await expect(page.locator('#execution .efficiency-warning')).toHaveCount(0);
});

test('a five-percent increase is not labelled as exceeding the efficiency warning threshold', async ({ page }) => {
  await comparisonPage(page, null, { base_cost_nano_aiu: 1e9, candidate_cost_nano_aiu: 1.05e9,
    cost_improvement_percent: undefined, time_improvement_percent: -5 });
  await expect(page.locator('#cost-card .big-number')).toHaveText('+5%');
  await expect(page.locator('#time-card .big-number')).toHaveText('+5%');
  await expect(page.locator('#execution .efficiency-warning')).toHaveCount(0);
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

const hash = value => require('node:crypto').createHash('sha256').update(value).digest('hex');
const canonical = value => JSON.stringify(sortKeys(value));
function sortKeys(value) {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, sortKeys(value[key])]));
  return value;
}
function evolutionFixture({ full = false, baseFull = full, candidateFull = full, omitBaseFiles = [],
  dual = false, twins = false, baseText = 'AS-IS\n', candidateText = 'TO-BE\n' } = {}) {
  const baseRun = '20260914T072451Z-50354146d951', candidateRun = '20260915T005404Z-611d681b8bf7';
  const comparisonRun = '20260915T010916Z-74058d9807da';
  const makeVersion = (text, changed) => {
    const complete = changed ? candidateFull : baseFull;
    const bytes = { 'SKILL.md': Buffer.from(text) };
    if (complete) Object.assign(bytes, { 'assets/data.bin': Buffer.from([255, 0, 128]),
      'scripts/check.py': Buffer.from(changed ? '<img src=x onerror="window.evolved=true">' : 'pass\n') });
    if (!changed) for (const path of omitBaseFiles) delete bytes[path];
    const files = Object.entries(bytes).sort(([a], [b]) => a < b ? -1 : 1).map(([path, raw]) => ({ path, sha256: hash(raw), bytes: raw.length }));
    const payload = { domain: 'skillops.captured-version', schema_version: 1,
      capture_scope: complete ? 'complete_bundle' : 'entrypoint_only', entrypoint: 'SKILL.md', files };
    const version = { version_id: `sha256:${hash(canonical(payload))}`, capture_scope: payload.capture_scope,
      entrypoint: 'SKILL.md', files };
    return { version, contents: Object.entries(bytes).map(([path, raw]) => ({
      version_id: version.version_id, path, encoding: 'base64', data: raw.toString('base64'),
    })) };
  };
  const base = makeVersion(baseText, false), candidate = makeVersion(candidateText, true);
  const keys = twins ? ['skillops:develop', 'other:develop'] : ['skillops:develop'];
  const report = { schema_version: 1, project_id: 'sample_repo', run_id: `import-${comparisonRun}`,
    purpose: 'comparison', created_at: '2026-09-15T01:09:16Z', origin: 'historical_import',
    source_report_sha256: hash('comparison fixture'), source_commit: null, evaluator_sha256: null,
    project_tree_sha256: null, source_schema_version: 2,
    guide: { status: 'not_assessed', reason_code: 'historical_import', metrics: null, decision: null },
    execution: { status: 'completed', reason_code: 'historical_import', decision: 'rejected', metrics: null } };
  const ref = (kind, run_id, artifact_sha256, availability = 'verified') => ({ kind, run_id, artifact_sha256, availability });
  const generation = { skill_key: keys[0], candidate_ref: ref('candidate', candidateRun, hash('candidate fixture')),
    baseline_ref: ref('baseline', baseRun, hash('baseline fixture')), base_entrypoint_sha256: hash(baseText),
    candidate_entrypoint_sha256: hash(candidateText), base_version_id: base.version.version_id,
    candidate_version_id: candidate.version.version_id, observed_failure_count: 0,
    hypothesis_kind: 'unknown', hypothesis_basis: 'unavailable' };
  const records = { identities: keys.map(skill_key => ({ skill_key, display_name: 'Develop' })),
    sources: keys.map((skill_key, i) => ({ skill_key, project_id: 'sample_repo', kind: 'workspace',
      scope: 'project', path: i ? '.agents/skills/develop' : '.github/skills/develop', observed_at: null, evidence_ref: null })),
    versions: [base.version, candidate.version],
    skill_versions: keys.flatMap(skill_key => [base, candidate].map(({ version }) => ({ skill_key, version_id: version.version_id }))),
    generations: [generation], comparisons: [{ skill_key: keys[0],
      comparison_ref: ref('comparison', comparisonRun, report.source_report_sha256),
      candidate_ref: ref('candidate', candidateRun, null, 'referenced_only'),
      base_entrypoint_sha256: hash(baseText), candidate_entrypoint_sha256: hash(candidateText),
      base_version_id: base.version.version_id, candidate_version_id: candidate.version.version_id,
      decision: 'rejected', status: 'completed' }],
    adoptions: keys.map(skill_key => ({ skill_key, project_id: 'sample_repo', state: 'unknown',
      observed_at: null, entrypoint_sha256: null, version_id: null, evidence_kind: 'none', registry_sha256: null })) };
  const bindings = keys.map((skill_key, i) => ({ skill_key, base_version_id: base.version.version_id,
    candidate_version_id: candidate.version.version_id, legacy_skill_id: dual && !i ? 'develop' : null }));
  const envelope = { schema_version: 1, project_id: 'sample_repo', run_id: report.run_id,
    report_sha256: hash(JSON.stringify(report)), records, bindings,
    file_contents: [...base.contents, ...candidate.contents] };
  const snapshots = dual ? { schema_version: 1, project_id: 'sample_repo', run_id: report.run_id,
    report_sha256: envelope.report_sha256, skill_id: 'develop',
    base: { content: baseText, sha256: hash(baseText) }, candidate: { content: candidateText, sha256: hash(candidateText) } } : null;
  const summary = { ...report, guide_status: 'not_assessed', execution_status: 'completed',
    skill_evolution: `${report.run_id}/skill-evolution.json`,
    evolution_skills: bindings.map(({ legacy_skill_id, ...binding }) => ({ ...binding, display_name: 'Develop' })),
    ...(dual ? { skill_id: 'develop', skill_snapshots: `${report.run_id}/skill-snapshots.json`,
      base_skill_sha256: hash(baseText), candidate_skill_sha256: hash(candidateText) } : {}) };
  return { report, envelope, snapshots, summary };
}
async function evolutionPage(page, fixture) {
  const origin = 'https://dashboard.test';
  const folder = `${origin}/results/sample_repo`;
  const fixtures = [fixture, ...(fixture.related || [])];
  await page.route(`${folder}/index.json`, route => route.fulfill({ json: { schema_version: 1, history: fixtures.map(item => item.summary) } }));
  for (const item of fixtures) {
    await page.route(`${folder}/${item.report.run_id}/report.json`, route => route.fulfill({ body: JSON.stringify(item.report), contentType: 'application/json' }));
    await page.route(`${folder}/${item.report.run_id}/skill-evolution.json`, route => route.fulfill({ json: item.envelope }));
    if (item.snapshots) await page.route(`${folder}/${item.report.run_id}/skill-snapshots.json`, route => route.fulfill({ json: item.snapshots }));
  }

  await pageWith(page, { schema_version: 1, projects: [{ id: 'sample_repo', state: 'active',
    history_count: 1, current_run: null }] }, 200, origin);
}

test('complete Skill bundles use a dedicated two-MiB bound without enlarging report reads', async ({ page }) => {
  const fixture = evolutionFixture({ baseText: 'x'.repeat(800000) });
  const raw = JSON.stringify(fixture.envelope);
  expect(Buffer.byteLength(raw)).toBeGreaterThan(1048576);
  expect(Buffer.byteLength(raw)).toBeLessThan(2097152);
  await evolutionPage(page, fixture);
  await expect(page.locator('#detail')).toBeVisible();
  await expect(page.locator('#error')).toBeHidden();
  await expect(page.locator('#skill-changes')).toContainText('표시 한도');
  const folder = `https://dashboard.test/results/sample_repo/${fixture.report.run_id}`;
  await page.route(`${folder}/skill-evolution.json`, route => route.fulfill({
    contentType: 'application/json', body: ' '.repeat(2097152) + raw,
  }));
  await page.reload();
  await expect(page.locator('#error')).toBeVisible();
  await page.unroute(`${folder}/skill-evolution.json`);
  await page.route(`${folder}/skill-evolution.json`, route => route.fulfill({ json: fixture.envelope }));
  await page.route(`${folder}/report.json`, route => route.fulfill({
    contentType: 'application/json', body: ' '.repeat(1048576) + JSON.stringify(fixture.report),
  }));
  await page.reload();
  await expect(page.locator('#error')).toBeVisible();
});

function assessmentFixture({ legacy = true } = {}) {
    const { execFileSync } = require('node:child_process');
    const [report, envelope, details] = JSON.parse(execFileSync('python3', ['-c',
      'import sys,json; sys.path.insert(0,"tests"); from test_skill_assessments import fixture; print(json.dumps(fixture(legacy=sys.argv[1]=="legacy")))',
      legacy ? 'legacy' : 'current',
    ], { encoding: 'utf8' }));
    envelope.report_sha256 = details.report_sha256 = hash(JSON.stringify(report));
    const summary = { ...report, execution_status: report.execution.status, guide_status: report.guide.status,
      skill_evolution: `${report.run_id}/skill-evolution.json`,
      skill_assessments: `${report.run_id}/skill-assessments.json`,
      evolution_skills: envelope.bindings.map(({ legacy_skill_id, ...binding }) => ({ ...binding, display_name: 'develop' })) };
    return { report, envelope, details, summary };
  }

function pythonDecisionCases() {
  return JSON.parse(require('node:child_process').execFileSync('python3', ['-c',
    'import sys,json; sys.path.insert(0,"tests"); from test_skill_assessments import decision_cases; print(json.dumps(decision_cases()))',
  ], { encoding: 'utf8' }));
}

test('Python and JavaScript decisions agree on efficiency boundaries, missing measurements, rejections and legacy policy', async ({ page }) => {
  const cases = pythonDecisionCases();
  await pageWith(page, { schema_version: 1, projects: [] }, 200, 'https://dashboard.test');
  const decisions = await page.evaluate(async cases => {
    const { decide } = await import('/assessments.js');
    return cases.map(item => decide(item.row, item.legacy ? null : undefined));
  }, cases);
  for (const [index, item] of cases.entries()) expect(decisions[index], item.name).toEqual(item.expected);
});

test('versioned assessment records display Python efficiency decisions and reject unsupported policy stamps', async ({ page }) => {
  const fixture = assessmentFixture({ legacy: false });
  const item = pythonDecisionCases().find(item => item.name === 'cost-regression');
  fixture.details.skills[0] = { ...item.row, decision: item.expected };
  await page.route('https://dashboard.test/results/sample_repo/123-1/skill-assessments.json', route => route.fulfill({ json: fixture.details }));
  await evolutionPage(page, fixture);
  await expect(page.locator('#execution .decision-title')).toHaveText('검증 불충분');
  await expect(page.locator('#decision-reasons')).toContainText('efficiency_regression');
  await expect(page.locator('#execution-scope')).toContainText('검증 범위에서 회귀 미발견');
  await expect(page.locator('#error')).toBeHidden();
  for (const version of [null, true, '1', 2]) {
    fixture.details.skills[0].decision.policy_version = version;
    await evolutionPage(page, fixture);
    await expect(page.locator('#error')).toBeVisible();
    await expect(page.locator('#detail')).toBeHidden();
  }
});

  test('automatic Skill assessment shows real quality, frozen work and scoped non-regression', async ({ page }) => {
    const fixture = assessmentFixture();
    await page.route('https://dashboard.test/results/sample_repo/123-1/skill-assessments.json',
      route => route.fulfill({ json: fixture.details }));
    await evolutionPage(page, fixture);
    await expect(page.locator('#quality-summary')).toHaveText('선택 Skill · 실제 버전 평가');
    await expect(page.locator('#guide')).toContainText('2 → 3');
    await expect(page.locator('#improvement-evidence')).toContainText('Boundary guidance is missing.');
    await expect(page.locator('#improvement-evidence')).toContainText('모델이 제시한 가설 (영문 원문)');
    await expect(page.locator('#execution')).toContainText('품질 점수 상승 (회귀 없음)');
    await expect(page.locator('#execution .dimension-changes')).toHaveText('clarity 2 → 3');
    await expect(page.locator('#task-results')).toContainText('bug');
    await expect(page.locator('#execution-scope')).toContainText('검증 범위에서 회귀 미발견');
    await expect(page.locator('#quality-section')).not.toContainText('합성 평가 예시');
    await expect(page.locator('#cost-card')).toContainText('미기록');
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await page.getByRole('button', { name: '샘플 화면 보기', exact: true }).click();
    await expect(page.locator('#improvement-evidence')).not.toContainText('Boundary guidance is missing.');
  });

  test('automatic assessment rejects forged qualification or report and version bindings', async ({ page }) => {
    for (const change of ['report', 'version', 'decision', 'cases', 'path']) {
      const fixture = assessmentFixture();
      if (change === 'report') fixture.details.report_sha256 = 'a'.repeat(64);
      if (change === 'version') fixture.details.skills[0].candidate_version_id = `sha256:${'a'.repeat(64)}`;
      if (change === 'decision') fixture.details.skills[0].quality.candidate = null;
      if (change === 'cases') fixture.details.skills[0].checks.candidate.cases = [];
      if (change === 'path') fixture.summary.skill_assessments = '../outside/skill-assessments.json';
      await page.route('https://dashboard.test/results/sample_repo/123-1/skill-assessments.json',
        route => route.fulfill({ json: fixture.details }));
      await evolutionPage(page, fixture);
      await expect(page.locator('#error')).toBeVisible();
      await expect(page.locator('#detail')).toBeHidden();
      await page.unrouteAll();
    }
  });
test('completed assessment selection preselects its Skill and renders evidence without opening the dropdown', async ({ page }) => {
  const imported = evolutionFixture({ twins: true });
  const assessed = assessmentFixture();
  imported.related = [assessed];
  await page.route(`https://dashboard.test/results/sample_repo/${assessed.report.run_id}/skill-assessments.json`,
    route => route.fulfill({ json: assessed.details }));
  await evolutionPage(page, imported);
  await expect(page.locator('#report-link')).toHaveAttribute('href', `/results/sample_repo/${assessed.report.run_id}/report.json`);
  await expect(page.locator('#skill-select')).toHaveValue(assessed.details.skills[0].skill_key);
  await expect(page.locator('#quality-summary')).toHaveText('선택 Skill · 실제 버전 평가');
  await expect(page.locator('#execution .decision-title')).toHaveText('품질 점수 상승 (회귀 없음)');
});

test('assessment dimensions use Korean labels with recorded IDs and hypotheses stay collapsed and inert', async ({ page }) => {
  const fixture = assessmentFixture();
  const row = fixture.details.skills[0];
  const labels = { trigger_description: '사용 조건 설명', workflow_clarity: '작업 흐름 명확성', generalization: '일반화',
    instruction_quality: '지침 품질', progressive_disclosure: '단계적 정보 제공', resource_organization: '리소스 구성',
    principle_of_lack_of_surprise: '예측 가능한 동작' };
  row.quality.base.dimensions = Object.keys(labels).map(id => ({ id, score: 3 }));
  row.quality.candidate.dimensions = Object.keys(labels).map(id => ({ id, score: id === 'trigger_description' ? 4 : 3 }));
  row.generation.hypothesis = '<img src=x onerror="window.hypothesisExecuted=true">';
  await page.route('https://dashboard.test/results/sample_repo/123-1/skill-assessments.json', route => route.fulfill({ json: fixture.details }));
  await evolutionPage(page, fixture);
  for (const [id, label] of Object.entries(labels)) {
    const dimension = page.locator('#guide tr').filter({ has: page.locator('small', { hasText: id }) });
    await expect(dimension).toContainText(label);
    await expect(dimension.locator('small')).toHaveText(id);
  }
  await expect(page.locator('#execution .dimension-changes')).toHaveText('trigger_description 3 → 4');
  const hypothesis = page.locator('#improvement-evidence .hypothesis');
  await expect(hypothesis).toHaveJSProperty('open', false);
  await expect(hypothesis.locator('p')).toBeHidden();
  await hypothesis.locator('summary').click();
  await expect(hypothesis.locator('p')).toHaveText(row.generation.hypothesis);
  await expect(hypothesis.locator('img')).toHaveCount(0);
  expect(await page.evaluate(() => window.hypothesisExecuted)).toBeUndefined();
});

test('assessment quality improvement remains scoped and measured efficiency increases get a visible warning', async ({ page }) => {
  for (const [cost, time, warning] of [[1.16e9, 100, true], [1e9, 132, true], [1.05e9, 105, false], [null, null, false]]) {
    const fixture = assessmentFixture();
    const row = fixture.details.skills[0];
    row.applications.base.measurement = { cost_nano_aiu: 1e9, elapsed_seconds: 100 };
    row.applications.candidate.measurement = { cost_nano_aiu: cost, elapsed_seconds: time };
    await page.route('https://dashboard.test/results/sample_repo/123-1/skill-assessments.json', route => route.fulfill({ json: fixture.details }));
    await evolutionPage(page, fixture);
    await expect(page.locator('#execution .decision-title')).toHaveText('품질 점수 상승 (회귀 없음)');
    await expect(page.locator('#execution .dimension-changes')).toHaveText('clarity 2 → 3');
    await expect(page.locator('#execution .decision-heading .efficiency-warning')).toHaveCount(warning ? 1 : 0);
    if (warning) await expect(page.locator('#execution .efficiency-warning')).toBeVisible();
    if (cost !== null) await expect(page.locator('#cost-card .metric-origin')).toHaveText('현장 계산');
    expect(row.decision.status).toBe('improved');
    await page.unrouteAll();
  }
});

test('evolution dual binding selects one stable Skill and separates recommendation from adoption', async ({ page }) => {
  await evolutionPage(page, evolutionFixture({ dual: true }));
  await expect(page.locator('#skill-list .skill-item')).toHaveCount(1);
  await expect(page.locator('#skill-select')).toHaveValue('skillops:develop');
  await expect(page.locator('#evolution-metadata')).toContainText('skillops:develop');
  await expect(page.locator('#evolution-metadata')).toContainText('SKILL.md만 보관');
  await expect(page.locator('#evolution-metadata')).toContainText('.github/skills/develop');
  await expect(page.locator('#evolution-metadata')).toContainText('채택 상태 미기록');
  await expect(page.locator('#improvement-evidence')).toContainText('관측 실패 0건');
  await expect(page.locator('#improvement-evidence')).toContainText('개선 가설 미기록');
  await expect(page.locator('#improvement-evidence')).toContainText('후보 거절');
  await expect(page.locator('#improvement-evidence')).toContainText('참조만 기록');
  await expect(page.locator('#skill-changes pre').first()).toHaveText('AS-IS\n');
  await expect(page.locator('#sample-banner')).toBeHidden();
});

test('evolution lifecycle-only equal names remain separate identities and clear old evidence', async ({ page }) => {
  await evolutionPage(page, evolutionFixture({ twins: true }));
  await expect(page.locator('#skill-list .skill-item')).toHaveCount(2);
  await page.locator('#skill-select').selectOption('other:develop');
  await expect(page.locator('#evolution-metadata')).toContainText('.agents/skills/develop');
  await expect(page.locator('#evolution-metadata')).not.toContainText('.github/skills/develop');
  await expect(page.locator('#improvement-evidence')).not.toContainText('관측 실패 0건');
  await expect(page.locator('#skill-history-runs .skill-run')).toHaveCount(1);
});

test('evolution complete bundle file selection renders binary metadata and inert script content on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await evolutionPage(page, evolutionFixture({ full: true }));
  await expect(page.locator('#evolution-metadata')).toContainText('전체 번들 보관');
  await page.locator('#evolution-file').selectOption('assets/data.bin');
  await expect(page.locator('#skill-changes')).toContainText('바이너리');
  await page.locator('#evolution-file').selectOption('scripts/check.py');
  await expect(page.locator('#skill-changes pre').nth(1)).toContainText('<img');
  await expect(page.locator('#skill-changes img')).toHaveCount(0);
  await page.getByRole('button', { name: '변경 비교', exact: true }).click();
  await expect(page.locator('#skill-diff')).toContainText('+ <img');
  expect(await page.evaluate(() => window.evolved)).toBeUndefined();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

for (const [label, text] of [['lines', 'line\n'.repeat(401)], ['characters', 'x'.repeat(51000)]]) {
  test(`evolution ${label} display limit preserves report and retained content`, async ({ page }) => {
    const fixture = evolutionFixture({ baseText: text, candidateText: text + 'change' });
    await evolutionPage(page, fixture);
    await expect(page.locator('#detail')).toBeVisible();
    await expect(page.locator('#skill-changes')).toContainText('표시 한도');
    await expect(page.locator('#execution')).toContainText('후보 거절');
    await expect(page.locator(`#evolution-metadata code[title="${fixture.envelope.bindings[0].base_version_id}"]`)).toBeVisible();
    await expect(page.getByRole('alert')).toBeHidden();
    expect(Buffer.from(fixture.envelope.file_contents[0].data, 'base64').toString()).toBe(text);
  });
}

test('evolution altered retained bytes fail visibly rather than displaying a false capture', async ({ page }) => {
  const fixture = evolutionFixture();
  fixture.envelope.file_contents[0].data = Buffer.from('tampered').toString('base64');
  await evolutionPage(page, fixture);
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.locator('#detail')).toBeHidden();
});

test('evolution late attachment cannot overwrite sample selection', async ({ page }) => {
  const fixture = evolutionFixture();
  await evolutionPage(page, fixture);
  await expect(page.locator('#evolution-metadata')).toContainText('skillops:develop');
  let release, arrived;
  const waiting = new Promise(resolve => { arrived = resolve; });
  const delayed = new Promise(resolve => { release = resolve; });
  const url = `https://dashboard.test/results/sample_repo/${fixture.report.run_id}/skill-evolution.json`;
  await page.route(url, async route => { arrived(); await delayed; await route.fallback(); });
  await page.getByRole('button', { name: '새로고침', exact: true }).click();
  await waiting;
  await page.getByRole('button', { name: '샘플 화면 보기', exact: true }).click();
  const response = page.waitForResponse(url);
  release();
  await response;
  await expect(page.locator('#origin')).toHaveText('합성 샘플');
  await expect(page.locator('#evolution-metadata')).toBeHidden();
  await expect(page.locator('#skill-changes')).toContainText('issue-helper');
});

function baselineFixture(fixture) {
  const baseline = JSON.parse(JSON.stringify(fixture));
  const ref = fixture.envelope.records.generations[0].baseline_ref;
  baseline.report = { ...baseline.report, run_id: `import-${ref.run_id}`, purpose: 'baseline',
    source_report_sha256: ref.artifact_sha256, created_at: '2026-09-14T07:24:51Z',
    execution: { status: 'completed', reason_code: 'historical_import', metrics: null, decision: null } };
  const id = baseline.envelope.bindings[0].base_version_id;
  Object.assign(baseline.envelope, { run_id: baseline.report.run_id, report_sha256: hash(JSON.stringify(baseline.report)) });
  baseline.envelope.bindings[0].candidate_version_id = null;
  baseline.envelope.records.versions = baseline.envelope.records.versions.filter(item => item.version_id === id);
  baseline.envelope.records.skill_versions = baseline.envelope.records.skill_versions.filter(item => item.version_id === id);
  baseline.envelope.records.generations = [];
  baseline.envelope.records.comparisons = [];
  baseline.envelope.file_contents = baseline.envelope.file_contents.filter(item => item.version_id === id);
  baseline.summary = { ...baseline.summary, ...baseline.report,
    skill_evolution: `${baseline.report.run_id}/skill-evolution.json`,
    evolution_skills: baseline.summary.evolution_skills.map(item => ({ ...item, candidate_version_id: null })) };
  if (baseline.snapshots) {
    Object.assign(baseline.snapshots, { run_id: baseline.report.run_id, candidate: null,
      report_sha256: baseline.envelope.report_sha256 });
    baseline.summary.skill_snapshots = `${baseline.report.run_id}/skill-snapshots.json`;
    baseline.summary.candidate_skill_sha256 = null;
  }
  return baseline;
}

test('evolution evidence navigation restores baseline without a future candidate', async ({ page }) => {
  const fixture = evolutionFixture();
  const ref = fixture.envelope.records.generations[0].baseline_ref;
  fixture.related = [baselineFixture(fixture)];
  await evolutionPage(page, fixture);
  await page.locator('#improvement-evidence').getByRole('button', { name: ref.run_id, exact: true }).click();
  await expect(page.locator('#run-title')).toHaveText('기준 평가');
  await expect(page.locator('#skill-changes')).toContainText('후보 Skill이 없습니다');
  await expect(page.locator('#improvement-evidence')).not.toContainText('관측 실패 0건');
  await expect(page.locator(`#evolution-metadata code[title="${fixture.envelope.bindings[0].candidate_version_id}"]`)).toHaveCount(0);
});

test('evolution provenance scope and timestamped pin remain weaker than deployment', async ({ page }) => {
  const fixture = evolutionFixture();
  fixture.envelope.records.sources[0] = { ...fixture.envelope.records.sources[0],
    kind: 'run_archive', scope: 'unknown', path: 'runs/retained', observed_at: null };
  Object.assign(fixture.envelope.records.adoptions[0], { state: 'entrypoint_pin_observed',
    evidence_kind: 'registry_snapshot', observed_at: '2026-09-16T00:00:00Z',
    entrypoint_sha256: hash('pin bytes'), registry_sha256: hash('registry bytes') });
  await evolutionPage(page, fixture);
  await expect(page.locator('#evolution-metadata')).toContainText('출처 범위 미기록');
  await expect(page.locator('#evolution-metadata')).toContainText('정의 경로 아님');
  await expect(page.locator('#evolution-metadata')).toContainText('설정된 진입점 pin 관측');
  await expect(page.locator(`#evolution-metadata code[title="${hash('registry bytes')}"]`)).toHaveText(hash('registry bytes').slice(0, 12));
  await expect(page.locator('#evolution-metadata')).toContainText('현재 설치·실행 또는 전체 번들 배포의 증거가 아닙니다');
});

test('uncaptured identity never inherits another identity legacy source', async ({ page }) => {
  const fixture = evolutionFixture({ dual: true, twins: true });
  const second = fixture.envelope.bindings[1];
  second.base_version_id = null; second.candidate_version_id = null;
  fixture.summary.evolution_skills[1].base_version_id = null;
  fixture.summary.evolution_skills[1].candidate_version_id = null;
  fixture.envelope.records.skill_versions = fixture.envelope.records.skill_versions.filter(item => item.skill_key !== second.skill_key);
  await evolutionPage(page, fixture);
  await expect(page.locator('#skill-changes')).toContainText('AS-IS');
  await page.locator('#skill-select').selectOption(second.skill_key);
  await expect(page.locator('#evolution-metadata')).toContainText(second.skill_key);
  await expect(page.locator('#skill-changes')).not.toContainText('AS-IS');
  await expect(page.locator('#skill-changes')).not.toContainText('TO-BE');
  await expect(page.locator('#skill-changes')).toContainText('Skill 원문이 공개 기록에 없습니다');
  await page.locator('#skill-select').selectOption('skillops:develop');
  await expect(page.locator('#skill-changes pre').first()).toHaveText('AS-IS\n');
});

test('uncaptured file or version cannot become a proven absence or added-file diff', async ({ page }) => {
  for (const missingVersion of [false, true]) {
    const fixture = evolutionFixture({ candidateFull: true });
    if (missingVersion) {
      const id = fixture.envelope.bindings[0].base_version_id;
      fixture.envelope.bindings[0].base_version_id = null;
      fixture.summary.evolution_skills[0].base_version_id = null;
      fixture.envelope.records.versions = fixture.envelope.records.versions.filter(item => item.version_id !== id);
      fixture.envelope.records.skill_versions = fixture.envelope.records.skill_versions.filter(item => item.version_id !== id);
      fixture.envelope.file_contents = fixture.envelope.file_contents.filter(item => item.version_id !== id);
      fixture.envelope.records.generations[0].base_version_id = null;
      fixture.envelope.records.comparisons[0].base_version_id = null;
    }
    await evolutionPage(page, fixture);
    await page.locator('#evolution-file').selectOption('scripts/check.py');
    await expect(page.locator('#skill-changes')).toContainText('원문 미보관');
    await expect(page.locator('#skill-changes')).not.toContainText('해당 파일이 없습니다');
    await expect(page.locator('#skill-changes').getByRole('button', { name: '변경 비교', exact: true })).toHaveCount(0);
    await expect(page.locator('#skill-changes')).not.toContainText('삭제 1줄');
  }
});

test('complete inventory supports a real addition without a fabricated removed blank line', async ({ page }) => {
  await evolutionPage(page, evolutionFixture({ full: true, omitBaseFiles: ['scripts/check.py'] }));
  await page.locator('#evolution-file').selectOption('scripts/check.py');
  await expect(page.locator('#skill-changes')).toContainText('해당 파일이 없습니다');
  await page.getByRole('button', { name: '변경 비교', exact: true }).click();
  await expect(page.locator('#skill-diff .added')).toHaveCount(1);
  await expect(page.locator('#skill-diff .removed')).toHaveCount(0);
});

test('served relationship contradictions fail before displaying an authoritative conclusion', async ({ page }) => {
  const mutations = [
    rows => { rows.comparisons[0].decision = 'eligible_for_canary'; },
    rows => { rows.comparisons[0].candidate_ref.availability = 'verified'; },
    rows => { Object.assign(rows.comparisons[0].candidate_ref, { availability: 'verified', artifact_sha256: hash('other candidate') }); },
    rows => { rows.generations[0].base_entrypoint_sha256 = hash('wrong entrypoint'); },
    rows => { rows.comparisons[0].base_version_id = rows.comparisons[0].candidate_version_id; },
    rows => { rows.comparisons[0].comparison_ref.artifact_sha256 = hash('wrong report'); },
    rows => { rows.generations[0].candidate_ref.kind = 'baseline'; },
    rows => { rows.generations[0].approved = true; },
    rows => { rows.generations[0].observed_failure_count = false; },
    rows => { rows.comparisons.push({ ...rows.comparisons[0] }); },
    rows => { rows.comparisons[0].status = 'blocked'; },
    rows => { rows.comparisons[0].base_version_id = null; },
  ];
  for (const mutate of mutations) {
    const fixture = evolutionFixture();
    mutate(fixture.envelope.records);
    await evolutionPage(page, fixture);
    await expect(page.getByRole('alert')).toBeVisible();
    await expect(page.locator('#detail')).toBeHidden();
  }
});

test('historical baseline reference accepts a different retained complete-bundle identity', async ({ page }) => {
  const fixture = evolutionFixture({ dual: true });
  const baseline = baselineFixture(evolutionFixture({ full: true, dual: true }));
  fixture.related = [baseline];
  const partialId = fixture.envelope.bindings[0].base_version_id;
  const fullId = baseline.envelope.bindings[0].base_version_id;
  expect(partialId).not.toBe(fullId);
  await evolutionPage(page, fixture);
  await expect(page.getByRole('alert')).toBeHidden();
  await expect(page.locator(`#evolution-metadata code[title="${partialId}"]`)).toBeVisible();
  await expect(page.locator('#improvement-evidence')).toContainText('참조만 기록');
  await page.locator('#improvement-evidence').getByRole('button', {
    name: fixture.envelope.records.generations[0].baseline_ref.run_id, exact: true,
  }).click();
  await expect(page.locator(`#evolution-metadata code[title="${fullId}"]`)).toBeVisible();
  await expect(page.locator('#evolution-metadata')).toContainText('전체 번들 보관');
  await expect(page.locator(`#evolution-metadata code[title="${partialId}"]`)).toHaveCount(0);
  await expect(page.locator('#skill-changes pre').first()).toHaveText('AS-IS\n');
  await page.locator('#skill-history-runs').getByRole('button', { name: /후보 비교/ }).click();
  await expect(page.locator(`#evolution-metadata code[title="${partialId}"]`)).toBeVisible();
});

test('SHA-256 labels show twelve characters while titles and clipboard retain the complete value', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: 'https://dashboard.test' });
  const fixture = evolutionFixture();
  await evolutionPage(page, fixture);
  const value = fixture.envelope.bindings[0].base_version_id;
  const digest = page.locator(`#evolution-metadata code[title="${value}"]`);
  await expect(digest).toHaveText(`sha256:${value.slice(7, 19)}`);
  const wrapper = digest.locator('..');
  await wrapper.getByRole('button', { name: 'SHA-256 전체 값 복사', exact: true }).click();
  await expect(wrapper.getByRole('status')).toHaveText('복사했습니다.');
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(value);
  await page.locator('.provenance > summary').click();
  await expect(page.locator(`#provenance code[title="${fixture.report.source_report_sha256}"]`))
    .toHaveText(fixture.report.source_report_sha256.slice(0, 12));
});

test('clipboard denial stays explicit and never hides the full SHA-256 title', async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: async () => { throw new Error('Permission denied'); } },
  }));
  const fixture = evolutionFixture();
  await evolutionPage(page, fixture);
  const value = fixture.envelope.bindings[0].base_version_id;
  const wrapper = page.locator(`#evolution-metadata code[title="${value}"]`).locator('..');
  await wrapper.getByRole('button', { name: 'SHA-256 전체 값 복사', exact: true }).click();
  await expect(wrapper.getByRole('status')).toContainText('복사하지 못했습니다.');
  await expect(wrapper.locator('code')).toHaveAttribute('title', value);
});
