import React from 'react';
import { Database, X } from 'lucide-react';

/**
 * Shared Warehouse (Platform) Filter Component
 * Dynamically populated from backend platforms, renders counts and removable chips.
 */
export default function WarehouseFilter({
  warehouses,
  platforms,
  selectedWarehouse = '',
  onSelectWarehouse,
  counts = {},
  totalCount,
  compact = false,
  style = {},
}) {
  const list = warehouses || platforms || [];
  const calculatedTotal = totalCount ?? (
    counts && Object.keys(counts).length > 0
      ? Object.values(counts).reduce((a, b) => a + b, 0)
      : list.length
  );

  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6, ...style }}>
      <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <Database size={13} style={{ color: 'var(--text-secondary)', flexShrink: 0 }} />
        <select
          value={selectedWarehouse}
          onChange={(e) => onSelectWarehouse(e.target.value)}
          aria-label="Filter by warehouse"
          style={{
            padding: compact ? '4px 8px' : '5px 8px',
            borderRadius: 6,
            border: '1px solid var(--border-main)',
            background: 'var(--bg-surface-raised, var(--bg-surface))',
            color: 'var(--text-primary)',
            fontSize: 12,
            cursor: 'pointer',
            outline: 'none',
            maxWidth: 180,
          }}
        >
          <option value="">All warehouses ({calculatedTotal})</option>
          {list.map((plat) => {
            const count = counts[plat] ?? 0;
            const label = (plat === 'NOT_SET' || plat === 'UNKNOWN') ? 'Not set' : plat;
            return (
              <option key={plat} value={plat}>
                {label} ({count})
              </option>
            );
          })}
        </select>
      </div>

      {selectedWarehouse && (
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 4,
            padding: '2px 8px',
            borderRadius: 12,
            background: 'rgba(59, 130, 246, 0.1)',
            border: '1px solid rgba(59, 130, 246, 0.3)',
            color: 'var(--color-primary, #3b82f6)',
            fontSize: 11,
            fontWeight: 500,
          }}
        >
          {(selectedWarehouse === 'NOT_SET' || selectedWarehouse === 'UNKNOWN') ? 'Not set' : selectedWarehouse}
          <button
            type="button"
            onClick={() => onSelectWarehouse('')}
            style={{
              background: 'none',
              border: 'none',
              padding: 0,
              marginLeft: 2,
              cursor: 'pointer',
              display: 'inline-flex',
              color: 'inherit',
            }}
            title="Clear warehouse filter"
            aria-label="Clear warehouse filter"
          >
            <X size={11} />
          </button>
        </span>
      )}
    </div>
  );
}
