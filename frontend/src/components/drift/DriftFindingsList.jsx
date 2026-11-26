import React, { useState, useMemo, useEffect, useRef } from 'react';
import {
  Search, X, ChevronDown, ChevronRight, Layers, FileText, Database,
  Sparkles, Check, CheckCircle2, Clock, AlertTriangle, Zap, Copy,
  CheckCheck, Filter, ArrowUpDown, ChevronLeft, ShieldCheck, Table, ExternalLink
} from 'lucide-react';
import Modal from '../common/Modal';
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

function formatKindLabel(kind) {
  if (!kind) return 'Metric';
  const k = String(kind).trim().toLowerCase();
  return k.charAt(0).toUpperCase() + k.slice(1);
}

function formatCategoryLabel(cat) {
  if (!cat) return 'Expression';
  const c = String(cat).trim().replace(/_/g, ' ').toLowerCase();
  return c.charAt(0).toUpperCase() + c.slice(1);
}

function formatStatusLabel(st) {
  if (!st) return 'Pending';
  const s = String(st).trim().replace(/_/g, ' ').toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function getCategoryBadgeStyle(category) {
  const cat = String(category || '').toUpperCase();
  if (cat.includes('ADDITION')) {
    return { bg: 'rgba(34, 197, 94, 0.15)', color: 'var(--color-success, #22c55e)' };
  }
  if (cat.includes('DELETION') || cat.includes('MISSING')) {
    return { bg: 'rgba(239, 68, 68, 0.15)', color: 'var(--color-error, #ef4444)' };
  }
  if (cat.includes('EXPRESSION') || cat.includes('FORMULA') || cat.includes('MISMATCH')) {
    return { bg: 'rgba(234, 179, 8, 0.15)', color: 'var(--color-warning, #eab308)' };
  }
  if (cat.includes('DESCRIPTION')) {
    return { bg: 'rgba(168, 85, 247, 0.15)', color: 'var(--accent-purple, #a855f7)' };
  }
  return { bg: 'rgba(107, 114, 128, 0.15)', color: 'var(--text-secondary, #9ca3af)' };
}

function getStatusBadgeStyle(status) {
  const st = String(status || 'pending').toLowerCase();
  if (st === 'approved') return { bg: 'rgba(34, 197, 94, 0.15)', color: 'var(--color-success, #22c55e)' };
  if (st === 'accepted') return { bg: 'rgba(59, 130, 246, 0.15)', color: 'var(--color-primary, #3b82f6)' };
  if (st === 'resolved') return { bg: 'rgba(20, 184, 166, 0.15)', color: '#14b8a6' };
  if (st === 'ignored') return { bg: 'rgba(239, 68, 68, 0.15)', color: 'var(--color-error, #ef4444)' };
  return { bg: 'rgba(234, 179, 8, 0.15)', color: 'var(--color-warning, #eab308)' };
}

// ── Clamped Expression with Show More / Copy ─────────────────────────────────
function ClampedExpression({ label, icon: Icon, expression, headerRight, accentColor = 'var(--text-secondary)' }) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  const clean = stripDiffMarkers(expression);
  const text = clean || '—';
  const isLong = text.length > 30 || text.includes('\n');
  const isDesc = label?.toLowerCase().includes('description');

  const handleCopy = (e) => {
    e.stopPropagation();
    if (clean && clean !== '(no description)' && clean !== '(No description)') {
      navigator.clipboard.writeText(clean);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div
      style={{
        padding: '8px 12px',
        borderRadius: 6,
        background: 'var(--bg-surface)',
        border: '1px solid var(--border-main)',
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
      }}
    >
      <div
        style={{
          color: accentColor,
          fontWeight: 600,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          fontSize: 11.5,
        }}
      >
        <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          {Icon && <Icon size={12} />} {label}
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          {headerRight}
          {expression && (
            <button
              type="button"
              onClick={handleCopy}
              style={{
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                color: copied ? 'var(--color-success, #22c55e)' : 'var(--text-tertiary)',
                padding: '1px 4px',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 3,
                fontSize: 10.5,
              }}
              title={isDesc ? 'Copy description' : 'Copy expression'}
            >
              {copied ? <CheckCheck size={11} /> : <Copy size={11} />}
              <span>{copied ? 'Copied' : 'Copy'}</span>
            </button>
          )}
        </div>
      </div>
      <div
        style={{
          color: 'var(--text-primary)',
          fontFamily: isDesc ? 'inherit' : 'monospace',
          fontSize: 11,
          lineHeight: 1.4,
          maxHeight: expanded ? 'none' : '4.2em',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: expanded ? 'pre-wrap' : 'normal',
          wordBreak: 'break-word',
          display: expanded ? 'block' : '-webkit-box',
          WebkitLineClamp: expanded ? 'unset' : 3,
          WebkitBoxOrient: 'vertical',
        }}
      >
        <FormattedDiffText text={expression} />
      </div>
      {isLong && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setExpanded(!expanded);
          }}
          style={{
            alignSelf: 'flex-start',
            background: 'none',
            border: 'none',
            color: 'var(--accent-blue)',
            cursor: 'pointer',
            fontSize: 10.5,
            fontWeight: 600,
            padding: 0,
            marginTop: 2,
          }}
        >
          {expanded ? 'Show less' : 'Show more'}
        </button>
      )}
    </div>
  );
}

