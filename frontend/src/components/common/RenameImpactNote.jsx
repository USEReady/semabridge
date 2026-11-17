import { Info } from 'lucide-react';

/** What a rename does and does not change (see utils/nameChanges.renameImpactNote). */
export default function RenameImpactNote({ text }) {
  if (!text) return null;
  return (
    <div
      role="note"
      style={{
        display: 'flex', gap: 6, fontSize: 11, lineHeight: 1.5, color: 'var(--text-secondary)',
        padding: '6px 8px', borderRadius: 6, background: 'var(--bg-surface-raised)', marginTop: 6,
      }}
    >
      <Info size={12} style={{ flexShrink: 0, marginTop: 2 }} />
      <span>{text}</span>
    </div>
  );
}
