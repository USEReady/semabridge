import { Search } from 'lucide-react';

/**
 * SearchInput — styled search field with prepended icon.
 * Props:
 *   value       : string
 *   onChange    : (value: string) => void
 *   placeholder : string
 *   className   : string  (wrapper extra classes)
 *   width       : number | string  (default 260)
 */
export default function SearchInput({ value, onChange, placeholder = 'Search…', className = '', width = 260 }) {
  return (
    <div
      className={`relative flex items-center ${className}`}
      style={{ width }}
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
        value={value}
        onChange={(e) => onChange?.(e.target.value)}
        placeholder={placeholder}
        className="w-full text-sm rounded-lg theme-transition"
        style={{
          background: 'var(--bg-input)',
          border: '1px solid var(--border-main)',
          color: 'var(--text-primary)',
          padding: '7px 12px 7px 30px',
          outline: 'none',
          fontFamily: 'inherit',
          width: '100%',
        }}
        onFocus={(e) => { e.currentTarget.style.borderColor = 'var(--accent-blue)'; }}
        onBlur={(e) => { e.currentTarget.style.borderColor = 'var(--border-main)'; }}
      />
    </div>
  );
}