// ── Multi-select Checkbox Dropdown Filter ────────────────────────────────────
function MultiSelectDropdown({
  label,
  options = [],
  selected = 'ALL',
  onChange,
  allLabel = 'All',
  allValue = 'ALL',
  totalCount = 0,
  onPageReset,
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    const handleClickOutside = (e) => {
      if (ref.current && !ref.current.contains(e.target)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [open]);

  const rawList = String(selected || allValue)
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s && s.toLowerCase() !== allValue.toLowerCase());

  const isAll = rawList.length === 0 || String(selected).toLowerCase() === allValue.toLowerCase();

  const handleToggle = (val) => {
    if (val.toLowerCase() === allValue.toLowerCase()) {
      onChange(allValue);
      if (onPageReset) onPageReset();
      return;
    }
    let next;
    if (rawList.includes(val)) {
      next = rawList.filter((v) => v !== val);
    } else {
      next = [...rawList, val];
    }
    if (next.length === 0 || next.length >= options.length) {
      onChange(allValue);
    } else {
      onChange(next.join(','));
    }
    if (onPageReset) onPageReset();
  };

  let btnText = `${allLabel} (${totalCount})`;
  if (!isAll) {
    if (rawList.length === 1) {
      const match = options.find((o) => o.value === rawList[0]);
      btnText = match ? `${match.label}` : rawList[0];
    } else {
      btnText = `${rawList.length} selected`;
    }
  }

  return (
    <div ref={ref} style={{ position: 'relative', display: 'inline-flex', alignItems: 'center' }}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          padding: '4px 8px',
          borderRadius: 6,
          border: '1px solid var(--border-main)',
          background: isAll ? 'var(--bg-app)' : 'var(--bg-surface-hover, rgba(59, 130, 246, 0.12))',
          color: isAll ? 'var(--text-primary)' : 'var(--accent-blue)',
          fontSize: 11.5,
          fontWeight: 500,
          cursor: 'pointer',
        }}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span style={{ fontSize: 11, color: 'var(--text-secondary)', fontWeight: 600 }}>{label}:</span>
        <span>{btnText}</span>
        <ChevronDown size={12} style={{ color: 'var(--text-tertiary)' }} />
      </button>

      <div
        style={{
          position: 'absolute',
          top: 'calc(100% + 4px)',
          left: 0,
          zIndex: 100,
          minWidth: 190,
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-main)',
          borderRadius: 6,
          boxShadow: '0 8px 24px rgba(0, 0, 0, 0.4)',
          padding: 4,
          display: open ? 'flex' : 'none',
          flexDirection: 'column',
          gap: 2,
        }}
      >
        <label
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            padding: '5px 8px',
            borderRadius: 4,
            cursor: 'pointer',
            fontSize: 11.5,
            color: 'var(--text-primary)',
            userSelect: 'none',
            background: isAll ? 'rgba(59, 130, 246, 0.1)' : 'transparent',
          }}
        >
          <input
            type="checkbox"
            checked={isAll}
            onChange={() => handleToggle(allValue)}
            style={{ cursor: 'pointer', accentColor: 'var(--accent-blue)' }}
          />
          <span style={{ flex: 1, fontWeight: isAll ? 600 : 400 }}>{allLabel}</span>
          <span style={{ fontSize: 10.5, color: 'var(--text-tertiary)' }}>({totalCount})</span>
        </label>
        <div style={{ height: 1, background: 'var(--border-main)', margin: '2px 0' }} />
        {options.map((opt) => {
          const isChecked = !isAll && rawList.includes(opt.value);
          return (
            <label
              key={opt.value}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '5px 8px',
                borderRadius: 4,
                cursor: 'pointer',
                fontSize: 11.5,
                color: 'var(--text-primary)',
                userSelect: 'none',
                background: isChecked ? 'rgba(59, 130, 246, 0.1)' : 'transparent',
              }}
            >
              <input
                type="checkbox"
                checked={isChecked}
                onChange={() => handleToggle(opt.value)}
                style={{ cursor: 'pointer', accentColor: 'var(--accent-blue)' }}
              />
              <span style={{ flex: 1, fontWeight: isChecked ? 600 : 400 }}>{opt.label}</span>
              <span style={{ fontSize: 10.5, color: 'var(--text-tertiary)' }}>({opt.count ?? 0})</span>
            </label>
          );
        })}
      </div>
    </div>
  );
}

