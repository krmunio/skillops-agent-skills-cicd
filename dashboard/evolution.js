import { $, node, stamp, renderSkill } from './views.js';

const keyPattern = /^[a-z0-9][a-z0-9-]{0,63}:[a-z0-9][a-z0-9-]{0,63}$/;
const versionPattern = /^sha256:[a-f0-9]{64}$/;
const digestPattern = /^[a-f0-9]{64}$/;
const encoder = new TextEncoder();
const scopes = { entrypoint_only: 'SKILL.md만 보관 · 전체 번들 미확인', complete_bundle: '전체 번들 보관' };
const sourceScopes = { project: '프로젝트', shared: '공유', personal: '개인', plugin: '플러그인', unknown: '출처 범위 미기록' };
const decisions = { rejected: '후보 거절', blocked: '판정 차단', eligible_for_canary: 'Canary 검토 후보 · 배포 승인 아님' };

function check(value) {
  if (!value) throw new Error('Invalid Skill evolution evidence');
}
function exact(value, fields) {
  check(value && typeof value === 'object' && !Array.isArray(value));
  check(Object.keys(value).sort().join(',') === fields.split(' ').sort().join(','));
}
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).sort()
    .map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}
function safePath(path) {
  check(typeof path === 'string' && encoder.encode(path).length <= 1024 &&
    !/[\\:\u0000-\u001f\u007f]/.test(path) && !path.startsWith('~') &&
    path.split('/').every(part => part && part !== '.' && part !== '..'));
}
function pathOrder(a, b) {
  const left = Array.from(a), right = Array.from(b);
  for (let i = 0; i < Math.min(left.length, right.length); i++) {
    const delta = left[i].codePointAt(0) - right[i].codePointAt(0);
    if (delta) return delta;
  }
  return left.length - right.length;
}
async function digest(bytes) {
  check(globalThis.crypto?.subtle);
  const hash = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('');
}

export function validateEvolutionSummary(run) {
  if (!run.skill_evolution && run.evolution_skills === undefined) return;
  check(run.skill_evolution === `${run.run_id}/skill-evolution.json` &&
    Array.isArray(run.evolution_skills) && run.evolution_skills.length > 0);
  const keys = new Set();
  for (const item of run.evolution_skills) {
    exact(item, 'skill_key display_name base_version_id candidate_version_id');
    check(keyPattern.test(item.skill_key) && !keys.has(item.skill_key));
    check(item.display_name === null || (typeof item.display_name === 'string' && item.display_name.length <= 160));
    for (const arm of ['base', 'candidate']) check(item[`${arm}_version_id`] === null || versionPattern.test(item[`${arm}_version_id`]));
    keys.add(item.skill_key);
  }
}

function validateRef(ref, kind = null) {
  exact(ref, 'kind run_id artifact_sha256 availability');
  check(['baseline', 'candidate', 'comparison'].includes(ref.kind) && (kind === null || ref.kind === kind));
  check(typeof ref.run_id === 'string' && /^\d{8}T\d{6}Z-[a-f0-9]{12}$/.test(ref.run_id));
  check(['verified', 'referenced_only'].includes(ref.availability));
  check(ref.artifact_sha256 === null || (typeof ref.artifact_sha256 === 'string' && digestPattern.test(ref.artifact_sha256)));
  check(ref.availability !== 'verified' || ref.artifact_sha256 !== null);
}

