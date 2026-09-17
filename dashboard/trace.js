import { $, node, table, renderMetric } from './views.js';
import { check, exact, canonical, safePath, digest } from './evolution.js';
import { quality, observation, regression, exceedsEfficiencyLimit } from './assessments.js';

const id = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const runId = /^(?:[0-9]+-[0-9]+|(?:import-|local-|sample-)?[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12})$/;
const skillId = /^[a-z0-9][a-z0-9-]{0,63}:[a-z0-9][a-z0-9-]{0,63}$/;
const hash = /^[a-f0-9]{64}$/;
const version = /^sha256:[a-f0-9]{64}$/;
// Contract c194ea8: H({"policy": candidates.POLICY, "rule": "replay-v1"}).
const replayPolicyHash = 'd6efd14169aab7830b99a03d28f85282aeb04644d54df4d876fb02a3a1d7b212';
const modes = ['live', 'offline_test', 'sample'];
const stops = ['improved', 'max_rounds', 'no_change', 'call_limit', 'time_limit', 'credit_limit',
  'input_changed', 'evaluation_unverified', 'runtime_error', 'cancelled'];
const states = { improved: '지침 품질 개선 판정', not_improved: '개선 미확인', rejected: '후보 거절', unverified: '검증 불충분' };
const files = { replay_evaluation: 'replay-evaluation.json', cycle: 'cycle.json', adoption: 'adoption.json' };
const cycleFields = 'cycle_id skill_key source_path input_sha256 reference_sha256 original_version_id ' +
  'max_rounds budget rounds stop_reason selected_candidate_version_id confirmation_ref confirmation_status';
const encoder = new TextEncoder();
const numeric = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
const integer = (value, low, high) => check(Number.isSafeInteger(value) && value >= low && value <= high);
const matches = (pattern, value) => typeof value === 'string' && pattern.test(value);
const same = (left, right) => check(canonical(left) === canonical(right));
const text = (value, limit = 4096) => check(typeof value === 'string' && value.length > 0 &&
  encoder.encode(value).length <= limit && !/[\u0000-\u0008\u000b-\u001f\u007f]/.test(value));
const timestamp = value => check(typeof value === 'string' &&
  /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(value) && Number.isFinite(Date.parse(value)) &&
  new Date(value).toISOString().slice(0, 19) === value.slice(0, 19));
function unique(rows, validate, key = value => value) {
  check(Array.isArray(rows));
  const seen = new Set();
  for (const row of rows) { validate(row); check(!seen.has(key(row))); seen.add(key(row)); }
}
function identity(data) {
  check(matches(id, data.project_id) && matches(skillId, data.skill_key));
  safePath(data.source_path);
}

// Preserve number tokens (including Python's 1.0) when recomputing H from public bytes.
export function parsePublic(raw) {
  check(encoder.encode(raw).length <= 1048576);
  const tokens = [], pattern = /\s+|"(?:\\.|[^"\\])*"|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null|[{}[\],:]/gy;
  let position = 0, match;
  while (position < raw.length) {
    pattern.lastIndex = position;
    match = pattern.exec(raw); check(match);
    position = pattern.lastIndex;
    if (!/^\s/.test(match[0])) tokens.push(match[0]);
  }
  let cursor = 0;
  const numbers = new WeakMap();
  function read(depth = 0) {
    check(depth <= 64);
    const token = tokens[cursor++];
    if (token === '{' || token === '[') {
      const array = token === '[', value = array ? [] : {}, end = array ? ']' : '}';
      const lexical = new Map(); numbers.set(value, lexical);
      while (tokens[cursor] !== end) {
        let key = value.length;
        if (!array) {
          check(tokens[cursor]?.startsWith('"'));
          key = JSON.parse(tokens[cursor++]);
          check(!Object.hasOwn(value, key) && tokens[cursor++] === ':');
        }
        const numberToken = tokens[cursor], child = read(depth + 1);
        if (['schema_version', 'round_number', 'max_rounds', 'max_invocations', 'max_seconds', 'score'].includes(key) && child !== null) {
          check(/^-?\d+$/.test(numberToken) && Number.isSafeInteger(child));
        }
        Object.defineProperty(value, key, { value: child, enumerable: true, configurable: true, writable: true });
        if (typeof child === 'number') lexical.set(String(key), numberToken);
        if (tokens[cursor] === end) break;
        check(tokens[cursor++] === ',' && tokens[cursor] !== end);
      }
      check(tokens[cursor++] === end); return value;
    }
    check(token !== undefined);
    const value = JSON.parse(token);
    check(value === null || ['string', 'boolean'].includes(typeof value) || numeric(Math.abs(value)));
    return value;
  }
  const value = read(); check(cursor === tokens.length && value && !Array.isArray(value));
  const quote = value => JSON.stringify(value).replace(/[\u007f-\uffff]/g,
    char => `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`);
  function encode(value, depth = 0, omit = null) {
    if (typeof value === 'string') return quote(value);
    if (!value || typeof value !== 'object') return JSON.stringify(value);
    const array = Array.isArray(value), keys = Object.keys(value).filter(key => key !== omit);
    if (!array) keys.sort();
    const parts = keys.map(key => `${'  '.repeat(depth + 1)}${array ? '' : quote(key) + ': '}${
      numbers.get(value)?.get(key) ?? encode(value[key], depth + 1)}`);
    return (array ? '[' : '{') + (parts.length ? '\n' + parts.join(',\n') + '\n' + '  '.repeat(depth) : '') + (array ? ']' : '}');
  }
  check(raw === encode(value) + '\n');
  return { value, raw, hashObject: (object, omit) => digest(encoder.encode(encode(object, 0, omit) + '\n')) };
}

