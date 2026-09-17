const { test, expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

async function pageWith(page, index, status = 200, origin = 'http://dashboard.test', pathname = '/') {
  await page.route(`${origin}/**`, route => {
    const name = new URL(route.request().url()).pathname;
    if (name === '/results/index.json') {
      return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(index) });
    }
    const assets = { '/': 'index.html', '/styles.css': 'styles.css', '/app.js': 'app.js',
      '/views.js': 'views.js', '/evolution.js': 'evolution.js', '/assessments.js': 'assessments.js', '/trace.js': 'trace.js',
      '/sample-data.json': 'sample-data.json' };
    if (!assets[name]) return route.fallback();
    const contentType = name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css' :
      name.endsWith('.json') ? 'application/json' : 'text/html';
    return route.fulfill({ contentType, body: fs.readFileSync(path.join('dashboard', assets[name])) });
  });
  await page.goto(`${origin}${pathname}`);
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

test('detected Skills are selectable before any evaluation and do not invent history', async ({ page }) => {
  const skills = ['first', 'second'].map((name, i) => ({
    skill_key: `path:${String(i).repeat(24)}`, display_name: name, source_path: `skills/${name}`,
  }));
  await page.route('http://dashboard.test/results/project-b/index.json', route => route.fulfill({
    json: { schema_version: 1, history: [] },
  }));
  await pageWith(page, { schema_version: 1, projects: [{
    id: 'project-b', state: 'active', history_count: 0, current_run: null, detected_skills: skills,
  }] });
  await expect(page.locator('#skill-select')).toBeEnabled();
  await expect(page.locator('#skill-select option')).toHaveCount(2);
  await page.locator('#skill-select').selectOption(skills[1].skill_key);
  await expect(page.locator('#skill-select')).toHaveValue(skills[1].skill_key);
  await expect(page.locator('#origin')).toHaveText('미평가');
  await expect(page.locator('#selection-summary')).toContainText('second');
  await expect(page.locator('#detail')).toBeHidden();
  await expect(page.locator('#history-count')).toHaveText('0건');
  await expect(page.locator('#project-summary')).toBeVisible();
  await expect(page.locator('#summary-history')).toContainText('평가 기록 0건');
  await expect(page.locator('#error')).toBeHidden();
});

test('choosing an unevaluated detected Skill clears a previously visible run', async ({ page }) => {
  const report = historyRun(1, 'baseline', 'completed', 'completed');
  await page.route('http://dashboard.test/results/sample_repo/index.json', route => route.fulfill({
    json: { schema_version: 1, history: [{ ...report, execution_status: 'completed', guide_status: 'completed' }] },
  }));
  await page.route('http://dashboard.test/results/sample_repo/1-1/report.json', route => route.fulfill({ json: report }));
  const key = `path:${'a'.repeat(24)}`;
  await pageWith(page, { schema_version: 1, projects: [{
    id: 'sample_repo', state: 'active', history_count: 1, current_run: null,
    detected_skills: [{ skill_key: key, display_name: 'unassessed', source_path: 'skills/unassessed' }],
  }] });
  await expect(page.locator('#detail')).toBeVisible();
  await expect(page.locator('#skill-select option')).toHaveCount(1);
  await expect(page.locator('#skill-list .skill-item[aria-pressed="true"]')).toHaveCount(0);
  await page.locator('#skill-select').selectOption(key);
  await expect(page.locator('#detail')).toBeHidden();
  await expect(page.locator('#selection-summary')).toContainText('unassessed');
  await expect(page.locator('#origin')).toHaveText('미평가');
  await expect(page.locator('#error')).toBeHidden();
});

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

async function nativeExamplePage(page) {
  const projects = [];
  for (const id of ['project-a', 'project-b']) {
    const stored = JSON.parse(fs.readFileSync(`results/${id}/index.json`, 'utf8'));
    const history = stored.history.filter(run => run.origin === 'sample');
    projects.push({ ...stored.project, history_count: history.length, current_run: null });
    await page.route(`https://dashboard.test/results/${id}/index.json`, route => route.fulfill({
      json: { schema_version: 1, history },
    }));
    for (const run of history) {
      for (const name of ['report.json', 'skill-assessments.json', 'skill-evolution.json']) {
        await page.route(`https://dashboard.test/results/${id}/${run.run_id}/${name}`, route => route.fulfill({
          contentType: 'application/json', body: fs.readFileSync(`results/${id}/${run.run_id}/${name}`),
        }));
      }
    }
  }
  await pageWith(page, { schema_version: 1, projects }, 200, 'https://dashboard.test');
  return projects;
}

test('native result examples show baseline project evaluation and all45 candidates without sample mode', async ({ page }) => {
  const projects = await nativeExamplePage(page);
  const decisions = ['품질 점수 상승', '개선 미확인', '후보 거절'];
  for (const project of projects) {
    await page.locator(`.project[data-project="${project.id}"]`).click();
    await expect(page.locator('#detail')).toBeVisible();
    await expect(page.locator('#skill-select option')).toHaveCount(project.detected_skills.length);
    await expect(page.locator('#project-summary')).toBeVisible();
    await expect(page.locator('#summary-skills')).toContainText(`Skill ${project.detected_skills.length}개`);
    await expect(page.locator('#project-summary .summary-warning')).toHaveCount(0);
    await expect(page.locator('#sample-banner')).toBeHidden();
    await expect(page.locator('#sample-project-select')).toHaveCount(0);
    for (const skill of project.detected_skills) {
      await page.locator('#skill-select').selectOption(skill.skill_key);
      await expect(page.locator('#skill-history-runs .skill-run')).toHaveCount(3);
      for (let i = 0; i < 3; i++) {
        await page.locator('#skill-history-runs .skill-run').nth(i).click();
        await expect(page.locator('#guide tbody tr')).toHaveCount(7);
        await expect(page.locator('#execution')).toContainText(decisions[i]);
        await expect(page.locator('#improvement-evidence')).toContainText('후보 생성: generated');
        await expect(page.locator('#task-results')).toContainText('원본 프로젝트');
        await expect(page.locator('#skill-changes')).toContainText('Verification workflow');
        await expect(page.locator('#cost-card .big-number')).toBeVisible();
        await expect(page.locator('#origin')).toHaveText('저장된 평가');
        await expect(page.locator('#provenance')).toContainText('sample');
        await expect(page.locator('#error')).toBeHidden();
      }
      await page.getByRole('button', { name: '변경 비교', exact: true }).click();
      await expect(page.locator('#skill-diff')).toContainText('+');
    }
  }
});

