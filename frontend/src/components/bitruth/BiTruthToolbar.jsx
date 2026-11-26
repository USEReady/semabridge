import React from 'react';
import {
  Target, CheckCircle2, AlertTriangle, ShieldCheck, Sparkles, Plus, Upload, Clock, HelpCircle, Send
} from 'lucide-react';
import DeployedViewSelector from '../common/DeployedViewSelector';

const INPUT = {
  padding: '6px 10px',
  borderRadius: 6,
  background: 'var(--bg-surface-raised)',
  border: '1px solid var(--border-main)',
  color: 'var(--text-primary)',
  fontSize: 12,
  outline: 'none',
};

const BTN = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 5,
  padding: '5px 10px',
  borderRadius: 6,
  border: '1px solid var(--border-main)',
  background: 'var(--bg-surface-raised)',
  color: 'var(--text-primary)',
  fontSize: 12,
  fontWeight: 600,
  cursor: 'pointer',
};

const BTN_PRIMARY = {
  ...BTN,
  borderColor: 'var(--accent-blue)',
  background: 'var(--accent-blue)',
  color: '#fff',
};

export default function BiTruthToolbar({
  projectId,
  selectedView,
  cases = [],
  approvedCount = 0,
  latestRun = null,
  busy = false,
  limit = 15,
  onLimitChange,
  onRunSuite,
  onGenerate,
  onOpenAdd,
  onOpenImport,
  onOpenSchedules,
  onOpenInject,
}) {
  const accuracyPct = latestRun?.accuracy_pct !== undefined ? latestRun.accuracy_pct : null;
  const totalCases = cases.length;
  const draftCount = cases.filter((c) => c.status === 'draft').length;

  // Determine Run Suite disabled state and informative tooltip
  const canRunSuite = Boolean(projectId && selectedView && approvedCount > 0 && !busy);
  let runSuiteTooltip = '';
  if (busy) {
    runSuiteTooltip = 'Verifying test suite against warehouse…';
  } else if (!projectId) {
    runSuiteTooltip = 'Select a project to run truth suite';
  } else if (!selectedView) {
    runSuiteTooltip = 'Select a deployed view to run truth suite';
  } else if (approvedCount === 0) {
    if (draftCount > 0) {
      runSuiteTooltip = `No approved cases to run (${draftCount} draft case${draftCount === 1 ? '' : 's'} must be approved before running)`;
    } else if (totalCases === 0) {
      runSuiteTooltip = 'No test cases found. Generate or add cases first';
    } else {
      runSuiteTooltip = 'No approved cases to run';
    }
  } else {
    runSuiteTooltip = `Execute ${approvedCount} approved case${approvedCount === 1 ? '' : 's'} against warehouse`;
  }

  // Determine Generate disabled state and informative tooltip
  const canGenerate = Boolean(projectId && selectedView && !busy);
  let generateTooltip = '';
  if (busy) {
    generateTooltip = 'Generating truth questions…';
  } else if (!projectId) {
    generateTooltip = 'Select a project to generate truth questions';
  } else if (!selectedView) {
    generateTooltip = 'Select a deployed view to generate truth questions';
  } else {
    generateTooltip = `Generate ${limit} ground-truth questions and SQL for ${selectedView}`;
  }

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexWrap: 'wrap',
        gap: 10,
        background: 'var(--bg-surface)',
        padding: '10px 16px',
        borderRadius: 8,
        border: '1px solid var(--border-main)',
      }}
    >
      {/* Left: Title + Key Indicator Pills + Deployed View Dropdown */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Target size={18} style={{ color: 'var(--accent-blue)' }} />
          <span style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)' }}>
            BI Truth Suite
          </span>
        </div>

        {/* Accuracy Meter Badge */}
        {latestRun && (
          <span
            style={{
              fontSize: 11.5,
              fontWeight: 700,
              padding: '3px 8px',
              borderRadius: 4,
              background:
                accuracyPct >= 90 ? 'rgba(34,197,94,0.12)' : 'rgba(234,179,8,0.12)',
              color:
                accuracyPct >= 90
                  ? 'var(--color-success, #22c55e)'
                  : 'var(--color-warning, #eab308)',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 5,
            }}
            title={`${latestRun.passed} of ${latestRun.total} verified accurately`}
          >
            {accuracyPct >= 90 ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />}
            {accuracyPct}% Accuracy ({latestRun.passed}/{latestRun.total})
          </span>
        )}

        {/* View Selector Dropdown */}
        <DeployedViewSelector projectId={projectId} alwaysDropdown={true} />
      </div>

      {/* Right: Actions */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        {/* Run Suite Button with informative tooltip */}
        <span
          title={runSuiteTooltip}
          style={{ display: 'inline-flex', cursor: canRunSuite ? 'pointer' : 'not-allowed' }}
        >
          <button
            type="button"
            disabled={!canRunSuite}
            onClick={() => onRunSuite(selectedView)}
            style={{
              ...BTN_PRIMARY,
              background:
                approvedCount > 0 && canRunSuite
                  ? 'var(--color-success, #22c55e)'
                  : 'var(--bg-surface-raised)',
              borderColor:
                approvedCount > 0 && canRunSuite
                  ? 'var(--color-success, #22c55e)'
                  : 'var(--border-main)',
              color: approvedCount > 0 && canRunSuite ? '#fff' : 'var(--text-tertiary)',
              cursor: canRunSuite ? 'pointer' : 'not-allowed',
            }}
          >
            <ShieldCheck size={13} /> {busy ? 'Verifying…' : `Run Suite (${approvedCount})`}
          </button>
        </span>

        {/* Generate Controls with informative tooltip */}
        <span
          title={generateTooltip}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 4, cursor: canGenerate ? 'pointer' : 'not-allowed' }}
        >
          <button
            type="button"
            style={{
              ...BTN_PRIMARY,
              background: canGenerate ? 'var(--accent-blue)' : 'var(--bg-surface-raised)',
              borderColor: canGenerate ? 'var(--accent-blue)' : 'var(--border-main)',
              color: canGenerate ? '#fff' : 'var(--text-tertiary)',
              cursor: canGenerate ? 'pointer' : 'not-allowed',
            }}
            disabled={!canGenerate}
            onClick={onGenerate}
          >
            <Sparkles size={13} /> {busy ? 'Generating…' : 'Generate'}
          </button>
          <select
            value={limit}
            onChange={(e) => onLimitChange(Number(e.target.value))}
            disabled={!canGenerate}
            style={{
              ...INPUT,
              padding: '4px 6px',
              fontSize: 11,
              borderRadius: 6,
              height: 28,
              cursor: canGenerate ? 'pointer' : 'not-allowed',
              opacity: canGenerate ? 1 : 0.6,
            }}
            title="Number of questions to generate"
          >
            <option value={10}>10</option>
            <option value={15}>15</option>
            <option value={20}>20</option>
            <option value={50}>50</option>
          </select>
        </span>

        <button
          type="button"
          style={BTN}
          onClick={onOpenAdd}
          title="Add a custom ground-truth question"
        >
          <Plus size={13} /> Add
        </button>

        <button
          type="button"
          style={BTN}
          onClick={onOpenImport}
          title="Import questions from spreadsheet or JSON"
        >
          <Upload size={13} /> Import
        </button>

        <button
          type="button"
          style={BTN}
          onClick={onOpenSchedules}
          title="Automated recurring test schedules"
        >
          <Clock size={13} /> Schedules
        </button>

        {/* Visual separator before write action */}
        <div style={{ width: 1, height: 20, background: 'var(--border-main)', margin: '0 4px' }} />

        {/* Inject to AI (Dedicated Write Action) */}
        <span
          title={
            !projectId
              ? 'Select a project to inject verified benchmark pairs'
              : 'Inspect qualifying verified pairs and write them to Cortex Analyst (Snowflake) or Genie Spaces (Databricks) as AI ground-truth'
          }
          style={{ display: 'inline-flex', cursor: projectId ? 'pointer' : 'not-allowed' }}
        >
          <button
            type="button"
            disabled={!projectId}
            onClick={onOpenInject}
            style={{
              ...BTN,
              background: 'rgba(245, 158, 11, 0.1)',
              borderColor: 'var(--color-warning, #f59e0b)',
              color: 'var(--color-warning, #d97706)',
              cursor: projectId ? 'pointer' : 'not-allowed',
            }}
          >
            <Send size={13} /> Inject to AI (Write)
          </button>
        </span>
      </div>
    </div>
  );
}
