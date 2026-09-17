export const $ = id => document.getElementById(id);
export const labels = {
  completed: '실행 완료', failed: '실패', blocked: '차단', not_assessed: '미평가',
  configuration_required: '설정 필요', pass: '통과', review: '검토 필요', not_applicable: '적용 제외',
};
export const purposes = {
  baseline: '기준 평가', comparison: '후보 비교', candidate: '후보 생성',
  calibration: '평가기 교정', project_assessment: '프로젝트 평가',
};
const decisions = {
  rejected: '후보 거절', eligible_for_canary: '잠정 canary 자격', blocked: '판정 차단',
  calibration_passed: '교정 통과', calibration_failed: '교정 실패',
};
const reasons = {
  no_skills: '프로젝트에서 평가할 스킬이 발견되지 않았습니다.',
  no_adapter: '실행 기반 평가 어댑터를 설정해야 합니다.',
  live_disabled: '실제 모델 평가가 아직 활성화되지 않았습니다.',
  missing_auth: '모델 평가 인증이 설정되지 않았습니다.',
  missing_limits: '모델 호출·시간 한도를 설정해야 합니다.',
  guide_integration_pending: '공통 가이드 평가기 연동이 아직 준비되지 않았습니다.',
  historical_import: '과거 로컬 실행의 공개용 요약입니다.',
  evaluation_completed: '실행 완료와 품질 통과는 다릅니다. 기록된 지표를 확인하세요.',
  evaluation_failed: '평가가 정상적으로 완료되지 않았습니다.',
  unsupported_adapter: '등록된 실행 어댑터를 지원하지 않습니다.',
  unsafe_project: '프로젝트 구조 또는 파일 검증이 차단됐습니다.',
  runtime_error: '평가 환경이나 계약을 확인해야 합니다.',
  call_limit: '허용된 모델 호출 한도에 도달했습니다.',
  time_limit: '허용된 평가 시간에 도달했습니다.',
  assessment_unverified: '개선 또는 프로젝트 회귀 검증에 필요한 근거가 부족합니다.',
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

export function node(tag, text, className) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  if (className) item.className = className;
  return item;
}
export function stamp(value) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) throw new Error('Invalid timestamp');
  return date.toLocaleString('ko-KR', { year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit' });
}
function number(value, places = 3) {
  return value === null || value === undefined ? '미기록' :
    value.toLocaleString('ko-KR', { maximumFractionDigits: places });
}
export function measurementChange(base, candidate) {
  if (typeof base !== 'number' || typeof candidate !== 'number' ||
      !Number.isFinite(base) || !Number.isFinite(candidate) || base <= 0 || candidate < 0) return null;
  const change = (candidate - base) / base * 100;
  return Number.isFinite(change) ? change : null;
}
function fraction(value, total) {
  return value === null || value === undefined || total === null || total === undefined ?
    '미기록' : `${number(value)}/${number(total)}`;
}
function badge(status) {
  if (!labels[status]) throw new Error('Invalid assessment status');
  return node('span', labels[status], `badge ${status}`);
}
function explanation(message) {
  const details = node('details', undefined, 'state-explanation');
  details.append(node('summary', '왜 비어 있나요?'), node('p', message, 'reason'));
  return details;
}
function empty(container, title, message) {
  const box = node('div', undefined, 'empty-state');
  box.append(node('strong', title), explanation(message));
  container.replaceChildren(box);
}
export function hashValue(value) {
  const wrapper = node('span', undefined, 'hash-value');
  const prefix = value.startsWith('sha256:') ? 'sha256:' : '';
  const digest = node('code', prefix + value.slice(prefix.length, prefix.length + 12));
  digest.title = value;
  const copy = node('button', '복사', 'copy-hash');
  copy.type = 'button';
  copy.setAttribute('aria-label', 'SHA-256 전체 값 복사');
  const status = node('span', '', 'copy-status');
  status.setAttribute('role', 'status');
  copy.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(value);
      status.textContent = '복사했습니다.';
    } catch {
      status.textContent = '복사하지 못했습니다. 전체 값은 해시에 마우스를 올려 확인해 주세요.';
    }
  });
  wrapper.append(digest, copy, status);
  return wrapper;
}
function metricList(metrics) {
  const list = node('dl', undefined, 'metrics');
  for (const [key, value] of Object.entries(metrics || {})) {
    if (!metricLabels[key] || (value !== null && (typeof value !== 'number' || !Number.isFinite(value)))) {
      throw new Error('Invalid metric');
    }
    const line = node('div');
    line.append(node('dt', metricLabels[key]), node('dd', number(value)));
    list.append(line);
  }
  return list;
}
export function table(headers, rows) {
  const wrapper = node('div', undefined, 'table-scroll');
  const element = node('table');
  const head = node('thead');
  const heading = node('tr');
  headers.forEach(text => { const th = node('th', text); th.scope = 'col'; heading.append(th); });
  head.append(heading);
  const body = node('tbody');
  for (const values of rows) {
    const row = node('tr');
    for (const value of values) {
      const cell = node('td');
      if (value instanceof Node) cell.append(value);
      else cell.textContent = String(value);
      row.append(cell);
    }
    body.append(row);
  }
  element.append(head, body);
  wrapper.append(element);
  return wrapper;
}

