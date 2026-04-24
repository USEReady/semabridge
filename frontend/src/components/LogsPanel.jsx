import { Fragment, useMemo, useState } from 'react';
import {
    X,
    AlertTriangle,
    AlertCircle,
    Info,
    Check,
    ChevronDown,
    ChevronUp,
    Copy,
    Trash2,
    ChevronLeft,
    ChevronRight,
    Terminal,
    Search,
    History,
} from 'lucide-react';
import { useLogs } from '../context/LogsContext';

const PAGE_SIZE = 25;

const SEVERITY_META = {
    info: {
        label: 'Info',
        icon: Info,
        text: 'var(--accent-blue)',
        dot: 'var(--accent-blue)',
        bg: 'var(--color-accent-faint)',
        border: 'var(--border-main)',
    },
    warning: {
        label: 'Warning',
        icon: AlertTriangle,
        text: 'var(--color-warning)',
        dot: 'var(--color-warning)',
        bg: 'var(--color-warning-bg)',
        border: 'var(--color-warning)',
    },
    error: {
        label: 'Error',
        icon: AlertCircle,
        text: 'var(--color-error)',
        dot: 'var(--color-error)',
        bg: 'var(--color-error-bg)',
        border: 'var(--color-error)',
    },
};

function normalizeSeverity(value) {
    const v = String(value || 'info').toLowerCase();
    if (v === 'success') return 'info';
    if (v === 'critical') return 'error';
    if (v === 'warn') return 'warning';
    return SEVERITY_META[v] ? v : 'info';
}

function parseSearchQuery(query) {
    const tokens = query.trim().split(/\s+/).filter(Boolean);
    const keyed = {};
    const freeText = [];

    for (const token of tokens) {
        const idx = token.indexOf(':');
        if (idx > 0) {
            const key = token.slice(0, idx).toLowerCase();
            const value = token.slice(idx + 1).toLowerCase();
            if (value) keyed[key] = value;
        } else {
            freeText.push(token.toLowerCase());
        }
    }

    return { keyed, freeText };
}

function renderJsonSyntax(value, compact) {
    const jsonString = compact ? JSON.stringify(value) : JSON.stringify(value, null, 2);
    const lines = jsonString.split('\n');

    return lines.map((line, idx) => {
        const parts = [];
        const regex = /("(?:\\.|[^"])*")\s*:|(\btrue\b|\bfalse\b|\bnull\b)|(-?\d+(?:\.\d+)?)|("(?:\\.|[^"])*")/g;
        let last = 0;
        let match;

        while ((match = regex.exec(line)) !== null) {
            if (match.index > last) {
                parts.push(<span key={`t-${idx}-${last}`}>{line.slice(last, match.index)}</span>);
            }

            if (match[1]) {
                parts.push(<span key={`k-${idx}-${match.index}`} className="text-amber-300">{match[1]}</span>);
            } else if (match[2]) {
                parts.push(<span key={`b-${idx}-${match.index}`} className="text-purple-300">{match[2]}</span>);
            } else if (match[3]) {
                parts.push(<span key={`n-${idx}-${match.index}`} className="text-sky-300">{match[3]}</span>);
            } else if (match[4]) {
                parts.push(<span key={`s-${idx}-${match.index}`} className="text-emerald-300">{match[4]}</span>);
            }

            last = regex.lastIndex;
        }

        if (last < line.length) {
            parts.push(<span key={`r-${idx}-${last}`}>{line.slice(last)}</span>);
        }

        return <div key={`line-${idx}`} className="whitespace-pre">{parts}</div>;
    });
}

function buildOriginBreadcrumb(source) {
    const raw = String(source || 'system');
    const segments = raw
        .split(/[\\/]/)
        .flatMap((s) => s.split('>'))
        .map((s) => s.trim())
        .filter(Boolean);

    if (segments.length <= 2) return raw;
    return `...${segments[segments.length - 2]} > ${segments[segments.length - 1]}`;
}

function GeometricSeverityPill({ severity }) {
    const meta = SEVERITY_META[severity] || SEVERITY_META.info;
    const Icon = meta.icon;

    return (
        <span
            className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[10px] font-black uppercase tracking-tight"
            style={{
                background: meta.bg,
                color: meta.text,
                border: `1px solid ${meta.border}`,
            }}
        >
            <span className="h-1.5 w-1.5 rounded-full" style={{ background: meta.dot, boxShadow: `0 0 8px ${meta.dot}` }} />
            <Icon size={11} strokeWidth={2} />
            {meta.label}
        </span>
    );
}

