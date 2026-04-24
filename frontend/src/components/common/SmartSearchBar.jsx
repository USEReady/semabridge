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
      <div 
        className="group relative flex items-center theme-transition" 
        style={{ width: '100%' }}
      >
        <Search
          size={14}
          className="absolute left-3 text-tertiary group-focus-within:text-secondary theme-transition"
          style={{ pointerEvents: 'none' }}
        />
        <input
          value={query}
          onChange={e => onChange?.(e.target.value)}
          placeholder={placeholder}
          className="w-full text-[13px] rounded-lg theme-transition"
          style={{
            padding: '9px 12px 9px 36px',
            paddingRight: allowRegex ? (query ? 78 : 46) : (query? 36 : 12),
            background: 'var(--bg-app)',
            border: `1px solid ${regexError ? 'var(--color-error)' : 'var(--border-main)'}`,
            color: 'var(--text-primary)',
            outline: 'none',
            boxShadow: '0 1px 2px rgba(0,0,0,0.1)',
          }}
          onFocus={e => {
            if (!regexError) e.target.style.borderColor = 'var(--accent-blue)';
            e.target.style.boxShadow = '0 0 0 3px var(--color-accent-faint)';
          }}
          onBlur={e => {
            e.target.style.borderColor = regexError ? 'var(--color-error)' : 'var(--border-main)';
            e.target.style.boxShadow = '0 1px 2px rgba(0,0,0,0.1)';
          }}
        />

        <div className="absolute right-2 flex items-center gap-1.5">
          {allowRegex && (
            <button
              type="button"
              onMouseDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation();
                onToggleRegex?.(!useRegex);
              }}
              title={useRegex ? 'Regex enabled' : 'Regex disabled'}
              className="flex items-center justify-center rounded border theme-transition"
              style={{
                width: 24,
                height: 24,
                fontSize: 9,
                fontWeight: 700,
                background: useRegex ? 'var(--color-accent-faint)' : 'var(--bg-surface-raised)',
                borderColor: useRegex ? 'var(--accent-blue)' : 'var(--border-main)',
                color: useRegex ? 'var(--accent-blue)' : 'var(--text-tertiary)',
                cursor: 'pointer',
              }}
            >
              <Regex size={11} />
            </button>
          )}

          {query && (
            <button
              type="button"
              onMouseDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                e.stopPropagation();
                onChange?.('');
              }}
              title="Clear"
              className="flex items-center justify-center rounded border border-main bg-surface-raised text-tertiary hover:text-primary theme-transition"
              style={{
                width: 24,
                height: 24,
                cursor: 'pointer',
              }}
            >
              <X size={13} />
            </button>
          )}
        </div>
      </div>
      {regexError && (
        <div style={{ fontSize: 10, color: 'var(--color-error)' }}>
          Regex error: {regexError}
        </div>
      )}
    </div>
  );
}