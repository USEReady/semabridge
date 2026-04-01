import { useState, useEffect, useRef } from 'react';
import { Zap, RefreshCw, Link, Edit3, GripVertical, ArrowRight, X, Check } from 'lucide-react';
import PageHeader from '../components/common/PageHeader';
import Modal from '../components/common/Modal';
import StatusBadge from '../components/common/StatusBadge';
import SearchInput from '../components/common/SearchInput';
import { matchesSmartQuery } from '../components/common/SmartSearchBar';
import { api } from '../utils/api';

const TYPE_COLORS = {
  string: '#6366f1', integer: '#10b981', decimal: '#f59e0b',
  boolean: '#8b5cf6', date: '#06b6d4', timestamp: '#ec4899',
  uuid: '#84cc16', text: '#6b7280', float: '#f97316',
};

function TypeBadge({ type }) {
  const color = TYPE_COLORS[type?.toLowerCase()] ?? '#6b7280';
  return (
    <span
      style={{
        fontFamily: 'monospace',
        fontSize: 10,
        fontWeight: 700,
        padding: '2px 6px',
        borderRadius: 4,
        background: `${color}20`,
        color,
        border: `1px solid ${color}30`,
      }}
    >
      {type ?? '?'}
    </span>
  );
}

const VALIDATION_RULES = ['None', 'Not Null', 'Email Format', 'Phone Format', 'Custom Regex'];