function validateRelationships(rows, report, identities, versions, bindings, associations) {
  const generations = new Map(), comparisons = new Set();
  const originalRun = report.run_id.replace(/^(import-|local-)/, '');
  const common = 'skill_key candidate_ref base_entrypoint_sha256 candidate_entrypoint_sha256 base_version_id candidate_version_id';
  for (const [collection, fields] of [
    ['generations', 'baseline_ref observed_failure_count hypothesis_kind hypothesis_basis'],
    ['comparisons', 'comparison_ref decision status'],
  ]) {
    for (const item of rows[collection]) {
      exact(item, `${common} ${fields}`);
      check(identities.has(item.skill_key));
      validateRef(item.candidate_ref, 'candidate');
      for (const arm of ['base', 'candidate']) {
        const hash = item[`${arm}_entrypoint_sha256`], id = item[`${arm}_version_id`];
        check(typeof hash === 'string' && digestPattern.test(hash));
        if (id !== null) {
          check(associations.has(`${item.skill_key}/${id}`));
          check(versions.get(id)?.inventory.get('SKILL.md').sha256 === hash);
        }
      }
      const key = `${item.skill_key}/${item.candidate_ref.run_id}`;
      let ownRef;
      if (collection === 'generations') {
        check(!generations.has(key) && item.candidate_ref.availability === 'verified');
        generations.set(key, item);
        validateRef(item.baseline_ref, 'baseline');
        check(item.observed_failure_count === null ||
          (Number.isInteger(item.observed_failure_count) && item.observed_failure_count >= 0));
        check(['unknown', 'efficiency', 'quality'].includes(item.hypothesis_kind));
        check(['unavailable', 'operator_reviewed'].includes(item.hypothesis_basis));
        check((item.hypothesis_kind === 'unknown') === (item.hypothesis_basis === 'unavailable'));
        ownRef = item.candidate_ref;
      } else {
        validateRef(item.comparison_ref, 'comparison');
        check(item.comparison_ref.availability === 'verified');
        const comparisonKey = `${item.skill_key}/${item.comparison_ref.run_id}`;
        check(!comparisons.has(comparisonKey));
        comparisons.add(comparisonKey);
        check(Object.hasOwn(decisions, item.decision) && ['blocked', 'completed'].includes(item.status));
        check(item.status !== 'blocked' || item.decision === 'blocked');
        const parent = generations.get(key);
        if (parent) {
          for (const arm of ['base', 'candidate']) {
            check(item[`${arm}_entrypoint_sha256`] === parent[`${arm}_entrypoint_sha256`]);
            if (item[`${arm}_version_id`] !== null && parent[`${arm}_version_id`] !== null) {
              check(item[`${arm}_version_id`] === parent[`${arm}_version_id`]);
            }
          }
          if (item.candidate_ref.artifact_sha256 !== null) {
            check(item.candidate_ref.artifact_sha256 === parent.candidate_ref.artifact_sha256);
          }
        }
        ownRef = item.comparison_ref;
      }
      if (ownRef.run_id === originalRun) {
        check(ownRef.kind === report.purpose);
        const binding = bindings.get(item.skill_key);
        for (const arm of ['base', 'candidate']) check(binding[`${arm}_version_id`] === item[`${arm}_version_id`]);
        if (report.origin === 'historical_import') check(ownRef.artifact_sha256 === report.source_report_sha256);
        if (collection === 'comparisons') check(item.decision === report.execution.decision);
      }
    }
  }
}

