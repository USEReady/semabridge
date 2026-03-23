import { X } from 'lucide-react';
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
 */
export default function Modal({ open, onClose, title, size = 'md', children, footer }) {
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
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'var(--bg-backdrop)', backdropFilter: 'blur(4px)' }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose?.(); }}
    >
      <div
        className="w-full rounded-2xl flex flex-col overflow-hidden"
        style={{
          maxWidth: maxW,
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-main)',
          boxShadow: 'var(--shadow-lg)',
          maxHeight: 'min(90vh, 800px)',
        }}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between px-6 py-4 flex-shrink-0"
          style={{ borderBottom: '1px solid var(--border-main)' }}
        >
          <h2 className="text-primary font-semibold text-base" style={{ margin: 0 }}>
            {title}
          </h2>
          <button
            onClick={onClose}
            className="flex items-center justify-center rounded-lg transition-colors"
            style={{
              width: 28,
              height: 28,
              color: 'var(--text-tertiary)',
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
            }}
            onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-surface-hover)'; e.currentTarget.style.color = 'var(--text-primary)'; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-tertiary)'; }}
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-6 custom-scrollbar">
          {children}
        </div>

        {/* Footer */}
        {footer && (
          <div
            className="flex items-center justify-end gap-3 px-6 py-4 flex-shrink-0"
            style={{ borderTop: '1px solid var(--border-main)' }}
          >
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}
