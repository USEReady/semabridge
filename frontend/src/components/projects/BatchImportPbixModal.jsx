/**
 * BatchImportPbixModal — create one project per uploaded .pbix file in a
 * single request, dropping them all into the same folder so ProjectsPage's
 * existing folder view becomes the single place to see the batch.
 *
 * Props:
 *   open        — boolean
 *   onClose     — function
 *   projects    — existing projects (used to offer "same target as an
 *                 existing project" instead of re-building a full Snowflake
 *                 connection picker here)
 *   folders     — existing folders (for the "add to existing folder" choice)
 *   onImported  — called after a batch completes with the result payload
 */

import { useMemo, useRef, useState } from 'react';
import { X, Upload, CheckCircle2, AlertCircle, FileText, Loader2, FolderPlus } from 'lucide-react';
import { api } from '../../utils/api';

function projectTargets(project) {
  if (Array.isArray(project?.targets) && project.targets.length > 0) return project.targets;
  if (project?.target && typeof project.target === 'object') return [project.target];
  return [];
}

export default function BatchImportPbixModal({ open, onClose, projects = [], folders = [], onImported }) {
  const [files, setFiles] = useState([]);
  const [folderChoice, setFolderChoice] = useState('__new__');
  const [newFolderName, setNewFolderName] = useState('');
  const [sourceProjectId, setSourceProjectId] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [results, setResults] = useState(null);
  const [error, setError] = useState('');
  const inputRef = useRef(null);

  const snowflakeProjects = useMemo(
    () => (projects || []).filter(p => projectTargets(p).some(t => String(t?.type || '').toLowerCase() === 'snowflake')),
    [projects],
  );

  const handleFiles = (incoming) => {
    const accepted = Array.from(incoming || []).filter(f => f.name.toLowerCase().endsWith('.pbix'));
    setFiles(prev => {
      const names = new Set(prev.map(f => f.name));
      return [...prev, ...accepted.filter(f => !names.has(f.name))];
    });
    setError('');
  };

  const handleDrop = (e) => {
    e.preventDefault();
    handleFiles(e.dataTransfer.files);
  };

  const reset = () => {
    setFiles([]);
    setFolderChoice('__new__');
    setNewFolderName('');
    setSourceProjectId('');
    setResults(null);
    setError('');
  };

  const handleClose = () => {
    reset();
    onClose?.();
  };

  const handleSubmit = async () => {
    if (files.length === 0) return;
    if (!sourceProjectId) {
      setError('Pick an existing project to copy the Snowflake target from.');
      return;
    }

    setSubmitting(true);
    setError('');
    try {
      let folderId = null;
      if (folderChoice === '__new__') {
        if (newFolderName.trim()) {
          const folder = await api.createFolder({ name: newFolderName.trim() });
          folderId = folder?.id ?? null;
        }
      } else if (folderChoice !== '__none__') {
        folderId = folderChoice;
      }

      const sourceProject = snowflakeProjects.find(p => String(p.id) === String(sourceProjectId));
      const targets = projectTargets(sourceProject);

      const data = await api.batchImportPbix(files, { targets, folderId });
      setResults(data?.results ?? []);
      onImported?.(data);
    } catch (err) {
      setResults([{ filename: 'Request failed', status: 'error', error: err.message }]);
    } finally {
      setSubmitting(false);
    }
  };

  if (!open) return null;

  const successCount = results?.filter(r => r.status === 'success').length ?? 0;

  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 1000,
        background: 'var(--bg-backdrop)', backdropFilter: 'blur(4px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24,
      }}
      onClick={e => e.target === e.currentTarget && handleClose()}
    >
      <div
        style={{
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-main)',
          borderRadius: 12,
          width: '100%', maxWidth: 640,
          maxHeight: '85vh',
          display: 'flex', flexDirection: 'column',
          overflow: 'hidden',
          boxShadow: '0 16px 48px rgba(0,0,0,0.22)',
        }}
      >
        {/* Header */}
        <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border-main)', display: 'flex', alignItems: 'center' }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)' }}>Batch Import PBIX Files</div>
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 2 }}>
              Creates one project per file, all pointed at the same Snowflake target
            </div>
          </div>
          <button onClick={handleClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)' }}>
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflow: 'auto', padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
          {!results && (
            <>
              {/* Drop zone */}
              <div
                onDragOver={e => e.preventDefault()}
                onDrop={handleDrop}
                onClick={() => inputRef.current?.click()}
                style={{
                  border: '2px dashed var(--border-main)',
                  borderRadius: 10,
                  padding: 28,
                  textAlign: 'center',
                  cursor: 'pointer',
                }}
              >
                <Upload size={26} style={{ color: 'var(--text-tertiary)', margin: '0 auto 8px' }} />
                <div style={{ fontSize: 14, color: 'var(--text-secondary)', fontWeight: 500 }}>
                  Drop .pbix files here or click to browse
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4 }}>
                  Multiple files supported — one project will be created per file
                </div>
                <input
                  ref={inputRef}
                  type="file"
                  accept=".pbix"
                  multiple
                  style={{ display: 'none' }}
                  onChange={e => handleFiles(e.target.files)}
                />
              </div>

              {files.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {files.map(f => (
                    <div
                      key={f.name}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 8,
                        padding: '6px 10px', borderRadius: 6,
                        background: 'var(--bg-surface-raised)',
                        border: '1px solid var(--border-subtle)',
                      }}
                    >
                      <FileText size={14} style={{ color: 'var(--text-tertiary)', flexShrink: 0 }} />
                      <span style={{ fontSize: 13, color: 'var(--text-primary)', flex: 1 }}>{f.name}</span>
                      <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                        {(f.size / 1024 / 1024).toFixed(2)} MB
                      </span>
                      <button
                        onClick={e => { e.stopPropagation(); setFiles(prev => prev.filter(x => x.name !== f.name)); }}
                        style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: 2 }}
                      >
                        <X size={12} />
                      </button>
                    </div>
                  ))}
                </div>
              )}

              {/* Snowflake target source */}
              <div>
                <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', display: 'block', marginBottom: 6 }}>
                  Snowflake target — copy from an existing project
                </label>
                <select
                  value={sourceProjectId}
                  onChange={e => setSourceProjectId(e.target.value)}
                  style={{
                    width: '100%', padding: '8px 10px', borderRadius: 7,
                    border: '1px solid var(--border-main)', background: 'var(--bg-surface-raised)',
                    color: 'var(--text-primary)', fontSize: 13,
                  }}
                >
                  <option value="">Select a project's Snowflake target…</option>
                  {snowflakeProjects.map(p => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
                {snowflakeProjects.length === 0 && (
                  <div style={{ fontSize: 11, color: 'var(--color-warning)', marginTop: 4 }}>
                    No existing project has a Snowflake target yet — create one project manually first,
                    then batch-import the rest against it.
                  </div>
                )}
              </div>

              {/* Folder */}
              <div>
                <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', display: 'block', marginBottom: 6 }}>
                  Group these projects in a folder
                </label>
                <select
                  value={folderChoice}
                  onChange={e => setFolderChoice(e.target.value)}
                  style={{
                    width: '100%', padding: '8px 10px', borderRadius: 7,
                    border: '1px solid var(--border-main)', background: 'var(--bg-surface-raised)',
                    color: 'var(--text-primary)', fontSize: 13, marginBottom: folderChoice === '__new__' ? 8 : 0,
                  }}
                >
                  <option value="__new__">Create new folder…</option>
                  <option value="__none__">Don't group (no folder)</option>
                  {folders.map(f => (
                    <option key={f.id} value={f.id}>{f.name}</option>
                  ))}
                </select>
                {folderChoice === '__new__' && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <FolderPlus size={13} style={{ color: 'var(--text-tertiary)' }} />
                    <input
                      type="text"
                      value={newFolderName}
                      onChange={e => setNewFolderName(e.target.value)}
                      placeholder="e.g. Q3 Migration"
                      style={{
                        flex: 1, padding: '7px 10px', borderRadius: 7,
                        border: '1px solid var(--border-main)', background: 'var(--bg-surface-raised)',
                        color: 'var(--text-primary)', fontSize: 13,
                      }}
                    />
                  </div>
                )}
              </div>

              {error && (
                <div style={{ fontSize: 12, color: 'var(--color-error)' }}>{error}</div>
              )}
            </>
          )}

          {/* Results */}
          {results && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 4 }}>
                {successCount} project{successCount !== 1 ? 's' : ''} created, {results.length - successCount} failed
              </div>
              {results.map((r, i) => (
                <div
                  key={`${r.filename}-${i}`}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderRadius: 8,
                    border: `1px solid ${r.status === 'success' ? 'var(--color-success)30' : 'var(--color-error)30'}`,
                    background: r.status === 'success' ? 'var(--color-success)08' : 'var(--color-error)08',
                  }}
                >
                  {r.status === 'success'
                    ? <CheckCircle2 size={14} style={{ color: 'var(--color-success)' }} />
                    : <AlertCircle size={14} style={{ color: 'var(--color-error)' }} />}
                  <span style={{ fontSize: 13, color: 'var(--text-primary)', flex: 1 }}>{r.filename}</span>
                  <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                    {r.status === 'success' ? (r.project_name || r.project_id) : (r.error || 'Failed')}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{ padding: '12px 20px', borderTop: '1px solid var(--border-main)', display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          {!results ? (
            <>
              <button
                onClick={handleClose}
                style={{
                  padding: '7px 16px', borderRadius: 7, background: 'transparent',
                  border: '1px solid var(--border-main)', color: 'var(--text-secondary)',
                  fontSize: 13, cursor: 'pointer',
                }}
              >
                Cancel
              </button>
              <button
                onClick={handleSubmit}
                disabled={files.length === 0 || submitting}
                style={{
                  padding: '7px 16px', borderRadius: 7, background: 'var(--accent-blue)', border: 'none',
                  color: '#fff', fontSize: 13, fontWeight: 600,
                  cursor: files.length === 0 || submitting ? 'not-allowed' : 'pointer',
                  opacity: files.length === 0 ? 0.5 : 1,
                  display: 'flex', alignItems: 'center', gap: 6,
                }}
              >
                {submitting && <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} />}
                Create {files.length > 0 ? `${files.length} Project${files.length !== 1 ? 's' : ''}` : 'Projects'}
              </button>
            </>
          ) : (
            <button
              onClick={handleClose}
              style={{
                padding: '7px 16px', borderRadius: 7, background: 'var(--accent-blue)', border: 'none',
                color: '#fff', fontSize: 13, fontWeight: 600, cursor: 'pointer',
              }}
            >
              Done
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
