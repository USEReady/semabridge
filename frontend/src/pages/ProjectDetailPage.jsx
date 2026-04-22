import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { 
  ArrowLeft, Edit3, Trash2, Play, Calendar, 
  Clock, Tag, Info, Layers, ExternalLink,
  ChevronRight, BarChart3, Cloud, Snowflake, Database
} from 'lucide-react';
import { api } from '../utils/api';
import StatusBadge from '../components/common/StatusBadge';
import PageHeader from '../components/common/PageHeader';

function normalizeSourceKey(value) {
  if (!value) return '';
  let key = '';
  if (typeof value === 'string') key = value.toLowerCase().trim();
  else if (typeof value === 'object') {
    key = String(value.type || value.adapter || value.source || value.source_type || value.connector || '').toLowerCase().trim();
  }
  if (key.includes('pbix') || key.includes('powerbi') || key === 'pbi') return 'pbix';
  if (key.includes('fabric')) return 'fabric';
  if (key.includes('snowflake')) return 'snowflake';
  if (key.includes('databricks')) return 'databricks';
  return key;
}

function renderSourceIcon(sourceType, size = 18) {
  const source = normalizeSourceKey(sourceType);
  if (source.includes('pbix')) return <BarChart3 size={size} color="#F2C811" />;
  if (source.includes('fabric')) return <Cloud size={size} color="#3b82f6" />;
  if (source.includes('snowflake')) return <Snowflake size={size} color="#38bdf8" />;
  if (source.includes('databricks')) return <Database size={size} color="#f97316" />;
  return <Layers size={size} color="var(--text-tertiary)" />;
}