// ── Main DriftFindingsList Component ──────────────────────────────────────────
export default function DriftFindingsList({
  events = [],
  totalCount = 0,
  totalUnfilteredCount = 0,
  countsByCategory = {},
  pendingByCategory = {},
  countsByKind = {},
  countsByStatus = {},
  page = 1,
  pageSize = 25,
  totalPages = 1,
  onPageChange,
  onPageSizeChange,
  search = '',
  onSearchChange,
  selectedCategory = 'ALL',
  onCategoryChange,
  selectedKind = 'ALL',
  onKindChange,
  selectedStatus = 'pending',
  onStatusChange,
  sort = 'newest',
  onSortChange,
  onResolve,
  onBulkResolve,
  onOpenBlast,
  onOpenAIImpact,
  formatOccurred,
  mode = 'live',
}) {
  // Accordion row expansion state (only one expanded at a time)
  const [expandedEventId, setExpandedEventId] = useState(null);

  // Group collapsed state
  const isLargeSet = (totalUnfilteredCount > 10 || totalCount > 10);
  const [allCollapsed, setAllCollapsed] = useState(true);
  const [userToggledGroups, setUserToggledGroups] = useState(new Set());

  // Bulk action confirmation modal state
  const [bulkModalOpen, setBulkModalOpen] = useState(false);
  const [bulkTarget, setBulkTarget] = useState(null); // { kind, category, count, action }
  const [bulkReason, setBulkReason] = useState('');
  const [bulkSubmitting, setBulkSubmitting] = useState(false);

  // Group events by Kind then Category
  const groupedEvents = useMemo(() => {
    const groups = {};
    for (const ev of events) {
      const k = String(ev.kind || 'METRIC').toUpperCase();
      const c = String(ev.category || ev.drift_type || 'UNKNOWN').toUpperCase();
      const groupKey = `${k}__${c}`;
      if (!groups[groupKey]) {
        groups[groupKey] = {
          key: groupKey,
          kind: k,
          category: c,
          events: [],
        };
      }
      groups[groupKey].events.push(ev);
    }
    return Object.values(groups);
  }, [events]);

  const isGroupCollapsed = (key) => {
    const isDefaultCollapsed = isLargeSet && allCollapsed;
    const toggled = userToggledGroups.has(key);
    return toggled ? !isDefaultCollapsed : isDefaultCollapsed;
  };

  const toggleGroup = (key) => {
    setUserToggledGroups((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  };

  const handleToggleAllGroups = () => {
    setAllCollapsed((prev) => !prev);
    setUserToggledGroups(new Set());
  };

  const toggleRow = (eventId) => {
    setExpandedEventId((prev) => (prev === eventId ? null : eventId));
  };

  const handleOpenBulkModal = (kind, category, count, action) => {
    setBulkTarget({ kind, category, count, action });
    setBulkReason('');
    setBulkModalOpen(true);
  };

  const handleConfirmBulk = async () => {
    if (!bulkTarget || !onBulkResolve) return;
    setBulkSubmitting(true);
    try {
      await onBulkResolve({
        kind: bulkTarget.kind,
        category: bulkTarget.category,
        action: bulkTarget.action,
        reason: bulkReason,
      });
      setBulkModalOpen(false);
      setBulkTarget(null);
    } finally {
      setBulkSubmitting(false);
    }
  };

  // Check active filters for chips
  const hasActiveFilters = Boolean(
    (search && search.trim()) ||
    (selectedCategory && selectedCategory !== 'ALL') ||
    (selectedKind && selectedKind !== 'ALL') ||
    (selectedStatus && selectedStatus !== 'all')
  );

  const handleClearAllFilters = () => {
    if (onSearchChange) onSearchChange('');
    if (onCategoryChange) onCategoryChange('ALL');
    if (onKindChange) onKindChange('ALL');
    if (onStatusChange) onStatusChange('all');
    if (onPageChange) onPageChange(1);
  };

  // Calculate slice range for "Showing X-Y of Z"
  const startItem = totalCount === 0 ? 0 : (page - 1) * pageSize + 1;
  const endItem = Math.min(page * pageSize, totalCount);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* ── FILTERS TOOLBAR ──────────────────────────────────────────────── */}
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 10,
          padding: 12,
          borderRadius: 8,
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-main)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          {/* Search by object name */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              background: 'var(--bg-app)',
              border: '1px solid var(--border-main)',
              borderRadius: 6,
              padding: '4px 8px',
              flex: '1 1 180px',
              minWidth: 150,
            }}
          >
            <Search size={13} style={{ color: 'var(--text-tertiary)' }} />
            <input
              type="text"
              placeholder="Search object, table or formula…"
              value={search}
              onChange={(e) => {
                onSearchChange(e.target.value);
                if (onPageChange) onPageChange(1);
              }}
              style={{
                background: 'none',
                border: 'none',
                outline: 'none',
                color: 'var(--text-primary)',
                fontSize: 12,
                width: '100%',
              }}
            />
            {search && (
              <button
                type="button"
                onClick={() => {
                  onSearchChange('');
                  if (onPageChange) onPageChange(1);
                }}
                style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, color: 'var(--text-tertiary)' }}
              >
                <X size={12} />
              </button>
            )}
          </div>

          {/* Kind Multi-select Filter */}
          <MultiSelectDropdown
            label="Kind"
            options={Object.entries(countsByKind).map(([k, cnt]) => ({
              value: k,
              label: `${formatKindLabel(k)} (${cnt})`,
              count: cnt,
            }))}
            selected={selectedKind}
            onChange={onKindChange}
            allLabel="All Kinds"
            allValue="ALL"
            totalCount={totalUnfilteredCount}
            onPageReset={() => onPageChange && onPageChange(1)}
          />

          {/* Category Multi-select Filter */}
          <MultiSelectDropdown
            label="Category"
            options={Object.entries(countsByCategory).map(([cat, cnt]) => ({
              value: cat,
              label: `${formatCategoryLabel(cat)} (${cnt})`,
              count: cnt,
            }))}
            selected={selectedCategory}
            onChange={onCategoryChange}
            allLabel="All Categories"
            allValue="ALL"
            totalCount={totalUnfilteredCount}
            onPageReset={() => onPageChange && onPageChange(1)}
          />

          {/* Status Multi-select Filter */}
          <MultiSelectDropdown
            label="Status"
            options={Object.entries(countsByStatus).map(([st, cnt]) => ({
              value: st,
              label: `${formatStatusLabel(st)} (${cnt})`,
              count: cnt,
            }))}
            selected={selectedStatus}
            onChange={onStatusChange}
            allLabel="All Statuses"
            allValue="all"
            totalCount={totalUnfilteredCount}
            onPageReset={() => onPageChange && onPageChange(1)}
          />

          {/* Sort Select */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginLeft: 'auto' }}>
            <ArrowUpDown size={12} style={{ color: 'var(--text-tertiary)' }} />
            <select
              value={sort}
              onChange={(e) => {
                onSortChange(e.target.value);
                if (onPageChange) onPageChange(1);
              }}
              style={{
                padding: '4px 8px',
                borderRadius: 6,
                border: '1px solid var(--border-main)',
                background: 'var(--bg-app)',
                color: 'var(--text-primary)',
                fontSize: 11.5,
                fontWeight: 500,
                cursor: 'pointer',
              }}
            >
              <option value="newest">Newest First</option>
              <option value="oldest">Oldest First</option>
              <option value="name_asc">Name (A–Z)</option>
              <option value="name_desc">Name (Z–A)</option>
              <option value="category">Category</option>
              <option value="kind">Kind</option>
            </select>
          </div>
        </div>

        {/* Removable Active Filter Chips & Clear All */}
        {hasActiveFilters && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', paddingTop: 4, borderTop: '1px solid var(--border-main)' }}>
            <span style={{ fontSize: 11, color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 4 }}>
              <Filter size={11} /> Active Filters:
            </span>

            {search && search.trim() && (
              <span
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 4,
                  padding: '2px 8px',
                  borderRadius: 12,
                  background: 'var(--bg-surface-raised)',
                  border: '1px solid var(--border-main)',
                  fontSize: 11,
                  color: 'var(--text-primary)',
                }}
              >
                Search: &ldquo;{search}&rdquo;
                <button
                  type="button"
                  onClick={() => {
                    onSearchChange('');
                    if (onPageChange) onPageChange(1);
                  }}
                  style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: 0 }}
                >
                  <X size={11} />
                </button>
              </span>
            )}

            {selectedKind !== 'ALL' &&
              selectedKind.split(',').map((k) => (
                <span
                  key={k}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 4,
                    padding: '2px 8px',
                    borderRadius: 12,
                    background: 'var(--bg-surface-raised)',
                    border: '1px solid var(--border-main)',
                    fontSize: 11,
                    color: 'var(--text-primary)',
                  }}
                >
                  Kind: {formatKindLabel(k)} {countsByKind[k] !== undefined ? `(${countsByKind[k]})` : ''}
                  <button
                    type="button"
                    onClick={() => {
                      const remaining = selectedKind.split(',').filter((x) => x !== k);
                      onKindChange(remaining.length === 0 ? 'ALL' : remaining.join(','));
                      if (onPageChange) onPageChange(1);
                    }}
                    style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: 0 }}
                  >
                    <X size={11} />
                  </button>
                </span>
              ))}

            {selectedCategory !== 'ALL' &&
              selectedCategory.split(',').map((cat) => (
                <span
                  key={cat}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 4,
                    padding: '2px 8px',
                    borderRadius: 12,
                    background: 'var(--bg-surface-raised)',
                    border: '1px solid var(--border-main)',
                    fontSize: 11,
                    color: 'var(--text-primary)',
                  }}
                >
                  Category: {formatCategoryLabel(cat)} {countsByCategory[cat] !== undefined ? `(${countsByCategory[cat]})` : ''}
                  <button
                    type="button"
                    onClick={() => {
                      const remaining = selectedCategory.split(',').filter((x) => x !== cat);
                      onCategoryChange(remaining.length === 0 ? 'ALL' : remaining.join(','));
                      if (onPageChange) onPageChange(1);
                    }}
                    style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: 0 }}
                  >
                    <X size={11} />
                  </button>
                </span>
              ))}

            {selectedStatus !== 'all' &&
              selectedStatus.split(',').map((st) => (
                <span
                  key={st}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 4,
                    padding: '2px 8px',
                    borderRadius: 12,
                    background: 'var(--bg-surface-raised)',
                    border: '1px solid var(--border-main)',
                    fontSize: 11,
                    color: 'var(--text-primary)',
                  }}
                >
                  Status: {formatStatusLabel(st)} {countsByStatus[st] !== undefined ? `(${countsByStatus[st]})` : ''}
                  <button
                    type="button"
                    onClick={() => {
                      const remaining = selectedStatus.split(',').filter((x) => x !== st);
                      onStatusChange(remaining.length === 0 ? 'all' : remaining.join(','));
                      if (onPageChange) onPageChange(1);
                    }}
                    style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: 0 }}
                  >
                    <X size={11} />
                  </button>
                </span>
              ))}

            <button
              type="button"
              onClick={handleClearAllFilters}
              style={{
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                color: 'var(--accent-blue)',
                fontSize: 11,
                fontWeight: 600,
                padding: '2px 6px',
                marginLeft: 4,
              }}
            >
              Clear all filters
            </button>
          </div>
        )}
      </div>

      {/* ── BIG-RESULT GUARD ADVISORY BANNER ──────────────────────────────── */}
      {isLargeSet && (
        <div
          style={{
            padding: '8px 12px',
            borderRadius: 6,
            background: 'var(--bg-surface-raised)',
            border: '1px solid var(--border-main)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            fontSize: 11.5,
            color: 'var(--text-secondary)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <AlertTriangle size={13} style={{ color: 'var(--color-warning, #eab308)' }} />
            <span>
              Showing <strong>{totalCount}</strong> findings across{' '}
              <strong>{Object.keys(countsByCategory).length || new Set(events.map((e) => e.category || e.drift_type)).size || 1}</strong> categories. Groups are collapsed by default to save space.
            </span>
          </div>
          <button
            type="button"
            onClick={handleToggleAllGroups}
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--accent-blue)',
              fontSize: 11.5,
              fontWeight: 600,
              cursor: 'pointer',
              padding: '2px 4px',
            }}
          >
            {allCollapsed ? 'Expand all groups' : 'Collapse all groups'}
          </button>
        </div>
      )}

      {/* ── SUMMARY / SLICE COUNT ────────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 11.5, color: 'var(--text-secondary)', padding: '0 4px' }}>
        <div>
          Showing <strong>{startItem}–{endItem}</strong> of <strong>{totalCount}</strong> findings
          {totalUnfilteredCount > totalCount && (
            <span style={{ color: 'var(--text-tertiary)', marginLeft: 6 }}>
              (filtered from {totalUnfilteredCount} total)
            </span>
          )}
        </div>
      </div>

      {/* ── EMPTY STATE ─────────────────────────────────────────────────── */}
      {totalCount === 0 && (
        <div
          style={{
            padding: 32,
            textAlign: 'center',
            background: 'var(--bg-surface)',
            borderRadius: 8,
            border: '1px solid var(--border-main)',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 8,
          }}
        >
          <ShieldCheck size={32} style={{ color: 'var(--color-success, #22c55e)' }} />
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>
            No findings match your criteria
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            {hasActiveFilters ? 'Try adjusting or clearing your filters to see more results.' : 'All compared objects are in sync with the warehouse.'}
          </div>
          {hasActiveFilters && (
            <button
              type="button"
              onClick={handleClearAllFilters}
              style={{ ...BTN, marginTop: 8 }}
            >
              Clear filters
            </button>
          )}
        </div>
      )}

      {/* ── GROUPED FINDINGS LIST ────────────────────────────────────────── */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {groupedEvents.map((group) => {
          const isCollapsed = isGroupCollapsed(group.key);
          const groupCount = group.events.length;
          const catStyle = getCategoryBadgeStyle(group.category);

          // Consistent group count for the current filters across pages
          const fullCategoryCount = (countsByCategory && countsByCategory[group.category] !== undefined)
            ? countsByCategory[group.category]
            : groupCount;

          // Pending items in this specific group for bulk action
          const pendingInGroup = group.events.filter((e) => e.status === 'pending');
          const pendingCountForGroup = (pendingByCategory && pendingByCategory[group.category] !== undefined)
            ? pendingByCategory[group.category]
            : pendingInGroup.length;
          const canBulkAction = mode === 'live' && pendingCountForGroup > 0;

          return (
            <div
              key={group.key}
              style={{
                borderRadius: 8,
                background: 'var(--bg-app)',
                border: '1px solid var(--border-main)',
                overflow: 'hidden',
              }}
            >
              {/* Group Header */}
              <div
                onClick={() => toggleGroup(group.key)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '10px 14px',
                  background: 'var(--bg-surface)',
                  borderBottom: isCollapsed ? 'none' : '1px solid var(--border-main)',
                  cursor: 'pointer',
                  userSelect: 'none',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  {isCollapsed ? (
                    <ChevronRight size={14} style={{ color: 'var(--text-tertiary)' }} />
                  ) : (
                    <ChevronDown size={14} style={{ color: 'var(--text-tertiary)' }} />
                  )}
                  <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>
                    {formatKindLabel(group.kind)} · {formatCategoryLabel(group.category)}
                  </span>
                  <span
                    style={{
                      fontSize: 10.5,
                      fontWeight: 700,
                      padding: '2px 7px',
                      borderRadius: 10,
                      background: catStyle.bg,
                      color: catStyle.color,
                    }}
                  >
                    {fullCategoryCount}
                  </span>
                </div>

                {/* Bulk Actions on Group */}
                {canBulkAction && (
                  <div
                    onClick={(e) => e.stopPropagation()}
                    style={{ display: 'flex', alignItems: 'center', gap: 6 }}
                  >
                    <button
                      type="button"
                      onClick={() => handleOpenBulkModal(group.kind, group.category, pendingCountForGroup, 'approve')}
                      style={{
                        ...BTN,
                        padding: '3px 8px',
                        fontSize: 10.5,
                        background: 'rgba(34, 197, 94, 0.1)',
                        borderColor: 'rgba(34, 197, 94, 0.3)',
                        color: 'var(--color-success, #22c55e)',
                      }}
                      title={`Approve and restore all ${pendingCountForGroup} pending items in this group`}
                    >
                      <Check size={11} /> {pendingCountForGroup < fullCategoryCount ? `Approve pending (${pendingCountForGroup})` : `Approve all (${pendingCountForGroup})`}
                    </button>
                    <button
                      type="button"
                      onClick={() => handleOpenBulkModal(group.kind, group.category, pendingCountForGroup, 'ignore')}
                      style={{
                        ...BTN,
                        padding: '3px 8px',
                        fontSize: 10.5,
                        background: 'rgba(239, 68, 68, 0.1)',
                        borderColor: 'rgba(239, 68, 68, 0.3)',
                        color: 'var(--color-error, #ef4444)',
                      }}
                      title={`Ignore all ${pendingCountForGroup} pending items in this group`}
                    >
                      <X size={11} /> {pendingCountForGroup < fullCategoryCount ? `Ignore pending (${pendingCountForGroup})` : `Ignore all (${pendingCountForGroup})`}
                    </button>
                  </div>
                )}
              </div>

              {/* Group Body: Compact Finding Rows (when expanded) */}
              {!isCollapsed && (
                <div style={{ display: 'flex', flexDirection: 'column', divideY: '1px solid var(--border-main)' }}>
                  {group.events.map((ev) => {
                    const isRowExpanded = expandedEventId === ev.id;
                    const isPending = ev.status === 'pending';

                    const objName = ev.metric_name || ev.object_name || ev.name || 'Unnamed Object';
                    const rowCatStyle = getCategoryBadgeStyle(ev.category || ev.drift_type);
                    const rowStatusStyle = getStatusBadgeStyle(ev.status);
                    const isDescDrift = (ev.category || '').toUpperCase() === 'DESCRIPTION_DRIFT' || (ev.drift_type || '').toUpperCase() === 'DESCRIPTION_MISMATCH';

                    return (
                      <div
                        key={ev.id}
                        style={{
                          borderBottom: '1px solid var(--border-main)',
                          background: isRowExpanded ? 'var(--bg-surface-raised)' : 'transparent',
                          transition: 'background 0.15s ease',
                        }}
                      >
                        {/* Compact Row */}
                        <div
                          onClick={() => toggleRow(ev.id)}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            padding: '9px 14px',
                            cursor: 'pointer',
                            flexWrap: 'wrap',
                            gap: 8,
                          }}
                        >
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                            {isRowExpanded ? (
                              <ChevronDown size={14} style={{ color: 'var(--accent-blue)', flexShrink: 0 }} />
                            ) : (
                              <ChevronRight size={14} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />
                            )}

                            <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>
                              {objName}
                            </span>

                            {/* Kind Badge */}
                            <span
                              style={{
                                fontSize: 9.5,
                                fontWeight: 700,
                                padding: '1px 5px',
                                borderRadius: 4,
                                background: 'var(--bg-surface)',
                                border: '1px solid var(--border-main)',
                                color: 'var(--text-secondary)',
                                textTransform: 'uppercase',
                              }}
                            >
                              {ev.kind || 'METRIC'}
                            </span>

                            {/* Table Location Badge with Direct Link */}
                            {ev.table && (
                              <span
                                style={{
                                  fontSize: 10,
                                  fontWeight: 600,
                                  padding: '2px 7px',
                                  borderRadius: 5,
                                  background: 'rgba(59, 130, 246, 0.1)',
                                  border: '1px solid rgba(59, 130, 246, 0.28)',
                                  color: 'var(--accent-blue)',
                                  display: 'inline-flex',
                                  alignItems: 'center',
                                  gap: 4,
                                }}
                                title={`Table location: ${ev.table}`}
                              >
                                <Table size={11} />
                                <span>Table: <strong>{ev.table}</strong></span>
                                {ev.project_id && (
                                  <a
                                    href={`/projects/${ev.project_id}?tab=mappings&search=${encodeURIComponent(ev.table)}`}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    onClick={(e) => e.stopPropagation()}
                                    style={{
                                      color: 'var(--accent-blue)',
                                      textDecoration: 'none',
                                      marginLeft: 2,
                                      display: 'inline-flex',
                                      alignItems: 'center',
                                      opacity: 0.85,
                                    }}
                                    title={`Open table ${ev.table} in project schema mappings`}
                                  >
                                    <ExternalLink size={10} />
                                  </a>
                                )}
                              </span>
                            )}

                            {/* Category Badge */}
                            <span
                              style={{
                                fontSize: 9.5,
                                fontWeight: 700,
                                padding: '1px 5px',
                                borderRadius: 4,
                                background: rowCatStyle.bg,
                                color: rowCatStyle.color,
                                textTransform: 'uppercase',
                              }}
                            >
                              {formatCategoryLabel(ev.category || ev.drift_type)}
                            </span>

                            {/* Status Badge */}
                            <span
                              style={{
                                fontSize: 9.5,
                                fontWeight: 700,
                                padding: '1px 5px',
                                borderRadius: 4,
                                background: rowStatusStyle.bg,
                                color: rowStatusStyle.color,
                                textTransform: 'uppercase',
                              }}
                            >
                              {formatStatusLabel(ev.status)}
                            </span>

                            <code style={{ fontSize: 10, padding: '1px 4px', borderRadius: 3, background: 'var(--bg-surface)', border: '1px solid var(--border-main)', color: 'var(--text-tertiary)', fontFamily: 'monospace' }}>
                              {ev.id}
                            </code>
                          </div>

                          {/* Timestamp info */}
                          <div
                            style={{
                              fontSize: 11,
                              color: 'var(--text-tertiary)',
                              display: 'flex',
                              alignItems: 'center',
                              gap: 4,
                            }}
                          >
                            <Clock size={11} />
                            <span>
                              Last seen: <strong>{formatOccurred ? formatOccurred(ev.last_seen_at || ev.detected_at) : (ev.last_seen_at || ev.detected_at || '—')}</strong>
                            </span>
                          </div>
                        </div>

                        {/* Expanded Detail (only one expanded at a time) */}
                        {isRowExpanded && (
                          <div
                            style={{
                              padding: '10px 16px 14px 34px',
                              display: 'flex',
                              flexDirection: 'column',
                              gap: 10,
                              background: 'var(--bg-surface)',
                              borderTop: '1px dashed var(--border-main)',
                            }}
                          >
                            {/* Business Story */}
                            {ev.story && (
                              <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.4 }}>
                                <strong style={{ color: 'var(--text-primary)' }}>Story:</strong> {ev.story}
                              </div>
                            )}

                            {/* Side-by-side Clamped Expression / Description Comparison */}
                            {(() => {
                              const isDescDrift = (ev.category || '').toUpperCase() === 'DESCRIPTION_DRIFT' || (ev.drift_type || '').toUpperCase() === 'DESCRIPTION_MISMATCH';
                              return (
                                <>
                                  {isDescDrift && (ev.live_expression || ev.baseline_expression || ev.bi_definition) && (
                                    <div
                                      style={{
                                        display: 'flex',
                                        alignItems: 'center',
                                        gap: 6,
                                        padding: '6px 10px',
                                        borderRadius: 6,
                                        background: 'rgba(255, 255, 255, 0.02)',
                                        border: '1px solid var(--border-main)',
                                        fontSize: 11,
                                      }}
                                    >
                                      <span style={{ color: 'var(--text-secondary)', fontWeight: 600 }}>Formula / SQL:</span>
                                      <code style={{ color: 'var(--text-primary)', fontFamily: 'monospace' }}>
                                        {ev.live_expression || ev.baseline_expression || ev.bi_definition}
                                      </code>
                                    </div>
                                  )}

                                  {isDescDrift && (ev.description_classification || ev.modified_by) && (
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', fontSize: 11 }}>
                                      {ev.description_classification && (
                                        <span style={{
                                          padding: '2px 8px',
                                          borderRadius: 4,
                                          fontWeight: 700,
                                          background: ev.description_classification === 'TEXT_REMOVED'
                                            ? 'rgba(239, 68, 68, 0.15)'
                                            : (ev.description_classification === 'TEXT_ADDED' ? 'rgba(34, 197, 94, 0.15)' : 'rgba(59, 130, 246, 0.15)'),
                                          color: ev.description_classification === 'TEXT_REMOVED'
                                            ? 'var(--color-error)'
                                            : (ev.description_classification === 'TEXT_ADDED' ? 'var(--color-success)' : 'var(--accent-blue)'),
                                        }}>
                                          {ev.description_classification.replace('_', ' ')}
                                        </span>
                                      )}
                                      {ev.modified_by && (
                                        <span style={{ color: 'var(--text-tertiary)' }}>
                                          Warehouse modifier: <strong>{ev.modified_by}</strong>
                                        </span>
                                      )}
                                    </div>
                                  )}

                                  <div
                                    style={{
                                      display: 'grid',
                                      gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))',
                                      gap: 10,
                                    }}
                                  >
                                    <ClampedExpression
                                      label={isDescDrift ? "Baseline Description" : "Baseline Definition"}
                                      icon={FileText}
                                      expression={
                                        isDescDrift
                                          ? (ev.baseline_marked || ev.baseline_description || ev.details?.baseline_description_normalized || '(No description)')
                                          : (ev.bi_definition || ev.baseline_expression || ev.expression)
                                      }
                                      accentColor="var(--accent-blue)"
                                    />
                                    <ClampedExpression
                                      label={isDescDrift ? "Warehouse Description" : "Warehouse (Current)"}
                                      icon={Database}
                                      expression={
                                        isDescDrift
                                          ? (ev.live_marked || ev.live_description || ev.details?.live_description_normalized || '(No description)')
                                          : (ev.snowflake_definition || ev.databricks_definition || ev.live_expression || ev.expression)
                                      }
                                      accentColor="var(--text-secondary)"
                                    />
                                    <ClampedExpression
                                      label="Proposed Resolution"
                                      icon={Sparkles}
                                      expression={
                                        isDescDrift
                                          ? 'update description in semantic view'
                                          : (ev.proposed_snowflake_definition || ev.proposed_databricks_definition || 'redeploy from source')
                                      }
                                      headerRight={
                                        <span style={{ fontSize: 9.5, padding: '1px 5px', borderRadius: 3, background: 'var(--bg-surface-raised)', border: '1px solid var(--border-main)', color: 'var(--text-secondary)', fontWeight: 700 }}>
                                          {isDescDrift ? 'SYNC COMMENT' : 'REDEPLOY'}
                                        </span>
                                      }
                                      accentColor="var(--text-primary)"
                                    />
                                  </div>
                                </>
                              );
                            })()}

                            {/* What Changed & Value Impact */}
                            {((ev.what_changed && ev.what_changed.length > 0) || ev.value_impact) && (
                              <div
                                style={{
                                  padding: '8px 12px',
                                  borderRadius: 6,
                                  background: 'rgba(255, 255, 255, 0.02)',
                                  border: '1px solid var(--border-main)',
                                  fontSize: 11.5,
                                  display: 'flex',
                                  flexDirection: 'column',
                                  gap: 4,
                                }}
                              >
                                {ev.what_changed && ev.what_changed.length > 0 && (
                                  <div style={{ display: 'flex', gap: 6, alignItems: 'flex-start' }}>
                                    <span style={{ color: 'var(--text-secondary)', fontWeight: 600, minWidth: 92 }}>What changed:</span>
                                    <span style={{ color: 'var(--text-tertiary)', flex: 1 }}>{ev.what_changed.join(' · ')}</span>
                                  </div>
                                )}
                                {ev.value_impact && (
                                  <div style={{ display: 'flex', gap: 6, alignItems: 'flex-start' }}>
                                    <span style={{ color: 'var(--text-secondary)', fontWeight: 600, minWidth: 92 }}>Value impact:</span>
                                    <span style={{ color: 'var(--accent-blue, #60a5fa)', fontWeight: 600, flex: 1 }}>{ev.value_impact}</span>
                                  </div>
                                )}
                              </div>
                            )}

                            {/* Table Location and Quick Navigation */}
                            {(ev.table || ev.semantic_view) && (
                              <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', fontSize: 11.5 }}>
                                {ev.table && (
                                  <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                                    <span style={{ color: 'var(--text-secondary)' }}>Table Location:</span>
                                    <code style={{ color: 'var(--text-primary)', fontWeight: 700, padding: '1px 6px', borderRadius: 3, background: 'var(--bg-surface)', border: '1px solid var(--border-main)', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                                      <Table size={11} style={{ color: 'var(--accent-blue)' }} />
                                      {ev.table}
                                    </code>
                                    {ev.project_id && (
                                      <a
                                        href={`/projects/${ev.project_id}?tab=mappings&search=${encodeURIComponent(ev.table)}`}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        onClick={(e) => e.stopPropagation()}
                                        style={{
                                          fontSize: 11,
                                          color: 'var(--accent-blue)',
                                          textDecoration: 'none',
                                          display: 'inline-flex',
                                          alignItems: 'center',
                                          gap: 3,
                                          marginLeft: 4,
                                        }}
                                        title={`Go to table ${ev.table} in project mappings`}
                                      >
                                        Go to table <ExternalLink size={11} />
                                      </a>
                                    )}
                                  </div>
                                )}
                                {ev.semantic_view && (
                                  <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                                    <span style={{ color: 'var(--text-secondary)' }}>Semantic View:</span>
                                    <code style={{ color: 'var(--text-primary)', padding: '1px 6px', borderRadius: 3, background: 'var(--bg-surface)', border: '1px solid var(--border-main)' }}>
                                      {ev.semantic_view}
                                    </code>
                                  </div>
                                )}
                              </div>
                            )}

                            {/* Impacted Objects / Blast Radius */}
                            {ev.affected_objects?.length > 0 && (
                              <div style={{ fontSize: 11, color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                                <Layers size={12} style={{ color: 'var(--text-secondary)' }} />
                                <span>Impacted Objects:</span>
                                {ev.affected_objects.map((obj, i) => (
                                  <span key={i} style={{ padding: '1px 5px', borderRadius: 3, background: 'var(--bg-surface)', border: '1px solid var(--border-main)', color: 'var(--text-secondary)', fontFamily: 'monospace' }}>
                                    {obj}
                                  </span>
                                ))}
                              </div>
                            )}

                            {/* Actions Toolbar */}
                            <div
                              style={{
                                display: 'flex',
                                alignItems: 'center',
                                justifyContent: 'space-between',
                                borderTop: '1px solid var(--border-main)',
                                paddingTop: 8,
                                marginTop: 2,
                              }}
                            >
                              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                {onOpenBlast && (
                                  <button
                                    type="button"
                                    style={{
                                      ...BTN,
                                      padding: '4px 9px',
                                      fontSize: 11,
                                      background: 'rgba(168, 85, 247, 0.1)',
                                      borderColor: 'rgba(168, 85, 247, 0.3)',
                                      color: 'var(--accent-purple, #a855f7)',
                                    }}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      onOpenBlast(
                                        objName,
                                        ev.category || ev.drift_type,
                                        ev.kind,
                                        ev.table,
                                        ev.live_value || ev.live_expression,
                                        ev
                                      );
                                    }}
                                    title="Check Blast Radius: See downstream BI & AI impact of this metric drift"
                                  >
                                    <Zap size={11} style={{ color: 'var(--accent-purple, #a855f7)' }} /> Blast Radius
                                  </button>
                                )}
                                {isDescDrift && onOpenAIImpact && (
                                  <button
                                    type="button"
                                    style={{
                                      ...BTN,
                                      padding: '4px 9px',
                                      fontSize: 11,
                                      background: 'rgba(59, 130, 246, 0.1)',
                                      borderColor: 'rgba(59, 130, 246, 0.3)',
                                      color: 'var(--accent-blue, #3b82f6)',
                                    }}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      onOpenAIImpact(ev);
                                    }}
                                    title="Check AI Impact: Test natural language query routing under baseline vs live comment"
                                  >
                                    <Sparkles size={11} style={{ color: 'var(--accent-blue, #3b82f6)' }} /> AI Impact Check
                                  </button>
                                )}
                              </div>

                              {isPending && mode === 'live' ? (
                                <div style={{ display: 'flex', gap: 8, marginLeft: 'auto' }}>
                                  <button
                                    type="button"
                                    style={{
                                      ...BTN_PRIMARY,
                                      background: 'var(--color-success, #22c55e)',
                                      borderColor: 'var(--color-success, #22c55e)',
                                      color: '#fff',
                                      padding: '5px 12px',
                                    }}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      onResolve(ev.id, 'approve');
                                    }}
                                    title={isDescDrift ? "Update description in semantic view (SYNC COMMENT)" : "Restore from SemaBridge: Gated redeploy of the baseline definition"}
                                  >
                                    <Check size={13} /> {isDescDrift ? 'Sync Comment' : 'Restore & Sync'}
                                  </button>
                                  <button
                                    type="button"
                                    style={{
                                      ...BTN,
                                      background: 'rgba(59, 130, 246, 0.1)',
                                      borderColor: 'rgba(59, 130, 246, 0.3)',
                                      color: 'var(--color-primary, #3b82f6)',
                                      padding: '5px 12px',
                                    }}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      onResolve(ev.id, 'accept');
                                    }}
                                    title="Accept warehouse change: The warehouse change is intended"
                                  >
                                    <CheckCircle2 size={13} /> Accept Change
                                  </button>
                                  <button
                                    type="button"
                                    style={{ ...BTN, padding: '5px 12px' }}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      onResolve(ev.id, 'ignore');
                                    }}
                                    title="Ignore drift and keep current warehouse definition"
                                  >
                                    <X size={13} /> Ignore
                                  </button>
                                </div>
                              ) : (
                                <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginLeft: 'auto' }}>
                                  {ev.resolution_reason ? `Resolution: ${ev.resolution_reason}` : ''}
                                  {ev.resolved_at ? ` · Resolved ${formatOccurred ? formatOccurred(ev.resolved_at) : ev.resolved_at}` : ''}
                                </div>
                              )}
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* ── NUMBERED PAGINATION BAR ──────────────────────────────────────── */}
      {totalPages > 1 && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '10px 14px',
            borderRadius: 8,
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-main)',
            flexWrap: 'wrap',
            gap: 10,
            marginTop: 4,
          }}
        >
          {/* Page size select */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11.5, color: 'var(--text-secondary)' }}>
            <span>Rows per page:</span>
            <select
              value={pageSize}
              onChange={(e) => {
                onPageSizeChange(Number(e.target.value));
                if (onPageChange) onPageChange(1);
              }}
              style={{
                padding: '3px 8px',
                borderRadius: 5,
                border: '1px solid var(--border-main)',
                background: 'var(--bg-app)',
                color: 'var(--text-primary)',
                fontSize: 11.5,
                cursor: 'pointer',
              }}
            >
              <option value={10}>10</option>
              <option value={25}>25</option>
              <option value={50}>50</option>
              <option value={100}>100</option>
            </select>
          </div>

          {/* Page navigation controls */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => onPageChange(page - 1)}
              style={{
                ...BTN,
                padding: '4px 8px',
                opacity: page <= 1 ? 0.4 : 1,
                cursor: page <= 1 ? 'not-allowed' : 'pointer',
              }}
              title="Previous Page"
            >
              <ChevronLeft size={13} />
            </button>

            {Array.from({ length: totalPages }, (_, i) => i + 1)
              .filter((p) => p === 1 || p === totalPages || Math.abs(p - page) <= 2)
              .reduce((acc, p, idx, arr) => {
                if (idx > 0 && p - arr[idx - 1] > 1) {
                  acc.push('ellipsis-' + p);
                }
                acc.push(p);
                return acc;
              }, [])
              .map((item) => {
                if (typeof item === 'string') {
                  return (
                    <span key={item} style={{ padding: '0 4px', color: 'var(--text-tertiary)', fontSize: 11 }}>
                      …
                    </span>
                  );
                }
                const isCurrent = item === page;
                return (
                  <button
                    key={item}
                    type="button"
                    onClick={() => onPageChange(item)}
                    style={{
                      ...BTN,
                      padding: '3px 8px',
                      fontSize: 11,
                      minWidth: 26,
                      justifyContent: 'center',
                      background: isCurrent ? 'var(--accent-blue)' : 'var(--bg-surface-raised)',
                      borderColor: isCurrent ? 'var(--accent-blue)' : 'var(--border-main)',
                      color: isCurrent ? '#fff' : 'var(--text-primary)',
                      fontWeight: isCurrent ? 700 : 500,
                    }}
                  >
                    {item}
                  </button>
                );
              })}

            <button
              type="button"
              disabled={page >= totalPages}
              onClick={() => onPageChange(page + 1)}
              style={{
                ...BTN,
                padding: '4px 8px',
                opacity: page >= totalPages ? 0.4 : 1,
                cursor: page >= totalPages ? 'not-allowed' : 'pointer',
              }}
              title="Next Page"
            >
              <ChevronRight size={13} />
            </button>
          </div>
        </div>
      )}

      {/* ── BULK RESOLUTION CONFIRMATION MODAL ────────────────────────────── */}
      <Modal
        open={bulkModalOpen}
        onClose={() => setBulkModalOpen(false)}
        title={
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <AlertTriangle size={16} style={{ color: bulkTarget?.action === 'approve' ? 'var(--color-success, #22c55e)' : 'var(--color-error, #ef4444)' }} />
            <span>
              Confirm Bulk {bulkTarget?.action === 'approve' ? 'Approval & Redeploy' : 'Ignore'}
            </span>
          </div>
        }
        width={520}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.5 }}>
            You are about to <strong>{bulkTarget?.action === 'approve' ? 'approve and restore' : 'ignore'}</strong> all{' '}
            <strong>{bulkTarget?.count}</strong> finding(s) in{' '}
            <strong>{formatKindLabel(bulkTarget?.kind)} · {formatCategoryLabel(bulkTarget?.category)}</strong>.
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>
              Reason for bulk action (required for audit trail):
            </label>
            <input
              type="text"
              placeholder={bulkTarget?.action === 'approve' ? 'e.g. Approved gated redeploy to restore deleted objects' : 'e.g. Expected warehouse maintenance by analytics engineer'}
              value={bulkReason}
              onChange={(e) => setBulkReason(e.target.value)}
              style={{
                padding: '8px 12px',
                borderRadius: 6,
                border: '1px solid var(--border-main)',
                background: 'var(--bg-surface-raised)',
                color: 'var(--text-primary)',
                fontSize: 12,
                outline: 'none',
              }}
            />
          </div>

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 8 }}>
            <button
              type="button"
              onClick={() => setBulkModalOpen(false)}
              style={{ ...BTN, padding: '6px 14px' }}
              disabled={bulkSubmitting}
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleConfirmBulk}
              disabled={bulkSubmitting || !bulkReason.trim()}
              style={{
                ...BTN_PRIMARY,
                background: bulkTarget?.action === 'approve' ? 'var(--color-success, #22c55e)' : 'var(--color-error, #ef4444)',
                borderColor: bulkTarget?.action === 'approve' ? 'var(--color-success, #22c55e)' : 'var(--color-error, #ef4444)',
                padding: '6px 16px',
                opacity: (!bulkReason.trim() || bulkSubmitting) ? 0.5 : 1,
                cursor: (!bulkReason.trim() || bulkSubmitting) ? 'not-allowed' : 'pointer',
              }}
            >
              {bulkSubmitting ? 'Applying…' : `Confirm (${bulkTarget?.count} items)`}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
