import { Search, X } from 'lucide-react';

const REGEX_PREFIX_RE = /^(?:re|regex):(.+)$/i;
const PREFIX_PREFIX_RE = /^(?:p|prefix):(.+)$/i;

export function getSmartQueryMode(query, useRegex = false) {
  const q = String(query || '').trim();
  if (!q) return { mode: 'empty', term: '' };

  const prefixMatch = q.match(PREFIX_PREFIX_RE);
  if (prefixMatch) return { mode: 'prefix', term: prefixMatch[1].trim() };

  const regexMatch = q.match(REGEX_PREFIX_RE);
  if (regexMatch) return { mode: 'regex', pattern: regexMatch[1].trim(), flags: 'i' };

  if (q.startsWith('/') && q.length > 2 && q.lastIndexOf('/') > 0) {
    const lastSlash = q.lastIndexOf('/');
    return {
      mode: 'regex',
      pattern: q.slice(1, lastSlash),
      flags: q.slice(lastSlash + 1) || 'i',
    };
  }

  if (useRegex) return { mode: 'regex', pattern: q, flags: 'i' };
  return { mode: 'prefix', term: q };
}

export function getSmartQueryError(query, useRegex = false) {
  const parsed = getSmartQueryMode(query, useRegex);
  if (parsed.mode !== 'regex' || !parsed.pattern) return '';

  try {
    new RegExp(parsed.pattern, parsed.flags);
    return '';
  } catch (err) {
    return err?.message || 'Invalid regex';
  }
}

export function matchesSmartQuery(value, query, useRegex) {
  const haystack = String(value || '');
  const parsed = getSmartQueryMode(query, useRegex);

  if (parsed.mode === 'empty') return true;

  if (parsed.mode === 'regex') {
    try {
      return new RegExp(parsed.pattern, parsed.flags).test(haystack);
    } catch {
      return false;
    }
  }

  const lowerHaystack = haystack.toLowerCase();
  const lowerQuery = String(parsed.term || '').toLowerCase();
  if (!lowerQuery) return true;
  if (lowerHaystack.startsWith(lowerQuery)) return true;

  const words = lowerHaystack.split(/\s+/).filter(Boolean);
  return words.some(word => word.startsWith(lowerQuery));
}

export default function SmartSearchBar({
  value,
  onChange,
  useRegex = false,
  placeholder = 'Search...',
  width = '100%',
  allowRegex = true,
}) {
  const query = String(value || '');
  const regexError = allowRegex ? getSmartQueryError(query, useRegex) : '';

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
            paddingRight: query ? 46 : 12,
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
