import { useState, useEffect, useMemo } from 'react';
import { Play, Pause, SkipBack, SkipForward, GitCommit, GitCompare, CheckCircle2, XCircle, RotateCcw } from 'lucide-react';
import { api } from '../../utils/api';

function statusTone(status) {
  const s = String(status || '').toLowerCase();
  if (s === 'success') return { bg: 'rgba(34,197,94,.12)', fg: '#22C55E', border: 'rgba(34,197,94,.28)' };
  if (s === 'failed' || s === 'error') return { bg: 'rgba(239,68,68,.12)', fg: '#EF4444', border: 'rgba(239,68,68,.28)' };
  return { bg: 'rgba(148,163,184,.12)', fg: '#94A3B8', border: 'rgba(148,163,184,.28)' };
}

export default function TimeMachine({
  modelName,
  onSnapshotSelect,
  onCompareSnapshotSelect,
  onDiffModeChange,
  selectedSnapshotId,
  compareSnapshotId,
  diffMode = false,
}) {
  const [snapshots, setSnapshots] = useState([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);

  const ordered = useMemo(() => {
    return [...snapshots].sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
  }, [snapshots]);

  useEffect(() => {
    async function fetchHistory() {
      if (!modelName) return;
      try {
        const data = await api.getGraphSnapshots(modelName);
        const list = Array.isArray(data) ? data : [];
        setSnapshots(list);
        if (list.length > 0) {
          const sorted = [...list].sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
          const latest = sorted[sorted.length - 1];
          setCurrentIndex(sorted.length - 1);
          onSnapshotSelect?.(latest.snapshot_id);
          if (!compareSnapshotId && sorted.length > 1) {
            onCompareSnapshotSelect?.(sorted[sorted.length - 2].snapshot_id);
          }
        }
      } catch (e) {
        console.error('Failed to load history', e);
      }
    }
    fetchHistory();
  }, [modelName, onSnapshotSelect, onCompareSnapshotSelect, compareSnapshotId]);

  useEffect(() => {
    let timer;
    if (isPlaying && currentIndex < ordered.length - 1) {
      timer = setTimeout(() => {
        const nextIdx = currentIndex + 1;
        const next = ordered[nextIdx];
        setCurrentIndex(nextIdx);
        if (next) onSnapshotSelect?.(next.snapshot_id);
      }, 1400);
    } else if (currentIndex >= ordered.length - 1) {
      setIsPlaying(false);
    }
    return () => clearTimeout(timer);
  }, [isPlaying, currentIndex, ordered, onSnapshotSelect]);

  useEffect(() => {
    if (!selectedSnapshotId || !ordered.length) return;
    const idx = ordered.findIndex(s => s.snapshot_id === selectedSnapshotId);
    if (idx >= 0 && idx !== currentIndex) setCurrentIndex(idx);
  }, [selectedSnapshotId, ordered, currentIndex]);

  const current = ordered[currentIndex];
  const currentTone = statusTone(current?.status);

  const jumpTo = (idx) => {
    const safe = Math.max(0, Math.min(ordered.length - 1, idx));
    const s = ordered[safe];
    setCurrentIndex(safe);
    if (s) onSnapshotSelect?.(s.snapshot_id);
  };

  if (!ordered.length) return null;

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '1fr 360px',
      gap: 14,
      padding: '10px 16px',
      background: 'var(--bg-surface)',
      borderTop: '1px solid var(--border-color)',
    }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button onClick={() => jumpTo(currentIndex - 1)} disabled={currentIndex <= 0} style={miniBtn} title="Previous snapshot">
            <SkipBack size={15} />
          </button>
          <button onClick={() => setIsPlaying(v => !v)} style={miniBtn} title="Auto-scrub timeline">
            {isPlaying ? <Pause size={15} /> : <Play size={15} />}
          </button>
          <button onClick={() => jumpTo(currentIndex + 1)} disabled={currentIndex >= ordered.length - 1} style={miniBtn} title="Next snapshot">
            <SkipForward size={15} />
          </button>

          <input
            type="range"
            min="0"
            max={Math.max(0, ordered.length - 1)}
            value={currentIndex}
            onChange={(e) => jumpTo(parseInt(e.target.value, 10))}
            style={{ flex: 1, accentColor: 'var(--accent-blue)' }}
          />

          <button
            onClick={() => {
              onDiffModeChange?.(!diffMode);
              if (!diffMode && !compareSnapshotId && ordered.length > 1) {
                onCompareSnapshotSelect?.(ordered[Math.max(0, currentIndex - 1)].snapshot_id);
              }
            }}
            style={{
              ...miniBtn,
              borderColor: diffMode ? 'rgba(129,140,248,.45)' : 'var(--border-color)',
              background: diffMode ? 'rgba(129,140,248,.14)' : 'transparent',
              color: diffMode ? 'var(--accent-blue)' : 'var(--text-secondary)',
              minWidth: 108,
              justifyContent: 'center',
            }}
            title="Toggle diff mode"
          >
            <GitCompare size={14} /> Diff Mode
          </button>
        </div>

        <div style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '8px 10px', borderRadius: 8,
          border: `1px solid ${currentTone.border}`,
          background: currentTone.bg,
          fontSize: 11,
        }}>
          <GitCommit size={13} style={{ color: 'var(--accent-blue)' }} />
          <span style={{ color: 'var(--text-secondary)' }}>Snapshot</span>
          <span style={{ color: 'var(--accent-blue)', fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace' }}>
            {String(current?.snapshot_id || '').slice(0, 12)}
          </span>
          <span style={{ color: 'var(--text-tertiary)' }}>•</span>
          <span style={{ color: currentTone.fg, fontWeight: 700, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            {String(current?.status || '').toLowerCase() === 'success' ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
            {current?.status || 'unknown'}
          </span>
          <span style={{ color: 'var(--text-tertiary)' }}>•</span>
          <span style={{ color: 'var(--text-secondary)' }}>{current?.run_id || 'run:n/a'}</span>
          <span style={{ color: 'var(--text-tertiary)' }}>•</span>
          <span style={{ color: 'var(--text-secondary)' }}>{(current?.connectors || []).join(' → ') || 'connector:n/a'}</span>
          <span style={{ marginLeft: 'auto', color: 'var(--text-tertiary)' }}>
            {current?.timestamp ? new Date(current.timestamp).toLocaleString() : 'n/a'}
          </span>
        </div>
      </div>

      <div style={{
        border: '1px solid var(--border-color)',
        borderRadius: 8,
        background: 'var(--bg-app)',
        maxHeight: 126,
        overflow: 'auto',
      }}>
        {ordered.slice().reverse().map((s) => {
          const active = s.snapshot_id === selectedSnapshotId;
          const compare = diffMode && s.snapshot_id === compareSnapshotId;
          const tone = statusTone(s.status);
          return (
            <div
              key={s.snapshot_id}
              style={{
                display: 'grid',
                gridTemplateColumns: '1fr auto auto',
                gap: 8,
                alignItems: 'center',
                padding: '7px 8px',
                borderBottom: '1px solid var(--border-color)',
                background: active ? 'rgba(129,140,248,.12)' : 'transparent',
              }}
            >
              <button onClick={() => onSnapshotSelect?.(s.snapshot_id)} style={{ ...listBtn, color: active ? 'var(--accent-blue)' : 'var(--text-secondary)' }}>
                <span style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace' }}>{String(s.snapshot_id).slice(0, 8)}</span>
                <span style={{ color: 'var(--text-tertiary)', marginLeft: 6 }}>{new Date(s.timestamp).toLocaleTimeString()}</span>
                <span style={{ marginLeft: 6, color: tone.fg }}>{String(s.status || 'unknown').toUpperCase()}</span>
              </button>
              <button
                onClick={() => onCompareSnapshotSelect?.(s.snapshot_id)}
                title="Set baseline"
                style={{
                  ...miniBtn,
                  padding: '2px 7px',
                  height: 24,
                  borderColor: compare ? 'rgba(245,158,11,.45)' : 'var(--border-color)',
                  background: compare ? 'rgba(245,158,11,.15)' : 'transparent',
                  color: compare ? '#F59E0B' : 'var(--text-tertiary)',
                }}
              >
                <RotateCcw size={12} /> Base
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

const miniBtn = {
  height: 28,
  border: '1px solid var(--border-color)',
  borderRadius: 6,
  background: 'var(--bg-app)',
  color: 'var(--text-secondary)',
  padding: '0 8px',
  display: 'inline-flex',
  alignItems: 'center',
  gap: 5,
  cursor: 'pointer',
};

const listBtn = {
  border: 'none',
  background: 'transparent',
  padding: 0,
  fontSize: 11,
  cursor: 'pointer',
  textAlign: 'left',
  display: 'inline-flex',
  alignItems: 'center',
};
