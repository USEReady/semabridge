/**
 * ProjectsPage — Folder-grouped projects with HP search, drag-drop, and import/export.
 */
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useVirtualizer } from '@tanstack/react-virtual';
import {
  FolderOpen, Plus, MoreVertical, Layers, Trash2, Edit3,
  Upload, Download, Play, Settings, Copy, Folder, FolderPlus,
  X, Check, BarChart3, Cloud, Snowflake, Database,
} from 'lucide-react';

import StatusBadge from '../components/common/StatusBadge';
import EmptyState from '../components/common/EmptyState';
import ProjectDetailModal from '../components/projects/ProjectDetailModal';
import ImportProjectModal from '../components/projects/ImportProjectModal';
import BatchImportPbixModal from '../components/projects/BatchImportPbixModal';
import BulkDryRunSummaryModal from '../components/projects/BulkDryRunSummaryModal';
import { summarizeRowHealth } from '../utils/riskLabels';
import SmartSearchBar from '../components/common/SmartSearchBar';
import { getSmartQueryMode, matchesSmartQuery } from '../components/common/smartSearchQuery.js';
import { api } from '../utils/api';
import { resolveProjectSyncMode } from '../utils/syncMode';
import React, { useContext } from 'react';
import { SyncContext } from '../context/SyncContext';
import { useAuth } from '../context/AuthContext';
import { DEFAULT_FILTER_OPTIONS, useUIStore } from '../store/uiStore';
import { SidebarSystemGroup, SidebarAddButton } from '../components/projects/SystemSidebar';
import { hasFailures } from '../utils/statusUtils';

function openRunsPage() {
  if (typeof window !== 'undefined') {
    window.location.assign('/jobs');
  }
}

function normalizeSourceKey(value) {
  if (!value) return '';

  let key = '';
  if (typeof value === 'string') {
    key = value.toLowerCase().trim();
  }
  if (typeof value === 'object') {
    key = String(
      value.type || value.adapter || value.source || value.source_type || value.connector || ''
    ).toLowerCase().trim();
  }

  // Canonicalize common API variants so icon/render logic stays stable.
  if (!key) return '';
  if (key.includes('pbix') || key.includes('powerbi') || key.includes('power bi') || key === 'pbi') return 'pbix';
  if (key.includes('fabric')) return 'fabric';
  if (key.includes('snowflake')) return 'snowflake';
  if (key.includes('databricks')) return 'databricks';
  if (key.includes('google') && key.includes('sheet')) return 'google_sheets';
  if (key.includes('postgres')) return 'postgresql';
  if (key.includes('salesforce')) return 'salesforce';

  return key;
}

function sourceKeyOf(project) {
  return (
    normalizeSourceKey(project?.source)
    || normalizeSourceKey(project?.adapter)
    || normalizeSourceKey(project?.source_type)
    || normalizeSourceKey(project?.connector)
  );
}

function renderSourceIcon(sourceType, size = 14) {
  const source = normalizeSourceKey(sourceType);
  if (source.includes('pbix')) return <BarChart3 size={size} color="#F2C811" />;
  if (source.includes('fabric')) return <Cloud size={size} color="#3b82f6" />;
  if (source.includes('snowflake')) return <Snowflake size={size} color="#38bdf8" />;
  if (source.includes('databricks')) return <Database size={size} color="#f97316" />;
  return <Folder size={size} color="var(--text-tertiary)" />;
}

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

const SOURCE_TARGET_OPTIONS = [
  { value: 'fabric', label: 'MS Fabric' },
  { value: 'snowflake', label: 'Snowflake' },
  { value: 'databricks', label: 'Databricks' },
  { value: 'postgresql', label: 'PostgreSQL' },
  { value: 'salesforce', label: 'Salesforce' },
];

const PROJECTS_CACHE_KEY = 'semabridge:cache:projects';
const FOLDERS_CACHE_KEY = 'semabridge:cache:folders';

function statusRollupText(projects) {
  const counts = projects.reduce((acc, p) => {
    const s = (p.status || 'idle').toLowerCase();
    acc[s] = (acc[s] || 0) + 1;
    return acc;
  }, {});
  const successLike = (counts.success || 0) + (counts.active || 0);
  const failedLike = (counts.failed || 0) + (counts.error || 0);
  const pendingLike = projects.length - successLike - failedLike - (counts.running || 0);

  const parts = [];
  if (successLike > 0) parts.push(`${successLike} deployed`);
  if (counts.running > 0) parts.push(`${counts.running} running`);
  if (failedLike > 0) parts.push(`${failedLike} failed`);
  if (pendingLike > 0) parts.push(`${pendingLike} pending`);
  return parts.join(', ') || 'no runs yet';
}

function readCachedList(key) {
  try {
    const raw = localStorage.getItem(key);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeCachedList(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(Array.isArray(value) ? value : []));
  } catch {
    // Ignore storage write errors (private mode / quota).
  }
}

