import React from 'react';
import { Database, Film, AlertTriangle } from 'lucide-react';

const BTN = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 6,
  padding: '5px 12px',
  borderRadius: 6,
  fontSize: 12,
  fontWeight: 600,
  cursor: 'pointer',
  border: '1px solid var(--border-main)',
  background: 'transparent',
  color: 'var(--text-secondary)',
  transition: 'all 0.15s ease',
};

/**
 * DriftModeToggle Component
 * Toggles between Live Warehouse and Mock (recorded) mode.
 * Hides or disables mock mode when capabilities.allow_mock is false.
 */
export default function DriftModeToggle({
  mode = 'live',
  onChangeMode = () => {},
  capabilities = null,
  disabled = false,
}) {
  const allowMock = capabilities ? Boolean(capabilities.allow_mock) : true;
  const isMock = mode === 'mock';

  // If mock mode is explicitly disallowed by backend capabilities, do not show the toggle
  if (capabilities && !allowMock && !isMock) {
    return (
      <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11.5, color: 'var(--text-tertiary)' }}>
        <Database size={13} style={{ color: 'var(--color-success, #22c55e)' }} />
        <span>Live Warehouse Mode</span>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          background: 'var(--bg-surface-raised)',
          border: '1px solid var(--border-main)',
          borderRadius: 8,
          padding: 2,
        }}
        role="group"
        aria-label="Execution mode toggle"
      >
        <button
          type="button"
          disabled={disabled}
          onClick={() => onChangeMode('live')}
          style={{
            ...BTN,
            background: !isMock ? 'var(--bg-surface)' : 'transparent',
            borderColor: !isMock ? 'var(--border-main)' : 'transparent',
            color: !isMock ? 'var(--text-primary)' : 'var(--text-secondary)',
            boxShadow: !isMock ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
          }}
          aria-pressed={!isMock}
        >
          <Database size={13} style={{ color: !isMock ? 'var(--color-success, #22c55e)' : 'inherit' }} />
          <span>Live Warehouse</span>
        </button>

        <button
          type="button"
          disabled={disabled || !allowMock}
          onClick={() => onChangeMode('mock')}
          style={{
            ...BTN,
            background: isMock ? 'rgba(234, 179, 8, 0.15)' : 'transparent',
            borderColor: isMock ? 'rgba(234, 179, 8, 0.4)' : 'transparent',
            color: isMock ? 'var(--color-warning, #eab308)' : 'var(--text-secondary)',
            opacity: !allowMock ? 0.4 : 1,
            cursor: !allowMock ? 'not-allowed' : 'pointer',
          }}
          aria-pressed={isMock}
          title={!allowMock ? 'Mock mode is not enabled in this environment' : 'Run drift checks against recorded offline fixtures'}
        >
          <Film size={13} style={{ color: isMock ? 'var(--color-warning, #eab308)' : 'inherit' }} />
          <span>Mock (recorded)</span>
        </button>
      </div>

      {isMock && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            padding: '4px 10px',
            borderRadius: 6,
            background: 'rgba(234, 179, 8, 0.1)',
            border: '1px solid rgba(234, 179, 8, 0.25)',
            color: 'var(--color-warning, #eab308)',
            fontSize: 11,
          }}
        >
          <AlertTriangle size={12} />
          <span>Mock mode active: drift checks evaluate recorded warehouse DDL fixtures offline.</span>
        </div>
      )}
    </div>
  );
}