export function validateTraceSummary(run) {
  for (const [key, file] of Object.entries(files)) {
    if (Object.hasOwn(run, key)) check(run[key] === `${run.run_id}/${file}`);
  }
}
async function wrapper(parsed, bundle, fields) {
  const data = parsed.value, report = bundle.report;
  exact(data, `schema_version project_id run_id report_sha256 execution_mode ${fields}`);
  check(data.schema_version === 1 && data.project_id === report.project_id && data.run_id === report.run_id &&
    modes.includes(data.execution_mode) && data.report_sha256 === await digest(encoder.encode(bundle.rawReport)));
  check(report.origin !== 'sample' || data.execution_mode === 'sample');
}
export async function loadTraceSkills(project, run, report, read) {
  const skills = new Map();
  for (const type of ['cycle', 'adoption']) {
    if (!run[type]) continue;
    if (type === 'cycle' && run.skill_evolution) continue;
    const parsed = parsePublic((await read(`/results/${project.id}/${run[type]}`, true)).raw);
    await wrapper(parsed, { report: report.value, rawReport: report.raw }, type === 'cycle' ? cycleFields : 'approvals executions');
    const data = parsed.value;
    if (type === 'adoption') check(Array.isArray(data.approvals) && Array.isArray(data.executions));
    for (const row of type === 'cycle' ? [data] : [...data.approvals, ...data.executions]) {
      check(matches(skillId, row.skill_key) && row.project_id === project.id);
      skills.set(row.skill_key, { skill_key: row.skill_key, display_name: row.skill_key,
        base_version_id: null, candidate_version_id: null });
    }
  }
  return [...skills.values()];
}
function validateDecision(value, checks) {
  exact(value, 'policy_id status reasons regression');
  check(value.policy_id === 'replay-v1' && Object.hasOwn(states, value.status));
  unique(value.reasons, value => text(value, 1024));
  check(value.reasons.length <= 128);
  same(value.regression, regression(checks));
}
function application(value) {
  if (value === null) return;
  exact(value, 'version_id staged_version_id work_sha256 output_sha256 activated changed task_outcome' +
    (Object.hasOwn(value, 'measurement') ? ' measurement' : ''));
  check(matches(version, value.version_id) && matches(version, value.staged_version_id) &&
    matches(hash, value.work_sha256) && matches(hash, value.output_sha256) &&
    typeof value.activated === 'boolean' && typeof value.changed === 'boolean' &&
    ['satisfied', 'not_satisfied', 'unverified'].includes(value.task_outcome));
  if (Object.hasOwn(value, 'measurement')) {
    exact(value.measurement, 'cost_nano_aiu elapsed_seconds');
    Object.values(value.measurement).forEach(value => check(value === null || numeric(value)));
  }
}
function taskState(row) {
  let missing = false, failed = false;
  for (const [collection, key] of [['cases', 'required_case_ids'], ['gates', 'required_gate_ids']]) {
    const observed = new Map((row.checks.candidate?.[collection] || []).map(item => [item.id, item.status]));
    for (const id of row.work.checks[key]) {
      const status = observed.get(id);
      failed ||= ['failed', 'error'].includes(status);
      missing ||= status !== 'passed';
    }
  }
  return failed ? 'failed' : missing ? 'unverified' : 'passed';
}
function decisionStatus(row) {
  let rejected = row.decision.regression.status === 'rejected', incomplete = row.errors.length > 0 ||
    row.decision.regression.status !== 'passed', improved = false;
  const { base, candidate } = row.quality;
  if (!base || !candidate || [base, candidate].some(value => value.status !== 'completed') ||
    base.rubric_sha256 !== candidate.rubric_sha256 || base.context_sha256 !== candidate.context_sha256) incomplete = true;
  else {
    const before = new Map(base.dimensions.map(item => [item.id, item.score]));
    const after = new Map(candidate.dimensions.map(item => [item.id, item.score]));
    if (!before.size || before.size !== after.size || ![...before.values()].some(value => value !== null) ||
      [...before].some(([id, score]) => !after.has(id) || (score === null) !== (after.get(id) === null))) incomplete = true;
    else for (const [id, score] of before) if (score !== null) {
      rejected ||= after.get(id) < score; improved ||= after.get(id) > score;
    }
    const previous = new Map(base.findings.map(item => [item.id, item.severity]));
    rejected ||= candidate.findings.some(item => item.severity === 'error' || !previous.has(item.id));
    improved ||= base.findings.some(item => !candidate.findings.some(next => next.id === item.id));
  }
  for (const arm of ['base', 'candidate']) {
    const receipt = row.applications[arm];
    incomplete ||= !receipt || !receipt.activated || !receipt.changed ||
      receipt.version_id !== row[`${arm}_version_id`] || receipt.staged_version_id !== row[`${arm}_version_id`] ||
      receipt.work_sha256 !== row.work.input_sha256;
  }
  rejected ||= taskState(row) === 'failed';
  incomplete ||= taskState(row) === 'unverified' || row.applications.candidate?.task_outcome !== 'satisfied';
  for (const metric of ['cost_nano_aiu', 'elapsed_seconds']) {
    const before = row.applications.base?.measurement?.[metric], after = row.applications.candidate?.measurement?.[metric];
    incomplete ||= before == null || after == null || exceedsEfficiencyLimit(before, after);
  }
  return rejected ? 'rejected' : incomplete ? 'unverified' : improved ? 'improved' : 'not_improved';
}
async function validateReplay(parsed, bundle) {
  await wrapper(parsed, bundle, 'reference generation evaluation');
  const { reference: ref, evaluation: row, generation } = parsed.value;
  exact(ref, 'schema_version project_id skill_key source_path source_commit project_tree_sha256 input_sha256 original_version_id ' +
    'rubric_sha256 quality_context_sha256 evaluator_sha256 policy_sha256 plan_sha256 environment_sha256 protected_sha256 ' +
    'original_checks base_quality reference_sha256');
  identity(ref);
  check(ref.schema_version === 1 && matches(/^[a-f0-9]{40}$/, ref.source_commit) && matches(version, ref.original_version_id));
  for (const [key, value] of Object.entries(ref)) if (key.endsWith('_sha256')) check(matches(hash, value));
  check(ref.policy_sha256 === replayPolicyHash);
  check(ref.reference_sha256 === await parsed.hashObject(ref, 'reference_sha256'));
  for (const key of ['project_id', 'source_commit', 'project_tree_sha256', 'evaluator_sha256']) same(ref[key], bundle.report[key]);
  quality(ref.base_quality); observation(ref.original_checks);
  exact(row, 'skill_key source_path base_version_id candidate_version_id work reference_sha256 quality applications checks decision errors');
  const binding = bundle.lifecycle?.bindings.get(row.skill_key);
  check(binding && row.skill_key === ref.skill_key && row.source_path === ref.source_path &&
    row.base_version_id === ref.original_version_id && row.reference_sha256 === ref.reference_sha256);
  for (const arm of ['base', 'candidate']) {
    check(matches(version, row[`${arm}_version_id`]) && binding[`${arm}_version_id`] === row[`${arm}_version_id`] &&
      bundle.lifecycle.versions.get(row[`${arm}_version_id`])?.capture_scope === 'complete_bundle');
  }
  check(row.candidate_version_id !== row.base_version_id);
  check(bundle.lifecycle.data.records.sources.some(source => source.skill_key === row.skill_key &&
    source.project_id === ref.project_id && source.path === ref.source_path));
  exact(row.work, 'task_id input_sha256 split provenance checks');
  check(matches(id, row.work.task_id) && row.work.input_sha256 === ref.input_sha256 &&
    ['development', 'confirmation'].includes(row.work.split) && row.work.provenance === 'recorded');
  exact(row.work.checks, 'plan_sha256 protected_sha256 required_case_ids required_gate_ids');
  for (const key of ['plan_sha256', 'protected_sha256']) same(row.work.checks[key], ref[key]);
  for (const [field, collection] of [['required_case_ids', 'cases'], ['required_gate_ids', 'gates']]) {
    unique(row.work.checks[field], value => text(value, 1024));
    same(row.work.checks[field], [...row.work.checks[field]].sort());
    check(row.work.checks[field].every(id => ref.original_checks?.[collection].some(item => item.id === id)));
  }
  check(row.work.checks.required_case_ids.length > 0);
  same(row.work.checks.required_gate_ids, (ref.original_checks?.gates || []).map(item => item.id).sort());
  exact(row.quality, 'base candidate'); Object.values(row.quality).forEach(quality);
  same(row.quality.base, ref.base_quality);
  for (const value of Object.values(row.quality)) if (value) {
    check(value.rubric_sha256 === ref.rubric_sha256 && value.context_sha256 === ref.quality_context_sha256);
  }
  exact(row.applications, 'base candidate'); Object.values(row.applications).forEach(application);
  exact(row.checks, 'original base candidate'); Object.values(row.checks).forEach(observation);
  same(row.checks.original, ref.original_checks);
  for (const value of Object.values(row.checks)) if (value) {
    for (const key of ['plan_sha256', 'environment_sha256', 'protected_sha256']) same(value[key], ref[key]);
  }
  check(Array.isArray(row.errors) && row.errors.length <= 16);
  for (const error of row.errors) {
    exact(error, 'stage code');
    check(['preparation', 'discovery', 'original_checks', 'base_quality', 'generation', 'candidate_quality',
      'base_application', 'candidate_application', 'work'].includes(error.stage) && matches(/^[a-z0-9_]{1,128}$/, error.code));
  }
  validateDecision(row.decision, row.checks);
  same(row.decision.status, decisionStatus(row));
  if (generation !== null) {
    exact(generation, 'parent_version_id feedback_sha256 addressed_findings hypothesis');
    check(matches(version, generation.parent_version_id) && matches(hash, generation.feedback_sha256) && row.work.split === 'development');
    unique(generation.addressed_findings, value => text(value, 160));
    check(generation.addressed_findings.length <= 128); text(generation.hypothesis);
  } else check(row.work.split === 'confirmation');
  return parsed;
}