/* ─── Main Page ─── */
export default function ProjectsPage() {
  const { loading: authLoading, token } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [menuOpen, setMenuOpen] = useState(null);
  const [detailProject, setDetailProject] = useState(null);
  const [filterPanelOpen, setFilterPanelOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [batchImportOpen, setBatchImportOpen] = useState(false);
  const [newFolderMode, setNewFolderMode] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const [renameFolderId, setRenameFolderId] = useState(null);
  const [renameFolderName, setRenameFolderName] = useState('');
  const [draggingProjectId, setDraggingProjectId] = useState(null);
  const [dragOverFolder, setDragOverFolder] = useState(null);
  const [sidebarWidth, setSidebarWidth] = useState(240);
  const [isResizing, setIsResizing] = useState(false);
  const [runningProjectIds, setRunningProjectIds] = useState(new Set());
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [bulkDryRunPending, setBulkDryRunPending] = useState(false);
  const [bulkDryRunResults, setBulkDryRunResults] = useState(null);
  const [expandedGroups, setExpandedGroups] = useState(new Set());
  const searchQuery = useUIStore(state => state.searchQuery);
  const setSearchQuery = useUIStore(state => state.setSearchQuery);
  const filterOptions = useUIStore(state => state.filterOptions);
  const setFilterOptions = useUIStore(state => state.setFilterOptions);
  const projectListScrollTop = useUIStore(state => state.projectListScrollTop);
  const setProjectListScrollTop = useUIStore(state => state.setProjectListScrollTop);
  const setActiveProjectId = useUIStore(state => state.setActiveProjectId);
  const selectedFolder = filterOptions?.selectedFolder ?? DEFAULT_FILTER_OPTIONS.selectedFolder;
  const sourceFilter = filterOptions?.sourceFilter ?? DEFAULT_FILTER_OPTIONS.sourceFilter;
  const targetFilters = Array.isArray(filterOptions?.targetFilters)
    ? filterOptions.targetFilters
    : (filterOptions?.targetFilter && filterOptions.targetFilter !== 'all' ? [filterOptions.targetFilter] : []);
  const tagFilters = new Set(filterOptions?.tagFilters ?? DEFAULT_FILTER_OPTIONS.tagFilters);
  const viewMode = filterOptions?.viewMode ?? DEFAULT_FILTER_OPTIONS.viewMode;
  const useRegexSearch = filterOptions?.useRegexSearch ?? DEFAULT_FILTER_OPTIONS.useRegexSearch;
  const isAnyFilterActive = sourceFilter !== 'all' || targetFilters.length > 0 || tagFilters.size > 0;

  const hasRunning = runningProjectIds.size > 0;

  const {
    data: projects = [],
    isLoading: projectsLoading,
    refetch: refetchProjects,
    isError: projectsError,
  } = useQuery({
    queryKey: ['projects'],
    initialData: () => readCachedList(PROJECTS_CACHE_KEY),
    staleTime: hasRunning ? 0 : 5000,
    refetchOnWindowFocus: true,
    refetchInterval: hasRunning ? 5000 : false,
    retry: 0,
    enabled: !authLoading && !!token,
    queryFn: async () => {
      const data = await api.listProjects();
      writeCachedList(PROJECTS_CACHE_KEY, data);
      return data;
    },
  });

  const {
    data: folders = [],
    isLoading: foldersLoading,
    refetch: refetchFolders,
    isError: foldersError,
  } = useQuery({
    queryKey: ['folders'],
    initialData: () => readCachedList(FOLDERS_CACHE_KEY),
    staleTime: 5000,
    retry: 0,
    enabled: !authLoading && !!token,
    queryFn: async () => {
      const data = await api.listFolders();
      writeCachedList(FOLDERS_CACHE_KEY, data);
      return data;
    },
  });

  const loading = (projectsLoading && projects.length === 0) || (foldersLoading && folders.length === 0);
  const loadFailed = (projectsError || foldersError) && projects.length === 0 && folders.length === 0;

  // Stop polling a project once the fetched data shows a terminal status
  useEffect(() => {
    if (runningProjectIds.size === 0) return;
    const terminalStatuses = new Set(['success', 'active', 'failed', 'warning', 'partial', 'draft', 'idle']);
    runningProjectIds.forEach(pid => {
      const p = projects.find(pr => pr.id === pid || pr.project_id === pid);
      if (p && terminalStatuses.has(String(p.status || '').toLowerCase())) {
        setRunningProjectIds(prev => { const next = new Set(prev); next.delete(pid); return next; });
      }
    });
  }, [projects, runningProjectIds]);

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

  useEffect(() => {
    const handleMouseMove = (e) => {
      if (!isResizing) return;
      const newWidth = window.innerWidth - e.clientX;
      if (newWidth >= 180 && newWidth <= 600) {
        setSidebarWidth(newWidth);
      }
    };
    const handleMouseUp = () => setIsResizing(false);

    if (isResizing) {
      window.addEventListener('mousemove', handleMouseMove);
      window.addEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    }

    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, [isResizing]);

  // Smart Expand: Automatically expand systems with failures
  useEffect(() => {
    if (viewMode === 'adapter' && projects.length > 0) {
      const systemsWithFailures = new Set();
      const systems = [...new Set(projects.map(p => sourceKeyOf(p)).filter(Boolean))];
      
      systems.forEach(system => {
        const systemProjects = projects.filter(p => sourceKeyOf(p) === system);
        if (hasFailures(systemProjects)) {
          systemsWithFailures.add(system);
        }
      });

      if (systemsWithFailures.size > 0) {
        setExpandedGroups(prev => {
          const next = new Set(prev);
          systemsWithFailures.forEach(s => next.add(s));
          return next;
        });
      }
    }
  }, [viewMode, projects.length]);

  const updateFilterOption = useCallback((key, value) => {
    setFilterOptions({ [key]: value });
  }, [setFilterOptions]);

  const toggleTargetFilter = useCallback((targetType) => {
    const next = new Set(targetFilters);
    if (next.has(targetType)) next.delete(targetType);
    else next.add(targetType);

    setFilterOptions({
      targetFilters: [...next],
      targetFilter: next.size > 0 ? [...next][0] : 'all',
    });
  }, [setFilterOptions, targetFilters]);

  const allProjectTags = useMemo(
    () => [...new Set(projects.flatMap(p => p.tags || []))],
    [projects],
  );

  const folderFiltered = useMemo(() => projects.filter(p => {
    // Handle both folder and source selection
    if (selectedFolder !== null) {
      const selectedFolderKey = String(selectedFolder);
      if (selectedFolderKey.startsWith('source:')) {
        const source = selectedFolderKey.substring(7);
        if (sourceKeyOf(p) !== source) return false;
      } else if (selectedFolderKey.startsWith('project:')) {
        const projectId = selectedFolderKey.substring(8);
        if (String(p.id) !== projectId) return false;
      } else {
        if (String(p.folder_id ?? '') !== selectedFolderKey) return false;
      }
    }
    if (sourceFilter !== 'all' && sourceKeyOf(p) !== sourceFilter) return false;
    if (targetFilters.length > 0 && !targetFilters.includes(String(p.target_type || '').toLowerCase())) return false;
    
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
  }), [projects, selectedFolder, sourceFilter, targetFilters, tagFilters]);

  const queryMode = useMemo(
    () => getSmartQueryMode(searchQuery, useRegexSearch),
    [searchQuery, useRegexSearch],
  );

  const filtered = useMemo(() => searchQuery.trim()
    ? folderFiltered.filter((p) => {
        if (queryMode.mode === 'prefix' && queryMode.strictPrefix) {
          return matchesSmartQuery(p.name || '', searchQuery, useRegexSearch);
        }

        const haystack = [
          p.name,
          p.description,
          sourceKeyOf(p),
          p.workspace_id,
          p.target_type,
          ...(Array.isArray(p.tags) ? p.tags : []),
        ].join(' ');
        return matchesSmartQuery(haystack, searchQuery, useRegexSearch);
      })
    : folderFiltered,
  [folderFiltered, queryMode, searchQuery, useRegexSearch]);

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
    setDraggingProjectId(projectId);
    e.dataTransfer.effectAllowed = 'move';
  };

  const handleDragEnd = () => {
    setDraggingProjectId(null);
    setDragOverFolder(null);
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

  /* ── Multi-select + bulk actions ── */
  const toggleSelected = useCallback((projectId) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(projectId)) next.delete(projectId);
      else next.add(projectId);
      return next;
    });
  }, []);

  const clearSelection = useCallback(() => setSelectedIds(new Set()), []);

  const selectAllVisible = useCallback(() => {
    setSelectedIds(new Set(filtered.map(p => p.id)));
  }, [filtered]);

  const selectedProjects = useMemo(
    () => filtered.filter(p => selectedIds.has(p.id)),
    [filtered, selectedIds],
  );

  const handleBulkRun = async () => {
    const targets = selectedProjects;
    clearSelection();
    for (const project of targets) {
      await handleRunNow(project);
    }
  };

  const handleBulkDryRun = async () => {
    const targets = selectedProjects;
    if (targets.length === 0) return;
    // Deliberately NOT clearing selection until the loop finishes (unlike
    // handleBulkRun/handleBulkDelete above) -- this is the one bulk action
    // that can take several seconds per project with no other visual
    // indicator anywhere in the UI that a dry run is in flight (deploy runs
    // at least get a "Running" badge on the card; dry runs get nothing),
    // so the progress bar below would otherwise disappear the instant you
    // click it and give zero feedback until the final alert.
    setBulkDryRunPending({ done: 0, total: targets.length });
    const results = [];
    for (const project of targets) {
      try {
        // Built from the project's own already-persisted source/target --
        // this is "run dry-run as the project is configured today," not a
        // fresh wizard walkthrough, so none of CreateProjectPage's
        // client-side wizard state (fabricAccountId, selectedModelNames,
        // etc.) applies here; those are already baked into source_config/
        // targets when the project has them.
        const sourceConfig = project.source_config || { type: project.source };
        const targetConfig = (Array.isArray(project.targets) && project.targets[0])
          || project.target
          || { type: project.target_type };
        const response = await api.runProjectDryRun(project.id, {
          source_config: sourceConfig,
          target_config: targetConfig,
          selected_sources: [],
          reset_manual: true,
        });
        results.push({
          projectId: project.id,
          projectName: project.name,
          status: 'success',
          health: summarizeRowHealth(response?.entity_mappings),
          compatibilityScore: typeof response?.compatibility_score === 'number' ? response.compatibility_score : null,
        });
      } catch (err) {
        results.push({
          projectId: project.id,
          projectName: project.name,
          status: 'error',
          error: err?.message || 'Unknown error',
        });
      }
      setBulkDryRunPending(prev => (prev ? { ...prev, done: prev.done + 1 } : prev));
    }
    setBulkDryRunPending(false);
    clearSelection();
    setBulkDryRunResults(results);
  };

  const handleBulkExport = async () => {
    const ids = selectedProjects.map(p => p.id);
    if (ids.length === 0) return;
    await api.exportProjectsBulk(ids);
    clearSelection();
  };

  const handleBulkDelete = async () => {
    const targets = selectedProjects;
    if (targets.length === 0) return;
    if (!confirm(`Delete ${targets.length} selected project${targets.length !== 1 ? 's' : ''}? This cannot be undone.`)) return;
    const deletedIds = new Set();
    for (const project of targets) {
      try {
        await api.deleteProject(project.id);
        deletedIds.add(project.id);
      } catch (err) {
        console.error(`Delete failed for project ${project.id}:`, err);
      }
    }
    setProjects(prev => prev.filter(p => !deletedIds.has(p.id)));
    clearSelection();
  };

  const handleRunNow = useCallback(async (projectOrId) => {
    const projectId = typeof projectOrId === 'object' ? (projectOrId?.id || projectOrId?.project_id) : projectOrId;
    if (!projectId) return;

    setRunningProjectIds(prev => {
      const next = new Set(prev);
      next.add(projectId);
      return next;
    });

    try {
      const project = typeof projectOrId === 'object'
        ? projectOrId
        : projects.find(p => String(p.id || p.project_id) === String(projectId));
      const savedSyncMode = resolveProjectSyncMode(
        project,
        localStorage.getItem(`project_${projectId}_syncMode`) || 'copy',
      );
      const result = await api.runProjectNow(projectId, { sync_mode: savedSyncMode });
      const updatedProject = result?.project;
      if (updatedProject?.id || updatedProject?.project_id) {
        const pid = updatedProject.id || updatedProject.project_id;
        setProjects(prev => prev.map(p => (p.id === pid || p.project_id === pid) ? { ...p, ...updatedProject } : p));
      }
      const status = String(result?.status || '').toLowerCase();
      if (result?.run_id || result?.id || status === 'running') {
        // Optimistically mark the project as "running" so the badge updates immediately,
        // then schedule a re-fetch after the run is likely to have completed so the
        // Projects page shows the real final status when the user returns.
        setProjects(prev => prev.map(p =>
          (p.id === projectId || p.project_id === projectId) ? { ...p, status: 'running' } : p
        ));
        // Refetch at 5s, 15s, 30s, 60s, 120s to catch fast failures and slow runs
        [5000, 15000, 30000, 60000, 120000].forEach(delay =>
          setTimeout(() => refetchProjects(), delay)
        );
        openRunsPage();
        return result;
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
  }, [setProjects, refetchProjects, openRunsPage]);

  /* ── Render ── */
  if (loadFailed) {
    return (
      <div style={{ padding: '80px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 }}>
        Unable to load projects right now. Retrying may help.
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'row-reverse', height: '100%', overflow: 'hidden' }}>

      {/* ── Folder/Adapter Sidebar ── */}
      <aside style={{
        width: sidebarWidth, flexShrink: 0,
        borderLeft: '1px solid var(--border-main)',
        borderRight: 'none',
        display: 'flex', flexDirection: 'column',
        padding: '16px 0',
        overflowY: 'auto',
        position: 'relative',
        background: 'var(--bg-app)',
      }}>
        <div
          onMouseDown={(e) => {
            e.preventDefault();
            setIsResizing(true);
          }}
          style={{
            position: 'absolute',
            left: -2, top: 0, bottom: 0,
            width: 4,
            cursor: 'col-resize',
            zIndex: 50,
            transition: 'background 0.2s',
          }}
          className={isResizing ? 'bg-accent' : 'hover:bg-accent/40'}
        />
        <div style={{ padding: '0 12px', marginBottom: 8, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, width: '100%' }}>
            <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-tertiary)', letterSpacing: '0.06em', textTransform: 'uppercase' }}>
              Group Projects
            </span>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
              <button
                onClick={() => updateFilterOption('viewMode', 'folder')}
                style={{
                  border: viewMode === 'folder' ? '1px solid var(--accent-blue)' : '1px solid var(--border-dark, #2d3139)',
                  borderRadius: 6,
                  padding: '6px 4px',
                  fontSize: 11,
                  fontWeight: 600,
                  cursor: 'pointer',
                  background: viewMode === 'folder' ? 'rgba(59, 130, 246, 0.1)' : 'transparent',
                  color: viewMode === 'folder' ? 'var(--accent-blue)' : '#888888',
                  transition: 'all 0.2s'
                }}
              >
                Group by Folder
              </button>
              <button
                onClick={() => updateFilterOption('viewMode', 'adapter')}
                style={{
                  border: viewMode === 'adapter' ? '1px solid var(--accent-blue)' : '1px solid var(--border-dark, #2d3139)',
                  borderRadius: 6,
                  padding: '6px 4px',
                  fontSize: 11,
                  fontWeight: 600,
                  cursor: 'pointer',
                  background: viewMode === 'adapter' ? 'rgba(59, 130, 246, 0.1)' : 'transparent',
                  color: viewMode === 'adapter' ? 'var(--accent-blue)' : '#888888',
                  transition: 'all 0.2s'
                }}
              >
                Group by System
              </button>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 4, marginLeft: 8 }}>
            <button
              onClick={() => updateFilterOption('viewMode', viewMode === 'folder' ? 'adapter' : 'folder')}
              title={viewMode === 'folder' ? 'View by system' : 'View by folder'}
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
          isDraggingGlobal={!!draggingProjectId}
          onDragOver={e => { e.preventDefault(); setDragOverFolder('all'); }}
          onDragLeave={() => setDragOverFolder(null)}
          onDrop={async e => {
            e.preventDefault();
            const pid = e.dataTransfer.getData('projectId');
            if (!pid) return;
            await api.moveProjectToFolder(pid, null);
            setProjects(prev => prev.map(p => p.id === pid ? { ...p, folder_id: null } : p));
            setDraggingProjectId(null);
            setDragOverFolder(null);
          }}
          isDragOver={dragOverFolder === 'all'}
        />

        {/* Folder list or Source list */}
        {viewMode === 'folder' ? (
          folders.map(f => {
            const projectsInFolder = projects.filter(p => p.folder_id === f.id);
            return (
              <SidebarFolder
                key={f.id}
                folder={f}
                projectsInFolder={projectsInFolder}
                active={selectedFolder === f.id}
                onClick={() => updateFilterOption('selectedFolder', f.id)}
                isRenaming={renameFolderId === f.id}
                renameValue={renameFolderName}
                onRenameChange={setRenameFolderName}
                onRenameSubmit={() => handleRenameFolder(f.id)}
                onRenameStart={() => { setRenameFolderId(f.id); setRenameFolderName(f.name); }}
                onDelete={() => handleDeleteFolder(f.id)}
                isDragOver={dragOverFolder === f.id}
                isDraggingGlobal={!!draggingProjectId}
                onDragOver={e => { e.preventDefault(); setDragOverFolder(f.id); }}
                onDragLeave={() => setDragOverFolder(null)}
                onDrop={async e => {
                  await handleDropOnFolder(e, f.id);
                  setDraggingProjectId(null);
                }}
                onRunAll={() => {
                  projectsInFolder.forEach(p => handleRunNow(p));
                }}
              />
            );
          })
        ) : (
          // Adapter/Source view (Refined)
          <div style={{ flex: 1, overflowY: 'auto' }}>
            {[...new Set(projects.map(p => sourceKeyOf(p)).filter(Boolean))]
              .sort()
              .map(source => {
                const sourceProjects = projects.filter(p => sourceKeyOf(p) === source);
                return (
                  <SidebarSystemGroup
                    key={source}
                    systemName={source.toUpperCase()}
                    projects={sourceProjects}
                    icon={renderSourceIcon(source, 14)}
                    expanded={expandedGroups.has(source)}
                    onToggle={() => {
                      setExpandedGroups(prev => {
                        const next = new Set(prev);
                        if (next.has(source)) next.delete(source);
                        else next.add(source);
                        return next;
                      });
                    }}
                    onHeaderClick={() => {
                      updateFilterOption('selectedFolder', `source:${source}`);
                    }}
                    activeProject={selectedFolder && selectedFolder.startsWith('project:') ? projects.find(p => `project:${p.id}` === selectedFolder) : null}
                    onProjectClick={(project) => {
                      updateFilterOption('selectedFolder', `project:${project.id}`);
                      setActiveProjectId(project.id);
                    }}
                    onMenuToggle={setMenuOpen}
                    onRunFailed={() => {
                      const failed = sourceProjects.filter(p => (p.status || '').toLowerCase() === 'failed');
                      if (failed.length > 0) {
                        failed.forEach(p => handleRunNow(p));
                      } else {
                        // If none failed, run all that are 'stale' or just run all
                        sourceProjects.forEach(p => handleRunNow(p));
                      }
                    }}
                    onForceRunAll={() => {
                      sourceProjects.forEach(p => handleRunNow(p));
                    }}
                  />
                );
              })}
            <SidebarAddButton 
              label="Add New System/Connection" 
              onClick={() => navigate('/connections/new')} 
            />
          </div>
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
            margin: '8px 10px 0',
            borderRadius: 6,
            border: draggingProjectId ? '1.5px dashed var(--accent-blue)' : '1px dashed var(--border-subtle)',
            padding: '8px 10px',
            fontSize: 11,
            color: draggingProjectId ? 'var(--accent-blue)' : 'var(--text-tertiary)',
            textAlign: 'center',
            background: dragOverFolder === 'root' ? 'var(--accent-blue)10' : 'transparent',
            transition: 'all 0.2s',
            opacity: draggingProjectId ? 1 : 0.6,
          }}
          onDragOver={e => { e.preventDefault(); setDragOverFolder('root'); }}
          onDragLeave={() => setDragOverFolder(null)}
          onDrop={async e => {
            e.preventDefault();
            const pid = e.dataTransfer.getData('projectId');
            if (!pid) return;
            await api.moveProjectToFolder(pid, null);
            setProjects(prev => prev.map(p => p.id === pid ? { ...p, folder_id: null } : p));
            setDraggingProjectId(null);
            setDragOverFolder(null);
          }}
        >
          {dragOverFolder === 'root' ? 'Release to remove' : ''}
        </div>
      </aside>

      {/* ── Main content ── */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', maxWidth: 1400, margin: '0 auto', padding: '28px 16px' }} className="md:px-10">
        {/* Header */}
        <div style={{ padding: '0 0 16px', flexShrink: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
            <div>
              <h1 style={{ fontSize: 20, fontWeight: 700, color: 'var(--text-primary)', margin: 0 }}>
                {selectedFolder === null ? 'Projects' : (folders.find(f => f.id === selectedFolder)?.name ?? 'Folder')}
              </h1>
              <p style={{ fontSize: 13, color: 'var(--text-tertiary)', margin: '4px 0 0' }}>
                {filtered.length} project{filtered.length !== 1 ? 's' : ''}
                {filtered.length > 0 && ` · ${statusRollupText(filtered)}`}
              </p>
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <button onClick={() => setImportOpen(true)} style={btnStyle('secondary')}>
                <Upload size={13} /> Import
              </button>
              <button onClick={() => setBatchImportOpen(true)} style={btnStyle('secondary')}>
                <Layers size={13} /> Batch Import PBIX
              </button>
              {filtered.length > 0 && (
                <button onClick={handleExportAll} style={btnStyle('secondary')}>
                  <Download size={13} /> Export All
                </button>
              )}
              <button onClick={() => navigate('/projects/new', { state: { fresh: true } })} style={btnStyle('primary')}>
                <Plus size={13} /> New Project
              </button>
            </div>
          </div>

          {selectedIds.size > 0 && (
            <div style={{
              display: 'flex', alignItems: 'center', gap: 10,
              padding: '8px 12px', marginBottom: 12, borderRadius: 8,
              background: 'var(--accent-blue)10', border: '1px solid var(--accent-blue)30',
            }}>
              <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>
                {selectedIds.size} selected
              </span>
              <button onClick={selectAllVisible} style={{ ...btnStyle('secondary'), padding: '4px 10px', fontSize: 11 }}>
                Select all {filtered.length}
              </button>
              <div style={{ flex: 1 }} />
              <button
                onClick={handleBulkDryRun}
                disabled={!!bulkDryRunPending}
                title="Run dry-run mapping detection for every selected project, using each project's own saved source/target config"
                style={{
                  ...btnStyle('secondary'), padding: '4px 10px', fontSize: 11,
                  cursor: bulkDryRunPending ? 'not-allowed' : 'pointer',
                  opacity: bulkDryRunPending ? 0.6 : 1,
                }}
              >
                {bulkDryRunPending ? `Running Dry Run… (${bulkDryRunPending.done}/${bulkDryRunPending.total})` : 'Run Dry Run Selected'}
              </button>
              <button onClick={handleBulkRun} disabled={!!bulkDryRunPending} style={{ ...btnStyle('secondary'), padding: '4px 10px', fontSize: 11, opacity: bulkDryRunPending ? 0.5 : 1 }}>
                <Play size={11} fill="currentColor" /> Run Selected
              </button>
              <button onClick={handleBulkExport} disabled={!!bulkDryRunPending} style={{ ...btnStyle('secondary'), padding: '4px 10px', fontSize: 11, opacity: bulkDryRunPending ? 0.5 : 1 }}>
                <Download size={11} /> Export Selected
              </button>
              <button
                onClick={handleBulkDelete}
                disabled={!!bulkDryRunPending}
                style={{ ...btnStyle('secondary'), padding: '4px 10px', fontSize: 11, color: 'var(--color-error)', borderColor: 'var(--color-error)40', opacity: bulkDryRunPending ? 0.5 : 1 }}
              >
                <Trash2 size={11} /> Delete Selected
              </button>
              <button onClick={clearSelection} disabled={!!bulkDryRunPending} style={{ ...btnStyle('secondary'), padding: '4px 10px', fontSize: 11, opacity: bulkDryRunPending ? 0.5 : 1 }}>
                <X size={11} /> Clear
              </button>
            </div>
          )}

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
                background: isAnyFilterActive ? 'var(--accent-blue)' : 'var(--bg-surface)',
                border: isAnyFilterActive ? 'none' : '1px solid var(--border-main)',
                color: isAnyFilterActive ? '#fff' : 'var(--text-secondary)',
                cursor: 'pointer', fontSize: 12, fontWeight: 600, outline: 'none',
                transition: 'all 0.2s',
              }}
              onMouseEnter={e => { e.currentTarget.style.transform = 'scale(1.02)'; }}
              onMouseLeave={e => { e.currentTarget.style.transform = 'scale(1)'; }}
            >
              <Layers size={13} />
              Filters
              {isAnyFilterActive && (
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
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    <button
                      onClick={() => updateFilterOption('sourceFilter', 'all')}
                      style={{
                        textAlign: 'left',
                        padding: '8px 10px',
                        fontSize: 12,
                        fontWeight: 600,
                        borderRadius: 6,
                        border: sourceFilter === 'all' ? '1px solid var(--accent-blue)' : '1px solid var(--border-main)',
                        background: sourceFilter === 'all' ? 'var(--accent-blue)14' : 'var(--bg-input)',
                        color: sourceFilter === 'all' ? 'var(--accent-blue)' : 'var(--text-primary)',
                        cursor: 'pointer',
                      }}
                    >
                      All Sources
                    </button>
                    {SOURCE_TARGET_OPTIONS.map((option) => {
                      const isSelected = sourceFilter === option.value;
                      const shouldDim = sourceFilter !== 'all' && !isSelected;
                      return (
                        <button
                          key={`source-${option.value}`}
                          onClick={() => updateFilterOption('sourceFilter', option.value)}
                          style={{
                            textAlign: 'left',
                            padding: '8px 10px',
                            fontSize: 12,
                            fontWeight: isSelected ? 600 : 500,
                            borderRadius: 6,
                            border: isSelected ? '1px solid var(--accent-blue)' : '1px solid var(--border-main)',
                            background: isSelected ? 'var(--accent-blue)14' : 'var(--bg-input)',
                            color: isSelected ? 'var(--accent-blue)' : shouldDim ? 'var(--text-tertiary)' : 'var(--text-primary)',
                            opacity: shouldDim ? 0.45 : 1,
                            filter: shouldDim ? 'grayscale(100%)' : 'none',
                            cursor: 'pointer',
                          }}
                        >
                          {option.label}
                        </button>
                      );
                    })}
                  </div>
                </div>

                {/* Target Filter */}
                <div>
                  <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', display: 'block', marginBottom: 6 }}>
                    Target (multi-select)
                  </label>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {SOURCE_TARGET_OPTIONS.map((option) => {
                      const active = targetFilters.includes(option.value);
                      return (
                        <button
                          key={`target-${option.value}`}
                          onClick={() => toggleTargetFilter(option.value)}
                          style={{
                            padding: '6px 10px',
                            borderRadius: 999,
                            fontSize: 11,
                            fontWeight: 600,
                            border: active ? '1px solid var(--accent-blue)' : '1px solid var(--border-main)',
                            background: active ? 'var(--accent-blue)14' : 'var(--bg-main)',
                            color: active ? 'var(--accent-blue)' : 'var(--text-primary)',
                            cursor: 'pointer',
                            transition: 'all 0.2s',
                          }}
                        >
                          {option.label}
                        </button>
                      );
                    })}
                  </div>
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
                {isAnyFilterActive && (
                  <div style={{ display: 'flex', alignItems: 'flex-end' }}>
                    <button
                      onClick={() => {
                        setFilterOptions({
                          sourceFilter: 'all',
                          targetFilter: 'all',
                          targetFilters: [],
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
        <VirtualProjectGrid
          filtered={filtered}
          loading={loading}
          searchQuery={searchQuery}
          menuOpen={menuOpen}
          setMenuOpen={setMenuOpen}
          setActiveProjectId={setActiveProjectId}
          setDetailProject={setDetailProject}
          navigate={navigate}
          handleRunNow={handleRunNow}
          runningProjectIds={runningProjectIds}
          handleDuplicate={handleDuplicate}
          handleExportSingle={handleExportSingle}
          handleDelete={handleDelete}
          handleDragStart={handleDragStart}
          handleDragEnd={handleDragEnd}
          setProjectListScrollTop={setProjectListScrollTop}
          projectListScrollTop={projectListScrollTop}
          selectedIds={selectedIds}
          onToggleSelect={toggleSelected}
        />
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
      <BatchImportPbixModal
        open={batchImportOpen}
        onClose={() => setBatchImportOpen(false)}
        projects={projects}
        folders={folders}
        onImported={handleImported}
      />
      <BulkDryRunSummaryModal
        open={!!bulkDryRunResults}
        onClose={() => setBulkDryRunResults(null)}
        results={bulkDryRunResults || []}
        onOpenProject={(projectId) => {
          setBulkDryRunResults(null);
          navigate(`/projects/${projectId}/edit`);
        }}
      />
    </div>
  );
}

/* ─── Sidebar helpers ─── */
function FolderStatusDistribution({ projects = [] }) {
  if (projects.length === 0) return null;
  
  const total = projects.length;
  const counts = projects.reduce((acc, p) => {
    const s = (p.status || 'idle').toLowerCase();
    acc[s] = (acc[s] || 0) + 1;
    return acc;
  }, {});

  const statuses = [
    { key: 'failed', color: 'var(--color-error)' },
    { key: 'error', color: 'var(--color-error)' },
    { key: 'warning', color: 'var(--color-warning)' },
    { key: 'running', color: 'var(--accent-blue)' },
    { key: 'success', color: 'var(--color-success)' },
    { key: 'active', color: 'var(--color-success)' },
  ];

  return (
    <div style={{ 
      display: 'flex', 
      height: 6, 
      width: 60, 
      background: 'var(--bg-surface-raised, #1a1d23)', 
      borderRadius: 99, 
      overflow: 'hidden',
      marginRight: 8
    }}>
      {statuses.map(s => {
        const count = counts[s.key] || 0;
        if (count === 0) return null;
        return (
          <div 
            key={s.key} 
            style={{ 
              width: `${(count / total) * 100}%`, 
              height: '100%', 
              background: s.color 
            }} 
          />
        );
      })}
    </div>
  );
}

function SidebarItem({ color, label, active, onClick, icon, isDraggingGlobal, isDragOver, onDragOver, onDragLeave, onDrop }) {
  return (
    <button
      onClick={onClick}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '8px 12px', width: '100%', border: 'none',
        background: isDragOver ? 'var(--accent-blue)18' : active ? 'var(--accent-blue)14' : 'transparent',
        color: isDragOver || active ? 'var(--accent-blue)' : 'var(--text-secondary)',
        fontSize: 12, fontWeight: active ? 600 : 400, cursor: 'pointer',
        textAlign: 'left',
        borderLeft: isDragOver ? '3px solid var(--accent-blue)' : '3px solid transparent',
        transition: 'all 0.2s',
        animation: isDraggingGlobal && !isDragOver ? 'pulse 2s infinite' : 'none',
      }}
    >
      {icon || <Folder size={13} style={{ color }} />}
      {label}
    </button>
  );
}

function SidebarFolder({
  folder, projectsInFolder = [], active, onClick,
  isRenaming, renameValue, onRenameChange, onRenameSubmit, onRenameStart,
  onDelete, isDragOver, isDraggingGlobal, onDragOver, onDragLeave, onDrop,
  onRunAll
}) {
  const [hover, setHover] = useState(false);

  return (
    <div
      role="button"
      tabIndex={0}
      style={{
        display: 'flex', alignItems: 'center', gap: 6,
        padding: '6px 12px', cursor: 'pointer',
        background: isDragOver ? `${folder.color}22` : active ? 'var(--accent-blue)14' : 'transparent',
        borderLeft: isDragOver ? `4px solid ${folder.color}` : active ? `3px solid ${folder.color}` : '3px solid transparent',
        transition: 'all 0.2s',
        animation: isDraggingGlobal && !isDragOver ? 'pulse 2s infinite' : 'none',
        position: 'relative',
        minHeight: 36
      }}
      className="hover:bg-white/5"
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
      
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {hover && !isRenaming ? (
          <div className="flex items-center gap-2 animate-fade-in">
            <button 
              onClick={e => { e.stopPropagation(); onRunAll(); }}
              title="Run All"
              style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: 1 }}
              className="hover:text-white"
            >
              <Play size={12} fill="currentColor" />
            </button>
            <button onClick={e => { e.stopPropagation(); onRenameStart(); }} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: 1 }} className="hover:text-white">
              <Edit3 size={12} />
            </button>
            <button onClick={e => { e.stopPropagation(); onDelete(); }} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: 1 }} className="hover:text-white">
              <Trash2 size={12} />
            </button>
          </div>
        ) : (
          <>
            <FolderStatusDistribution projects={projectsInFolder} />
            <span style={{ fontSize: 10, color: 'var(--text-tertiary)', flexShrink: 0 }}>({projectsInFolder.length})</span>
          </>
        )}
      </div>
    </div>
  );
}

