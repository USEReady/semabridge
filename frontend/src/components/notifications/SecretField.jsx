/**
 * SecretField — masked secret input for sensitive config values (API keys, URLs, passwords).
 *
 * Behavior:
 *   - Shows masked value on load (e.g., "••••••••abcd" or "https://...abcd")
 *   - "Update" link toggles to editable input
 *   - On blur without change: returns to masked display
 *   - On change: calls onUpdate(newValue) with new value only
 *   - Never emits the masked string as a new value
 *
 * Props:
 *   label      : string — field label
 *   maskedValue: string — pre-masked display value (e.g., "https://hooks.slack.com/...abcd")
 *   onUpdate   : (newValue: string) => void — called when user confirms new value
 *   placeholder: string — input placeholder
 */

import { useState } from 'react';

function maskSecretValue(value) {
  if (!value) return '••••••••';
  const valueStr = String(value);
  if (valueStr.length <= 8) return '•'.repeat(valueStr.length);
  return '••••••••' + valueStr.slice(-4);
}

export default function SecretField({
  label = 'Secret',
  maskedValue,
  onUpdate,
  placeholder = 'Enter new value',
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState('');

  const displayValue = maskedValue || maskSecretValue('');

  const handleStartEdit = () => {
    setEditValue('');
    setIsEditing(true);
  };

  const handleConfirm = () => {
    if (editValue.trim()) {
      onUpdate(editValue.trim());
    }
    setIsEditing(false);
    setEditValue('');
  };

  const handleCancel = () => {
    setIsEditing(false);
    setEditValue('');
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') handleConfirm();
    if (e.key === 'Escape') handleCancel();
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {label && (
        <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
          {label}
        </label>
      )}

      {!isEditing ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <code
            style={{
              flex: 1,
              padding: '8px 12px',
              borderRadius: 6,
              border: '1px solid var(--border-main)',
              background: 'var(--bg-surface-raised)',
              fontFamily: 'monospace',
              fontSize: 13,
              color: 'var(--text-secondary)',
              wordBreak: 'break-all',
            }}
          >
            {displayValue}
          </code>
          <button
            onClick={handleStartEdit}
            style={{
              padding: '6px 12px',
              fontSize: 12,
              fontWeight: 500,
              borderRadius: 6,
              border: '1px solid var(--border-main)',
              background: 'var(--bg-surface)',
              color: 'var(--accent-blue)',
              cursor: 'pointer',
              transition: 'background-color 0.15s, color 0.15s',
              whiteSpace: 'nowrap',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor = 'rgba(88, 166, 255, 0.12)';
              e.currentTarget.style.borderColor = 'var(--accent-blue)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = 'var(--bg-surface)';
              e.currentTarget.style.borderColor = 'var(--border-main)';
            }}
          >
            Update
          </button>
        </div>
      ) : (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <input
            type="password"
            autoFocus
            value={editValue}
            onChange={(e) => setEditValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            style={{
              flex: 1,
              padding: '8px 12px',
              borderRadius: 6,
              border: '1px solid var(--accent-blue)',
              background: 'var(--bg-surface-raised)',
              fontSize: 13,
              color: 'var(--text-primary)',
              outline: 'none',
              fontFamily: 'inherit',
            }}
          />
          <button
            onClick={handleConfirm}
            style={{
              padding: '6px 12px',
              fontSize: 12,
              fontWeight: 500,
              borderRadius: 6,
              border: '1px solid var(--color-success)',
              background: 'rgba(34, 197, 94, 0.12)',
              color: 'var(--color-success)',
              cursor: 'pointer',
              whiteSpace: 'nowrap',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor = 'rgba(34, 197, 94, 0.2)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = 'rgba(34, 197, 94, 0.12)';
            }}
          >
            Save
          </button>
          <button
            onClick={handleCancel}
            style={{
              padding: '6px 12px',
              fontSize: 12,
              fontWeight: 500,
              borderRadius: 6,
              border: '1px solid var(--border-main)',
              background: 'var(--bg-surface)',
              color: 'var(--text-secondary)',
              cursor: 'pointer',
              whiteSpace: 'nowrap',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor = 'var(--bg-surface-hover)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = 'var(--bg-surface)';
            }}
          >
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}
