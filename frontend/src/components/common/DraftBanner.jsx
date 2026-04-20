import { useState } from 'react';
import { X, FileText } from 'lucide-react';

/**
 * DraftBanner — non-intrusive banner shown when an unsaved draft exists.
 *
 * Props:
 *   visible  — whether to render the banner
 *   onResume — called when user clicks "Resume"
 *   onDiscard — called when user clicks the dismiss (✕) button
 */
export default function DraftBanner({ visible, onResume, onDiscard }) {
  const [dismissed, setDismissed] = useState(false);

  if (!visible || dismissed) return null;

  const handleDiscard = () => {
    setDismissed(true);
    onDiscard?.();
  };

  return (
    <div
      style={{
        margin: '16px 32px 0',
        padding: '10px 16px',
        borderRadius: 8,
        background: 'var(--color-accent-faint, rgba(88,166,255,0.08))',
        border: '1.5px solid var(--accent-blue, #58a6ff)40',
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        animation: 'draftBannerSlideIn 0.3s ease-out',
      }}
    >
      <FileText size={16} style={{ color: 'var(--accent-blue)', flexShrink: 0 }} />
      <span style={{ flex: 1, fontSize: 13, color: 'var(--text-secondary)' }}>
        You have an unsaved project draft.
      </span>
      <button
        onClick={onResume}
        style={{
          padding: '5px 14px',
          borderRadius: 6,
          background: 'var(--accent-blue)',
          color: '#fff',
          border: 'none',
          fontSize: 12,
          fontWeight: 600,
          cursor: 'pointer',
          transition: 'opacity 0.15s',
        }}
        onMouseEnter={e => { e.currentTarget.style.opacity = '0.85'; }}
        onMouseLeave={e => { e.currentTarget.style.opacity = '1'; }}
      >
        Resume
      </button>
      <button
        onClick={handleDiscard}
        title="Discard draft"
        style={{
          background: 'none',
          border: 'none',
          cursor: 'pointer',
          color: 'var(--text-tertiary)',
          display: 'flex',
          alignItems: 'center',
          padding: 2,
          transition: 'color 0.15s',
        }}
        onMouseEnter={e => { e.currentTarget.style.color = 'var(--color-error, #f85149)'; }}
        onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-tertiary)'; }}
      >
        <X size={16} />
      </button>

      {/* Inline keyframe for the slide-in animation */}
      <style>{`
        @keyframes draftBannerSlideIn {
          from { opacity: 0; transform: translateY(-8px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}
