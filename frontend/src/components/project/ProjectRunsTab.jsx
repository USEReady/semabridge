/**
 * Runs tab of the project page: this project's runs (newest first) with the
 * conversion result bar. Data: GET /api/projects/{id}/runs and, for the most
 * recent finished runs, /runs/{run_id}/report-summary. Full detail lives on /runs.
 */
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { ConversionBar, ConversionLegend } from '../projects/projectHealth';
import { runId, conversionPct, durationLabel, relTime, statusLabel, isFinished } from './projectData';
import { card, lbl, muted, faint, th, td, statusTone, btnGhost } from './projectStyles';
import { Pill } from './projectUi';

function ResultCell({ run, summary }) {
  if (!isFinished(run)) return <span style={faint}>—</span>;
  if (summary === undefined) return <span style={faint} title="Open the run for its result">—</span>;
  if (!summary || summary.crashed || !summary.counts) return <span style={faint}>Report not available</span>;
  const pct = conversionPct(summary.counts);
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 180 }}>
      <div style={{ flex: 1 }}><ConversionBar counts={summary.counts} /></div>
      <span style={{ fontSize: 12, color: 'var(--text-primary)', fontVariantNumeric: 'tabular-nums', width: 36, textAlign: 'right' }}>
        {pct === null ? '—' : `${pct}%`}
      </span>
    </div>
  );
}

export default function ProjectRunsTab({ runs, runsLoading, summaries }) {
  const navigate = useNavigate();
  const open = (run) => navigate('/runs', { state: { runId: runId(run), autoExpand: true } });

  return (
    <div style={{ ...card, padding: 0, gap: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 14px 8px', flexWrap: 'wrap' }}>
        <span style={lbl}>Runs</span>
        <span style={faint}>{runs.length}</span>
        <span style={{ flex: 1 }} />
        <ConversionLegend />
        <button type="button" style={btnGhost} onClick={() => navigate('/runs')}>Open Runs for full detail →</button>
      </div>
      {runsLoading ? (
        <div style={{ ...muted, padding: '8px 14px 16px' }}>Loading runs…</div>
      ) : runs.length === 0 ? (
        <div style={{ ...muted, padding: '8px 14px 16px' }}>No runs yet. Use “Run now” to start the first one.</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th style={th}>Run</th>
                <th style={th}>Started</th>
                <th style={th}>Took</th>
                <th style={{ ...th, width: '35%' }}>Result</th>
                <th style={th}>Status</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run, i) => {
                const started = run.started_at ? new Date(run.started_at) : null;
                return (
                  <tr
                    key={runId(run) || i}
                    onClick={() => open(run)}
                    style={{ cursor: 'pointer' }}
                    title="Open this run in Runs"
                  >
                    <td style={{ ...td, fontWeight: 600, color: 'var(--text-primary)' }}>
                      #{runs.length - i}
                      <div style={{ ...faint, fontSize: 10, fontWeight: 400, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace' }}>{runId(run)}</div>
                    </td>
                    <td style={td}>
                      <div style={{ color: 'var(--text-primary)' }}>{started && !Number.isNaN(started.getTime()) ? started.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '—'}</div>
                      <div style={faint}>{relTime(run.started_at) || ''}</div>
                    </td>
                    <td style={{ ...td, color: 'var(--text-secondary)', fontVariantNumeric: 'tabular-nums' }}>{isFinished(run) ? durationLabel(run) : '—'}</td>
                    <td style={td}><ResultCell run={run} summary={summaries[runId(run)]} /></td>
                    <td style={td}><Pill tone={statusTone(run.status)}>{statusLabel(run.status)}</Pill></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
