/**
 * Notification-specific badge components.
 */

import { Slack, Mail, Webhook, AlertCircle, BarChart3 } from 'lucide-react';

// Channel type badges
const CHANNEL_TYPES = {
  slack: { icon: Slack, label: 'Slack', color: '#636e72' },
  email: { icon: Mail, label: 'Email', color: '#0984e3' },
  teams: { icon: AlertCircle, label: 'Teams', color: '#a29bfe' },
  webhook: { icon: Webhook, label: 'Webhook', color: '#fdcb6e' },
  pagerduty: { icon: BarChart3, label: 'PagerDuty', color: '#e17055' },
  snowflake: { icon: Webhook, label: 'Snowflake', color: '#38bdf8' },
};

export function ChannelTypeBadge({ type = 'slack' }) {
  const cfg = CHANNEL_TYPES[type?.toLowerCase()] || CHANNEL_TYPES.webhook;
  const Icon = cfg.icon;

  return (
    <div
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '4px 8px',
        borderRadius: 6,
        background: 'var(--bg-surface-raised)',
        color: cfg.color,
        fontSize: 12,
        fontWeight: 500,
      }}
    >
      <Icon size={14} />
      {cfg.label}
    </div>
  );
}

// Level badges
const LEVEL_CONFIGS = {
  'SYNC_RESULT': { color: '#74b9ff', bg: 'rgba(116, 185, 255, 0.12)' },
  'CRITICAL': { color: '#d63031', bg: 'rgba(214, 48, 49, 0.12)' },
  'ERROR': { color: '#e17055', bg: 'rgba(225, 112, 85, 0.12)' },
  'WARNING': { color: '#fdcb6e', bg: 'rgba(253, 203, 110, 0.12)' },
  'INFO': { color: '#74b9ff', bg: 'rgba(116, 185, 255, 0.12)' },
  'DEBUG': { color: '#a29bfe', bg: 'rgba(162, 155, 254, 0.12)' },
};

export function LevelBadge({ level = 'INFO' }) {
  const cfg = LEVEL_CONFIGS[level] || LEVEL_CONFIGS.INFO;

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: '4px 10px',
        borderRadius: 6,
        background: cfg.bg,
        color: cfg.color,
        fontSize: 11,
        fontWeight: 600,
      }}
    >
      <span
        style={{
          width: 4,
          height: 4,
          borderRadius: '50%',
          background: cfg.color,
          flexShrink: 0,
        }}
      />
      {level}
    </span>
  );
}

// Delivery status badges
const STATUS_CONFIGS = {
  delivered: { color: '#00b894', bg: 'rgba(0, 184, 148, 0.12)' },
  failed: { color: '#d63031', bg: 'rgba(214, 48, 49, 0.12)' },
  retrying: { color: '#fdcb6e', bg: 'rgba(253, 203, 110, 0.12)' },
  dead: { color: '#2d3436', bg: 'rgba(45, 52, 54, 0.12)' },
  'skipped_dedupe': { color: '#636e72', bg: 'rgba(99, 110, 114, 0.12)' },
  'skipped_quiet': { color: '#636e72', bg: 'rgba(99, 110, 114, 0.12)' },
};

export function NotificationStatusBadge({ status = 'delivered' }) {
  const cfg = STATUS_CONFIGS[status] || STATUS_CONFIGS.delivered;

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '4px 10px',
        borderRadius: 6,
        background: cfg.bg,
        color: cfg.color,
        fontSize: 11,
        fontWeight: 600,
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: '50%',
          background: cfg.color,
          flexShrink: 0,
        }}
      />
      {status.replace(/_/g, ' ')}
    </span>
  );
}
