/**
 * Shared "what does an uploading/succeeded/failed PBIX file look like"
 * components -- the status icon and the remove ("X") button, used by BOTH
 * the single-file dropzone (StepConnectorConfig.jsx) and the multi-file
 * list (MultiPbixUpload.jsx). See uploadStatusColors.js for the shared
 * success/error color treatment (kept in its own file, not here, purely so
 * this file only exports components -- required for Vite fast refresh).
 *
 * Extracted after the same single-vs-multi visual drift happened twice:
 * first the green "uploaded" treatment existed in one mode but not the
 * other, then the remove button did too. Each mode still owns its own
 * idle/dragging layout (a big dropzone panel vs. a compact list row) --
 * only the parts both modes should always agree on live here.
 */
import { CheckCircle2, AlertCircle, Loader2, X } from 'lucide-react';

export function UploadStatusIcon({ status, size = 14 }) {
  if (status === 'uploading') {
    return <Loader2 size={size} style={{ color: 'var(--text-tertiary)', animation: 'spin 1s linear infinite', flexShrink: 0 }} />;
  }
  if (status === 'done') {
    return <CheckCircle2 size={size} style={{ color: 'var(--color-success)', flexShrink: 0 }} />;
  }
  if (status === 'error') {
    return <AlertCircle size={size} style={{ color: 'var(--color-error)', flexShrink: 0 }} />;
  }
  return null;
}

/**
 * Clears an uploaded/uploading/errored file. `preventDefault` + `stopPropagation`
 * matter here specifically because this button is used inside both a
 * clickable `<div onClick>` row (multi-file) AND a native `<label>` wrapping
 * a hidden file `<input>` (single-file) -- without preventDefault, clicking
 * X inside the label would also trigger the label's default "activate the
 * associated file input" behavior and reopen the file picker.
 */
export function RemoveFileButton({ onClick, size = 12, title = 'Remove file' }) {
  return (
    <button
      type="button"
      onClick={(evt) => {
        evt.preventDefault();
        evt.stopPropagation();
        onClick?.();
      }}
      title={title}
      style={{
        background: 'none', border: 'none', cursor: 'pointer',
        color: 'var(--text-tertiary)', padding: 2, flexShrink: 0,
        display: 'inline-flex', alignItems: 'center',
      }}
    >
      <X size={size} />
    </button>
  );
}