/* ─── Project Card ─── */
const ProjectCard = React.memo(function ProjectCard({
  project, menuOpen, onMenuToggle,
  onViewDetail, onConfigure, onRunNow, isRunning = false, onDuplicate, onExport, onDelete,
  onDragStart, onDragEnd,
  selected = false, onToggleSelect,
}) {
  const navigate = useNavigate();
  const isOpen = menuOpen === project.id;
  const sourceKey = sourceKeyOf(project);
  const { activeRuns } = useContext(SyncContext);
  const myRun = activeRuns.find(
    run =>
      String(run.projectId) === String(project.id) ||
      String(run.project_id) === String(project.id)
  );
  const status = myRun ? myRun.status : (project.status || 'Idle');
  const syncInFlight = isRunning || status === 'Running';

  const handleSyncClick = async () => {
    if (syncInFlight) return;
    await onRunNow();
  };

  return (
    <div
      onClick={() => navigate(`/projects/${project.id}`)}
      draggable
      onDragStart={e => onDragStart(e, project.id)}
      onDragEnd={onDragEnd}
      style={{
        background: 'var(--bg-surface)',
        border: `1px solid ${selected ? 'var(--accent-blue)' : 'var(--border-main)'}`,
        borderRadius: 10, padding: 18,
        display: 'flex', flexDirection: 'column',
        cursor: 'pointer', position: 'relative',
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
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 1, minWidth: 0 }}>
          <div
            onClick={e => { e.stopPropagation(); onToggleSelect?.(project.id); }}
            title={selected ? 'Deselect' : 'Select'}
            style={{
              width: 18, height: 18, borderRadius: 5, flexShrink: 0, cursor: 'pointer',
              border: `1.5px solid ${selected ? 'var(--accent-blue)' : 'var(--border-main)'}`,
              background: selected ? 'var(--accent-blue)' : 'transparent',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
          >
            {selected && <Check size={12} color="#fff" />}
          </div>
          <div style={{
            width: 40, height: 40, borderRadius: 10,
            background: 'var(--color-accent-faint)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 20,
            flexShrink: 0,
          }}>
            {renderSourceIcon(sourceKey, 20)}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', lineHeight: 1.2 }}>
              <span style={{
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                display: 'block',
              }}>
                {project.name}
              </span>
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {sourceKey || '—'}
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
});

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

/* ─── Virtual grid wrapper ─── */
const CARD_HEIGHT = 160; // approximate card height in px
const CARD_MIN_WIDTH = 290;
const GRID_GAP = 14;

function VirtualProjectGrid({
  filtered, loading, searchQuery,
  menuOpen, setMenuOpen, setActiveProjectId, setDetailProject,
  navigate, handleRunNow, runningProjectIds,
  handleDuplicate, handleExportSingle, handleDelete,
  handleDragStart, handleDragEnd,
  setProjectListScrollTop, projectListScrollTop,
  selectedIds, onToggleSelect,
}) {
  const scrollRef = useRef(null);

  // Compute column count from container width
  const [colCount, setColCount] = useState(3);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const update = () => {
      const w = el.clientWidth;
      setColCount(Math.max(1, Math.floor((w + GRID_GAP) / (CARD_MIN_WIDTH + GRID_GAP))));
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Build rows from flat list
  const rows = useMemo(() => {
    const out = [];
    for (let i = 0; i < filtered.length; i += colCount) {
      out.push(filtered.slice(i, i + colCount));
    }
    return out;
  }, [filtered, colCount]);

  const rowVirtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => CARD_HEIGHT + GRID_GAP,
    overscan: 4,
  });

  // Restore scroll position
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !Number.isFinite(projectListScrollTop)) return;
    el.scrollTop = projectListScrollTop;
  }, [projectListScrollTop, loading]);

  if (loading) {
    return (
      <div style={{ padding: '80px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 }}>
        Loading projects…
      </div>
    );
  }
  if (filtered.length === 0 && !searchQuery) {
    return (
      <EmptyState
        icon={<FolderOpen size={26} />}
        title="No projects yet"
        description="Create your first project to start connecting sources and building semantic models."
        actionLabel="Create Project"
        onAction={() => navigate('/projects/new', { state: { fresh: true } })}
      />
    );
  }
  if (filtered.length === 0) {
    return (
      <div style={{ padding: '80px 0', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 13 }}>
        No projects match "{searchQuery}"
      </div>
    );
  }

  return (
    <div
      id="projects-grid-scroll"
      ref={scrollRef}
      onScroll={(e) => setProjectListScrollTop(e.currentTarget.scrollTop)}
      style={{ flex: 1, overflowY: 'auto', padding: '4px 0 28px' }}
    >
      <div style={{ height: rowVirtualizer.getTotalSize(), position: 'relative' }}>
        {rowVirtualizer.getVirtualItems().map((virtualRow) => {
          const rowProjects = rows[virtualRow.index];
          return (
            <div
              key={virtualRow.key}
              style={{
                position: 'absolute',
                top: virtualRow.start,
                left: 0,
                right: 0,
                display: 'grid',
                gridTemplateColumns: `repeat(${colCount}, minmax(0, 1fr))`,
                gap: GRID_GAP,
                paddingBottom: GRID_GAP,
              }}
            >
              {rowProjects.map(project => (
                <ProjectCard
                  key={project.id}
                  project={project}
                  menuOpen={menuOpen}
                  onMenuToggle={setMenuOpen}
                  onViewDetail={() => { setActiveProjectId(project.id); setDetailProject(project); }}
                  onConfigure={() => { setActiveProjectId(project.id); navigate(`/projects/${project.id}/edit`); }}
                  onRunNow={() => handleRunNow(project)}
                  isRunning={runningProjectIds.has(project.id)}
                  onDuplicate={() => handleDuplicate(project)}
                  onExport={() => handleExportSingle(project)}
                  onDelete={() => handleDelete(project)}
                  onDragStart={handleDragStart}
                  onDragEnd={handleDragEnd}
                  selected={selectedIds.has(project.id)}
                  onToggleSelect={onToggleSelect}
                />
              ))}
            </div>
          );
        })}
      </div>
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


