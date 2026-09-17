import { $, node, table, renderMetric } from './views.js';
import { check, exact, canonical, safePath, digest } from './evolution.js';

const hash = /^[a-f0-9]{64}$/;
const version = /^sha256:[a-f0-9]{64}$/;
const states = { improved: '개선 확인', not_improved: '개선 미확인', rejected: '후보 거절', unverified: '검증 불충분' };
const outcomes = { passed: '통과', failed: '실패', error: '오류', skipped: '건너뜀', expected_failure: '예상 실패' };
const encoder = new TextEncoder();
const failure = state => ['failed', 'error'].includes(state);
const numeric = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
const byId = rows => new Map(rows.map(row => [row.id, row.status]));
const sameKeys = (a, b) => a.size === b.size && [...a.keys()].every(key => b.has(key));
const text = (value, limit = 4096) => check(typeof value === 'string' && value.length > 0 &&
  encoder.encode(value).length <= limit && !/[\u0000-\u0008\u000b-\u001f]/.test(value));

function list(rows, limit, validate) {
  check(Array.isArray(rows) && rows.length <= limit);
  const seen = new Set();
  for (const row of rows) {
    validate(row);
    check(!seen.has(row.id));
    seen.add(row.id);
  }
}

function quality(value) {
  if (value === null) return;
  exact(value, 'status rubric_sha256 context_sha256 dimensions findings');
  check(['completed', 'blocked'].includes(value.status) && hash.test(value.rubric_sha256) && hash.test(value.context_sha256));
  list(value.dimensions, 128, row => {
    exact(row, 'id score'); text(row.id, 160);
    check(row.score === null || (Number.isInteger(row.score) && row.score >= 0 && row.score <= 4));
  });
  list(value.findings, 128, row => {
    exact(row, 'id severity message'); text(row.id, 160); text(row.message);
    check(['error', 'warning'].includes(row.severity));
  });
}

function observation(value) {
  if (value === null) return;
  exact(value, 'plan_sha256 environment_sha256 protected_sha256 status cases gates elapsed_seconds');
  for (const key of ['plan_sha256', 'environment_sha256', 'protected_sha256']) check(hash.test(value[key]));
  check(['completed', 'failed', 'blocked'].includes(value.status) && numeric(value.elapsed_seconds));
  for (const name of ['cases', 'gates']) {
    list(value[name], 10000, row => {
      exact(row, 'id status'); text(row.id, 1024);
      check(!/[\u0000-\u001f\u007f]/.test(row.id) && Object.hasOwn(outcomes, row.status) &&
        (name === 'cases' || ['passed', 'failed', 'error'].includes(row.status)));
    });
  }
  const failed = [...value.cases, ...value.gates].some(row => failure(row.status));
  check(value.status !== 'completed' || !failed);
  check(value.status !== 'failed' || failed);
}

function regression(checks) {
  const rows = ['original', 'base', 'candidate'].map(arm => checks[arm]);
  if (rows.some(row => row === null)) return { status: 'unverified', reasons: ['missing_checks'], regressions: [] };
  if (['plan_sha256', 'environment_sha256', 'protected_sha256'].some(key => new Set(rows.map(row => row[key])).size !== 1)) {
    return { status: 'unverified', reasons: ['input_mismatch'], regressions: [] };
  }
  const reasons = new Set(), regressions = new Set();
  if (rows.some(row => row.status === 'blocked')) reasons.add('incomplete_checks');
  for (const [name, prefix] of [['cases', 'test'], ['gates', 'gate']]) {
    const [before, base, after] = rows.map(row => byId(row[name]));
    if (!sameKeys(before, base)) reasons.add('baseline_population_changed');
    if (!sameKeys(after, before) || !sameKeys(after, base)) {
      reasons.add('candidate_population_changed');
      for (const key of [...before.keys(), ...base.keys()]) if (!after.has(key)) regressions.add(`${prefix}:${key}`);
    }
    for (const prior of [before, base]) {
      for (const [key, state] of prior) if (state === 'passed' && after.get(key) !== 'passed') regressions.add(`${prefix}:${key}`);
    }
    if (name === 'cases' && ![...before].some(([key, state]) =>
      state === 'passed' && base.get(key) === 'passed' && after.get(key) === 'passed')) reasons.add('no_passing_coverage');
    if (name === 'gates' && [before, base, after].some(rows => [...rows.values()].some(state => state !== 'passed'))) {
      reasons.add('required_gate_failed');
    }
  }
  return { status: regressions.size ? 'rejected' : reasons.size ? 'unverified' : 'passed',
    reasons: [...reasons].sort(), regressions: [...regressions].sort() };
}

