/**
 * LevelMatrix — checkbox group for 6 notification levels with bitmask encoding.
 *
 * Levels:
 *   SYNC_RESULT (1)   | CRITICAL (2) | ERROR (4)    | WARNING (8)  | INFO (16) | DEBUG (32)
 *
 * Props:
 *   value    : number — bitmask (0-63)
 *   onChange : (newMask: number) => void
 */

const LEVELS = [
  { value: 1, name: 'SYNC_RESULT', label: 'Sync Result' },
  { value: 2, name: 'CRITICAL', label: 'Critical' },
  { value: 4, name: 'ERROR', label: 'Error' },
  { value: 8, name: 'WARNING', label: 'Warning' },
  { value: 16, name: 'INFO', label: 'Info' },
  { value: 32, name: 'DEBUG', label: 'Debug' },
];

const ALL_MASK = 63; // 1 | 2 | 4 | 8 | 16 | 32

export default function LevelMatrix({ value = 0, onChange }) {
  const isChecked = (level) => (value & level) !== 0;
  const allChecked = value === ALL_MASK;
  const someChecked = value > 0;

  const handleLevel = (level) => {
    const newValue = isChecked(level) ? value & ~level : value | level;
    onChange(newValue);
  };

  const handleSelectAll = () => {
    onChange(allChecked ? 0 : ALL_MASK);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 12 }}>
        {LEVELS.map((level) => (
          <label
            key={level.value}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              cursor: 'pointer',
              userSelect: 'none',
              padding: '4px 8px',
              borderRadius: 6,
              transition: 'background-color 0.15s',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor = 'var(--bg-surface-hover)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = 'transparent';
            }}
          >
            <input
              type="checkbox"
              checked={isChecked(level.value)}
              onChange={() => handleLevel(level.value)}
              style={{ cursor: 'pointer' }}
            />
            <span
              style={{
                fontSize: 13,
                fontWeight: 500,
                color: 'var(--text-primary)',
              }}
            >
              {level.label}
            </span>
          </label>
        ))}
      </div>

      <div style={{ display: 'flex', gap: 8, borderTop: '1px solid var(--border-main)', paddingTop: 12 }}>
        <button
          onClick={handleSelectAll}
          style={{
            flex: 1,
            padding: '6px 12px',
            fontSize: 12,
            fontWeight: 500,
            borderRadius: 6,
            border: '1px solid var(--border-main)',
            background: 'var(--bg-surface)',
            color: 'var(--text-primary)',
            cursor: 'pointer',
            transition: 'background-color 0.15s, border-color 0.15s',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.backgroundColor = 'var(--bg-surface-hover)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.backgroundColor = 'var(--bg-surface)';
          }}
        >
          Select All
        </button>
        <button
          onClick={() => onChange(0)}
          style={{
            flex: 1,
            padding: '6px 12px',
            fontSize: 12,
            fontWeight: 500,
            borderRadius: 6,
            border: '1px solid var(--border-main)',
            background: 'var(--bg-surface)',
            color: 'var(--text-primary)',
            cursor: 'pointer',
            transition: 'background-color 0.15s, border-color 0.15s',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.backgroundColor = 'var(--bg-surface-hover)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.backgroundColor = 'var(--bg-surface)';
          }}
        >
          Clear
        </button>
      </div>
    </div>
  );
}