export default function LogsPanel({ isOpen, onClose }) {
    const { logs, clearLogs, wsConnected, addLog } = useLogs();
    const [activeFilter, setActiveFilter] = useState('all');
    const [searchQuery, setSearchQuery] = useState('');
    const [expandedId, setExpandedId] = useState(null);
    const [compactJson, setCompactJson] = useState(false);
    const [copiedEntryId, setCopiedEntryId] = useState(null);
    const [page, setPage] = useState(1);

    const filteredLogs = useMemo(() => {
        const { keyed, freeText } = parseSearchQuery(searchQuery);

        return logs.filter((entry) => {
            const severity = normalizeSeverity(entry.severity);
            const source = String(entry.source || '').toLowerCase();
            const message = String(entry.message || '').toLowerCase();
            const timestamp = String(entry.timestamp || '').toLowerCase();

            if (activeFilter !== 'all' && severity !== activeFilter) return false;
            if (keyed.source && !source.includes(keyed.source)) return false;
            if (keyed.severity && !severity.includes(keyed.severity)) return false;
            if (keyed.message && !message.includes(keyed.message)) return false;
            if (keyed.time && !timestamp.includes(keyed.time)) return false;
            if (keyed.origin && !source.includes(keyed.origin)) return false;

            if (freeText.length > 0) {
                const haystack = `${timestamp} ${source} ${message} ${severity}`;
                return freeText.every((t) => haystack.includes(t));
            }

            return true;
        });
    }, [logs, activeFilter, searchQuery]);

    const totalPages = Math.max(1, Math.ceil(filteredLogs.length / PAGE_SIZE));
    const clampedPage = Math.min(page, totalPages);
    const pageStart = (clampedPage - 1) * PAGE_SIZE;
    const pagedLogs = filteredLogs.slice(pageStart, pageStart + PAGE_SIZE);

    const copyTextToClipboard = async (text) => {
        if (navigator?.clipboard?.writeText) {
            await navigator.clipboard.writeText(text);
            return true;
        }

        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.setAttribute('readonly', '');
        textarea.style.position = 'absolute';
        textarea.style.left = '-9999px';
        document.body.appendChild(textarea);
        textarea.select();

        const copied = document.execCommand('copy');
        document.body.removeChild(textarea);
        return copied;
    };

    const handleCopyJson = async (entry) => {
        try {
            const copied = await copyTextToClipboard(JSON.stringify(entry, null, 2));
            if (!copied) throw new Error('Clipboard copy not supported');
            setCopiedEntryId(entry.id);
            setTimeout(() => setCopiedEntryId((prev) => (prev === entry.id ? null : prev)), 1600);
            addLog('info', 'Logs', 'Copied JSON object to clipboard');
        } catch {
            addLog('warning', 'Logs', 'Clipboard copy failed in this browser context');
        }
    };

    const setPageSafe = (nextPage) => {
        const p = Math.min(Math.max(nextPage, 1), totalPages);
        setPage(p);
    };

    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <div
                className="absolute inset-0"
                style={{ background: 'var(--bg-backdrop)', backdropFilter: 'blur(3px)' }}
                onClick={onClose}
            />

            <div
                className="relative w-full max-w-5xl h-[85vh] rounded-lg overflow-hidden flex flex-col"
                style={{
                    background: 'var(--bg-surface)',
                    border: '1px solid var(--border-main)',
                    boxShadow: '0 22px 48px rgba(0,0,0,0.55)',
                    backdropFilter: 'blur(10px)',
                }}
            >
                <header className="flex items-center justify-between px-6 py-4 border-b" style={{ borderColor: 'var(--border-main)', background: 'var(--bg-surface-raised)' }}>
                    <div className="flex items-center gap-4">
                        <div
                            className="p-2 rounded-lg"
                            style={{ background: 'rgba(37, 99, 235, 0.12)', border: '1px solid rgba(37, 99, 235, 0.3)' }}
                        >
                            <Terminal size={22} color="#2563EB" />
                        </div>
                        <div>
                            <h2 className="text-xl font-black leading-none text-primary">Logs & Activity</h2>
                            <p className="text-[10px] font-bold uppercase tracking-widest mt-1 text-tertiary">
                                Live System Feed
                            </p>
                        </div>
                    </div>

                    <div className="flex items-center gap-3">
                        <div className="relative">
                            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-tertiary" />
                            <input
                                type="text"
                                value={searchQuery}
                                onChange={(e) => {
                                    setSearchQuery(e.target.value);
                                    setPage(1);
                                }}
                                placeholder="Search logs... source:api"
                                className="w-64 rounded-lg pl-9 pr-3 py-2 text-sm outline-none"
                                style={{
                                    background: 'var(--bg-input)',
                                    border: '1px solid var(--border-main)',
                                    color: 'var(--text-primary)',
                                }}
                            />
                        </div>
                        <button className="p-2 rounded-lg text-tertiary hover:text-primary transition-colors" onClick={onClose} aria-label="Close logs panel">
                            <X size={18} />
                        </button>
                    </div>
                </header>

                <nav
                    className="flex items-center px-6 py-3 gap-1 border-b"
                    style={{ borderColor: 'var(--border-main)', background: 'var(--bg-surface-raised)' }}
                >
                    {['all', 'info', 'warning', 'error'].map((id) => (
                        <button
                            key={id}
                            onClick={() => {
                                setActiveFilter(id);
                                setPage(1);
                            }}
                            className="px-5 py-1.5 rounded-lg text-xs font-black uppercase transition-all"
                            style={{
                                background: activeFilter === id ? '#2563EB' : '#EFF6FF',
                                color: activeFilter === id ? '#fff' : '#2563EB',
                                border: `1px solid ${activeFilter === id ? '#2563EB' : '#BFDBFE'}`,
                            }}
                        >
                            {id}
                        </button>
                    ))}

                    <div
                        className="ml-auto flex items-center gap-2 px-3 py-1.5 rounded-full"
                        style={{ background: 'rgba(37, 99, 235, 0.1)', border: '1px solid rgba(37, 99, 235, 0.25)' }}
                    >
                        <span
                            className={`h-2 w-2 rounded-full ${wsConnected ? 'animate-pulse' : ''}`}
                            style={{ background: wsConnected ? '#2563EB' : '#9497ad' }}
                        />
                        <span className="text-[10px] font-black uppercase tracking-widest" style={{ color: wsConnected ? '#2563EB' : '#9497ad' }}>
                            {wsConnected ? 'Live Stream Active' : 'Stream Idle'}
                        </span>
                    </div>
                </nav>

                <div className="flex-1 overflow-y-auto custom-scrollbar">
                    {pagedLogs.length === 0 ? (
                        <div className="h-full min-h-[260px] grid place-items-center text-sm text-tertiary">
                            No logs for current filter.
                        </div>
                    ) : (
                        <table className="w-full text-left border-collapse">
                            <thead
                                className="sticky top-0 z-10"
                                style={{ background: 'var(--bg-surface-raised)', borderBottom: '1px solid var(--border-main)' }}
                            >
                                <tr>
                                    <th className="px-6 py-4 text-[10px] font-black uppercase tracking-widest text-tertiary">Timestamp</th>
                                    <th className="px-6 py-4 text-[10px] font-black uppercase tracking-widest text-tertiary">Severity</th>
                                    <th className="px-6 py-4 text-[10px] font-black uppercase tracking-widest text-tertiary">Origin</th>
                                    <th className="px-6 py-4 text-[10px] font-black uppercase tracking-widest text-tertiary">Message</th>
                                    <th className="px-6 py-4 w-10" />
                                </tr>
                            </thead>
                            <tbody>
                                {pagedLogs.map((entry) => {
                                    const severity = normalizeSeverity(entry.severity);
                                    const isExpanded = expandedId === entry.id;
                                    const sourceText = String(entry.source || 'system');
                                    const sourceBreadcrumb = buildOriginBreadcrumb(sourceText);

                                    return (
                                        <Fragment key={entry.id}>
                                            <tr
                                                className="group cursor-pointer transition-colors"
                                                style={{
                                                    background: isExpanded ? 'var(--color-accent-faint)' : 'transparent',
                                                    borderBottom: '1px solid var(--border-main)',
                                                }}
                                                onClick={() => setExpandedId(isExpanded ? null : entry.id)}
                                            >
                                                <td className="px-6 py-4 text-xs font-mono font-bold text-tertiary">{entry.timestamp}</td>
                                                <td className="px-6 py-4"><GeometricSeverityPill severity={severity} /></td>
                                                <td className="px-6 py-4 text-xs font-mono font-bold text-accent-blue" title={sourceText}>{sourceBreadcrumb}</td>
                                                <td className="px-6 py-4 text-sm font-medium text-primary">{String(entry.message || '')}</td>
                                                <td className="px-6 py-4 text-tertiary">
                                                    {isExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                                                </td>
                                            </tr>

                                            {isExpanded && (
                                                <tr style={{ background: 'var(--color-accent-faint)' }}>
                                                    <td colSpan={5} className="px-6 pb-5">
                                                        <div className="rounded-lg p-4" style={{ background: 'var(--bg-input)', border: '1px solid var(--border-main)' }}>
                                                            <div className="flex items-center justify-between pb-3 mb-3" style={{ borderBottom: '1px solid var(--border-main)' }}>
                                                                <span className="text-[10px] font-black uppercase tracking-widest text-tertiary">
                                                                    Extended Metadata
                                                                </span>
                                                                <div className="flex items-center gap-2">
                                                                    <button
                                                                        onClick={(e) => {
                                                                            e.stopPropagation();
                                                                            setCompactJson((prev) => !prev);
                                                                        }}
                                                                        className="px-2.5 py-1 rounded text-[10px] font-black uppercase"
                                                                        style={{ color: 'var(--text-tertiary)', border: '1px solid var(--border-main)' }}
                                                                    >
                                                                        {compactJson ? 'Pretty JSON' : 'Compact JSON'}
                                                                    </button>
                                                                    <button
                                                                        onClick={(e) => {
                                                                            e.stopPropagation();
                                                                            handleCopyJson(entry);
                                                                        }}
                                                                        type="button"
                                                                        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-[10px] font-black uppercase transition-all"
                                                                        style={{
                                                                            color: '#2563EB',
                                                                            border: '1px solid rgba(37, 99, 235, 0.4)',
                                                                            background: copiedEntryId === entry.id ? 'rgba(37, 99, 235, 0.1)' : 'transparent',
                                                                        }}
                                                                    >
                                                                        {copiedEntryId === entry.id ? <Check size={12} /> : <Copy size={12} />}
                                                                        {copiedEntryId === entry.id ? 'Copied' : 'Copy JSON'}
                                                                    </button>
                                                                </div>
                                                            </div>
                                                            <div className="rounded-lg p-3 overflow-x-auto text-xs font-mono" style={{ background: '#05060b', color: '#e1e1e6' }}>
                                                                {renderJsonSyntax(entry, compactJson)}
                                                            </div>
                                                        </div>
                                                    </td>
                                                </tr>
                                            )}
                                        </Fragment>
                                    );
                                })}
                            </tbody>
                        </table>
                    )}
                </div>

                <footer
                    className="px-6 py-4 flex items-center justify-between border-t"
                    style={{ borderColor: 'var(--border-main)', background: 'var(--bg-surface-raised)' }}
                >
                    <p className="text-[10px] font-black uppercase tracking-widest text-tertiary">
                        Entry Range: {filteredLogs.length === 0 ? '0-0' : `${pageStart + 1}-${Math.min(pageStart + PAGE_SIZE, filteredLogs.length)}`} / {filteredLogs.length}
                    </p>

                    <div className="flex items-center gap-3">
                        <button
                            onClick={clearLogs}
                            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-black uppercase tracking-wider"
                            style={{ border: '1px solid rgba(62,64,90,0.45)', color: '#9497ad' }}
                        >
                            <Trash2 size={14} />
                            Clear
                        </button>

                        <button
                            onClick={() => setPageSafe(clampedPage - 1)}
                            disabled={clampedPage <= 1}
                            className="p-2 rounded-lg disabled:opacity-40"
                            style={{ border: '1px solid rgba(62,64,90,0.45)', color: '#9497ad' }}
                            aria-label="Previous page"
                        >
                            <ChevronLeft size={16} />
                        </button>
                        <span className="text-xs font-bold px-2 text-primary">
                            {clampedPage} / {totalPages}
                        </span>
                        <button
                            onClick={() => setPageSafe(clampedPage + 1)}
                            disabled={clampedPage >= totalPages}
                            className="p-2 rounded-lg disabled:opacity-40"
                            style={{ border: '1px solid rgba(62,64,90,0.45)', color: '#9497ad' }}
                            aria-label="Next page"
                        >
                            <ChevronRight size={16} />
                        </button>

                        <button
                            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-black uppercase tracking-wider"
                            style={{ background: '#2563EB', color: '#ffffff' }}
                            onClick={() => addLog('info', 'logs-panel', 'History fetch is not configured yet')}
                        >
                            <History size={14} />
                            Fetch History
                        </button>
                    </div>
                </footer>
            </div>
        </div>
    );
}
