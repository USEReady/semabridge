import React, { useState } from 'react';
import {
  FileText,
  Database,
  Sparkles,
  Check,
  X,
  Copy,
  CheckCheck,
  Zap,
  ArrowRight,
  ShieldAlert,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';
import { stripDiffMarkers } from '../../utils/driftLabels';
import FormattedDiffText from './FormattedDiffText';

const BTN = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  padding: '6px 12px',
  borderRadius: 7,
  border: '1px solid var(--border-main)',
  background: 'var(--bg-surface-raised)',
  color: 'var(--text-primary)',
  fontSize: 12,
  fontWeight: 600,
  cursor: 'pointer',
};

const BTN_PRIMARY = {
  ...BTN,
  borderColor: 'var(--accent-blue)',
  background: 'var(--accent-blue)',
  color: '#fff',
};

function ClampedCode({ text, label, icon: Icon, accentColor = 'var(--text-secondary)' }) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const cleanText = stripDiffMarkers(text);
  const content = cleanText || '—';
  const isLong = content.length > 60 || content.includes('\n');
  const isDesc = label?.toLowerCase().includes('description');

  const handleCopy = (e) => {
    e.stopPropagation();
    if (cleanText && cleanText !== '(no description)' && cleanText !== '(No description)') {
      navigator.clipboard.writeText(cleanText);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div
      style={{
        padding: '10px 12px',
        borderRadius: 8,
        background: 'var(--bg-surface)',
        border: '1px solid var(--border-main)',
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, fontWeight: 700, color: accentColor, textTransform: 'uppercase' }}>
          {Icon && <Icon size={12} />}
          <span>{label}</span>
        </div>
        {text && (
          <button
            type="button"
            onClick={handleCopy}
            style={{
              background: 'none',
              border: 'none',
              cursor: 'pointer',
              color: copied ? 'var(--color-success, #22c55e)' : 'var(--text-tertiary)',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
              fontSize: 11,
              padding: '2px 6px',
              borderRadius: 4,
            }}
            title={isDesc ? 'Copy description' : 'Copy formula'}
          >
            {copied ? <CheckCheck size={12} /> : <Copy size={12} />}
            <span>{copied ? 'Copied' : 'Copy'}</span>
          </button>
        )}
      </div>

      <div
        style={{
          fontFamily: isDesc ? 'inherit' : 'monospace',
          fontSize: 11.5,
          color: 'var(--text-primary)',
          wordBreak: 'break-word',
          whiteSpace: expanded ? 'pre-wrap' : 'normal',
          maxHeight: expanded ? 'none' : '4.5em',
          overflow: 'hidden',
          lineHeight: 1.5,
        }}
      >
        <FormattedDiffText text={text} />
      </div>

      {isLong && (
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          style={{
            background: 'none',
            border: 'none',
            color: 'var(--accent-blue, #3b82f6)',
            cursor: 'pointer',
            fontSize: 11,
            fontWeight: 600,
            alignSelf: 'flex-start',
            padding: 0,
            display: 'inline-flex',
            alignItems: 'center',
            gap: 2,
          }}
        >
          {expanded ? (
            <>Show less <ChevronUp size={11} /></>
          ) : (
            <>Show more ({content.length} chars) <ChevronDown size={11} /></>
          )}
        </button>
      )}
    </div>
  );
}

/**
 * DriftFindingDetail Component
 * Side-by-side comparison of baseline vs live warehouse definitions,
 * impacted objects, proposed resolutions, and reviewer actions.
 */
