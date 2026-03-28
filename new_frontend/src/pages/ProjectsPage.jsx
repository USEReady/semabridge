/**
 * ProjectsPage — Folder-grouped projects with HP search, drag-drop, and import/export.
 */
import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  FolderOpen, Plus, MoreVertical, Layers, Trash2, Edit3,
  Upload, Download, Play, Settings, Copy, Folder, FolderPlus,
  X, Check,
} from 'lucide-react';

import StatusBadge from '../components/common/StatusBadge';
import EmptyState from '../components/common/EmptyState';
import ProjectDetailModal from '../components/projects/ProjectDetailModal';
import ImportProjectModal from '../components/projects/ImportProjectModal';
import SmartSearchBar, { matchesSmartQuery } from '../components/common/SmartSearchBar';
import { api } from '../utils/api';
import React, { useContext } from 'react';
import { SyncContext } from '../context/SyncContext';
import { DEFAULT_FILTER_OPTIONS, useUIStore } from '../store/uiStore';

const SOURCE_ICONS = {
  fabric: '🔷', snowflake: '❄️', databricks: '🧱', google_sheets: '📊',
};

/* Use CSS variables for folder colors - mapped to semantic status colors */
const FOLDER_COLORS = [
  'var(--accent-blue)',
  'var(--color-warning)',
  'var(--color-success)',
  'var(--color-error)',
  'var(--accent-purple)',
  'var(--accent-cyan)',
  'var(--accent-orange)',
];

