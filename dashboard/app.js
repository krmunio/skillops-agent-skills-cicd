'use strict';
const $ = id => document.getElementById(id);
const idPattern = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const runPattern = /^(?:[0-9]+-[0-9]+|(?:import-|local-)?[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12})$/;
const labels = {
  completed: '실행 완료', failed: '실패', blocked: '차단',
  not_assessed: '미평가', configuration_required: '설정 필요',
};
const purposes = { baseline: '기준 평가', comparison: '후보 비교', candidate: '후보 생성', calibration: '평가기 교정', project_assessment: '프로젝트 평가' };
const reasons = {
  no_skills: '프로젝트에서 평가할 스킬이 발견되지 않았습니다.',
  no_adapter: '실행 기반 평가 어댑터를 설정해야 합니다.',
  live_disabled: '실제 모델 평가가 아직 활성화되지 않았습니다.',
  missing_auth: '모델 평가 인증이 설정되지 않았습니다.',
  missing_limits: '모델 호출·시간 한도를 설정해야 합니다.',
  guide_integration_pending: '스킬이 발견됐지만 공통 가이드 평가기 연동이 아직 준비되지 않았습니다.',
  historical_import: '과거 로컬 실행에서 공개가 허용된 요약만 가져온 기록입니다.',
  evaluation_completed: '기록된 실행을 완료했습니다. 세부 품질 지표를 함께 확인하세요.',
  evaluation_failed: '평가가 정상적으로 완료되지 않았습니다.',
  unsupported_adapter: '등록된 실행 어댑터를 지원하지 않습니다.',
  unsafe_project: '프로젝트 구조 또는 파일 검증이 차단됐습니다.',
  runtime_error: '평가 실행에 필요한 환경이나 계약을 확인해야 합니다.',
  call_limit: '허용된 모델 호출 한도에 도달했습니다.',
  time_limit: '허용된 평가 시간에 도달했습니다.',
};
const metricLabels = {
  requested: '요청 작업', attempted: '시도 작업', evaluation_completed: '평가 완료 작업',
  errors: '오류', blocked: '차단', correctness_successes: '정답 작업',
  mean_judge_score: 'Judge 점수', judge_score_denominator: 'Judge 평가 분모',
  controls: '교정 사례', cli_invocations: 'CLI 실행',
  base_requested: '기존 요청 작업', candidate_requested: '후보 요청 작업',
  base_correctness_successes: '기존 정답 작업', candidate_correctness_successes: '후보 정답 작업',
  base_judge_score: '기존 Judge 점수', candidate_judge_score: '후보 Judge 점수',
  base_cost_nano_aiu: '기존 비용 (NanoAIU)', candidate_cost_nano_aiu: '후보 비용 (NanoAIU)',
  base_elapsed_seconds: '기존 시간 (초)', candidate_elapsed_seconds: '후보 시간 (초)',
  cost_improvement_percent: '비용 개선율 (%)', time_improvement_percent: '시간 개선율 (%)',
  skills: '스킬 수', pass: '통과', review: '검토 필요',
};
const decisions = { rejected: '후보 거절', eligible_for_canary: '잠정 canary 자격 · 배포 아님',
  blocked: '판정 차단', calibration_passed: '교정 통과', calibration_failed: '교정 실패' };
let projects = [];
let selection = 0;
let reportSelection = 0;

