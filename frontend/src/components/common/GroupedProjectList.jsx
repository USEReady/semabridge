import React, { useState, useMemo, useRef, useCallback } from 'react';
import { ChevronDown, ChevronRight, Database, AlertCircle, Clock, CheckCircle2, Target } from 'lucide-react';
import {
  computeProjectDriftState,
  getDriftStateSortPriority,
  getReasonDetails,
} from '../../utils/driftLabels.js';
import EmptyState from './EmptyState.jsx';

const STORAGE_COLLAPSED_KEY = 'semabridge_drift_collapsed_sections';

function loadCollapsedSections() {
  if (typeof window === 'undefined') return {};
  try {
    const raw = localStorage.getItem(STORAGE_COLLAPSED_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function saveCollapsedSections(state) {
  if (typeof window === 'undefined') return;
  try {
    localStorage.setItem(STORAGE_COLLAPSED_KEY, JSON.stringify(state));
  } catch {
    // Ignore storage errors
  }
}

function cleanPid(id) {
  return String(id || '').trim().toLowerCase().replace(/^proj-/, '');
}

/**
 * GroupedProjectList
 *
 * Renders projects grouped into collapsible sections by warehouse platform.
 * - Dynamic platforms directly from data (never hardcoded in JSX)
 * - Section header shows platform name, count, and summary of states
 * - "Not set" section placed at the end with explanatory caption
 * - Inside sections, sorted:
 *     In drift mode: Needs Attention -> Drift Detected -> In Sync -> Never Checked
 *     In bitruth mode: Case count descending -> Alphabetical
 * - One-line project row with truncated name, hover tooltip, reason badge, and muted id
 * - Keyboard navigable with ArrowDown / ArrowUp
 * - Skeleton rows for smooth loading
 */
export default function GroupedProjectList({
  projects = [],
  targetInfo = {},
  allEvents = [],
  selectedProjectId = '',
  onSelectProject,
  selectedWarehouse = '',
  searchQuery = '',
  onClearFilters,
  loading = false,
  mode = 'drift', // 'drift' | 'bitruth'
  casesCounts = {},
  renderProjectBadge,
}) {
  const [collapsed, setCollapsed] = useState(loadCollapsedSections);
  const containerRef = useRef(null);

  const toggleSection = useCallback((platformKey) => {
    setCollapsed((prev) => {
      const next = { ...prev, [platformKey]: !prev[platformKey] };
      saveCollapsedSections(next);
      return next;
    });
  }, []);

  const getCaseCount = useCallback((p) => {
    if (!casesCounts) return 0;
    const pid = p?.id || p?.name || '';
    if (casesCounts[pid] !== undefined) return casesCounts[pid];
    const cpid = cleanPid(pid);
    if (casesCounts[cpid] !== undefined) return casesCounts[cpid];
    if (p?.name && casesCounts[p.name] !== undefined) return casesCounts[p.name];
    return 0;
  }, [casesCounts]);

  // Filter and group projects
  const { groupedSections, totalFilteredCount } = useMemo(() => {
    const cleanSearch = String(searchQuery || '').trim().toLowerCase();
    const cleanTargetWh = String(selectedWarehouse || '').trim().toUpperCase();

    // 1. Group projects by platform
    const groups = {};
    for (const p of projects) {
      const pid = p?.id || p?.name || '';
      const pName = p?.name || pid;
      if (cleanSearch) {
        const matchesName = pName.toLowerCase().includes(cleanSearch);
        const matchesId = pid.toLowerCase().includes(cleanSearch);
        if (!matchesName && !matchesId) continue;
      }

      const rawPlat = String(p?.platform || p?.target_warehouse || '').toUpperCase().trim();
      const platKey = (!rawPlat || rawPlat === 'UNKNOWN' || rawPlat === 'NOT_SET') ? 'NOT_SET' : rawPlat;

      // Filter by selected warehouse if specified
      if (cleanTargetWh && platKey !== cleanTargetWh) {
        continue;
      }

      if (!groups[platKey]) {
        groups[platKey] = [];
      }
      groups[platKey].push(p);
    }

    // 2. Build structured section data
    const sections = [];

    const platforms = Object.keys(groups);
    for (const plat of platforms) {
      const pList = groups[plat];

      if (mode === 'bitruth') {
        pList.sort((a, b) => {
          const cA = getCaseCount(a);
          const cB = getCaseCount(b);
          if (cA !== cB) return cB - cA;
          return (a.name || a.id || '').localeCompare(b.name || b.id || '');
        });

        const totalCases = pList.reduce((acc, p) => acc + getCaseCount(p), 0);
        const projectsWithCases = pList.filter((p) => getCaseCount(p) > 0).length;

        sections.push({
          platformKey: plat,
          label: plat === 'NOT_SET' ? 'Not set' : plat,
          isNotSet: plat === 'NOT_SET',
          projects: pList,
          summaryText: totalCases > 0
            ? `${totalCases} case${totalCases === 1 ? '' : 's'} (${projectsWithCases}/${pList.length} configured)`
            : '0 cases configured',
        });
      } else {
        // Drift mode sorting
        pList.sort((a, b) => {
          const sA = computeProjectDriftState(a, targetInfo, allEvents);
          const sB = computeProjectDriftState(b, targetInfo, allEvents);
          const pA = getDriftStateSortPriority(sA);
          const pB = getDriftStateSortPriority(sB);
          if (pA !== pB) return pA - pB;
          return (a.name || a.id || '').localeCompare(b.name || b.id || '');
        });

        let needsAttention = 0;
        let driftCount = 0;
        let inSyncCount = 0;
        let neverCheckedCount = 0;

        for (const p of pList) {
          const s = computeProjectDriftState(p, targetInfo, allEvents);
          const pri = getDriftStateSortPriority(s);
          if (pri === 0) needsAttention += 1;
          else if (pri === 1) driftCount += 1;
          else if (pri === 2) inSyncCount += 1;
          else neverCheckedCount += 1;
        }

        const summaryParts = [];
        if (needsAttention > 0) summaryParts.push(`${needsAttention} need attention`);
        if (driftCount > 0) summaryParts.push(`${driftCount} drift`);
        if (summaryParts.length === 0) {
          if (inSyncCount > 0) summaryParts.push(`${inSyncCount} in sync`);
          else if (neverCheckedCount > 0) summaryParts.push(`${neverCheckedCount} no check yet`);
        }

        sections.push({
          platformKey: plat,
          label: plat === 'NOT_SET' ? 'Not set' : plat,
          isNotSet: plat === 'NOT_SET',
          projects: pList,
          needsAttention,
          driftCount,
          inSyncCount,
          neverCheckedCount,
          summaryText: summaryParts.join(' · '),
        });
      }
    }

    // Sort sections: in drift mode by urgency (needs-attention, then drift), NOT_SET always at the end
    sections.sort((a, b) => {
      if (a.isNotSet && !b.isNotSet) return 1;
      if (!a.isNotSet && b.isNotSet) return -1;
      if (mode === 'drift') {
        const uA = a.needsAttention > 0 ? 0 : a.driftCount > 0 ? 1 : 2;
        const uB = b.needsAttention > 0 ? 0 : b.driftCount > 0 ? 1 : 2;
        if (uA !== uB) return uA - uB;
      }
      return a.label.localeCompare(b.label);
    });

    const totalFiltered = sections.reduce((acc, s) => acc + s.projects.length, 0);

    return {
      groupedSections: sections,
      totalFilteredCount: totalFiltered,
    };
  }, [projects, searchQuery, selectedWarehouse, targetInfo, allEvents, mode, getCaseCount]);

  // Flat list of projects for keyboard navigation
  const flatNavigableProjects = useMemo(() => {
    const list = [];
    for (const sec of groupedSections) {
      if (!collapsed[sec.platformKey]) {
        for (const p of sec.projects) {
          list.push(p);
        }
      }
    }
    return list;
  }, [groupedSections, collapsed]);

  const handleKeyDown = useCallback((e) => {
    if (!flatNavigableProjects.length) return;

    const currentIndex = flatNavigableProjects.findIndex(
      (p) => p.id === selectedProjectId || cleanPid(p.id) === cleanPid(selectedProjectId)
    );

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      const nextIndex = currentIndex < flatNavigableProjects.length - 1 ? currentIndex + 1 : 0;
      const nextProject = flatNavigableProjects[nextIndex];
      if (nextProject) {
        onSelectProject(nextProject.id || nextProject.name);
      }
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      const prevIndex = currentIndex > 0 ? currentIndex - 1 : flatNavigableProjects.length - 1;
      const prevProject = flatNavigableProjects[prevIndex];
      if (prevProject) {
        onSelectProject(prevProject.id || prevProject.name);
      }
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (currentIndex >= 0 && currentIndex < flatNavigableProjects.length) {
        const currentProject = flatNavigableProjects[currentIndex];
        onSelectProject(currentProject.id || currentProject.name);
      }
    }
  }, [flatNavigableProjects, selectedProjectId, onSelectProject]);

  // Loading Skeleton State
  if (loading && projects.length === 0) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: 8 }}>
        {[1, 2, 3, 4, 5, 6].map((i) => (
          <div
            key={i}
            style={{
              height: 36,
              borderRadius: 6,
              border: '1px solid var(--border-main)',
              background: 'var(--bg-surface-raised, rgba(255,255,255,0.03))',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '0 10px',
              opacity: 0.6,
            }}
          >
            <div style={{ width: '55%', height: 12, borderRadius: 4, background: 'var(--border-main)' }} />
            <div style={{ width: 64, height: 16, borderRadius: 10, background: 'var(--border-main)' }} />
          </div>
        ))}
      </div>
    );
  }

  // Zero Matches Empty State
  if (totalFilteredCount === 0) {
    const hasSearch = Boolean(searchQuery);
    const isFiltered = Boolean(selectedWarehouse || searchQuery);
    const whLabel = (selectedWarehouse === 'NOT_SET' || selectedWarehouse === 'UNKNOWN') ? 'No Warehouse Configured' : selectedWarehouse;
    const title = hasSearch && selectedWarehouse
      ? `No projects matching '${searchQuery}' in ${whLabel}`
      : hasSearch
      ? `No projects match '${searchQuery}'`
      : selectedWarehouse
      ? `No projects in ${whLabel}`
      : 'No projects available';
    const description = hasSearch && selectedWarehouse
      ? `No projects matching '${searchQuery}' were found in ${whLabel}. Try clearing your search.`
      : hasSearch
      ? 'Check your spelling or clear the search to see all projects.'
      : selectedWarehouse
      ? `No projects are configured for ${whLabel}.`
      : mode === 'bitruth'
      ? 'Deploy a project to begin verifying BI truth cases.'
      : 'Deploy a project to begin monitoring semantic view drift.';
    const actionLabel = hasSearch
      ? 'Clear search'
      : isFiltered
      ? 'Clear filters'
      : undefined;

    return (
      <div style={{ padding: 12 }}>
        <EmptyState
          icon={Database}
          title={title}
          description={description}
          actionLabel={actionLabel}
          onAction={isFiltered ? onClearFilters : undefined}
          compact
        />
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      tabIndex={0}
      onKeyDown={handleKeyDown}
      role="listbox"
      aria-label="Monitored Projects List"
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 12,
        outline: 'none',
      }}
    >
      {groupedSections.map((sec) => {
        const isCollapsed = Boolean(collapsed[sec.platformKey]);

        return (
          <div
            key={sec.platformKey}
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 4,
            }}
          >
            {/* Section Header */}
            <button
              type="button"
              onClick={() => toggleSection(sec.platformKey)}
              aria-expanded={!isCollapsed}
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                padding: '6px 8px',
                borderRadius: 6,
                background: 'var(--bg-surface-raised, rgba(255,255,255,0.02))',
                border: '1px solid var(--border-main)',
                color: 'var(--text-primary)',
                fontSize: 12,
                fontWeight: 600,
                cursor: 'pointer',
                textAlign: 'left',
                width: '100%',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
                {isCollapsed ? (
                  <ChevronRight size={14} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />
                ) : (
                  <ChevronDown size={14} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />
                )}
                <span style={{ textTransform: sec.isNotSet ? 'none' : 'uppercase', letterSpacing: '0.02em' }}>
                  {sec.label}
                </span>
                <span
                  style={{
                    fontSize: 11,
                    color: 'var(--text-tertiary)',
                    fontWeight: 500,
                  }}
                >
                  ({sec.projects.length})
                </span>
              </div>

              {sec.summaryText && (
                <span
                  style={{
                    fontSize: 10.5,
                    color: mode === 'drift' && sec.needsAttention > 0
                      ? 'var(--color-danger, #ef4444)'
                      : mode === 'drift' && sec.driftCount > 0
                      ? 'var(--color-warning, #f59e0b)'
                      : 'var(--text-tertiary)',
                    fontWeight: 500,
                    whiteSpace: 'nowrap',
                    marginLeft: 6,
                  }}
                >
                  {sec.summaryText}
                </span>
              )}
            </button>

            {/* Explanatory subtitle for unconfigured projects */}
            {!isCollapsed && sec.isNotSet && (
              <div
                style={{
                  fontSize: 11,
                  color: 'var(--text-tertiary)',
                  padding: '2px 8px 4px 8px',
                  fontStyle: 'italic',
                }}
              >
                No target warehouse configured for these projects
              </div>
            )}

            {/* Project Rows inside Section */}
            {!isCollapsed && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4, paddingLeft: 2 }}>
                {sec.projects.map((p) => {
                  const pid = p?.id || p?.name;
                  if (!pid) return null;
                  const isSelected = selectedProjectId === pid || cleanPid(selectedProjectId) === cleanPid(pid);

                  let badgeNode = null;
                  if (renderProjectBadge) {
                    badgeNode = renderProjectBadge(p, isSelected);
                  } else if (mode === 'bitruth') {
                    const cCount = getCaseCount(p);
                    const hasCases = cCount > 0;
                    badgeNode = (
                      <span
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 3.5,
                          padding: '1.5px 6px',
                          borderRadius: 10,
                          background: hasCases ? 'rgba(59, 130, 246, 0.1)' : 'var(--bg-surface-raised)',
                          color: hasCases ? 'var(--accent-blue, #3b82f6)' : 'var(--text-tertiary)',
                          border: hasCases ? '1px solid rgba(59, 130, 246, 0.3)' : '1px solid var(--border-main)',
                          fontSize: 10,
                          fontWeight: 600,
                          flexShrink: 0,
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {hasCases ? <Target size={10} /> : <Clock size={10} />}
                        {hasCases ? `${cCount} case${cCount === 1 ? '' : 's'}` : 'No cases'}
                      </span>
                    );
                  } else {
                    const dState = computeProjectDriftState(p, targetInfo, allEvents);
                    const reasonInfo = dState.reasonCode ? getReasonDetails(dState.reasonCode) : null;

                    let badgeText = 'No Check Yet';
                    let badgeBg = 'var(--bg-surface-raised)';
                    let badgeColor = 'var(--text-secondary)';
                    let badgeBorder = '1px solid var(--border-main)';
                    let badgeIcon = <Clock size={10} />;

                    if (dState.status === 'DRIFT_DETECTED' || dState.pendingCount > 0) {
                      badgeText = 'Drift Detected';
                      badgeBg = 'rgba(239, 68, 68, 0.1)';
                      badgeColor = 'var(--color-danger, #ef4444)';
                      badgeBorder = '1px solid rgba(239, 68, 68, 0.3)';
                      badgeIcon = <AlertCircle size={10} />;
                    } else if (dState.status === 'IN_SYNC') {
                      badgeText = 'In Sync';
                      badgeBg = 'rgba(16, 185, 129, 0.1)';
                      badgeColor = 'var(--color-success, #10b981)';
                      badgeBorder = '1px solid rgba(16, 185, 129, 0.3)';
                      badgeIcon = <CheckCircle2 size={10} />;
                    } else if (reasonInfo) {
                      badgeText = reasonInfo.badge || reasonInfo.title;
                      if (reasonInfo.severity === 'danger') {
                        badgeBg = 'rgba(239, 68, 68, 0.1)';
                        badgeColor = 'var(--color-danger, #ef4444)';
                        badgeBorder = '1px solid rgba(239, 68, 68, 0.3)';
                        badgeIcon = <AlertCircle size={10} />;
                      } else if (reasonInfo.severity === 'warning') {
                        badgeBg = 'rgba(245, 158, 11, 0.1)';
                        badgeColor = 'var(--color-warning, #f59e0b)';
                        badgeBorder = '1px solid rgba(245, 158, 11, 0.3)';
                        badgeIcon = <AlertCircle size={10} />;
                      }
                    }

                    badgeNode = (
                      <span
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 3.5,
                          padding: '1.5px 6px',
                          borderRadius: 10,
                          background: badgeBg,
                          color: badgeColor,
                          border: badgeBorder,
                          fontSize: 10,
                          fontWeight: 600,
                          flexShrink: 0,
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {badgeIcon}
                        {badgeText}
                      </span>
                    );
                  }

                  return (
                    <button
                      key={pid}
                      type="button"
                      role="option"
                      aria-selected={isSelected}
                      onClick={() => onSelectProject(pid)}
                      title={p?.name ? `${p.name} (${pid})` : pid}
                      onMouseEnter={(e) => {
                        if (!isSelected) e.currentTarget.style.background = 'var(--bg-surface-raised, rgba(255, 255, 255, 0.04))';
                      }}
                      onMouseLeave={(e) => {
                        if (!isSelected) e.currentTarget.style.background = 'transparent';
                      }}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        gap: 8,
                        padding: '6px 8px',
                        borderRadius: 6,
                        border: '1px solid',
                        borderColor: isSelected ? 'var(--accent-blue, #3b82f6)' : 'transparent',
                        background: isSelected ? 'rgba(59, 130, 246, 0.08)' : 'transparent',
                        cursor: 'pointer',
                        textAlign: 'left',
                        transition: 'background 0.12s ease, border-color 0.12s ease',
                        width: '100%',
                        boxSizing: 'border-box',
                        outline: 'none',
                      }}
                    >
                      {/* Name on the left, with muted monospace id */}
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 6,
                          minWidth: 0,
                          flex: 1,
                          overflow: 'hidden',
                        }}
                      >
                        <span
                          style={{
                            fontSize: 12.5,
                            fontWeight: isSelected ? 700 : 500,
                            color: isSelected ? 'var(--accent-blue, #3b82f6)' : 'var(--text-primary)',
                            whiteSpace: 'nowrap',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            flexShrink: 1,
                          }}
                        >
                          {p?.name || pid}
                        </span>

                        {p?.name && p?.name !== pid && (
                          <span
                            style={{
                              fontSize: 10,
                              color: 'var(--text-tertiary)',
                              fontFamily: 'monospace',
                              whiteSpace: 'nowrap',
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              flexShrink: 0,
                              opacity: 0.75,
                            }}
                          >
                            {pid}
                          </span>
                        )}
                      </div>

                      {/* State badge on the right */}
                      {badgeNode}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