export function summarizeHistory(runs) {
  const skills = new Set(), legacySkills = new Set(), versions = new Set(), legacyVersions = new Set();
  const completed = run => run.guide_status === 'completed' && run.execution_status === 'completed';
  const blocked = run => ['blocked', 'configuration_required'].includes(run.execution_status);
  const imported = run => run.origin === 'historical_import';
  for (const run of runs) {
    for (const binding of run.evolution_skills || []) {
      skills.add(binding.skill_key);
      for (const version of [binding.base_version_id, binding.candidate_version_id]) if (version) versions.add(version);
    }
    if (run.skill_id) {
      legacySkills.add(run.skill_id);
      for (const hash of [run.base_skill_sha256, run.candidate_skill_sha256]) if (hash) legacyVersions.add(hash);
    }
  }
  return { skills: skills.size + legacySkills.size, versions: versions.size + legacyVersions.size,
    total: runs.length, completed: runs.filter(completed).length, newestCompleted: runs.find(completed) ?? null,
    blocked: runs.filter(blocked).length, imported: runs.filter(imported).length,
    unassessed: runs.filter(run => !completed(run) && !blocked(run) && !imported(run)).length };
}

function newestAssessment(evidence) {
  const newest = evidence.find(item => item.run.skill_assessments);
  return { run: newest?.run, skill: newest?.bundle?.assessments?.skills[0] ?? null };
}

