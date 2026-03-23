/**
 * ImportProjectModal — Drag-and-drop multi-file YAML import with per-file validation display.
 *
 * Props:
 *   open     — boolean
 *   onClose  — function
 *   onImported — called after successful import(s) with count
 */

import { useState, useRef, useCallback } from 'react';
import {
  X, Upload, CheckCircle2, AlertCircle, FileText,
  ChevronDown, ChevronRight, Loader2,
} from 'lucide-react';
import { api } from '../../utils/api';

export default function ImportProjectModal({ open, onClose, onImported }) {
  const [files, setFiles] = useState([]);
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [expandedErrors, setExpandedErrors] = useState({});
  const inputRef = useRef(null);

  const handleFiles = useCallback((incoming) => {
    const accepted = Array.from(incoming).filter(f => f.name.endsWith('.yaml') || f.name.endsWith('.yml'));
    setFiles(prev => {
      const names = new Set(prev.map(f => f.name));
      return [...prev, ...accepted.filter(f => !names.has(f.name))];
    });
    setResults(null);
  }, []);

  const handleDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    handleFiles(e.dataTransfer.files);
  };

  const handleValidate = async () => {
    if (files.length === 0) return;
    setLoading(true);
    try {
      const data = await api.importProjects(files);
      setResults(data.results ?? []);
    } catch (err) {
      setResults([{ filename: 'Request failed', status: 'error', errors: [{ msg: err.message }] }]);
    } finally {
      setLoading(false);
    }
  };

  const validCount = results?.filter(r => r.status === 'valid').length ?? 0;

  const handleImportValid = () => {
    onImported?.(validCount);
    onClose();
    setFiles([]);
    setResults(null);
  };

  const toggleErrors = (filename) => {
    setExpandedErrors(prev => ({ ...prev, [filename]: !prev[filename] }));
  };

  if (!open) return null;

  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 1000,
        background: 'var(--bg-backdrop)', backdropFilter: 'blur(4px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24,
      }}
      onClick={e => e.target === e.currentTarget && onClose()}
    >
      <div
        style={{
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-main)',
          borderRadius: 12,
          width: '100%', maxWidth: 640,
          maxHeight: '80vh',
          display: 'flex', flexDirection: 'column',
          overflow: 'hidden',
          boxShadow: '0 16px 48px rgba(0,0,0,0.22)',
        }}
      >
        {/* Header */}
        <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border-main)', display: 'flex', alignItems: 'center' }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)' }}>Import Projects</div>
            <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 2 }}>
              Upload one or more semabridge.yaml files
            </div>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)' }}>
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflow: 'auto', padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Drop zone */}
          {!results && (
            <div
              onDragOver={e => { e.preventDefault(); setDragging(true); }}
              onDragLeave={() => setDragging(false)}
              onDrop={handleDrop}
              onClick={() => inputRef.current?.click()}
              style={{
                border: `2px dashed ${dragging ? 'var(--accent-blue)' : 'var(--border-main)'}`,
                borderRadius: 10,
                padding: 32,
                textAlign: 'center',
                cursor: 'pointer',
                background: dragging ? 'var(--accent-blue)08' : 'transparent',
                transition: 'all 0.15s',
              }}
            >
              <Upload size={28} style={{ color: 'var(--text-tertiary)', margin: '0 auto 8px' }} />
              <div style={{ fontSize: 14, color: 'var(--text-secondary)', fontWeight: 500 }}>
                Drop .yaml files here or click to browse
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4 }}>
                Multiple files supported • .yaml and .yml
              </div>
              <input
                ref={inputRef}
                type="file"
                accept=".yaml,.yml"
                multiple
                style={{ display: 'none' }}
                onChange={e => handleFiles(e.target.files)}
              />
            </div>
          )}

          {/* Selected files list */}
          {files.length > 0 && !results && (
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
                    {(f.size / 1024).toFixed(1)} KB
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

          {/* Validation results */}
          {results && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 4 }}>
                Validation Results — {validCount} valid, {results.length - validCount} failed
              </div>
              {results.map(r => (
                <div
                  key={r.filename}
                  style={{
                    borderRadius: 8,
                    border: `1px solid ${r.status === 'valid' ? 'var(--color-success)30' : 'var(--color-error)30'}`,
                    background: r.status === 'valid' ? 'var(--color-success)08' : 'var(--color-error)08',
                    overflow: 'hidden',
                  }}
                >
                  <div
                    style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', cursor: r.errors?.length ? 'pointer' : 'default' }}
                    onClick={() => r.errors?.length && toggleErrors(r.filename)}
                  >
                    {r.status === 'valid'
                      ? <CheckCircle2 size={14} style={{ color: 'var(--color-success)' }} />
                      : <AlertCircle size={14} style={{ color: 'var(--color-error)' }} />}
                    <span style={{ fontSize: 13, color: 'var(--text-primary)', flex: 1 }}>
                      {r.filename}
                    </span>
                    {r.project_name && (
                      <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{r.project_name}</span>
                    )}
                    {r.errors?.length > 0 && (
                      expandedErrors[r.filename]
                        ? <ChevronDown size={13} style={{ color: 'var(--text-tertiary)' }} />
                        : <ChevronRight size={13} style={{ color: 'var(--text-tertiary)' }} />
                    )}
                  </div>

                  {/* Error details */}
                  {expandedErrors[r.filename] && r.errors?.length > 0 && (
                    <div style={{ padding: '0 12px 10px' }}>
                      {r.errors.map((err, i) => (
                        <div key={i} style={{ fontSize: 11, color: 'var(--color-error)', marginTop: 4, fontFamily: 'monospace' }}>
                          {err.loc ? `[${err.loc.join(' → ')}] ` : ''}{err.msg}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{ padding: '12px 20px', borderTop: '1px solid var(--border-main)', display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button
            onClick={onClose}
            style={{
              padding: '7px 16px', borderRadius: 7,
              background: 'transparent',
              border: '1px solid var(--border-main)',
              color: 'var(--text-secondary)', fontSize: 13, cursor: 'pointer',
            }}
          >
            Cancel
          </button>

          {!results ? (
            <button
              onClick={handleValidate}
              disabled={files.length === 0 || loading}
              style={{
                padding: '7px 16px', borderRadius: 7,
                background: 'var(--accent-blue)', border: 'none',
                color: '#fff', fontSize: 13, fontWeight: 600,
                cursor: files.length === 0 || loading ? 'not-allowed' : 'pointer',
                opacity: files.length === 0 ? 0.5 : 1,
                display: 'flex', alignItems: 'center', gap: 6,
              }}
            >
              {loading && <Loader2 size={13} style={{ animation: 'spin 1s linear infinite' }} />}
              Validate {files.length > 0 ? `(${files.length})` : ''}
            </button>
          ) : (
            <>
              <button
                onClick={() => { setResults(null); }}
                style={{
                  padding: '7px 16px', borderRadius: 7,
                  background: 'transparent',
                  border: '1px solid var(--border-main)',
                  color: 'var(--text-secondary)', fontSize: 13, cursor: 'pointer',
                }}
              >
                Back
              </button>
              <button
                onClick={handleImportValid}
                disabled={validCount === 0}
                style={{
                  padding: '7px 16px', borderRadius: 7,
                  background: 'var(--accent-blue)', border: 'none',
                  color: '#fff', fontSize: 13, fontWeight: 600,
                  cursor: validCount === 0 ? 'not-allowed' : 'pointer',
                  opacity: validCount === 0 ? 0.4 : 1,
                }}
              >
                Import {validCount} Valid Project{validCount !== 1 ? 's' : ''}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