export default function DriftFindingDetail({
  event,
  onResolve = () => { },
  onOpenBlast = () => { },
  onOpenAIImpact = () => { },
}) {
  const [actionReason, setActionReason] = useState('');
  const [showReasonDialog, setShowReasonDialog] = useState(false);
  const [pendingAction, setPendingAction] = useState(null);

  if (!event) return null;

  const isPending = event.status === 'pending';
  const isDescDrift = (event.category || '').toUpperCase() === 'DESCRIPTION_DRIFT' || (event.drift_type || '').toUpperCase() === 'DESCRIPTION_MISMATCH';

  const baselineExpr = isDescDrift
    ? (event.baseline_marked || event.baseline_description || event.details?.baseline_description_normalized || '(No description)')
    : (event.bi_definition || event.baseline_expression || '');

  const liveExpr = isDescDrift
    ? (event.live_marked || event.live_description || event.details?.live_description_normalized || '(No description)')
    : (event.snowflake_definition || event.live_expression || '');

  const proposedExpr = isDescDrift
    ? 'update description in semantic view'
    : (event.proposed_snowflake_definition || event.proposed_resolution || 'redeploy from source');

  const targetView = event.target_view || event.semantic_view || '';

  const handleActionClick = (action) => {
    setPendingAction(action);
    setActionReason(action === 'ignore' ? 'Accepted variance' : 'Approved for sync');
    setShowReasonDialog(true);
  };

  const handleConfirmAction = () => {
    if (!actionReason.trim()) return;
    onResolve(event.id, pendingAction, actionReason.trim());
    setShowReasonDialog(false);
    setPendingAction(null);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* Underlying formula if this is description drift */}
      {isDescDrift && (event.live_expression || event.baseline_expression || event.bi_definition) && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--text-secondary)', background: 'rgba(255, 255, 255, 0.02)', padding: '6px 10px', borderRadius: 6, border: '1px solid var(--border-main)' }}>
          <span style={{ fontWeight: 600 }}>Formula / SQL:</span>
          <code style={{ color: 'var(--text-primary)', fontFamily: 'monospace' }}>
            {event.live_expression || event.baseline_expression || event.bi_definition}
          </code>
        </div>
      )}

      {/* Side-by-side DDL/Expression comparison */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 10 }}>
        <ClampedCode
          label={isDescDrift ? "Baseline Description" : "Baseline (BI Semantic Model)"}
          icon={FileText}
          text={baselineExpr}
          accentColor="var(--accent-blue, #3b82f6)"
        />

        <ClampedCode
          label={isDescDrift ? "Warehouse Description" : "Current Warehouse View"}
          icon={Database}
          text={liveExpr}
          accentColor="var(--text-secondary, #9ca3af)"
        />

        <ClampedCode
          label="Proposed Resolution"
          icon={Sparkles}
          text={proposedExpr}
          accentColor="var(--color-success, #22c55e)"
        />
      </div>

      {/* Impacted Objects & Target View info */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, fontSize: 11.5, color: 'var(--text-secondary)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          {event.table && (
            <span>
              <strong>Table:</strong>{' '}
              <code style={{ color: 'var(--text-primary)', fontWeight: 700, padding: '1px 5px', borderRadius: 3, background: 'var(--bg-surface)', border: '1px solid var(--border-main)' }}>
                {event.table}
              </code>
              {event.project_id && (
                <a
                  href={`/projects/${event.project_id}?tab=mappings&search=${encodeURIComponent(event.table)}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ color: 'var(--accent-blue)', marginLeft: 6, fontSize: 11, textDecoration: 'none' }}
                  title="View table mappings"
                >
                  View table ↗
                </a>
              )}
            </span>
          )}
          {targetView && (
            <span>
              <strong>Target View:</strong> <code style={{ color: 'var(--text-primary)' }}>{targetView}</code>
            </span>
          )}
          {event.affected_objects?.length > 0 && (
            <span>
              <strong>Impacted Objects:</strong> {event.affected_objects.join(', ')}
            </span>
          )}
        </div>

        {/* Action Toolbar */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <button
            type="button"
            onClick={() => onOpenBlast(
              event.object_name || event.metric_name || event.name,
              event.category || event.drift_type,
              event.kind,
              event.table,
              event.live_value || event.live_expression,
              event
            )}
            style={{
              ...BTN,
              background: 'rgba(168, 85, 247, 0.1)',
              borderColor: 'rgba(168, 85, 247, 0.35)',
              color: 'var(--accent-purple, #a855f7)',
              fontSize: 11,
              padding: '4px 10px',
            }}
            title="Check downstream impact across BI reports and AI queries"
          >
            <Zap size={12} /> Blast Radius
          </button>

          {isDescDrift && (
            <button
              type="button"
              onClick={() => onOpenAIImpact(event)}
              style={{
                ...BTN,
                background: 'rgba(59, 130, 246, 0.1)',
                borderColor: 'rgba(59, 130, 246, 0.35)',
                color: 'var(--accent-blue, #3b82f6)',
                fontSize: 11,
                padding: '4px 10px',
              }}
              title="Check AI Impact: Test natural language query routing under baseline vs live comment"
            >
              <Sparkles size={12} /> AI Impact Check
            </button>
          )}

          {isPending && (
            <>
              <button
                type="button"
                onClick={() => handleActionClick('approve')}
                style={{
                  ...BTN_PRIMARY,
                  background: 'var(--color-success, #22c55e)',
                  borderColor: 'var(--color-success, #22c55e)',
                  fontSize: 11,
                  padding: '4px 10px',
                }}
              >
                <Check size={12} /> Restore &amp; Sync
              </button>

              <button
                type="button"
                onClick={() => handleActionClick('accept')}
                style={{
                  ...BTN,
                  background: 'rgba(59, 130, 246, 0.1)',
                  borderColor: 'rgba(59, 130, 246, 0.35)',
                  color: 'var(--accent-blue, #3b82f6)',
                  fontSize: 11,
                  padding: '4px 10px',
                }}
              >
                Accept Change
              </button>

              <button
                type="button"
                onClick={() => handleActionClick('ignore')}
                style={{
                  ...BTN,
                  fontSize: 11,
                  padding: '4px 10px',
                }}
              >
                <X size={12} /> Ignore
              </button>
            </>
          )}
        </div>
      </div>

      {/* Reviewer Action Reason Dialog (Modal-like inline prompt) */}
      {showReasonDialog && (
        <div
          style={{
            padding: 12,
            borderRadius: 8,
            background: 'var(--bg-surface-raised)',
            border: '1px solid var(--accent-blue, #3b82f6)',
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
          }}
        >
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>
            Confirm Reviewer Action: {pendingAction?.toUpperCase()}
          </div>
          <input
            type="text"
            value={actionReason}
            onChange={(e) => setActionReason(e.target.value)}
            placeholder="Enter reason for audit trail (required)"
            style={{
              padding: '6px 10px',
              borderRadius: 6,
              background: 'var(--bg-surface)',
              border: '1px solid var(--border-main)',
              color: 'var(--text-primary)',
              fontSize: 12,
              outline: 'none',
              width: '100%',
              boxSizing: 'border-box',
            }}
            autoFocus
          />
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 6 }}>
            <button
              type="button"
              onClick={() => setShowReasonDialog(false)}
              style={{ ...BTN, padding: '4px 10px', fontSize: 11 }}
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={!actionReason.trim()}
              onClick={handleConfirmAction}
              style={{ ...BTN_PRIMARY, padding: '4px 12px', fontSize: 11, opacity: !actionReason.trim() ? 0.5 : 1 }}
            >
              Confirm
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
