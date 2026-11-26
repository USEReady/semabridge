import { useState, useEffect, useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { authFetch } from '../utils/api';

/**
 * Single id normalization helper used across the application.
 * Strips leading 'proj-', trims, and lowercases.
 */
export function normalizeProjectId(id) {
  if (!id) return '';
  return String(id).trim().toLowerCase().replace(/^proj-/, '');
}

/**
 * Flexible project matching helper: checks exact match or normalized match.
 */
export function matchesProjectId(a, b) {
  if (!a || !b) return false;
  if (a === b) return true;
  return normalizeProjectId(a) === normalizeProjectId(b);
}

const STORAGE_KEY = 'semabridge_selected_project';

export function useProjects() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Read initial selection from URL, fallback to stored selection.
  const urlPid = searchParams.get('project') || '';
  const storedPid = typeof window !== 'undefined' ? (localStorage.getItem(STORAGE_KEY) || '') : '';
  const initialPid = urlPid || storedPid;

  const [selectedProjectId, setSelectedProjectIdState] = useState(initialPid);

  // Read warehouse filter from URL
  const urlWarehouse = searchParams.get('warehouse') || '';
  const [selectedWarehouse, setSelectedWarehouseState] = useState(urlWarehouse);

  const fetchAllProjects = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      let all = [];
      let offset = 0;
      const limit = 200;
      let hasMore = true;

      while (hasMore) {
        const res = await authFetch(`/api/projects?limit=${limit}&offset=${offset}`);
        if (!res.ok) {
          throw new Error(`Failed to load projects: ${res.statusText}`);
        }
        const data = await res.json();
        const batch = Array.isArray(data) ? data : (data?.projects || []);
        all = all.concat(batch);
        if (batch.length < limit || all.length >= 1000) {
          hasMore = false;
        } else {
          offset += limit;
        }
      }

      setProjects(all);
    } catch (err) {
      console.error('Error fetching projects:', err);
      setError(String(err));
      setProjects([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let ignore = false;
    const timer = setTimeout(() => {
      if (!ignore) {
        fetchAllProjects();
      }
    }, 0);
    return () => {
      ignore = true;
      clearTimeout(timer);
    };
  }, [fetchAllProjects]);

  // Keep project selection in sync with URL query param if present
  useEffect(() => {
    const qProject = searchParams.get('project');
    if (qProject && qProject !== selectedProjectId) {
      const timer = setTimeout(() => {
        setSelectedProjectIdState(qProject);
        if (typeof window !== 'undefined') {
          localStorage.setItem(STORAGE_KEY, qProject);
        }
      }, 0);
      return () => clearTimeout(timer);
    }
  }, [searchParams, selectedProjectId]);

  // Keep warehouse selection in sync with URL query param
  useEffect(() => {
    const qWarehouse = searchParams.get('warehouse') || '';
    if (qWarehouse !== selectedWarehouse) {
      const timer = setTimeout(() => {
        setSelectedWarehouseState(qWarehouse);
      }, 0);
      return () => clearTimeout(timer);
    }
  }, [searchParams, selectedWarehouse]);

  const setSelectedProjectId = useCallback((pid) => {
    const cleanId = String(pid || '').trim();
    setSelectedProjectIdState(cleanId);
    if (typeof window !== 'undefined') {
      if (cleanId) {
        localStorage.setItem(STORAGE_KEY, cleanId);
      } else {
        localStorage.removeItem(STORAGE_KEY);
      }
    }
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (cleanId) {
        if (cleanId !== selectedProjectId) {
          next.delete('view');
        }
        next.set('project', cleanId);
      } else {
        next.delete('project');
        next.delete('view');
      }
      return next;
    });
  }, [selectedProjectId, setSearchParams]);

  // Warehouse filter setter: updates state & URL; clears selected project if filtered out
  const setSelectedWarehouse = useCallback((warehouse) => {
    const cleanWh = String(warehouse || '').trim();
    setSelectedWarehouseState(cleanWh);

    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (cleanWh) {
        next.set('warehouse', cleanWh);
      } else {
        next.delete('warehouse');
      }

      // Check if current project is filtered out by the new warehouse
      if (cleanWh && selectedProjectId) {
        const curProject = projects.find((p) => {
          const pId = p.id || p.project_id || '';
          return matchesProjectId(pId, selectedProjectId);
        });
        const curPlatform = String(curProject?.platform || curProject?.target_warehouse || '').toUpperCase().trim();
        const normCur = (!curPlatform || curPlatform === 'UNKNOWN' || curPlatform === 'NOT_SET') ? 'NOT_SET' : curPlatform;
        if (normCur !== cleanWh.toUpperCase()) {
          next.delete('project');
          next.delete('view');
          setSelectedProjectIdState('');
          if (typeof window !== 'undefined') {
            localStorage.removeItem(STORAGE_KEY);
          }
        }
      }
      return next;
    });
  }, [selectedProjectId, projects, setSearchParams]);

  // Resolve selectedProject object from projects list
  const selectedProject = useMemo(() => {
    if (!selectedProjectId) return null;
    return projects.find((p) => {
      const pId = p.id || p.project_id || '';
      const pName = p.name || '';
      return matchesProjectId(pId, selectedProjectId) || matchesProjectId(pName, selectedProjectId);
    }) || { id: selectedProjectId, name: selectedProjectId };
  }, [projects, selectedProjectId]);

  // Compute available warehouses and counts across all projects
  const { availableWarehouses, warehouseCounts } = useMemo(() => {
    const counts = {};
    projects.forEach((p) => {
      const raw = String(p.platform || p.target_warehouse || '').toUpperCase().trim();
      const plat = (!raw || raw === 'UNKNOWN' || raw === 'NOT_SET') ? 'NOT_SET' : raw;
      counts[plat] = (counts[plat] || 0) + 1;
    });
    const regular = Object.keys(counts).filter((k) => k !== 'NOT_SET').sort();
    if (counts['NOT_SET']) {
      regular.push('NOT_SET');
    }
    return {
      availableWarehouses: regular,
      warehouseCounts: counts,
    };
  }, [projects]);

  // Filter projects by selected warehouse
  const filteredProjects = useMemo(() => {
    if (!selectedWarehouse) return projects;
    const target = selectedWarehouse.toUpperCase().trim();
    return projects.filter((p) => {
      const raw = String(p.platform || p.target_warehouse || '').toUpperCase().trim();
      const plat = (!raw || raw === 'UNKNOWN' || raw === 'NOT_SET') ? 'NOT_SET' : raw;
      return plat === target;
    });
  }, [projects, selectedWarehouse]);

  return {
    projects,
    filteredProjects,
    availableWarehouses,
    warehouseCounts,
    selectedWarehouse,
    setSelectedWarehouse,
    selectedProjectId,
    setSelectedProjectId,
    selectedProject,
    loading,
    error,
    refreshProjects: fetchAllProjects,
  };
}
