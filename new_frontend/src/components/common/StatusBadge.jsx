/**
 * StatusBadge — a compact pill badge reflecting state.
 * Props:
 *   status  : 'active' | 'success' | 'warning' | 'error' | 'draft' | 'running' | 'failed' | string
 *   label   : override display text (defaults to capitalised status)
 *   size    : 'sm' | 'md'  (default 'md')
 */
const CONFIG = {
  active:  { bg: 'var(--color-success-muted)',  text: 'var(--color-success)',  dot: 'var(--color-success)',  label: 'Active' },
  success: { bg: 'var(--color-success-muted)',  text: 'var(--color-success)',  dot: 'var(--color-success)',  label: 'Success' },
  warning: { bg: 'var(--color-warning-muted)',  text: 'var(--color-warning)',  dot: 'var(--color-warning)',  label: 'Warning' },
  error:   { bg: 'var(--color-error-muted)',    text: 'var(--color-error)',    dot: 'var(--color-error)',    label: 'Error' },
  failed:  { bg: 'var(--color-error-muted)',    text: 'var(--color-error)',    dot: 'var(--color-error)',    label: 'Failed' },
  draft:   { bg: 'var(--bg-surface-raised)',    text: 'var(--text-tertiary)',  dot: 'var(--text-tertiary)',  label: 'Draft' },
  running: { bg: 'var(--color-warning-muted)',  text: 'var(--color-warning)',  dot: 'var(--color-warning)',  label: 'Running' },
  connected: { bg: 'var(--color-success-muted)', text: 'var(--color-success)', dot: 'var(--color-success)', label: 'Connected' },
  configured: { bg: 'var(--color-warning-muted)', text: 'var(--color-warning)', dot: 'var(--color-warning)', label: 'Configured' },
  disconnected: { bg: 'var(--color-error-muted)', text: 'var(--color-error)', dot: 'var(--color-error)', label: 'Disconnected' },
};

export default function StatusBadge({ status = 'draft', label, size = 'md' }) {
  const key = (status || 'draft').toLowerCase();
  const cfg = CONFIG[key] ?? {
    bg: 'var(--bg-surface-raised)',
    text: 'var(--text-tertiary)',
    dot: 'var(--text-tertiary)',
    label: status,
  };

  const display = label ?? cfg.label;
  const px = size === 'sm' ? '6px 8px' : '4px 10px';
  const fontSize = size === 'sm' ? '10px' : '11px';

  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full font-semibold"
      style={{
        background: cfg.bg,
        color: cfg.text,
        padding: px,
        fontSize,
        lineHeight: '1.4',
        whiteSpace: 'nowrap',
      }}
    >
      <span
        className={key === 'running' ? 'animate-pulse' : ''}
        style={{
          width: 6,
          height: 6,
          borderRadius: '50%',
          background: cfg.dot,
          flexShrink: 0,
        }}
      />
      {display}
    </span>
  );
}