function node(tag, text, className) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  if (className) item.className = className;
  return item;
}
function fail() {
  $('error').hidden = false;
  $('error').textContent = '이력을 불러오지 못했습니다. 저장된 결과와 배포 상태를 확인한 뒤 새로고침해 주세요.';
}
async function load(url) {
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) throw new Error('Result unavailable');
  const value = await response.json();
  if (value.schema_version !== 1) throw new Error('Unsupported result schema');
  return value;
}
function stamp(value) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) throw new Error('Invalid timestamp');
  return date.toLocaleString('ko-KR');
}
function renderAxis(container, part) {
  if (!part || !labels[part.status] || !reasons[part.reason_code]) throw new Error('Invalid assessment');
  container.replaceChildren(node('span', labels[part.status], `badge ${part.status}`),
    node('p', reasons[part.reason_code], 'reason'));
  if (part.decision) {
    if (!decisions[part.decision]) throw new Error('Invalid decision');
    container.append(node('strong', decisions[part.decision]));
  }
  if (part.metrics) {
    const list = node('dl', undefined, 'metrics');
    for (const [key, value] of Object.entries(part.metrics)) {
      if (!metricLabels[key] || (value !== null && (typeof value !== 'number' || !Number.isFinite(value)))) throw new Error('Invalid metric');
      const line = node('div');
      line.append(node('dt', metricLabels[key]), node('dd', value === null ? '미기록' : value.toLocaleString('ko-KR', { maximumFractionDigits: 3 })));
      list.append(line);
    }
    container.append(list);
  }
}
async function selectRun(project, run, token) {
  const request = ++reportSelection;
  try {
    if (!runPattern.test(run.run_id)) throw new Error('Invalid run');
    const url = `/results/${project.id}/${run.run_id}/report.json`;
    const report = await load(url);
    if (request !== reportSelection || token !== selection) return;
    if (report.project_id !== project.id || report.run_id !== run.run_id) throw new Error('Identity mismatch');
    $('detail').hidden = false;
    $('run-title').textContent = purposes[report.purpose] || '평가 기록';
    $('origin').textContent = report.origin === 'historical_import' ? '과거 로컬 이력' : '자동 평가 기록';
    $('run-time').textContent = stamp(report.created_at);
    renderAxis($('guide'), report.guide);
    renderAxis($('execution'), report.execution);
    $('provenance').replaceChildren();
    for (const [key, label] of [['run_id', '실행 ID'], ['source_commit', '평가 대상 커밋'],
      ['project_tree_sha256', '프로젝트 내용 SHA-256'], ['evaluator_sha256', '평가기 식별 SHA-256'],
      ['source_report_sha256', '원본 보고서 SHA-256']]) {
      $('provenance').append(node('dt', label), node('dd', report[key] ?? '미기록 · 현재 버전으로 추정하지 않음'));
    }
    $('report-link').href = url;
    document.querySelectorAll('.run').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.run === run.run_id)));
  } catch {
    if (request === reportSelection && token === selection) { $('detail').hidden = true; fail(); }
  }
}
async function selectProject(project) {
  const token = ++selection;
  ++reportSelection;
  $('detail').hidden = true;
  $('error').hidden = true;
  $('project-title').textContent = project.id;
  $('freshness').textContent = project.state === 'removed' ? '삭제된 프로젝트의 과거 이력을 보존하고 있습니다.' :
    project.current_run ? '현재 프로젝트와 평가기 식별 정보에 일치하는 기록이 있습니다.' :
      '현재 버전에 일치하는 평가가 없습니다. 과거 이력은 현재 버전의 검증 근거로 사용하지 않습니다.';
  document.querySelectorAll('.project').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.project === project.id)));
  $('history').replaceChildren(node('p', '이력을 불러오는 중입니다.', 'empty'));
  try {
    const data = await load(`/results/${project.id}/index.json`);
    if (token !== selection) return;
    if (!Array.isArray(data.history)) throw new Error('Invalid history');
    $('history-count').textContent = `${data.history.length}건`;
    $('history').replaceChildren();
    for (const run of data.history) {
      if (!runPattern.test(run.run_id) || !purposes[run.purpose] || !labels[run.execution_status]) throw new Error('Invalid row');
      const button = node('button', undefined, 'run');
      button.type = 'button'; button.dataset.run = run.run_id; button.setAttribute('aria-pressed', 'false');
      const title = node('span', purposes[run.purpose]);
      title.append(node('small', run.origin === 'historical_import' ? '과거 로컬 이력' : '프로젝트 평가'));
      button.append(node('time', stamp(run.created_at)), title, node('span', labels[run.execution_status], `badge ${run.execution_status}`));
      button.addEventListener('click', () => selectRun(project, run, token));
      $('history').append(button);
    }
    if (!data.history.length) $('history').append(node('p', '아직 저장된 평가 이력이 없습니다.', 'empty'));
    else await selectRun(project, data.history.find(row => row.purpose === 'comparison') || data.history[0], token);
  } catch { if (token === selection) fail(); }
}
async function refresh() {
  ++selection; ++reportSelection;
  $('error').hidden = true; $('detail').hidden = true;
  try {
    const index = await load('/results/index.json');
    if (!Array.isArray(index.projects)) throw new Error('Invalid catalog');
    projects = index.projects;
    $('projects').replaceChildren();
    $('project-count').textContent = String(projects.length);
    for (const project of projects) {
      if (!idPattern.test(project.id) || !Number.isInteger(project.history_count) || project.history_count < 0 ||
          !['active', 'blocked', 'removed'].includes(project.state)) throw new Error('Invalid project');
      const button = node('button', undefined, 'project');
      button.type = 'button'; button.dataset.project = project.id; button.setAttribute('aria-pressed', 'false');
      button.append(node('strong', project.id), node('small', `평가 이력 ${project.history_count}건`));
      button.addEventListener('click', () => selectProject(project));
      $('projects').append(button);
    }
    if (projects.length) await selectProject(projects[0]);
    else $('projects').append(node('p', '등록된 프로젝트가 없습니다.'));
  } catch { fail(); }
}
$('refresh').addEventListener('click', refresh);
refresh();