export default function ModelMappingPage() {
  const [mappings, setMappings] = useState([]);
  const [sourceFields, setSourceFields] = useState([]);
  const [targetFields, setTargetFields] = useState([]);
  const [sourceModel, setSourceModel] = useState({ name: 'Source Model', type: 'PostgreSQL', field_count: 0 });
  const [targetModel, setTargetModel] = useState({ name: 'Target Model', type: 'Snowflake', field_count: 0 });
  const [loading, setLoading] = useState(true);
  const [autoMapping, setAutoMapping] = useState(false);
  const [search, setSearch] = useState('');
  const [searchUseRegex, setSearchUseRegex] = useState(false);
  const [selectedMapping, setSelectedMapping] = useState(null);
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [editForm, setEditForm] = useState({ transform: '', validation: 'None' });
  const [editSaving, setEditSaving] = useState(false);

  // Load existing mappings
  useEffect(() => {
    (async () => {
      try {
        const data = await api.getMappings();
        if (data) {
          setMappings(data.mappings ?? []);
          setSourceFields(data.source_fields ?? []);
          setTargetFields(data.target_fields ?? []);
          if (data.source_model) setSourceModel(data.source_model);
          if (data.target_model) setTargetModel(data.target_model);
        }
      } catch {
        // Use demo data when no API available
        const demo = generateDemoData();
        setSourceFields(demo.source);
        setTargetFields(demo.target);
        setMappings(demo.mappings);
        setSourceModel({ name: 'sales_transactions', type: 'PostgreSQL', field_count: demo.source.length });
        setTargetModel({ name: 'fact_sales', type: 'Snowflake', field_count: demo.target.length });
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const handleAutoMap = async () => {
    setAutoMapping(true);
    try {
      const result = await api.autoMap();
      if (result?.mappings) {
        setMappings(result.mappings);
      }
    } catch (err) {
      console.error('Auto-map failed:', err);
    } finally {
      setAutoMapping(false);
    }
  };

  const openEditModal = (mapping) => {
    setSelectedMapping(mapping);
    setEditForm({ transform: mapping.transform ?? '', validation: mapping.validation ?? 'None' });
    setEditModalOpen(true);
  };

  const handleSaveEdit = async () => {
    if (!selectedMapping) return;
    setEditSaving(true);
    try {
      const updated = await api.updateMapping(selectedMapping.id, {
        transform: editForm.transform,
        validation: editForm.validation,
        status: 'manual',
      });
      setMappings(m => m.map(x => x.id === selectedMapping.id ? { ...x, ...updated, status: 'manual' } : x));
      setEditModalOpen(false);
    } catch (err) {
      // Optimistic update if API fails
      setMappings(m => m.map(x => x.id === selectedMapping.id ? {
        ...x,
        transform: editForm.transform,
        validation: editForm.validation,
        status: 'manual',
      } : x));
      setEditModalOpen(false);
    } finally {
      setEditSaving(false);
    }
  };

  const filteredMappings = mappings.filter(m => !search || matchesSmartQuery(
    `${m.source_field || ''} ${m.target_field || ''}`,
    search,
    searchUseRegex,
  ));

  const autoCount = mappings.filter(m => m.status === 'auto').length;
  const manualCount = mappings.filter(m => m.status === 'manual').length;
  const unmappedCount = (sourceFields.length || mappings.length) - autoCount - manualCount;

  const inputStyle = {
    width: '100%',
    background: 'var(--bg-input)',
    border: '1px solid var(--border-main)',
    borderRadius: 8,
    color: 'var(--text-primary)',
    padding: '8px 12px',
    fontSize: 13,
    outline: 'none',
    fontFamily: 'inherit',
    boxSizing: 'border-box',
  };

  return (
    <div style={{ padding: '28px 32px', minHeight: '100%' }}>
      <PageHeader
        breadcrumb={['Projects', 'Model Mapping']}
        title="Model Mapping"
        description="Map source fields to target fields. Auto-detect or edit manually."
        action={{
          label: autoMapping ? 'Mapping…' : 'Run Auto-Map',
          icon: autoMapping ? <RefreshCw size={14} className="animate-spin" /> : <Zap size={14} />,
          onClick: handleAutoMap,
        }}
      />

      {/* Source / Target info cards */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 24 }}>
        {[
          { label: 'SOURCE MODEL', model: sourceModel, iconBg: 'var(--color-success-muted)', iconColor: 'var(--color-success)' },
          { label: 'TARGET MODEL',  model: targetModel,  iconBg: 'var(--color-accent-faint)',  iconColor: 'var(--accent-blue)' },
        ].map(({ label, model, iconBg, iconColor }) => (
          <div
            key={label}
            className="flex items-center gap-4 rounded-xl"
            style={{
              background: 'var(--bg-surface)',
              border: '1px solid var(--border-main)',
              padding: '16px 20px',
            }}
          >
            <div
              className="flex items-center justify-center rounded-xl"
              style={{ width: 44, height: 44, background: iconBg, color: iconColor, fontSize: 22, flexShrink: 0 }}
            >
              {label === 'SOURCE MODEL' ? '📦' : '🎯'}
            </div>
            <div className="min-w-0">
              <p style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-tertiary)', letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 2 }}>
                {label}
              </p>
              <p className="text-primary font-semibold truncate" style={{ fontSize: 14 }}>{model.name}</p>
              <p className="text-tertiary" style={{ fontSize: 11 }}>{model.type} · {model.field_count} fields</p>
            </div>
          </div>
        ))}
      </div>

      {/* Summary bar */}
      <div className="flex items-center gap-4 mb-4">
        <div className="flex items-center gap-2">
          <span
            className="rounded-full font-bold text-xs px-2 py-0.5"
            style={{ background: 'var(--color-success-muted)', color: 'var(--color-success)', letterSpacing: '0.04em' }}
          >
            {autoCount} AUTO
          </span>
          <span
            className="rounded-full font-bold text-xs px-2 py-0.5"
            style={{ background: 'var(--color-warning-muted)', color: 'var(--color-warning)', letterSpacing: '0.04em' }}
          >
            {manualCount} MANUAL
          </span>
          <span
            className="rounded-full font-bold text-xs px-2 py-0.5"
            style={{ background: 'var(--bg-surface-raised)', color: 'var(--text-tertiary)', letterSpacing: '0.04em' }}
          >
            {unmappedCount > 0 ? unmappedCount : 0} UNMAPPED
          </span>
        </div>
        <div className="flex-1" />
        <SearchInput
          value={search}
          onChange={setSearch}
          useRegex={searchUseRegex}
          onToggleRegex={setSearchUseRegex}
          allowRegex
          helperText={searchUseRegex ? 'Regex examples: ^cust_.* or amount|revenue' : 'Tip: enable regex to use patterns like ^cust_.*'}
          placeholder="Search fields…"
          width={240}
        />
      </div>

      {/* Mapping table */}
      {loading ? (
        <div className="flex items-center justify-center" style={{ padding: '80px 0' }}>
          <span className="text-tertiary text-sm">Loading mappings…</span>
        </div>
      ) : (
        <div className="rounded-xl overflow-hidden" style={{ border: '1px solid var(--border-main)' }}>
          {/* Table header */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 40px 1fr 100px 60px',
              gap: 0,
              background: 'var(--bg-surface-raised)',
              borderBottom: '1px solid var(--border-main)',
              padding: '10px 14px',
            }}
          >
            {['Source Field', '', 'Target Field', 'Status', ''].map((h, i) => (
              <span key={i} style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.06em', textAlign: i === 3 ? 'center' : 'left' }}>
                {h}
              </span>
            ))}
          </div>

          {filteredMappings.length === 0 ? (
            <div className="flex items-center justify-center" style={{ padding: 40 }}>
              <span className="text-tertiary text-sm">No mappings found.</span>
            </div>
          ) : (
            filteredMappings.map((m, i) => (
              <div
                key={m.id ?? i}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr 40px 1fr 100px 60px',
                  gap: 0,
                  padding: '11px 14px',
                  borderBottom: i < filteredMappings.length - 1 ? '1px solid var(--border-main)' : 'none',
                  alignItems: 'center',
                  transition: 'background 0.15s',
                }}
                onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-surface-hover)'; }}
                onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
              >
                {/* Source field */}
                <div className="flex items-center gap-2">
                  <GripVertical size={12} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />
                  <span className="text-primary text-sm font-medium">{m.source_field}</span>
                  <TypeBadge type={m.source_type} />
                </div>

                {/* Arrow */}
                <div className="flex items-center justify-center">
                  <Link size={13} style={{ color: m.status === 'auto' ? 'var(--color-success)' : m.status === 'manual' ? 'var(--color-warning)' : 'var(--text-tertiary)' }} />
                </div>

                {/* Target field */}
                <div className="flex items-center gap-2">
                  <span className="text-primary text-sm font-medium">{m.target_field || '—'}</span>
                  {m.target_type && <TypeBadge type={m.target_type} />}
                </div>

                {/* Status */}
                <div style={{ textAlign: 'center' }}>
                  <StatusBadge
                    status={m.status === 'auto' ? 'success' : m.status === 'manual' ? 'warning' : 'draft'}
                    label={m.status === 'auto' ? 'Auto' : m.status === 'manual' ? 'Manual' : 'Unmapped'}
                    size="sm"
                  />
                </div>

                {/* Edit action */}
                <div className="flex items-center justify-end">
                  <button
                    onClick={() => openEditModal(m)}
                    className="flex items-center justify-center rounded-md theme-transition"
                    style={{
                      width: 28,
                      height: 28,
                      color: 'var(--text-tertiary)',
                      background: 'transparent',
                      border: 'none',
                      cursor: 'pointer',
                    }}
                    onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-surface-hover)'; e.currentTarget.style.color = 'var(--accent-blue)'; }}
                    onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-tertiary)'; }}
                    title="Edit mapping"
                  >
                    <Edit3 size={13} />
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {/* Edit Mapping Modal */}
      <Modal
        open={editModalOpen}
        onClose={() => setEditModalOpen(false)}
        title="Edit Mapping"
        footer={
          <>
            <button
              onClick={() => setEditModalOpen(false)}
              className="rounded-lg text-sm font-medium px-4 py-2"
              style={{ background: 'transparent', border: '1px solid var(--border-main)', color: 'var(--text-secondary)', cursor: 'pointer' }}
            >
              Cancel
            </button>
            <button
              onClick={handleSaveEdit}
              disabled={editSaving}
              className="flex items-center gap-2 rounded-lg text-sm font-semibold px-4 py-2"
              style={{
                background: editSaving ? 'var(--bg-surface-raised)' : 'var(--accent-blue)',
                color: editSaving ? 'var(--text-tertiary)' : '#fff',
                border: 'none',
                cursor: editSaving ? 'not-allowed' : 'pointer',
              }}
            >
              {editSaving ? <RefreshCw size={12} className="animate-spin" /> : <Check size={12} />}
              Apply Mapping
            </button>
          </>
        }
      >
        {selectedMapping && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            {/* Field info */}
            <div
              className="flex items-center gap-3 rounded-lg"
              style={{ background: 'var(--bg-surface-raised)', padding: '12px 14px' }}
            >
              <div>
                <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginBottom: 2 }}>Source</p>
                <div className="flex items-center gap-2">
                  <span className="text-primary text-sm font-semibold">{selectedMapping.source_field}</span>
                  <TypeBadge type={selectedMapping.source_type} />
                </div>
              </div>
              <ArrowRight size={16} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />
              <div>
                <p style={{ fontSize: 11, color: 'var(--text-tertiary)', marginBottom: 2 }}>Target</p>
                <div className="flex items-center gap-2">
                  <span className="text-primary text-sm font-semibold">{selectedMapping.target_field || '—'}</span>
                  {selectedMapping.target_type && <TypeBadge type={selectedMapping.target_type} />}
                </div>
              </div>
            </div>

            {/* Transform */}
            <div>
              <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 6 }}>
                Transformation Logic
              </label>
              <textarea
                value={editForm.transform}
                onChange={e => setEditForm(f => ({ ...f, transform: e.target.value }))}
                placeholder="e.g. TRIM(LOWER(source.field_name))"
                rows={3}
                style={{
                  ...inputStyle,
                  fontFamily: 'monospace',
                  fontSize: 12,
                  resize: 'vertical',
                }}
                onFocus={e => { e.target.style.borderColor = 'var(--accent-blue)'; }}
                onBlur={e => { e.target.style.borderColor = 'var(--border-main)'; }}
              />
            </div>

            {/* Validation */}
            <div>
              <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 6 }}>
                Validation Rule
              </label>
              <select
                value={editForm.validation}
                onChange={e => setEditForm(f => ({ ...f, validation: e.target.value }))}
                style={{ ...inputStyle, cursor: 'pointer' }}
              >
                {VALIDATION_RULES.map(r => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>

            {selectedMapping.status === 'auto' && (
              <div
                className="flex items-start gap-2 rounded-lg"
                style={{ background: 'var(--color-warning-muted)', padding: '10px 12px', fontSize: 12, color: 'var(--color-warning)' }}
              >
                <Zap size={13} style={{ flexShrink: 0, marginTop: 1 }} />
                Saving will override the auto-detected mapping and lock this field as manual.
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
}

/* ── Demo data generator (used when API is unavailable) ── */
function generateDemoData() {
  const src = [
    { name: 'transaction_id', type: 'UUID' },
    { name: 'amount', type: 'Decimal' },
    { name: 'customer_ref', type: 'String' },
    { name: 'created_at', type: 'Timestamp' },
    { name: 'status_code', type: 'Integer' },
    { name: 'contact_email', type: 'String' },
    { name: 'region_id', type: 'Integer' },
    { name: 'product_sku', type: 'String' },
  ];
  const tgt = [
    { name: 'txn_id', type: 'VARCHAR' },
    { name: 'sale_amount', type: 'FLOAT' },
    { name: 'customer_key', type: 'VARCHAR' },
    { name: 'sale_date', type: 'DATE' },
    { name: 'order_status', type: 'INTEGER' },
    { name: 'email_address', type: 'VARCHAR' },
    { name: 'region_key', type: 'INTEGER' },
  ];
  const statuses = ['auto', 'auto', 'auto', 'auto', 'manual', 'manual', 'auto', null];
  const mappings = src.map((s, i) => ({
    id: `m_${i}`,
    source_field: s.name,
    source_type: s.type,
    target_field: tgt[i]?.name ?? null,
    target_type: tgt[i]?.type ?? null,
    status: statuses[i],
    transform: '',
    validation: 'None',
  }));
  return { source: src, target: tgt, mappings };
}