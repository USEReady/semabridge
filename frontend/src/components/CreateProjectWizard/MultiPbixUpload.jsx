/**
 * MultiPbixUpload — multi-file PBIX picker for the source-configuration step.
 *
 * Reuses ImportProjectModal.jsx's exact multi-file collection pattern (drag
 * + drop, hidden multi <input>, dedupe by filename, per-file remove button)
 * rather than inventing a new one — only the accept-type (.pbix instead of
 * .yaml/.yml) and the per-file action (direct upload via api.uploadPbix
 * instead of a separate validate step) differ.
 *
 * Each file is uploaded as soon as it's added; onFilesChange is called with
 * the current list of successfully-uploaded absolute paths every time it
 * changes, so the parent (CreateProjectPage.jsx) only ever needs to store
 * `source.models` as a flat array of paths — never raw File objects.
 *
 * Capped at maxFiles (10, matching the backend's MAX_BATCH_MODELS /
 * MAX_PBIX_FILES cap) — selecting past the cap is blocked client-side with
 * an inline message rather than silently truncating.
 */
import { useCallback, useRef, useState } from 'react';
import { Upload, FileText } from 'lucide-react';
import { api } from '../../utils/api';
import { uploadStatusColors } from './uploadStatusColors';
import { UploadStatusIcon, RemoveFileButton } from './uploadStatusUI';

export default function MultiPbixUpload({ onFilesChange, maxFiles = 10 }) {
  const [entries, setEntries] = useState([]); // [{ name, size, path, status, error }]
  const [dragging, setDragging] = useState(false);
  const [capError, setCapError] = useState('');
  const inputRef = useRef(null);

  const emitPaths = useCallback((list) => {
    onFilesChange?.(list.filter((e) => e.status === 'done').map((e) => e.path));
  }, [onFilesChange]);

  const uploadOne = useCallback(async (file) => {
    try {
      const response = await api.uploadPbix(file);
      const path = String(response?.path || '').trim();
      setEntries((prev) => {
        const next = prev.map((e) => (
          e.name === file.name
            ? { ...e, status: path ? 'done' : 'error', path, error: path ? '' : 'Upload succeeded but no path was returned.' }
            : e
        ));
        emitPaths(next);
        return next;
      });
    } catch (err) {
      setEntries((prev) => {
        const next = prev.map((e) => (
          e.name === file.name ? { ...e, status: 'error', error: err?.message || 'Upload failed.' } : e
        ));
        emitPaths(next);
        return next;
      });
    }
  }, [emitPaths]);

  const handleFiles = useCallback((incoming) => {
    const accepted = Array.from(incoming).filter((f) => f.name.toLowerCase().endsWith('.pbix'));
    setEntries((prev) => {
      const existingNames = new Set(prev.map((e) => e.name));
      const fresh = accepted.filter((f) => !existingNames.has(f.name));
      if (prev.length + fresh.length > maxFiles) {
        setCapError(`Up to ${maxFiles} PBIX files are supported per project.`);
        const room = Math.max(0, maxFiles - prev.length);
        const toAdd = fresh.slice(0, room);
        toAdd.forEach((f) => uploadOne(f));
        return [
          ...prev,
          ...toAdd.map((f) => ({ name: f.name, size: f.size, path: '', status: 'uploading', error: '' })),
        ];
      }
      setCapError('');
      fresh.forEach((f) => uploadOne(f));
      return [
        ...prev,
        ...fresh.map((f) => ({ name: f.name, size: f.size, path: '', status: 'uploading', error: '' })),
      ];
    });
  }, [maxFiles, uploadOne]);

  const removeFile = useCallback((name) => {
    setEntries((prev) => {
      const next = prev.filter((e) => e.name !== name);
      emitPaths(next);
      return next;
    });
    setCapError('');
  }, [emitPaths]);

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
          background: dragging ? 'var(--accent-blue)08' : 'var(--accent-blue)08',
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

      {capError && (
        <div style={{ fontSize: 12, color: 'var(--color-warning)' }}>{capError}</div>
      )}

      {entries.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {entries.map((e) => {
            const statusStyle = uploadStatusColors(e.status);
            return (
            <div
              key={e.name}
              style={{
                display: 'flex', alignItems: 'center', gap: 8,
                padding: '6px 10px', borderRadius: 6,
                background: statusStyle?.background || 'var(--bg-surface-raised)',
                border: `1px solid ${statusStyle?.border || 'var(--border-subtle)'}`,
              }}
            >
              <UploadStatusIcon status={e.status} size={13} />
              {e.status === 'uploading' ? null : <FileText size={13} style={{ color: 'var(--text-tertiary)', flexShrink: 0, display: e.status === 'error' || e.status === 'done' ? 'none' : 'block' }} />}
              <span style={{ fontSize: 12, color: 'var(--text-primary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {e.name}
              </span>
              {e.status === 'error' && (
                <span style={{ fontSize: 11, color: 'var(--color-error)' }}>{e.error}</span>
              )}
              <RemoveFileButton onClick={() => removeFile(e.name)} />
            </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
