/**
 * Run report viewer (moved out of ProjectJobsPage). One modal per run with
 * Summary (JSON → RunReportSummary) and Raw Report (Markdown) views, a
 * per-file picker for multi-PBIX runs, and a .md download.
 *
 * Props: run (null = closed), initialModel, initialView ('summary' | 'markdown'), onClose.
 * Mount it with a `key` per opening so the initial model/view props apply.
 */
import { useEffect, useState } from 'react';
import { Download } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import Modal from '../common/Modal';
import RunReportSummary from '../RunReportSummary';
import { api } from '../../utils/api';
import { cleanPbixLabel } from '../../utils/pbixLabel';

const MD_COMPONENTS = {
  table: ({ ...props }) => (
    <table style={{ width: '100%', borderCollapse: 'collapse', margin: '16px 0', border: '1px solid var(--border-main)', borderRadius: 8, overflow: 'hidden' }} {...props} />
  ),
  th: ({ style, align, ...props }) => (
    <th style={{ background: 'var(--bg-surface-raised)', padding: '10px 20px', border: '1px solid var(--border-main)', textAlign: style?.textAlign || align || 'left', fontWeight: 600, color: 'var(--text-primary)', whiteSpace: 'nowrap', ...style }} {...props} />
  ),
  td: ({ style, align, ...props }) => (
    <td style={{ padding: '10px 20px', border: '1px solid var(--border-main)', textAlign: style?.textAlign || align || 'left', color: 'var(--text-primary)', ...style }} {...props} />
  ),
  h1: ({ ...props }) => <h1 style={{ fontSize: '1.4em', fontWeight: 700, margin: '16px 0 8px 0', color: 'var(--text-primary)' }} {...props} />,
  h2: ({ ...props }) => <h2 style={{ fontSize: '1.2em', fontWeight: 600, margin: '14px 0 6px 0', color: 'var(--text-primary)', borderBottom: '1px solid var(--border-subtle)', paddingBottom: 4 }} {...props} />,
  h3: ({ ...props }) => <h3 style={{ fontSize: '1.05em', fontWeight: 600, margin: '12px 0 4px 0', color: 'var(--text-primary)' }} {...props} />,
  ul: ({ ...props }) => <ul style={{ paddingLeft: 20, margin: '8px 0' }} {...props} />,
  ol: ({ ...props }) => <ol style={{ paddingLeft: 20, margin: '8px 0' }} {...props} />,
  li: ({ ...props }) => <li style={{ marginBottom: 4 }} {...props} />,
  code: ({ inline, ...props }) => (
    inline ? (
      <code style={{ background: 'var(--bg-surface-raised)', padding: '2px 5px', borderRadius: 4, fontFamily: 'monospace', fontSize: '0.9em' }} {...props} />
    ) : (
      <code style={{ display: 'block', background: 'var(--bg-surface-raised)', padding: 10, borderRadius: 6, fontFamily: 'monospace', fontSize: '0.9em', overflowX: 'auto' }} {...props} />
    )
  ),
  blockquote: ({ ...props }) => (
    <blockquote style={{ borderLeft: '3px solid var(--accent-blue)', paddingLeft: 12, margin: '8px 0', color: 'var(--text-secondary)', fontStyle: 'italic' }} {...props} />
  ),
};

import { downloadRunMarkdown } from './runReportActions';

function chipStyle(on) {
  return {
    padding: '5px 12px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
    border: `1px solid ${on ? 'var(--accent-blue)' : 'var(--border-main)'}`,
    background: on ? 'rgba(99, 102, 241, 0.12)' : 'var(--bg-surface-raised)',
    color: on ? 'var(--accent-blue)' : 'var(--text-secondary)',
  };
}

