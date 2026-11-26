import React from 'react';
import { parseDiffSegments } from '../../utils/driftLabels';

/**
 * FormattedDiffText Component
 * Renders diff text visually:
 * - strips [ADDED: ...] and [REMOVED: ...] wrapper syntax
 * - renders added text with subtle green highlighting
 * - renders removed text with subtle red strikethrough highlighting
 * - handles (no description) as muted italic text
 */
export default function FormattedDiffText({ text }) {
  if (!text) return null;
  const str = String(text);
  if (str === '(no description)' || str === '(No description)') {
    return <span style={{ color: 'var(--text-tertiary, #6b7280)', fontStyle: 'italic' }}>{str}</span>;
  }

  const segments = parseDiffSegments(str);
  if (segments.length === 0) return null;
  if (segments.length === 1 && segments[0].type === 'text') {
    return <span>{str}</span>;
  }

  return (
    <span>
      {segments.map((seg, idx) => {
        if (seg.type === 'ADDED') {
          return (
            <span
              key={idx}
              style={{
                background: 'rgba(34, 197, 94, 0.18)',
                color: 'var(--color-success, #22c55e)',
                padding: '1px 5px',
                borderRadius: 4,
                fontWeight: 600,
                border: '1px solid rgba(34, 197, 94, 0.35)',
              }}
              title="Added text"
            >
              {seg.text}
            </span>
          );
        }
        if (seg.type === 'REMOVED') {
          return (
            <span
              key={idx}
              style={{
                background: 'rgba(239, 68, 68, 0.18)',
                color: 'var(--color-error, #ef4444)',
                textDecoration: 'line-through',
                padding: '1px 5px',
                borderRadius: 4,
                fontWeight: 600,
                border: '1px solid rgba(239, 68, 68, 0.35)',
              }}
              title="Removed text"
            >
              {seg.text}
            </span>
          );
        }
        return <span key={idx}>{seg.text}</span>;
      })}
    </span>
  );
}