function decide(row) {
  const compared = regression(row.checks), reasons = new Set();
  let rejected = compared.status === 'rejected', improved = false;
  if (row.errors.length) reasons.add('incomplete_stage');
  if (compared.status !== 'passed') reasons.add(rejected ? 'project_regression' : 'regression_unverified');
  const { base, candidate } = row.quality;
  if (!base || !candidate || [base, candidate].some(item => item.status !== 'completed')) reasons.add('quality_unverified');
  else if (['rubric_sha256', 'context_sha256'].some(key => base[key] !== candidate[key])) reasons.add('quality_inputs_mismatch');
  else {
    const before = new Map(base.dimensions.map(row => [row.id, row.score]));
    const after = new Map(candidate.dimensions.map(row => [row.id, row.score]));
    if (!before.size || !sameKeys(before, after) || [...before].some(([key, score]) =>
      (score === null) !== (after.get(key) === null)) || [...before.values()].every(score => score === null)) {
      reasons.add('quality_coverage_mismatch');
    } else {
      rejected ||= [...before].some(([key, score]) => score !== null && after.get(key) < score);
      improved ||= [...before].some(([key, score]) => score !== null && after.get(key) > score);
    }
    const prior = new Map(base.findings.map(row => [row.id, row.severity]));
    const current = new Map(candidate.findings.map(row => [row.id, row.severity]));
    if ([...current.values()].includes('error')) { rejected = true; reasons.add('candidate_quality_error'); }
    if ([...current].some(([key, severity]) => !prior.has(key) || (severity === 'error' && prior.get(key) !== 'error'))) {
      rejected = true; reasons.add('quality_regression');
    }
    improved ||= [...prior.keys()].some(key => !current.has(key));
    if ([...before].some(([key, score]) => score !== null && after.has(key) && after.get(key) !== null &&
      after.get(key) < score)) reasons.add('quality_regression');
  }
  const known = new Set([...(base?.findings || []), ...(base?.dimensions || [])].map(item => item.id));
  if (row.generation.status !== 'generated') reasons.add('candidate_unavailable');
  if (row.generation.addressed_findings.some(key => !known.has(key))) reasons.add('unsupported_generation_evidence');
  for (const arm of ['base', 'candidate']) {
    const application = row.applications[arm];
    if (!application || !row.work || !application.activated || application.version_id !== row[`${arm}_version_id`] ||
      application.staged_version_id !== row[`${arm}_version_id`] || application.work_sha256 !== row.work.sha256) {
      reasons.add('activation_unverified');
    } else if (!application.changed) reasons.add('no_execution_effect');
    if (arm === 'candidate' && application && application.task_outcome !== 'satisfied') reasons.add('task_unverified');
  }
  const checkId = row.work?.check_id;
  if (!checkId || !failure(byId(row.checks.original?.cases || []).get(checkId)) ||
    byId(row.checks.candidate?.cases || []).get(checkId) !== 'passed') reasons.add('task_unverified');
  return { status: rejected ? 'rejected' : reasons.size ? 'unverified' : improved ? 'improved' : 'not_improved',
    reasons: [...reasons].sort(), regression: compared };
}

export function validateAssessmentSummary(run) {
  if (run.skill_assessments === undefined) return;
  check(run.skill_assessments === `${run.run_id}/skill-assessments.json` && run.skill_evolution);
}

