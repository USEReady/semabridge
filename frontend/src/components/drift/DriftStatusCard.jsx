import React from 'react';
import {
  CheckCircle2,
  AlertTriangle,
  Clock,
  HelpCircle,
  RefreshCw,
  Rocket,
  Settings,
  Database,
  Layers,
} from 'lucide-react';
import {
  getStatusDetails,
  getReasonDetails,
  CATEGORY_DETAILS,
} from '../../utils/driftLabels';

/**
 * Actionable Drift Status Card
 * Renders one of 3 distinct states:
 * 1. In Sync: Healthy green banner with check time and baseline timestamp
 * 2. Drift Detected: Breakdown of counts by category
 * 3. Needs Attention / Not Checked: Friendly explanation and dedicated primary action
 */
export default function DriftStatusCard({
  status,
  reasonCode,
  reasonMessage,
  checkedAt,
  baselineAt,
  baselineSource,
  metricsCompared = 0,
  driftDetected = 0,
  totalCount,
  totalUnfilteredCount,
  pendingCount,
  activeStatus = 'all',
  categoryCounts = {},
  selectedCategory = 'ALL',
  onSelectCategory,
  showCheckButton = false,
  onCheckNow,
  onDeploy,
  onOpenSettings,
  onSelectView,
  busy = false,
  formatTime = (t) => t,
}) {
  const isBaselineMissing = !baselineSource || baselineSource === 'missing';
  const isLiveCapture = baselineSource === 'live_capture';

  // A live-capture baseline or missing baseline must never be shown as In Sync
  let effectiveStatus = status;
  let effectiveReasonCode = reasonCode;
  let effectiveReasonMsg = reasonMessage;

  if (isLiveCapture) {
    effectiveStatus = 'NOT_CHECKED';
    if (!effectiveReasonCode) effectiveReasonCode = 'LIVE_CAPTURE_BASELINE';
  } else if (isBaselineMissing && (status === 'IN_SYNC' || !status)) {
    effectiveStatus = 'NOT_CHECKED';
    if (!effectiveReasonCode) effectiveReasonCode = 'NO_DEPLOY_BASELINE';
  }

  const statusInfo = getStatusDetails(effectiveStatus);
  const reasonInfo = getReasonDetails(effectiveReasonCode, effectiveReasonMsg);

  // Render Action Button based on actionType
  const renderAction = () => {
    if (!reasonInfo.actionType || !reasonInfo.actionLabel) return null;
    switch (reasonInfo.actionType) {
      case 'deploy':
        return (
          <button
            type="button"
            onClick={onDeploy}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '6px 14px',
              borderRadius: 6,
              background: 'var(--color-primary, #3b82f6)',
              color: '#ffffff',
              border: '1px solid var(--color-primary, #3b82f6)',
              fontSize: 12.5,
              fontWeight: 500,
              cursor: 'pointer',
            }}
          >
            <Rocket size={13} /> {reasonInfo.actionLabel}
          </button>
        );
      case 'settings':
        return (
          <button
            type="button"
            onClick={onOpenSettings}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '6px 14px',
              borderRadius: 6,
              background: 'var(--color-primary, #3b82f6)',
              color: '#ffffff',
              border: '1px solid var(--color-primary, #3b82f6)',
              fontSize: 12.5,
              fontWeight: 500,
              cursor: 'pointer',
            }}
          >
            <Settings size={13} /> {reasonInfo.actionLabel}
          </button>
        );
      case 'view_selector':
        return (
          <button
            type="button"
            onClick={onSelectView}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '6px 14px',
              borderRadius: 6,
              background: 'var(--color-primary, #3b82f6)',
              color: '#ffffff',
              border: '1px solid var(--color-primary, #3b82f6)',
              fontSize: 12.5,
              fontWeight: 500,
              cursor: 'pointer',
            }}
          >
            <Layers size={13} /> {reasonInfo.actionLabel}
          </button>
        );
      case 'check_now':
      case 'refresh':
      default:
        return (
          <button
            type="button"
            onClick={onCheckNow}
            disabled={busy}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '6px 14px',
              borderRadius: 6,
              background: 'var(--color-primary, #3b82f6)',
              color: '#ffffff',
              border: '1px solid var(--color-primary, #3b82f6)',
              fontSize: 12.5,
              fontWeight: 500,
              cursor: busy ? 'not-allowed' : 'pointer',
              opacity: busy ? 0.7 : 1,
            }}
          >
            <RefreshCw size={13} className={busy ? 'sb-spin' : ''} /> {reasonInfo.actionLabel || 'Check Drift Now'}
          </button>
        );
    }
  };

  // State 1: In Sync
  if (effectiveStatus === 'IN_SYNC') {
    return (
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '16px 20px',
          borderRadius: 10,
          background: 'rgba(34, 197, 94, 0.08)',
          border: '1px solid rgba(34, 197, 94, 0.25)',
          gap: 16,
          flexWrap: 'wrap',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div
            style={{
              width: 38,
              height: 38,
              borderRadius: '50%',
              background: 'rgba(34, 197, 94, 0.15)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--color-success, #22c55e)',
              flexShrink: 0,
            }}
          >
            <CheckCircle2 size={20} />
          </div>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
              <span style={{ fontSize: 15, fontWeight: 600, color: 'var(--color-success, #22c55e)' }}>
                In Sync
              </span>
              <span
                style={{
                  fontSize: 11,
                  padding: '2px 8px',
                  borderRadius: 10,
                  background: 'rgba(34, 197, 94, 0.15)',
                  color: 'var(--color-success, #22c55e)',
                  fontWeight: 600,
                }}
              >
                0 Drift
              </span>
            </div>
            <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', display: 'flex', gap: 12, flexWrap: 'wrap' }}>
              <span>Checked {checkedAt ? formatTime(checkedAt) : 'just now'}</span>
              <span>·</span>
              <span>{metricsCompared} metric(s) verified</span>
              {baselineAt && (
                <>
                  <span>·</span>
                  <span>Baseline from {formatTime(baselineAt)}</span>
                </>
              )}
            </div>
          </div>
        </div>

        {showCheckButton && (
          <button
            type="button"
            onClick={onCheckNow}
            disabled={busy}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '6px 14px',
              borderRadius: 6,
              background: 'var(--bg-surface)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border-main)',
              fontSize: 12.5,
              cursor: busy ? 'not-allowed' : 'pointer',
            }}
          >
            <RefreshCw size={13} className={busy ? 'sb-spin' : ''} /> Check Drift Now
          </button>
        )}
      </div>
    );
  }

  // State 2: Drift Detected
  if (effectiveStatus === 'DRIFT_DETECTED') {
    return (
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
          padding: '16px 20px',
          borderRadius: 10,
          background: 'rgba(239, 68, 68, 0.06)',
          border: '1px solid rgba(239, 68, 68, 0.22)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div
              style={{
                width: 38,
                height: 38,
                borderRadius: '50%',
                background: 'rgba(239, 68, 68, 0.12)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: 'var(--color-danger, #ef4444)',
                flexShrink: 0,
              }}
            >
              <AlertTriangle size={20} />
            </div>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <span style={{ fontSize: 15, fontWeight: 600, color: 'var(--color-danger, #ef4444)' }}>
                  Drift Detected
                </span>
                <span
                  style={{
                    fontSize: 11,
                    padding: '2px 8px',
                    borderRadius: 10,
                    background: 'rgba(239, 68, 68, 0.15)',
                    color: 'var(--color-danger, #ef4444)',
                    fontWeight: 600,
                  }}
                >
                  {pendingCount !== undefined && pendingCount > 0
                    ? (totalUnfilteredCount && totalUnfilteredCount > pendingCount
                        ? `${pendingCount} pending of ${totalUnfilteredCount}`
                        : `${pendingCount} drift event(s) need review`)
                    : `${totalCount ?? driftDetected} event(s)`}
                </span>
              </div>
              <div style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>
                Checked {checkedAt ? formatTime(checkedAt) : 'just now'} · {metricsCompared} object(s) compared
              </div>
            </div>
          </div>

          {showCheckButton && (
            <button
              type="button"
              onClick={onCheckNow}
              disabled={busy}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                padding: '6px 14px',
                borderRadius: 6,
                background: 'var(--color-primary, #3b82f6)',
                color: '#ffffff',
                border: '1px solid var(--color-primary, #3b82f6)',
                fontSize: 12.5,
                fontWeight: 500,
                cursor: busy ? 'not-allowed' : 'pointer',
              }}
            >
              <RefreshCw size={13} className={busy ? 'sb-spin' : ''} /> Check Drift Now
            </button>
          )}
        </div>

        {/* Category Breakdown Badges (Multi-select Checkbox Filters) */}
        {(() => {
          const selectedList = String(selectedCategory || 'ALL')
            .split(',')
            .map((s) => s.trim().toUpperCase())
            .filter((s) => s && s !== 'ALL');
          const isAll = selectedList.length === 0 || String(selectedCategory).toUpperCase() === 'ALL';

          const handleToggleCategory = (catKey) => {
            if (!onSelectCategory) return;
            if (catKey === 'ALL') {
              onSelectCategory('ALL');
              return;
            }
            const target = catKey.toUpperCase();
            let nextList;
            if (selectedList.includes(target)) {
              nextList = selectedList.filter((k) => k !== target);
            } else {
              nextList = [...selectedList, target];
            }
            const allCatKeys = Object.keys(CATEGORY_DETAILS).map((k) => k.toUpperCase());
            if (nextList.length === 0 || nextList.length >= allCatKeys.length) {
              onSelectCategory('ALL');
            } else {
              onSelectCategory(nextList.join(','));
            }
          };

          return (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', paddingTop: 4 }}>
              {onSelectCategory && (
                <button
                  type="button"
                  onClick={() => handleToggleCategory('ALL')}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                    padding: '4px 10px',
                    borderRadius: 14,
                    fontSize: 12,
                    background: isAll ? 'rgba(59, 130, 246, 0.15)' : 'var(--bg-surface)',
                    border: isAll ? '1px solid var(--accent-blue, #3b82f6)' : '1px solid var(--border-main)',
                    color: isAll ? 'var(--accent-blue, #3b82f6)' : 'var(--text-secondary)',
                    fontWeight: 600,
                    cursor: 'pointer',
                  }}
                  title="Show all drift categories"
                >
                  <input
                    type="checkbox"
                    checked={isAll}
                    onChange={() => handleToggleCategory('ALL')}
                    style={{ cursor: 'pointer', accentColor: 'var(--accent-blue)', margin: 0 }}
                  />
                  <span>All</span>
                  <span style={{ fontSize: 11, opacity: 0.85 }}>({categoryCounts?.ALL !== undefined ? categoryCounts.ALL : (totalCount ?? driftDetected)})</span>
                </button>
              )}
              {Object.entries(CATEGORY_DETAILS).map(([catKey, catMeta]) => {
                const count = categoryCounts[catKey] || 0;
                const isSelected = !isAll && selectedList.includes(catKey.toUpperCase());
                const isClickable = Boolean(onSelectCategory);
                return (
                  <button
                    key={catKey}
                    type="button"
                    onClick={() => onSelectCategory && handleToggleCategory(catKey)}
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 6,
                      padding: '4px 10px',
                      borderRadius: 14,
                      fontSize: 12,
                      background: isSelected ? 'var(--bg-surface-raised)' : 'var(--bg-surface)',
                      border: isSelected ? `1.5px solid ${catMeta.color}` : '1px solid var(--border-main)',
                      color: count > 0 ? catMeta.color : 'var(--text-secondary)',
                      fontWeight: count > 0 || isSelected ? 600 : 400,
                      cursor: isClickable ? 'pointer' : 'default',
                      boxShadow: isSelected ? `0 0 0 1px ${catMeta.color}` : 'none',
                      transition: 'all 0.15s ease',
                    }}
                    title={`Toggle ${catMeta.label} filter`}
                  >
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={() => onSelectCategory && handleToggleCategory(catKey)}
                      style={{ cursor: 'pointer', accentColor: catMeta.color, margin: 0 }}
                    />
                    <span>{catMeta.label}</span>
                    <span style={{ fontSize: 11, opacity: 0.85 }}>({count})</span>
                  </button>
                );
              })}
            </div>
          );
        })()}
      </div>
    );
  }

  // State 3: Needs Attention / Not Checked / No Check Yet
  const isNeverChecked = effectiveStatus === 'NEVER_CHECKED';
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '16px 20px',
        borderRadius: 10,
        background: isNeverChecked ? 'rgba(148, 163, 184, 0.06)' : statusInfo.bg,
        border: `1px solid ${isNeverChecked ? 'rgba(148, 163, 184, 0.22)' : statusInfo.border}`,
        gap: 16,
        flexWrap: 'wrap',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <div
          style={{
            width: 38,
            height: 38,
            borderRadius: '50%',
            background: isNeverChecked ? 'rgba(148, 163, 184, 0.12)' : `${statusInfo.color}15`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: isNeverChecked ? 'var(--text-secondary)' : statusInfo.color,
            flexShrink: 0,
          }}
        >
          {isNeverChecked ? <Clock size={20} /> : <HelpCircle size={20} />}
        </div>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <span
              style={{
                fontSize: 15,
                fontWeight: 600,
                color: isNeverChecked ? 'var(--text-primary)' : statusInfo.color,
              }}
            >
              {reasonInfo.title}
            </span>
            <span
              style={{
                fontSize: 11,
                padding: '2px 8px',
                borderRadius: 10,
                background: 'var(--bg-surface)',
                border: '1px solid var(--border-main)',
                color: 'var(--text-secondary)',
                fontWeight: 500,
              }}
            >
              {reasonInfo.badge}
            </span>
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--text-secondary)', maxWidth: 560, lineHeight: 1.4 }}>
            {reasonInfo.explanation}
          </div>
        </div>
      </div>

      <div>{renderAction()}</div>
    </div>
  );
}
