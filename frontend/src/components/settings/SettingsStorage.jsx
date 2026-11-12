import { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { History, RefreshCw } from 'lucide-react';
import { api } from '../../utils/api';
import { SectionHeader, Card, ActionButton } from './settingsUi';

const MAX_PROJECTS = 50;

async function getJson(url) {
  const res = await api.apiFetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

function formatSize(kb) {
  const n = Number(kb || 0);
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} GB`;
  if (n >= 1024) return `${(n / 1024).toFixed(1)} MB`;
  return `${Math.round(n)} KB`;
}

function describePolicy(policy) {
  if (!policy || policy.strategy === 'unlimited') return 'Keep everything';
  if (policy.strategy === 'count' && policy.max_snapshots_per_connector) {
    return `Keep last ${policy.max_snapshots_per_connector}`;
  }
  if (policy.strategy === 'days' && policy.max_age_days) {
    return `Keep ${policy.max_age_days} days`;
  }
  return policy.strategy;
}

/**
 * Settings › Storage & history — how many saved versions each project keeps,
 * how much space they use and the retention rule that prunes them.
 * Read-only; versions themselves are managed on the History page.
 */
export default function SettingsStorage() {
  const navigate = useNavigate();
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    (async () => {
      let projects = [];
      try {
        projects = await api.listProjects({ limit: 200 });
      } catch {
        projects = [];
      }
      const list = (Array.isArray(projects) ? projects : [])
        .slice()
        .sort((a, b) => new Date(b?.updated_at || 0) - new Date(a?.updated_at || 0));
      const scoped = list.slice(0, MAX_PROJECTS);
      const results = await Promise.all(scoped.map(async (p) => {
        const id = p.id || p.project_id;
        const enc = encodeURIComponent(id);
        const [stats, policy] = await Promise.allSettled([
          getJson(`/version-control/stats?project_id=${enc}`),
          getJson(`/version-control/retention-policy?project_id=${enc}`),
        ]);
        return {
          id,
          name: p.name || id,
          versions: stats.status === 'fulfilled' ? Number(stats.value?.snapshot_count || 0) : null,
          sizeKb: stats.status === 'fulfilled' ? Number(stats.value?.estimated_size_kb || 0) : null,
          policy: policy.status === 'fulfilled' && policy.value?.status === 'success' ? policy.value.policy : null,
        };
      }));
      if (!active) return;
      setRows(results);
      setTotal(list.length);
      setLoading(false);
    })();
    return () => { active = false; };
  }, [reloadKey]);

  const totals = useMemo(() => rows.reduce((acc, r) => ({
    versions: acc.versions + (r.versions || 0),
    sizeKb: acc.sizeKb + (r.sizeKb || 0),
  }), { versions: 0, sizeKb: 0 }), [rows]);

  const th = { textAlign: 'left', padding: '10px 14px', fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.05em' };
  const td = { padding: '10px 14px', fontSize: 13, color: 'var(--text-primary)', borderTop: '1px solid var(--border-main)' };

  return (
    <div>
      <SectionHeader
        title="Storage & history"
        description="Every run saves a version of the model so you can compare and restore. Old versions are pruned by each project's retention rule; pinned versions and versions used by a run are never removed."
        action={(
          <div style={{ display: 'flex', gap: 8 }}>
            <ActionButton onClick={() => { setLoading(true); setReloadKey((k) => k + 1); }} disabled={loading} aria-label="Refresh">
              <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            </ActionButton>
            <ActionButton onClick={() => navigate('/history')}>
              <History size={13} /> Open History
            </ActionButton>
          </div>
        )}
      />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 12, marginBottom: 16 }}>
        <Stat label="Saved versions" value={loading ? '…' : totals.versions.toLocaleString()} />
        <Stat label="Space used" value={loading ? '…' : formatSize(totals.sizeKb)} />
        <Stat label="Projects" value={loading ? '…' : total.toLocaleString()} />
      </div>

      <Card style={{ padding: 0, overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ background: 'var(--bg-surface-raised)' }}>
              <th style={th}>Project</th>
              <th style={{ ...th, textAlign: 'right' }}>Versions</th>
              <th style={{ ...th, textAlign: 'right' }}>Size</th>
              <th style={th}>Retention</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={4} style={{ ...td, color: 'var(--text-tertiary)' }}>Loading…</td></tr>
            ) : rows.length === 0 ? (
              <tr><td colSpan={4} style={{ ...td, color: 'var(--text-tertiary)' }}>No projects yet.</td></tr>
            ) : rows.map((r) => (
              <tr key={r.id}>
                <td style={{ ...td, fontWeight: 600 }}>{r.name}</td>
                <td style={{ ...td, textAlign: 'right' }}>{r.versions ?? '—'}</td>
                <td style={{ ...td, textAlign: 'right', color: 'var(--text-secondary)' }}>{r.sizeKb == null ? '—' : formatSize(r.sizeKb)}</td>
                <td style={{ ...td, color: 'var(--text-secondary)' }}>{describePolicy(r.policy)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      {!loading && total > MAX_PROJECTS && (
        <p style={{ fontSize: 12, color: 'var(--text-tertiary)', margin: '8px 2px 0' }}>
          Showing the {MAX_PROJECTS} most recently updated of {total} projects.
        </p>
      )}
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div style={{ padding: '12px 14px', borderRadius: 10, border: '1px solid var(--border-main)', background: 'var(--bg-surface)' }}>
      <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '.05em' }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--text-primary)', marginTop: 4 }}>{value}</div>
    </div>
  );
}
