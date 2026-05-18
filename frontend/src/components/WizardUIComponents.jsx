import { Check, Play, Loader2, AlertTriangle } from 'lucide-react';
import { useState, useMemo, useEffect } from 'react';
import { getConnectorPresentation } from '../utils/projectHelpers';

export function ConnectorChip({ icon, label, selected, onClick }) {
  return (
    <button
      onClick={onClick}
      style={{
        display: 'flex', alignItems: 'center', gap: 10, padding: '10px 20px', borderRadius: 12, cursor: 'pointer',
        background: selected ? 'rgba(59, 130, 246, 0.1)' : 'var(--bg-surface-raised)',
        border: `1.5px solid ${selected ? 'var(--accent-blue)' : 'rgba(255, 255, 255, 0.05)'}`,
        color: selected ? 'var(--accent-blue)' : 'var(--text-secondary)',
        fontSize: 13, fontWeight: selected ? 600 : 500, transition: 'all 0.2s ease',
        boxShadow: selected ? '0 0 0 1px rgba(59, 130, 246, 0.2)' : 'none',
      }}
    >
      {icon && <div style={{ display: 'flex' }}>{icon}</div>}
      {label}
      {selected && <Check size={14} strokeWidth={3} />}
    </button>
  );
}

export function ToggleOption({ label, description, checked, onChange }) {
  return (
    <div
      style={{
        display: 'flex', alignItems: 'flex-start', gap: 12,
        padding: '14px 16px', borderRadius: 10, cursor: 'pointer',
        background: checked ? 'var(--accent-blue)08' : 'var(--bg-surface)',
        border: checked ? '1.5px solid var(--accent-blue)40' : '1px solid var(--border-main)',
      }}
      onClick={() => onChange(v => !v)}
    >
      <div
        style={{
          width: 40, height: 22, borderRadius: 11, flexShrink: 0, marginTop: 2,
          background: checked ? 'var(--accent-blue)' : 'var(--bg-surface-raised)',
          position: 'relative', transition: 'background 0.2s',
        }}
      >
        <div style={{
          position: 'absolute', top: 3, left: checked ? 20 : 3,
          width: 16, height: 16, borderRadius: '50%',
          background: '#fff', transition: 'left 0.2s',
          boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
        }} />
      </div>
      <div>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 3 }}>{label}</div>
        <div style={{ fontSize: 11, color: 'var(--text-tertiary)', lineHeight: 1.5 }}>{description}</div>
      </div>
    </div>
  );
}

export function footerBtn(variant) {
  return {
    padding: '9px 20px', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer',
    background: variant === 'primary' ? 'var(--accent-blue)' : 'transparent',
    color: variant === 'primary' ? '#fff' : 'var(--text-secondary)',
    border: variant === 'primary' ? 'none' : '1px solid var(--border-main)',
  };
}

export function escapeYamlString(value) {
  return String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

export function FlowCard({ label, model }) {
  const connector = getConnectorPresentation(model.type);
  return (
    <div style={{ border: '1px solid var(--border-main)', borderRadius: 10, background: 'var(--bg-surface)', padding: '14px 16px', display: 'grid', gap: 8 }}>
      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-tertiary)', letterSpacing: '0.08em', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <div style={{ width: 30, height: 30, borderRadius: 8, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: `${connector.accent}1f`, color: connector.accent, border: `1px solid ${connector.accent}33` }}>
          {connector.icon}
        </div>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{model.name}</div>
          <div style={{ marginTop: 2, fontSize: 12, color: 'var(--text-tertiary)' }}>{connector.label} · {model.field_count} fields</div>
        </div>
      </div>
    </div>
  );
}

export function CenteredNotice({ icon = null, text }) {
  return (
    <div style={{ padding: '38px 0', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, color: 'var(--text-tertiary)', fontSize: 13 }}>
      {icon}
      <span>{text}</span>
    </div>
  );
}

export function DryRunCTA({ mappingLoading, dryRunFailed, dryRunError, mappingError, onRunDryRun }) {
  return (
    <div style={{ border: '1px solid var(--border-main)', borderRadius: 10, background: 'var(--bg-surface)', padding: '34px 18px', display: 'grid', justifyItems: 'center', gap: 14, textAlign: 'center' }}>
      <div style={{ width: 44, height: 44, borderRadius: 12, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: 'var(--accent-blue)18', color: 'var(--accent-blue)', border: '1px solid var(--accent-blue)30' }}>
        <Play size={20} />
      </div>
      <div style={{ display: 'grid', gap: 5 }}>
        <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)' }}>Run a dry run to generate field-level mappings.</div>
        <div style={{ fontSize: 12, color: 'var(--text-tertiary)', lineHeight: 1.55, maxWidth: 460 }}>
          A dry run validates your connector config and selected sources without writing any data.
        </div>
      </div>
      {dryRunFailed && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--color-error)', lineHeight: 1.5 }}>
          <AlertTriangle size={13} style={{ color: 'var(--color-error)' }} />
          <span>{dryRunError || mappingError || 'Dry run failed. Review connector settings and selected sources, then retry.'}</span>
        </div>
      )}
      <button
        type="button"
        onClick={onRunDryRun}
        disabled={mappingLoading}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 7,
          padding: '9px 14px',
          borderRadius: 8,
          background: 'var(--accent-blue)',
          color: '#fff',
          border: '1px solid var(--accent-blue)',
          fontSize: 12,
          fontWeight: 700,
          cursor: mappingLoading ? 'not-allowed' : 'pointer',
          opacity: mappingLoading ? 0.7 : 1,
        }}
      >
        {mappingLoading ? <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> : <Play size={13} />}
        Run Dry Run
      </button>
    </div>
  );
}

export function useFieldMappings({ rows, filteredRows }) {
  const [page, setPage] = useState(0);
  const pageSize = 50;
  const requiresPagination = filteredRows.length > 1000;
  const pageCount = requiresPagination ? Math.max(1, Math.ceil(filteredRows.length / pageSize)) : 1;

  useEffect(() => {
    setPage(0);
  }, [filteredRows.length]);

  const visibleRows = useMemo(() => {
    if (!requiresPagination) return filteredRows;
    const start = page * pageSize;
    return filteredRows.slice(start, start + pageSize);
  }, [filteredRows, page, requiresPagination]);

  return {
    totalCount: rows.length,
    filteredCount: filteredRows.length,
    visibleRows,
    page,
    pageCount,
    pageSize,
    requiresPagination,
    setPage,
  };
}
