import { X, Maximize2, Minimize2 } from 'lucide-react';
import { useEffect } from 'react';

/**
 * Modal — generic overlay dialog.
 * Props:
 *   open     : boolean — controls visibility
 *   onClose  : () => void
 *   title    : string
 *   size     : 'sm' | 'md' | 'lg' | 'xl'  (default 'md')
 *   children : modal body content
 *   footer   : ReactNode — custom footer (optional)
 *   allowMaximize : boolean — show maximize/minimize toggle in header
 *   isMaximized   : boolean — whether modal is in fullscreen mode
 *   onToggleMaximize : () => void — toggle callback
 */
export default function Modal({
  open,
  onClose,
  title,
  size = 'md',
  children,
  footer,
  allowMaximize = false,
  isMaximized = false,
  onToggleMaximize,
}) {
  // Close on Escape key
  useEffect(() => {
    if (!open) return;
    const handler = (e) => { if (e.key === 'Escape') onClose?.(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [open, onClose]);

  if (!open) return null;

  const maxWidths = { sm: 400, md: 520, lg: 680, xl: 860 };
  const maxW = maxWidths[size] ?? 520;

  return (
    <div
      style={{ 
        position: 'fixed', inset: 0, zIndex: 50,
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
        background: 'var(--bg-backdrop)', backdropFilter: 'blur(4px)' 
      }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose?.(); }}
    >
      <div
        style={{
          width: '100%', borderRadius: 16, display: 'flex', flexDirection: 'column', overflow: 'hidden',
          maxWidth: isMaximized ? '96vw' : maxW,
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-main)',
          boxShadow: 'var(--shadow-lg)',
          maxHeight: isMaximized ? '95vh' : 'min(90vh, 800px)',
        }}
      >
        {/* Header */}
        <div
          style={{ 
            display: 'flex', alignItems: 'center', justifyContent: 'space-between', 
            padding: '16px 24px', flexShrink: 0,
            borderBottom: '1px solid var(--border-main)' 
          }}
        >
          <h2 style={{ margin: 0, color: 'var(--text-primary)', fontWeight: 600, fontSize: 16 }}>
            {title}
          </h2>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            {allowMaximize && (
              <button
                onClick={onToggleMaximize}
                style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'center', borderRadius: 8, transition: 'background-color 0.2s',
                  width: 28, height: 28, padding: 0, margin: 0,
                  color: 'var(--text-secondary)', background: 'transparent', border: 'none', cursor: 'pointer', outline: 'none'
                }}
                onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-surface-hover)'; e.currentTarget.style.color = 'var(--text-primary)'; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-secondary)'; }}
                title={isMaximized ? 'Exit fullscreen' : 'Maximize'}
                aria-label={isMaximized ? 'Exit fullscreen' : 'Maximize'}
              >
                {isMaximized ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
              </button>
            )}
            <button
              onClick={onClose}
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'center', borderRadius: 8, transition: 'background-color 0.2s',
                width: 28, height: 28, padding: 0, margin: 0,
                color: 'var(--text-secondary)', background: 'transparent', border: 'none', cursor: 'pointer', outline: 'none'
              }}
              onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-surface-hover)'; e.currentTarget.style.color = 'var(--text-primary)'; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-secondary)'; }}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
              </svg>
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="custom-scrollbar" style={{ flex: 1, overflowY: 'auto', padding: 24 }}>
          {children}
        </div>

        {/* Footer */}
        {footer && (
          <div
            style={{ 
              display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 12, 
              padding: '16px 24px', flexShrink: 0,
              borderTop: '1px solid var(--border-main)' 
            }}
          >
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}