/* ─── Main Page ─── */
export default function ProjectsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [menuOpen, setMenuOpen] = useState(null);
  const [detailProject, setDetailProject] = useState(null);
  const [filterPanelOpen, setFilterPanelOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [newFolderMode, setNewFolderMode] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const [renameFolderId, setRenameFolderId] = useState(null);
  const [renameFolderName, setRenameFolderName] = useState('');
  const [dragOverFolder, setDragOverFolder] = useState(null);
  const [runningProjectIds, setRunningProjectIds] = useState(new Set());
  const searchQuery = useUIStore(state => state.searchQuery);
  const setSearchQuery = useUIStore(state => state.setSearchQuery);
  const filterOptions = useUIStore(state => state.filterOptions);
  const setFilterOptions = useUIStore(state => state.setFilterOptions);
  const projectListScrollTop = useUIStore(state => state.projectListScrollTop);
  const setProjectListScrollTop = useUIStore(state => state.setProjectListScrollTop);
  const setActiveProjectId = useUIStore(state => state.setActiveProjectId);
  const selectedFolder = filterOptions?.selectedFolder ?? DEFAULT_FILTER_OPTIONS.selectedFolder;
  const sourceFilter = filterOptions?.sourceFilter ?? DEFAULT_FILTER_OPTIONS.sourceFilter;
  const targetFilter = filterOptions?.targetFilter ?? DEFAULT_FILTER_OPTIONS.targetFilter;
  const tagFilters = new Set(filterOptions?.tagFilters ?? DEFAULT_FILTER_OPTIONS.tagFilters);
  const viewMode = filterOptions?.viewMode ?? DEFAULT_FILTER_OPTIONS.viewMode;
  const useRegexSearch = filterOptions?.useRegexSearch ?? DEFAULT_FILTER_OPTIONS.useRegexSearch;

  const {
    data: projects = [],
    isLoading: projectsLoading,
    refetch: refetchProjects,
  } = useQuery({
    queryKey: ['projects'],
    queryFn: api.listProjects,
  });

  const {
    data: folders = [],
    isLoading: foldersLoading,
    refetch: refetchFolders,
  } = useQuery({
    queryKey: ['folders'],
    queryFn: api.listFolders,
  });

  const loading = projectsLoading || foldersLoading;

  const setProjects = useCallback((updater) => {
    queryClient.setQueryData(['projects'], (current = []) => (
      typeof updater === 'function' ? updater(current) : updater
    ));
  }, [queryClient]);

  const setFolders = useCallback((updater) => {
    queryClient.setQueryData(['folders'], (current = []) => (
      typeof updater === 'function' ? updater(current) : updater
    ));
  }, [queryClient]);

  const refreshData = useCallback(async () => {
    await Promise.all([refetchProjects(), refetchFolders()]);
  }, [refetchProjects, refetchFolders]);

  useEffect(() => {
    const container = document.getElementById('projects-grid-scroll');
    if (!container || !Number.isFinite(projectListScrollTop)) return;
    container.scrollTop = projectListScrollTop;
  }, [projectListScrollTop, loading]);

  const updateFilterOption = useCallback((key, value) => {
    setFilterOptions({ [key]: value });
  }, [setFilterOptions]);

  const allProjectTags = [...new Set(projects.flatMap(p => p.tags || []))];

  const folderFiltered = projects.filter(p => {
    // Handle both folder and source selection
    if (selectedFolder !== null) {
      const selectedFolderKey = String(selectedFolder);
      if (selectedFolderKey.startsWith('source:')) {
        const source = selectedFolderKey.substring(7);
        if ((p.source || p.adapter) !== source) return false;
      } else {
        if (String(p.folder_id ?? '') !== selectedFolderKey) return false;
      }
    }
    if (sourceFilter !== 'all' && (p.source || p.adapter) !== sourceFilter) return false;
    if (targetFilter !== 'all' && p.target_type !== targetFilter) return false;
    
    // Tag filtering - if tags are selected, project must have ALL selected tags
    if (tagFilters.size > 0) {
      const projectTags = new Set(p.tags || []);
      const hasAllTags = [...tagFilters].every(tag => projectTags.has(tag));
      if (!hasAllTags) return false;
    }
    
    // Filter out auto-generated test projects
    const projectName = (p.name || '').trim().toLowerCase();
    const isTestProject = (
      projectName === 'test' ||
      projectName === 'teste' ||
      projectName === 'test project' ||
      projectName === 'fabricmodel' ||
      p.isAutoGenerated === true
    );
    if (isTestProject) return false;
    
    return true;
  });

  const filtered = searchQuery.trim()
    ? folderFiltered.filter((p) => {
        const haystack = [
          p.name,
          p.description,
          p.source,
          p.adapter,
          p.workspace_id,
          p.target_type,
          ...(Array.isArray(p.tags) ? p.tags : []),
        ].join(' ');
        return matchesSmartQuery(haystack, searchQuery, useRegexSearch);
      })
    : folderFiltered;

  /* ── Folder actions ── */
  const handleCreateFolder = async () => {
    if (!newFolderName.trim()) return;
    const color = FOLDER_COLORS[folders.length % FOLDER_COLORS.length];
    const f = await api.createFolder({ name: newFolderName.trim(), color });
    setFolders(prev => [...prev, f]);
    setNewFolderMode(false);
    setNewFolderName('');
  };

  const handleRenameFolder = async (id) => {
    if (!renameFolderName.trim()) return;
    const updated = await api.renameFolder(id, { name: renameFolderName.trim() });
    setFolders(prev => prev.map(f => f.id === id ? { ...f, ...updated } : f));
    setRenameFolderId(null);
  };

  const handleDeleteFolder = async (id) => {
    if (!confirm('Delete folder? Projects will be moved to root.')) return;
    await api.deleteFolder(id);
    setFolders(prev => prev.filter(f => f.id !== id));
    if (selectedFolder === id) updateFilterOption('selectedFolder', null);
    setProjects(prev => prev.map(p => p.folder_id === id ? { ...p, folder_id: null } : p));
  };

  /* ── Drag-and-drop ── */
  const handleDragStart = (e, projectId) => {
    e.dataTransfer.setData('projectId', String(projectId));
  };

  const handleDropOnFolder = async (e, folderId) => {
    e.preventDefault();
    setDragOverFolder(null);
    const projectId = e.dataTransfer.getData('projectId');
    if (!projectId) return;
    await api.moveProjectToFolder(projectId, folderId);
    setProjects(prev => prev.map(p => p.id === projectId ? { ...p, folder_id: folderId } : p));
  };

  /* ── Project actions ── */
  const handleDelete = async (project) => {
    if (!confirm(`Delete project "${project.name}"?`)) return;
    try {
      await api.deleteProject(project.id);
      setProjects(p => p.filter(x => x.id !== project.id));
      setMenuOpen(null);
    } catch (err) {
      console.error('Delete project failed:', err);
      alert(`Delete failed: ${err.message || 'Unknown error'}`);
      await refreshData();
    }
  };

  const handleDuplicate = async (project) => {
    await api.createProject({
      name: `${project.name} (copy)`,
      description: project.description,
      source: project.source ? { type: project.source, workspace_id: project.workspace_id } : undefined,
      target: project.target_type ? { type: project.target_type } : undefined,
    });
    await refreshData();
    setMenuOpen(null);
  };

  const handleExportSingle = async (project) => {
    await api.exportProject(project.id);
    setMenuOpen(null);
  };

  const handleExportAll = async () => {
    const ids = filtered.map(p => p.id);
    await api.exportProjectsBulk(ids);
  };

  const handleImported = useCallback(() => { refreshData(); }, [refreshData]);

  const handleRunNow = useCallback(async (projectId) => {
    if (!projectId) return;

    setRunningProjectIds(prev => {
      const next = new Set(prev);
      next.add(projectId);
      return next;
    });

    try {
      const result = await api.syncProject(projectId);
      const updatedProject = result?.project;
      if (updatedProject?.id || updatedProject?.project_id) {
        const pid = updatedProject.id || updatedProject.project_id;
        setProjects(prev => prev.map(p => (p.id === pid || p.project_id === pid) ? { ...p, ...updatedProject } : p));
      }
      // Fallback: force progress bar to 100% and status to 'success' if POST returns 200
      if (result && typeof window !== 'undefined' && window.dispatchEvent) {
        window.dispatchEvent(new CustomEvent('semabridge-sync-fallback', { detail: { projectId, status: 'success', progress: 100 } }));
      }
      return result;
    } catch (err) {
      console.error('Run project failed:', err);
      alert(`Run failed: ${err.message || 'Unknown error'}`);
      throw err;
    } finally {
      setRunningProjectIds(prev => {
        const next = new Set(prev);
        next.delete(projectId);
        return next;
      });
    }
  }, [setProjects]);

  /* ── Render ── */
  return (
    <div style={{ display: 'flex', flexDirection: 'row-reverse', height: '100%', overflow: 'hidden' }}>

      {/* ── Folder/Adapter Sidebar ── */}
      <aside style={{
        width: 220, flexShrink: 0,
        borderLeft: '1px solid var(--border-main)',
        borderRight: 'none',
        display: 'flex', flexDirection: 'column',
        padding: '16px 0',
        overflowY: 'auto',
      }}>
        <div style={{ padding: '0 12px', marginBottom: 8, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-tertiary)', letterSpacing: '0.06em', textTransform: 'uppercase' }}>
            {viewMode === 'folder' ? 'Folders' : 'Sources'}
          </span>
          <div style={{ display: 'flex', gap: 4 }}>
            <button
              onClick={() => updateFilterOption('viewMode', viewMode === 'folder' ? 'adapter' : 'folder')}
              title={viewMode === 'folder' ? 'View by source' : 'View by folder'}
              style={{
                background: 'none', border: 'none', cursor: 'pointer',
                color: 'var(--text-tertiary)', padding: 2,
                display: 'flex', alignItems: 'center',
              }}
            >
              <Layers size={13} />
            </button>
            {viewMode === 'folder' && (
              <button
                onClick={() => setNewFolderMode(true)}
                title="New folder"
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: 2 }}
              >
                <FolderPlus size={13} />
              </button>
            )}
          </div>
        </div>

        {/* All Projects */}
        <SidebarItem
          color="var(--text-tertiary)"
          label={`All Projects (${projects.length})`}
          active={selectedFolder === null}
          onClick={() => updateFilterOption('selectedFolder', null)}
        />

        {/* Folder list or Source list */}
        {viewMode === 'folder' ? (
          folders.map(f => (
          <SidebarFolder
            key={f.id}
            folder={f}
            projectCount={projects.filter(p => p.folder_id === f.id).length}
            active={selectedFolder === f.id}
            onClick={() => updateFilterOption('selectedFolder', f.id)}
            isRenaming={renameFolderId === f.id}
            renameValue={renameFolderName}
            onRenameChange={setRenameFolderName}
            onRenameSubmit={() => handleRenameFolder(f.id)}
            onRenameStart={() => { setRenameFolderId(f.id); setRenameFolderName(f.name); }}
            onDelete={() => handleDeleteFolder(f.id)}
            isDragOver={dragOverFolder === f.id}
            onDragOver={e => { e.preventDefault(); setDragOverFolder(f.id); }}
            onDragLeave={() => setDragOverFolder(null)}
            onDrop={e => handleDropOnFolder(e, f.id)}
          />
        ))
        ) : (
          // Adapter/Source view
          [...new Set(projects.map(p => p.source || p.adapter).filter(Boolean))]
            .sort()
            .map(source => {
              const sourceProjects = projects.filter(p => (p.source || p.adapter) === source);
              const sourceIcon = SOURCE_ICONS[source] || '🔗';
              return (
                <SidebarItem
                  key={source}
                  color="var(--accent-blue)"
                  label={`${sourceIcon} ${source} (${sourceProjects.length})`}
                  active={selectedFolder === `source:${source}`}
                  onClick={() => updateFilterOption('selectedFolder', `source:${source}`)}
                />
              );
            })
        )}

        {/* New folder input */}
        {viewMode === 'folder' && newFolderMode && (
          <div style={{ padding: '4px 10px', display: 'flex', alignItems: 'center', gap: 4 }}>
            <input
              autoFocus
              value={newFolderName}
              onChange={e => setNewFolderName(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter') handleCreateFolder();
                if (e.key === 'Escape') { setNewFolderMode(false); setNewFolderName(''); }
              }}
              placeholder="Folder name"
              style={{
                flex: 1, fontSize: 12, padding: '4px 8px',
                background: 'var(--bg-input)', border: '1px solid var(--accent-blue)',
                borderRadius: 5, color: 'var(--text-primary)', outline: 'none',
              }}
            />
            <button onClick={handleCreateFolder} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--accent-blue)' }}>
              <Check size={13} />
            </button>
            <button onClick={() => { setNewFolderMode(false); setNewFolderName(''); }} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)' }}>
              <X size={13} />
            </button>
          </div>
        )}

        {/* Drop-to-root zone */}
        <div
          style={{
            margin: '8px 10px 0', borderRadius: 6, border: '1px dashed var(--border-subtle)',
            padding: '6px 10px', fontSize: 11, color: 'var(--text-tertiary)', textAlign: 'center',
          }}
          onDragOver={e => e.preventDefault()}
          onDrop={async e => {
            e.preventDefault();
            const projectId = e.dataTransfer.getData('projectId');
            if (!projectId) return;
            await api.moveProjectToFolder(projectId, null);
            setProjects(prev => prev.map(p => p.id === projectId ? { ...p, folder_id: null } : p));
          }}
        >
          Drop here to remove from folder
        </div>
      </aside>

      {/* ── Main content ── */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {/* Header */}
        <div style={{ padding: '20px 28px 0', flexShrink: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
            <div>
              <h1 style={{ fontSize: 20, fontWeight: 700, color: 'var(--text-primary)', margin: 0 }}>
                {selectedFolder === null ? 'Projects' : (folders.find(f => f.id === selectedFolder)?.name ?? 'Folder')}
              </h1>
              <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: '4px 0 0' }}>
                {filtered.length} project{filtered.length !== 1 ? 's' : ''}
              </p>
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <button
                onClick={() => updateFilterOption('viewMode', viewMode === 'folder' ? 'adapter' : 'folder')}
                title={viewMode === 'folder' ? 'View by source' : 'View by folder'}
                style={{
                  display: 'flex', alignItems: 'center', gap: 6,
                  padding: '8px 14px', borderRadius: 6, cursor: 'pointer',
                  background: 'var(--accent-blue)12', border: '1.5px solid var(--accent-blue)40',
                  color: 'var(--accent-blue)', fontSize: 12, fontWeight: 500,
                  transition: 'all 0.2s',
                }}
                onMouseEnter={e => { e.currentTarget.style.background = 'var(--accent-blue)20'; e.currentTarget.style.borderColor = 'var(--accent-blue)60'; }}
                onMouseLeave={e => { e.currentTarget.style.background = 'var(--accent-blue)12'; e.currentTarget.style.borderColor = 'var(--accent-blue)40'; }}
              >
                <Layers size={13} />
                {viewMode === 'folder' ? 'By Source' : 'By Folder'}
              </button>
              <button onClick={() => setImportOpen(true)} style={btnStyle('secondary')}>
                <Upload size={13} /> Import
              </button>
              {filtered.length > 0 && (
                <button onClick={handleExportAll} style={btnStyle('secondary')}>
                  <Download size={13} /> Export All
                </button>
              )}
              <button onClick={() => navigate('/projects/new')} style={btnStyle('primary')}>
                <Plus size={13} /> New Project
              </button>
            </div>
          </div>

          {/* Search + filter bar */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
            <div style={{ flex: 1, maxWidth: 440 }}>
              <SmartSearchBar
                value={searchQuery}
                onChange={setSearchQuery}
                useRegex={useRegexSearch}
                onToggleRegex={(next) => updateFilterOption('useRegexSearch', next)}
                placeholder="Search by prefix or regex"
              />
            </div>
            <button
              onClick={() => setFilterPanelOpen(!filterPanelOpen)}
              style={{
                display: 'flex', alignItems: 'center', gap: 6,
                padding: '8px 14px', borderRadius: 6,
                background: (sourceFilter !== 'all' || targetFilter !== 'all' || tagFilters.size > 0) ? 'var(--accent-blue)' : 'var(--bg-surface)',
                border: (sourceFilter !== 'all' || targetFilter !== 'all' || tagFilters.size > 0) ? 'none' : '1px solid var(--border-main)',
                color: (sourceFilter !== 'all' || targetFilter !== 'all' || tagFilters.size > 0) ? '#fff' : 'var(--text-secondary)',
                cursor: 'pointer', fontSize: 12, fontWeight: 600, outline: 'none',
                transition: 'all 0.2s',
              }}
              onMouseEnter={e => { e.currentTarget.style.transform = 'scale(1.02)'; }}
              onMouseLeave={e => { e.currentTarget.style.transform = 'scale(1)'; }}
            >
              <Layers size={13} />
              Filters
              {(sourceFilter !== 'all' || targetFilter !== 'all' || tagFilters.size > 0) && (
                <div style={{ width: 6, height: 6, borderRadius: '50%', background: '#fff', animation: 'pulse 2s infinite' }} />
              )}
            </button>
            {/* Hidden - using consolidated filter panel instead */}
            {/* <FilterBar ... /> */}
          </div>

          {/* Consolidated Filter Panel */}
          {filterPanelOpen && (
            <div style={{
              marginBottom: 12, padding: '14px 16px', borderRadius: 8,
              background: 'var(--bg-surface)', border: '1.5px solid var(--accent-blue)40',
              boxShadow: 'var(--shadow-sm)',
            }}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 14 }}>
                {/* Source Filter */}
                <div>
                  <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', display: 'block', marginBottom: 6 }}>Source</label>
                  <select
                    value={sourceFilter}
                    onChange={e => updateFilterOption('sourceFilter', e.target.value)}
                    style={{
                      width: '100%', padding: '8px 10px', fontSize: 12, fontWeight: 500,
                      background: 'var(--bg-input)', border: '1px solid var(--border-main)',
                      borderRadius: 6, color: 'var(--text-primary)', outline: 'none',
                      cursor: 'pointer', transition: 'all 0.2s',
                    }}
                  >
                    <option value="all">All Sources</option>
                     <option value="fabric">MS Fabric</option>
                     <option value="snowflake">Snowflake</option>
                     <option value="databricks">Databricks</option>
                     <option value="postgresql">PostgreSQL</option>
                     <option value="salesforce">Salesforce</option>
                  </select>
                </div>

                {/* Target Filter */}
                <div>
                  <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', display: 'block', marginBottom: 6 }}>Target</label>
                  <select
                    value={targetFilter}
                    onChange={e => updateFilterOption('targetFilter', e.target.value)}
                    style={{
                      width: '100%', padding: '8px 10px', fontSize: 12, fontWeight: 500,
                      background: 'var(--bg-input)', border: '1px solid var(--border-main)',
                      borderRadius: 6, color: 'var(--text-primary)', outline: 'none',
                      cursor: 'pointer', transition: 'all 0.2s',
                    }}
                  >
                    <option value="all">All Targets</option>
                     <option value="fabric">MS Fabric</option>
                     <option value="snowflake">Snowflake</option>
                     <option value="databricks">Databricks</option>
                     <option value="postgresql">PostgreSQL</option>
                     <option value="salesforce">Salesforce</option>
                  </select>
                </div>

                {/* Tag Filter */}
                {allProjectTags.length > 0 && (
                  <div>
                    <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', display: 'block', marginBottom: 6 }}>Tags</label>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                      {allProjectTags.map(tag => (
                        <button
                          key={tag}
                          onClick={() => {
                            const newTags = new Set(tagFilters);
                            if (newTags.has(tag)) newTags.delete(tag);
                            else newTags.add(tag);
                            updateFilterOption('tagFilters', [...newTags]);
                          }}
                          style={{
                            padding: '5px 10px', borderRadius: 5, fontSize: 11, fontWeight: 600,
                            background: tagFilters.has(tag) ? 'var(--accent-blue)' : 'var(--bg-main)',
                            border: tagFilters.has(tag) ? 'none' : '1.5px solid var(--border-main)',
                            color: tagFilters.has(tag) ? '#fff' : 'var(--text-primary)',
                            cursor: 'pointer', whiteSpace: 'nowrap',
                            transition: 'all 0.2s',
                          }}
                          onMouseEnter={e => { if (!tagFilters.has(tag)) { e.currentTarget.style.borderColor = 'var(--accent-blue)40'; } }}
                          onMouseLeave={e => { if (!tagFilters.has(tag)) { e.currentTarget.style.borderColor = 'var(--border-main)'; } }}
                        >
                          {tag}
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {/* Clear Filters */}
                {(sourceFilter !== 'all' || targetFilter !== 'all' || tagFilters.size > 0) && (
                  <div style={{ display: 'flex', alignItems: 'flex-end' }}>
                    <button
                      onClick={() => {
                        setFilterOptions({
                          sourceFilter: 'all',
                          targetFilter: 'all',
                          tagFilters: [],
                        });
                      }}
                      style={{
                        width: '100%', padding: '8px 12px', fontSize: 11, fontWeight: 600,
                        background: 'var(--color-error)20', border: '1.5px solid var(--color-error)40',
                        borderRadius: 6, color: 'var(--color-error)', cursor: 'pointer',
                        transition: 'all 0.2s',
                      }}
                      onMouseEnter={e => { e.currentTarget.style.background = 'var(--color-error)30'; }}
                      onMouseLeave={e => { e.currentTarget.style.background = 'var(--color-error)20'; }}
                    >
                      ✕ Clear All
                    </button>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Cards grid */}
        <div
          id="projects-grid-scroll"
          onScroll={(e) => setProjectListScrollTop(e.currentTarget.scrollTop)}
          style={{ flex: 1, overflowY: 'auto', padding: '4px 28px 28px' }}
        >
          {loading ? (
            <div style={{ padding: '80px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 }}>
              Loading projects…
            </div>
          ) : filtered.length === 0 && !searchQuery ? (
            <EmptyState
              icon={<FolderOpen size={26} />}
              title="No projects yet"
              description="Create your first project to start connecting sources and building semantic models."
              actionLabel="Create Project"
              onAction={() => navigate('/projects/new')}
            />
          ) : filtered.length === 0 ? (
            <div style={{ padding: '80px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 }}>
              No projects match "{searchQuery}"
            </div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(290px, 1fr))', gap: 14 }}>
              {filtered.map(project => (
                <ProjectCard
                  key={project.id}
                  project={project}
                  menuOpen={menuOpen}
                  onMenuToggle={setMenuOpen}
                  onViewDetail={() => { setActiveProjectId(project.id); setDetailProject(project); }}
                  onConfigure={() => { setActiveProjectId(project.id); navigate(`/projects/${project.id}/edit`); }}
                  onRunNow={() => handleRunNow(project.id)}
                  isRunning={runningProjectIds.has(project.id)}
                  onDuplicate={() => handleDuplicate(project)}
                  onExport={() => handleExportSingle(project)}
                  onDelete={() => handleDelete(project)}
                  onDragStart={handleDragStart}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Modals */}
      <ProjectDetailModal
        project={detailProject}
        open={!!detailProject}
        onClose={() => setDetailProject(null)}
        onDuplicate={() => { if (detailProject) handleDuplicate(detailProject); }}
      />
      <ImportProjectModal
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onImported={handleImported}
      />
    </div>
  );
}

/* ─── Sidebar helpers ─── */
function SidebarItem({ color, label, active, onClick }) {
  return (
    <button
      onClick={onClick}
      style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '6px 12px', width: '100%', border: 'none',
        background: active ? 'var(--accent-blue)14' : 'transparent',
        color: active ? 'var(--accent-blue)' : 'var(--text-secondary)',
        fontSize: 12, fontWeight: active ? 600 : 400, cursor: 'pointer',
        textAlign: 'left',
      }}
    >
      <Folder size={13} style={{ color }} />
      {label}
    </button>
  );
}

function SidebarFolder({
  folder, projectCount, active, onClick,
  isRenaming, renameValue, onRenameChange, onRenameSubmit, onRenameStart,
  onDelete, isDragOver, onDragOver, onDragLeave, onDrop,
}) {
  const [hover, setHover] = useState(false);
  return (
    <div
      role="button"
      tabIndex={0}
      style={{
        display: 'flex', alignItems: 'center', gap: 6,
        padding: '5px 10px', cursor: 'pointer',
        background: isDragOver ? `${folder.color}22` : active ? 'var(--accent-blue)14' : 'transparent',
        borderLeft: active ? `2px solid ${folder.color}` : '2px solid transparent',
        transition: 'background 0.1s',
      }}
      onClick={onClick}
      onKeyDown={e => e.key === 'Enter' && onClick()}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      <div style={{ width: 8, height: 8, borderRadius: 2, background: folder.color, flexShrink: 0 }} />
      {isRenaming ? (
        <input
          autoFocus
          value={renameValue}
          onChange={e => onRenameChange(e.target.value)}
          onClick={e => e.stopPropagation()}
          onKeyDown={e => {
            if (e.key === 'Enter') { e.stopPropagation(); onRenameSubmit(); }
            if (e.key === 'Escape') { e.stopPropagation(); onRenameChange(''); }
          }}
          style={{
            flex: 1, fontSize: 12, padding: '2px 5px',
            background: 'var(--bg-input)', border: '1px solid var(--accent-blue)',
            borderRadius: 4, color: 'var(--text-primary)', outline: 'none',
          }}
        />
      ) : (
        <span style={{
          flex: 1, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          color: active ? folder.color : 'var(--text-secondary)', fontWeight: active ? 600 : 400,
        }}>
          {folder.name}
        </span>
      )}
      <span style={{ fontSize: 10, color: 'var(--text-tertiary)', flexShrink: 0 }}>{projectCount}</span>
      {hover && !isRenaming && (
        <>
          <button onClick={e => { e.stopPropagation(); onRenameStart(); }} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: 1 }}>
            <Edit3 size={10} />
          </button>
          <button onClick={e => { e.stopPropagation(); onDelete(); }} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: 1 }}>
            <Trash2 size={10} />
          </button>
        </>
      )}
    </div>
  );
}

/* ─── Project Card ─── */
function ProjectCard({
  project, menuOpen, onMenuToggle,
  onViewDetail, onConfigure, onRunNow, isRunning = false, onDuplicate, onExport, onDelete,
  onDragStart,
}) {
  const isOpen = menuOpen === project.id;
  const emoji = SOURCE_ICONS[project.source || project.adapter] ?? '📁';
  const { activeRuns } = useContext(SyncContext);
  // Debug log for troubleshooting status updates
  console.log('[ProjectCard] project.id:', project.id, 'activeRuns:', activeRuns);
  const myRun = activeRuns.find(
    run =>
      String(run.projectId) === String(project.id) ||
      String(run.project_id) === String(project.id)
  );
  console.log('[ProjectCard] myRun:', myRun, 'project.status:', project.status);
  const status = myRun ? myRun.status : (project.status || 'Idle');
  let progress = 0;
  if (myRun) {
    if (myRun.progress !== undefined) {
      progress = myRun.progress;
    } else if (myRun.stepName) {
      const stepMatch = myRun.stepName.match(/Step\s+(\d+)/i);
      if (stepMatch && stepMatch[1]) {
        progress = (parseInt(stepMatch[1], 10) / 10) * 100;
      }
    }
  }
  const syncInFlight = isRunning || status === 'Running';

  const handleSyncClick = async () => {
    if (syncInFlight) return;
    await onRunNow();
  };

  return (
    <div
      draggable
      onDragStart={e => onDragStart(e, project.id)}
      style={{
        background: 'var(--bg-surface)',
        border: '1px solid var(--border-main)',
        borderRadius: 10, padding: 18,
        display: 'flex', flexDirection: 'column',
        cursor: 'grab', position: 'relative',
        transition: 'border-color 0.15s, box-shadow 0.15s',
      }}
      onMouseEnter={e => {
        e.currentTarget.style.borderColor = 'var(--accent-blue)';
        e.currentTarget.style.boxShadow = 'var(--shadow-md)';
      }}
      onMouseLeave={e => {
        e.currentTarget.style.borderColor = 'var(--border-main)';
        e.currentTarget.style.boxShadow = 'none';
      }}
    >
      {/* Top row */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{
            width: 40, height: 40, borderRadius: 10,
            background: 'var(--color-accent-faint)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 20,
          }}>
            {emoji}
          </div>
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', lineHeight: 1.2 }}>
              {project.name}
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 2 }}>
              {project.source || project.adapter || '—'}
            </div>
          </div>
        </div>

        {/* 3-dot menu */}
        <div style={{ position: 'relative' }}>
          <button
            onClick={() => onMenuToggle(isOpen ? null : project.id)}
            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: 4 }}
          >
            <MoreVertical size={14} />
          </button>
          {isOpen && (
            <ProjectMenu
              onViewDetail={onViewDetail}
              onConfigure={onConfigure}
              onRunNow={onRunNow}
              onDuplicate={onDuplicate}
              onExport={onExport}
              onDelete={onDelete}
              onClose={() => onMenuToggle(null)}
            />
          )}
        </div>
      </div>

      {project.description && (
        <p style={{
          fontSize: 11, color: 'var(--text-tertiary)', lineHeight: 1.5, marginBottom: 10,
          display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden',
        }}>
          {project.description}
        </p>
      )}

      {/* Tags */}
      {project.tags && project.tags.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginBottom: 10 }}>
          {project.tags.map((tag, idx) => (
            <div
              key={idx}
              style={{
                display: 'inline-block',
                padding: '2px 8px', borderRadius: 3,
                background: 'var(--accent-blue)15', color: 'var(--accent-blue)',
                fontSize: 10, fontWeight: 500, whiteSpace: 'nowrap',
              }}
            >
              {tag}
            </div>
          ))}
        </div>
      )}

      {/* Footer */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        marginTop: 'auto', paddingTop: 10, borderTop: '1px solid var(--border-subtle)',
      }}>
        <div className={status === 'Running' ? 'animate-pulse' : ''}>
          <StatusBadge status={status || 'draft'} size="sm" />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <IconBtn title={syncInFlight ? 'Running...' : 'Run Now'} onClick={handleSyncClick} disabled={syncInFlight}>
            {syncInFlight ? <Layers size={12} className="animate-spin" /> : <Play size={12} fill="currentColor" />}
          </IconBtn>
          <IconBtn title="View Runs" onClick={onViewDetail}><Layers size={12} /></IconBtn>
          <IconBtn title="Configure" onClick={onConfigure}><Settings size={12} /></IconBtn>
        </div>
      </div>

      {/* Card progress/status bar removed; global StatusBar will be used instead */}
    </div>
  );
}