export default function ProjectDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [project, setProject] = useState(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    const fetchProject = async () => {
      try {
        const data = await api.getProject(id);
        setProject(data);
      } catch (err) {
        console.error('Failed to fetch project:', err);
      } finally {
        setLoading(false);
      }
    };
    fetchProject();
  }, [id]);

  const handleRunNow = async () => {
    setRunning(true);
    try {
      await api.syncProject(id);
      navigate('/jobs');
    } catch (err) {
      alert(`Run failed: ${err.message}`);
    } finally {
      setRunning(false);
    }
  };

  const handleDelete = async () => {
    if (!window.confirm('Are you sure you want to delete this project?')) return;
    try {
      await api.deleteProject(id);
      navigate('/projects');
    } catch (err) {
      alert(`Delete failed: ${err.message}`);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-tertiary animate-pulse">Loading project details...</div>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4">
        <div className="text-secondary text-lg">Project not found</div>
        <button onClick={() => navigate('/projects')} className="text-accent hover:underline flex items-center gap-2">
          <ArrowLeft size={16} /> Back to Projects
        </button>
      </div>
    );
  }

  const sourceKey = normalizeSourceKey(project.source || project.adapter || project.source_type);

  return (
    <div style={{ padding: '28px 32px', maxWidth: 1200, margin: '0 auto' }}>
      {/* Breadcrumbs */}
      <nav className="flex items-center gap-2 mb-6 text-sm">
        <button onClick={() => navigate('/projects')} className="text-tertiary hover:text-primary transition-colors">Projects</button>
        <ChevronRight size={14} className="text-tertiary" />
        <span className="text-primary font-medium truncate">{project.name}</span>
      </nav>

      {/* Header Section */}
      <div className="flex flex-col md:flex-row md:items-start justify-between gap-6 mb-8">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 mb-2">
            <h1 className="text-2xl font-bold text-primary truncate" style={{ margin: 0 }}>
              {project.name}
            </h1>
            <StatusBadge status={project.status || 'draft'} />
          </div>
          <p className="text-secondary leading-relaxed max-w-3xl" style={{ fontSize: 15 }}>
            {project.description || 'No description provided for this project.'}
          </p>
        </div>

        <div className="flex items-center gap-3 flex-shrink-0">
          <button 
            onClick={handleRunNow}
            disabled={running}
            className="flex items-center gap-2 rounded-lg font-semibold px-5 py-2.5 transition-all"
            style={{ 
              background: 'var(--accent-blue)', 
              color: '#fff',
              opacity: running ? 0.7 : 1,
              cursor: running ? 'not-allowed' : 'pointer'
            }}
          >
            {running ? <Layers size={18} className="animate-spin" /> : <Play size={18} fill="currentColor" />}
            {running ? 'Running...' : 'Run Now'}
          </button>
          
          <button 
            onClick={() => navigate(`/projects/${id}/edit`)}
            className="flex items-center gap-2 rounded-lg font-semibold px-4 py-2.5 transition-all"
            style={{ 
              background: 'var(--bg-surface)', 
              border: '1px solid var(--border-main)',
              color: 'var(--text-primary)'
            }}
          >
            <Edit3 size={18} />
            Edit
          </button>

          <button 
            onClick={handleDelete}
            className="flex items-center justify-center rounded-lg p-2.5 transition-all text-tertiary hover:text-error hover:bg-error-faint border border-transparent hover:border-error-subtle"
          >
            <Trash2 size={18} />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Main Info Column */}
        <div className="lg:col-span-2 flex flex-col gap-8">
          
          {/* Tags Section */}
          <div className="bg-surface rounded-xl border border-main p-6">
            <div className="flex items-center gap-2 mb-4">
              <Tag size={16} className="text-accent" />
              <h2 className="text-primary font-semibold text-sm m-0">Labels & Tags</h2>
            </div>
            <div className="flex flex-wrap gap-2">
              {project.tags && project.tags.length > 0 ? (
                project.tags.map((tag, idx) => (
                  <span 
                    key={idx} 
                    className="px-3 py-1.5 rounded-lg text-xs font-semibold"
                    style={{ background: 'var(--accent-blue)15', color: 'var(--accent-blue)' }}
                  >
                    {tag}
                  </span>
                ))
              ) : (
                <span className="text-tertiary text-xs italic">No tags assigned</span>
              )}
            </div>
          </div>

          {/* Details Section */}
          <div className="bg-surface rounded-xl border border-main p-6">
            <div className="flex items-center gap-2 mb-4">
              <Info size={16} className="text-accent" />
              <h2 className="text-primary font-semibold text-sm m-0">Project Details</h2>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="flex flex-col gap-1">
                <span className="text-tertiary text-[11px] uppercase font-bold tracking-wider">Source Type</span>
                <div className="flex items-center gap-2 text-primary font-medium">
                  {renderSourceIcon(sourceKey)}
                  <span className="capitalize">{sourceKey || 'Not configured'}</span>
                </div>
              </div>
              <div className="flex flex-col gap-1">
                <span className="text-tertiary text-[11px] uppercase font-bold tracking-wider">Target Type</span>
                <div className="flex items-center gap-2 text-primary font-medium text-sm">
                   {project.target_type ? (
                     <>
                        {renderSourceIcon(project.target_type)}
                        <span className="capitalize">{project.target_type}</span>
                     </>
                   ) : 'Internal Semantic Store'}
                </div>
              </div>
              <div className="flex flex-col gap-1">
                <span className="text-tertiary text-[11px] uppercase font-bold tracking-wider">Workspace ID</span>
                <span className="text-primary font-mono text-xs">{project.workspace_id || 'N/A'}</span>
              </div>
              <div className="flex flex-col gap-1">
                <span className="text-tertiary text-[11px] uppercase font-bold tracking-wider">Created Date</span>
                <div className="flex items-center gap-2 text-secondary text-sm">
                  <Calendar size={14} />
                  {project.created_at ? new Date(project.created_at).toLocaleDateString() : 'N/A'}
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Sidebar Info Column */}
        <div className="flex flex-col gap-8">
           {/* Recent Activity Mini-Card */}
           <div className="bg-surface rounded-xl border border-main p-6">
            <h2 className="text-primary font-semibold text-sm mb-4">Status & Activity</h2>
            <div className="flex flex-col gap-4">
              <div className="flex items-center justify-between">
                <span className="text-secondary text-xs">Current Status</span>
                <StatusBadge status={project.status || 'draft'} size="sm" />
              </div>
              <div className="flex items-center justify-between">
                <span className="text-secondary text-xs">Last Run</span>
                <div className="flex items-center gap-1.5 text-xs text-primary font-medium">
                  <Clock size={12} />
                  {project.last_run_at ? new Date(project.last_run_at).toLocaleDateString() : 'Never'}
                </div>
              </div>
              <div className="mt-2">
                 <button 
                  onClick={() => navigate('/jobs')}
                  className="w-full flex items-center justify-center gap-2 text-xs font-semibold py-2 rounded-lg border border-main hover:bg-surface-raised transition-colors text-secondary"
                 >
                   View Run History <ExternalLink size={12} />
                 </button>
              </div>
            </div>
          </div>

        </div>
      </div>
    </div>
  );
}