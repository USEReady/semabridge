/**
 * SearchableSelect — Dropdown with built-in HP search.
 *
 * Props:
 *   items          — array of objects
 *   displayKey     — field to show as label (default "name")
 *   valueKey       — field used as the selected value (default "id")
 *   searchFields   — fields to search across (default [displayKey])
 *   placeholder    — input placeholder text
 *   ghostValue     — grayed text shown when no value is set (global default hint)
 *   value          — currently selected value (controlled)
 *   onChange       — called with the selected item object
 *   disabled       — disables the control
 *   groupKey       — optional field to group items under headings
 *   renderItem     — optional custom item renderer (item) => ReactNode
 *   maxHeight      — dropdown max-height in px (default 280)
 *   loading        — show loading spinner instead of list
 *   clearable      — show clear button when a value is selected
 */

import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { ChevronDown, X, Search, Loader2, Regex } from 'lucide-react';
import { useHPSearch } from '../../hooks/useHPSearch';
import { matchesSmartQuery } from './SmartSearchBar';

export default function SearchableSelect({
  items = [],
  displayKey = 'name',
  valueKey = 'id',
  searchFields,
  placeholder = 'Select…',
  ghostValue = null,
  value = null,
  onChange,
  disabled = false,
  groupKey = null,
  renderItem = null,
  maxHeight = 280,
  loading = false,
  clearable = true,
}) {
  const fields = searchFields ?? [displayKey];
  const [open, setOpen] = useState(false);
  const [useRegex, setUseRegex] = useState(false);
  const containerRef = useRef(null);

  // Normalise items so every entry has a usable `id` field for MiniSearch
  const normItems = items.map((item, i) => ({
    ...item,
    _sid: item[valueKey] != null ? String(item[valueKey]) : `__idx_${i}`,
  }));

  const { results, query, setQuery } = useHPSearch(normItems, fields, {
    idField: '_sid',
    maxResults: 1000,
  });

  const regexError = useMemo(() => {
    const q = String(query || '').trim();
    if (!useRegex || !q) return '';
    try {
      new RegExp(q);
      return '';
    } catch (err) {
      return err?.message || 'Invalid regex';
    }
  }, [query, useRegex]);

  const displayResults = useMemo(() => {
    if (!useRegex) return results;
    const q = String(query || '').trim();
    if (!q || regexError) return [];
    return normItems.filter(item => {
      const haystack = fields.map(field => String(item?.[field] ?? '')).join(' ');
      return matchesSmartQuery(haystack, q, true);
    });
  }, [useRegex, results, query, regexError, normItems, fields]);

  // Find the currently selected item for display
  const selectedItem = items.find(it => String(it[valueKey]) === String(value ?? '')) ?? null;

  // Close on outside click
  useEffect(() => {
    const handler = (e) => {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setOpen(false);
        setQuery('');
        setUseRegex(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [setQuery]);

  const handleSelect = useCallback((item) => {
    onChange?.(item);
    setOpen(false);
    setQuery('');
    setUseRegex(false);
  }, [onChange, setQuery]);

  const handleClear = useCallback((e) => {
    e.stopPropagation();
    onChange?.(null);
  }, [onChange]);

  // Group items if groupKey is provided
  const grouped = groupKey
    ? displayResults.reduce((acc, item) => {
        const grp = item[groupKey] ?? 'Other';
        if (!acc[grp]) acc[grp] = [];
        acc[grp].push(item);
        return acc;
      }, {})
    : null;

  const displayLabel = selectedItem
    ? (renderItem ? null : String(selectedItem[displayKey] ?? ''))
    : null;

  return (
    <div ref={containerRef} style={{ position: 'relative', width: '100%' }}>
      {/* Trigger */}
      <button
        type="button"
        disabled={disabled}
        onClick={() => { if (!disabled) setOpen(o => !o); }}
        style={{
          display: 'flex',
          alignItems: 'center',
          width: '100%',
          background: 'var(--bg-input)',
          border: '1px solid var(--border-main)',
          borderRadius: 8,
          color: displayLabel ? 'var(--text-primary)' : 'var(--text-tertiary)',
          padding: '8px 10px',
          fontSize: 13,
          cursor: disabled ? 'not-allowed' : 'pointer',
          gap: 6,
          outline: open ? '2px solid var(--accent-blue)' : 'none',
          opacity: disabled ? 0.55 : 1,
          textAlign: 'left',
        }}
      >
        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {selectedItem
            ? (renderItem ? renderItem(selectedItem) : displayLabel)
            : (ghostValue
                ? <span style={{ color: 'var(--text-tertiary)', fontStyle: 'italic' }}>{ghostValue}</span>
                : placeholder)
          }
        </span>

        {/* Clear button */}
        {clearable && selectedItem && !disabled && (
          <span
            onClick={handleClear}
            style={{ color: 'var(--text-tertiary)', display: 'flex', lineHeight: 1 }}
          >
            <X size={13} />
          </span>
        )}
        <ChevronDown
          size={14}
          style={{
            color: 'var(--text-tertiary)',
            flexShrink: 0,
            transform: open ? 'rotate(180deg)' : 'none',
            transition: 'transform 0.15s',
          }}
        />
      </button>

      {/* Dropdown */}
      {open && (
        <div
          style={{
            position: 'absolute',
            top: 'calc(100% + 4px)',
            left: 0,
            right: 0,
            zIndex: 9999,
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-main)',
            borderRadius: 8,
            boxShadow: '0 8px 32px rgba(0,0,0,0.18)',
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
          }}
        >
          {/* Search input */}
          <div style={{ padding: '8px 8px 0', position: 'relative' }}>
            <Search
              size={13}
              style={{
                position: 'absolute', left: 18, top: '50%', transform: 'translateY(-20%)',
                color: 'var(--text-tertiary)', pointerEvents: 'none',
              }}
            />
            <input
              autoFocus
              type="text"
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Search…"
              style={{
                width: '100%',
                background: 'var(--bg-input)',
                border: `1px solid ${regexError ? 'var(--color-error)' : 'var(--border-subtle)'}`,
                borderRadius: 6,
                color: 'var(--text-primary)',
                padding: '6px 28px 6px 28px',
                fontSize: 12,
                outline: 'none',
                boxSizing: 'border-box',
              }}
            />
            <button
              type="button"
              onMouseDown={(e) => {
                e.stopPropagation();
              }}
              onClick={(e) => {
                e.stopPropagation();
                setUseRegex(v => !v);
              }}
              title={useRegex ? 'Regex enabled' : 'Regex disabled'}
              style={{
                position: 'absolute',
                right: 14,
                top: '50%',
                transform: 'translateY(-20%)',
                zIndex: 2,
                width: 18,
                height: 18,
                borderRadius: 5,
                border: '1px solid var(--border-main)',
                background: useRegex ? 'var(--accent-blue)18' : 'var(--bg-surface)',
                color: useRegex ? 'var(--accent-blue)' : 'var(--text-tertiary)',
                cursor: 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Regex size={10} />
            </button>
            {regexError && (
              <div style={{ marginTop: 4, fontSize: 10, color: 'var(--color-error)' }}>
                Regex error: {regexError}
              </div>
            )}
          </div>

          {/* Items list */}
          <div
            style={{
              overflowY: 'auto',
              maxHeight,
              padding: '4px 4px',
              marginTop: 4,
            }}
          >
            {loading ? (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
                <Loader2 size={16} style={{ color: 'var(--text-tertiary)', animation: 'spin 1s linear infinite' }} />
              </div>
            ) : displayResults.length === 0 ? (
              <div style={{ padding: '10px 12px', fontSize: 12, color: 'var(--text-tertiary)', textAlign: 'center' }}>
                No results
              </div>
            ) : grouped ? (
              Object.entries(grouped).map(([grp, grpItems]) => (
                <div key={grp}>
                  <div style={{
                    padding: '6px 10px 2px',
                    fontSize: 10,
                    fontWeight: 700,
                    textTransform: 'uppercase',
                    letterSpacing: '0.06em',
                    color: 'var(--text-tertiary)',
                  }}>
                    {grp}
                  </div>
                  {grpItems.map(item => (
                    <ItemRow
                      key={item._sid}
                      item={item}
                      displayKey={displayKey}
                      valueKey={valueKey}
                      selected={String(item[valueKey]) === String(value ?? '')}
                      onSelect={handleSelect}
                      renderItem={renderItem}
                    />
                  ))}
                </div>
              ))
            ) : (
              displayResults.map(item => (
                <ItemRow
                  key={item._sid}
                  item={item}
                  displayKey={displayKey}
                  valueKey={valueKey}
                  selected={String(item[valueKey]) === String(value ?? '')}
                  onSelect={handleSelect}
                  renderItem={renderItem}
                />
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function ItemRow({ item, displayKey, valueKey, selected, onSelect, renderItem }) {
  return (
    <button
      type="button"
      onClick={() => onSelect(item)}
      style={{
        display: 'flex',
        alignItems: 'center',
        width: '100%',
        padding: '7px 10px',
        background: selected ? 'var(--accent-blue)15' : 'transparent',
        border: 'none',
        borderRadius: 6,
        cursor: 'pointer',
        fontSize: 13,
        color: selected ? 'var(--accent-blue)' : 'var(--text-primary)',
        textAlign: 'left',
        gap: 6,
      }}
      onMouseEnter={e => { if (!selected) e.currentTarget.style.background = 'var(--bg-surface-hover)'; }}
      onMouseLeave={e => { if (!selected) e.currentTarget.style.background = 'transparent'; }}
    >
      {renderItem ? renderItem(item) : (
        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {String(item[displayKey] ?? '')}
        </span>
      )}
    </button>
  );
}