function IconBtn({ title, onClick, disabled = false, children }) {
  return (
    <button
      title={title}
      disabled={disabled}
      onClick={e => {
        e.stopPropagation();
        if (!disabled) onClick();
      }}
      style={{
        background: 'none',
        border: 'none',
        cursor: disabled ? 'not-allowed' : 'pointer',
        color: disabled ? 'var(--text-tertiary)' : 'var(--text-tertiary)',
        opacity: disabled ? 0.55 : 1,
        padding: 3,
        display: 'flex',
      }}
      onMouseEnter={e => { if (!disabled) e.currentTarget.style.color = 'var(--text-secondary)'; }}
      onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-tertiary)'; }}
    >
      {children}
    </button>
  );
}

/* ─── Project context menu ─── */
function ProjectMenu({ onViewDetail, onConfigure, onRunNow, onDuplicate, onExport, onDelete, onClose }) {
  useEffect(() => {
    const h = () => onClose();
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, [onClose]);

  const items = [
    { icon: <Layers size={13} />, label: 'View Runs', action: onViewDetail },
    { icon: <Settings size={13} />, label: 'Configure', action: onConfigure },
    { icon: <Play size={13} fill="currentColor" />, label: 'Run Now', action: onRunNow },
    { icon: <Copy size={13} />, label: 'Duplicate', action: onDuplicate },
    { icon: <Download size={13} />, label: 'Export', action: onExport },
    null,
    { icon: <Trash2 size={13} />, label: 'Delete', action: onDelete, danger: true },
  ];

  return (
    <div
      onMouseDown={e => e.stopPropagation()}
      style={{
        position: 'absolute', top: 28, right: 0, zIndex: 200,
        background: 'var(--bg-surface)',
        border: '1px solid var(--border-main)',
        borderRadius: 8, boxShadow: '0 8px 24px rgba(0,0,0,0.15)',
        minWidth: 162, overflow: 'hidden',
      }}
    >
      {items.map((item, i) =>
        item === null
          ? <div key={i} style={{ height: 1, background: 'var(--border-subtle)', margin: '2px 0' }} />
          : (
            <button
              key={item.label}
              onClick={() => { item.action(); onClose(); }}
              style={{
                display: 'flex', alignItems: 'center', gap: 8,
                padding: '8px 12px', width: '100%', border: 'none',
                background: 'transparent', cursor: 'pointer', textAlign: 'left',
                fontSize: 12,
                color: item.danger ? 'var(--color-error)' : 'var(--text-secondary)',
              }}
              onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-surface-hover)'; }}
              onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
            >
              {item.icon} {item.label}
            </button>
          )
      )}
    </div>
  );
}

/* ─── Shared button style helper ─── */
function btnStyle(variant) {
  return {
    display: 'inline-flex', alignItems: 'center', gap: 5,
    padding: '7px 14px', borderRadius: 7, fontSize: 12, fontWeight: 600,
    cursor: 'pointer',
    background: variant === 'primary' ? 'var(--accent-blue)' : 'transparent',
    color: variant === 'primary' ? '#fff' : 'var(--text-secondary)',
    border: variant === 'primary' ? 'none' : '1px solid var(--border-main)',
  };
}


