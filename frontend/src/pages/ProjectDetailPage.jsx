/**
 * /projects/:id — one project page with tabs:
 * Overview · Runs · Mappings · Ontology · Settings (tab kept in ?tab=).
 * "Run now" is the only primary button.
 */
import { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate, useSearchParams, Link } from 'react-router-dom';
import { ArrowLeft, Play, Loader2, MoreHorizontal, Copy, Download, Trash2 } from 'lucide-react';
import { api } from '../utils/api';
import OntologyDomainManager from '../components/OntologyDomainManager';
import { ReverseSyncView } from './GovernanceSuitePage';
import ProjectConfigPage from './ProjectConfigPage';
import ProjectOverviewTab from '../components/project/ProjectOverviewTab';
import ProjectRunsTab from '../components/project/ProjectRunsTab';
import ProjectMappingsTab from '../components/project/ProjectMappingsTab';
import { useProjectPageData, runId, scheduleText } from '../components/project/projectData';
import { Pill, Chip } from '../components/project/projectUi';
import { btn, btnPrimary } from '../components/project/projectStyles';
import { useUIStore } from '../store/uiStore';

const TABS = [
  { key: 'overview', label: 'Overview' },
  { key: 'runs', label: 'Runs' },
  { key: 'mappings', label: 'Mappings' },
  { key: 'ontology', label: 'Ontology' },
  { key: 'reverse-sync', label: 'Reverse Sync' },
  { key: 'settings', label: 'Settings' },
];

const SOURCE_NAMES = {
  pbix: 'Power BI', powerbi: 'Power BI', pbi: 'Power BI', fabric: 'Fabric',
  snowflake: 'Snowflake', databricks: 'Databricks', tableau: 'Tableau',
};

function systemName(value) {
  if (!value) return null;
  const raw = typeof value === 'object'
    ? String(value.type || value.adapter || value.source || '')
    : String(value);
  const key = raw.toLowerCase().trim();
  if (!key) return null;
  const hit = Object.keys(SOURCE_NAMES).find(k => key.includes(k));
  return hit ? SOURCE_NAMES[hit] : raw;
}

function MoreMenu({ onDuplicate, onExport, onDelete }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);
  const item = {
    display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '8px 10px', border: 'none',
    background: 'transparent', color: 'var(--text-primary)', fontSize: 13, cursor: 'pointer', borderRadius: 6, textAlign: 'left',
  };
  const pick = (fn) => () => { setOpen(false); fn(); };
  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button type="button" style={{ ...btn, padding: '7px 9px' }} aria-label="More actions" aria-expanded={open} onClick={() => setOpen(o => !o)}>
        <MoreHorizontal size={16} />
      </button>
      {open && (
        <div role="menu" style={{
          position: 'absolute', right: 0, top: 'calc(100% + 6px)', zIndex: 50, width: 180, padding: 4,
          background: 'var(--bg-surface)', border: '1px solid var(--border-main)', borderRadius: 10, boxShadow: 'var(--shadow-md)',
        }}>
          <button type="button" role="menuitem" style={item} onClick={pick(onDuplicate)}><Copy size={14} /> Duplicate</button>
          <button type="button" role="menuitem" style={item} onClick={pick(onExport)}><Download size={14} /> Export</button>
          <div style={{ height: 1, background: 'var(--border-main)', margin: '4px 0' }} />
          <button type="button" role="menuitem" style={{ ...item, color: 'var(--color-error)' }} onClick={pick(onDelete)}><Trash2 size={14} /> Delete</button>
        </div>
      )}
    </div>
  );
}

