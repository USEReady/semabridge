/**
 * StatusBadge — a compact pill badge reflecting state.
 * Props:
 *   status  : 'active' | 'success' | 'warning' | 'error' | 'draft' | 'running' | 'failed' | string
 *   label   : override display text (defaults to capitalised status)
 *   size    : 'sm' | 'md'  (default 'md')
 *   wrap    : when true, allows the label to wrap onto multiple lines instead
 *             of forcing single-line nowrap — for long labels (e.g. the
 *             dry-run risk-tier text) rendered inside a fixed-width column,
 *             where nowrap would force the pill wider than its container and
 *             bleed into neighboring content. Default false preserves the
 *             single-line pill everywhere else. (default false)
 */
const CONFIG = {
  active:  { bg: 'var(--color-success-muted)',  text: 'var(--color-success)',  dot: 'var(--color-success)',  label: 'Active' },
  success: { bg: 'var(--color-success-muted)',  text: 'var(--color-success)',  dot: 'var(--color-success)',  label: 'Success' },
  warning: { bg: 'var(--color-warning-muted)',  text: 'var(--color-warning)',  dot: 'var(--color-warning)',  label: 'Warning' },
  error:   { bg: 'var(--color-error-muted)',    text: 'var(--color-error)',    dot: 'var(--color-error)',    label: 'Error' },
  failed:  { bg: 'var(--color-error-muted)',    text: 'var(--color-error)',    dot: 'var(--color-error)',    label: 'Failed' },
  draft:   { bg: 'var(--bg-surface-raised)',    text: 'var(--text-tertiary)',  dot: 'var(--text-tertiary)',  label: 'Draft' },
  running: { bg: 'rgba(59, 130, 246, 0.12)',  text: '#3B82F6',  dot: '#3B82F6',  label: 'Running' },
  connected: { bg: 'var(--color-success-muted)', text: 'var(--color-success)', dot: 'var(--color-success)', label: 'Connected' },
  configured: { bg: 'var(--color-warning-muted)', text: 'var(--color-warning)', dot: 'var(--color-warning)', label: 'Configured' },
  disconnected: { bg: 'var(--color-error-muted)', text: 'var(--color-error)', dot: 'var(--color-error)', label: 'Disconnected' },
};

export default function StatusBadge({ status = 'draft', label, size = 'md', wrap = false }) {
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
      className="inline-flex rounded-full font-semibold"
      style={{
        background: cfg.bg,
        color: cfg.text,
        padding: px,
        fontSize,
        lineHeight: '1.4',
        whiteSpace: wrap ? 'normal' : 'nowrap',
        alignItems: wrap ? 'flex-start' : 'center',
        gap: '6px',
        textAlign: wrap ? 'left' : undefined,
        maxWidth: wrap ? '100%' : undefined,
        boxSizing: 'border-box',
      }}
    >
      {key === 'running' ? (
        <span
          className="relative inline-flex"
          style={{ width: 6, height: 6, flexShrink: 0, marginTop: wrap ? 4 : 0 }}
        >
          <span
            className="absolute inline-flex h-full w-full rounded-full animate-ping"
            style={{ background: cfg.dot, opacity: 0.5 }}
          />
          <span
            className="relative inline-flex h-full w-full rounded-full"
            style={{ background: cfg.dot }}
          />
        </span>
      ) : (
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: '50%',
            background: cfg.dot,
            flexShrink: 0,
            marginTop: wrap ? 4 : 0,
          }}
        />
      )}
      {display}
    </span>
  );
}
