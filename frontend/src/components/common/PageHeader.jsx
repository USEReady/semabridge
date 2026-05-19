import { createElement, isValidElement } from 'react';

/**
 * PageHeader — standardised page title block.
 * Props:
 *   title        : string  (required)
 *   description  : string  (optional sub-title)
 *   subtitle     : string  (optional alias for description)
 *   action       : ReactNode | { label: string, icon: ReactNode | ComponentType, onClick: fn }
 *   breadcrumb   : string[]  (optional breadcrumb trail, e.g. ['Projects', 'Alpha'])
 */
function renderActionIcon(icon) {
  if (!icon) return null;
  if (isValidElement(icon)) return icon;
  if (typeof icon === 'function' || (typeof icon === 'object' && icon.$$typeof)) {
    return createElement(icon, { size: 16, 'aria-hidden': true });
  }
  return icon;
}

export default function PageHeader({ title, description, subtitle, action, breadcrumb }) {
  const supportingText = description ?? subtitle;

  return (
    <div className="flex items-start justify-between mb-6 flex-shrink-0">
      <div>
        {breadcrumb && breadcrumb.length > 0 && (
          <div className="flex items-center gap-1.5 mb-1">
            {breadcrumb.map((crumb, i) => (
              <span key={i} className="flex items-center gap-1.5">
                {i > 0 && (
                  <span className="text-tertiary" style={{ fontSize: 11 }}>›</span>
                )}
                <span
                  className="text-xs font-medium"
                  style={{ color: i < breadcrumb.length - 1 ? 'var(--text-tertiary)' : 'var(--text-secondary)' }}
                >
                  {crumb}
                </span>
              </span>
            ))}
          </div>
        )}
        <h1
          className="font-bold"
          style={{
            fontSize: 22,
            margin: 0,
            color: 'var(--text-primary)',
            lineHeight: 1.2,
          }}
        >
          {title}
        </h1>
        {supportingText && (
          <p className="text-tertiary text-sm mt-1" style={{ margin: '6px 0 0' }}>
            {supportingText}
          </p>
        )}
      </div>

      {action && (
        <div className="flex-shrink-0 ml-4">
          {typeof action === 'object' && action.label ? (
            <button
              onClick={action.onClick}
              className="flex items-center gap-2 rounded-lg font-semibold text-sm px-4 py-2 theme-transition"
              style={{
                background: 'var(--accent-blue)',
                color: '#fff',
                border: 'none',
                cursor: 'pointer',
                boxShadow: '0 2px 8px rgba(99,102,241,0.25)',
                whiteSpace: 'nowrap',
              }}
              onMouseEnter={(e) => { e.currentTarget.style.opacity = '0.9'; }}
              onMouseLeave={(e) => { e.currentTarget.style.opacity = '1'; }}
            >
              {renderActionIcon(action.icon)}
              {action.label}
            </button>
          ) : (
            action
          )}
        </div>
      )}
    </div>
  );
}