export async function validateEvolution(data, report, reportRaw, summary, snapshots) {
  exact(data, 'schema_version project_id run_id report_sha256 records bindings file_contents');
  check(data.schema_version === 1 && data.project_id === report.project_id && data.run_id === report.run_id);
  check(data.report_sha256 === await digest(encoder.encode(reportRaw)));
  const rows = data.records;
  const collections = 'identities sources versions skill_versions generations comparisons adoptions';
  exact(rows, collections);
  for (const name of collections.split(' ')) check(Array.isArray(rows[name]) && rows[name].length <= 4096);
  const identities = new Map(), versions = new Map(), contents = new Map(), associations = new Set();
  for (const item of rows.identities) {
    exact(item, 'skill_key display_name');
    check(keyPattern.test(item.skill_key) && !identities.has(item.skill_key));
    identities.set(item.skill_key, item);
  }
  for (const version of rows.versions) {
    exact(version, 'version_id capture_scope entrypoint files');
    check(versionPattern.test(version.version_id) && !versions.has(version.version_id) &&
      Object.hasOwn(scopes, version.capture_scope) && version.entrypoint === 'SKILL.md');
    check(Array.isArray(version.files) && version.files.length > 0 && version.files.length <= 256);
    const paths = [], inventory = new Map();
    for (const file of version.files) {
      exact(file, 'path sha256 bytes');
      safePath(file.path);
      check(digestPattern.test(file.sha256) && Number.isInteger(file.bytes) && file.bytes >= 0 && file.bytes <= 2097152);
      check(!inventory.has(file.path));
      paths.push(file.path); inventory.set(file.path, file);
    }
    check(inventory.has('SKILL.md') && canonical(paths) === canonical([...paths].sort(pathOrder)));
    check(version.capture_scope !== 'entrypoint_only' || paths.length === 1);
    const payload = { domain: 'skillops.captured-version', schema_version: 1,
      capture_scope: version.capture_scope, entrypoint: version.entrypoint, files: version.files };
    check(version.version_id === `sha256:${await digest(encoder.encode(canonical(payload)))}`);
    versions.set(version.version_id, { ...version, inventory });
  }
  for (const item of rows.skill_versions) {
    exact(item, 'skill_key version_id');
    const pair = `${item.skill_key}/${item.version_id}`;
    check(identities.has(item.skill_key) && versions.has(item.version_id) && !associations.has(pair));
    associations.add(pair);
  }
  check(Array.isArray(data.bindings) && data.bindings.length === identities.size);
  const bindings = new Map();
  for (const item of data.bindings) {
    exact(item, 'skill_key base_version_id candidate_version_id legacy_skill_id');
    check(identities.has(item.skill_key) && !bindings.has(item.skill_key));
    for (const arm of ['base', 'candidate']) {
      const id = item[`${arm}_version_id`];
      check(id === null || associations.has(`${item.skill_key}/${id}`));
    }
    if (report.purpose === 'baseline') check(item.candidate_version_id === null);
    bindings.set(item.skill_key, item);
  }
  const projected = data.bindings.map(({ legacy_skill_id, ...item }) => ({
    ...item, display_name: identities.get(item.skill_key).display_name,
  }));
  check(canonical(projected) === canonical(summary.evolution_skills));
  const legacy = data.bindings.filter(item => item.legacy_skill_id !== null);
  if (snapshots) {
    check(legacy.length === 1 && legacy[0].legacy_skill_id === snapshots.skill_id);
    for (const arm of ['base', 'candidate']) {
      const version = versions.get(legacy[0][`${arm}_version_id`]);
      check((version?.inventory.get('SKILL.md').sha256 ?? null) === (snapshots[arm]?.sha256 ?? null));
    }
  } else check(legacy.length === 0);
  check(Array.isArray(data.file_contents));
  for (const item of data.file_contents) {
    exact(item, 'version_id path encoding data');
    const file = versions.get(item.version_id)?.inventory.get(item.path);
    const key = `${item.version_id}/${item.path}`;
    check(file && !contents.has(key) && item.encoding === 'base64' && typeof item.data === 'string');
    const binary = atob(item.data);
    check(btoa(binary) === item.data);
    const raw = Uint8Array.from(binary, char => char.charCodeAt(0));
    check(raw.length === file.bytes && await digest(raw) === file.sha256);
    contents.set(key, raw);
  }
  check(contents.size === [...versions.values()].reduce((sum, version) => sum + version.files.length, 0));
  validateRelationships(rows, report, identities, versions, bindings, associations);
  for (const source of rows.sources) {
    exact(source, 'skill_key project_id kind scope path observed_at evidence_ref');
    check(identities.has(source.skill_key) && source.project_id === report.project_id);
    check(['workspace', 'run_archive'].includes(source.kind));
    check(Object.hasOwn(sourceScopes, source.scope));
    if (source.path !== null) safePath(source.path);
    if (source.evidence_ref !== null) validateRef(source.evidence_ref);
  }
  for (const observation of rows.adoptions) {
    check(identities.has(observation.skill_key) && observation.project_id === report.project_id);
    check(['unknown', 'entrypoint_pin_observed'].includes(observation.state));
    if (observation.state === 'entrypoint_pin_observed') {
      check(observation.evidence_kind === 'registry_snapshot' && digestPattern.test(observation.registry_sha256) &&
        digestPattern.test(observation.entrypoint_sha256) && observation.observed_at);
    }
  }
  return { data, identities, versions, bindings, contents };
}

export function clearEvolution() {
  $('evolution-metadata').hidden = true;
  $('evolution-metadata').replaceChildren();
}