export async function loadTrace({ project, run, skill, history, loadBundle, read }) {
  const catalog = new Map(history.map(row => [row.run_id, row]));
  const parsed = new Map(), replays = new Map(), cycles = new Map();
  const getRun = identifier => { const row = catalog.get(identifier); check(row); return row; };
  async function attachment(identifier, type) {
    const key = `${identifier}/${type}`;
    if (!parsed.has(key)) parsed.set(key, (async () => {
      const summary = getRun(identifier); validateTraceSummary(summary);
      check(summary[type] === `${identifier}/${files[type]}`);
      return parsePublic((await read(`/results/${project.id}/${summary[type]}`, true)).raw);
    })());
    return parsed.get(key);
  }
  async function replay(identifier) {
    if (!replays.has(identifier)) replays.set(identifier, (async () => {
      const data = await attachment(identifier, 'replay_evaluation');
      return validateReplay(data, await loadBundle(getRun(identifier)));
    })());
    return replays.get(identifier);
  }
  async function resolve(ref, type) {
    exact(ref, 'project_id run_id path sha256');
    check(ref.project_id === project.id && matches(runId, ref.run_id) && ref.path === files[type] && matches(hash, ref.sha256));
    const data = await (type === 'cycle' ? cycle(ref.run_id) : replay(ref.run_id));
    check(ref.sha256 === await digest(encoder.encode(data.raw)));
    return data;
  }
  async function cycle(identifier) {
    if (!cycles.has(identifier)) cycles.set(identifier, (async () => {
      const parsed = await attachment(identifier, 'cycle'), data = parsed.value;
      const bundle = await loadBundle(getRun(identifier));
      await wrapper(parsed, bundle, cycleFields);
      identity(data);
      check(data.cycle_id === identifier && matches(hash, data.input_sha256) && matches(hash, data.reference_sha256) &&
        matches(version, data.original_version_id) && stops.includes(data.stop_reason));
      integer(data.max_rounds, 1, 10);
      exact(data.budget, 'max_invocations max_seconds max_ai_credits_per_session');
      integer(data.budget.max_invocations, 1, 1000); integer(data.budget.max_seconds, 1, 7200);
      check(data.budget.max_ai_credits_per_session === null ||
        numeric(data.budget.max_ai_credits_per_session) && data.budget.max_ai_credits_per_session >= 30);
      check(Array.isArray(data.rounds) && data.rounds.length <= data.max_rounds);
      const binding = bundle.lifecycle?.bindings.get(data.skill_key);
      if (bundle.lifecycle) check(binding?.base_version_id === data.original_version_id &&
        bundle.lifecycle.versions.get(data.original_version_id)?.capture_scope === 'complete_bundle');
      let parent = data.original_version_id, previous = null, selected = null, development = null;
      const runIds = new Set();
      for (const [index, round] of data.rounds.entries()) {
        exact(round, 'round_id round_number run_id parent_version_id candidate_version_id input_sha256 reference_sha256 ' +
          'feedback_source_round_id feedback_sha256 evaluation_ref decision stop_reason');
        check(round.round_number === index + 1 && round.round_id === `${identifier}-r${index + 1}` &&
          (round.run_id === null || matches(runId, round.run_id) && round.run_id !== identifier && !runIds.has(round.run_id)) &&
          round.parent_version_id === parent && round.input_sha256 === data.input_sha256 &&
          round.reference_sha256 === data.reference_sha256 && matches(hash, round.feedback_sha256) &&
          round.feedback_source_round_id === (index ? data.rounds[index - 1].round_id : null));
        if (round.run_id !== null) runIds.add(round.run_id);
        check(round.candidate_version_id === null || matches(version, round.candidate_version_id));
        check(round.candidate_version_id === null || round.candidate_version_id !== round.parent_version_id);
        check(round.stop_reason === null || stops.includes(round.stop_reason));
        check(index === data.rounds.length - 1 ? round.stop_reason === data.stop_reason : round.stop_reason === null);
        if (round.evaluation_ref !== null) {
          check(round.run_id !== null && round.evaluation_ref.run_id === round.run_id && round.candidate_version_id !== null);
          const evaluated = (await resolve(round.evaluation_ref, 'replay_evaluation')).value;
          const row = evaluated.evaluation, generation = evaluated.generation;
          check(evaluated.execution_mode === data.execution_mode && generation &&
            row.skill_key === data.skill_key && row.source_path === data.source_path &&
            row.base_version_id === data.original_version_id && row.candidate_version_id === round.candidate_version_id &&
            row.work.input_sha256 === data.input_sha256 && row.reference_sha256 === data.reference_sha256 &&
            generation.parent_version_id === parent && generation.feedback_sha256 === round.feedback_sha256);
          same(row.decision, round.decision);
          if (development) same(evaluated.reference, development.reference);
          for (const key of ['source_commit', 'project_tree_sha256', 'evaluator_sha256']) same(evaluated.reference[key], bundle.report[key]);
          const feedbackQuality = previous?.evaluation.quality.candidate || evaluated.reference.base_quality;
          const known = [...(feedbackQuality?.dimensions || []), ...(feedbackQuality?.findings || [])].map(item => item.id);
          check(generation.addressed_findings.every(id => known.includes(id)));
          development ||= evaluated;
          previous = evaluated;
          if (row.decision.status === 'improved') {
            check(selected === null && round.stop_reason === 'improved'); selected = round.candidate_version_id;
          } else if (row.decision.status === 'unverified') check(round.stop_reason === 'evaluation_unverified' ||
            ['call_limit', 'time_limit', 'credit_limit', 'input_changed', 'runtime_error', 'cancelled'].includes(round.stop_reason));
          if (round.stop_reason === null) check(['rejected', 'not_improved'].includes(row.decision.status));
          if (round.stop_reason === 'max_rounds') check(index + 1 === data.max_rounds &&
            ['rejected', 'not_improved'].includes(row.decision.status));
        } else {
          check(round.run_id === null && round.decision === null && round.stop_reason !== null &&
            !['improved', 'max_rounds'].includes(round.stop_reason));
        }
        if (round.candidate_version_id) parent = round.candidate_version_id;
      }
      same(selected, data.selected_candidate_version_id);
      check((data.stop_reason === 'improved') === (selected !== null));
      if (selected && bundle.lifecycle) check(binding.candidate_version_id === selected &&
        bundle.lifecycle.versions.get(selected)?.capture_scope === 'complete_bundle');
      check(['not_run', 'passed', 'failed', 'unverified'].includes(data.confirmation_status));
      if (data.confirmation_ref !== null) {
        check(selected && !runIds.has(data.confirmation_ref.run_id) && data.confirmation_ref.run_id !== identifier);
        const confirmed = (await resolve(data.confirmation_ref, 'replay_evaluation')).value;
        const row = confirmed.evaluation, work = development.evaluation.work;
        check(confirmed.execution_mode === data.execution_mode && confirmed.generation === null &&
          row.skill_key === data.skill_key && row.source_path === data.source_path &&
          row.base_version_id === data.original_version_id && row.candidate_version_id === selected &&
          row.work.split === 'confirmation' && row.work.task_id !== work.task_id && row.work.input_sha256 !== work.input_sha256 &&
          !row.work.checks.required_case_ids.some(id => work.checks.required_case_ids.includes(id)));
        for (const key of ['source_commit', 'project_tree_sha256', 'original_version_id', 'rubric_sha256',
          'quality_context_sha256', 'evaluator_sha256', 'policy_sha256', 'plan_sha256', 'environment_sha256', 'protected_sha256']) {
          same(confirmed.reference[key], development.reference[key]);
        }
        same(data.confirmation_status, row.decision.status === 'rejected' ? 'failed' :
          row.decision.status === 'unverified' ? 'unverified' : 'passed');
      } else check(['not_run', 'unverified'].includes(data.confirmation_status));
      return parsed;
    })());
    return cycles.get(identifier);
  }
  const belongs = row => [...(row.evolution_skills || []), ...(row.trace_skills || [])].some(binding => binding.skill_key === skill);
  const selectedReplay = run.replay_evaluation ? (await replay(run.run_id)).value : null;
  if (selectedReplay) check(selectedReplay.evaluation.skill_key === skill);
  const selectedAdoption = run.adoption ? await attachment(run.run_id, 'adoption') : null;
  const relevant = [];
  for (const summary of history.filter(row => row.cycle && belongs(row))) {
    const parsed = await attachment(summary.run_id, 'cycle'), data = parsed.value;
    if (summary.run_id === run.run_id || data.rounds?.some(row => row.run_id === run.run_id) ||
      data.confirmation_ref?.run_id === run.run_id || selectedAdoption?.value.approvals?.some(row => row.cycle_id === summary.run_id)) {
      relevant.push(await cycle(summary.run_id));
    }
  }
  const approvals = new Map(), executions = new Map(), observations = [];
  for (const summary of history.filter(row => row.adoption && belongs(row))) {
    const parsed = await attachment(summary.run_id, 'adoption'), data = parsed.value;
    await wrapper(parsed, await loadBundle(summary), 'approvals executions');
    unique(data.approvals, row => {
      exact(row, 'approval_id project_id skill_key candidate_version_id cycle_id evidence_sha256 approved_at approved_by trust_scope previous_active_version_id');
      check(matches(id, row.approval_id) && row.project_id === project.id && matches(skillId, row.skill_key) &&
        matches(version, row.candidate_version_id) && matches(runId, row.cycle_id) && matches(hash, row.evidence_sha256) &&
        row.approved_by === 'local_operator' && row.trust_scope === 'local_environment' &&
        (row.previous_active_version_id === null || matches(version, row.previous_active_version_id)));
      timestamp(row.approved_at);
    }, row => row.approval_id);
    unique(data.executions, row => {
      exact(row, 'execution_id run_id project_id skill_key approval_id evidence_sha256 work_input_sha256 approved_version_id ' +
        'loaded_version_id skill_version_verified observed_at status reason_code');
      check(matches(id, row.execution_id) && matches(runId, row.run_id) && row.project_id === project.id &&
        matches(skillId, row.skill_key) && matches(id, row.approval_id) && matches(hash, row.evidence_sha256) &&
        matches(hash, row.work_input_sha256) && matches(version, row.approved_version_id) &&
        (row.loaded_version_id === null || matches(version, row.loaded_version_id)) &&
        typeof row.skill_version_verified === 'boolean' && ['verified', 'failed', 'blocked'].includes(row.status) &&
        (row.reason_code === null || matches(/^[a-z0-9_]{1,128}$/, row.reason_code)));
      timestamp(row.observed_at);
      if (row.status === 'verified') check(row.skill_version_verified &&
        row.loaded_version_id === row.approved_version_id && row.reason_code === null);
      else check(row.reason_code !== null);
    }, row => row.execution_id);
    observations.push({ data, summary });
    for (const approval of data.approvals) {
      const confirmed = await resolve({ project_id: project.id, run_id: approval.cycle_id,
        path: 'cycle.json', sha256: approval.evidence_sha256 }, 'cycle');
      check(confirmed.value.skill_key === approval.skill_key && confirmed.value.confirmation_status === 'passed' &&
        confirmed.value.selected_candidate_version_id === approval.candidate_version_id &&
        confirmed.value.execution_mode === data.execution_mode);
      if (approvals.has(approval.approval_id)) same(approvals.get(approval.approval_id).row, approval);
      else approvals.set(approval.approval_id, { row: approval, mode: data.execution_mode, run: summary.run_id });
    }
  }
  for (const { data, summary } of observations) for (const receipt of data.executions) {
    const approval = approvals.get(receipt.approval_id);
    check(approval && approval.mode === data.execution_mode && receipt.skill_key === approval.row.skill_key &&
      receipt.approved_version_id === approval.row.candidate_version_id && receipt.evidence_sha256 === approval.row.evidence_sha256 &&
      Date.parse(receipt.observed_at) >= Date.parse(approval.row.approved_at));
    const executionBundle = await loadBundle(getRun(receipt.run_id));
    check(executionBundle.report.project_id === receipt.project_id);
    if (executions.has(receipt.execution_id)) same(executions.get(receipt.execution_id).row, receipt);
    else executions.set(receipt.execution_id, { row: receipt, mode: data.execution_mode, run: summary.run_id });
  }
  if (selectedAdoption) {
    for (const receipt of selectedAdoption.value.executions) {
      const approval = approvals.get(receipt.approval_id);
      check(approval);
      if (approval.row.skill_key === skill && !relevant.some(item => item.value.cycle_id === approval.row.cycle_id)) {
        relevant.push(await cycle(approval.row.cycle_id));
      }
    }
  }
  const cycleIds = new Set(relevant.map(item => item.value.cycle_id));
  const matchingApprovals = [...approvals.values()].filter(item => item.row.skill_key === skill && cycleIds.has(item.row.cycle_id));
  const matchingIds = new Set(matchingApprovals.map(item => item.row.approval_id));
  return { replay: selectedReplay, cycles: relevant.map(item => item.value),
    approvals: matchingApprovals, executions: [...executions.values()].filter(item => matchingIds.has(item.row.approval_id)),
    adoptionRecorded: observations.some(item => item.summary.run_id === run.run_id) || matchingApprovals.length > 0,
    mode: selectedReplay?.execution_mode || relevant.find(item => item.value.run_id === run.run_id)?.value.execution_mode ||
      selectedAdoption?.value.execution_mode || null, project: project.id, run: run.run_id, skill };
}

