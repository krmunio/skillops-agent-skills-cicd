import { $, node, labels, purposes, stamp, renderReport } from './views.js';

const idPattern = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const runPattern = /^(?:[0-9]+-[0-9]+|(?:import-|local-)?[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12})$/;
let projects = [];
let selection = 0;
let reportSelection = 0;
let activeProject = null;
let history = [];
let activeRun = null;
let sample = null;
let sampleSkill = null;
let sampleMode = false;

function fail(error) {
  $('error').hidden = false;
  $('error').textContent = '이력을 불러오지 못했습니다. 저장된 결과와 배포 상태를 확인한 뒤 새로고침해 주세요.';
  console.error('Dashboard data unavailable:', error.message);
}
async function load(url) {
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) throw new Error(`Result unavailable (${response.status})`);
  const value = await response.json();
  if (value.schema_version !== 1) throw new Error('Unsupported result schema');
  return value;
}
function resetSelection(isSample) {
  ++selection; ++reportSelection;
  sampleMode = isSample;
  activeRun = null; history = [];
  $('detail').hidden = true;
  $('error').hidden = true;
  $('sample-banner').hidden = !isSample;
  $('demo-open').setAttribute('aria-pressed', String(isSample));
  $('history-filter').value = '';
  $('history-count').textContent = '';
  $('history').replaceChildren(node('p', '이력을 불러오는 중입니다.', 'empty'));
  $('selection-summary').textContent = '평가 기록을 불러오는 중입니다.';
  $('origin').textContent = isSample ? '합성 샘플' : '기록 불러오는 중';
  $('freshness').textContent = '';
  document.querySelectorAll('.project').forEach(button => {
    button.setAttribute('aria-pressed', String(!isSample && button.dataset.project === activeProject?.id));
  });
}
function renderHistory() {
  $('history-count').textContent = `${history.length}건`;
  $('history').replaceChildren();
  const filter = $('history-filter').value;
  const rows = history.filter(row => !filter || row.purpose === filter);
  for (const run of rows) {
    const button = node('button', undefined, 'run');
    button.type = 'button'; button.dataset.run = run.run_id;
    button.setAttribute('aria-pressed', String(run.run_id === activeRun));
    const title = node('span', purposes[run.purpose]);
    title.append(node('small', sampleMode ? '합성 샘플 · 실제 평가 아님' :
      run.origin === 'historical_import' ? '과거 로컬 이력' : '프로젝트 평가'));
    const state = run.execution_status;
    button.append(node('time', stamp(run.created_at)), title, node('span', labels[state], `badge ${state}`));
    button.addEventListener('click', () => selectRun(run, selection));
    $('history').append(button);
  }
  if (!rows.length) $('history').append(node('p', history.length ? '해당 종류의 이력이 없습니다.' : '아직 저장된 평가 이력이 없습니다.', 'empty'));
}
async function selectRun(run, token) {
  const request = ++reportSelection;
  $('detail').hidden = true;
  $('error').hidden = true;
  try {
    let report, detail = null;
    if (sampleMode) {
      report = sampleSkill.reports.find(item => item.run_id === run.run_id);
      if (!report) throw new Error('Sample run unavailable');
      const stored = report.details;
      detail = { ...stored, skill_id: sampleSkill.id,
        base: sampleSkill.versions[stored.base],
        candidate: stored.candidate ? sampleSkill.versions[stored.candidate] : null };
      if (!detail.base || (stored.candidate && !detail.candidate)) throw new Error('Sample skill version unavailable');
    } else {
      if (!runPattern.test(run.run_id)) throw new Error('Invalid run');
      const url = `/results/${activeProject.id}/${run.run_id}/report.json`;
      report = await load(url);
      if (request !== reportSelection || token !== selection) return;
      if (report.project_id !== activeProject.id || report.run_id !== run.run_id) throw new Error('Identity mismatch');
      $('report-link').href = url;
    }
    if (request !== reportSelection || token !== selection) return;
    renderReport(report, activeProject, detail);
    activeRun = run.run_id;
    $('detail').hidden = false;
    renderHistory();
  } catch (error) {
    if (request === reportSelection && token === selection) { $('detail').hidden = true; fail(error); }
  }
}
async function selectProject(project) {
  activeProject = project;
  resetSelection(false);
  const token = selection;
  $('project-title').textContent = project.id;
  $('skill-select').replaceChildren(node('option', 'Skill 정보 미기록'));
  $('skill-select').disabled = true;
  try {
    const data = await load(`/results/${project.id}/index.json`);
    if (token !== selection) return;
    if (!Array.isArray(data.history)) throw new Error('Invalid history');
    for (const run of data.history) {
      if (!runPattern.test(run.run_id) || !purposes[run.purpose] || !labels[run.execution_status]) throw new Error('Invalid history row');
      stamp(run.created_at);
    }
    history = data.history;
    renderHistory();
    if (history.length) await selectRun(history.find(row => row.purpose === 'comparison') || history[0], token);
    else {
      $('origin').textContent = '미평가';
      $('selection-summary').textContent = '평가 기록이 없습니다.';
    }
  } catch (error) { if (token === selection) fail(error); }
}
function selectSampleSkill(id) {
  const nextSkill = sample.skills.find(skill => skill.id === id);
  if (!nextSkill || !Array.isArray(nextSkill.reports) || !nextSkill.versions) throw new Error('Invalid sample skill');
  const nextHistory = nextSkill.reports.map(report => {
    if (!purposes[report.purpose] || !labels[report.execution?.status] || typeof report.run_id !== 'string') {
      throw new Error('Invalid sample history');
    }
    stamp(report.created_at);
    return { ...report, execution_status: report.execution.status };
  });
  sampleSkill = nextSkill;
  resetSelection(true);
  $('project-title').textContent = sample.name;
  $('skill-select').value = id;
  history = nextHistory;
  renderHistory();
  if (history.length) selectRun(history[0], selection);
}
async function openSample() {
  resetSelection(true);
  const token = selection;
  $('project-title').textContent = '샘플 화면';
  try {
    if (!sample) sample = await load('/sample-data.json');
    if (token !== selection) return;
    if (sample.synthetic !== true || !Array.isArray(sample.skills) || !sample.skills.length ||
        !idPattern.test(sample.project_id) || projects.some(project => project.id === sample.project_id)) {
      throw new Error('Invalid synthetic sample boundary');
    }
    activeProject = { id: sample.project_id, current_run: null, state: 'sample' };
    $('skill-select').replaceChildren();
    for (const skill of sample.skills) {
      const option = node('option', skill.id);
      option.value = skill.id;
      $('skill-select').append(option);
    }
    $('skill-select').disabled = false;
    selectSampleSkill(sample.skills[0].id);
  } catch (error) { if (token === selection) fail(error); }
}
async function refresh() {
  activeProject = null;
  resetSelection(false);
  const token = selection;
  $('project-title').textContent = '프로젝트 평가';
  $('skill-select').replaceChildren(node('option', 'Skill 정보 미기록'));
  $('skill-select').disabled = true;
  try {
    const index = await load('/results/index.json');
    if (token !== selection) return;
    if (!Array.isArray(index.projects)) throw new Error('Invalid catalog');
    projects = index.projects;
    $('projects').replaceChildren();
    $('project-count').textContent = String(projects.length);
    for (const project of projects) {
      if (!idPattern.test(project.id) || !Number.isInteger(project.history_count) || project.history_count < 0 ||
          !['active', 'blocked', 'removed'].includes(project.state)) throw new Error('Invalid project');
      const button = node('button', undefined, 'project');
      button.type = 'button'; button.dataset.project = project.id;
      button.setAttribute('aria-pressed', 'false');
      button.append(node('strong', project.id), node('small', `평가 이력 ${project.history_count}건`));
      button.addEventListener('click', () => selectProject(project));
      $('projects').append(button);
    }
    if (projects.length) await selectProject(projects[0]);
    else {
      $('projects').append(node('p', '등록된 프로젝트가 없습니다.', 'muted'));
      $('origin').textContent = '프로젝트 없음';
      $('selection-summary').textContent = '프로젝트를 등록하거나 샘플 화면을 확인하세요.';
      renderHistory();
    }
  } catch (error) { if (token === selection) fail(error); }
}
$('refresh').addEventListener('click', refresh);
$('demo-open').addEventListener('click', openSample);
$('exit-sample').addEventListener('click', refresh);
$('history-filter').addEventListener('change', renderHistory);
$('skill-select').addEventListener('change', () => {
  if (sampleMode) {
    try { selectSampleSkill($('skill-select').value); } catch (error) { fail(error); }
  }
});
$('improvement-evidence').addEventListener('click', event => {
  const button = event.target.closest('button[data-evidence-run]');
  if (!button || !sampleMode) return;
  const run = history.find(item => item.run_id === button.dataset.evidenceRun);
  if (run) selectRun(run, selection);
  else fail(new Error('Evidence run unavailable'));
});
refresh();
