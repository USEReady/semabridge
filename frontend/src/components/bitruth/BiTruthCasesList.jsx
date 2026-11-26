import React, { useState, useMemo } from 'react';
import {
  Search, Star, CheckCircle2, AlertTriangle, AlertCircle, HelpCircle,
  Calendar, Sparkles, ChevronRight,
} from 'lucide-react';

export default function BiTruthCasesList({
  cases = [],
  resultsMap = new Map(),
  selectedCaseId,
  onSelectCase,
  busy = false,
  onGenerate,
  onToggleCritical,
}) {
  const [searchTerm, setSearchTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState('all'); // 'all' | 'approved' | 'draft' | 'rejected' | 'critical'

  const counts = useMemo(() => {
    let approved = 0;
    let draft = 0;
    let rejected = 0;
    let critical = 0;
    for (const c of cases) {
      if (c.status === 'approved') approved++;
      if (c.status === 'draft') draft++;
      if (c.status === 'rejected') rejected++;
      if (c.is_critical) critical++;
    }
    return { all: cases.length, approved, draft, rejected, critical };
  }, [cases]);

  const filtered = useMemo(() => {
    let list = cases;
    if (statusFilter === 'approved') list = list.filter((c) => c.status === 'approved');
    else if (statusFilter === 'draft') list = list.filter((c) => c.status === 'draft');
    else if (statusFilter === 'rejected') list = list.filter((c) => c.status === 'rejected');
    else if (statusFilter === 'critical') list = list.filter((c) => c.is_critical);

    if (searchTerm.trim()) {
      const q = searchTerm.toLowerCase().trim();
      list = list.filter(
        (c) =>
          (c.question && c.question.toLowerCase().includes(q)) ||
          (c.metric_name && c.metric_name.toLowerCase().includes(q))
      );
    }
    return list;
  }, [cases, statusFilter, searchTerm]);

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        background: 'var(--bg-surface)',
        borderRadius: 10,
        border: '1px solid var(--border-main)',
        overflow: 'hidden',
      }}
    >
      {/* Header with Search and Filter tabs */}
      <div
        style={{
          padding: '10px 12px',
          borderBottom: '1px solid var(--border-main)',
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
          background: 'var(--bg-surface-raised)',
        }}
      >
        {/* Search Input */}
        <div style={{ position: 'relative', width: '100%' }}>
          <Search
            size={13}
            style={{
              position: 'absolute',
              left: 10,
              top: '50%',
              transform: 'translateY(-50%)',
              color: 'var(--text-tertiary)',
            }}
          />
          <input
            type="text"
            placeholder="Search questions, metrics..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            style={{
              width: '100%',
              boxSizing: 'border-box',
              padding: '6px 10px 6px 30px',
              borderRadius: 6,
              background: 'var(--bg-surface)',
              border: '1px solid var(--border-main)',
              color: 'var(--text-primary)',
              fontSize: 12,
              outline: 'none',
            }}
          />
          {searchTerm && (
            <button
              type="button"
              onClick={() => setSearchTerm('')}
              style={{
                position: 'absolute',
                right: 8,
                top: '50%',
                transform: 'translateY(-50%)',
                background: 'transparent',
                border: 'none',
                color: 'var(--text-tertiary)',
                cursor: 'pointer',
                fontSize: 11,
              }}
            >
              ✕
            </button>
          )}
        </div>

        {/* Filter Pills */}
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', alignItems: 'center' }}>
          {[
            { id: 'all', label: `All (${counts.all})` },
            { id: 'approved', label: `Approved (${counts.approved})` },
            { id: 'draft', label: `Draft (${counts.draft})` },
            { id: 'rejected', label: `Rejected (${counts.rejected})` },
            { id: 'critical', label: `Critical (${counts.critical})` },
          ].map((tab) => {
            const on = statusFilter === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => setStatusFilter(tab.id)}
                style={{
                  padding: '3px 8px',
                  borderRadius: 5,
                  border: `1px solid ${on ? 'var(--accent-blue)' : 'transparent'}`,
                  background: on ? 'rgba(59,130,246,0.12)' : 'transparent',
                  color: on ? 'var(--accent-blue)' : 'var(--text-secondary)',
                  fontSize: 11,
                  fontWeight: on ? 600 : 500,
                  cursor: 'pointer',
                  transition: 'all 0.15s ease',
                }}
              >
                {tab.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Scrollable Cases List */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: 8,
          display: 'flex',
          flexDirection: 'column',
          gap: 4,
        }}
      >
        {filtered.length === 0 ? (
          <div
            style={{
              padding: 32,
              textAlign: 'center',
              color: 'var(--text-tertiary)',
              fontSize: 12,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 8,
            }}
          >
            <div>No matching questions found.</div>
            {cases.length === 0 && (
              <button
                type="button"
                disabled={busy}
                onClick={onGenerate}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                  background: 'var(--accent-blue)',
                  color: '#fff',
                  padding: '6px 12px',
                  fontSize: 12,
                  borderRadius: 6,
                  border: 'none',
                  cursor: 'pointer',
                }}
              >
                <Sparkles size={13} /> {busy ? 'Generating…' : 'Generate Questions'}
              </button>
            )}
          </div>
        ) : (
          filtered.map((c) => {
            const isSelected = c.id === selectedCaseId;
            const res = resultsMap.get(c.id);
            const isCritical = Boolean(c.is_critical);
            const windowsCount = (c.windows || []).length;

            let resultBadge = null;
            if (res) {
              if (res.comparable === false) {
                resultBadge = (
                  <span
                    style={{
                      fontSize: 10,
                      fontWeight: 700,
                      padding: '1px 5px',
                      borderRadius: 3,
                      background: 'rgba(156,163,175,0.15)',
                      color: 'var(--text-tertiary)',
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 2,
                      whiteSpace: 'nowrap',
                    }}
                  >
                    <HelpCircle size={10} /> Refused
                  </span>
                );
              } else if (res.row_count_mismatch) {
                resultBadge = (
                  <span
                    style={{
                      fontSize: 10,
                      fontWeight: 700,
                      padding: '1px 5px',
                      borderRadius: 3,
                      background: 'rgba(234,179,8,0.15)',
                      color: 'var(--color-warning, #eab308)',
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 2,
                      whiteSpace: 'nowrap',
                    }}
                  >
                    <AlertTriangle size={10} /> Mismatch
                  </span>
                );
              } else if (res.passed) {
                resultBadge = (
                  <span
                    style={{
                      fontSize: 10,
                      fontWeight: 700,
                      padding: '1px 5px',
                      borderRadius: 3,
                      background: 'rgba(34,197,94,0.15)',
                      color: 'var(--color-success, #22c55e)',
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 2,
                      whiteSpace: 'nowrap',
                    }}
                  >
                    <CheckCircle2 size={10} /> Pass
                  </span>
                );
              } else {
                resultBadge = (
                  <span
                    style={{
                      fontSize: 10,
                      fontWeight: 700,
                      padding: '1px 5px',
                      borderRadius: 3,
                      background: 'rgba(239,68,68,0.15)',
                      color: 'var(--color-error, #ef4444)',
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 2,
                      whiteSpace: 'nowrap',
                    }}
                  >
                    <AlertCircle size={10} /> Fail
                    {res.variance_pct !== undefined && res.variance_pct !== null && (
                      <span style={{ fontSize: 9.5 }}>
                        ({res.variance_pct > 0 ? '+' : ''}{Number(res.variance_pct).toFixed(1)}%)
                      </span>
                    )}
                  </span>
                );
              }
            }

            return (
              <div
                key={c.id}
                role="button"
                tabIndex={0}
                onClick={() => onSelectCase(c.id)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onSelectCase(c.id);
                  }
                }}
                style={{
                  padding: '8px 10px',
                  borderRadius: 6,
                  border: `1px solid ${
                    isSelected ? 'var(--accent-blue)' : 'var(--border-main)'
                  }`,
                  background: isSelected
                    ? 'rgba(59,130,246,0.08)'
                    : 'var(--bg-surface-raised)',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: 8,
                  transition: 'all 0.12s ease',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0, flex: 1 }}>
                  {/* Critical Toggle Star */}
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onToggleCritical(c.id, !isCritical, c.tolerance);
                    }}
                    style={{
                      background: 'transparent',
                      border: 'none',
                      cursor: 'pointer',
                      padding: 2,
                      display: 'inline-flex',
                      alignItems: 'center',
                      color: isCritical ? '#eab308' : 'var(--text-tertiary)',
                      flexShrink: 0,
                    }}
                    title={isCritical ? 'Critical Deploy Gate Measure' : 'Mark as Critical'}
                  >
                    <Star size={13} fill={isCritical ? '#eab308' : 'none'} />
                  </button>

                  {/* Status Indicator */}
                  <span
                    style={{
                      width: 6,
                      height: 6,
                      borderRadius: '50%',
                      background:
                        c.status === 'approved'
                          ? 'var(--color-success, #22c55e)'
                          : c.status === 'rejected'
                          ? 'var(--color-error, #ef4444)'
                          : 'var(--accent-blue, #3b82f6)',
                      flexShrink: 0,
                    }}
                    title={`Status: ${c.status}`}
                  />

                  {/* Question & Metric */}
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div
                      style={{
                        fontSize: 12,
                        fontWeight: 600,
                        color: isSelected ? 'var(--accent-blue)' : 'var(--text-primary)',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                      title={c.question}
                    >
                      {c.question}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 2 }}>
                      {c.metric_name && (
                        <span
                          style={{
                            fontSize: 10,
                            fontFamily: 'monospace',
                            color: 'var(--accent-blue)',
                          }}
                        >
                          [{c.metric_name}]
                        </span>
                      )}
                      {windowsCount > 0 && (
                        <span
                          style={{
                            fontSize: 10,
                            color: 'var(--text-tertiary)',
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: 2,
                          }}
                        >
                          <Calendar size={10} /> {windowsCount} {windowsCount === 1 ? 'slice' : 'slices'}
                        </span>
                      )}
                    </div>
                  </div>
                </div>

                {/* Right side: result badge + arrow */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
                  {resultBadge}
                  <ChevronRight
                    size={14}
                    style={{
                      color: isSelected ? 'var(--accent-blue)' : 'var(--text-tertiary)',
                      opacity: isSelected ? 1 : 0.4,
                    }}
                  />
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
