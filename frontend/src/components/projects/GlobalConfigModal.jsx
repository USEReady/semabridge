import { useEffect, useState } from 'react';
import { X, Save, Loader2, FileCode2, SlidersHorizontal } from 'lucide-react';
import { api } from '../../utils/api';

const INPUT = {
  display: 'block', width: '100%',
  background: 'var(--bg-input)', border: '1px solid var(--border-main)',
  borderRadius: 8, color: 'var(--text-primary)', padding: '8px 12px',
  fontSize: 13, outline: 'none', fontFamily: 'inherit', boxSizing: 'border-box',
};

const LABEL = { display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 6 };

/**
 * GlobalConfigModal — edits ONLY connector sections of global config.yaml
 * via /api/config/global/connectors
 */
export default function GlobalConfigModal({ open, onClose }) {
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [mode, setMode] = useState('form'); // form | yaml
  const [yamlText, setYamlText] = useState('');
  const [form, setForm] = useState({
    fabric: { workspace_id: '' },
    snowflake: { account: '', warehouse: '', database: '', schema: '', role: '' },
  });

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    api.getGlobalConnectorConfig()
      .then(cfg => {
        const next = {
          fabric: { workspace_id: cfg?.fabric?.workspace_id || '' },
          snowflake: {
            account: cfg?.snowflake?.account || '',
            warehouse: cfg?.snowflake?.warehouse || '',
            database: cfg?.snowflake?.database || '',
            schema: cfg?.snowflake?.schema || '',
            role: cfg?.snowflake?.role || '',
          },
        };
        setForm(next);
        setYamlText(buildYaml(next));
      })
      .finally(() => setLoading(false));
  }, [open]);

  const buildYaml = (f) => [
    'fabric:',
    `  workspace_id: "${f.fabric.workspace_id || ''}"`,
    'snowflake:',
    `  account: "${f.snowflake.account || ''}"`,
    `  warehouse: "${f.snowflake.warehouse || ''}"`,
    `  database: "${f.snowflake.database || ''}"`,
    `  schema: "${f.snowflake.schema || ''}"`,
    `  role: "${f.snowflake.role || ''}"`,
  ].join('\n');

  const parseYaml = (text) => {
    const get = (re) => (text.match(re)?.[1] || '').replace(/["']/g, '').trim();
    return {
      fabric: { workspace_id: get(/^\s*workspace_id:\s*(.+)$/m) },
      snowflake: {
        account: get(/^\s*account:\s*(.+)$/m),
        warehouse: get(/^\s*warehouse:\s*(.+)$/m),
        database: get(/^\s*database:\s*(.+)$/m),
        schema: get(/^\s*schema:\s*(.+)$/m),
        role: get(/^\s*role:\s*(.+)$/m),
      },
    };
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const payload = mode === 'yaml' ? parseYaml(yamlText) : form;
      await api.saveGlobalConnectorConfig(payload);
      onClose();
    } finally {
      setSaving(false);
    }
  };

  if (!open) return null;

  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 1200,
        background: 'var(--bg-backdrop)', backdropFilter: 'blur(4px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24,
      }}
      onClick={e => e.target === e.currentTarget && onClose()}
    >
      <div style={{
        width: '100%', maxWidth: 760, maxHeight: '84vh',
        background: 'var(--bg-surface)', border: '1px solid var(--border-main)', borderRadius: 12,
        display: 'flex', flexDirection: 'column', overflow: 'hidden',
        boxShadow: '0 16px 48px rgba(0,0,0,0.22)',
      }}>
        <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border-main)', display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)' }}>Global Connector Config</div>
            <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 2 }}>
              Connector fields only (fabric and snowflake)
            </div>
          </div>
          <div style={{ display: 'flex', border: '1px solid var(--border-main)', borderRadius: 8, overflow: 'hidden' }}>
            <ModeBtn active={mode === 'form'} onClick={() => setMode('form')} icon={<SlidersHorizontal size={12} />} label="Form" />
            <ModeBtn active={mode === 'yaml'} onClick={() => setMode('yaml')} icon={<FileCode2 size={12} />} label="YAML" />
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)' }}>
            <X size={17} />
          </button>
        </div>

        <div style={{ flex: 1, overflow: 'auto', padding: 18 }}>
          {loading ? (
            <div style={{ textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13, padding: '60px 0' }}>
              Loading connector config...
            </div>
          ) : mode === 'form' ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
              <section>
                <h4 style={{ margin: '0 0 10px', fontSize: 13, color: 'var(--text-primary)' }}>Fabric</h4>
                <label style={LABEL}>Workspace ID</label>
                <input
                  value={form.fabric.workspace_id}
                  onChange={e => setForm(prev => ({ ...prev, fabric: { ...prev.fabric, workspace_id: e.target.value } }))}
                  style={INPUT}
                  placeholder="Default Fabric workspace ID"
                />
              </section>

              <section>
                <h4 style={{ margin: '0 0 10px', fontSize: 13, color: 'var(--text-primary)' }}>Snowflake</h4>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                  <div>
                    <label style={LABEL}>Account</label>
                    <input value={form.snowflake.account} onChange={e => setForm(prev => ({ ...prev, snowflake: { ...prev.snowflake, account: e.target.value } }))} style={INPUT} placeholder="xy12345.us-east-1" />
                  </div>
                  <div>
                    <label style={LABEL}>Warehouse</label>
                    <input value={form.snowflake.warehouse} onChange={e => setForm(prev => ({ ...prev, snowflake: { ...prev.snowflake, warehouse: e.target.value } }))} style={INPUT} placeholder="COMPUTE_WH" />
                  </div>
                  <div>
                    <label style={LABEL}>Database</label>
                    <input value={form.snowflake.database} onChange={e => setForm(prev => ({ ...prev, snowflake: { ...prev.snowflake, database: e.target.value } }))} style={INPUT} placeholder="ANALYTICS_DB" />
                  </div>
                  <div>
                    <label style={LABEL}>Schema</label>
                    <input value={form.snowflake.schema} onChange={e => setForm(prev => ({ ...prev, snowflake: { ...prev.snowflake, schema: e.target.value } }))} style={INPUT} placeholder="PUBLIC" />
                  </div>
                </div>
                <div style={{ marginTop: 12 }}>
                  <label style={LABEL}>Role</label>
                  <input value={form.snowflake.role} onChange={e => setForm(prev => ({ ...prev, snowflake: { ...prev.snowflake, role: e.target.value } }))} style={INPUT} placeholder="ANALYST_ROLE" />
                </div>
              </section>
            </div>
          ) : (
            <textarea
              value={yamlText}
              onChange={e => setYamlText(e.target.value)}
              style={{
                width: '100%', minHeight: 420, background: 'var(--bg-input)', color: 'var(--text-primary)',
                border: '1px solid var(--border-main)', borderRadius: 8, padding: 12,
                fontSize: 12, lineHeight: 1.55,
                fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
                outline: 'none', boxSizing: 'border-box',
              }}
            />
          )}
        </div>

        <div style={{ padding: '12px 18px', borderTop: '1px solid var(--border-main)', display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button onClick={onClose} style={secondaryBtn}>Cancel</button>
          <button onClick={handleSave} disabled={saving} style={primaryBtn}>
            {saving ? <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} /> : <Save size={13} />}
            Save
          </button>
        </div>
      </div>
    </div>
  );
}

function ModeBtn({ active, onClick, icon, label }) {
  return (
    <button
      onClick={onClick}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 5,
        padding: '6px 10px', border: 'none', cursor: 'pointer',
        background: active ? 'var(--accent-blue)' : 'transparent',
        color: active ? '#fff' : 'var(--text-secondary)',
        fontSize: 11, fontWeight: 600,
      }}
    >
      {icon} {label}
    </button>
  );
}

const primaryBtn = {
  display: 'inline-flex', alignItems: 'center', gap: 6,
  padding: '8px 14px', borderRadius: 8, border: 'none',
  background: 'var(--accent-blue)', color: '#fff', fontSize: 13, fontWeight: 600, cursor: 'pointer',
};

const secondaryBtn = {
  display: 'inline-flex', alignItems: 'center', gap: 6,
  padding: '8px 14px', borderRadius: 8, border: '1px solid var(--border-main)',
  background: 'transparent', color: 'var(--text-secondary)', fontSize: 13, fontWeight: 600, cursor: 'pointer',
};