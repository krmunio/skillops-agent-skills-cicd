import { $, node, labels, purposes, stamp, renderReport } from './views.js';
import { validateEvolutionSummary, validateEvolution, renderEvolution, clearEvolution } from './evolution.js';

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
let selectedSkill = null;

function fail(error) {
  $('error').hidden = false;
  $('error').textContent = '이력을 불러오지 못했습니다. 저장된 결과와 배포 상태를 확인한 뒤 새로고침해 주세요.';
  console.error('Dashboard data unavailable:', error.message);
}
async function load(url, withRaw = false) {
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) throw new Error(`Result unavailable (${response.status})`);
  const raw = await response.text();
  if (new TextEncoder().encode(raw).length > 1048576) throw new Error('Result exceeds public size limit');
  const value = JSON.parse(raw);
  if (value.schema_version !== 1) throw new Error('Unsupported result schema');
  return withRaw ? { value, raw } : value;
}
function resetSelection(isSample) {
  ++selection; ++reportSelection;
  sampleMode = isSample;
  activeRun = null; history = [];
  selectedSkill = null;
  clearEvolution();
  $('detail').hidden = true;
  $('error').hidden = true;
  $('sample-banner').hidden = !isSample;
  $('demo-open').setAttribute('aria-pressed', String(isSample));
  $('history-filter').value = '';
  $('history-count').textContent = '';
  $('skill-list').replaceChildren();
  $('skill-history-runs').replaceChildren();
  $('skill-count').textContent = '';
  selectHistoryTab('skill');
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
  renderSkillHistory();
}
function skillGroups() {
  if (sampleMode && sample) {
    return sample.skills.map(skill => ({ id: skill.id, versions: Object.keys(skill.versions).length,
      runs: skill.reports.map(report => ({ ...report, execution_status: report.execution.status })) }));
  }
  const groups = new Map();
  for (const run of history) {
    for (const binding of runSkills(run)) {
      if (!groups.has(binding.id)) groups.set(binding.id, { ...binding, hashes: new Set(), runs: [] });
      const group = groups.get(binding.id);
      group.runs.push(run);
      for (const hash of binding.hashes) if (hash) group.hashes.add(hash);
    }
  }
  return [...groups.values()].map(group => ({ ...group, versions: group.hashes.size }));
}
function runSkills(run) {
  if (run.evolution_skills) return run.evolution_skills.map(item => ({
    id: item.skill_key, name: item.display_name || item.skill_key, registered: true,
    hashes: [item.base_version_id, item.candidate_version_id],
  }));
  return run.skill_id ? [{ id: `legacy:${activeProject.id}:${run.skill_id}`, name: run.skill_id, registered: false,
    hashes: [run.base_skill_sha256, run.candidate_skill_sha256] }] : [];
}
function renderSkillHistory() {
  const groups = skillGroups();
  $('skill-count').textContent = `${groups.length}개 Skill`;
  $('skill-list').replaceChildren();
  $('skill-history-runs').replaceChildren();
  if (!groups.length) {
    $('skill-list').append(node('p', '원문·식별 정보가 연결된 Skill이 없습니다. 미연결 기록은 실행 이력에서 확인하세요.', 'empty'));
    return;
  }
  if (!groups.some(group => group.id === selectedSkill)) selectedSkill = groups[0].id;
  for (const group of groups) {
    const button = node('button', undefined, 'skill-item');
    button.type = 'button';
    button.setAttribute('aria-pressed', String(group.id === selectedSkill));
    button.append(node('strong', group.name || group.id), node('small', `버전 ${group.versions}개 · 연결 기록 ${group.runs.length}건`),
      node('small', `최근 ${stamp(group.runs[0].created_at)}${sampleMode ? ' · 합성 샘플' : ''}`));
    if (!sampleMode) button.append(node('small', group.registered ? group.id : '고정 Skill ID 미등록 · 프로젝트 범위 이름'));
    button.addEventListener('click', () => chooseSkill(group.id));
    $('skill-list').append(button);
  }
  const current = groups.find(group => group.id === selectedSkill);
  $('skill-history-runs').append(node('p', `${current.id}의 기록 · 선택하면 위의 As-Is / To-Be와 평가 결과가 바뀝니다.`));
  for (const run of current.runs) {
    const button = node('button', undefined, 'skill-run');
    button.type = 'button';
    button.setAttribute('aria-pressed', String(run.run_id === activeRun));
    const title = node('span', purposes[run.purpose]);
    title.append(node('small', stamp(run.created_at)));
    button.append(title, node('span', labels[run.execution_status], `badge ${run.execution_status}`));
    button.addEventListener('click', () => selectRun(run, selection));
    $('skill-history-runs').append(button);
  }
}
function chooseSkill(id) {
  if (sampleMode) { selectSampleSkill(id); return; }
  selectedSkill = id;
  const run = skillGroups().find(group => group.id === id)?.runs[0];
  if (run) selectRun(run, selection);
  else fail(new Error('Skill history unavailable'));
}
function selectHistoryTab(name) {
  for (const kind of ['skill', 'execution']) {
    const active = name === kind;
    $(`${kind}-history-tab`).setAttribute('aria-selected', String(active));
    $(`${kind}-history-tab`).tabIndex = active ? 0 : -1;
    $(`${kind}-history-panel`).hidden = !active;
  }
}
async function selectRun(run, token) {
  const request = ++reportSelection;
  $('detail').hidden = true;
  $('error').hidden = true;
  try {
    let report, detail = null, snapshots = null, lifecycle = null;
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
      const loaded = await load(url, true);
      report = loaded.value;
      if (request !== reportSelection || token !== selection) return;
      if (report.project_id !== activeProject.id || report.run_id !== run.run_id) throw new Error('Identity mismatch');
      $('report-link').href = url;
      if (run.skill_snapshots) {
        if (run.skill_snapshots !== `${run.run_id}/skill-snapshots.json`) throw new Error('Invalid snapshot reference');
        snapshots = await load(`/results/${activeProject.id}/${run.skill_snapshots}`);
        if (request !== reportSelection || token !== selection) return;
        if (snapshots.project_id !== report.project_id || snapshots.run_id !== report.run_id ||
            snapshots.skill_id !== run.skill_id || snapshots.base?.sha256 !== run.base_skill_sha256 ||
            (snapshots.candidate?.sha256 ?? null) !== (run.candidate_skill_sha256 ?? null)) {
          throw new Error('Skill snapshot identity mismatch');
        }
      }
      if (run.skill_evolution) {
        const data = await load(`/results/${activeProject.id}/${run.skill_evolution}`);
        if (request !== reportSelection || token !== selection) return;
        lifecycle = await validateEvolution(data, report, loaded.raw, run, snapshots);
        if (request !== reportSelection || token !== selection) return;
      }
      const skills = runSkills(run);
      selectedSkill = skills.some(item => item.id === selectedSkill) ? selectedSkill : skills[0]?.id ?? null;
      $('skill-select').value = selectedSkill || '';
    }
    if (request !== reportSelection || token !== selection) return;
    renderReport(report, activeProject, detail, snapshots);
    clearEvolution();
    if (lifecycle) renderEvolution(lifecycle, selectedSkill, history, next => selectRun(next, selection));
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
      if (run.skill_id && !idPattern.test(run.skill_id)) throw new Error('Invalid Skill identity');
      if (Boolean(run.skill_id) !== Boolean(run.skill_snapshots)) throw new Error('Incomplete Skill reference');
      validateEvolutionSummary(run);
    }
    history = data.history;
    const unlinked = node('option', 'Skill 미연결');
    unlinked.value = '';
    unlinked.disabled = !history.some(run => !runSkills(run).length);
    $('skill-select').replaceChildren(unlinked);
    for (const group of skillGroups()) {
      const option = node('option', group.registered ? `${group.name} (${group.id})` : group.name); option.value = group.id;
      $('skill-select').append(option);
    }
    $('skill-select').disabled = skillGroups().length === 0;
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
  selectedSkill = id;
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
  try {
    const id = $('skill-select').value;
    if (id) chooseSkill(id);
    else {
      const run = history.find(item => !runSkills(item).length);
      if (run) selectRun(run, selection);
    }
  } catch (error) { fail(error); }
});
for (const kind of ['skill', 'execution']) {
  $(`${kind}-history-tab`).addEventListener('click', () => selectHistoryTab(kind));
  $(`${kind}-history-tab`).addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const target = event.key === 'Home' ? 'skill' : event.key === 'End' ? 'execution' :
      kind === 'skill' ? 'execution' : 'skill';
    selectHistoryTab(target);
    $(`${target}-history-tab`).focus();
  });
}
$('improvement-evidence').addEventListener('click', event => {
  const button = event.target.closest('button[data-evidence-run]');
  if (!button || !sampleMode) return;
  const run = history.find(item => item.run_id === button.dataset.evidenceRun);
  if (run) selectRun(run, selection);
  else fail(new Error('Evidence run unavailable'));
});
refresh();
