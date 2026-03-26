/**
 * FilterBar — Source/Target connector type filters for the Projects list.
 *
 * Props:
 *   sourceFilter   — current source filter value ('' = All)
 *   targetFilter   — current target filter value ('' = All)
 *   onSourceChange — called with new source filter value
 *   onTargetChange — called with new target filter value
 *   connectorTypes — list of known connector type strings
 */

const CONNECTOR_LABELS = {
  fabric: 'MS Fabric',
  snowflake: 'Snowflake',
  databricks: 'Databricks',
  postgresql: 'PostgreSQL',
  salesforce: 'Salesforce',
  google_sheets: 'Google Sheets',
};

const CONNECTOR_ICONS = {
  fabric: '🔷',
  snowflake: '❄️',
  databricks: '🧱',
  postgresql: '🐘',
  salesforce: '☁️',
  google_sheets: '📊',
};

function Chip({ value, label, icon, active, onClick }) {
  return (
    <button
      onClick={() => onClick(value)}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: '4px 10px',
        borderRadius: 99,
        border: active ? '1.5px solid var(--accent-blue)' : '1px solid var(--border-main)',
        background: active ? 'var(--accent-blue)18' : 'transparent',
        color: active ? 'var(--accent-blue)' : 'var(--text-secondary)',
        fontSize: 12,
        fontWeight: active ? 600 : 400,
        cursor: 'pointer',
        transition: 'all 0.15s',
        whiteSpace: 'nowrap',
      }}
    >
      {icon && <span>{icon}</span>}
      {label}
    </button>
  );
}

export default function FilterBar({
  sourceFilter = '',
  targetFilter = '',
  onSourceChange,
  onTargetChange,
  connectorTypes = [],
}) {
  // Always show all connector types, regardless of data
  const types = ['fabric', 'snowflake', 'databricks', 'postgresql', 'salesforce'];

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 16,
        marginBottom: 16,
        flexWrap: 'wrap',
      }}
    >
      {/* Source filter */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Source
        </span>
        <Chip value="" label="All" active={sourceFilter === ''} onClick={onSourceChange} />
        {types.map(t => (
          <Chip
            key={t}
            value={t}
            label={CONNECTOR_LABELS[t] || t}
            icon={CONNECTOR_ICONS[t]}
            active={sourceFilter === t}
            onClick={onSourceChange}
          />
        ))}
      </div>

      {/* Separator */}
      <div style={{ width: 1, height: 20, background: 'var(--border-main)' }} />

      {/* Target filter */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Target
        </span>
        <Chip value="" label="All" active={targetFilter === ''} onClick={onTargetChange} />
        {types.map(t => (
          <Chip
            key={t}
            value={t}
            label={CONNECTOR_LABELS[t] || t}
            icon={CONNECTOR_ICONS[t]}
            active={targetFilter === t}
            onClick={onTargetChange}
          />
        ))}
      </div>
    </div>
  );
}
