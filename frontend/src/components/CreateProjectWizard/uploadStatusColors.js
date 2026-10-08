/**
 * Border/background for a file's "done" (success) or "error" state, using
 * the same --color-success/--color-error tokens everywhere both PBIX
 * upload modes (single-file and multi-file) need to agree on the same
 * treatment -- see uploadStatusUI.jsx for the icon/remove-button
 * counterparts. Returns null for any other status ('uploading', undefined,
 * ...) -- callers keep their own context-specific idle/dragging styling in
 * that case, since a big empty dropzone and a compact list row legitimately
 * look different while idle.
 */
export function uploadStatusColors(status) {
  if (status === 'done') {
    return { border: 'var(--color-success)', background: 'var(--color-success-muted)' };
  }
  if (status === 'error') {
    return { border: 'var(--color-error)', background: null };
  }
  return null;
}
