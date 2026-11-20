import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { AlertTriangle, ArrowUp, Check, Copy, ExternalLink, Loader2, Play, Rocket, Sparkles, Wand2, X } from 'lucide-react';
import AnswerCard from '../components/ask/AnswerCard';
import AskRail from '../components/ask/AskRail';
import {
  askQuestion,
  certifyAnswer,
  ENGINES,
  fetchDynamicSuggestedQuestions,
  fetchProjects,
  fetchVerifiedQueries,
} from '../components/ask/askApi';
import { BTN_PRIMARY } from '../components/ask/askStyles';
import { api, authFetch } from '../utils/api';

/**
 * AskAiPage (/ask) — ask a business question in plain language and get an
 * answer computed from the governed ontology (Snowflake Cortex, Databricks
 * Genie or local engine). One "Ask" compiles the question to governed SQL
 * (/api/ai/nl-query) and runs it (/api/ai/execute-query); the matched
 * metrics, applied policies and SQL sit behind "How this was answered".
 * /ask?q=… prefills the question and asks it once.
 */

const THREAD_KEY = 'semabridge.ask.thread';
const CERTIFIED_KEY = 'semabridge.ask.certified';
const TARGET_KEY = 'semabridge.ask.target';

function readStore(storage, key, fallback) {
  try {
    const raw = storage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

function writeStore(storage, key, value) {
  try {
    storage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable (private mode, quota) — state still lives in memory */
  }
}

function useNarrow(query = '(max-width: 900px)') {
  const [narrow, setNarrow] = useState(() => typeof window !== 'undefined' && window.matchMedia?.(query).matches);
  useEffect(() => {
    const mq = window.matchMedia?.(query);
    if (!mq) return undefined;
    const onChange = (e) => setNarrow(e.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, [query]);
  return narrow;
}

let nextId = Date.now();

function resolveProjectTarget(project) {
  if (!project || typeof project !== 'object') return null;
  const raw =
    project.target_type ??
    project.target?.type ??
    (Array.isArray(project.targets) && project.targets[0]?.type) ??
    null;
  if (!raw) return null;
  const norm = String(raw).trim().toLowerCase();
  if (norm.includes('databricks')) return 'DATABRICKS';
  if (norm.includes('snowflake')) return 'SNOWFLAKE';
  return null;
}

function engineModeSubtitle(target) {
  if (target === 'DATABRICKS') return 'Databricks Metric View / SQL Warehouse';
  if (target === 'LOCAL') return 'Local offline draft engine';
  return 'Snowflake Cortex Analyst';
}

export default function AskAiPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const urlQuestion = searchParams.get('q') || '';
  const projectParam = searchParams.get('project') || '';

  const [target, setTarget] = useState(() => {
    const saved = readStore(window.localStorage, TARGET_KEY, 'SNOWFLAKE');
    return ENGINES.some((e) => e.id === saved) ? saved : 'SNOWFLAKE';
  });
  const [projectId, setProjectId] = useState(projectParam);
  const [projects, setProjects] = useState([]);
  const [suggestedQuestions, setSuggestedQuestions] = useState([]);
  const [draft, setDraft] = useState(urlQuestion);
  // Entries that were mid-flight when the page was left come back as errors.
  const [thread, setThread] = useState(() =>
    readStore(window.sessionStorage, THREAD_KEY, []).map((e) =>
      e.status === 'loading' ? { ...e, status: 'error', errorStage: 'run', error: 'Interrupted before it finished.' } : e,
    ),
  );
  const [certified, setCertified] = useState(() => readStore(window.sessionStorage, CERTIFIED_KEY, []));

  // Databricks Genie Space preview / deploy / eval modal state
  const [genieOpen, setGenieOpen] = useState(false);
  const [genieLoading, setGenieLoading] = useState(false);
  const [genieDeploying, setGenieDeploying] = useState(false);
  const [genieData, setGenieData] = useState(null);
  const [genieError, setGenieError] = useState('');
  const [genieCopied, setGenieCopied] = useState(false);
  const [genieEvaluating, setGenieEvaluating] = useState(false);
  const [genieEval, setGenieEval] = useState(null);
  const [genieEvalError, setGenieEvalError] = useState('');

  const threadEndRef = useRef(null);
  const autoAskedRef = useRef('');
  const narrow = useNarrow();
  const busy = thread.some((e) => e.status === 'loading');
  const isDatabricks = target === 'DATABRICKS';

  useEffect(() => writeStore(window.sessionStorage, THREAD_KEY, thread), [thread]);
  useEffect(() => writeStore(window.sessionStorage, CERTIFIED_KEY, certified), [certified]);
  useEffect(() => writeStore(window.localStorage, TARGET_KEY, target), [target]);

  // Automatically align Ask AI target/dialect with the selected project's target_type
  useEffect(() => {
    if (!projectId || !projects.length) return;
    const selected = projects.find((p) => (p.id || p.name) === projectId);
    const detected = resolveProjectTarget(selected);
    if (detected) {
      setTarget(detected);
    }
  }, [projectId, projects]);

  useEffect(() => {
    let alive = true;
    fetchProjects()
      .then((list) => {
        if (!alive) return;
        setProjects(list);
        if (!projectParam && list.length > 0) {
          const preferred =
            list.find((p) => String(p.status || '').toLowerCase() === 'active') || list[0];
          const firstId = preferred?.id || preferred?.name || '';
          if (firstId) {
            setProjectId((cur) => cur || firstId);
          }
        }
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [projectParam]);

  useEffect(() => {
    let alive = true;
    fetchDynamicSuggestedQuestions(projectId)
      .then((list) => alive && setSuggestedQuestions(list))
      .catch(() => alive && setSuggestedQuestions([]));
    if (projectId) {
      fetchVerifiedQueries(projectId)
        .then((list) => alive && setCertified(list))
        .catch(() => {});
    }
    return () => {
      alive = false;
    };
  }, [projectId]);

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [thread.length, thread[thread.length - 1]?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const patch = (id, changes) => setThread((t) => t.map((e) => (e.id === id ? { ...e, ...changes } : e)));

  const runEntry = useCallback(async (id, question, engine, project, allowOfflineDraft = false, semanticView = null) => {
    try {
      const { plan, result } = await askQuestion({
        question,
        target: engine,
        projectId: project,
        allowOfflineDraft,
        semanticView,
      });
      patch(id, { status: 'done', plan, result, cortexUnavailable: false, candidateViews: [], semanticView });
    } catch (err) {
      patch(id, {
        status: 'error',
        error: err?.message || String(err),
        errorStage: err?.stage || 'compile',
        cortexUnavailable: Boolean(err?.cortexUnavailable),
        candidateViews: err?.candidateViews || [],
        semanticView,
        plan: err?.plan || null,
      });
    }
  }, []);

  const ask = useCallback(
    (text) => {
      const question = String(text ?? '').trim();
      if (!question) return;
      const id = `q${nextId++}`;
      setThread((t) => [...t, { id, question, target, projectId, status: 'loading' }]);
      setDraft('');
      runEntry(id, question, target, projectId);
    },
    [target, projectId, runEntry],
  );

  // /ask?q=… → prefill and ask once, then drop q so a reload does not re-ask.
  useEffect(() => {
    if (!urlQuestion || autoAskedRef.current === urlQuestion) return;
    autoAskedRef.current = urlQuestion;
    ask(urlQuestion);
    const next = new URLSearchParams(searchParams);
    next.delete('q');
    setSearchParams(next, { replace: true });
  }, [urlQuestion]); // eslint-disable-line react-hooks/exhaustive-deps

  const retry = (entry, allowOfflineDraft = false, semanticView = entry.semanticView || null) => {
    patch(entry.id, { projectId, target, status: 'loading', error: null, plan: null, result: null, cortexUnavailable: false, candidateViews: [] });
    runEntry(entry.id, entry.question, target, projectId, allowOfflineDraft, semanticView);
  };

  const certify = async (entry) => {
    patch(entry.id, { certifyState: 'saving', certifyError: null });
    try {
      await certifyAnswer({
        question: entry.question,
        sql: entry.plan?.generated_sql,
        target: entry.target,
        projectId: entry.projectId,
        viewName: entry.plan?.answered_by_view || entry.semanticView || null,
      });
      patch(entry.id, { certifyState: 'done' });
      setCertified((c) => [{ question: entry.question, target: entry.target }, ...c.filter((x) => x.question !== entry.question)]);
    } catch (err) {
      patch(entry.id, { certifyState: null, certifyError: err?.message || 'Could not certify this answer' });
    }
  };

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      if (!busy) ask(draft);
    }
  };

  const openGeniePreview = async () => {
    if (!projectId) return;
    setGenieOpen(true);
    setGenieLoading(true);
    setGenieError('');
    setGenieCopied(false);
    setGenieEval(null);
    setGenieEvalError('');
    try {
      const res = await api.generateDatabricksGenieSpace(projectId, { deploy: false });
      setGenieData(res);
    } catch (err) {
      setGenieError(err?.message || 'Failed to generate Genie Space preview.');
      setGenieData(null);
    } finally {
      setGenieLoading(false);
    }
  };

  const deployGenieSpace = async ({ forceOverwrite = false, spaceId = null } = {}) => {
    if (!projectId) return;
    setGenieDeploying(true);
    setGenieError('');
    setGenieEval(null);
    setGenieEvalError('');
    try {
      const targetSpaceId = spaceId || genieData?.space_id || null;
      let res;
      if (targetSpaceId || forceOverwrite) {
        const apiBase = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '');
        const rawRes = await authFetch(`${apiBase}/ai/databricks/genie-space`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            project_id: projectId,
            space_title: genieData?.spec?.title || undefined,
            deploy: true,
            space_id: targetSpaceId || undefined,
            force_overwrite: Boolean(forceOverwrite),
          }),
        });
        if (!rawRes.ok) {
          const text = await rawRes.text();
          throw new Error(text || `Failed to deploy Genie Space (HTTP ${rawRes.status})`);
        }
        res = await rawRes.json();
      } else {
        res = await api.generateDatabricksGenieSpace(projectId, {
          spaceTitle: genieData?.spec?.title || null,
          deploy: true,
        });
      }
      setGenieData(res);
    } catch (err) {
      setGenieError(err?.message || 'Failed to deploy Genie Space to Databricks.');
    } finally {
      setGenieDeploying(false);
    }
  };

  const runGenieEvaluation = async () => {
    if (!projectId || !genieData?.space_id) return;
    setGenieEvaluating(true);
    setGenieEvalError('');
    try {
      const res = await api.evaluateDatabricksGenieSpace(projectId, genieData.space_id);
      setGenieEval(res);
    } catch (err) {
      setGenieEvalError(err?.message || 'Failed to run Genie Space benchmark evaluation.');
      setGenieEval(null);
    } finally {
      setGenieEvaluating(false);
    }
  };

  const copyGenieJson = async () => {
    if (!genieData?.spec) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(genieData.spec, null, 2));
      setGenieCopied(true);
      setTimeout(() => setGenieCopied(false), 1800);
    } catch {
      /* clipboard unavailable */
    }
  };

  return (
    <div style={{ height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column', background: 'var(--bg-app)', color: 'var(--text-primary)' }}>
      <style>{`
        .ask-rail-item:hover:not(:disabled){background:var(--bg-surface-hover);color:var(--text-primary);border-color:var(--border-main)}
        .ask-rail-item:disabled{opacity:.55;cursor:default}
        .ask-composer-input,.ask-composer-input:focus,.ask-composer-input:hover{border:none!important;box-shadow:none!important;outline:none!important;background:transparent!important}
        .ask-seg button:focus-visible,.ask-rail-item:focus-visible{outline:2px solid var(--border-focus);outline-offset:1px}
      `}</style>

      {/* Header */}
      <div style={{ padding: '18px 24px 14px', borderBottom: '1px solid var(--border-main)', display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '10px 14px' }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, margin: 0 }}>Ask AI</h1>
        <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
          {engineModeSubtitle(target)} · Answers use your governed metrics and access rules
        </span>
        <span style={{ flex: 1 }} />
        {projects.length > 0 && (
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text-tertiary)' }}>
            Scope
            <select
              value={projectId}
              onChange={(e) => {
                const nextId = e.target.value;
                setProjectId(nextId);
                const selected = projects.find((p) => (p.id || p.name) === nextId);
                const detected = resolveProjectTarget(selected);
                if (detected) setTarget(detected);
              }}
              style={{ padding: '5px 8px', borderRadius: 8, border: '1px solid var(--border-main)', background: 'var(--bg-input)', color: 'var(--text-primary)', fontSize: 12, maxWidth: 200 }}
            >
              <option value="">All projects</option>
              {projects.map((p) => (
                <option key={p.id || p.name} value={p.id || p.name}>{p.name || p.id}</option>
              ))}
            </select>
          </label>
        )}
        <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Run on</span>
        <div className="ask-seg" role="radiogroup" aria-label="Run on" style={{ display: 'inline-flex', padding: 2, borderRadius: 8, border: '1px solid var(--border-main)', background: 'var(--bg-surface)' }}>
          {ENGINES.map((e) => {
            const on = e.id === target;
            const label =
              e.id === 'DATABRICKS'
                ? 'Databricks Metric View'
                : e.label;
            return (
              <button
                key={e.id}
                type="button"
                role="radio"
                aria-checked={on}
                onClick={() => setTarget(e.id)}
                title={engineModeSubtitle(e.id)}
                style={{
                  padding: '4px 10px',
                  borderRadius: 6,
                  border: 'none',
                  fontSize: 12,
                  fontWeight: on ? 600 : 500,
                  cursor: 'pointer',
                  background: on ? 'var(--bg-surface-raised)' : 'transparent',
                  color: on ? 'var(--text-primary)' : 'var(--text-secondary)',
                  boxShadow: on ? '0 0 0 1px var(--border-main)' : 'none',
                }}
              >
                {label}
              </button>
            );
          })}
        </div>
        {isDatabricks && (
          <button
            type="button"
            onClick={openGeniePreview}
            disabled={!projectId || genieLoading}
            title={projectId ? 'Preview and optionally deploy a Databricks AI/BI Genie Space for this project' : 'Select a Databricks project in Scope first'}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '5px 10px',
              borderRadius: 8,
              border: '1px solid var(--border-focus)',
              background: 'var(--color-accent-faint)',
              color: 'var(--text-primary)',
              fontSize: 12,
              fontWeight: 600,
              cursor: !projectId || genieLoading ? 'not-allowed' : 'pointer',
              opacity: !projectId || genieLoading ? 0.55 : 1,
            }}
          >
            <Wand2 size={13} />
            Generate Genie Space
          </button>
        )}
        {thread.length > 0 && (
          <button
            type="button"
            onClick={() => setThread([])}
            disabled={busy}
            title="Clear conversation"
            style={{
              padding: '5px 10px',
              borderRadius: 8,
              border: '1px solid var(--border-main)',
              background: 'var(--bg-surface)',
              color: 'var(--text-secondary)',
              fontSize: 12,
              fontWeight: 500,
              cursor: busy ? 'not-allowed' : 'pointer',
              opacity: busy ? 0.5 : 1,
            }}
          >
            Clear
          </button>
        )}
      </div>

      {/* Body: thread + composer | rail */}
      <div style={{ flex: 1, minHeight: 0, display: 'grid', gridTemplateColumns: narrow ? 'minmax(0,1fr)' : 'minmax(0,1fr) 260px' }}>
        <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0 }}>
          <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '20px 24px' }}>
            <div style={{ maxWidth: 860, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 14 }}>
              {thread.length === 0 ? (
                <div style={{ padding: '40px 0 20px', display: 'flex', flexDirection: 'column', gap: 12, maxWidth: 560 }}>
                  <Sparkles size={22} color="var(--accent-blue)" />
                  <h2 style={{ fontSize: 17, fontWeight: 700, margin: 0 }}>Ask a question about your business</h2>
                  <p style={{ fontSize: 13, lineHeight: 1.6, color: 'var(--text-secondary)', margin: 0 }}>
                    Write it the way you would ask a colleague. Semabridge matches your words to the governed metrics and
                    business concepts in your ontology, applies your access policies, and runs the query on the engine you
                    pick. Every answer shows how it was computed, and you can certify good answers so AI engines reuse them.
                  </p>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 4 }}>
                    {suggestedQuestions.map((q) => (
                      <button key={q} type="button" className="ask-rail-item" onClick={() => ask(q)} style={{ padding: '6px 11px', borderRadius: 999, border: '1px solid var(--border-main)', background: 'var(--bg-surface)', color: 'var(--text-secondary)', fontSize: 12.5, cursor: 'pointer' }}>
                        {q}
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                thread.map((entry) => (
                  <React.Fragment key={entry.id}>
                    <div
                      style={{
                        alignSelf: 'flex-end',
                        maxWidth: '75%',
                        padding: '9px 13px',
                        borderRadius: '12px 12px 3px 12px',
                        background: 'var(--color-accent-faint)',
                        border: '1px solid var(--border-focus)',
                        fontSize: 13.5,
                        lineHeight: 1.45,
                        wordBreak: 'break-word',
                      }}
                    >
                      {entry.question}
                    </div>
                    <AnswerCard
                      entry={entry}
                      onCertify={() => certify(entry)}
                      onRetry={() => retry(entry, false)}
                      onOfflineDraft={() => retry(entry, true)}
                      onPickView={(view) => retry(entry, false, view)}
                    />
                  </React.Fragment>
                ))
              )}
              <div ref={threadEndRef} />
            </div>
          </div>

          {/* Composer */}
          <div style={{ padding: '12px 24px 18px' }}>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (!busy) ask(draft);
              }}
              style={{ maxWidth: 860, margin: '0 auto', display: 'flex', alignItems: 'flex-end', gap: 8, padding: '8px 8px 8px 14px', borderRadius: 12, border: '1px solid var(--border-main)', background: 'var(--bg-surface)' }}
            >
              <textarea
                aria-label="Ask a business question"
                className="ask-composer-input"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={onKeyDown}
                rows={1}
                placeholder="Ask a business question…"
                style={{ flex: 1, resize: 'none', border: 'none', outline: 'none', background: 'transparent', color: 'var(--text-primary)', fontSize: 13.5, lineHeight: 1.5, padding: '5px 0', maxHeight: 140, fontFamily: 'inherit' }}
              />
              <button type="submit" disabled={busy || !draft.trim()} style={{ ...BTN_PRIMARY, padding: '7px 14px', opacity: busy || !draft.trim() ? 0.55 : 1, cursor: busy || !draft.trim() ? 'default' : 'pointer' }}>
                <ArrowUp size={14} /> {busy ? 'Asking…' : 'Ask'}
              </button>
            </form>
          </div>
        </div>

        {!narrow && <AskRail suggestions={suggestedQuestions} certified={certified} onPick={ask} disabled={busy} />}
      </div>

      {genieOpen && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Databricks Genie Space Specification"
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 1000,
            background: 'rgba(0, 0, 0, 0.55)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 20,
          }}
        >
          <div
            style={{
              width: 'min(760px, 100%)',
              maxHeight: '86vh',
              display: 'flex',
              flexDirection: 'column',
              borderRadius: 12,
              border: '1px solid var(--border-main)',
              background: 'var(--bg-surface)',
              color: 'var(--text-primary)',
              boxShadow: '0 20px 48px rgba(0, 0, 0, 0.35)',
              overflow: 'hidden',
            }}
          >
            <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border-main)', display: 'flex', alignItems: 'center', gap: 10 }}>
              <Wand2 size={16} color="var(--accent-blue)" />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 15, fontWeight: 700 }}>Databricks AI/BI Genie Space</div>
                <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                  Generated from Unity Catalog Metric Views, ontology metrics, and verified queries
                </div>
              </div>
              <button
                type="button"
                aria-label="Close Genie Space preview"
                onClick={() => setGenieOpen(false)}
                style={{ border: 'none', background: 'transparent', color: 'var(--text-secondary)', cursor: 'pointer', padding: 4 }}
              >
                <X size={16} />
              </button>
            </div>

            <div style={{ padding: '16px 18px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 12 }}>
              {genieLoading && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: 'var(--text-secondary)' }}>
                  <Loader2 size={15} className="animate-spin" /> Generating Genie Space specification…
                </div>
              )}

              {genieError && (
                <div style={{ padding: '10px 12px', borderRadius: 8, border: '1px solid var(--color-danger, #dc2626)', background: 'rgba(220, 38, 38, 0.08)', color: 'var(--color-danger, #dc2626)', fontSize: 12.5 }}>
                  {genieError}
                </div>
              )}

              {genieData?.status === 'conflict_manual_edit' && (
                <div
                  role="alert"
                  style={{
                    padding: '10px 12px',
                    borderRadius: 8,
                    border: '1px solid #d97706',
                    background: 'rgba(217, 119, 6, 0.1)',
                    fontSize: 12.5,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: 10,
                    flexWrap: 'wrap',
                  }}
                >
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: '#b45309', fontWeight: 500 }}>
                    <AlertTriangle size={14} />
                    {genieData.message || 'Genie space was edited manually in Databricks. Pass force_overwrite=true to overwrite.'}
                  </span>
                  <button
                    type="button"
                    onClick={() => deployGenieSpace({ forceOverwrite: true, spaceId: genieData.space_id })}
                    disabled={genieDeploying}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 5,
                      padding: '5px 10px',
                      borderRadius: 6,
                      border: '1px solid #d97706',
                      background: '#d97706',
                      color: '#fff',
                      fontSize: 12,
                      fontWeight: 600,
                      cursor: genieDeploying ? 'not-allowed' : 'pointer',
                      opacity: genieDeploying ? 0.6 : 1,
                    }}
                  >
                    {genieDeploying ? <Loader2 size={12} className="animate-spin" /> : <AlertTriangle size={12} />}
                    Force Overwrite
                  </button>
                </div>
              )}

              {genieData?.status === 'deployed' && (
                <div style={{ padding: '10px 12px', borderRadius: 8, border: '1px solid var(--color-success, #16a34a)', background: 'rgba(22, 163, 74, 0.08)', fontSize: 12.5, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
                  <span>
                    Deployed to Databricks Genie Space <b>{genieData.space_id || 'created'}</b>
                  </span>
                  {genieData.space_url && (
                    <a
                      href={genieData.space_url}
                      target="_blank"
                      rel="noreferrer"
                      style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color: 'var(--accent-blue)', fontWeight: 600, textDecoration: 'none' }}
                    >
                      Open in Databricks <ExternalLink size={12} />
                    </a>
                  )}
                </div>
              )}

              {genieEvalError && (
                <div style={{ padding: '10px 12px', borderRadius: 8, border: '1px solid var(--color-danger, #dc2626)', background: 'rgba(220, 38, 38, 0.08)', color: 'var(--color-danger, #dc2626)', fontSize: 12.5 }}>
                  {genieEvalError}
                </div>
              )}

              {genieEval && (
                <div style={{ padding: '10px 12px', borderRadius: 8, border: '1px solid var(--border-focus)', background: 'var(--color-accent-faint)', fontSize: 12.5, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
                  <span>
                    Evaluation Score: <b>{genieEval.score_pct ?? 0}%</b> ({genieEval.passed ?? 0}/{genieEval.total_benchmarks ?? 0} benchmarks passed)
                  </span>
                  {genieEval.eval_run_id && (
                    <span style={{ fontSize: 11.5, color: 'var(--text-secondary)' }}>
                      Run ID: <code>{genieEval.eval_run_id}</code>
                    </span>
                  )}
                </div>
              )}

              {genieData?.spec && (
                <>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 8 }}>
                    <div style={{ padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border-main)', background: 'var(--bg-app)' }}>
                      <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>Metric Views</div>
                      <div style={{ fontSize: 15, fontWeight: 700 }}>{(genieData.spec.table_identifiers || []).length}</div>
                    </div>
                    <div style={{ padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border-main)', background: 'var(--bg-app)' }}>
                      <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>Sample Questions</div>
                      <div style={{ fontSize: 15, fontWeight: 700 }}>{(genieData.spec.sample_questions || []).length}</div>
                    </div>
                    <div style={{ padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border-main)', background: 'var(--bg-app)' }}>
                      <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>SQL Snippets</div>
                      <div style={{ fontSize: 15, fontWeight: 700 }}>{(genieData.spec.sql_snippets || []).length}</div>
                    </div>
                    <div style={{ padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border-main)', background: 'var(--bg-app)' }}>
                      <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>Benchmarks</div>
                      <div style={{ fontSize: 15, fontWeight: 700 }}>{(genieData.spec.benchmarks || []).length}</div>
                    </div>
                  </div>

                  <pre
                    style={{
                      margin: 0,
                      padding: 12,
                      borderRadius: 8,
                      border: '1px solid var(--border-main)',
                      background: 'var(--bg-app)',
                      color: 'var(--text-primary)',
                      fontSize: 12,
                      lineHeight: 1.5,
                      overflowX: 'auto',
                      maxHeight: 320,
                    }}
                  >
                    {JSON.stringify(genieData.spec, null, 2)}
                  </pre>
                </>
              )}
            </div>

            <div style={{ padding: '12px 18px', borderTop: '1px solid var(--border-main)', display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 8, flexWrap: 'wrap' }}>
              <button
                type="button"
                onClick={copyGenieJson}
                disabled={!genieData?.spec}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                  padding: '6px 12px',
                  borderRadius: 8,
                  border: '1px solid var(--border-main)',
                  background: 'var(--bg-app)',
                  color: 'var(--text-primary)',
                  fontSize: 12.5,
                  fontWeight: 500,
                  cursor: !genieData?.spec ? 'not-allowed' : 'pointer',
                  opacity: !genieData?.spec ? 0.5 : 1,
                }}
              >
                {genieCopied ? <Check size={13} /> : <Copy size={13} />}
                {genieCopied ? 'Copied JSON' : 'Copy JSON'}
              </button>
              {genieData?.space_id && genieData?.status === 'deployed' && (
                <button
                  type="button"
                  onClick={runGenieEvaluation}
                  disabled={genieEvaluating}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                    padding: '6px 12px',
                    borderRadius: 8,
                    border: '1px solid var(--border-focus)',
                    background: 'var(--color-accent-faint)',
                    color: 'var(--text-primary)',
                    fontSize: 12.5,
                    fontWeight: 600,
                    cursor: genieEvaluating ? 'not-allowed' : 'pointer',
                    opacity: genieEvaluating ? 0.55 : 1,
                  }}
                >
                  {genieEvaluating ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
                  {genieEvaluating ? 'Evaluating…' : 'Run Evaluation'}
                </button>
              )}
              <button
                type="button"
                onClick={() => deployGenieSpace()}
                disabled={!genieData?.spec || genieDeploying}
                style={{
                  ...BTN_PRIMARY,
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                  padding: '6px 14px',
                  fontSize: 12.5,
                  opacity: !genieData?.spec || genieDeploying ? 0.55 : 1,
                  cursor: !genieData?.spec || genieDeploying ? 'not-allowed' : 'pointer',
                }}
              >
                {genieDeploying ? <Loader2 size={13} className="animate-spin" /> : <Rocket size={13} />}
                {genieDeploying ? 'Deploying…' : 'Deploy to Databricks Genie'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