export async function validateAssessments(data, report, rawReport, lifecycle) {
  exact(data, 'schema_version project_id run_id report_sha256 skills');
  check(data.schema_version === 1 && data.project_id === report.project_id && data.run_id === report.run_id);
  check(lifecycle && data.report_sha256 === lifecycle.data.report_sha256 &&
    data.report_sha256 === await digest(encoder.encode(rawReport)));
  check(Array.isArray(data.skills) && data.skills.length > 0 && data.skills.length <= 128);
  const keys = new Set();
  for (const row of data.skills) {
    exact(row, 'skill_key source_path base_version_id candidate_version_id quality generation work applications checks errors decision');
    const binding = lifecycle.bindings.get(row.skill_key);
    check(binding && !keys.has(row.skill_key)); keys.add(row.skill_key);
    safePath(row.source_path);
    check(version.test(row.base_version_id));
    for (const arm of ['base', 'candidate']) check(binding[`${arm}_version_id`] === row[`${arm}_version_id`]);
    exact(row.quality, 'base candidate'); quality(row.quality.base); quality(row.quality.candidate);
    exact(row.generation, 'status addressed_findings hypothesis');
    check(['generated', 'no_change', 'failed'].includes(row.generation.status));
    const addressed = row.generation.addressed_findings;
    check(Array.isArray(addressed) && addressed.length <= 128 && new Set(addressed).size === addressed.length);
    addressed.forEach(key => text(key, 160));
    if (row.generation.hypothesis !== null) text(row.generation.hypothesis);
    check(row.generation.status === 'generated' ? version.test(row.candidate_version_id) &&
      row.candidate_version_id !== row.base_version_id : row.candidate_version_id === null);
    if (row.work !== null) {
      exact(row.work, 'sha256 provenance check_id');
      check(hash.test(row.work.sha256) && row.work.provenance === 'generated');
      if (row.work.check_id !== null) text(row.work.check_id, 1024);
    }
    exact(row.applications, 'base candidate');
    for (const arm of ['base', 'candidate']) {
      const application = row.applications[arm];
      if (application === null) continue;
      exact(application, 'version_id staged_version_id work_sha256 output_sha256 activated changed task_outcome' +
        (Object.hasOwn(application, 'measurement') ? ' measurement' : ''));
      check(version.test(application.version_id) && version.test(application.staged_version_id) &&
        hash.test(application.work_sha256) && hash.test(application.output_sha256) && row[`${arm}_version_id`] !== null);
      check(typeof application.activated === 'boolean' && typeof application.changed === 'boolean' &&
        ['satisfied', 'not_satisfied', 'unverified'].includes(application.task_outcome));
      if (Object.hasOwn(application, 'measurement')) {
        exact(application.measurement, 'cost_nano_aiu elapsed_seconds');
        Object.values(application.measurement).forEach(value => check(value === null || numeric(value)));
      }
    }
    exact(row.checks, 'original base candidate');
    Object.values(row.checks).forEach(observation);
    if (row.candidate_version_id === null) check(row.quality.candidate === null &&
      row.applications.candidate === null && row.checks.candidate === null);
    check(Array.isArray(row.errors) && row.errors.length <= 16);
    for (const error of row.errors) {
      exact(error, 'stage code');
      check(['original_checks', 'base_quality', 'generation', 'candidate_quality', 'base_application',
        'candidate_application', 'work'].includes(error.stage) && /^[a-z0-9_]{1,128}$/.test(error.code));
    }
    check(canonical(row.decision) === canonical(decide(row)));
  }
  return data;
}

