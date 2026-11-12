import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ArrowUp, Sparkles } from 'lucide-react';
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

  const threadEndRef = useRef(null);
  const autoAskedRef = useRef('');
  const narrow = useNarrow();
  const busy = thread.some((e) => e.status === 'loading');

  useEffect(() => writeStore(window.sessionStorage, THREAD_KEY, thread), [thread]);
  useEffect(() => writeStore(window.sessionStorage, CERTIFIED_KEY, certified), [certified]);
  useEffect(() => writeStore(window.localStorage, TARGET_KEY, target), [target]);

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

  const runEntry = useCallback(async (id, question, engine, project, allowOfflineDraft = false) => {
    try {
      const { plan, result } = await askQuestion({
        question,
        target: engine,
        projectId: project,
        allowOfflineDraft,
      });
      patch(id, { status: 'done', plan, result, cortexUnavailable: false });
    } catch (err) {
      patch(id, {
        status: 'error',
        error: err?.message || String(err),
        errorStage: err?.stage || 'compile',
        cortexUnavailable: Boolean(err?.cortexUnavailable),
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

  const retry = (entry, allowOfflineDraft = false) => {
    patch(entry.id, { projectId, target, status: 'loading', error: null, plan: null, result: null, cortexUnavailable: false });
    runEntry(entry.id, entry.question, target, projectId, allowOfflineDraft);
  };

  const certify = async (entry) => {
    patch(entry.id, { certifyState: 'saving', certifyError: null });
    try {
      await certifyAnswer({
        question: entry.question,
        sql: entry.plan?.generated_sql,
        target: entry.target,
        projectId: entry.projectId,
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
        <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Answers use your governed metrics and access rules</span>
        <span style={{ flex: 1 }} />
        {projects.length > 0 && (
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text-tertiary)' }}>
            Scope
            <select
              value={projectId}
              onChange={(e) => setProjectId(e.target.value)}
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
            return (
              <button
                key={e.id}
                type="button"
                role="radio"
                aria-checked={on}
                onClick={() => setTarget(e.id)}
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
                {e.label}
              </button>
            );
          })}
        </div>
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
    </div>
  );
}
