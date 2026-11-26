import React from 'react';
import { CATEGORY_DETAILS } from '../../utils/driftLabels';

const PILL = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  padding: '4px 10px',
  borderRadius: 999,
  fontSize: 12,
  fontWeight: 600,
  cursor: 'pointer',
  border: '1px solid var(--border-main)',
  background: 'var(--bg-surface-raised)',
  color: 'var(--text-secondary)',
  transition: 'all 0.15s ease',
};

/**
 * DriftCategorySummary Component
 * Clickable category counter pill buttons with multi-select checkbox support:
 * All, Addition, Deletion, Expression Drift, Description Drift.
 */
export default function DriftCategorySummary({
  countsByCategory = {},
  totalCount = 0,
  selectedCategory = 'ALL',
  onSelectCategory = () => {},
  disabled = false,
}) {
  const categories = [
    { key: 'ALL', label: 'All', count: totalCount },
    { key: 'ADDITION', label: 'Addition', count: countsByCategory.ADDITION || 0, color: 'var(--color-success, #22c55e)' },
    { key: 'DELETION', label: 'Deletion', count: countsByCategory.DELETION || 0, color: 'var(--color-error, #ef4444)' },
    { key: 'EXPRESSION_DRIFT', label: 'Expression', count: countsByCategory.EXPRESSION_DRIFT || 0, color: 'var(--color-warning, #eab308)' },
    { key: 'DESCRIPTION_DRIFT', label: 'Description', count: countsByCategory.DESCRIPTION_DRIFT || 0, color: 'var(--accent-purple, #a855f7)' },
  ];

  const selectedList = String(selectedCategory || 'ALL')
    .split(',')
    .map((c) => c.trim().toUpperCase())
    .filter((c) => c && c !== 'ALL');

  const isAll = selectedList.length === 0 || String(selectedCategory).toUpperCase() === 'ALL';

  const handleCategoryClick = (key) => {
    if (key === 'ALL') {
      onSelectCategory('ALL');
      return;
    }
    let next;
    if (isAll) {
      next = [key];
    } else if (selectedList.includes(key)) {
      next = selectedList.filter((k) => k !== key);
    } else {
      next = [...selectedList, key];
    }
    if (next.length === 0 || next.length >= 4) {
      onSelectCategory('ALL');
    } else {
      onSelectCategory(next.join(','));
    }
  };

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }} role="toolbar" aria-label="Filter findings by category">
      <span style={{ fontSize: 11.5, color: 'var(--text-tertiary)', fontWeight: 600, marginRight: 2 }}>
        Category:
      </span>
      {categories.map((cat) => {
        const isSelected = cat.key === 'ALL' ? isAll : (!isAll && selectedList.includes(cat.key));
        return (
          <button
            key={cat.key}
            type="button"
            disabled={disabled}
            onClick={() => handleCategoryClick(cat.key)}
            style={{
              ...PILL,
              background: isSelected ? 'var(--bg-surface-hover, rgba(59, 130, 246, 0.12))' : 'var(--bg-surface-raised)',
              borderColor: isSelected ? 'var(--accent-blue, #3b82f6)' : 'var(--border-main)',
              color: isSelected ? 'var(--accent-blue, #3b82f6)' : 'var(--text-secondary)',
              boxShadow: isSelected ? '0 0 0 1px var(--accent-blue, #3b82f6)' : 'none',
              opacity: disabled ? 0.6 : 1,
            }}
            aria-pressed={isSelected}
            aria-label={`Filter by ${cat.label} (${cat.count})`}
          >
            {cat.key !== 'ALL' && (
              <input
                type="checkbox"
                checked={isSelected}
                readOnly
                tabIndex={-1}
                style={{
                  marginRight: 2,
                  cursor: 'pointer',
                  accentColor: 'var(--accent-blue, #3b82f6)',
                  pointerEvents: 'none',
                  width: 12,
                  height: 12,
                }}
              />
            )}
            <span>{cat.label}</span>
            <span
              style={{
                fontSize: 11,
                padding: '1px 6px',
                borderRadius: 999,
                background: isSelected ? 'rgba(59, 130, 246, 0.2)' : 'var(--bg-surface)',
                color: cat.color || 'var(--text-primary)',
                fontWeight: 700,
              }}
            >
              {cat.count}
            </span>
          </button>
        );
      })}
    </div>
  );
}
