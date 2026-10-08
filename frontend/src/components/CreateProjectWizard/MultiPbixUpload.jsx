/**
 * MultiPbixUpload — multi-file PBIX picker for the source-configuration step.
 *
 * Controlled component: the file list (`entries`) lives in the persisted
 * wizard store (projectWizardStore.pbixFileEntries), so it survives leaving
 * this step and page reloads. Adding files calls `onAddFiles(files)`, which
 * (in CreateProjectPage) records the entries and starts each upload OUTSIDE
 * any React state updater -- an upload still in flight when this component
 * unmounts still lands in the list. `onRemove(entryId)` removes one file
 * (including one that is still uploading; its late result is dropped).
 *
 * Duplicate file names, non-.pbix files and files past the `maxFiles` cap
 * (10, the backend's MAX_PBIX_FILES) are never dropped silently -- the
 * message returned by onAddFiles is shown inline.
 */
import { useCallback, useRef, useState } from 'react';
import { Upload } from 'lucide-react';
import { uploadStatusColors } from './uploadStatusColors';
import { UploadStatusIcon, RemoveFileButton } from './uploadStatusUI';

export default function MultiPbixUpload({ entries = [], onAddFiles, onRemove, maxFiles = 10 }) {
  const [dragging, setDragging] = useState(false);
  const [notice, setNotice] = useState('');
  const inputRef = useRef(null);

  const handleFiles = useCallback((incoming) => {
    const files = Array.from(incoming || []);
    if (files.length === 0) return;
    const message = onAddFiles?.(files) || '';
    setNotice(message);
  }, [onAddFiles]);

  const doneCount = entries.filter((e) => e.status === 'done').length;
  const uploadingCount = entries.filter((e) => e.status === 'uploading').length;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); e.stopPropagation(); setDragging(false); handleFiles(e.dataTransfer.files); }}
        onClick={() => inputRef.current?.click()}
        style={{
          border: `2px dashed ${dragging ? 'var(--accent-blue)' : 'var(--border-main)'}`,
          borderRadius: 10,
          padding: 24,
          textAlign: 'center',
          cursor: 'pointer',
          background: dragging ? 'var(--accent-blue)14' : 'var(--accent-blue)08',
          transition: 'all 0.15s',
        }}
      >
        <Upload size={22} style={{ color: 'var(--text-tertiary)', margin: '0 auto 6px' }} />
        <div style={{ fontSize: 13, color: 'var(--text-secondary)', fontWeight: 500 }}>
          Drag and drop .pbix files here or click to browse
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4 }}>
          Up to {maxFiles} files — each becomes its own independent semantic view
        </div>
        <input
          ref={inputRef}
          type="file"
          accept=".pbix"
          multiple
          style={{ display: 'none' }}
          onChange={(e) => { handleFiles(e.target.files); e.target.value = ''; }}
        />
      </div>

      {notice && (
        <div role="status" style={{ fontSize: 12, color: 'var(--color-warning)', display: 'flex', alignItems: 'flex-start', gap: 8 }}>
          <span style={{ flex: 1 }}>{notice}</span>
          <button
            type="button"
            onClick={() => setNotice('')}
            style={{ background: 'none', border: 'none', color: 'var(--text-tertiary)', cursor: 'pointer', fontSize: 11 }}
          >
            Dismiss
          </button>
        </div>
      )}

      {entries.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
            {doneCount} of {entries.length} file{entries.length !== 1 ? 's' : ''} uploaded
            {uploadingCount > 0 ? ` · ${uploadingCount} uploading…` : ''}
            {entries.length === 1 && doneCount === 1 ? ' · a single file runs as a single-model project' : ''}
          </div>
          {entries.map((e) => {
            const statusStyle = uploadStatusColors(e.status);
            return (
              <div
                key={e.id || e.name}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '6px 10px', borderRadius: 6,
                  background: statusStyle?.background || 'var(--bg-surface-raised)',
                  border: `1px solid ${statusStyle?.border || 'var(--border-subtle)'}`,
                }}
              >
                <UploadStatusIcon status={e.status} size={13} />
                <span title={e.path || e.name} style={{ fontSize: 12, color: 'var(--text-primary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {e.name}
                </span>
                {e.status === 'error' && (
                  <span style={{ fontSize: 11, color: 'var(--color-error)' }}>{e.error}</span>
                )}
                <RemoveFileButton onClick={() => { onRemove?.(e.id); setNotice(''); }} />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
