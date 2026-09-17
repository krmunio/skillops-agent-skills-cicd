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

function assessmentFixture() {
    const { execFileSync } = require('node:child_process');
    const [report, envelope, details] = JSON.parse(execFileSync('python3', ['-c',
      'import sys,json; sys.path.insert(0,"tests"); from test_skill_assessments import fixture; print(json.dumps(fixture()))',
    ], { encoding: 'utf8' }));
    envelope.report_sha256 = details.report_sha256 = hash(JSON.stringify(report));
    const summary = { ...report, execution_status: report.execution.status, guide_status: report.guide.status,
      skill_evolution: `${report.run_id}/skill-evolution.json`,
      skill_assessments: `${report.run_id}/skill-assessments.json`,
      evolution_skills: envelope.bindings.map(({ legacy_skill_id, ...binding }) => ({ ...binding, display_name: 'develop' })) };
    return { report, envelope, details, summary };
  }

  test('automatic Skill assessment shows real quality, frozen work and scoped non-regression', async ({ page }) => {
    const fixture = assessmentFixture();
    await page.route('https://dashboard.test/results/sample_repo/123-1/skill-assessments.json',
      route => route.fulfill({ json: fixture.details }));
    await evolutionPage(page, fixture);
    await expect(page.locator('#quality-summary')).toHaveText('선택 Skill · 실제 버전 평가');
    await expect(page.locator('#guide')).toContainText('2 → 3');
    await expect(page.locator('#improvement-evidence')).toContainText('Boundary guidance is missing.');
    await expect(page.locator('#improvement-evidence')).toContainText('개선 가설 · 검증 결과와 구분');
    await expect(page.locator('#execution')).toContainText('개선 확인');
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
    await expect(page.locator('#evolution-metadata')).toContainText(fixture.envelope.bindings[0].base_version_id);
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
  await expect(page.locator('#evolution-metadata')).not.toContainText(fixture.envelope.bindings[0].candidate_version_id);
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
  await expect(page.locator('#evolution-metadata')).toContainText(hash('registry bytes'));
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
  await expect(page.locator('#evolution-metadata')).toContainText(partialId);
  await expect(page.locator('#improvement-evidence')).toContainText('참조만 기록');
  await page.locator('#improvement-evidence').getByRole('button', {
    name: fixture.envelope.records.generations[0].baseline_ref.run_id, exact: true,
  }).click();
  await expect(page.locator('#evolution-metadata')).toContainText(fullId);
  await expect(page.locator('#evolution-metadata')).toContainText('전체 번들 보관');
  await expect(page.locator('#evolution-metadata')).not.toContainText(partialId);
  await expect(page.locator('#skill-changes pre').first()).toHaveText('AS-IS\n');
  await page.locator('#skill-history-runs').getByRole('button', { name: /후보 비교/ }).click();
  await expect(page.locator('#evolution-metadata')).toContainText(partialId);
});
