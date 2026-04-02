import { Search, Regex, X } from 'lucide-react';

/**
 * SearchInput — styled search field with prepended icon.
 * Props:
 *   value       : string
 *   onChange    : (value: string) => void
 *   placeholder : string
 *   className   : string  (wrapper extra classes)
 *   width       : number | string  (default 260)
 */
export default function SearchInput({
  value,
  onChange,
  placeholder = 'Search…',
  className = '',
  width = 260,
  useRegex = false,
  onToggleRegex,
  allowRegex = false,
  helperText = '',
  ...inputProps
}) {
  const query = String(value || '');
  let regexError = '';

  if (allowRegex && useRegex && query.trim()) {
    try {
      new RegExp(query);
    } catch (err) {
      regexError = err?.message || 'Invalid regex';
    }
  }

  return (
    <div style={{ width, display: 'flex', flexDirection: 'column', gap: 4 }}>
      <div
        className={`relative flex items-center ${className}`}
        style={{ width: '100%' }}
      >
        <Search
          size={14}
          style={{
            position: 'absolute',
            left: 10,
            color: 'var(--text-tertiary)',
            pointerEvents: 'none',
            flexShrink: 0,
          }}
        />
        <input
          type="text"
          value={query}
          onChange={(e) => onChange?.(e.target.value)}
          placeholder={placeholder}
          className="w-full text-sm rounded-lg theme-transition"
          style={{
            background: 'var(--bg-input)',
            border: `1px solid ${regexError ? 'var(--color-error)' : 'var(--border-main)'}`,
            color: 'var(--text-primary)',
            padding: '7px 12px 7px 30px',
            paddingRight: allowRegex ? (query ? 56 : 34) : (query ? 30 : 12),
            outline: 'none',
            fontFamily: 'inherit',
            width: '100%',
          }}
          onFocus={(e) => {
            if (!regexError) e.currentTarget.style.borderColor = 'var(--accent-blue)';
          }}
          onBlur={(e) => {
            e.currentTarget.style.borderColor = regexError ? 'var(--color-error)' : 'var(--border-main)';
          }}
          {...inputProps}
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

      {!regexError && helperText && (
        <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>
          {helperText}
        </div>
      )}
    </div>
  );
}