export function renderProjectSummary(runs = null, evidence = [], completedReport = null) {
  const container = $('project-summary');
  const title = node('h2', '프로젝트 평가 요약');
  title.id = 'project-summary-title';
  container.replaceChildren(title);
  container.hidden = false;
  if (runs === null) {
    const loading = node('p', '공개 이력의 요약 근거를 불러오는 중입니다.', 'muted');
    loading.setAttribute('role', 'status');
    container.append(loading);
    return;
  }
  const summary = summarizeHistory(runs), list = node('dl', undefined, 'summary-grid');
  const entry = (id, label, text) => {
    const line = node('div'), value = node('dd', text);
    value.id = id;
    line.append(node('dt', label), value); list.append(line);
    return value;
  };
  entry('summary-skills', 'Skill·버전', `Skill ${summary.skills}개 · 버전 ${summary.versions}개`);
  entry('summary-history', '평가 이력', `평가 기록 ${summary.total}건 · 완료 ${summary.completed} · 차단 ${summary.blocked} · 과거 가져오기 ${summary.imported} · 미평가 ${summary.unassessed}`);
  const recent = entry('summary-completed', '최근 완료 평가', summary.newestCompleted ?
    `${stamp(summary.newestCompleted.created_at)} · 커밋 ` : '없음');
  if (summary.newestCompleted) {
    const commit = completedReport?.value?.source_commit;
    const code = node('code', typeof commit === 'string' && /^[a-f0-9]{40}$/.test(commit) ? commit.slice(0, 12) : '기록 없음');
    if (code.textContent !== '기록 없음') code.title = commit;
    recent.append(code);
  }
  const assessed = evidence.filter(item => item.run.skill_assessments);
  const skills = assessed.flatMap(item => item.bundle?.assessments?.skills || []);
  entry('summary-decisions', '후보 판정', !assessed.length ? '후보 판정 없음' : !skills.length ? '미평가' :
    [['improved', '상승'], ['not_improved', '변화 없음'], ['unverified', '검증 불충분'], ['rejected', '거절']]
      .map(([status, label]) => `${label} ${skills.filter(row => row.decision.status === status).length}`).join(' · '));
  const adoptions = evidence.flatMap(item => item.bundle?.lifecycle?.data.records.adoptions || [])
    .filter(row => row.state !== 'unknown');
  entry('summary-adoptions', '채택 기록', adoptions.length ? `${adoptions.length}건` :
    evidence.some(item => item.error) ? '기록 없음' : 'SkillOps에 채택 기록 없음');
  const latest = newestAssessment(evidence);
  const changes = ['cost_nano_aiu', 'elapsed_seconds'].map(key => measurementChange(
    latest.skill?.applications.base?.measurement?.[key], latest.skill?.applications.candidate?.measurement?.[key]));
  const display = change => change === null ? '미기록' : `${change > 0 ? '+' : ''}${number(change, 2)}%`;
  const usage = entry('summary-usage', '최근 평가의 적용 비용·시간',
    `기존 → 후보 비용 ${display(changes[0])} / 시간 ${display(changes[1])}${changes.some(change => change !== null) ? ' (현장 계산)' : ''}`);
  if (latest.skill) usage.append(node('small', `${latest.skill.skill_key} · ${stamp(latest.run.created_at)} · Skill 한 건 기준입니다.`));
  const explanation = node('details', undefined, 'summary-explanation');
  explanation.append(node('summary', '이 요약은 무엇을 세나요?'),
    node('p', '공개 이력의 식별자와 평가 상태를 세며, 후보 판정과 채택은 불러와 검증한 공개 근거만 집계합니다. 품질 점수는 평균하지 않으며 비용·시간은 최근 평가의 Skill 한 건만 비교합니다.'));
  container.append(list, node('p', `후보·채택 집계는 근거를 확인한 실행 ${evidence.filter(item => item.bundle).length}건 기준입니다. 완료·차단·과거 가져오기 집계는 서로 겹칠 수 있습니다.`, 'summary-scope'), explanation);
  if (evidence.some(item => item.error) || completedReport?.error) {
    container.append(node('p', '일부 공개 근거를 확인하지 못해 요약이 불완전합니다. 새로고침으로 다시 확인할 수 있습니다.', 'summary-warning'));
  }
}

function renderQuality(report, detail) {
  const container = $('guide');
  $('quality-summary').textContent = detail ? '선택 Skill · 합성 평가 예시' : '기록된 검사만 표시';
  if (!detail?.quality?.length) {
    container.replaceChildren(badge(report.guide.status));
    container.append(metricList(report.guide.metrics));
    const missing = node('div');
    empty(missing, '세부 품질 검사 결과가 공개 기록에 없습니다.',
      `${reasons[report.guide.reason_code]} 참고 기준의 설명이며, 실제 정적 검사나 LLM 채점이 수행됐다는 뜻은 아닙니다.`);
    container.append(missing);
    return;
  }
  const finalKey = detail.candidate ? 'candidate' : 'base';
  const applicable = detail.quality.filter(check => check[finalKey] !== 'not_applicable');
  const passed = applicable.filter(check => check[finalKey] === 'pass').length;
  const totals = node('div', undefined, 'quality-totals');
  totals.append(node('strong', `적용 항목 ${passed}/${applicable.length} 통과`),
    node('span', `적용 제외 ${detail.quality.length - applicable.length}개`, 'badge not_applicable'),
    node('span', detail.candidate ? '기존 → 후보' : '기존 Skill', 'muted'));
  const list = node('div', undefined, 'check-list');
  for (const check of detail.quality) {
    const item = node('details', undefined, 'check-row');
    const summary = node('summary');
    const name = node('span', check.name, 'check-name');
    name.append(node('small', ` / ${check.method} · 예시`, 'check-source'));
    const pair = node('span', undefined, 'check-pair');
    pair.append(badge(check.base));
    if (detail.candidate) pair.append(node('span', '→'), badge(check.candidate));
    summary.append(name, pair);
    item.append(summary, node('p', check.finding));
    list.append(item);
  }
  container.replaceChildren(totals, list);
}