export function renderEvolution(model, key, history, onSelectRun) {
  const { data, identities, versions, bindings, contents } = model;
  const binding = bindings.get(key);
  check(binding);
  const rows = data.records, identity = identities.get(key);
  const panel = $('evolution-metadata');
  panel.hidden = false;
  panel.replaceChildren(node('h3', identity.display_name || key), node('p', `고정 Skill ID · ${key}`, 'source-hash'));
  const sources = rows.sources.filter(item => item.skill_key === key);
  if (!sources.length || !sources.some(item => item.kind === 'workspace')) panel.append(node('p', '정의 경로 미기록', 'reason'));
  for (const source of sources) {
    panel.append(node('p', `${source.kind === 'run_archive' ? '보관 위치 · 정의 경로 아님' : '정의 경로'}: ${source.path ?? '미기록'}`, 'evolution-path'));
    panel.append(node('p', `출처 범위 · ${sourceScopes[source.scope]} · 관측 시각 ${source.observed_at ? stamp(source.observed_at) : '미기록'}`, 'reason'));
  }
  const captured = ['base', 'candidate'].map(arm => versions.get(binding[`${arm}_version_id`]));
  for (let i = 0; i < captured.length; i++) {
    const version = captured[i];
    panel.append(node('p', `${i ? 'To-Be' : 'As-Is'} · ${version ? scopes[version.capture_scope] : '캡처 미기록'}`, 'reason'));
    if (version) panel.append(node('p', `${version.version_id} · ${version.files.length}개 파일`, 'source-hash'));
  }
  const adoptions = rows.adoptions.filter(item => item.skill_key === key);
  if (!adoptions.length) panel.append(node('p', '채택 상태 미기록', 'reason'));
  for (const observation of adoptions) {
    panel.append(node('p', observation.state === 'unknown' ? '채택 상태 미기록' :
      `설정된 진입점 pin 관측 · ${stamp(observation.observed_at)} · ${observation.entrypoint_sha256}`, 'evolution-path'));
    if (observation.registry_sha256) panel.append(node('p', `관측 근거 registry SHA-256 ${observation.registry_sha256}`, 'source-hash'));
  }
  panel.append(node('p', '캡처와 과거 설정 관측은 현재 설치·실행 또는 전체 번들 배포의 증거가 아닙니다.', 'reason'));
  const files = [...new Set(captured.flatMap(version => version?.files.map(file => file.path) || []))].sort(pathOrder);
  renderSkill(null, false);
  if (files.length) {
    const select = node('select');
    select.id = 'evolution-file';
    const label = node('label', '보관 파일 ');
    label.htmlFor = select.id;
    for (const path of files) { const option = node('option', path); option.value = path; select.append(option); }
    label.append(select); panel.append(label);
    select.value = 'SKILL.md';
    const renderFile = () => {
      const hasCandidate = Boolean(captured[1]) ||
        [...rows.generations, ...rows.comparisons].some(item => item.skill_key === key);
      const detail = { skill_id: identity.display_name || key, file_path: select.value,
        has_candidate_version: hasCandidate };
      for (let i = 0; i < captured.length; i++) {
        const version = captured[i], file = version?.inventory.get(select.value);
        const arm = i ? 'candidate' : 'base';
        if (i && !hasCandidate) { detail[arm] = null; continue; }
        if (!file) {
          detail[arm] = { availability: version?.capture_scope === 'complete_bundle' ? 'absent' : 'uncaptured',
            version: version?.version_id.slice(0, 19), content: null };
          continue;
        }
        let text = null;
        try { text = new TextDecoder('utf-8', { fatal: true }).decode(contents.get(`${version.version_id}/${file.path}`)); }
        catch (error) { if (!(error instanceof TypeError)) throw error; }
        detail[arm] = { ...file, availability: 'captured', content: text, version: version.version_id.slice(0, 19) };
      }
      renderSkill(detail, false);
    };
    select.addEventListener('change', renderFile);
    renderFile();
  }
  const generation = rows.generations.filter(item => item.skill_key === key);
  const comparisons = rows.comparisons.filter(item => item.skill_key === key);
  if (!generation.length && !comparisons.length) return;
  const evidencePanel = $('improvement-evidence');
  evidencePanel.replaceChildren();
  const reference = (ref, label) => {
    check(ref && /^(?:\d{8}T\d{6}Z-[a-f0-9]{12})$/.test(ref.run_id));
    const run = history.find(item => item.run_id.replace(/^(import-|local-)/, '') === ref.run_id && item.purpose === ref.kind);
    const state = ref.availability === 'verified' ? '원본 해시 확인' : '참조만 기록';
    const row = node('p', `${label} · ${state} · `, 'evolution-path');
    if (run) {
      const button = node('button', ref.run_id);
      button.type = 'button'; button.addEventListener('click', () => onSelectRun(run));
      row.append(button);
    } else row.append(node('span', `${ref.run_id} · 공개 이력 미연결`));
    evidencePanel.append(row);
  };
  for (const item of generation) {
    evidencePanel.append(node('p', item.observed_failure_count === null ? '관측 실패 수 미기록' :
      `관측 실패 ${item.observed_failure_count}건`, 'reason'));
    evidencePanel.append(node('p', item.hypothesis_kind === 'unknown' ? '개선 가설 미기록 · 원본 설명에서 추정하지 않음' :
      `검토된 개선 가설 · ${item.hypothesis_kind === 'efficiency' ? '효율' : '품질'}`, 'reason'));
    reference(item.baseline_ref, '개선 근거 기준 평가');
    reference(item.candidate_ref, '후보 생성');
  }
  for (const item of comparisons) {
    check(Object.hasOwn(decisions, item.decision));
    evidencePanel.append(node('p', decisions[item.decision], 'conclusion'));
    reference(item.comparison_ref, '비교 검증');
    reference(item.candidate_ref, '비교의 후보 메타데이터 연결');
  }
  evidencePanel.append(node('p', '후보 생성, 비교 결론, 프로젝트 채택은 별개입니다. 지표·임계값은 아래 저장된 실행 평가에서 확인합니다.', 'reason'));
}
