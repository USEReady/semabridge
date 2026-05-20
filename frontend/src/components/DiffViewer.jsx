import { useState, useMemo } from 'react';
import { Split, Layers, CheckCircle2 } from 'lucide-react';

export default function DiffViewer({ leftContent = '', rightContent = '', leftTitle = 'Previous', rightTitle = 'Current' }) {
    const [viewMode, setViewMode] = useState('split'); // 'split' or 'unified'
    const [showIdentical, setShowIdentical] = useState(false);

    const leftLines = useMemo(() => leftContent.split('\n'), [leftContent]);
    const rightLines = useMemo(() => rightContent.split('\n'), [rightContent]);

    const isIdentical = useMemo(() => {
        return leftContent === rightContent;
    }, [leftContent, rightContent]);

    // Simple line-by-line diff for demonstration
    // In a production app, we would use a more robust library like 'diff' or 'jsdiff'
    const diffResult = useMemo(() => {
        const maxLen = Math.max(leftLines.length, rightLines.length);
        const result = [];
        for (let i = 0; i < maxLen; i++) {
            const left = leftLines[i] || '';
            const right = rightLines[i] || '';
            const type = left === right ? 'equal' : (left && !right ? 'deleted' : (!left && right ? 'added' : 'modified'));
            result.push({ left, right, type, line: i + 1 });
        }
        return result;
    }, [leftLines, rightLines]);

    const counts = useMemo(() => ({
        added: diffResult.filter(r => r.type === 'added').length,
        deleted: diffResult.filter(r => r.type === 'deleted').length,
        modified: diffResult.filter(r => r.type === 'modified').length,
    }), [diffResult]);

    return (
        <div className="flex flex-col h-full bg-[#0b1120] text-slate-300 font-mono text-[13px] border border-white/5 rounded-lg overflow-hidden">
            {/* Toolbar */}
            <div className="flex items-center justify-between px-4 py-2 bg-white/2 border-b border-white/5">
                <div className="flex items-center gap-4">
                    <span className="text-[11px] font-bold uppercase tracking-widest text-slate-500">Diff Viewer</span>
                    <div className="flex items-center gap-2">
                        <button
                            onClick={() => setViewMode('split')}
                            className={`px-2 py-1 rounded flex items-center gap-1.5 transition-all ${viewMode === 'split' ? 'bg-indigo-600/20 text-indigo-400' : 'text-slate-500 hover:text-slate-300'}`}
                        >
                            <Split size={14} /> Split
                        </button>
                        <button
                            onClick={() => setViewMode('unified')}
                            className={`px-2 py-1 rounded flex items-center gap-1.5 transition-all ${viewMode === 'unified' ? 'bg-indigo-600/20 text-indigo-400' : 'text-slate-500 hover:text-slate-300'}`}
                        >
                            <Layers size={14} /> Unified
                        </button>
                    </div>
                </div>
                <div className="flex items-center gap-3 text-[11px]">
                    <span className="text-emerald-500 font-bold">+{counts.added}</span>
                    <span className="text-red-500 font-bold">-{counts.deleted}</span>
                    <span className="text-amber-400 font-bold">~{counts.modified}</span>
                </div>
            </div>

            {/* Diff Content */}
            <div className="flex-1 overflow-auto custom-scrollbar">
                {isIdentical && !showIdentical ? (
                    viewMode === 'split' ? (
                        <div className="flex min-w-full h-full min-h-[350px]">
                            {/* Left Panel */}
                            <div className="flex-1 min-w-0 border-r border-white/5 flex flex-col">
                                <div className="px-3 py-1 bg-white/2 border-b border-white/5 text-[10px] font-bold text-slate-500 sticky top-0 z-10">{leftTitle}</div>
                                <div className="flex-1 flex flex-col items-center justify-center p-8 text-center bg-[#0b1120]/30">
                                    <div className="w-12 h-12 rounded-2xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center mb-3">
                                        <CheckCircle2 size={24} className="text-emerald-500" />
                                    </div>
                                    <h4 className="text-xs font-bold text-white uppercase tracking-wider mb-1">Identical Content</h4>
                                    <p className="text-[11px] text-slate-400 max-w-xs leading-relaxed mb-4">
                                        No differences detected. Content matches target version.
                                    </p>
                                    <button
                                        onClick={() => setShowIdentical(true)}
                                        className="px-3.5 py-2 bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/30 hover:border-emerald-500/50 text-emerald-400 rounded-xl text-[10px] font-black uppercase tracking-wider transition-all active:scale-[0.98]"
                                    >
                                        Show Content Anyway
                                    </button>
                                </div>
                            </div>
                            {/* Right Panel */}
                            <div className="flex-1 min-w-0 flex flex-col">
                                <div className="px-3 py-1 bg-white/2 border-b border-white/5 text-[10px] font-bold text-slate-500 sticky top-0 z-10">{rightTitle}</div>
                                <div className="flex-1 flex flex-col items-center justify-center p-8 text-center bg-[#0b1120]/30">
                                    <div className="w-12 h-12 rounded-2xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center mb-3">
                                        <CheckCircle2 size={24} className="text-emerald-500" />
                                    </div>
                                    <h4 className="text-xs font-bold text-white uppercase tracking-wider mb-1">Identical Content</h4>
                                    <p className="text-[11px] text-slate-400 max-w-xs leading-relaxed mb-4">
                                        No differences detected. Content matches base version.
                                    </p>
                                    <button
                                        onClick={() => setShowIdentical(true)}
                                        className="px-3.5 py-2 bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/30 hover:border-emerald-500/50 text-emerald-400 rounded-xl text-[10px] font-black uppercase tracking-wider transition-all active:scale-[0.98]"
                                    >
                                        Show Content Anyway
                                    </button>
                                </div>
                            </div>
                        </div>
                    ) : (
                        <div className="flex flex-col items-center justify-center p-12 text-center h-full min-h-[350px] bg-[#0b1120]/50">
                            <div className="w-16 h-16 rounded-2xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center mb-4">
                                <CheckCircle2 size={32} className="text-emerald-500 animate-pulse" />
                            </div>
                            <h4 className="text-sm font-bold text-white uppercase tracking-wider mb-2">Identical Content</h4>
                            <p className="text-xs text-slate-400 max-w-md leading-relaxed mb-6">
                                No differences detected between selected snapshots. Both contain identical schema definition and metadata.
                            </p>
                            <button
                                onClick={() => setShowIdentical(true)}
                                className="px-4 py-2.5 bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/35 text-emerald-400 rounded-xl text-xs font-black uppercase tracking-wider transition-all active:scale-[0.98]"
                            >
                                Show Content Anyway
                            </button>
                        </div>
                    )
                ) : (
                    <div>
                        {isIdentical && (
                            <div className="flex items-center justify-between px-4 py-2.5 bg-emerald-500/5 border-b border-emerald-500/10 text-xs text-emerald-400/90 font-medium">
                                <div className="flex items-center gap-2">
                                    <CheckCircle2 size={14} className="shrink-0" />
                                    <span>Showing identical content. No changes detected.</span>
                                </div>
                                <button
                                    onClick={() => setShowIdentical(false)}
                                    className="hover:underline font-bold text-[10px] uppercase tracking-wider text-emerald-500/80 hover:text-emerald-400"
                                >
                                    Hide Content
                                </button>
                            </div>
                        )}
                        {viewMode === 'split' ? (
                    <div className="flex min-w-full">
                        {/* Left Panel */}
                        <div className="flex-1 min-w-0 border-r border-white/5">
                            <div className="px-3 py-1 bg-white/2 border-b border-white/5 text-[10px] font-bold text-slate-500 sticky top-0 z-10">{leftTitle}</div>
                            {diffResult.map((res, i) => (
                                <div key={i} className={`flex px-2 ${res.type === 'deleted' || res.type === 'modified' ? 'bg-red-500/10' : ''}`}>
                                    <span className="w-8 shrink-0 text-slate-600 text-right select-none pr-2 border-r border-white/5">{res.line}</span>
                                    <pre className={`min-w-0 overflow-x-auto pl-2 whitespace-pre ${res.type === 'deleted' || res.type === 'modified' ? 'text-red-400' : ''}`}>
                                        {(res.type === 'deleted' || res.type === 'modified') && '- '}{res.left}
                                    </pre>
                                </div>
                            ))}
                        </div>
                        {/* Right Panel */}
                        <div className="flex-1 min-w-0">
                            <div className="px-3 py-1 bg-white/2 border-b border-white/5 text-[10px] font-bold text-slate-500 sticky top-0 z-10">{rightTitle}</div>
                            {diffResult.map((res, i) => (
                                <div key={i} className={`flex px-2 ${res.type === 'added' || res.type === 'modified' ? 'bg-emerald-500/10' : ''}`}>
                                    <span className="w-8 shrink-0 text-slate-600 text-right select-none pr-2 border-r border-white/5">{res.line}</span>
                                    <pre className={`min-w-0 overflow-x-auto pl-2 whitespace-pre ${res.type === 'added' || res.type === 'modified' ? 'text-emerald-400' : ''}`}>
                                        {(res.type === 'added' || res.type === 'modified') && '+ '}{res.right}
                                    </pre>
                                </div>
                            ))}
                        </div>
                    </div>
                ) : (
                    <div className="min-w-full">
                        {/* Unified View */}
                        {diffResult.map((res, i) => (
                            <div key={i}>
                                {res.type === 'deleted' && (
                                    <div className="flex px-2 bg-red-500/10 min-w-0">
                                        <span className="w-8 shrink-0 text-slate-600 text-right select-none pr-2 border-r border-white/5">{res.line}</span>
                                        <pre className="min-w-0 overflow-x-auto pl-2 text-red-400">- {res.left}</pre>
                                    </div>
                                )}
                                {res.type === 'added' && (
                                    <div className="flex px-2 bg-emerald-500/10 min-w-0">
                                        <span className="w-8 shrink-0 text-slate-600 text-right select-none pr-2 border-r border-white/5">{res.line}</span>
                                        <pre className="min-w-0 overflow-x-auto pl-2 text-emerald-400">+ {res.right}</pre>
                                    </div>
                                )}
                                {res.type === 'modified' && (
                                    <>
                                        <div className="flex px-2 bg-red-500/10 min-w-0">
                                            <span className="w-8 shrink-0 text-slate-600 text-right select-none pr-2 border-r border-white/5">{res.line}</span>
                                            <pre className="min-w-0 overflow-x-auto pl-2 text-red-400">- {res.left}</pre>
                                        </div>
                                        <div className="flex px-2 bg-emerald-500/10 min-w-0">
                                            <span className="w-8 shrink-0 text-slate-600 text-right select-none pr-2 border-r border-white/5">{res.line}</span>
                                            <pre className="min-w-0 overflow-x-auto pl-2 text-emerald-400">+ {res.right}</pre>
                                        </div>
                                    </>
                                )}
                                {res.type === 'equal' && (
                                    <div className="flex px-2 min-w-0">
                                        <span className="w-8 shrink-0 text-slate-600 text-right select-none pr-2 border-r border-white/5">{res.line}</span>
                                        <pre className="min-w-0 overflow-x-auto pl-2 whitespace-pre">  {res.left}</pre>
                                    </div>
                                )}
                            </div>
                        ))}
                    </div>
                )}
                </div>
                )}
            </div>
        </div>
    );
}
