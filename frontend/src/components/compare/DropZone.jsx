import { useRef, useState } from 'react';
import { UploadCloud, FileText, Loader2, AlertTriangle, X } from 'lucide-react';
import { formatLabel } from './compareUtils';

const plural = (n, word) => `${Number(n ?? 0).toLocaleString()} ${word}${n === 1 ? '' : 's'}`;

/**
 * One file slot on the Compare screen. Accepts drag-and-drop or a file
 * picker and shows "Detected: <format> · N tables · N measures" once parsed.
 */
export default function DropZone({ label, optional, file, parsed, parsing, error, expectedFormat, onFile, onClear }) {
    const inputRef = useRef(null);
    const [dragging, setDragging] = useState(false);

    const pick = () => inputRef.current?.click();
    const onDrop = (e) => {
        e.preventDefault();
        setDragging(false);
        const f = e.dataTransfer?.files?.[0];
        if (f) onFile(f);
    };

    const mismatch = parsed && expectedFormat && expectedFormat !== 'auto'
        && String(parsed.format).toUpperCase() !== expectedFormat;

    return (
        <div
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            onClick={file ? undefined : pick}
            role={file ? undefined : 'button'}
            tabIndex={file ? undefined : 0}
            onKeyDown={file ? undefined : (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } }}
            aria-label={file ? undefined : `${label}: drop a YAML file or browse`}
            style={{
                flex: '1 1 260px', minWidth: 0, minHeight: 112,
                borderRadius: 10, padding: 16,
                border: file ? '1px solid var(--border-main)' : `2px dashed ${dragging ? 'var(--accent-blue)' : 'var(--border-main)'}`,
                background: dragging ? 'var(--color-accent-faint)' : 'var(--bg-surface)',
                display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: 6,
                cursor: file ? 'default' : 'pointer',
                transition: 'border-color 120ms, background 120ms',
            }}
        >
            <input
                ref={inputRef}
                type="file"
                accept=".yaml,.yml"
                style={{ display: 'none' }}
                onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ''; }}
            />
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-tertiary)' }}>
                {label}{optional && !file ? ' · optional' : ''}
            </div>

            {!file ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <UploadCloud size={20} color="var(--text-tertiary)" />
                    <div>
                        <div style={{ fontSize: 13, color: 'var(--text-primary)' }}>
                            Drop a YAML file here or <span style={{ color: 'var(--accent-blue)', fontWeight: 600 }}>browse</span>
                        </div>
                        <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>
                            {optional ? 'Add a second file to compare, or leave empty to inspect one.' : 'OSI, SML, Snowflake, dbt, Cube and more — detected automatically.'}
                        </div>
                    </div>
                </div>
            ) : (
                <>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <FileText size={16} color="var(--accent-blue)" />
                        <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>{file.name}</span>
                        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, flexShrink: 0 }}>
                            <button type="button" onClick={pick} style={{ fontSize: 12, padding: '3px 10px', borderRadius: 6, border: '1px solid var(--border-main)', background: 'var(--bg-surface)', color: 'var(--text-primary)', cursor: 'pointer' }}>
                                Change
                            </button>
                            {onClear && (
                                <button type="button" onClick={onClear} aria-label={`Remove ${label}`} title="Remove" style={{ padding: '3px 6px', borderRadius: 6, border: '1px solid var(--border-main)', background: 'var(--bg-surface)', color: 'var(--text-tertiary)', cursor: 'pointer', display: 'inline-flex' }}>
                                    <X size={13} />
                                </button>
                            )}
                        </div>
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--text-secondary)', minHeight: 18 }}>
                        {parsing && <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><Loader2 size={12} className="animate-spin" /> Reading file…</span>}
                        {!parsing && error && <span style={{ color: 'var(--color-error)', display: 'inline-flex', alignItems: 'center', gap: 6 }}><AlertTriangle size={12} /> {error}</span>}
                        {!parsing && !error && parsed && (
                            <>Detected: <strong style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{formatLabel(parsed.format)}</strong>
                                {' · '}{plural(parsed.summary?.total_tables, 'table')}
                                {' · '}{plural(parsed.summary?.total_metrics, 'measure')}</>
                        )}
                    </div>
                    {mismatch && (
                        <div style={{ fontSize: 12, color: 'var(--color-warning)', display: 'flex', alignItems: 'center', gap: 6 }}>
                            <AlertTriangle size={12} /> Expected {formatLabel(expectedFormat)}, but this file looks like {formatLabel(parsed.format)}.
                        </div>
                    )}
                </>
            )}
        </div>
    );
}
