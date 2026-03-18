/**
 * EmptyState — dashed-border placeholder card shown when a list is empty.
 * Props:
 *   icon        : React element (icon component, e.g. <FolderOpen size={32} />)
 *   title       : string
 *   description : string
 *   actionLabel : string
 *   onAction    : () => void
 */
export default function EmptyState({ icon, title, description, actionLabel, onAction }) {
  return (
    <div
      className="flex flex-col items-center justify-center text-center rounded-xl cursor-pointer"
      style={{
        border: '2px dashed var(--border-main)',
        padding: '48px 32px',
        background: 'transparent',
        transition: 'border-color 0.2s',
      }}
      onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--accent-blue)'; }}
      onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border-main)'; }}
      onClick={onAction}
    >
      {icon && (
        <div
          className="flex items-center justify-center rounded-xl mb-4"
          style={{
            width: 56,
            height: 56,
            background: 'var(--color-accent-faint)',
            color: 'var(--accent-blue)',
          }}
        >
          {icon}
        </div>
      )}
      {title && (
        <p className="font-semibold text-primary text-sm mb-1">{title}</p>
      )}
      {description && (
        <p className="text-tertiary text-xs mb-4" style={{ maxWidth: 260 }}>{description}</p>
      )}
      {actionLabel && (
        <button
          onClick={(e) => { e.stopPropagation(); onAction?.(); }}
          className="rounded-lg text-sm font-medium px-4 py-2 theme-transition"
          style={{
            background: 'var(--accent-blue)',
            color: '#fff',
            border: 'none',
            cursor: 'pointer',
          }}
        >
          {actionLabel}
        </button>
      )}
    </div>
  );
}
