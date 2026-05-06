import { X, RefreshCcw } from 'lucide-react';
import { useEffect, useState } from 'react';

/**
 * DraftToast — non-intrusive notification in the bottom right.
 * Shown when a project draft has been automatically resumed.
 * 
 * Props:
 *   visible      — whether to render the toast
 *   onStartFresh — called when user clicks "Start Fresh"
 *   onDismiss    — called when user dismisses the toast
 */
export default function DraftToast({ visible, onStartFresh, onDismiss }) {
  const [shouldRender, setShouldRender] = useState(visible);

  useEffect(() => {
    if (visible) {
      setShouldRender(true);
    } else {
      const timer = setTimeout(() => setShouldRender(false), 300);
      return () => clearTimeout(timer);
    }
  }, [visible]);

  if (!shouldRender) return null;

  return (
    <div
      style={{
        position: 'fixed',
        bottom: 24,
        right: 24,
        zIndex: 9999,
        padding: '12px 16px',
        background: '#1c2128',
        border: '1px solid var(--border-main, rgba(255,255,255,0.1))',
        borderRadius: 12,
        boxShadow: '0 8px 24px rgba(0,0,0,0.5)',
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        animation: visible ? 'toastSlideIn 0.4s cubic-bezier(0.16, 1, 0.3, 1)' : 'toastSlideOut 0.3s ease-in forwards',
        backdropFilter: 'blur(8px)',
      }}
    >
      <div style={{
        width: 32,
        height: 32,
        borderRadius: 8,
        background: 'rgba(56, 189, 248, 0.1)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: '#38bdf8'
      }}>
        <RefreshCcw size={16} />
      </div>
      
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: '#fff' }}>Draft restored</span>
        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>You are picking up where you left off.</span>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginLeft: 8 }}>
        <button
          onClick={onStartFresh}
          style={{
            padding: '6px 12px',
            borderRadius: 6,
            background: 'rgba(255,255,255,0.05)',
            border: '1px solid rgba(255,255,255,0.1)',
            color: '#fff',
            fontSize: 12,
            fontWeight: 500,
            cursor: 'pointer',
            transition: 'all 0.2s',
          }}
          onMouseEnter={e => { e.currentTarget.style.background = 'rgba(255,255,255,0.1)'; }}
          onMouseLeave={e => { e.currentTarget.style.background = 'rgba(255,255,255,0.05)'; }}
        >
          Start Fresh
        </button>
        
        <button
          onClick={onDismiss}
          style={{
            background: 'none',
            border: 'none',
            padding: 4,
            cursor: 'pointer',
            color: 'var(--text-tertiary)',
            display: 'flex',
            alignItems: 'center',
            transition: 'color 0.2s',
          }}
          onMouseEnter={e => { e.currentTarget.style.color = '#fff'; }}
          onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-tertiary)'; }}
        >
          <X size={16} />
        </button>
      </div>

      <style>{`
        @keyframes toastSlideIn {
          from { opacity: 0; transform: translateY(20px) scale(0.95); }
          to   { opacity: 1; transform: translateY(0) scale(1); }
        }
        @keyframes toastSlideOut {
          from { opacity: 1; transform: translateY(0) scale(1); }
          to   { opacity: 0; transform: translateY(10px) scale(0.95); }
        }
      `}</style>
    </div>
  );
}
