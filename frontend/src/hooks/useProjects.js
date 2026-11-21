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
  // Never default to first item, never select a project whose name contains 'comp'.
  const urlPid = searchParams.get('project') || '';
  const storedPid = typeof window !== 'undefined' ? (localStorage.getItem(STORAGE_KEY) || '') : '';
  const initialPid = urlPid || storedPid;

  const [selectedProjectId, setSelectedProjectIdState] = useState(initialPid);

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
    fetchAllProjects();
  }, [fetchAllProjects]);

  // Keep state in sync with URL query param if present
  useEffect(() => {
    const qProject = searchParams.get('project');
    if (qProject && qProject !== selectedProjectId) {
      setSelectedProjectIdState(qProject);
      if (typeof window !== 'undefined') {
        localStorage.setItem(STORAGE_KEY, qProject);
      }
    }
  }, [searchParams, selectedProjectId]);

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
        next.set('project', cleanId);
      } else {
        next.delete('project');
      }
      return next;
    });
  }, [setSearchParams]);

  // Resolve selectedProject object from projects list
  const selectedProject = useMemo(() => {
    if (!selectedProjectId) return null;
    return projects.find((p) => {
      const pId = p.id || p.project_id || '';
      const pName = p.name || '';
      return matchesProjectId(pId, selectedProjectId) || matchesProjectId(pName, selectedProjectId);
    }) || { id: selectedProjectId, name: selectedProjectId };
  }, [projects, selectedProjectId]);

  return {
    projects,
    selectedProjectId,
    setSelectedProjectId,
    selectedProject,
    loading,
    error,
    refreshProjects: fetchAllProjects,
  };
}