export default function ProjectDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  // Keyed by id so "loading" is derived rather than reset inside the effect.
  const [loaded, setLoaded] = useState({ id: null, project: null });
  const [running, setRunning] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get('tab');
  const tab = TABS.some(t => t.key === tabParam) ? tabParam : 'overview';
  const setActiveProjectId = useUIStore(state => state.setActiveProjectId);

  useEffect(() => {
    let cancelled = false;
    api.getProject(id)
      .then((p) => {
        if (cancelled) return;
        setLoaded({ id, project: p });
        setActiveProjectId(p?.id || id);
      })
      .catch((err) => {
        console.error('Failed to fetch project:', err);
        if (!cancelled) setLoaded({ id, project: null });
      });
    return () => { cancelled = true; };
  }, [id, setActiveProjectId]);
  const loading = loaded.id !== id;
  const project = loading ? null : loaded.project;

  const data = useProjectPageData(id, project);
  const { runs, summaries, schedule } = data;

  const setTab = (key) => {
    const next = new URLSearchParams(searchParams);
    if (key === 'overview') next.delete('tab'); else next.set('tab', key);
    setSearchParams(next, { replace: true });
  };

  const handleRunNow = async () => {
    setRunning(true);
    try {
      await api.syncProject(id);
      navigate('/runs');
    } catch (err) {
      alert(`Run failed: ${err.message}`);
    } finally {
      setRunning(false);
    }
  };

  const handleDelete = async () => {
    if (!window.confirm(`Delete project "${project?.name || id}"?`)) return;
    try {
      await api.deleteProject(id);
      navigate('/projects');
    } catch (err) {
      alert(`Delete failed: ${err.message}`);
    }
  };

  const handleDuplicate = async () => {
    try {
      await api.createProject({
        name: `${project.name} (copy)`,
        description: project.description,
        source: project.source ? { type: project.source, workspace_id: project.workspace_id } : undefined,
        target: project.target_type ? { type: project.target_type } : undefined,
      });
      navigate('/projects');
    } catch (err) {
      alert(`Duplicate failed: ${err.message}`);
    }
  };

  const handleExport = async () => {
    try {
      await api.exportProject(id);
    } catch (err) {
      alert(`Export failed: ${err.message}`);
    }
  };

  if (loading) {
    return (
      <div style={{ padding: 36, color: 'var(--text-tertiary)', fontSize: 13, display: 'flex', alignItems: 'center', gap: 8 }}>
        <Loader2 size={14} className="animate-spin" /> Loading project…
      </div>
    );
  }

  if (!project) {
    return (
      <div style={{ padding: 36, display: 'flex', flexDirection: 'column', gap: 12, alignItems: 'flex-start' }}>
        <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)' }}>Project not found</div>
        <button type="button" style={btn} onClick={() => navigate('/projects')}><ArrowLeft size={14} /> Back to Projects</button>
      </div>
    );
  }

  const source = systemName(project.source || project.adapter || project.source_type || project.source_config);
  const target = systemName(project.target_type || project.target);
  // Latest run with a usable report decides the "to review" count.
  const reported = runs.map(r => summaries[runId(r)]).find(s => s && !s.crashed && s.counts);
  const toReview = Number(reported?.counts?.needs_review) || 0;
  const scheduleLine = scheduleText(schedule);

  return (
    <div style={{ padding: '24px 32px 40px', maxWidth: 1280, margin: '0 auto', width: '100%', boxSizing: 'border-box' }}>
      {/* Page head: name, route, review count, schedule, actions */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, color: 'var(--text-primary)', margin: 0, letterSpacing: '-0.01em', minWidth: 0, overflowWrap: 'anywhere' }}>
          {project.name}
        </h1>
        {(source || target) && (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <Chip>{source || '?'}</Chip>
            <span style={{ color: 'var(--text-tertiary)' }}>→</span>
            <Chip>{target || '?'}</Chip>
          </span>
        )}
        {toReview > 0 && (
          <button type="button" onClick={() => setTab('overview')} style={{ border: 'none', background: 'none', padding: 0, cursor: 'pointer' }} title="Measures from the last run that need a human check">
            <Pill tone="warn">{toReview.toLocaleString()} to review</Pill>
          </button>
        )}
        <span style={{ flex: 1 }} />
        {scheduleLine && <span style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>{scheduleLine}</span>}
        <button type="button" style={btn} onClick={() => navigate(`/projects/${id}/edit`)}>Edit</button>
        <MoreMenu onDuplicate={handleDuplicate} onExport={handleExport} onDelete={handleDelete} />
        <button type="button" style={{ ...btnPrimary, opacity: running ? 0.7 : 1, cursor: running ? 'not-allowed' : 'pointer' }} disabled={running} onClick={handleRunNow}>
          {running ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} fill="currentColor" />}
          {running ? 'Starting…' : 'Run now'}
        </button>
      </div>

      {/* Tabs */}
      <div role="tablist" style={{ display: 'flex', gap: 4, marginTop: 14, marginBottom: 18, borderBottom: '1px solid var(--border-main)', overflowX: 'auto' }}>
        {TABS.map(t => {
          const on = t.key === tab;
          return (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={on}
              onClick={() => setTab(t.key)}
              style={{
                padding: '8px 12px', marginBottom: -1, border: 'none', background: 'none', cursor: 'pointer', whiteSpace: 'nowrap',
                borderBottom: `2px solid ${on ? 'var(--accent-blue)' : 'transparent'}`,
                color: on ? 'var(--text-primary)' : 'var(--text-secondary)', fontSize: 13, fontWeight: on ? 600 : 500,
              }}
            >
              {t.label}
              {t.key === 'runs' && runs.length > 0 && <span style={{ marginLeft: 6, color: 'var(--text-tertiary)', fontWeight: 500 }}>{runs.length}</span>}
            </button>
          );
        })}
      </div>

      {tab === 'overview' && <ProjectOverviewTab project={project} {...data} />}
      {tab === 'runs' && <ProjectRunsTab {...data} />}
      {tab === 'mappings' && <ProjectMappingsTab projectId={id} />}
      {tab === 'ontology' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <Link to="/ontology" style={{ fontSize: 12, color: 'var(--accent-blue)', textDecoration: 'none' }}>Open the full Ontology page →</Link>
          </div>
          <OntologyDomainManager projectId={id} />
        </div>
      )}
      {tab === 'reverse-sync' && <ReverseSyncView projectId={id} />}
      {tab === 'settings' && <ProjectConfigPage embedded />}
    </div>
  );
}
