import { authFetch } from '../../utils/api';

/** Engines the Ask AI page can run on (values are what /api/ai/* expects). */
export const ENGINES = [
  { id: 'SNOWFLAKE', label: 'Snowflake Cortex' },
  { id: 'DATABRICKS', label: 'Databricks Genie' },
  { id: 'LOCAL', label: 'Local' },
];

async function readError(res, fallback) {
  try {
    const data = await res.json();
    const detail = data?.detail ?? data?.error ?? data?.message;
    if (typeof detail === 'string') return detail;
    if (detail) return JSON.stringify(detail);
  } catch {
    /* body was not JSON */
  }
  return `${fallback} (HTTP ${res.status})`;
}

function postJson(url, body) {
  return authFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/**
 * Compile a question into governed SQL, then run it. Throws an Error with a
 * `stage` ('compile' | 'run') so the UI can say which step failed.
 */
export async function askQuestion({ question, target, projectId, allowOfflineDraft = false }) {
  let plan;
  try {
    const res = await postJson('/api/ai/nl-query', {
      prompt: question,
      dialect: target,
      target,
      project_id: projectId || undefined,
      allow_offline_draft: Boolean(allowOfflineDraft),
    });
    if (!res.ok) throw new Error(await readError(res, 'Could not interpret the question'));
    plan = await res.json();
  } catch (err) {
    err.stage = 'compile';
    throw err;
  }

  if (!plan?.generated_sql) {
    const reason = (plan?.warnings || []).find(Boolean) || plan?.explanation;
    const isCortexUnavailable = /cortex unavailable/i.test(`${reason || ''} ${plan?.explanation || ''}`);
    const err = new Error(reason || 'No SQL was generated for this question.');
    err.stage = 'compile';
    err.plan = plan;
    err.cortexUnavailable = isCortexUnavailable;
    throw err;
  }

  let result;
  try {
    const res = await postJson('/api/ai/execute-query', {
      sql: plan.generated_sql,
      target,
      project_id: projectId || undefined,
      question,
      limit: 25,
    });
    if (!res.ok) throw new Error(await readError(res, 'Query execution failed'));
    result = await res.json();
    if (result && result.success === false) throw new Error(result.error || result.message || 'Query execution failed');
  } catch (err) {
    err.stage = 'run';
    err.plan = plan;
    throw err;
  }

  return { plan, result };
}

/** Save the question + SQL as a verified query after real execution and user confirmation. */
export async function certifyAnswer({ question, sql, target, projectId }) {
  const res = await postJson('/api/ai/save-verified-query', {
    question,
    sql,
    target,
    project_id: projectId || undefined,
    confirmed: true,
  });
  if (!res.ok) throw new Error(await readError(res, 'Could not certify this answer'));
  return res.json();
}

export async function fetchVerifiedQueries(projectId) {
  if (!projectId) return [];
  try {
    const res = await authFetch(`/api/ai/verified-queries?project_id=${encodeURIComponent(projectId)}`);
    if (!res.ok) return [];
    const data = await res.json();
    return Array.isArray(data?.queries) ? data.queries : [];
  } catch {
    return [];
  }
}

export async function fetchProjects() {
  const res = await authFetch('/api/projects?limit=50');
  if (!res.ok) return [];
  const data = await res.json();
  return Array.isArray(data) ? data : data.projects || [];
}

/**
 * Build sample questions dynamically from the selected project's domain ontology
 * (or all loaded domains when no project is chosen). Never hardcodes model terms.
 */
export async function fetchDynamicSuggestedQuestions(projectId) {
  try {
    let targetDomainNames = [];
    if (projectId) {
      const pRes = await authFetch(`/api/ontology/projects/${encodeURIComponent(projectId)}/domains`);
      if (pRes.ok) {
        const pData = await pRes.json();
        targetDomainNames = (pData?.domains || []).map((d) => d.domain || d.id).filter(Boolean);
      }
    }
    if (!targetDomainNames.length) {
      const dRes = await authFetch('/api/ontology/domains');
      if (!dRes.ok) return [];
      const dData = await dRes.json();
      const allDoms = dData?.domains || [];
      const matching = projectId
        ? allDoms.filter((d) => d.source_project_id === projectId)
        : allDoms;
      targetDomainNames = (matching.length ? matching : allDoms).slice(0, 3).map((d) => d.domain);
    }

    const suggestions = [];
    for (const domName of targetDomainNames.slice(0, 2)) {
      const detRes = await authFetch(`/api/ontology/domains/${encodeURIComponent(domName)}`);
      if (!detRes.ok) continue;
      const det = await detRes.json();
      const parsed = det?.parsed || {};
      const metrics = (parsed.metrics || []).filter((m) => !m.stale && !m.is_hidden);
      const concepts = parsed.concepts || [];
      const textAttrs = concepts
        .flatMap((c) => c.attributes || [])
        .filter((a) => !a.is_hidden && !/INT|DEC|FLOAT|NUM/i.test(a.data_type || ''))
        .map((a) => a.label || a.name);

      for (const m of metrics.slice(0, 3)) {
        const mLabel = m.label || m.name;
        const dim = textAttrs[suggestions.length % Math.max(1, textAttrs.length)];
        suggestions.push(dim ? `What is ${mLabel} by ${dim}?` : `What is ${mLabel}?`);
        if (suggestions.length >= 4) break;
      }
      if (suggestions.length >= 4) break;
    }
    return [...new Set(suggestions)].slice(0, 4);
  } catch {
    return [];
  }
}

/** Parse "$145,000", "42.5%", 120 → number; returns null when not numeric. */
export function toNumber(value) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value !== 'string') return null;
  const cleaned = value.trim().replace(/[$€£,%\s]/g, '');
  if (!cleaned || !/^-?\d+(\.\d+)?$/.test(cleaned)) return null;
  return Number(cleaned);
}

const humanize = (name) =>
  String(name)
    .replace(/[_.]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase());

/** Short answer title from the resolution plan. */
export function answerTitle(plan, question) {
  const metrics = plan?.detected_metrics || [];
  if (!metrics.length) return question;
  let title = metrics.slice(0, 2).map(humanize).join(' and ');
  if (metrics.length > 2) title += ` +${metrics.length - 2}`;
  const dims = plan?.detected_dimensions || [];
  if (dims.length) title += ` by ${dims.slice(0, 2).map(humanize).join(', ')}`;
  const year = (plan?.filters || []).join(' ').match(/\b(20\d\d)\b/);
  if (year) title += `, ${year[1]}`;
  return title;
}