function renderEvidence(detail) {
  const container = $('improvement-evidence');
  if (!detail?.improvement) {
    empty(container, '개선 근거가 공개 기록에 없습니다.',
      '관측·가설·변경·재평가를 연결한 근거가 필요합니다. 후보 생성만으로 개선을 인정하지 않습니다.');
    return;
  }
  const evidence = detail.improvement;
  const chain = node('ol', undefined, 'evidence-chain');
  for (const [key, label] of [['observation', '관측된 문제'], ['hypothesis', '개선 가설'],
    ['change', '지침 변경'], ['verification', '재평가 결과']]) {
    const step = node('li');
    step.append(node('strong', label), node('p', evidence[key]));
    chain.append(step);
  }
  container.replaceChildren(chain, node('p', evidence.conclusion, 'conclusion'));
  if (evidence.source_run) {
    const link = node('button', '근거가 된 기준 평가 보기');
    link.type = 'button';
    link.dataset.evidenceRun = evidence.source_run;
    container.append(link);
  }
}

export function renderMetric(container, title, key, metrics, origin = null) {
  container.replaceChildren(node('h3', title));
  const base = metrics[`base_${key}`];
  const candidate = metrics[`candidate_${key}`];
  const improvement = metrics[key === 'cost_nano_aiu' ? 'cost_improvement_percent' : 'time_improvement_percent'];
  if (base === null || base === undefined || candidate === null || candidate === undefined) {
    container.append(node('strong', '미기록', 'decision-title'),
      explanation('같은 평가의 기존·후보 측정값이 필요합니다.'));
    return null;
  }
  const computed = improvement === undefined && base > 0;
  const change = computed ? measurementChange(base, candidate) :
    typeof improvement === 'number' ? -improvement : null;
  const line = node('div');
  if (change === null || !Number.isFinite(change)) {
    line.append(node('strong', '변화율 미기록'));
  } else {
    line.append(node('strong', `${change > 0 ? '+' : ''}${number(change, 2)}%`, 'big-number'),
      node('span', change > 0 ? '증가' : change < 0 ? '감소' : '변화 없음',
        `delta ${change > 0 ? 'worse' : change < 0 ? 'better' : ''}`),
      node('small', computed ? '현장 계산' : '기록된 변화율', 'metric-origin'));
  }
  const cost = key === 'cost_nano_aiu';
  const bars = node('div', undefined, 'metric-bars');
  for (const [label, value, name] of [['기존', base, 'base'], ['후보', candidate, 'candidate']]) {
    const row = node('div', undefined, 'bar-row');
    const track = node('div', undefined, 'bar-track');
    track.setAttribute('aria-hidden', 'true');
    const bar = node('div', undefined, `bar ${name}`);
    bar.style.width = `${Math.max(base, candidate) === 0 ? 0 : value / Math.max(base, candidate) * 100}%`;
    track.append(bar);
    const display = cost ? (value / 1e9).toLocaleString('ko-KR', { maximumSignificantDigits: 6 }) : number(value);
    row.append(node('span', label), track, node('span', display, 'bar-value'));
    bars.append(row);
  }
  container.append(line, bars, node('p', origin === 'layout' ? '합성 예시 · 실제 CLI 측정값이 아닙니다.' : cost ?
    origin === 'sample' ? 'AIU · 기록된 사용량 단위이며 통화 금액이 아닙니다.' :
      'AIU는 CLI가 기록한 Copilot 사용량 단위입니다.' : '초 · 기록된 실행 시간', 'metric-note'));
  return Number.isFinite(change) ? change : null;
}