function link(project, run, skill, label) {
  const anchor = node('a', label);
  anchor.href = `/?${new URLSearchParams({ project, run, skill })}`;
  anchor.dataset.traceRun = run;
  return anchor;
}
function paragraph(panel, label, value) { panel.append(node('p', `${label}: ${value ?? '미기록'}`)); }
export function clearTrace() {
  $('evidence-trace').replaceChildren(node('h2', '개발 작업에서 다음 Skill 사용까지'));
}
export function traceError() {
  clearTrace();
  const error = node('p', '근거 누락 또는 검증 실패 · 다른 실행으로 대체하지 않습니다.', 'summary-warning');
  error.id = 'trace-error'; error.setAttribute('role', 'alert'); $('evidence-trace').append(error);
}
export function renderTrace(trace) {
  clearTrace();
  const panel = $('evidence-trace');
  const badge = node('p', trace.mode === 'live' ? 'live · 공개 실행 근거' :
    trace.mode ? `${trace.mode} · 실측 성과·실제 채택 근거 아님` : '새 실행 방식: 미기록', 'reason');
  badge.id = 'trace-mode'; panel.append(badge);
  if (trace.mode && trace.mode !== 'live') {
    $('origin').textContent = `${trace.mode} · 비실측`;
    $('quality-summary').textContent = `${trace.mode} · 테스트/샘플 평가`;
  }
  paragraph(panel, '선택 실행', trace.run);
  if (trace.replay) renderReplay(trace.replay);
  else paragraph(panel, '기록된 개발 작업 replay', '미기록');
  if (!trace.cycles.length) paragraph(panel, '반복 평가', '미기록');
  for (const cycle of trace.cycles) {
    const section = node('section');
    section.append(node('h3', `반복 평가 · ${cycle.cycle_id} · ${cycle.execution_mode}`));
    paragraph(section, '최초 원본', cycle.original_version_id);
    paragraph(section, '승인된 한도', `최대 ${cycle.max_rounds}회 · 호출 ${cycle.budget.max_invocations} · ${cycle.budget.max_seconds}초`);
    paragraph(section, '세션별 Credit soft cap', cycle.budget.max_ai_credits_per_session);
    for (const round of cycle.rounds) {
      const article = node('article', undefined, 'trace-round'); article.dataset.round = round.round_number;
      article.append(node('h3', `라운드 ${round.round_number} · ${round.round_id}`));
      paragraph(article, '부모', round.parent_version_id);
      paragraph(article, '후보', round.candidate_version_id);
      paragraph(article, '피드백 연결', round.feedback_source_round_id ?? '최초 원본 관측');
      paragraph(article, '피드백 SHA-256', round.feedback_sha256);
      paragraph(article, '판정', round.decision ? `${round.decision.status} · ${states[round.decision.status]}` : null);
      paragraph(article, '종료 사유', round.stop_reason ?? '계속');
      if (round.evaluation_ref) article.append(link(trace.project, round.run_id, trace.skill, '이 라운드의 작업·품질·회귀·비용·변경 보기'));
      else paragraph(article, '재평가', '미기록');
      section.append(article);
    }
    paragraph(section, '종료 사유', cycle.stop_reason);
    paragraph(section, 'confirmation', cycle.confirmation_status);
    if (cycle.confirmation_ref) section.append(link(trace.project, cycle.confirmation_ref.run_id, trace.skill, '별도 최종 확인 근거'));
    paragraph(section, '선택 후보', cycle.selected_candidate_version_id);
    if (!trace.approvals.some(item => item.row.cycle_id === cycle.cycle_id)) {
      paragraph(section, '채택 단계', cycle.confirmation_status === 'passed' ?
        '승인 대기 · 이 공개 연결에 승인 기록 미기록' : '최종 확인 미완료 · 승인 가능으로 간주하지 않음');
    }
    panel.append(section);
  }
  paragraph(panel, '승인·사용', trace.adoptionRecorded ? '공개 관측 기록' : '미기록');
  for (const approval of trace.approvals) {
    const section = node('section'), uses = trace.executions.filter(item => item.row.approval_id === approval.row.approval_id)
      .sort((a, b) => Date.parse(a.row.observed_at) - Date.parse(b.row.observed_at));
    section.append(node('h3', `로컬 승인 관측 · ${approval.row.approval_id} · ${approval.mode}`));
    paragraph(section, '승인 시각', approval.row.approved_at);
    paragraph(section, '승인된 버전', approval.row.candidate_version_id);
    if (!uses.length) paragraph(section, '상태', '승인됐지만 사용 미기록');
    for (const use of uses) {
      paragraph(section, '상태', `${use.mode !== 'live' ? '테스트/샘플의 ' : ''}${
        use.row.status === 'verified' ? '검증된 사용' : use.row.status === 'failed' ? '사용 실패' : '사용 차단'}`);
      paragraph(section, '관측 시각', use.row.observed_at);
      paragraph(section, '다음 실행', use.row.run_id);
      paragraph(section, '실제 사용 버전', use.row.loaded_version_id);
      if (use.row.reason_code) paragraph(section, '실패 코드', use.row.reason_code);
      section.append(link(trace.project, use.row.run_id, trace.skill, '다음 실행의 작업 결과 확인'));
    }
    section.append(link(trace.project, approval.run, trace.skill, '승인·사용 공개 근거'));
    panel.append(section);
  }
  panel.append(node('p', '버전 사용 검증은 작업 성공을 의미하지 않습니다. 작업 결과는 해당 실행 보고서에서 별도로 확인합니다.', 'reason'),
    node('p', '현재 로컬 Active: 공개 근거로 확인 불가', 'reason'),
    node('p', 'local_operator / local_environment는 로컬 승인 관측입니다. 인증된 GitHub 신원이나 GitHub 운영 적용 권한이 아닙니다.', 'reason'),
    node('p', '공개 해시·교차 참조만 검증합니다. 비공개 요청·피드백의 진실성이나 현재 로컬 상태를 증명하지 않습니다.', 'reason'));
}
function renderReplay(data) {
  const row = data.evaluation, { base, candidate } = row.quality;
  $('quality-summary').textContent = data.execution_mode === 'live' ? '선택 Skill · 기록된 replay 품질' :
    `${data.execution_mode} · 테스트/샘플 평가`;
  const ids = new Set([...(base?.dimensions || []), ...(candidate?.dimensions || [])].map(item => item.id));
  const score = (value, id) => value?.dimensions.find(item => item.id === id)?.score ?? '미평가';
  $('guide').replaceChildren(table(['지침 품질', '최초 원본 → 후보'],
    [...ids].map(id => [id, `${score(base, id)} → ${score(candidate, id)}`])));
  for (const [label, value] of [['원본', base], ['후보', candidate]]) {
    paragraph($('guide'), label, value?.status);
    for (const finding of value?.findings || []) paragraph($('guide'), finding.severity, finding.message);
  }
  $('improvement-evidence').replaceChildren(node('strong', '관측된 문제 · 공개 품질/검사 근거'));
  for (const finding of base?.findings || []) paragraph($('improvement-evidence'), finding.id, finding.message);
  paragraph($('improvement-evidence'), '개발 작업', `${row.work.task_id} · ${row.work.split} · recorded`);
  paragraph($('improvement-evidence'), '작업 입력 SHA-256', row.work.input_sha256);
  if (data.generation) {
    paragraph($('improvement-evidence'), '피드백 SHA-256', data.generation.feedback_sha256);
    paragraph($('improvement-evidence'), '연결한 항목', data.generation.addressed_findings.join(', ') || '없음');
    paragraph($('improvement-evidence'), '미검증 생성 가설', data.generation.hypothesis);
  } else paragraph($('improvement-evidence'), '최종 확인', '고정된 후보 재평가 · 추가 생성 없음');
  $('execution').replaceChildren(node('h3', '후보 판정 · 채택 아님'),
    node('strong', `${data.execution_mode !== 'live' ? '테스트/샘플 · ' : ''}${states[row.decision.status]}`, 'decision-title'));
  $('execution-scope').textContent = `기록된 작업 ${row.work.task_id} · ${row.work.split} · 원본 프로젝트와 동일한 고정 검사 범위`;
  $('decision-reasons').replaceChildren(node('p', `회귀: ${row.decision.regression.status}`));
  for (const value of [...row.decision.reasons, ...row.decision.regression.reasons,
    ...row.decision.regression.regressions, ...row.errors.map(item => `${item.stage}: ${item.code}`)]) {
    paragraph($('decision-reasons'), '근거', value);
  }
  const tasks = [];
  for (const [collection, label] of [['cases', '검사'], ['gates', '필수 단계']]) {
    const observations = ['original', 'base', 'candidate'].map(arm => new Map((row.checks[arm]?.[collection] || []).map(item => [item.id, item.status])));
    const ids = new Set(observations.flatMap(map => [...map.keys()]));
    for (const id of ids) tasks.push([`${label}: ${id}`, ...observations.map(map => map.get(id) ?? '미관측')]);
  }
  $('task-results').replaceChildren(node('p', `실제 작업 결과(고정된 필수 검사): ${taskState(row)}`),
    table(['검사', '원본 프로젝트', '기존 적용', '후보 적용'], tasks));
  paragraph($('task-results'), '필수 작업 검사', row.work.checks.required_case_ids.join(', '));
  const metrics = {};
  for (const arm of ['base', 'candidate']) {
    for (const name of ['cost_nano_aiu', 'elapsed_seconds']) metrics[`${arm}_${name}`] = row.applications[arm]?.measurement?.[name] ?? null;
  }
  renderMetric($('cost-card'), 'Skill 적용 비용 · 기존 → 후보', 'cost_nano_aiu', metrics, data.execution_mode === 'live' ? 'github_actions' : 'sample');
  renderMetric($('time-card'), 'Skill 적용 시간 · 기존 → 후보', 'elapsed_seconds', metrics, data.execution_mode === 'live' ? 'github_actions' : 'sample');
}