test('native result examples remain usable on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await nativeExamplePage(page);
  await page.locator('.project[data-project="project-b"]').click();
  await expect(page.locator('#detail')).toBeVisible();
  await expect(page.locator('#skill-select option')).toHaveCount(14);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test('duplicate detected identities fail rather than merge different Skill paths', async ({ page }) => {
  const key = `path:${'b'.repeat(24)}`;
  await pageWith(page, { schema_version: 1, projects: [{
    id: 'project-b', state: 'active', history_count: 0, current_run: null,
    detected_skills: ['first', 'second'].map(name => ({
      skill_key: key, display_name: name, source_path: `skills/${name}`,
    })),
  }] });
  await expect(page.locator('#error')).toBeVisible();
  await expect(page.locator('#detail')).toBeHidden();
});

test('late discovery errors stay with their project instead of contaminating a new selection', async ({ page }) => {
  let finish, started;
  const waiting = new Promise(resolve => { started = resolve; });
  const release = new Promise(resolve => { finish = resolve; });
  const report = { ...historyRun(1, 'baseline', 'completed', 'completed'), project_id: 'project-a' };
  await page.route('http://dashboard.test/results/project-a/index.json', route => route.fulfill({
    json: { schema_version: 1, history: [{ ...report, guide_status: 'completed', execution_status: 'completed' }] },
  }));
  await page.route('http://dashboard.test/results/project-a/1-1/report.json', async route => {
    started();
    await release;
    await route.fulfill({ json: report });
  });
  await page.route('http://dashboard.test/results/project-b/index.json', route => route.fulfill({
    json: { schema_version: 1, history: [] },
  }));
  await pageWith(page, { schema_version: 1, projects: [
    { id: 'project-a', state: 'active', history_count: 1, current_run: null,
      detected_skills: [], skill_discovery_error: 'unsafe_skill_path' },
    { id: 'project-b', state: 'active', history_count: 0, current_run: null, detected_skills: [] },
  ] });
  await waiting;
  await page.locator('.project[data-project="project-b"]').click();
  await expect(page.locator('#selection-summary')).toHaveText('평가 기록이 없습니다.');
  const pending = page.waitForResponse('http://dashboard.test/results/project-a/1-1/report.json');
  finish();
  const response = await pending;
  await response.finished();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await expect(page.locator('#project-title')).toHaveText('project-b');
  await expect(page.locator('#error')).toBeHidden();
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
  await expect(page.locator('#catalog-overview, main table')).toHaveCount(0);
  await expect(page.locator('#project-summary')).toBeHidden();
  await expect(page.locator('#project-summary')).toBeEmpty();
  await expect(page.locator('#detail')).toBeHidden();
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

test('exact deep link survives refresh without selecting a newer successful run', async ({ page }) => {
  await historyPage(page, [historyRun(2, 'baseline', 'completed'), historyRun(1, 'baseline', 'blocked')], true);
  const query = '?project=sample_repo&run=1-1&skill=legacy%3Asample_repo%3Adevelop';
  await page.goto(`http://dashboard.test/${query}`);
  await expect(page.locator('#report-link')).toHaveAttribute('href', '/results/sample_repo/1-1/report.json');
  await page.getByRole('button', { name: '새로고침', exact: true }).click();
  await expect(page.locator('#report-link')).toHaveAttribute('href', '/results/sample_repo/1-1/report.json');
  await page.reload();
  await expect(page.locator('#report-link')).toHaveAttribute('href', '/results/sample_repo/1-1/report.json');
  await page.locator('#skill-history-runs [data-run="2-1"]').click();
  await expect(page).toHaveURL(/run=2-1/);
  await page.reload();
  await expect(page.locator('#report-link')).toHaveAttribute('href', '/results/sample_repo/2-1/report.json');
});

for (const query of [
  '?project=missing&run=1-1', '?project=sample_repo&run=999-1',
  '?project=sample_repo&run=1-1&skill=other%3Askill', '?run=1-1',
  '?project=sample_repo&run=1-1&run=2-1', '?project=sample_repo&run=..%2Freport.json',
]) {
  test(`invalid deep link does not fall back: ${query}`, async ({ page }) => {
    await historyPage(page, [historyRun(1, 'baseline', 'completed')], true);
    await page.goto(`http://dashboard.test/${query}`);
    await expect(page.locator('#error')).toBeVisible();
    await expect(page.locator('#detail')).toBeHidden();
    await expect(page.locator('#report-link')).not.toHaveAttribute('href', /report.json/);
  });
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

test('complete Skill bundles share a two-MiB bound across summary and detail without enlarging report reads', async ({ page }) => {
  const fixture = evolutionFixture({ baseText: 'x'.repeat(800000) });
  const raw = JSON.stringify(fixture.envelope);
  expect(Buffer.byteLength(raw)).toBeGreaterThan(1048576);
  expect(Buffer.byteLength(raw)).toBeLessThan(2097152);
  await evolutionPage(page, fixture);
  await expect(page.locator('#detail')).toBeVisible();
  await expect(page.locator('#error')).toBeHidden();
  await expect(page.locator('#skill-changes')).toContainText('표시 한도');
  await expect(page.locator('#project-summary .summary-scope')).toContainText('근거를 확인한 실행 1건');
  await expect(page.locator('#project-summary .summary-warning')).toHaveCount(0);
  const folder = `https://dashboard.test/results/sample_repo/${fixture.report.run_id}`;
  await page.route(`${folder}/skill-evolution.json`, route => route.fulfill({
    contentType: 'application/json', body: ' '.repeat(2097152) + raw,
  }));
  await page.reload();
  await expect(page.locator('#error')).toBeVisible();
  await page.unroute(`${folder}/skill-evolution.json`);
  await expect(page.locator('#project-summary .summary-warning')).toBeVisible();
  await expect(page.locator('#summary-adoptions')).toHaveText('기록 없음');
  await page.route(`${folder}/skill-evolution.json`, route => route.fulfill({ json: fixture.envelope }));
  await page.route(`${folder}/report.json`, route => route.fulfill({
    contentType: 'application/json', body: ' '.repeat(1048576) + JSON.stringify(fixture.report),
  }));
  await page.reload();
  await expect(page.locator('#error')).toBeVisible();
  await expect(page.locator('#project-summary .summary-warning')).toBeVisible();
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

// Contract fixtures only: never published as model measurements or adoption evidence.
const publicBytes = value => JSON.stringify(sortKeys(value), null, 2).replace(/[\u007f-\uffff]/g,
  char => `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`) + '\n';
const H = value => hash(publicBytes(value));
const replayPolicyHash = require('node:child_process').execFileSync('python3', ['-c',
  'from candidates import POLICY; from project_results import encoded; from hashlib import sha256; print(sha256(encoded({"policy": POLICY, "rule": "replay-v1"})).hexdigest())',
], { encoding: 'utf8' }).trim();
function traceFixture({ mode = 'offline_test', use = 'verified', unchangedSecond = false, policyHash = replayPolicyHash } = {}) {
  const key = 'skillops:develop', project = 'sample_repo', source = '.github/skills/develop';
  const cycleId = '104-1';
  const items = [];
  const make = (run, body) => {
    const item = evolutionFixture({ full: true, candidateText: body });
    Object.assign(item.report, { run_id: run, purpose: 'project_assessment', origin: 'github_actions',
      created_at: `2026-09-17T12:00:${run.slice(1, 3)}Z`, source_commit: 'a'.repeat(40),
      evaluator_sha256: hash('evaluator'), project_tree_sha256: hash('project'),
      source_report_sha256: null, source_schema_version: null,
      guide: { status: 'completed', reason_code: 'evaluation_completed', metrics: null, decision: null },
      execution: { status: 'completed', reason_code: 'evaluation_completed', metrics: null, decision: null } });
    Object.assign(item.envelope, { run_id: run, report_sha256: H(item.report) });
    item.envelope.records.generations = [];
    item.envelope.records.comparisons = [];
    item.envelope.records.adoptions = [];
    item.summary = { ...item.report, guide_status: 'completed', execution_status: 'completed',
      skill_evolution: `${run}/skill-evolution.json`,
      evolution_skills: item.envelope.bindings.map(({ legacy_skill_id, ...binding }) => ({ ...binding, display_name: 'Develop' })) };
    items.push(item);
    return item;
  };
  const original = evolutionFixture({ full: true }).envelope.bindings[0].base_version_id;
  const checks = { plan_sha256: hash('plan'), environment_sha256: hash('images'), protected_sha256: hash('protected'),
    status: 'completed', cases: [{ id: 'development', status: 'passed' }, { id: 'confirmation', status: 'passed' }],
    gates: [], elapsed_seconds: 1.25 };
  const quality = score => ({ status: 'completed', rubric_sha256: hash('rubric'), context_sha256: hash('context'),
    dimensions: [{ id: 'instruction_quality', score }], findings: [] });
  const reference = (input) => {
    const value = { schema_version: 1, project_id: project, skill_key: key, source_path: source,
      source_commit: 'a'.repeat(40), project_tree_sha256: hash('project'), input_sha256: input,
      original_version_id: original, rubric_sha256: hash('rubric'), quality_context_sha256: hash('context'),
      evaluator_sha256: hash('evaluator'), policy_sha256: policyHash, plan_sha256: hash('plan'),
      environment_sha256: hash('images'), protected_sha256: hash('protected'),
      original_checks: checks, base_quality: quality(2) };
    return { ...value, reference_sha256: H(value) };
  };
  const artifact = (item, name) => ({ project_id: project, run_id: item.report.run_id,
    path: name === 'replay' ? 'replay-evaluation.json' : `${name}.json`, sha256: H(item[name]) });
  let parent = original;
  for (const [i, split, score] of [[1, 'development', 2], [2, 'development', 3], [3, 'confirmation', 3]]) {
    const item = make(`${100 + i}-1`, i === 1 || unchangedSecond ? 'Round one\n' : 'Round two\n');
    const version = item.envelope.bindings[0].candidate_version_id, input = hash(split);
    const ref = reference(input);
    const application = id => ({ version_id: id, staged_version_id: id, work_sha256: input,
      output_sha256: hash('output'), activated: true, changed: true, task_outcome: 'satisfied',
      measurement: { cost_nano_aiu: 12.25, elapsed_seconds: 2.25 } });
    item.replay = { schema_version: 1, project_id: project, run_id: item.report.run_id,
      report_sha256: H(item.report), execution_mode: mode, reference: ref,
      generation: i === 3 ? null : { parent_version_id: parent, feedback_sha256: hash(`feedback-${i}`),
        addressed_findings: ['instruction_quality'], hypothesis: '<img src=x onerror="window.traceXss=true">' },
      evaluation: { skill_key: key, source_path: source, base_version_id: original, candidate_version_id: version,
        work: { task_id: split, input_sha256: input, split, provenance: 'recorded',
          checks: { plan_sha256: hash('plan'), protected_sha256: hash('protected'),
            required_case_ids: [split], required_gate_ids: [] } },
        reference_sha256: ref.reference_sha256, quality: { base: quality(2), candidate: quality(score) },
        applications: { base: application(original), candidate: application(version) },
        checks: { original: checks, base: checks, candidate: checks },
        decision: { policy_id: 'replay-v1', status: i === 1 ? 'not_improved' : 'improved',
          reasons: [], regression: { status: 'passed', reasons: [], regressions: [] } }, errors: [] } };
    item.summary.replay_evaluation = `${item.report.run_id}/replay-evaluation.json`;
    parent = version;
  }
  const cycle = make(cycleId, unchangedSecond ? 'Round one\n' : 'Round two\n');
  cycle.cycle = { schema_version: 1, project_id: project, run_id: cycleId, report_sha256: H(cycle.report),
    execution_mode: mode, cycle_id: cycleId, skill_key: key, source_path: source,
    input_sha256: hash('development'), reference_sha256: items[0].replay.reference.reference_sha256,
    original_version_id: original, max_rounds: 2,
    budget: { max_invocations: 30, max_seconds: 600, max_ai_credits_per_session: null },
    rounds: items.slice(0, 2).map((item, i) => ({ round_id: `${cycleId}-r${i + 1}`, round_number: i + 1,
      run_id: item.report.run_id, parent_version_id: item.replay.generation.parent_version_id,
      candidate_version_id: item.replay.evaluation.candidate_version_id, input_sha256: hash('development'),
      reference_sha256: item.replay.reference.reference_sha256, feedback_source_round_id: i ? `${cycleId}-r1` : null,
      feedback_sha256: item.replay.generation.feedback_sha256, evaluation_ref: artifact(item, 'replay'),
      decision: item.replay.evaluation.decision, stop_reason: i ? 'improved' : null })),
    stop_reason: 'improved', selected_candidate_version_id: parent, confirmation_ref: artifact(items[2], 'replay'),
    confirmation_status: 'passed' };
  cycle.summary.cycle = `${cycleId}/cycle.json`;
  const adopted = make('105-1', unchangedSecond ? 'Round one\n' : 'Round two\n');
  const approval = { approval_id: 'approval-one', project_id: project, skill_key: key,
    candidate_version_id: parent, cycle_id: cycleId, evidence_sha256: H(cycle.cycle),
    approved_at: '2026-09-17T12:01:00Z', approved_by: 'local_operator', trust_scope: 'local_environment',
    previous_active_version_id: null };
  const receipt = { execution_id: 'execution-one', run_id: '105-1', project_id: project, skill_key: key,
    approval_id: approval.approval_id, evidence_sha256: approval.evidence_sha256, work_input_sha256: hash('next work'),
    approved_version_id: parent, loaded_version_id: use === 'failed' ? original : parent,
    skill_version_verified: use !== 'failed', observed_at: '2026-09-17T12:02:00Z',
    status: use === 'failed' ? 'failed' : 'verified', reason_code: use === 'failed' ? 'skill_version_mismatch' : null };
  adopted.adoption = { schema_version: 1, project_id: project, run_id: '105-1',
    report_sha256: H(adopted.report), execution_mode: mode, approvals: [approval],
    executions: use === 'none' ? [] : [receipt] };
  adopted.summary.adoption = '105-1/adoption.json';
  return { items, cycle, adopted, key };
}
async function tracePage(page, fixture, run = '104-1') {
  const origin = 'https://dashboard.test';
  await page.route(`${origin}/results/sample_repo/index.json`, route => route.fulfill({
    json: { schema_version: 1, history: fixture.items.map(item => item.summary) },
  }));
  for (const item of fixture.items) {
    for (const [field, file] of Object.entries({ report: 'report.json', envelope: 'skill-evolution.json',
      replay: 'replay-evaluation.json', cycle: 'cycle.json', adoption: 'adoption.json' })) {
      if (item[field]) await page.route(`${origin}/results/sample_repo/${item.report.run_id}/${file}`,
        route => route.fulfill({ contentType: 'application/json', body: publicBytes(item[field]) }));
    }
  }
  await pageWith(page, { schema_version: 1, projects: [{ id: 'sample_repo', state: 'active',
    history_count: fixture.items.length, current_run: null }] }, 200, origin,
  `/?project=sample_repo&run=${run}&skill=${encodeURIComponent(fixture.key)}`);
}

test('trace links original, two parents, feedback, confirmation and verified use without claiming task success or Active', async ({ page }) => {
  const fixture = traceFixture({ mode: 'live' });
  await tracePage(page, fixture);
  const panel = page.locator('#evidence-trace');
  await expect(panel).toContainText('최초 원본');
  await expect(panel.locator('[data-round]')).toHaveCount(2);
  await expect(panel).toContainText('104-1-r1');
  await expect(panel).toContainText('confirmation: passed');
  await expect(panel).toContainText('검증된 사용');
  await expect(panel).toContainText('작업 성공을 의미하지 않습니다');
  await expect(panel).toContainText('현재 로컬 Active: 공개 근거로 확인 불가');
  await expect(panel).toContainText('GitHub 운영 적용 권한');
  await expect(page.locator('#summary-adoptions')).toContainText('선택 실행 근거에서 확인');
  await panel.locator('a[data-trace-run="102-1"]').first().click();
  await expect(page).toHaveURL(/run=102-1/);
  await expect(page.locator('#guide')).toContainText('2 → 3');
  await expect(page.locator('#task-results')).toContainText('development');
  await expect(page.locator('#improvement-evidence')).toContainText('<img');
  expect(await page.evaluate(() => window.traceXss)).toBeUndefined();
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  }
});
for (const mode of ['live', 'offline_test']) {
  for (const [use, label] of [['none', '승인됐지만 사용 미기록'], ['failed', '사용 실패']]) {
    test(`trace distinguishes local approval/use: ${mode} ${use}`, async ({ page }) => {
      await tracePage(page, traceFixture({ mode, use }), '105-1');
      await expect(page.locator('#evidence-trace')).toContainText(label);
      await expect(page.locator('#evidence-trace')).not.toContainText('검증된 사용');
      await expect(page.locator('#trace-mode')).toContainText(mode);
    });
  }
}
for (const mode of ['offline_test', 'sample']) {
  test(`trace labels ${mode} separately from live adoption and measured improvement`, async ({ page }) => {
    await tracePage(page, traceFixture({ mode }), '102-1');
    await expect(page.locator('#trace-mode')).toContainText(mode);
    await expect(page.locator('#trace-mode')).toContainText('실측 성과·실제 채택 근거 아님');
    await expect(page.locator('#quality-summary')).not.toContainText('실제 버전 평가');
  });
}
test('legacy missing sidecars stay unrecorded, not N=1 or no approval', async ({ page }) => {
  await evolutionPage(page, evolutionFixture());
  await expect(page.locator('#evidence-trace')).toContainText('반복 평가: 미기록');
  await expect(page.locator('#evidence-trace')).toContainText('승인·사용: 미기록');
  await expect(page.locator('#evidence-trace')).not.toContainText('승인 없음');
  await expect(page.locator('#summary-adoptions')).not.toContainText('채택 기록 없음');
});
for (const [name, mutate] of [
  ['report hash', f => { f.cycle.cycle.report_sha256 = hash('tamper'); }],
  ['foreign project', f => { f.cycle.cycle.rounds[0].evaluation_ref.project_id = 'other'; }],
  ['unsafe path', f => { f.cycle.cycle.rounds[0].evaluation_ref.path = '../replay-evaluation.json'; }],
  ['orphan round', f => { f.cycle.cycle.rounds[0].evaluation_ref.run_id = '999-1'; }],
  ['round hash', f => { f.cycle.cycle.rounds[0].evaluation_ref.sha256 = hash('tamper'); }],
  ['changed parent', f => { f.cycle.cycle.rounds[1].parent_version_id = f.cycle.cycle.original_version_id; }],
  ['false confirmation', f => { f.cycle.cycle.confirmation_status = 'failed'; }],
  ['local identity leak', f => { f.adopted.adoption.approvals[0].approved_by = 'private-user'; }],
  ['private extra field', f => { f.adopted.adoption.environment_id = 'private-environment'; }],
  ['private approval CAS field', f => { f.adopted.adoption.approvals[0].previous_active_execution_sha256 = hash('private receipt'); }],
  ['private execution CAS field', f => { f.adopted.adoption.executions[0].previous_active_execution_sha256 = hash('private receipt'); }],
  ['forged verified mismatch', f => { f.adopted.adoption.executions[0].loaded_version_id = f.cycle.cycle.original_version_id; }],
  ['boolean round cap', f => { f.cycle.cycle.max_rounds = true; }],
  ['array execution identity', f => { f.adopted.adoption.executions[0].execution_id = ['execution-one']; }],
  ['invalid observation date', f => { f.adopted.adoption.executions[0].observed_at = '2026-02-30T12:00:00Z'; }],
]) {
  test(`trace rejects ${name} without falling back`, async ({ page }) => {
    const fixture = traceFixture({ mode: 'live' });
    mutate(fixture);
    await tracePage(page, fixture);
    await expect(page.locator('#trace-error')).toBeVisible();
    await expect(page.locator('#evidence-trace [data-round]')).toHaveCount(0);
    await expect(page.locator('#trace-error')).not.toContainText('private-user');
  });
}

test('public parser rejects duplicate keys, nonfinite numbers and fractional integer metadata but preserves Python hash encoding', async ({ page }) => {
  await evolutionPage(page, evolutionFixture());
  const result = await page.evaluate(async () => {
    const { parsePublic } = await import('/trace.js');
    const rejected = [];
    for (const raw of ['{"schema_version":1,"schema_version":1}\n',
      '{\n  "schema_version": 1.0\n}\n', '{\n  "max_rounds": 1e0\n}\n', '{\n  "cost": 1e999\n}\n']) {
      try { parsePublic(raw); rejected.push(false); } catch { rejected.push(true); }
    }
    const raw = '{\n  "cost": 1.0,\n  "label": "\\ud55c\\uae00",\n  "reference_sha256": "omit"\n}\n';
    const parsed = parsePublic(raw);
    return { rejected, hash: await parsed.hashObject(parsed.value, 'reference_sha256') };
  });
  expect(result.rejected).toEqual([true, true, true, true]);
  expect(result.hash).toBe(hash('{\n  "cost": 1.0,\n  "label": "\\ud55c\\uae00"\n}\n'));
});

for (const bad of ['missing', 'oversize', 'duplicate']) {
  test(`trace ${bad} attachment cannot become a successful older run`, async ({ page }) => {
    const fixture = traceFixture();
    await tracePage(page, fixture);
    await expect(page.locator('#evidence-trace [data-round]')).toHaveCount(2);
    await page.route('https://dashboard.test/results/sample_repo/104-1/cycle.json', route =>
      route.fulfill({ status: bad === 'missing' ? 404 : 200, contentType: 'application/json',
        body: bad === 'oversize' ? ' '.repeat(1048576) + publicBytes(fixture.cycle.cycle) :
          bad === 'duplicate' ? publicBytes(fixture.cycle.cycle).replace('"max_rounds": 2,', '"max_rounds": 1,\n  "max_rounds": 2,') : '{}' }));
    await page.reload();
    await expect(page.locator('#trace-error')).toBeVisible();
    await expect(page).toHaveURL(/run=104-1/);
    await expect(page.locator('#evidence-trace [data-round]')).toHaveCount(0);
  });
}

test('completed confirmation without a published approval is pending, not a claim of no approvals', async ({ page }) => {
  const fixture = traceFixture({ mode: 'live' });
  fixture.items = fixture.items.filter(item => item !== fixture.adopted);
  await tracePage(page, fixture);
  await expect(page.locator('#evidence-trace')).toContainText('승인 대기');
  await expect(page.locator('#evidence-trace')).toContainText('승인·사용: 미기록');
  await expect(page.locator('#evidence-trace')).not.toContainText('승인 없음');
});

test('alternate replay policy is rejected even when all reference digests and lineage match', async ({ page }) => {
  await tracePage(page, traceFixture({ policyHash: hash('different policy') }));
  await expect(page.locator('#trace-error')).toBeVisible();
  await expect(page.locator('#evidence-trace [data-round]')).toHaveCount(0);
});

test('trace fixture opens the requested run without a speculative default navigation', async ({ page }) => {
  const navigations = [];
  page.on('framenavigated', frame => { if (frame === page.mainFrame()) navigations.push(frame.url()); });
  await tracePage(page, traceFixture());
  expect(navigations).toEqual(['https://dashboard.test/?project=sample_repo&run=104-1&skill=skillops%3Adevelop']);
});

for (const status of ['not_run', 'unverified']) {
  test(`confirmation without an artifact preserves ${status} and does not permit approval`, async ({ page }) => {
    const fixture = traceFixture();
    fixture.cycle.cycle.confirmation_ref = null;
    fixture.cycle.cycle.confirmation_status = status;
    fixture.items = fixture.items.filter(item => item !== fixture.adopted && item.report.run_id !== '103-1');
    await tracePage(page, fixture);
    await expect(page.locator('#evidence-trace')).toContainText(`confirmation: ${status}`);
    await expect(page.locator('#evidence-trace')).toContainText('최종 확인 미완료');
    await expect(page.locator('#evidence-trace')).not.toContainText('승인 대기');
    await expect(page.locator('#trace-error')).toHaveCount(0);
  });
}

test('cycle and later use projections remain selectable without redundant local captures or approval copies', async ({ page }) => {
  const fixture = traceFixture({ mode: 'live' });
  const approvalRun = structuredClone(fixture.adopted);
  approvalRun.report.run_id = '106-1';
  approvalRun.report.created_at = '2026-09-17T12:03:00Z';
  approvalRun.adoption.run_id = '106-1';
  approvalRun.adoption.report_sha256 = H(approvalRun.report);
  approvalRun.adoption.executions = [];
  approvalRun.summary = { ...approvalRun.report, guide_status: 'completed', execution_status: 'completed', adoption: '106-1/adoption.json' };
  delete approvalRun.envelope;
  fixture.adopted.adoption.approvals = [];
  for (const item of [fixture.cycle, fixture.adopted]) {
    delete item.envelope;
    delete item.summary.skill_evolution;
    delete item.summary.evolution_skills;
  }
  fixture.items.push(approvalRun);
  await tracePage(page, fixture, '105-1');
  await expect(page.locator('#evidence-trace')).toContainText('검증된 사용');
  await expect(page.locator('#evidence-trace [data-round]')).toHaveCount(2);
  await expect(page.locator('#skill-select')).toHaveValue(fixture.key);
  await expect(page.locator('#trace-error')).toHaveCount(0);
});

test('same parent bytes cannot become a newly generated improved round even when every digest matches', async ({ page }) => {
  await tracePage(page, traceFixture({ mode: 'live', unchangedSecond: true }));
  await expect(page.locator('#trace-error')).toBeVisible();
  await expect(page.locator('#evidence-trace [data-round]')).toHaveCount(0);
});

test('approval-only Skill remains selectable alongside an unrelated captured Skill', async ({ page }) => {
  const fixture = traceFixture({ mode: 'live' }), item = fixture.adopted;
  const other = 'other:captured';
  for (const row of [...item.envelope.records.identities, ...item.envelope.records.sources,
    ...item.envelope.records.skill_versions, ...item.envelope.bindings, ...item.summary.evolution_skills]) row.skill_key = other;
  await tracePage(page, fixture, '105-1');
  await expect(page.locator('#skill-select')).toHaveValue(fixture.key);
  await expect(page.locator('#evidence-trace')).toContainText('검증된 사용');
  await expect(page.locator('#evolution-metadata')).toBeHidden();
  await expect(page.locator('#skill-changes')).not.toContainText('Round two');
  await expect(page.locator('#error')).toBeHidden();
});

test('interrupted generation retains round and cap without inventing a candidate or confirmation', async ({ page }) => {
  const fixture = traceFixture();
  const cycle = fixture.cycle.cycle;
  cycle.rounds = [{ ...cycle.rounds[0], candidate_version_id: null, evaluation_ref: null, decision: null, stop_reason: 'call_limit' }];
  Object.assign(cycle, { stop_reason: 'call_limit', selected_candidate_version_id: null, confirmation_ref: null, confirmation_status: 'not_run' });
  fixture.items = [fixture.cycle];
  await tracePage(page, fixture);
  await expect(page.locator('#evidence-trace')).toContainText('종료 사유: call_limit');
  await expect(page.locator('#evidence-trace')).toContainText('후보: 미기록');
  await expect(page.locator('#evidence-trace')).toContainText('confirmation: not_run');
  await expect(page.locator('#trace-error')).toHaveCount(0);
});

for (const target of ['project', 'skill']) {
  test(`late cycle response cannot replace a different ${target} selection`, async ({ page }) => {
    const fixture = traceFixture();
    await tracePage(page, fixture);
    await expect(page.locator('#evidence-trace [data-round]')).toHaveCount(2);
    const other = 'other:skill';
    await page.route('https://dashboard.test/results/index.json', route => route.fulfill({ json: {
      schema_version: 1, projects: [
        { id: 'sample_repo', state: 'active', history_count: 5, current_run: null,
          detected_skills: [{ skill_key: other, display_name: 'Other', source_path: 'skills/other' }] },
        { id: 'other-project', state: 'active', history_count: 0, current_run: null },
      ],
    } }));
    await page.route('https://dashboard.test/results/other-project/index.json', route => route.fulfill({
      json: { schema_version: 1, history: [] },
    }));
    let release, started;
    const waiting = new Promise(resolve => { started = resolve; });
    const gate = new Promise(resolve => { release = resolve; });
    await page.route('https://dashboard.test/results/sample_repo/104-1/cycle.json', async route => {
      started(); await gate; await route.fulfill({ contentType: 'application/json', body: publicBytes(fixture.cycle.cycle) });
    });
    await page.getByRole('button', { name: '새로고침', exact: true }).click();
    await waiting;
    if (target === 'project') await page.locator('[data-project="other-project"]').click();
    else await page.locator('#skill-select').selectOption(other);
    await expect(page.locator('#detail')).toBeHidden();
    const response = page.waitForResponse('**/104-1/cycle.json');
    release(); await response;
    await expect(page.locator('#detail')).toBeHidden();
    if (target === 'project') await expect(page.locator('#project-title')).toHaveText('other-project');
    else await expect(page.locator('#skill-select')).toHaveValue(other);
    await expect(page.locator('#error')).toBeHidden();
  });
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

test('equal code outputs do not present a quality score increase as developer productivity', async ({ page }) => {
  const fixture = assessmentFixture();
  const row = fixture.details.skills[0];
  row.applications.candidate.output_sha256 = row.applications.base.output_sha256;
  await page.route(`https://dashboard.test/results/sample_repo/${fixture.report.run_id}/skill-assessments.json`,
    route => route.fulfill({ json: fixture.details }));
  await evolutionPage(page, fixture);
  await expect(page.locator('#task-results')).toContainText('코드 출력 해시 동일');
  await expect(page.locator('#task-results')).toContainText('생산성 향상의 증거가 아닙니다');
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

function projectSummaryFixture() {
  const imported = evolutionFixture();
  const completed = historyRun(6, 'project_assessment', 'completed', 'completed');
  completed.source_commit = 'abcdef0123456789'.repeat(2) + 'abcdef01';
  const legacy = historyRun(2, 'baseline', 'completed');
  legacy.origin = 'historical_import';
  const reports = [completed, historyRun(5, 'project_assessment', 'blocked', 'blocked'),
    historyRun(4, 'project_assessment', 'configuration_required'), historyRun(3, 'baseline', 'blocked'), legacy];
  const files = {}, summaries = reports.map(report => ({ ...report,
    execution_status: report.execution.status, guide_status: report.guide.status }));
  for (const report of reports) files[`/results/sample_repo/${report.run_id}/report.json`] = report;
  for (const report of [completed, legacy]) {
    const content = 'Reviewed legacy instructions\n', summary = summaries.find(row => row.run_id === report.run_id);
    Object.assign(summary, { skill_id: 'develop', skill_snapshots: `${report.run_id}/skill-snapshots.json`,
      base_skill_sha256: hash(content), candidate_skill_sha256: null });
    files[`/results/sample_repo/${summary.skill_snapshots}`] = { schema_version: 1, project_id: 'sample_repo',
      run_id: report.run_id, report_sha256: hash(JSON.stringify(report)), skill_id: 'develop',
      base: { content, sha256: hash(content) }, candidate: null };
  }
  summaries.push(imported.summary);
  files[`/results/sample_repo/${imported.report.run_id}/report.json`] = imported.report;
  files[`/results/sample_repo/${imported.summary.skill_evolution}`] = imported.envelope;
  files['/results/sample_repo/index.json'] = { schema_version: 1, history: summaries };
  files['/results/waiting-project/index.json'] = { schema_version: 1, history: [] };
  return { files, completed, imported, catalog: { schema_version: 1, projects: [
    { id: 'sample_repo', state: 'active', current_run: completed.run_id, history_count: summaries.length },
    { id: 'waiting-project', state: 'blocked', current_run: null, history_count: 0 },
  ] } };
}

async function projectSummaryPage(page, fixture) {
  await page.route('https://dashboard.test/results/**', route => {
    const value = fixture.files[new URL(route.request().url()).pathname];
    return value === undefined ? route.fallback() : route.fulfill({ json: value });
  });
  await pageWith(page, fixture.catalog, 200, 'https://dashboard.test');
  await expect(page.locator('#summary-history')).toBeVisible();
}

test('landing opens the first project summary directly without a catalog overview or loading other projects', async ({ page }) => {
  const fixture = projectSummaryFixture(), requested = [];
  page.on('request', request => requested.push(new URL(request.url()).pathname));
  await projectSummaryPage(page, fixture);
  await expect(page.locator('#project-title')).toHaveText('sample_repo');
  await expect(page.locator('.intro + #project-summary + .section-nav')).toHaveCount(1);
  await expect(page.locator('#detail')).toBeVisible();
  await expect(page.locator('#report-link')).toHaveAttribute('href', `/results/sample_repo/${fixture.completed.run_id}/report.json`);
  await expect(page.locator('#catalog-overview, #overview-back')).toHaveCount(0);
  await expect(page.getByRole('link', { name: '← 전체 프로젝트', exact: true })).toHaveCount(0);
  await expect(page.locator('#projects .project')).toHaveCount(2);
  await expect(page.locator('#projects [data-project="sample_repo"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#summary-history')).toHaveText('평가 기록 6건 · 완료 1 · 차단 3 · 과거 가져오기 2 · 미평가 0');
  expect(requested.filter(url => url.startsWith('/results/waiting-project/'))).toEqual([]);
  expect(requested.filter(url => url === '/results/sample_repo/index.json')).toHaveLength(1);
});

test('projects without assessments show unevaluated decisions and no numeric candidate verdict in the project card', async ({ page }) => {
  const fixture = projectSummaryFixture();
  await projectSummaryPage(page, fixture);
  await expect(page.locator('#summary-decisions')).toHaveText('후보 판정 없음');
  await expect(page.locator('#summary-decisions')).not.toHaveText(/\d/);
  await expect(page.locator('#summary-skills')).toHaveText('Skill 2개 · 버전 3개');
  await expect(page.locator('#summary-history')).toHaveText('평가 기록 6건 · 완료 1 · 차단 3 · 과거 가져오기 2 · 미평가 0');
  const stamp = await page.evaluate(async value => (await import('/views.js')).stamp(value), fixture.completed.created_at);
  await expect(page.locator('#summary-completed')).toContainText(stamp);
  await expect(page.locator('#summary-completed code')).toHaveText(fixture.completed.source_commit.slice(0, 12));
  await expect(page.locator('#summary-completed code')).toHaveAttribute('title', fixture.completed.source_commit);
  await expect(page.locator('#summary-adoptions')).toHaveText('기록 없음');
  await expect(page.locator('#summary-usage')).toHaveText('기존 → 후보 비용 미기록 / 시간 미기록');
  await expect(page.locator('#project-summary details')).toHaveJSProperty('open', false);
  await expect(page.locator('#project-summary details summary')).toHaveText('이 요약은 무엇을 세나요?');
  await expect(page.locator('#project-summary details p')).toBeHidden();
});

test('sidebar and keyboard selection replace the project summary without retaining another project counts', async ({ page }) => {
  await projectSummaryPage(page, projectSummaryFixture());
  await page.locator('#projects [data-project="waiting-project"]').press('Enter');
  await expect(page.locator('#project-title')).toHaveText('waiting-project');
  await expect(page.locator('#summary-skills')).toHaveText('Skill 0개 · 버전 0개');
  await expect(page.locator('#summary-history')).toHaveText('평가 기록 0건 · 완료 0 · 차단 0 · 과거 가져오기 0 · 미평가 0');
  await expect(page.locator('#summary-completed')).toHaveText('없음');
  await expect(page.locator('#summary-decisions')).toHaveText('후보 판정 없음');
  await expect(page.locator('#detail')).toBeHidden();
  await page.locator('#projects [data-project="sample_repo"]').click();
  await expect(page.locator('#project-title')).toHaveText('sample_repo');
  await expect(page.locator('#projects [data-project="sample_repo"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#summary-history')).toHaveText('평가 기록 6건 · 완료 1 · 차단 3 · 과거 가져오기 2 · 미평가 0');
  await expect(page.locator('#detail')).toBeVisible();
  await expect(page.locator('#project-summary')).toHaveCount(1);
});

test('sample remains opt-in and returning to real records restores only the selected project summary', async ({ page }) => {
  const sample = JSON.parse(fs.readFileSync('dashboard/sample-data.json', 'utf8'));
  await projectSummaryPage(page, projectSummaryFixture());
  await page.getByRole('button', { name: '샘플 화면 보기', exact: true }).click();
  await expect(page.locator('#sample-banner')).toBeVisible();
  await expect(page.locator('#project-summary')).toBeHidden();
  await expect(page.locator('#project-summary')).toBeEmpty();
  await expect(page.locator('#quality-summary')).toHaveText('선택 Skill · 합성 평가 예시');
  await page.getByRole('button', { name: '실제 기록으로 돌아가기', exact: true }).click();
  await expect(page.locator('#project-title')).toHaveText('sample_repo');
  await expect(page.locator('#summary-history')).toHaveText('평가 기록 6건 · 완료 1 · 차단 3 · 과거 가져오기 2 · 미평가 0');
  await expect(page.locator('#summary-decisions')).toHaveText('후보 판정 없음');
  await expect(page.locator('#sample-banner')).toBeHidden();
  await expect(page.locator('#projects')).not.toContainText(sample.project_id);
  await expect(page.locator('#projects .project')).toHaveCount(2);
  await expect(page.locator('#project-summary')).not.toContainText(sample.project_id);
  await expect(page.locator('#detail')).toBeVisible();
  await expect(page.locator('#quality-summary')).toHaveText('기록된 검사만 표시');
});

test('the project summary stays within a 390px viewport without an all-projects table', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await projectSummaryPage(page, projectSummaryFixture());
  await expect(page.locator('#summary-history')).toBeVisible();
  await expect(page.locator('#catalog-overview')).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test('overlapping history categories use a union for unevaluated rows instead of subtracting overlapping counts', async ({ page }) => {
  const fixture = projectSummaryFixture();
  const history = fixture.files['/results/sample_repo/index.json'].history;
  history.find(row => row.run_id === fixture.completed.run_id).origin = 'historical_import';
  const unassessed = historyRun(7, 'baseline', 'not_assessed');
  history.push({ ...unassessed, guide_status: 'not_assessed', execution_status: 'not_assessed' });
  fixture.catalog.projects[0].history_count = history.length;
  await projectSummaryPage(page, fixture);
  await expect(page.locator('#summary-history')).toHaveText('평가 기록 7건 · 완료 1 · 차단 3 · 과거 가져오기 3 · 미평가 1');
});

test('project adoption counts include only validated non-unknown observations and never derive adoption from a verdict', async ({ page }) => {
  const fixture = projectSummaryFixture(), records = fixture.imported.envelope.records;
  const known = { ...records.adoptions[0], state: 'entrypoint_pin_observed', observed_at: '2026-09-15T05:00:00Z',
    evidence_kind: 'registry_snapshot', registry_sha256: hash('reviewed registry'),
    entrypoint_sha256: records.versions[0].files[0].sha256, version_id: records.versions[0].version_id };
  records.adoptions.push(known);
  await projectSummaryPage(page, fixture);
  await expect(page.locator('#summary-adoptions')).toHaveText('1건');
  await expect(page.locator('#summary-decisions')).toHaveText('후보 판정 없음');
  await expect(page.locator('#project-summary')).not.toContainText('채택 완료');
});

test('an unreadable selected project index leaves explicit missing evidence instead of invented zero counts', async ({ page }) => {
  const fixture = projectSummaryFixture();
  fixture.files['/results/waiting-project/index.json'].schema_version = 2;
  await projectSummaryPage(page, fixture);
  await expect(page.locator('#error')).toBeHidden();
  await page.locator('#projects [data-project="waiting-project"]').click();
  await expect(page.locator('#error')).toBeVisible();
  await expect(page.locator('#project-summary')).toContainText('기록 없음');
  await expect(page.locator('#project-summary dd')).toHaveCount(0);
  await expect(page.locator('#detail')).toBeHidden();
  await page.locator('#projects [data-project="sample_repo"]').click();
  await expect(page.locator('#summary-history')).toBeVisible();
  await expect(page.locator('#error')).toBeHidden();
});

test('late project history responses cannot reopen a real summary over an active sample view', async ({ page }) => {
  const fixture = projectSummaryFixture();
  let release, arrived;
  const pending = new Promise(resolve => { release = resolve; });
  const requested = new Promise(resolve => { arrived = resolve; });
  await page.route('https://dashboard.test/results/**', async route => {
    const name = new URL(route.request().url()).pathname;
    if (name === '/results/sample_repo/index.json') { arrived(); await pending; }
    const value = fixture.files[name];
    return value === undefined ? route.fallback() : route.fulfill({ json: value });
  });
  await pageWith(page, fixture.catalog, 200, 'https://dashboard.test');
  await requested;
  await page.getByRole('button', { name: '샘플 화면 보기', exact: true }).click();
  await expect(page.locator('#sample-banner')).toBeVisible();
  const response = page.waitForResponse('https://dashboard.test/results/sample_repo/index.json');
  release(); await response;
  await expect(page.locator('#project-summary')).toBeHidden();
  await expect(page.locator('#project-summary')).toBeEmpty();
  await expect(page.locator('#project-title')).toContainText('샘플');
  await expect(page.locator('#origin')).toHaveText('합성 샘플');
});

function assessmentCatalog(count, { legacy = true } = {}) {
  const initial = assessmentFixture({ legacy }), fixtures = [], files = {};
  for (let index = 0; index < count; index++) {
    const fixture = JSON.parse(JSON.stringify(initial));
    const run = `${800 + index}-1`, created = new Date(Date.UTC(2026, 8, 17, 6, index)).toISOString();
    Object.assign(fixture.report, { run_id: run, created_at: created });
    for (const document of [fixture.envelope, fixture.details]) {
      document.run_id = run; document.report_sha256 = hash(JSON.stringify(fixture.report));
    }
    Object.assign(fixture.summary, { run_id: run, created_at: created,
      skill_evolution: `${run}/skill-evolution.json`, skill_assessments: `${run}/skill-assessments.json` });
    files[`/results/sample_repo/${run}/report.json`] = fixture.report;
    files[`/results/sample_repo/${run}/skill-evolution.json`] = fixture.envelope;
    files[`/results/sample_repo/${run}/skill-assessments.json`] = fixture.details;
    fixtures.push(fixture);
  }
  files['/results/sample_repo/index.json'] = { schema_version: 1, history: fixtures.map(fixture => fixture.summary) };
  return { fixtures, files, catalog: { schema_version: 1, projects: [
    { id: 'sample_repo', state: 'active', current_run: null, history_count: count },
  ] } };
}

test('only the selected project assessment evidence is loaded and cached results are reused when returning', async ({ page }) => {
  const fixture = assessmentCatalog(7), requested = [];
  fixture.catalog.projects.push({ id: 'waiting-project', state: 'blocked', current_run: null, history_count: 0 });
  fixture.files['/results/waiting-project/index.json'] = { schema_version: 1, history: [] };
  page.on('request', request => requested.push(new URL(request.url()).pathname));
  await projectSummaryPage(page, fixture);
  for (const name of ['report.json', 'skill-evolution.json', 'skill-assessments.json']) {
    expect(requested.filter(url => url.endsWith(`/${name}`)).sort())
      .toEqual(fixture.fixtures.map(item => `/results/sample_repo/${item.report.run_id}/${name}`).sort());
  }
  await expect(page.locator('#summary-decisions')).toHaveText('상승 7 · 변화 없음 0 · 검증 불충분 0 · 거절 0');
  await expect(page.locator('#skill-select')).toHaveValue('skillops:develop');
  expect(requested.filter(url => url.endsWith('/skill-assessments.json'))).toHaveLength(7);
  expect(requested.filter(url => url.startsWith('/results/waiting-project/'))).toEqual([]);
  await page.locator('#projects [data-project="waiting-project"]').click();
  await expect(page.locator('#summary-decisions')).toHaveText('후보 판정 없음');
  await page.locator('#projects [data-project="sample_repo"]').click();
  await expect(page.locator('#summary-decisions')).toHaveText('상승 7 · 변화 없음 0 · 검증 불충분 0 · 거절 0');
  expect(requested.filter(url => url === '/results/sample_repo/index.json')).toHaveLength(1);
  expect(requested.filter(url => url === '/results/waiting-project/index.json')).toHaveLength(1);
  expect(requested.filter(url => url.endsWith('/skill-assessments.json'))).toHaveLength(7);
});

test('project assessment summaries count stored decisions and compare only the newest Skill rather than summing usage', async ({ page }) => {
  const fixture = assessmentCatalog(4), rows = fixture.fixtures.map(item => item.details.skills[0]);
  rows[1].quality.candidate = JSON.parse(JSON.stringify(rows[1].quality.base));
  rows[1].decision.status = 'not_improved';
  rows[2].quality.candidate.dimensions[0].score = 1;
  Object.assign(rows[2].decision, { status: 'rejected', reasons: ['quality_regression'] });
  rows[3].errors.push({ stage: 'work', code: 'incomplete_fixture' });
  Object.assign(rows[3].decision, { status: 'unverified', reasons: ['incomplete_stage'] });
  for (const row of rows) {
    row.applications.base.measurement = { cost_nano_aiu: 100e9, elapsed_seconds: 10000 };
    row.applications.candidate.measurement = { cost_nano_aiu: 1e9, elapsed_seconds: 1 };
  }
  rows[3].applications.base.measurement = { cost_nano_aiu: 1e9, elapsed_seconds: 100 };
  rows[3].applications.candidate.measurement = { cost_nano_aiu: 1.16e9, elapsed_seconds: 132 };
  await projectSummaryPage(page, fixture);
  await expect(page.locator('#summary-decisions')).toHaveText('상승 1 · 변화 없음 1 · 검증 불충분 1 · 거절 1');
  await expect(page.locator('#summary-usage')).toContainText('기존 → 후보 비용 +16% / 시간 +32% (현장 계산)');
  await expect(page.locator('#summary-usage small')).toContainText(rows[3].skill_key);
  await expect(page.locator('#summary-usage')).not.toContainText('합계');
  await expect(page.locator('#cost-card .big-number')).toHaveText('+16%');
  await expect(page.locator('#time-card .big-number')).toHaveText('+32%');
  await expect(page.locator('#summary-adoptions')).toHaveText('기록 없음');
});

test('project assessment summary changes stay missing for absent measurements or a zero baseline', async ({ page }) => {
  const fixture = assessmentCatalog(1), row = fixture.fixtures[0].details.skills[0];
  row.applications.base.measurement = { cost_nano_aiu: 0, elapsed_seconds: 100 };
  row.applications.candidate.measurement = { cost_nano_aiu: 1e9, elapsed_seconds: null };
  await projectSummaryPage(page, fixture);
  await expect(page.locator('#summary-usage')).toContainText('기존 → 후보 비용 미기록 / 시간 미기록');
  await expect(page.locator('#summary-usage')).not.toContainText('현장 계산');
  await expect(page.locator('#summary-usage')).not.toContainText('%');
});

test('a forged newest assessment stays unevaluated instead of borrowing an older candidate decision or usage', async ({ page }) => {
  const fixture = assessmentCatalog(2);
  fixture.fixtures[1].details.report_sha256 = '0'.repeat(64);
  await projectSummaryPage(page, fixture);
  await expect(page.locator('#summary-decisions')).toHaveText('상승 1 · 변화 없음 0 · 검증 불충분 0 · 거절 0');
  await expect(page.locator('#summary-usage')).toHaveText('기존 → 후보 비용 미기록 / 시간 미기록');
  await expect(page.locator('#project-summary .summary-warning')).toBeVisible();
  await expect(page.locator('#error')).toBeVisible();
  await expect(page.locator('#detail')).toBeHidden();
});

test('the project summary preserves a policy-stamped assessment verdict rather than relabelling an efficiency regression as improved', async ({ page }) => {
  const fixture = assessmentCatalog(1, { legacy: false }), row = fixture.fixtures[0].details.skills[0];
  row.applications.base.measurement = { cost_nano_aiu: 1e9, elapsed_seconds: 100 };
  row.applications.candidate.measurement = { cost_nano_aiu: 1.16e9, elapsed_seconds: 132 };
  Object.assign(row.decision, { status: 'unverified', reasons: ['efficiency_regression'] });
  expect(row.decision.policy_version).toBeTruthy();
  await projectSummaryPage(page, fixture);
  await expect(page.locator('#summary-decisions')).toHaveText('상승 0 · 변화 없음 0 · 검증 불충분 1 · 거절 0');
  await expect(page.locator('#summary-usage')).toContainText('기존 → 후보 비용 +16% / 시간 +32% (현장 계산)');
  await expect(page.locator('#execution .decision-title')).toHaveText('검증 불충분');
});
