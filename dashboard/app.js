import { $, node, labels, purposes, stamp, renderReport, summarizeHistory, renderProjectSummary } from './views.js';
import { validateEvolutionSummary, validateEvolution, renderEvolution, clearEvolution } from './evolution.js';
import { validateAssessmentSummary, validateAssessments, renderAssessment } from './assessments.js';

const idPattern = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const runPattern = /^(?:[0-9]+-[0-9]+|(?:import-|local-|sample-)?[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12})$/;
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
const newCache = () => ({ histories: new Map(), reports: new Map(), runs: new Map() });
let cache = newCache();

function fail(error) {
  $('error').hidden = false;
  $('error').textContent = '이력을 불러오지 못했습니다. 저장된 결과와 배포 상태를 확인한 뒤 새로고침해 주세요.';
  console.error('Dashboard data unavailable:', error.message);
}
async function load(url, withRaw = false, limit = 1048576) {
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) throw new Error(`Result unavailable (${response.status})`);
  const raw = await response.text();
  if (new TextEncoder().encode(raw).length > limit) throw new Error('Result exceeds public size limit');
  const value = JSON.parse(raw);
  if (value.schema_version !== 1) throw new Error('Unsupported result schema');
  return withRaw ? { value, raw } : value;
}
function cached(store, key, read) {
  if (!store.has(key)) store.set(key, read());
  return store.get(key);
}
function loadHistory(project, store) {
  return cached(store.histories, project.id, async () => {
    const data = await load(`/results/${project.id}/index.json`);
    if (!Array.isArray(data.history) || (data.project && data.project.id !== project.id)) throw new Error('Invalid history');
    const seen = new Set();
    for (const run of data.history) {
      if (!run || typeof run.run_id !== 'string' || !runPattern.test(run.run_id) || seen.has(run.run_id) ||
          !Object.hasOwn(purposes, run.purpose) || !Object.hasOwn(labels, run.execution_status) ||
          typeof run.created_at !== 'string') throw new Error('Invalid history row');
      seen.add(run.run_id);
      stamp(run.created_at);
      if (run.skill_id && (typeof run.skill_id !== 'string' || !idPattern.test(run.skill_id))) throw new Error('Invalid Skill identity');
      if (Boolean(run.skill_id) !== Boolean(run.skill_snapshots)) throw new Error('Incomplete Skill reference');
      if (run.skill_snapshots && run.skill_snapshots !== `${run.run_id}/skill-snapshots.json`) throw new Error('Invalid snapshot reference');
      for (const hash of [run.base_skill_sha256, run.candidate_skill_sha256]) {
        if (hash !== null && hash !== undefined && (typeof hash !== 'string' || !/^[a-f0-9]{64}$/.test(hash))) {
          throw new Error('Invalid legacy version');
        }
      }
      validateEvolutionSummary(run);
      validateAssessmentSummary(run);
    }
    return [...data.history].sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at));
  });
}
function loadReport(project, run, store) {
  return cached(store.reports, `${project.id}/${run.run_id}`, async () => {
    const loaded = await load(`/results/${project.id}/${run.run_id}/report.json`, true);
    if (loaded.value.project_id !== project.id || loaded.value.run_id !== run.run_id) throw new Error('Identity mismatch');
    return loaded;
  });
}
function loadRun(project, run, store) {
  return cached(store.runs, `${project.id}/${run.run_id}`, async () => {
    const loaded = await loadReport(project, run, store), report = loaded.value;
    let snapshots = null, lifecycle = null, assessments = null;
    if (run.skill_snapshots) {
      if (run.skill_snapshots !== `${run.run_id}/skill-snapshots.json`) throw new Error('Invalid snapshot reference');
      snapshots = await load(`/results/${project.id}/${run.skill_snapshots}`);
      if (snapshots.project_id !== report.project_id || snapshots.run_id !== report.run_id ||
          snapshots.skill_id !== run.skill_id || snapshots.base?.sha256 !== run.base_skill_sha256 ||
          (snapshots.candidate?.sha256 ?? null) !== (run.candidate_skill_sha256 ?? null)) {
        throw new Error('Skill snapshot identity mismatch');
      }
    }
    if (run.skill_evolution) {
      const data = await load(`/results/${project.id}/${run.skill_evolution}`, false, 2097152);
      lifecycle = await validateEvolution(data, report, loaded.raw, run, snapshots);
    }
    if (run.skill_assessments) {
      validateAssessmentSummary(run);
      const data = await load(`/results/${project.id}/${run.skill_assessments}`);
      assessments = await validateAssessments(data, report, loaded.raw, lifecycle);
    }
    return { report, snapshots, lifecycle, assessments };
  });
}
function loadEvidence(project, runs, store) {
  return Promise.all(runs.map(async run => {
    try { return { run, bundle: await loadRun(project, run, store) }; }
    catch (error) { return { run, error }; }
  }));
}
async function showProjectSummary(project, runs, token, store) {
  const latest = summarizeHistory(runs).newestCompleted;
  const [evidence, completed] = await Promise.all([
    loadEvidence(project, runs.filter(run => run.skill_evolution), store),
    latest ? loadReport(project, latest, store).catch(error => ({ error })) : null,
  ]);
  if (token === selection && !sampleMode) renderProjectSummary(runs, evidence, completed);
}
function resetSelection(isSample) {
  ++selection; ++reportSelection;
  sampleMode = isSample;
  activeRun = null; history = [];
  selectedSkill = null;
  clearEvolution();
  $('project-summary').hidden = true;
  $('project-summary').replaceChildren();
  $('detail').hidden = true;
  for (const id of ['guide', 'improvement-evidence', 'execution', 'cost-card', 'time-card', 'decision-reasons',
    'task-results', 'skill-changes', 'quality-summary', 'run-title', 'execution-scope', 'run-time', 'all-metrics', 'provenance']) {
    $(id).replaceChildren();
  }
  $('report-link').removeAttribute('href');
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
function renderRunGroups(container, runs, renderRun) {
  const expanded = new Set([...container.querySelectorAll('details[open]')].map(group => group.dataset.group));
  container.replaceChildren();
  let blocked = [];
  const flush = () => {
    if (!blocked.length) return;
    const group = node('details', undefined, 'blocked-runs');
    group.dataset.group = blocked[0].run_id;
    group.open = expanded.has(group.dataset.group);
    group.append(node('summary', `차단 기록 ${blocked.length}건 (최근 ${stamp(blocked[0].created_at)})`));
    for (const run of blocked) group.append(renderRun(run));
    container.append(group);
    blocked = [];
  };
  for (const run of runs) {
    if (['blocked', 'configuration_required'].includes(run.execution_status)) blocked.push(run);
    else { flush(); container.append(renderRun(run)); }
  }
  flush();
}
function renderHistory() {
  $('history-count').textContent = `${history.length}건`;
  const filter = $('history-filter').value;
  const rows = history.filter(row => !filter || row.purpose === filter);
  renderRunGroups($('history'), rows, run => {
    const button = node('button', undefined, 'run');
    button.type = 'button'; button.dataset.run = run.run_id;
    button.setAttribute('aria-pressed', String(run.run_id === activeRun));
    const title = node('span', purposes[run.purpose]);
    title.append(node('small', sampleMode ? '합성 샘플 · 실제 평가 아님' :
      run.origin === 'historical_import' ? '과거 로컬 이력' : '프로젝트 평가'));
    const state = run.execution_status;
    button.append(node('time', stamp(run.created_at)), title, node('span', labels[state], `badge ${state}`));
    button.addEventListener('click', () => selectRun(run, selection));
    return button;
  });
  if (!rows.length) $('history').append(node('p', history.length ? '해당 종류의 이력이 없습니다.' : '아직 저장된 평가 이력이 없습니다.', 'empty'));
  renderSkillHistory();
}
function skillGroups() {
  if (sampleMode && sample) {
    return sample.skills.map(skill => ({ id: skill.id, name: skill.name || skill.id, versions: Object.keys(skill.versions).length,
      runs: skill.reports.map(report => ({ ...report, execution_status: report.execution.status })) }));
  }
  const groups = new Map();
  for (const skill of activeProject?.detected_skills || []) {
    groups.set(skill.skill_key, { id: skill.skill_key, name: skill.display_name,
      source_path: skill.source_path, registered: true, detected: true, hashes: new Set(), runs: [] });
  }
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
  if (!groups.length) {
    $('skill-history-runs').replaceChildren();
    $('skill-list').append(node('p', '원문·식별 정보가 연결된 Skill이 없습니다. 미연결 기록은 실행 이력에서 확인하세요.', 'empty'));
    return;
  }
  if (!activeRun && !groups.some(group => group.id === selectedSkill)) selectedSkill = groups[0].id;
  for (const group of groups) {
    const button = node('button', undefined, 'skill-item');
    button.type = 'button';
    button.setAttribute('aria-pressed', String(group.id === selectedSkill));
    button.append(node('strong', group.name || group.id), node('small', `버전 ${group.versions}개 · 연결 기록 ${group.runs.length}건`),
      node('small', group.runs.length ? `최근 ${stamp(group.runs[0].created_at)}${sampleMode ? ' · 합성 샘플' : ''}` : '탐지됨 · 미평가'));
    if (!sampleMode) button.append(node('small', group.registered ? group.id : '고정 Skill ID 미등록 · 프로젝트 범위 이름'));
    button.addEventListener('click', () => chooseSkill(group.id));
    $('skill-list').append(button);
  }
  const current = groups.find(group => group.id === selectedSkill);
  if (!current) {
    $('skill-history-runs').replaceChildren(node('p', '선택한 실행 기록은 특정 Skill과 연결되지 않았습니다.', 'empty'));
    return;
  }
  renderRunGroups($('skill-history-runs'), current.runs, run => {
    const button = node('button', undefined, 'skill-run');
    button.type = 'button'; button.dataset.run = run.run_id;
    button.setAttribute('aria-pressed', String(run.run_id === activeRun));
    const title = node('span', purposes[run.purpose]);
    title.append(node('small', stamp(run.created_at)));
    button.append(title, node('span', labels[run.execution_status], `badge ${run.execution_status}`));
    button.addEventListener('click', () => selectRun(run, selection));
    return button;
  });
  $('skill-history-runs').prepend(node('p', current.runs.length ?
    `${current.id}의 기록 · 선택하면 위의 As-Is / To-Be와 평가 결과가 바뀝니다.` :
    `${current.name} · 탐지된 Skill이며 아직 연결된 평가 기록이 없습니다.`));
}
function chooseSkill(id) {
  if (sampleMode) { selectSampleSkill(id); return; }
  const group = skillGroups().find(group => group.id === id);
  if (!group) throw new Error('Skill history unavailable');
  selectedSkill = id;
  $('skill-select').value = id;
  const run = group.runs[0];
  if (run) selectRun(run, selection);
  else {
    ++reportSelection;
    activeRun = null;
    $('detail').hidden = true;
    $('error').hidden = true;
    clearEvolution();
    $('origin').textContent = '미평가';
    $('freshness').textContent = `${group.source_path} · 탐지 정보이며 품질 평가 결과가 아닙니다.`;
    $('selection-summary').textContent = `${group.name} · 아직 평가 기록이 없습니다.`;
    renderHistory();
  }
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
    let report, detail = null, snapshots = null, lifecycle = null, assessments = null;
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
      const project = activeProject;
      const bundle = await loadRun(project, run, cache);
      if (request !== reportSelection || token !== selection) return;
      ({ report, snapshots, lifecycle, assessments } = bundle);
      $('report-link').href = `/results/${project.id}/${run.run_id}/report.json`;
      const skills = runSkills(run).filter(skill => !assessments || assessments.skills.some(row => row.skill_key === skill.id));
      selectedSkill = skills.some(item => item.id === selectedSkill) ? selectedSkill : skills[0]?.id ?? null;
      $('skill-select').value = selectedSkill || '';
    }
    if (request !== reportSelection || token !== selection) return;
    renderReport(report, activeProject, detail, snapshots);
    clearEvolution();
    if (lifecycle) renderEvolution(lifecycle, selectedSkill, history, next => selectRun(next, selection));
    if (assessments) renderAssessment(assessments, selectedSkill, report.origin);
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
  const token = selection, store = cache;
  $('project-title').textContent = project.id;
  $('skill-select').replaceChildren(node('option', 'Skill 정보 미기록'));
  $('skill-select').disabled = true;
  renderProjectSummary();
  try {
    const runs = await loadHistory(project, store);
    if (token !== selection) return;
    history = runs;
    $('skill-select').replaceChildren();
    for (const group of skillGroups()) {
      const option = node('option', group.detected ?
        `${group.name} · ${group.source_path}${group.runs.length ? '' : ' · 미평가'}` :
        group.registered ? `${group.name} (${group.id})` : group.name); option.value = group.id;
      $('skill-select').append(option);
    }
    const groups = skillGroups();
    $('skill-select').disabled = groups.length === 0;
    if (!groups.length) $('skill-select').append(node('option', '탐지·연결된 Skill 없음'));
    renderHistory();
    const run = history.find(row => row.guide_status === 'completed' && row.execution_status === 'completed') ||
      history.find(row => row.purpose === 'comparison') || history[0];
    if (!run) {
      if (groups.length) chooseSkill(groups[0].id);
      else {
        $('origin').textContent = '미평가';
        $('selection-summary').textContent = '평가 기록이 없습니다.';
      }
    }
    await Promise.all([showProjectSummary(project, runs, token, store), run ? selectRun(run, token) : null]);
    if (token === selection && project.skill_discovery_error) fail(new Error(`Skill discovery blocked: ${project.skill_discovery_error}`));
  } catch (error) {
    if (token === selection) {
      renderProjectSummary();
      $('project-summary').lastElementChild.replaceWith(node('p', '기록 없음 · 공개 이력을 확인하지 못했습니다.', 'summary-warning'));
      fail(error);
    }
  }
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
  $('skill-select').disabled = true;
  $('skill-select').replaceChildren(node('option', '샘플 Skill을 불러오는 중입니다.'));
  const token = selection;
  $('project-title').textContent = '샘플 화면';
  try {
    const loaded = await load('/sample-data.json');
    if (token !== selection) return;
    if (loaded.synthetic !== true || !Array.isArray(loaded.skills) || !loaded.skills.length ||
        loaded.skills.length > 256 || !idPattern.test(loaded.project_id) ||
        projects.some(project => project.id === loaded.project_id)) {
      throw new Error('Invalid synthetic sample boundary');
    }
    sample = loaded;
    activeProject = { id: sample.project_id, current_run: null, state: 'sample' };
    $('skill-select').replaceChildren();
    for (const skill of sample.skills) {
      const option = node('option', skill.source_path ? `${skill.name} · ${skill.source_path}` : skill.id);
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
  cache = newCache();
  const token = selection;
  $('project-title').textContent = '프로젝트 평가';
  $('skill-select').replaceChildren(node('option', 'Skill 정보 미기록'));
  $('skill-select').disabled = true;
  try {
    const index = await load('/results/index.json');
    if (token !== selection) return;
    if (!Array.isArray(index.projects)) throw new Error('Invalid catalog');
    const ids = new Set();
    for (const project of index.projects) {
      if (!project || typeof project.id !== 'string' || !idPattern.test(project.id) || ids.has(project.id) ||
          !Number.isInteger(project.history_count) || project.history_count < 0 ||
          !['active', 'blocked', 'removed'].includes(project.state)) throw new Error('Invalid project');
      ids.add(project.id);
      if (project.detected_skills !== undefined) {
        if (!Array.isArray(project.detected_skills) || project.detected_skills.length > 256) throw new Error('Invalid Skill inventory');
        const keys = new Set(), paths = new Set();
        for (const skill of project.detected_skills) {
          if (!/^[a-z0-9][a-z0-9-]{0,63}:[a-z0-9][a-z0-9-]{0,63}$/.test(skill.skill_key) ||
              typeof skill.display_name !== 'string' || !skill.display_name || skill.display_name.length > 1024 ||
              typeof skill.source_path !== 'string' || skill.source_path.length > 1024 ||
              !/^(?:\.github\/skills|\.claude\/skills|skills)\//.test(skill.source_path) ||
              skill.source_path.split('/').some(part => !part || part === '..' || part === '.') ||
              /[\\\u0000-\u001f\u007f]/.test(skill.source_path) ||
              keys.has(skill.skill_key) || paths.has(skill.source_path)) throw new Error('Invalid Skill inventory');
          keys.add(skill.skill_key); paths.add(skill.source_path);
        }
      }
    }
    projects = index.projects;
    $('projects').replaceChildren();
    $('project-count').textContent = String(projects.length);
    for (const project of projects) {
      const button = node('button', undefined, 'project');
      button.type = 'button'; button.dataset.project = project.id;
      button.setAttribute('aria-pressed', 'false');
      button.setAttribute('aria-label', `${project.id} 평가 이력 ${project.history_count}건`);
      const count = node('small');
      count.append(node('span', '평가 이력 ', 'project-count-label'), node('span', '이력 ', 'project-count-short'), `${project.history_count}건`);
      button.append(node('strong', project.id), count);
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
function setupNavigation() {
  const navigation = document.querySelector('.section-nav');
  const links = [...navigation.querySelectorAll('a')];
  const sections = links.map(link => $(link.hash.slice(1)));
  const projectList = $('projects');
  const scrollHint = document.querySelector('.project-scroll-hint');
  let frame = null, selectedProject = null, projectWidth = 0;
  const reveal = (container, item) => {
    const bounds = container.getBoundingClientRect(), target = item.getBoundingClientRect();
    if (target.left < bounds.left + 4) container.scrollLeft += target.left - bounds.left - 4;
    else if (target.right > bounds.right - 4) container.scrollLeft += target.right - bounds.right + 4;
  };
  const update = () => {
    frame = null;
    const visible = sections.filter(section => section.getClientRects().length);
    for (const [index, link] of links.entries()) {
      const hidden = !visible.includes(sections[index]);
      if (link.hidden !== hidden) link.hidden = hidden;
    }
    const bounds = navigation.getBoundingClientRect();
    document.documentElement.style.setProperty('--section-offset', `${Math.ceil(bounds.height) + 20}px`);
    const atBottom = window.scrollY > 0 && window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 2;
    const current = atBottom ? visible.at(-1) :
      visible.filter(section => section.getBoundingClientRect().top <= bounds.bottom + 24).at(-1) || visible[0];
    for (const [index, link] of links.entries()) {
      if (sections[index] === current) {
        if (!link.hasAttribute('aria-current')) {
          link.setAttribute('aria-current', 'location');
          reveal(navigation, link);
        }
      } else link.removeAttribute('aria-current');
    }
    const selected = projectList.querySelector('[aria-pressed="true"]');
    if (selected && (selected !== selectedProject || projectList.clientWidth !== projectWidth)) reveal(projectList, selected);
    selectedProject = selected;
    projectWidth = projectList.clientWidth;
    scrollHint.hidden = projectList.scrollWidth <= projectList.clientWidth + 1;
    const atEnd = projectList.scrollLeft + projectList.clientWidth >= projectList.scrollWidth - 2;
    scrollHint.textContent = atEnd ? '← 이전' : '옆으로 이동 →';
  };
  const schedule = () => { if (frame === null) frame = requestAnimationFrame(update); };
  window.addEventListener('scroll', schedule, { passive: true });
  window.addEventListener('resize', schedule);
  projectList.addEventListener('scroll', schedule, { passive: true });
  const layout = new ResizeObserver(schedule);
  for (const element of [$('main'), navigation, projectList]) layout.observe(element);
  new MutationObserver(schedule).observe($('main'), { subtree: true, childList: true, attributes: true, attributeFilter: ['hidden'] });
  new MutationObserver(schedule).observe(projectList, { subtree: true, childList: true, attributes: true, attributeFilter: ['aria-pressed'] });
  update();
}
setupNavigation();
refresh();