export function renderAssessment(data, key) {
  const row = data.skills.find(item => item.skill_key === key);
  check(row);
  const { base, candidate } = row.quality;
  $('quality-summary').textContent = '선택 Skill · 실제 버전 평가';
  const dimensions = new Map([...(base?.dimensions || []), ...(candidate?.dimensions || [])].map(item => [item.id, item.id]));
  const score = (quality, id) => quality?.dimensions.find(item => item.id === id)?.score ?? '미평가';
  $('guide').replaceChildren(table(['평가 항목', '기존 → 후보'], [...dimensions.keys()].map(id =>
    [id, `${score(base, id)} → ${score(candidate, id)}`])));
  for (const [label, quality] of [['기존', base], ['후보', candidate]]) {
    $('guide').append(node('p', `${label}: ${quality?.status === 'completed' ? '평가 완료' : '검증 불충분'}`, 'reason'));
    for (const finding of quality?.findings || []) $('guide').append(node('p', `${label} · ${finding.message}`, 'reason'));
  }
  $('improvement-evidence').replaceChildren(node('strong', '관측된 문제'));
  for (const finding of base?.findings || []) $('improvement-evidence').append(node('p', finding.message));
  if (!base?.findings.length) $('improvement-evidence').append(node('p', '정적 검사 지적 사항 없음 · 점수 변화는 위에서 확인'));
  $('improvement-evidence').append(node('strong', '개선 가설 · 검증 결과와 구분'),
    node('p', row.generation.hypothesis ?? '가설 미기록'),
    node('p', `후보 생성: ${row.generation.status} · 근거 연결 ${row.generation.addressed_findings.length}건`, 'reason'),
    node('p', '생성된 작업과 가설은 과거 실제 작업 이력 또는 개선의 증명이 아닙니다.', 'reason'));
  $('execution').className = `outcome-card ${row.decision.status === 'rejected' ? 'rejected' : ''}`;
  $('execution').replaceChildren(node('h3', '선택 Skill 후보 판정'),
    node('strong', states[row.decision.status], 'decision-title'),
    node('p', '후보 검증 결과이며 원본 변경·채택·배포를 의미하지 않습니다.', 'reason'));
  $('execution-scope').textContent = row.decision.regression.status === 'passed' ?
    '동일한 검사와 환경을 사용한 검증 범위에서 회귀 미발견. 프로젝트 전체의 부작용 부재를 보장하지 않습니다.' :
    '프로젝트 회귀가 발견됐거나 검증이 불충분합니다. 후보를 안전한 변경으로 간주하지 않습니다.';
  $('decision-reasons').replaceChildren(node('p', '동일 품질 기준의 개선, 실제 버전 적용, 작업 완료 및 회귀 검증이 모두 필요합니다.'));
  for (const reason of row.decision.reasons) $('decision-reasons').append(node('p', reason, 'reason'));
  for (const reason of row.decision.regression.reasons) $('decision-reasons').append(node('p', reason, 'reason'));
  for (const error of row.errors) $('decision-reasons').append(node('p', `${error.stage}: ${error.code}`, 'reason'));
  const tasks = [];
  for (const [collection, label] of [['cases', '검사'], ['gates', '필수 단계']]) {
    const arms = ['original', 'base', 'candidate'].map(arm => byId(row.checks[arm]?.[collection] || []));
    const ids = new Set(arms.flatMap(map => [...map.keys()]));
    for (const id of ids) tasks.push([`${label}: ${id}`, ...arms.map(map => outcomes[map.get(id)] ?? '미관측')]);
  }
  $('task-results').replaceChildren(table(['검사 식별자', '원본 프로젝트', '기존 적용', '후보 적용'], tasks));
  $('task-results').append(node('p', row.work ? `자동 도출 작업 · 기존 실패 검사: ${row.work.check_id ?? '미기록'}` :
    '검증 가능한 자동 작업을 도출하지 못했습니다.', 'source-hash'));
  const metrics = {};
  for (const arm of ['base', 'candidate']) {
    for (const name of ['cost_nano_aiu', 'elapsed_seconds']) metrics[`${arm}_${name}`] = row.applications[arm]?.measurement?.[name] ?? null;
  }
  renderMetric($('cost-card'), 'Skill 적용 비용 · 기존 → 후보', 'cost_nano_aiu', metrics);
  renderMetric($('time-card'), 'Skill 적용 시간 · 기존 → 후보', 'elapsed_seconds', metrics);
}
