import React from 'react';

/**
 * Shared Empty State Component
 * Renders an icon, title, one-line explanation, and primary/secondary actions.
 */
export default function EmptyState({
  icon: Icon,
  title,
  description,
  actionLabel,
  onAction,
  secondaryLabel,
  onSecondary,
  style = {},
  compact = false,
}) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: compact ? '24px 16px' : '44px 24px',
        textAlign: 'center',
        background: 'var(--bg-surface)',
        border: '1px solid var(--border-main)',
        borderRadius: 10,
        ...style,
      }}
    >
      {Icon && (
        <div
          style={{
            width: compact ? 36 : 48,
            height: compact ? 36 : 48,
            borderRadius: '50%',
            background: 'var(--bg-surface-raised, rgba(255, 255, 255, 0.04))',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            marginBottom: 12,
            color: 'var(--text-secondary)',
          }}
        >
          {React.isValidElement(Icon) ? Icon : <Icon size={compact ? 18 : 22} />}
        </div>
      )}
      {title && (
        <h3
          style={{
            margin: '0 0 6px 0',
            fontSize: compact ? 14 : 16,
            fontWeight: 600,
            color: 'var(--text-primary)',
          }}
        >
          {title}
        </h3>
      )}
      {description && (
        <p
          style={{
            margin: '0 0 16px 0',
            fontSize: 13,
            color: 'var(--text-secondary)',
            maxWidth: 420,
            lineHeight: 1.5,
          }}
        >
          {description}
        </p>
      )}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {actionLabel && onAction && (
          <button
            type="button"
            onClick={onAction}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '6px 14px',
              borderRadius: 6,
              background: 'var(--color-primary, #3b82f6)',
              color: '#ffffff',
              border: '1px solid var(--color-primary, #3b82f6)',
              fontSize: 12.5,
              fontWeight: 500,
              cursor: 'pointer',
            }}
          >
            {actionLabel}
          </button>
        )}
        {secondaryLabel && onSecondary && (
          <button
            type="button"
            onClick={onSecondary}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '6px 14px',
              borderRadius: 6,
              background: 'transparent',
              color: 'var(--text-secondary)',
              border: '1px solid var(--border-main)',
              fontSize: 12.5,
              cursor: 'pointer',
            }}
          >
            {secondaryLabel}
          </button>
        )}
      </div>
    </div>
  );
}
