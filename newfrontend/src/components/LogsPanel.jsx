import { Fragment, useMemo, useState } from 'react';
import {
    X,
    AlertTriangle,
    AlertCircle,
    Info,
    ChevronDown,
    ChevronUp,
    Copy,
    Trash2,
    ChevronLeft,
    ChevronRight,
    Activity,
    List,
} from 'lucide-react';
import { useLogs } from '../context/LogsContext';
import SearchInput from './common/SearchInput';

const PAGE_SIZE = 25;

const SEVERITY_META = {
    info: {
        label: 'Info',
        icon: Info,
        text: 'var(--text-tertiary)',
        dot: 'var(--accent-blue)',
    },
    warning: {
        label: 'Warning',
        icon: AlertTriangle,
        text: 'var(--color-warning)',
        dot: 'var(--color-warning)',
    },
    error: {
        label: 'Error',
        icon: AlertCircle,
        text: 'var(--color-danger)',
        dot: 'var(--color-danger)',
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
        const regex = /("(?:\\.|[^"])*")\s*:|(\btrue\b|\bfalse\b|\bnull\b)|(-?\d+(?:\.\d+)?)|(\"(?:\\.|[^\"])*\")/g;
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

function extractLatencyMs(entry) {
    const payloadLatency = entry?.payload?.latency;
    if (typeof payloadLatency === 'number') return payloadLatency;
    if (typeof payloadLatency === 'string') {
        const m = payloadLatency.match(/(\d+(?:\.\d+)?)/);
        if (m) return Number(m[1]);
    }
    const messageLatency = String(entry?.message || '').match(/(\d+(?:\.\d+)?)\s*ms/i);
    return messageLatency ? Number(messageLatency[1]) : null;
}

function GeometricSeverityPill({ severity }) {
    const meta = SEVERITY_META[severity] || SEVERITY_META.info;
    const Icon = meta.icon;
    const shapeClass = severity === 'warning' ? 'rotate-45 rounded-[2px]' : severity === 'error' ? 'rounded-[2px]' : 'rounded-full';

    return (
        <span
            className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[10px] font-semibold"
            style={{
                background: 'var(--bg-surface-raised)',
                color: meta.text,
                border: '1px solid var(--border-main)',
            }}
        >
            <span className={`h-1.5 w-1.5 ${shapeClass}`} style={{ background: meta.dot }} />
            <Icon size={11} strokeWidth={1.75} />
            {meta.label}
        </span>
    );
}

function SoftButton({ active, onClick, children }) {
    return (
        <button
            onClick={onClick}
            className="inline-flex items-center rounded-[7px] px-3.5 py-1.5 text-[12px] font-semibold transition-colors"
            style={{
                background: active ? 'var(--bg-surface-hover)' : 'transparent',
                color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
                border: active ? '1px solid var(--border-main)' : '1px solid transparent',
            }}
        >
            {children}
        </button>
    );
}

export default function LogsPanel({ isOpen, onClose }) {
    const { logs, clearLogs, wsConnected, addLog } = useLogs();
    const [activeFilter, setActiveFilter] = useState('all');
    const [searchQuery, setSearchQuery] = useState('');
    const [expandedId, setExpandedId] = useState(null);
    const [compactJson, setCompactJson] = useState(false);
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

    const latencySummary = useMemo(() => {
        const candidates = filteredLogs.map(extractLatencyMs).filter((v) => typeof v === 'number');
        if (candidates.length === 0) return '--';
        const avg = candidates.reduce((a, b) => a + b, 0) / candidates.length;
        return `${avg.toFixed(1)} ms`;
    }, [filteredLogs]);

    const handleCopyJson = async (entry) => {
        try {
            await navigator.clipboard.writeText(JSON.stringify(entry, null, 2));
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
            <div className="absolute inset-0 backdrop-blur-sm" style={{ background: 'var(--bg-backdrop)' }} onClick={onClose} />

            <div
                className="relative w-full max-w-6xl max-h-[90vh] rounded-xl flex flex-col overflow-hidden"
                style={{
                    background: 'linear-gradient(180deg, rgba(14,18,28,0.98) 0%, rgba(11,16,25,0.98) 100%)',
                    border: '1px solid var(--border-main)',
                    boxShadow: '0 24px 56px rgba(0,0,0,0.5)',
                }}
            >
                <header className="h-14 px-5 flex items-center justify-between border-b border-main/70">
                    <div className="flex items-center gap-2.5">
                        <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: 'var(--bg-surface-raised)', border: '1px solid var(--border-main)' }}>
                            <List size={15} className="text-secondary" strokeWidth={1.75} />
                        </div>
                        <div>
                            <h2 className="text-[15px] font-semibold text-primary leading-none">Log Observer</h2>
                            <p className="text-[10px] uppercase tracking-widest text-tertiary mt-1">Live Event Stream</p>
                        </div>
                    </div>

                    <div className="flex items-center gap-3">
                        <SearchInput
                            value={searchQuery}
                            onChange={(value) => {
                                setSearchQuery(value);
                                setPage(1);
                            }}
                            placeholder="Search… (Ctrl+K)  source:snowflake"
                            width={360}
                        />
                        <div className="flex items-center gap-2 px-2.5 py-1 rounded-full" style={{ background: 'var(--bg-surface-raised)', border: '1px solid var(--border-main)' }}>
                            <span className={`h-1.5 w-1.5 rounded-full ${wsConnected ? 'animate-pulse' : ''}`} style={{ background: wsConnected ? 'var(--color-success)' : 'var(--text-tertiary)' }} />
                            <span className="text-[10px] font-semibold uppercase tracking-widest" style={{ color: wsConnected ? 'var(--color-success)' : 'var(--text-tertiary)' }}>
                                {wsConnected ? 'Live' : 'Idle'}
                            </span>
                        </div>
                        <button onClick={onClose} className="p-1.5 rounded-md hover:bg-surface-hover text-tertiary" aria-label="Close logs panel">
                            <X size={16} strokeWidth={1.75} />
                        </button>
                    </div>
                </header>

                <div className="px-5 py-2 border-b border-main/50 flex items-center gap-1.5">
                    {[
                        { id: 'all', label: 'All' },
                        { id: 'info', label: 'Info' },
                        { id: 'warning', label: 'Warning' },
                        { id: 'error', label: 'Error' },
                    ].map((item) => (
                        <SoftButton
                            key={item.id}
                            active={activeFilter === item.id}
                            onClick={() => {
                                setActiveFilter(item.id);
                                setPage(1);
                            }}
                        >
                            {item.label}
                        </SoftButton>
                    ))}
                </div>

                <div className="flex-1 overflow-y-auto custom-scrollbar px-4 py-2">
                    {pagedLogs.length === 0 ? (
                        <div className="h-full min-h-[240px] grid place-items-center text-tertiary text-sm">
                            No logs match current filters.
                        </div>
                    ) : (
                        <table className="w-full text-left table-fixed border-separate [border-spacing:0_8px]">
                            <colgroup>
                                <col style={{ width: 160 }} />
                                <col style={{ width: 150 }} />
                                <col style={{ width: 260 }} />
                                <col />
                                <col style={{ width: 34 }} />
                            </colgroup>
                            <thead className="sticky top-0 z-10" style={{ background: 'rgba(14,18,28,0.96)' }}>
                                <tr>
                                    <th className="px-3 py-2 text-[10px] font-semibold uppercase tracking-widest text-tertiary">Timestamp</th>
                                    <th className="px-3 py-2 text-[10px] font-semibold uppercase tracking-widest text-tertiary">Severity</th>
                                    <th className="px-3 py-2 text-[10px] font-semibold uppercase tracking-widest text-tertiary">Origin</th>
                                    <th className="px-3 py-2 text-[10px] font-semibold uppercase tracking-widest text-tertiary">Message</th>
                                    <th />
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
                                                onClick={() => setExpandedId(isExpanded ? null : entry.id)}
                                                className="cursor-pointer transition-colors"
                                                style={{ background: isExpanded ? 'var(--bg-surface-hover)' : 'var(--bg-surface-raised)' }}
                                            >
                                                <td className="px-3 py-3 rounded-l-lg text-xs font-mono text-tertiary whitespace-nowrap">{entry.timestamp}</td>
                                                <td className="px-3 py-3">
                                                    <GeometricSeverityPill severity={severity} />
                                                </td>
                                                <td className="px-3 py-3">
                                                    <span className="block text-xs font-mono text-secondary truncate" title={sourceText}>
                                                        {sourceBreadcrumb}
                                                    </span>
                                                </td>
                                                <td className="px-3 py-3 text-sm text-primary font-medium truncate">{entry.message}</td>
                                                <td className="px-2 py-3 rounded-r-lg text-tertiary">
                                                    {isExpanded ? <ChevronUp size={15} strokeWidth={1.75} /> : <ChevronDown size={15} strokeWidth={1.75} />}
                                                </td>
                                            </tr>

                                            <tr>
                                                <td colSpan={5} className="p-0">
                                                    <div className={`transition-all duration-200 ease-out overflow-hidden ${isExpanded ? 'max-h-[420px] opacity-100 mt-1' : 'max-h-0 opacity-0'}`}>
                                                        <div className="rounded-lg border border-main bg-surface px-3 py-3">
                                                            <div className="flex items-center justify-between mb-2 pb-2 border-b border-main/60">
                                                                <div className="flex items-center gap-2 text-tertiary">
                                                                    <Activity size={13} strokeWidth={1.75} className="text-secondary" />
                                                                    <span className="text-[10px] uppercase tracking-widest font-semibold">Metadata</span>
                                                                </div>
                                                                <div className="flex items-center gap-2">
                                                                    <button
                                                                        onClick={(e) => {
                                                                            e.stopPropagation();
                                                                            setCompactJson((prev) => !prev);
                                                                        }}
                                                                        className="rounded-[7px] px-2.5 py-1 text-[10px] font-semibold"
                                                                        style={{
                                                                            background: compactJson ? 'var(--bg-surface-hover)' : 'transparent',
                                                                            border: '1px solid var(--border-main)',
                                                                            color: 'var(--text-secondary)',
                                                                        }}
                                                                    >
                                                                        {compactJson ? 'Pretty JSON' : 'Format JSON'}
                                                                    </button>
                                                                    <button
                                                                        onClick={(e) => {
                                                                            e.stopPropagation();
                                                                            handleCopyJson(entry);
                                                                        }}
                                                                        className="inline-flex items-center gap-1.5 rounded-[7px] px-2.5 py-1 text-[10px] font-semibold"
                                                                        style={{
                                                                            border: '1px solid var(--border-main)',
                                                                            color: 'var(--text-secondary)',
                                                                            background: 'transparent',
                                                                        }}
                                                                    >
                                                                        <Copy size={11} strokeWidth={1.75} />
                                                                        Copy Object
                                                                    </button>
                                                                </div>
                                                            </div>
                                                            <div className="rounded-md border border-main/70 bg-surface-raised p-2.5 overflow-x-auto font-mono text-xs leading-relaxed text-primary">
                                                                {renderJsonSyntax(entry, compactJson)}
                                                            </div>
                                                        </div>
                                                    </div>
                                                </td>
                                            </tr>
                                        </Fragment>
                                    );
                                })}
                            </tbody>
                        </table>
                    )}
                </div>

                <footer className="h-10 px-5 border-t border-main/60 bg-transparent flex items-center justify-between">
                    <div className="flex items-center gap-4 text-[10px] font-semibold uppercase tracking-widest text-tertiary">
                        <span>Total Records: {filteredLogs.length}</span>
                        <span className="h-3.5 w-px bg-main" />
                        <span>System Latency: {latencySummary}</span>
                    </div>

                    <div className="flex items-center gap-2">
                        <button
                            onClick={clearLogs}
                            className="inline-flex items-center gap-1.5 rounded-[7px] px-3 py-1.5 text-[10px] font-semibold"
                            style={{ border: '1px solid var(--border-main)', color: 'var(--text-secondary)', background: 'transparent' }}
                        >
                            <Trash2 size={11} strokeWidth={1.75} />
                            Clear
                        </button>

                        <div className="inline-flex items-center rounded-[7px] overflow-hidden" style={{ border: '1px solid var(--border-main)' }}>
                            <button
                                onClick={() => setPageSafe(clampedPage - 1)}
                                disabled={clampedPage <= 1}
                                className="px-2 py-1.5 text-tertiary hover:text-primary disabled:opacity-40"
                                style={{ background: 'transparent' }}
                                aria-label="Previous page"
                            >
                                <ChevronLeft size={14} strokeWidth={1.75} />
                            </button>
                            {[clampedPage - 1, clampedPage, clampedPage + 1]
                                .filter((p) => p >= 1 && p <= totalPages)
                                .map((p) => (
                                    <button
                                        key={p}
                                        onClick={() => setPageSafe(p)}
                                        className="min-w-7 px-2 py-1.5 text-[11px] font-semibold"
                                        style={{
                                            borderLeft: '1px solid var(--border-main)',
                                            background: p === clampedPage ? 'var(--bg-surface-hover)' : 'transparent',
                                            color: p === clampedPage ? 'var(--text-primary)' : 'var(--text-secondary)',
                                        }}
                                    >
                                        {p}
                                    </button>
                                ))}
                            <button
                                onClick={() => setPageSafe(clampedPage + 1)}
                                disabled={clampedPage >= totalPages}
                                className="px-2 py-1.5 text-tertiary hover:text-primary disabled:opacity-40"
                                style={{ borderLeft: '1px solid var(--border-main)', background: 'transparent' }}
                                aria-label="Next page"
                            >
                                <ChevronRight size={14} strokeWidth={1.75} />
                            </button>
                        </div>
                    </div>
                </footer>
            </div>
        </div>
    );
}
