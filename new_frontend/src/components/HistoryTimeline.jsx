import {
    Clock,
    GitCommit,
    CheckCircle2,
    AlertTriangle,
    RefreshCw,
    Upload,
    ArrowRightLeft,
    ChevronRight,
    Grip,
    Filter,
    X,
    PanelRight
} from 'lucide-react';
import { useState, useEffect } from 'react';
import { api } from '../utils/api';

const TIMELINE_EVENTS = [
    {
        id: 1,
        type: 'sync',
        title: 'Full Sync Completed',
        subtitle: '22 models synced',
        time: '2m',
        icon: RefreshCw,
        color: 'var(--color-success)',
        colorMuted: 'var(--color-success-muted)',
    },
    {
        id: 2,
        type: 'validation',
        title: 'Validation Warning',
        subtitle: 'Web Sales.yaml error',
        time: '8m',
        icon: AlertTriangle,
        color: 'var(--color-warning)',
        colorMuted: 'var(--color-warning-muted)',
    }
];

export default function HistoryTimeline({ onCompare, onCollapse }) {
    const [events, setEvents] = useState([]);
    const [isLoading, setIsLoading] = useState(true);
    const [activeFilter, setActiveFilter] = useState('All');

    useEffect(() => {
        const loadHistory = async () => {
            try {
                const data = await api.getHistory();
                const mappedEvents = data.map(v => ({
                    id: v.version_id,
                    type: 'commit',
                    title: v.description || 'Config Modified',
                    subtitle: `v${v.version_id.substring(0, 6)}`,
                    time: 'Now',
                    icon: GitCommit,
                    color: 'var(--accent-blue)',
                    colorMuted: 'var(--color-primary-muted)',
                }));
                setEvents(mappedEvents.length > 0 ? mappedEvents : TIMELINE_EVENTS);
            } catch (err) {
                setEvents(TIMELINE_EVENTS);
            } finally {
                setIsLoading(false);
            }
        };
        loadHistory();
    }, []);

    return (
        <aside
            className="theme-transition flex flex-col h-full bg-surface select-none border-l border-main"
        >
            {/* Header */}
            <div className="flex items-center justify-between px-4 h-11 border-b border-main bg-surface-raised">
                <div className="flex items-center gap-2">
                    <Clock size={14} className="text-tertiary" />
                    <span className="text-[11px] font-bold uppercase tracking-wider text-secondary">Activity</span>
                </div>
                <div className="flex items-center gap-1">
                    <button className="p-1.5 hover:bg-surface-hover rounded text-tertiary"><Filter size={13} /></button>
                    <button
                        onClick={onCollapse}
                        className="p-1.5 hover:bg-surface-hover rounded text-tertiary"
                    >
                        <PanelRight size={14} />
                    </button>
                </div>
            </div>

            {/* Filters */}
            <div className="flex items-center gap-1 p-2 bg-surface overflow-x-auto no-scrollbar border-b border-main">
                {['All', 'Errors', 'Warnings', 'Syncs'].map(f => (
                    <button
                        key={f}
                        onClick={() => setActiveFilter(f)}
                        className={`px-3 py-1 rounded-full text-[10px] font-bold transition-all whitespace-nowrap ${activeFilter === f
                            ? 'bg-accent-blue/20 text-accent-blue border border-accent-blue/30'
                            : 'text-tertiary hover:bg-surface-hover border border-transparent'
                            }`}
                    >
                        {f}
                    </button>
                ))}
            </div>

            {/* Content Area */}
            <div className="flex-1 overflow-y-auto custom-scrollbar">
                <section className="p-3">
                    <h3 className="text-[10px] font-bold uppercase tracking-widest text-tertiary mb-3 flex items-center gap-2">
                        <div className="w-1 h-3 bg-accent-blue rounded-full" />
                        Timeline
                    </h3>

                    <div className="space-y-4">
                        {events.map((event, idx) => (
                            <div key={event.id} className="flex gap-3 group cursor-pointer" onClick={() => onCompare?.()}>
                                <div className="flex flex-col items-center pt-1">
                                    <div className="p-1.5 rounded bg-white/5 border border-white/5 text-primary group-hover:border-accent-blue/30 transition-colors">
                                        <event.icon size={13} style={{ color: event.color }} />
                                    </div>
                                    {idx < events.length - 1 && <div className="w-px h-full bg-border-main my-1 opacity-40" />}
                                </div>
                                <div className="flex-1 min-w-0">
                                    <div className="flex items-center justify-between">
                                        <p className="text-[12px] font-bold text-primary truncate leading-tight">{event.title}</p>
                                        <span className="text-[9px] text-tertiary">{event.time}</span>
                                    </div>
                                    <p className="text-[11px] text-secondary mt-0.5">{event.subtitle}</p>
                                </div>
                            </div>
                        ))}
                    </div>
                </section>

                <div className="h-px bg-border-main mx-4 opacity-50 my-2" />

                <section className="p-3">
                    <h3 className="text-[10px] font-bold uppercase tracking-widest text-tertiary mb-3 flex items-center gap-2">
                        <div className="w-1 h-3 bg-color-warning rounded-full" />
                        Validation
                    </h3>
                    <div className="p-3 rounded-lg bg-color-warning-muted border border-color-warning-muted">
                        <div className="flex items-center gap-2 text-color-warning">
                            <AlertTriangle size={14} />
                            <span className="text-[11px] font-bold">2 Warnings detected</span>
                        </div>
                        <p className="text-[10px] text-secondary mt-1 ml-6 italic">Review semabridge.yaml:L12</p>
                    </div>
                </section>
            </div>

            {/* Summary Block */}
            <div className="p-4 border-t border-main bg-surface-raised">
                <div className="flex items-center justify-between mb-3 text-[10px] font-bold uppercase tracking-wider text-tertiary">
                    <span>Performance</span>
                    <span className="text-accent-blue">↑ 12%</span>
                </div>
                <div className="h-1.5 bg-black/20 rounded-full overflow-hidden">
                    <div className="h-full bg-accent-blue w-2/3 shadow-[0_0_8px_rgba(88,166,255,0.4)]" />
                </div>
                <p className="text-[9px] text-secondary mt-2">Deployment stability: High (99.4%)</p>
            </div>
        </aside>
    );
}