function renderExecution(report, detail) {
  const metrics = report.execution.metrics || {};
  $('all-metrics').replaceChildren(metricList(metrics));
  const container = $('execution');
  const decision = report.execution.decision;
  if (decision !== null && decision !== undefined && !decisions[decision]) throw new Error('Invalid decision');
  container.className = `outcome-card ${decision === 'rejected' ? 'rejected' : ''}`;
  const heading = node('div', undefined, 'decision-heading');
  heading.append(node('strong', decisions[decision] || labels[report.execution.status], 'decision-title'));
  container.replaceChildren(node('h3', '선택한 실행의 판단'), heading);
  container.append(!detail && report.execution.status !== 'completed' ? explanation(reasons[report.execution.reason_code]) :
    node('p', detail ? '합성 평가 예시 · 실제 성과 아님' : reasons[report.execution.reason_code], 'reason'));
  if (report.purpose === 'comparison') {
    const correctness = node('div', undefined, 'correctness');
    correctness.append(node('span', '정답 작업'),
      node('strong', `${fraction(metrics.base_correctness_successes, metrics.base_requested)} → ${fraction(metrics.candidate_correctness_successes, metrics.candidate_requested)}`));
    container.append(correctness, node('p', `Judge ${number(metrics.base_judge_score)} → ${number(metrics.candidate_judge_score)}`, 'metric-note'));
  } else {
    container.append(metricList(metrics));
  }
  $('execution-scope').textContent = detail?.scope ||
    '공개 기록에 세부 작업 범위가 없습니다. 이 결과를 프로젝트 전체의 종합 검증으로 해석하지 않습니다.';
  const changes = [renderMetric($('cost-card'), '실행 비용 · 기존 → 후보', 'cost_nano_aiu', metrics, detail ? 'layout' : report.origin),
    renderMetric($('time-card'), '실행 시간 · 기존 → 후보', 'elapsed_seconds', metrics, detail ? 'layout' : report.origin)];
  if (changes.some(change => change !== null && change > 5)) {
    heading.append(node('span', '비용·시간 증가 주의', 'badge review efficiency-warning'));
  }
  if (detail?.policy) {
    $('decision-reasons').replaceChildren(node('p', detail.policy.summary, 'conclusion'),
      node('p', `판정 기준: ${detail.policy.version} · 합성 예시`, 'reason'),
      table(['항목', '통과 기준', '실제 변화'], detail.policy.rows.map(row => [row.name, row.threshold, row.observed])));
  } else {
    empty($('decision-reasons'), '판정 정책이 공개 기록에 없습니다.',
      '저장된 결론과 측정값만 표시합니다. 실제 임계값이나 거절 사유를 임의로 복원하지 않습니다.');
  }
  if (detail?.tasks?.length) {
    $('task-results').replaceChildren(table(['평가 작업', '기존 → 후보'], detail.tasks.map(task => [
      task.name, `${task.base}/${task.total} → ${task.candidate}/${task.total}`,
    ])), node('p', '고정 검사 통과 수 · 합성 예시', 'metric-note'));
  } else {
    empty($('task-results'), '작업별 상세 결과가 공개 기록에 없습니다.',
      '위의 전체 지표와 별개입니다. 작업별 검사 수와 근거가 기록되면 이 영역에 표시합니다.');
  }
}

