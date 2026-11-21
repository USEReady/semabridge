import React from 'react';
import { Database, Layers } from 'lucide-react';
import { useDeployedViews, normalizeViewName, getViewBadgeLabel } from '../../hooks/useDeployedViews';

function formatDeployTime(isoStr) {
  if (!isoStr) return 'No deploy recorded';
  try {
    const d = new Date(isoStr);
    const diffSec = Math.floor((Date.now() - d.getTime()) / 1000);
    if (diffSec < 60) return 'just now';
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
    return `${Math.floor(diffSec / 86400)}d ago`;
  } catch {
    return isoStr;
  }
}

export default function DeployedViewSelector({
  projectId,
  projectSchema = '',
  style = {},
  onViewChange,
}) {
  const {
    views,
    loading,
    error,
    selectedView,
    selectView,
  } = useDeployedViews(projectId);

  if (!projectId || loading) {
    return null;
  }

  if (!views || views.length === 0) {
    return null;
  }

  // Find currently active view object
  const currentViewObj = views.find(
    (v) =>
      normalizeViewName(v.semantic_view) === normalizeViewName(selectedView) ||
      normalizeViewName(v.view) === normalizeViewName(selectedView)
  ) || views[0];

  const currentLabel = getViewBadgeLabel(currentViewObj, projectSchema);

  const getBadgeColors = (label) => {
    switch (label) {
      case 'Latest':
        return { bg: 'rgba(34, 197, 94, 0.12)', color: 'var(--color-success, #22c55e)' };
      case 'Older deploy':
        return { bg: 'rgba(234, 179, 8, 0.12)', color: 'var(--color-warning, #eab308)' };
      case 'Other schema':
        return { bg: 'rgba(59, 130, 246, 0.12)', color: 'var(--accent-blue, #3b82f6)' };
      case 'Not deployed by this project':
      default:
        return { bg: 'var(--bg-surface-raised, rgba(148, 163, 184, 0.12))', color: 'var(--text-tertiary, #94a3b8)' };
    }
  };

  const badgeStyle = getBadgeColors(currentLabel);

  // If there is only one deployed view, show plain label, no dropdown
  if (views.length === 1) {
    const v = views[0];
    const nameStr = v.schema ? `${v.schema}.${v.view}` : v.view;
    return (
      <div
        data-testid="deployed-view-single"
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 12,
          padding: '3px 8px',
          borderRadius: 6,
          background: 'var(--bg-surface-raised)',
          border: '1px solid var(--border-main)',
          color: 'var(--text-primary)',
          ...style,
        }}
      >
        <Database size={12} style={{ color: 'var(--accent-blue)', flexShrink: 0 }} />
        <span style={{ color: 'var(--text-secondary)', fontSize: 11.5 }}>View:</span>
        <span style={{ fontFamily: 'monospace', fontWeight: 600 }}>{nameStr}</span>
        <span
          style={{
            fontSize: 10,
            fontWeight: 700,
            padding: '1px 6px',
            borderRadius: 8,
            background: badgeStyle.bg,
            color: badgeStyle.color,
          }}
        >
          {currentLabel}
        </span>
      </div>
    );
  }

  // Multiple views -> interactive dropdown selector
  return (
    <div
      data-testid="deployed-view-selector"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        fontSize: 12,
        ...style,
      }}
    >
      <Database size={13} style={{ color: 'var(--accent-blue)', flexShrink: 0 }} />
      <span style={{ color: 'var(--text-secondary)', fontSize: 11.5, fontWeight: 600 }}>View:</span>
      <select
        aria-label="Select deployed semantic view"
        value={selectedView}
        onChange={(e) => {
          const val = e.target.value;
          selectView(val);
          if (onViewChange) onViewChange(val);
        }}
        style={{
          padding: '4px 8px',
          fontSize: 11.5,
          fontFamily: 'monospace',
          borderRadius: 6,
          border: '1px solid var(--border-main)',
          background: 'var(--bg-surface)',
          color: 'var(--text-primary)',
          cursor: 'pointer',
          outline: 'none',
          maxWidth: 320,
        }}
      >
        {views.map((v) => {
          const val = v.semantic_view || v.view;
          const label = getViewBadgeLabel(v, projectSchema);
          const time = formatDeployTime(v.deployed_at);
          const platform = v.platform || 'snowflake';
          const name = v.schema ? `${v.schema}.${v.view}` : v.view;
          return (
            <option key={val} value={val}>
              {`${name} · ${time} · ${platform} (${label})`}
            </option>
          );
        })}
      </select>
      <span
        style={{
          fontSize: 10,
          fontWeight: 700,
          padding: '1px 6px',
          borderRadius: 8,
          background: badgeStyle.bg,
          color: badgeStyle.color,
          whiteSpace: 'nowrap',
        }}
      >
        {currentLabel}
      </span>
    </div>
  );
}