export default function RunReportModal({ run, initialModel = null, initialView = 'summary', onClose }) {
  const [model, setModel] = useState(initialModel);
  const [view, setView] = useState(initialView);
  const [maximized, setMaximized] = useState(false);
  // Results are tagged with the model they belong to; anything else counts as still loading.
  const [contentRes, setContentRes] = useState(null); // { model, text?, error? }
  const [summaryRes, setSummaryRes] = useState(null); // { model, data?, error? }
  const tag = model || '';

  // Summary and Raw Report are both fetched up front so switching views is instant.
  useEffect(() => {
    if (!run) return undefined;
    let alive = true;
    const pid = run.project_id;
    const rid = run.id || run.run_id;
    (model ? api.getRunModelReport(pid, rid, model) : api.getRunReport(pid, rid))
      .then(text => { if (alive) setContentRes({ model: tag, text }); })
      .catch(err => { if (alive) setContentRes({ model: tag, error: err.message || 'Failed to load report content.' }); });
    (model ? api.getRunModelReportSummary(pid, rid, model) : api.getRunReportSummary(pid, rid))
      .then(data => { if (alive) setSummaryRes({ model: tag, data }); })
      .catch(err => { if (alive) setSummaryRes({ model: tag, error: err.message || 'Failed to load report summary.' }); });
    return () => { alive = false; };
  }, [run, model, tag]);

  const contentState = { loading: !contentRes || contentRes.model !== tag, error: contentRes?.error || null };
  const summaryState = { loading: !summaryRes || summaryRes.model !== tag, error: summaryRes?.error || null };
  const content = contentState.loading ? '' : (contentRes.text || '');
  const summary = summaryState.loading ? null : (summaryRes.data || null);

  const modelReports = Array.isArray(run?.model_reports) ? run.model_reports : [];
  const rid = run?.id || run?.run_id || '';

  return (
    <Modal
      open={Boolean(run)}
      onClose={onClose}
      title={`Report · ${rid}${model ? ` · ${cleanPbixLabel(model)}` : ''}`}
      size="lg"
      allowMaximize
      isMaximized={maximized}
      onToggleMaximize={() => setMaximized(v => !v)}
      footer={
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%' }}>
          <button
            type="button"
            onClick={() => downloadRunMarkdown(run, model).catch(err => console.error('Failed to download report:', err))}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 6, padding: '6px 12px', borderRadius: 6,
              fontSize: 12, fontWeight: 600, background: 'var(--accent-blue)', color: '#fff', border: 'none', cursor: 'pointer',
            }}
          >
            <Download size={14} /> {model ? `Download ${cleanPbixLabel(model)} .md` : 'Download .md'}
          </button>
          <button
            type="button"
            onClick={onClose}
            style={{
              padding: '6px 14px', borderRadius: 6, fontSize: 12, fontWeight: 500, cursor: 'pointer',
              background: 'var(--bg-surface-raised)', color: 'var(--text-secondary)', border: '1px solid var(--border-main)',
            }}
          >
            Close
          </button>
        </div>
      }
    >
      {modelReports.length > 1 && (
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-tertiary)', marginBottom: 6 }}>File</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {modelReports.map(entry => (
              <button key={entry.model} type="button" onClick={() => setModel(entry.model)} style={chipStyle(model === entry.model)}>
                {cleanPbixLabel(entry.model)}
              </button>
            ))}
            <button type="button" onClick={() => setModel(null)} style={chipStyle(!model)}>
              All files (combined)
            </button>
          </div>
        </div>
      )}

      <div style={{ display: 'flex', gap: 4, padding: '0 4px 12px 4px', borderBottom: '1px solid var(--border-subtle)', marginBottom: 8 }}>
        {[{ key: 'summary', label: 'Summary' }, { key: 'markdown', label: 'Raw Report' }].map(tab => (
          <button
            key={tab.key}
            type="button"
            onClick={() => setView(tab.key)}
            style={{
              padding: '6px 14px', borderRadius: 6, fontSize: 12, fontWeight: 600, border: 'none', cursor: 'pointer',
              background: view === tab.key ? 'var(--accent-blue)' : 'transparent',
              color: view === tab.key ? '#fff' : 'var(--text-secondary)',
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {view === 'summary' ? (
        summaryState.loading ? (
          <div style={{ padding: 32, textAlign: 'center', color: 'var(--text-secondary)' }}>Loading summary...</div>
        ) : summaryState.error ? (
          <div style={{ padding: 24, color: 'var(--color-error)' }}>{summaryState.error}</div>
        ) : (
          <RunReportSummary data={summary} />
        )
      ) : contentState.loading ? (
        <div style={{ padding: 32, textAlign: 'center', color: 'var(--text-secondary)' }}>Loading report...</div>
      ) : contentState.error ? (
        <div style={{ padding: 24, color: 'var(--color-error)' }}>{contentState.error}</div>
      ) : (
        <div style={{ padding: '8px 4px', fontSize: 13, lineHeight: 1.6, color: 'var(--text-primary)' }}>
          {/* At a glance: built from the summary JSON, the Raw Report stays byte-for-byte unchanged. */}
          {summary && !summary.crashed && summary.counts && (
            <div
              style={{
                display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'center', padding: '12px 16px', marginBottom: 16,
                borderRadius: 8, background: 'var(--bg-surface-raised)', border: '1px solid var(--border-main)',
                fontSize: 12, color: 'var(--text-secondary)',
              }}
            >
              <span style={{
                fontWeight: 700,
                color: summary.status === 'success' ? 'var(--color-success)'
                  : summary.status === 'failed' ? 'var(--color-error)' : 'var(--color-warning)',
              }}>
                {summary.status === 'success' ? 'Completed successfully'
                  : summary.status === 'failed' ? 'Did not complete successfully'
                    : 'Completed. A few items need your attention'}
              </span>
              <span><strong style={{ color: 'var(--text-primary)' }}>{summary.counts?.tables ?? 0}</strong> tables found</span>
              <span><strong style={{ color: 'var(--text-primary)' }}>{summary.counts?.calculations ?? 0}</strong> calculations found</span>
              <span><strong style={{ color: 'var(--color-success)' }}>{summary.counts?.converted_total ?? 0}</strong> converted</span>
              <span>
                <strong style={{ color: 'var(--color-error)' }}>
                  {Math.max(0, (summary.counts?.calculations ?? 0) - (summary.counts?.converted_total ?? 0))}
                </strong> not converted
              </span>
            </div>
          )}
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
            {content}
          </ReactMarkdown>
        </div>
      )}
    </Modal>
  );
}
