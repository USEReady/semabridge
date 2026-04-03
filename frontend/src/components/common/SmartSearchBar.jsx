import { Search, Regex, X } from 'lucide-react';

export function matchesSmartQuery(value, query, useRegex) {
  const haystack = String(value || '');
  const q = String(query || '').trim();

  if (!q) return true;

  if (useRegex) {
    try {
      return new RegExp(q, 'i').test(haystack);
    } catch {
      return false;
    }
  }

  const lowerHaystack = haystack.toLowerCase();
  const lowerQuery = q.toLowerCase();
  if (lowerHaystack.startsWith(lowerQuery)) return true;

  const words = lowerHaystack.split(/\s+/).filter(Boolean);
  return words.some(word => word.startsWith(lowerQuery));
}

export default function SmartSearchBar({
  value,
  onChange,
  useRegex = false,
  onToggleRegex,
  placeholder = 'Search...',
  width = '100%',
  allowRegex = true,
}) {
  const query = String(value || '');
  let regexError = '';

  if (allowRegex && useRegex && query.trim()) {
    try {
      // Validate user regex in-place to avoid throwing in consumers.
      new RegExp(query);
    } catch (err) {
      regexError = err?.message || 'Invalid regex';
    }
  }

  return (
    <div style={{ width, display: 'flex', flexDirection: 'column', gap: 4 }}>
      <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
        <Search
          size={13}
          style={{
            position: 'absolute',
            left: 10,
            color: 'var(--text-tertiary)',
            pointerEvents: 'none',
          }}
        />
        <input
          value={query}
          onChange={e => onChange?.(e.target.value)}
          placeholder={placeholder}
          style={{
            width: '100%',
            paddingLeft: 30,
            paddingRight: 84,
            paddingTop: 7,
            paddingBottom: 7,
            background: 'var(--bg-input)',
            border: `1px solid ${regexError ? 'var(--color-error)' : 'var(--border-main)'}`,
            borderRadius: 8,
            fontSize: 12,
            color: 'var(--text-primary)',
            outline: 'none',
            boxSizing: 'border-box',
          }}
          onFocus={e => {
            if (!regexError) e.target.style.borderColor = 'var(--accent-blue)';
          }}
          onBlur={e => {
            e.target.style.borderColor = regexError ? 'var(--color-error)' : 'var(--border-main)';
          }}
        />

        {allowRegex && (
          <button
            type="button"
            onMouseDown={(e) => {
              e.stopPropagation();
            }}
            onClick={(e) => {
              e.stopPropagation();
              onToggleRegex?.(!useRegex);
            }}
            title={useRegex ? 'Regex enabled' : 'Regex disabled'}
            style={{
              position: 'absolute',
              right: query ? 28 : 6,
              zIndex: 2,
              width: 20,
              height: 20,
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
            <Regex size={11} />
          </button>
        )}

        {query && (
          <button
            type="button"
            onMouseDown={(e) => {
              e.stopPropagation();
            }}
            onClick={(e) => {
              e.stopPropagation();
              onChange?.('');
            }}
            title="Clear"
            style={{
              position: 'absolute',
              right: 6,
              zIndex: 2,
              width: 20,
              height: 20,
              border: 'none',
              borderRadius: 5,
              background: 'transparent',
              color: 'var(--text-tertiary)',
              cursor: 'pointer',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <X size={12} />
          </button>
        )}
      </div>
      {regexError && (
        <div style={{ fontSize: 10, color: 'var(--color-error)' }}>
          Regex error: {regexError}
        </div>
      )}
    </div>
  );
}