function diffLines(before, after) {
  const a = before === '' ? [] : before.split('\n');
  const b = after === '' ? [] : after.split('\n');
  if (a.length > 400 || b.length > 400 || before.length + after.length > 100000) {
    throw new Error('Skill comparison exceeds display limits');
  }
  const lengths = Array.from({ length: a.length + 1 }, () => new Uint16Array(b.length + 1));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lengths[i][j] = a[i] === b[j] ? lengths[i + 1][j + 1] + 1 : Math.max(lengths[i + 1][j], lengths[i][j + 1]);
    }
  }
  const lines = [];
  let i = 0, j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      lines.push(['context', `  ${a[i++]}`]); j++;
    } else if (j < b.length && (i === a.length || lengths[i][j + 1] > lengths[i + 1][j])) {
      lines.push(['added', `+ ${b[j++]}`]);
    } else {
      lines.push(['removed', `- ${a[i++]}`]);
    }
  }
  return lines;
}

export function renderSkill(detail, synthetic) {
  const container = $('skill-changes');
  if (!detail?.base && !detail?.candidate) {
    empty(container, 'Skill 원문이 공개 기록에 없습니다.',
      '평가 당시 버전의 공개가 검토된 원문·해시·변경 비교가 필요합니다. 현재 파일로 과거 원문을 대체하지 않습니다.');
    return;
  }
  const availability = snapshot => snapshot?.availability || (snapshot ? 'captured' : 'absent');
  const version = snapshot => snapshot?.version?.startsWith('sha256:') ? hashValue(snapshot.version) :
    snapshot?.version ? node('span', snapshot.version) : snapshot?.sha256 ? hashValue(snapshot.sha256) :
      node('span', availability(snapshot) === 'uncaptured' ? '원문 미보관' : '파일 없음');
  const displayable = snapshot => typeof snapshot?.content === 'string' &&
    snapshot.content.length <= 50000 && snapshot.content.split('\n').length <= 400;
  const meta = node('div', undefined, 'skill-meta');
  const versions = node('span', undefined, 'badge version-badge');
  versions.append(version(detail.base));
  if (detail.candidate) versions.append(node('span', '→'), version(detail.candidate));
  meta.append(node('strong', detail.skill_id),
    versions,
    node('span', synthetic ? '합성 예시 · 실제 평가 아님' : detail.file_path ?
      '보관된 원문만 해시 확인 · 미보관 파일은 판단하지 않음' : '평가 당시 원문 · 기록된 SHA-256과 일치', 'muted'));
  container.replaceChildren(meta);
  if (detail.file_path) container.append(node('p', detail.file_path, 'source-hash'));
  const columns = node('div', undefined, 'skill-compare-grid');
  for (const [key, label] of [['base', 'As-Is · 기존 Skill 원문'], ['candidate', 'To-Be · 후보 Skill 원문']]) {
    if (!detail[key] && !detail.has_candidate_version) continue;
    const source = node('details', undefined, 'source-panel');
    source.open = true;
    source.append(node('summary', label));
    if (detail[key]?.sha256) {
      const hash = node('p', 'SHA-256 ', 'source-hash');
      hash.append(hashValue(detail[key].sha256));
      source.append(hash);
    }
    if (detail[key]?.bytes !== undefined) source.append(node('p', `${detail[key].bytes} bytes`, 'source-hash'));
    if (key === 'candidate') source.append(node('p', '비교 대상 후보이며 채택·배포 승인을 의미하지 않습니다.', 'source-hash'));
    if (availability(detail[key]) === 'uncaptured') {
      source.append(node('p', '원문 미보관 · 해당 파일의 유무와 내용을 판단할 수 없습니다.', 'source-hash'));
    } else if (availability(detail[key]) === 'absent') {
      source.append(node('p', '전체 보관 목록 기준으로 이 버전에는 해당 파일이 없습니다.', 'source-hash'));
    }
    else if (detail[key].content === null) source.append(node('p', '바이너리 파일 · 원본 bytes 보관, 실행·미리보기 안 함', 'source-hash'));
    else if (!displayable(detail[key])) source.append(node('p', '원문 표시 한도 초과 · 전체 bytes는 변경 없이 보관됩니다.', 'source-hash'));
    else source.append(node('pre', detail[key].content));
    columns.append(source);
  }
  container.append(columns);
  if (!detail.candidate && !detail.has_candidate_version) {
    container.append(node('p', '이 기록에는 보관된 후보 Skill이 없습니다.', 'reason'));
    return;
  }
  if ([detail.base, detail.candidate].some(snapshot => availability(snapshot) === 'uncaptured')) {
    container.append(node('p', '원문 미보관으로 변경을 확인할 수 없습니다. 미보관을 추가·삭제로 해석하지 않습니다.', 'reason'));
    return;
  }
  if ([detail.base, detail.candidate].some(snapshot => availability(snapshot) === 'captured' && !displayable(snapshot))) {
    container.append(node('p', '변경 비교 표시 한도 또는 바이너리 파일 · 다른 파일과 평가 근거는 계속 확인할 수 있습니다.', 'reason'));
    return;
  }
  const lines = diffLines(detail.base?.content ?? '', detail.candidate?.content ?? '');
  const toolbar = node('div', undefined, 'diff-toolbar');
  const toggle = node('button', '변경 비교');
  toggle.type = 'button';
  toggle.setAttribute('aria-expanded', 'false');
  toggle.setAttribute('aria-controls', 'skill-diff');
  const diff = node('pre');
  diff.id = 'skill-diff'; diff.hidden = true;
  for (const [kind, text] of lines) diff.append(node('span', text, `diff-line ${kind}`));
  toggle.addEventListener('click', () => {
    diff.hidden = !diff.hidden;
    toggle.setAttribute('aria-expanded', String(!diff.hidden));
  });
  toolbar.append(toggle, node('span',
    `추가 ${lines.filter(line => line[0] === 'added').length}줄 / 삭제 ${lines.filter(line => line[0] === 'removed').length}줄`, 'muted'));
  container.append(toolbar, diff);
}

export function renderReport(report, project, detail = null, snapshots = null) {
  for (const axis of [report.guide, report.execution]) {
    if (!axis || !labels[axis.status] || !reasons[axis.reason_code]) throw new Error('Invalid assessment');
  }
  $('run-title').textContent = purposes[report.purpose];
  $('origin').textContent = detail ? '합성 샘플' : report.origin === 'historical_import' ? '과거 로컬 이력' : '저장된 평가';
  $('run-time').textContent = stamp(report.created_at);
  $('selection-summary').textContent = `${purposes[report.purpose]} / ${stamp(report.created_at)}`;
  $('freshness').textContent = detail ? '화면 설명용 합성 데이터입니다. 실제 Skill 품질·실행 개선의 근거로 사용하지 않습니다.' :
    project.state === 'removed' ? '삭제된 프로젝트의 과거 기록입니다. 현재 버전의 검증 근거가 아닙니다.' :
      project.current_run === report.run_id && report.origin !== 'historical_import' ?
        '선택한 기록이 현재 프로젝트·평가기 식별 정보와 일치합니다. 일치 여부와 평가 통과는 별개입니다.' :
        '선택한 평가는 과거 또는 다른 버전의 기록입니다. 현재 버전의 검증 근거가 아닙니다.';
  renderQuality(report, detail);
  renderEvidence(detail);
  renderExecution(report, detail);
  renderSkill(snapshots || detail, Boolean(detail));
  $('provenance').replaceChildren();
  for (const [key, label] of [['run_id', '실행 ID'], ['origin', '데이터 출처'], ['source_commit', '평가 대상 커밋'],
    ['project_tree_sha256', '프로젝트 내용 SHA-256'], ['evaluator_sha256', '평가기 SHA-256'],
    ['source_report_sha256', '원본 보고서 SHA-256']]) {
    const value = node('dd');
    value.append(key.endsWith('_sha256') && report[key] ? hashValue(report[key]) : node('span', report[key] ?? '미기록'));
    $('provenance').append(node('dt', label), value);
  }
  $('report-link').hidden = Boolean(detail);
}
